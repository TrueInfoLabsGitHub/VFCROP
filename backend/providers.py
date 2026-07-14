"""Model providers for the VERITAS analysis graph.

Two real providers (Gemini for vision, OpenAI for the verdict tier) and a
deterministic mock fallback so the whole pipeline runs end-to-end with NO API
keys. Each call returns (result_dict, usage_dict) where usage carries the
token counts that pricing.py turns into cost — that's what feeds the Run
Report. Set GEMINI_API_KEY / OPENAI_API_KEY to flip a provider to live.
"""
import base64
import hashlib
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
from dotenv import load_dotenv

import rimage
import supa
from references import reference_path

# Load backend/.env so keys set there take effect without exporting them.
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3-pro-preview")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5.5")

# Friendly labels track the actual configured model (drives display + pricing).
_GEMINI_LABELS = {
    "gemini-3-pro-preview": "Gemini 3 Pro", "gemini-pro-latest": "Gemini 3 Pro",
    "gemini-2.5-pro": "Gemini 2.5 Pro", "gemini-2.5-flash": "Gemini 2.5 Flash",
    "gemini-flash-latest": "Gemini 2.5 Flash",
}
_OPENAI_LABELS = {"gpt-5.5": "GPT-5.5", "gpt-5.2": "GPT-5.2"}
GEMINI_LABEL = _GEMINI_LABELS.get(GEMINI_MODEL, GEMINI_MODEL)
OPENAI_LABEL = _OPENAI_LABELS.get(OPENAI_MODEL, OPENAI_MODEL)

# Gemini is served through OpenRouter (OpenAI-compatible API) — no Google billing needed.
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "").strip()
OPENROUTER_MODEL = os.environ.get("GEMINI_OR_MODEL", "google/gemini-3.1-pro-preview")
_OR_LABELS = {
    "google/gemini-3.1-pro-preview": "Gemini 3.1 Pro",
    "google/gemini-3.1-pro-preview-customtools": "Gemini 3.1 Pro",
    "google/gemini-2.5-pro": "Gemini 2.5 Pro",
    "google/gemini-2.5-pro-preview": "Gemini 2.5 Pro",
    "google/gemini-pro-latest": "Gemini Pro",
}
OPENROUTER_LABEL = _OR_LABELS.get(OPENROUTER_MODEL, "Gemini Pro")

# Kimi (Moonshot) — a challenger engine on the same OpenAI-compatible interface.
# Defaults to OpenRouter routing; set KIMI_BASE to hit the Moonshot API directly.
# KIMI_API_KEY takes precedence; if unset we reuse OPENROUTER_API_KEY.
KIMI_API_KEY = os.environ.get("KIMI_API_KEY", "").strip()
KIMI_BASE = os.environ.get("KIMI_BASE", "https://openrouter.ai/api/v1").strip()
KIMI_MODEL = os.environ.get("KIMI_MODEL", "moonshotai/kimi-k2.6")
_KIMI_LABELS = {
    # OpenRouter-style ids
    "moonshotai/kimi-k2.6": "Kimi K2.6", "moonshotai/kimi-k2.7": "Kimi K2.7",
    "moonshotai/kimi-k2.7-code": "Kimi K2.7", "moonshotai/kimi-k2.5": "Kimi K2.5",
    "moonshotai/kimi-k2": "Kimi K2",
    # Moonshot direct-API ids
    "kimi-k2.6": "Kimi K2.6", "kimi-k2.5": "Kimi K2.5",
    "kimi-k2.7": "Kimi K2.7", "kimi-k2.7-code": "Kimi K2.7",
}
KIMI_LABEL = _KIMI_LABELS.get(KIMI_MODEL, "Kimi")


def _kimi_key():
    return KIMI_API_KEY or OPENROUTER_API_KEY


# Testing switch: when false (the default) a missing key or a failed live call
# RAISES instead of silently returning deterministic mock data — so what you see
# is always a real model output or a real error, never fabricated numbers.
# Set ALLOW_MOCK=1 to restore the no-key demo fallback.
ALLOW_MOCK = os.environ.get("ALLOW_MOCK", "0").strip().lower() in ("1", "true", "yes", "on")

# Per-call HTTP timeout (seconds). Reasoning models (Kimi K2.6) can spend many
# seconds thinking before the first token, especially under the concurrent load
# of Compare mode (5 dimension agents x N engines). Keep this generous.
CHAT_TIMEOUT = float(os.environ.get("CHAT_TIMEOUT", "240"))

# Which provider runs the vision (dimension) agents by default: "openai" or "gemini".
VISION_PROVIDER = os.environ.get("VISION_PROVIDER", "openai").strip().lower()


def _cfg(provider):
    """Resolve a request's provider ('openai' | 'gemini') to an OpenAI-compatible
    chat endpoint. 'gemini' is routed through OpenRouter. Returns None if the
    selected provider has no key (caller then falls back to mock)."""
    if provider == "gemini":
        if not OPENROUTER_API_KEY:
            return None
        return {"base": "https://openrouter.ai/api/v1", "key": OPENROUTER_API_KEY,
                "model": OPENROUTER_MODEL, "label": OPENROUTER_LABEL, "strict": False,
                "extra_headers": {"HTTP-Referer": "http://localhost:8753", "X-Title": "VF VERITAS"}}
    if provider == "kimi":
        key = _kimi_key()
        if not key:
            return None
        headers = ({"HTTP-Referer": "http://localhost:8753", "X-Title": "VF VERITAS"}
                   if "openrouter.ai" in KIMI_BASE else {})
        return {"base": KIMI_BASE.rstrip("/"), "key": key, "model": KIMI_MODEL,
                "label": KIMI_LABEL, "strict": False, "extra_headers": headers}
    if OPENAI_API_KEY:
        return {"base": "https://api.openai.com/v1", "key": OPENAI_API_KEY,
                "model": OPENAI_MODEL, "label": OPENAI_LABEL, "strict": True, "extra_headers": {}}
    return None


def _label_for(provider):
    if provider == "gemini":
        return OPENROUTER_LABEL
    if provider == "kimi":
        return KIMI_LABEL
    return OPENAI_LABEL


def _schema_hint(schema):
    props = list((schema.get("properties") or {}).keys())
    return ("Respond ONLY with a single valid JSON object containing exactly these keys: "
            + ", ".join(props) + ". No prose, no markdown fences.")


def _augment_content(content, hint):
    if isinstance(content, list):
        return content + [{"type": "text", "text": hint}]
    return f"{content}\n\n{hint}"


def _chat(cfg, content, schema, schema_name, timeout):
    """One OpenAI-compatible chat-completions call -> (parsed_json, tin, tout).

    Prefers strict json_schema structured output; if the provider rejects that
    response_format (some do), retries once in json_object mode with the schema
    described in the prompt. No mock — a real failure still raises."""
    headers = {"Authorization": f"Bearer {cfg['key']}", "Content-Type": "application/json"}
    headers.update(cfg.get("extra_headers", {}))

    def post(body):
        # Generous read timeout for slow reasoning models (Kimi K2.6 thinks before
        # answering); one retry on a transient timeout / dropped connection.
        to = httpx.Timeout(timeout, connect=20.0)
        last = None
        for _ in range(2):
            try:
                r = httpx.post(cfg["base"] + "/chat/completions", json=body, headers=headers, timeout=to)
                r.raise_for_status()
                return r.json()
            except (httpx.ConnectTimeout, httpx.PoolTimeout, httpx.RemoteProtocolError) as e:
                last = e  # transient — retry once
                continue
        raise last

    def extract(data):
        ch = (data.get("choices") or [{}])[0]
        return str((ch.get("message") or {}).get("content") or "")

    def loads_lenient(txt):
        s = txt.strip()
        if not s:
            raise ValueError("empty response from model")
        if s.startswith("```"):                       # strip ```json ... ``` fences
            s = s[3:]
            if s[:4].lower() == "json":
                s = s[4:]
            if s.rstrip().endswith("```"):
                s = s.rstrip()[:-3]
            s = s.strip()
        try:
            return json.loads(s)
        except json.JSONDecodeError:                   # salvage the first {...} block
            a, b = s.find("{"), s.rfind("}")
            if a != -1 and b > a:
                return json.loads(s[a:b + 1])
            raise

    def call(b):
        data = post(b)
        return loads_lenient(extract(data)), (data.get("usage") or {})

    js = {"name": schema_name, "schema": schema}
    if cfg["strict"]:
        js["strict"] = True
    body = {"model": cfg["model"], "messages": [{"role": "user", "content": content}],
            "response_format": {"type": "json_schema", "json_schema": js}}
    obj_body = {"model": cfg["model"],
                "messages": [{"role": "user", "content": _augment_content(content, _schema_hint(schema))}],
                "response_format": {"type": "json_object"}}
    try:
        parsed, u = call(body)
    except httpx.HTTPStatusError as e:                  # provider rejected json_schema
        t = (e.response.text or "").lower()
        if e.response.status_code in (400, 422) and ("response_format" in t or "json_schema" in t or "schema" in t):
            parsed, u = call(obj_body)
        else:
            raise
    except (ValueError, json.JSONDecodeError):          # empty / non-JSON body — retry in json_object mode
        parsed, u = call(obj_body)
    return parsed, u.get("prompt_tokens", 0), u.get("completion_tokens", 0)


def _vision_label():
    return GEMINI_LABEL if VISION_PROVIDER == "gemini" else OPENAI_LABEL


def _vision_live():
    return ((VISION_PROVIDER == "gemini" and bool(GEMINI_API_KEY))
            or (VISION_PROVIDER != "gemini" and bool(OPENAI_API_KEY)))


def mode() -> dict:
    return {
        # default-engine fields the badge reads
        "vision": "live" if OPENAI_API_KEY else "mock",
        "vision_model": OPENAI_MODEL if OPENAI_API_KEY else None,
        "verdict": "live" if OPENAI_API_KEY else "mock",
        # per-provider availability the toggle reads
        "openai": "live" if OPENAI_API_KEY else "mock",
        "openai_model": OPENAI_MODEL if OPENAI_API_KEY else None,
        "gemini": "live" if OPENROUTER_API_KEY else "mock",        # Gemini via OpenRouter
        "gemini_model": OPENROUTER_MODEL if OPENROUTER_API_KEY else None,
        "gemini_label": OPENROUTER_LABEL if OPENROUTER_API_KEY else None,
        "kimi": "live" if _kimi_key() else "mock",                 # Kimi (Moonshot) via router
        "kimi_model": KIMI_MODEL if _kimi_key() else None,
        "kimi_label": KIMI_LABEL if _kimi_key() else None,
        "openrouter": "live" if OPENROUTER_API_KEY else "mock",
        "allow_mock": ALLOW_MOCK,
        "serpapi": "live" if rimage.available() else "mock",   # Google reverse-image
        "supabase": "live" if supa.available() else "mock",    # product catalog
    }


# ---------------------------------------------------------------------------
# Deterministic mock copy — plausible, dimension-specific findings keyed by
# verdict band, so a mock run still reads like a real forensic assessment.
# ---------------------------------------------------------------------------
_COPY = {
    "Logo": {
        "authentic": ("Embroidery density & color match reference",
                      "Stitch count per cm2, thread color, and half-dome proportions align with the master within tolerance."),
        "caution": ("Slight color cast in logo thread",
                    "Geometry matches but thread reads ~1.5 dE warmer than reference - possible dye-lot variance."),
        "counterfeit": ("Logo proportions deviate from spec",
                        "Half-dome wordmark is ~8% wider than master and the registration mark is misplaced - consistent with traced artwork."),
    },
    "Stitching": {
        "authentic": ("Stitch pitch consistent with factory spec",
                      "Bartack placement and 7-SPI pitch match authorized production records."),
        "caution": ("Irregular pitch at hem seam",
                    "Stitch pitch drifts 6-9 SPI along the hem; within failure-prone range but not conclusive."),
        "counterfeit": ("Skipped & uneven stitching at stress seams",
                        "Visible thread skips and 5 SPI pitch at load-bearing seams - below VF minimum durability spec."),
    },
    "Hardware": {
        "authentic": ("Zipper pull stamped with correct foundry mark",
                      "Pull and slider carry the correct embossed code and finish weight."),
        "caution": ("Zipper finish slightly off-tone",
                    "Slider geometry correct; anodized finish reads cooler than reference - inconclusive without teardown."),
        "counterfeit": ("Unbranded zipper, incorrect pull weight",
                        "Pull lacks foundry stamp and weighs 0.4g under spec; tape gauge does not match authorized BOM."),
    },
    "Label": {
        "authentic": ("Care label fonts & RN number valid",
                      "Woven care label uses correct typeface, RN number, and country-of-origin format."),
        "caution": ("Care label kerning irregular",
                    "RN number is valid but care-symbol kerning is inconsistent - flag for physical review."),
        "counterfeit": ("Invalid RN number on care label",
                        "Printed (not woven) care label; RN number does not resolve to a VF-registered entity."),
    },
    "Material": {
        "authentic": ("Fabric hand & weave match reference",
                      "Panel weave count and coating match the authorized material spec."),
        "caution": ("Coating sheen differs from reference",
                    "Face-fabric weave correct; DWR sheen differs under raking light - possible substitute finish."),
        "counterfeit": ("Substituted shell fabric detected",
                        "Shell denier and ripstop grid do not match spec; lining is a non-authorized substitute."),
    },
}

_BOXES = {
    "Logo": {"x": 30, "y": 14, "w": 40, "h": 22},
    "Stitching": {"x": 8, "y": 60, "w": 30, "h": 16},
    "Hardware": {"x": 44, "y": 40, "w": 16, "h": 30},
    "Label": {"x": 60, "y": 8, "w": 30, "h": 14},
    "Material": {"x": 12, "y": 30, "w": 26, "h": 26},
}


def _band(score):
    if score is None:
        return "neutral"
    if score <= 30:
        return "authentic"
    if score <= 60:
        return "caution"
    return "counterfeit"


def _rng(seed: str):
    h = int(hashlib.sha256(seed.encode()).hexdigest(), 16)
    def nxt():
        nonlocal h
        h = (h * 6364136223846793005 + 1442695040888963407) & ((1 << 64) - 1)
        return (h >> 11) / (1 << 53)
    return nxt


def _img_b64(filename: str):
    try:
        with open(reference_path(filename), "rb") as f:
            return base64.b64encode(f.read()).decode()
    except OSError:
        return None


# ---------------------------------------------------------------------------
# Dimension agent (vision) — Gemini live, deterministic mock otherwise.
# ---------------------------------------------------------------------------
def run_dimension_agent(dim, brand, case_id, suspect_images, ref_b64s, provider="openai"):
    t0 = time.time()
    imgs = [b for b in (suspect_images or []) if b][:6]   # cap to keep token cost bounded
    refs = [b for b in (ref_b64s or []) if b][:2]
    cfg = _cfg(provider)
    if cfg:
        if not imgs:
            if ALLOW_MOCK:
                return _mock_dimension(dim, case_id, t0, _label_for(provider))
            raise RuntimeError(f"{dim}: no suspect image provided — cannot run a real vision agent.")
        try:
            return _chat_dimension(cfg, dim, imgs, refs, t0)
        except Exception as e:
            if ALLOW_MOCK:
                print(f"[{provider}] {dim} live call failed, using mock: {e}")
                return _mock_dimension(dim, case_id, t0, _label_for(provider))
            raise RuntimeError(f"{provider} {dim} agent failed: {e}") from e
    if ALLOW_MOCK:
        return _mock_dimension(dim, case_id, t0, _label_for(provider))
    raise RuntimeError(f"Provider '{provider}' is not configured — set its API key "
                       f"(or ALLOW_MOCK=1 to use demo data).")


def _mock_dimension(dim, case_id, t0, label):
    rnd = _rng(f"{case_id}|{dim}")
    # Spread across bands so a demo case shows a realistic mix of pass/caution/fail.
    base = 22 + rnd() * 70
    score = int(max(3, min(98, round(base))))
    band = _band(score)
    finding, reasoning = _COPY[dim][band]
    confidence = round(0.7 + rnd() * 0.28, 2)
    usage = {
        "agent": dim, "model": f"{label} (mock)",
        "tokens_in": 6400 + int(rnd() * 400), "tokens_out": 360 + int(rnd() * 120),
        "latency_ms": int((time.time() - t0) * 1000) + 3000 + int(rnd() * 600),
    }
    result = {
        "dimension": dim, "score": score, "band": band,
        "finding": finding, "reasoning": reasoning,
        "box": _BOXES[dim], "confidence": confidence,
        "status": "abstain" if band == "neutral" else "scored",
    }
    return result, usage


def _gemini_dimension(dim, suspect_b64s, reference_file, t0):
    ref_b64 = _img_b64(reference_file)
    schema = {
        "type": "object",
        "properties": {
            "score": {"type": "integer"},
            "finding": {"type": "string"},
            "reasoning": {"type": "string"},
            "confidence": {"type": "number"},
        },
        "required": ["score", "finding", "reasoning", "confidence"],
    }
    prompt = (
        f"You are a brand-protection vision specialist. Compare the SUSPECT product image "
        f"against the AUTHENTIC reference for the '{dim}' dimension of a counterfeit "
        f"authentication. Inspect fine detail closely. Return a counterfeit-probability "
        f"score 0-100 (0=clearly authentic, 100=clearly counterfeit), a one-line finding, "
        f"a short reasoning, and a confidence 0-1."
    )
    parts = [{"text": prompt}]
    for i, b in enumerate(suspect_b64s):
        parts.append({"text": f"SUSPECT photo {i + 1}:"})
        parts.append({"inline_data": {"mime_type": "image/jpeg", "data": b}})
    if ref_b64:
        parts += [{"text": "AUTHENTIC REFERENCE:"},
                  {"inline_data": {"mime_type": "image/jpeg", "data": ref_b64}}]
    body = {
        "contents": [{"parts": parts}],
        "generationConfig": {"responseMimeType": "application/json", "responseSchema": schema},
    }
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
    r = httpx.post(url, json=body, timeout=60)
    r.raise_for_status()
    data = r.json()
    text = data["candidates"][0]["content"]["parts"][0]["text"]
    parsed = json.loads(text)
    um = data.get("usageMetadata", {})
    score = int(max(0, min(100, parsed["score"])))
    band = _band(score)
    usage = {
        "agent": dim, "model": GEMINI_LABEL,
        "tokens_in": um.get("promptTokenCount", 0),
        "tokens_out": um.get("candidatesTokenCount", 0),
        "latency_ms": int((time.time() - t0) * 1000),
    }
    result = {
        "dimension": dim, "score": score, "band": band,
        "finding": parsed["finding"], "reasoning": parsed["reasoning"],
        "box": _BOXES[dim], "confidence": round(float(parsed.get("confidence", 0.8)), 2),
        "status": "scored",
    }
    return result, usage


def _chat_dimension(cfg, dim, suspect_b64s, ref_b64s, t0):
    schema = {
        "type": "object",
        "properties": {
            "score": {"type": "integer"},
            "finding": {"type": "string"},
            "reasoning": {"type": "string"},
            "confidence": {"type": "number"},
        },
        "required": ["score", "finding", "reasoning", "confidence"],
        "additionalProperties": False,
    }
    prompt = (
        f"You are a brand-protection vision specialist. Compare the SUSPECT product photos "
        f"(one or more views) against the AUTHENTIC reference photo(s) for the '{dim}' dimension "
        f"of a counterfeit authentication. Use whichever suspect photo best shows this dimension; "
        f"inspect fine detail closely. Return a counterfeit-probability score 0-100 "
        f"(0=clearly authentic, 100=clearly counterfeit), a one-line finding, a short "
        f"reasoning, and a confidence 0-1."
    )
    content = [{"type": "text", "text": prompt}]
    for i, b in enumerate(suspect_b64s):
        content.append({"type": "text", "text": f"SUSPECT photo {i + 1}:"})
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b}", "detail": "high"}})
    for j, rb in enumerate(ref_b64s):
        content.append({"type": "text", "text": f"AUTHENTIC REFERENCE {j + 1}:"})
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{rb}", "detail": "high"}})
    parsed, tin, tout = _chat(cfg, content, schema, "dimension", CHAT_TIMEOUT)
    score = int(max(0, min(100, parsed["score"])))
    band = _band(score)
    usage = {"agent": dim, "model": cfg["label"], "tokens_in": tin, "tokens_out": tout,
             "latency_ms": int((time.time() - t0) * 1000)}
    result = {
        "dimension": dim, "score": score, "band": band,
        "finding": parsed["finding"], "reasoning": parsed["reasoning"],
        "box": _BOXES[dim], "confidence": round(float(parsed.get("confidence", 0.8)), 2),
        "status": "scored",
    }
    return result, usage


# ---------------------------------------------------------------------------
# UPC / security-tag tool node — OCR + master-record lookup (mocked DB).
# ---------------------------------------------------------------------------
_MASTER_UPC = {"TNF": "193393578024", "Vans": "191167589436", "Timberland": "887168539921"}

# Reverse catalog so a UPC that reads as a *different* product surfaces a mismatch.
_UPC_CATALOG = {
    "193393578024": ("TNF", "1996 Retro Nuptse Jacket"),
    "191167589436": ("Vans", "Old Skool"),
    "887168539921": ("Timberland", "6-Inch Premium Waterproof Boot"),
}


def run_upc_tool(brand, case_id, upc_image, provider="openai"):
    """OCR the UPC from the uploaded barcode image and look it up.
    Falls back to a stub when no UPC image is provided / no key."""
    t0 = time.time()
    cfg = _cfg(provider)
    if upc_image and cfg:
        try:
            return _chat_upc(cfg, brand, upc_image, t0)
        except Exception as e:
            if not ALLOW_MOCK:
                raise RuntimeError(f"{provider} UPC OCR failed: {e}") from e
            print(f"[{provider}] UPC OCR failed, using stub: {e}")
    elif upc_image and not cfg and not ALLOW_MOCK:
        raise RuntimeError(f"Provider '{provider}' is not configured — set its API key "
                           f"(or ALLOW_MOCK=1 to use demo data).")
    # No UPC image supplied (or mock allowed): return the honest "no code" stub.
    expected = _MASTER_UPC.get(brand, "")
    usage = {"agent": "UPC / Tag", "model": f"{_label_for(provider)} (mock)",
             "tokens_in": 0, "tokens_out": 0, "latency_ms": int((time.time() - t0) * 1000)}
    return {"status": "nomatch", "note": "No UPC image provided — upload a barcode photo to extract and verify.",
            "expected": expected, "extracted": "", "belongs": None}, usage


def _chat_upc(cfg, brand, upc_b64, t0):
    schema = {"type": "object",
              "properties": {"upc": {"type": "string"}, "readable": {"type": "boolean"}},
              "required": ["upc", "readable"], "additionalProperties": False}
    prompt = ("Read the UPC/EAN barcode number or printed product code in this image. "
              "Return ONLY the digits (no spaces or dashes) in 'upc', and 'readable'=true "
              "if you could read a code, false if none is legible.")
    content = [{"type": "text", "text": prompt},
               {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{upc_b64}", "detail": "high"}}]
    parsed, tin, tout = _chat(cfg, content, schema, "upc", CHAT_TIMEOUT)
    digits = "".join(ch for ch in str(parsed.get("upc", "")) if ch.isdigit())
    expected = _MASTER_UPC.get(brand, "")
    belongs = None
    if not parsed.get("readable") or not digits:
        status, note = "nomatch", "No readable UPC could be extracted from the uploaded image."
    elif digits == expected:
        status, note = "match", f"UPC {digits} resolves to the matching {brand} master record in SAP MDG."
    elif digits in _UPC_CATALOG:
        b, name = _UPC_CATALOG[digits]
        status, note, belongs = "mismatch", f"UPC {digits} belongs to {name} ({b}) but this case is a {brand} product.", name
    else:
        status, note = "nomatch", f"UPC {digits} does not exist in the PIM master record — counterfeit indicator."
    usage = {"agent": "UPC / Tag", "model": cfg["label"], "tokens_in": tin, "tokens_out": tout,
             "latency_ms": int((time.time() - t0) * 1000)}
    return {"status": status, "note": note, "expected": expected, "extracted": digits, "belongs": belongs}, usage


# ---------------------------------------------------------------------------
# Verdict tier — OpenAI synthesis + adversarial verify (two calls), or mock.
# ---------------------------------------------------------------------------
def run_verdict(provider, brand, composite, dimensions, upc):
    # Run the synthesis and adversarial-verify calls concurrently instead of
    # back-to-back — halves the verdict-tier latency for slow reasoning models.
    with ThreadPoolExecutor(max_workers=2) as ex:
        fs = ex.submit(_verdict_call, provider, "synthesize", brand, composite, dimensions, upc)
        fv = ex.submit(_verdict_call, provider, "verify", brand, composite, dimensions, upc)
        synth, u1 = fs.result()
        verify, u2 = fv.result()
    verdict = {
        "label": composite["verdict_label"],
        "summary": synth["summary"],
        "escalated": composite["band"] != "authentic",
        "verifier_confirmed": verify["confirmed"],
        "verifier_votes": verify["votes"],
        "key_evidence": synth["key_evidence"],
    }
    return verdict, [u1, u2]


def _verdict_call(provider, kind, brand, composite, dimensions, upc):
    t0 = time.time()
    cfg = _cfg(provider)
    if cfg:
        try:
            return _chat_verdict(cfg, kind, brand, composite, dimensions, upc, t0)
        except Exception as e:
            if not ALLOW_MOCK:
                raise RuntimeError(f"{provider} verdict ({kind}) failed: {e}") from e
            print(f"[{provider}] {kind} live call failed, using mock: {e}")
            return _mock_verdict(kind, brand, composite, dimensions, upc, t0, _label_for(provider))
    if ALLOW_MOCK:
        return _mock_verdict(kind, brand, composite, dimensions, upc, t0, _label_for(provider))
    raise RuntimeError(f"Provider '{provider}' is not configured — set its API key "
                       f"(or ALLOW_MOCK=1 to use demo data).")


def _top_findings(dimensions, n=3):
    ranked = sorted([d for d in dimensions if d["score"] is not None],
                    key=lambda d: -d["score"])
    return [f"{d['dimension']}: {d['finding']}" for d in ranked[:n]]


def _mock_verdict(kind, brand, composite, dimensions, upc, t0, label):
    rnd = _rng(f"{brand}|{composite['score']}|{kind}")
    if kind == "synthesize":
        out = {
            "summary": (f"Composite counterfeit probability {composite['score']}/100 "
                        f"({composite['verdict_label']}). UPC {upc['status']}. "
                        f"Multiple construction dimensions deviate from the authentic reference."),
            "key_evidence": _top_findings(dimensions),
        }
        toks_out = 640 + int(rnd() * 160)
    else:
        confirmed = composite["score"] >= 55
        out = {"confirmed": confirmed, "votes": "2/2" if confirmed else "1/2"}
        toks_out = 480 + int(rnd() * 140)
    usage = {
        "agent": "Verdict synth." if kind == "synthesize" else "Verify",
        "model": f"{label} (mock)",
        "tokens_in": 3000 + int(rnd() * 300), "tokens_out": toks_out,
        "latency_ms": int((time.time() - t0) * 1000) + 3800 + int(rnd() * 600),
    }
    return out, usage


def _chat_verdict(cfg, kind, brand, composite, dimensions, upc, t0):
    findings = "; ".join(_top_findings(dimensions, 6))
    if kind == "synthesize":
        schema = {"type": "object", "properties": {
            "summary": {"type": "string"},
            "key_evidence": {"type": "array", "items": {"type": "string"}}},
            "required": ["summary", "key_evidence"], "additionalProperties": False}
        prompt = (f"Brand {brand}. Composite score {composite['score']}/100 "
                  f"({composite['verdict_label']}). UPC {upc['status']}. Findings: {findings}. "
                  f"Write a concise litigation-ready verdict summary and 3 key-evidence bullets.")
    else:
        schema = {"type": "object", "properties": {
            "confirmed": {"type": "boolean"}, "votes": {"type": "string"}},
            "required": ["confirmed", "votes"], "additionalProperties": False}
        prompt = (f"Adversarially review this counterfeit verdict. Composite {composite['score']}/100. "
                  f"Findings: {findings}. Is the counterfeit conclusion supported? "
                  f"Return confirmed (bool) and votes like '2/2'.")
    out, tin, tout = _chat(cfg, prompt, schema, "verdict", CHAT_TIMEOUT)
    usage = {
        "agent": "Verdict synth." if kind == "synthesize" else "Verify",
        "model": cfg["label"], "tokens_in": tin, "tokens_out": tout,
        "latency_ms": int((time.time() - t0) * 1000),
    }
    return out, usage
