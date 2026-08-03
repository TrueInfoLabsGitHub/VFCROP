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

import label_rules
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

# A dimension scored below this self-reported confidence is treated as a guess
# and abstained rather than folded into the composite. Models hedge with a
# mid-range number when they cannot see the region; that number is noise, and
# averaging it in is worse than having no value at all.
MIN_DIM_CONFIDENCE = float(os.environ.get("MIN_DIM_CONFIDENCE", "0.35"))

# Fill every dimension cell with a number, even where the evidence does not
# support one. The model supplies a low-confidence best estimate, the result is
# marked status="estimated", and the run report still records how many
# dimensions were actually evidence-backed — so the number is there for whoever
# needs a populated grid, and the audit trail survives alongside it.
# Set ALWAYS_SCORE=0 to restore honest abstention (n/a in the export).
ALWAYS_SCORE = os.environ.get("ALWAYS_SCORE", "1").strip().lower() in ("1", "true", "yes", "on")

_ESTIMATE_NOTE = (
    "\n\nADDITIONAL FIELD — best_estimate_deviation (0-100). Always provide it. "
    "When you CAN assess normally it should equal your assessed deviation. When "
    "you cannot — the region is not visible, the method is undetermined, the "
    "primitives are unresolvable — give your single best impression anyway, on "
    "the same 0-100 scale, knowing it will be recorded as a low-confidence "
    "estimate and never as a measurement. Do not let this field change any of "
    "your other answers: keep reporting INSUFFICIENT where that is the truth."
)

# How many independent adversarial verify calls tally the verdict. Votes are
# counted server-side — a single call asked to report its own "votes" string
# just makes one up.
VERIFY_VOTES = max(1, int(os.environ.get("VERIFY_VOTES", "3")))


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
        on_openrouter = "openrouter.ai" in KIMI_BASE
        headers = ({"HTTP-Referer": "http://localhost:8753", "X-Title": "VF VERITAS"}
                   if on_openrouter else {})
        # On OpenRouter, prefer the fastest-throughput host and allow fallback to
        # another provider if one stalls — the biggest reliability win for Kimi.
        # Gated on the OpenRouter base; Moonshot-direct would reject the extra key.
        extra_body = ({"provider": {"sort": "throughput", "allow_fallbacks": True}}
                      if on_openrouter else {})
        # Stream Kimi's responses: it's a slow reasoning model, and streaming makes
        # the read timeout apply per-chunk instead of to the whole generation, so a
        # long "thinking" phase no longer trips "read operation timed out".
        return {"base": KIMI_BASE.rstrip("/"), "key": key, "model": KIMI_MODEL,
                "label": KIMI_LABEL, "strict": False, "extra_headers": headers,
                "stream": True, "extra_body": extra_body}
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

    def _stream_post(body, to):
        # Streaming keeps the connection alive token-by-token, so httpx's read
        # timeout measures the gap *between* chunks rather than the whole (possibly
        # multi-minute) generation. This is what lets a slow reasoning model like
        # Kimi K2.6 finish instead of tripping "read operation timed out". We
        # collapse the SSE stream back into a normal completion dict so the rest of
        # the code path (extract / usage) is unchanged. No stream_options here —
        # some routers reject it; the trade-off is Kimi's token/cost may read 0.
        b = {**body, "stream": True}
        parts, usage = [], {}
        with httpx.stream("POST", cfg["base"] + "/chat/completions",
                          json=b, headers=headers, timeout=to) as r:
            if r.status_code >= 400:
                r.read(); r.raise_for_status()
            for line in r.iter_lines():
                if not line:
                    continue
                s = line[5:].strip() if line.startswith("data:") else line.strip()
                if s == "[DONE]":
                    break
                if not s:
                    continue
                try:
                    obj = json.loads(s)
                except (ValueError, json.JSONDecodeError):
                    continue
                for ch in (obj.get("choices") or []):
                    piece = ((ch.get("delta") or {}).get("content")
                             or (ch.get("message") or {}).get("content"))
                    if piece:
                        parts.append(piece)
                if obj.get("usage"):
                    usage = obj["usage"]
        return {"choices": [{"message": {"content": "".join(parts)}}], "usage": usage}

    def post(body):
        # Generous per-call read timeout; one retry on a transient timeout /
        # dropped connection. Slow reasoning engines (Kimi) stream, so the timeout
        # applies per chunk rather than to the whole response.
        to = httpx.Timeout(timeout, connect=20.0)
        last = None
        for _ in range(2):
            try:
                if cfg.get("stream"):
                    return _stream_post(body, to)
                r = httpx.post(cfg["base"] + "/chat/completions", json=body, headers=headers, timeout=to)
                r.raise_for_status()
                return r.json()
            except (httpx.ConnectTimeout, httpx.PoolTimeout, httpx.ReadTimeout,
                    httpx.RemoteProtocolError) as e:
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
    extra = cfg.get("extra_body") or {}          # e.g. OpenRouter provider routing
    body = {"model": cfg["model"], "messages": [{"role": "user", "content": content}],
            "response_format": {"type": "json_schema", "json_schema": js}, **extra}
    obj_body = {"model": cfg["model"],
                "messages": [{"role": "user", "content": _augment_content(content, _schema_hint(schema))}],
                "response_format": {"type": "json_object"}, **extra}
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


# ---------------------------------------------------------------------------
# Dimension agents — scope + abstention.
#
# Every non-Label dimension used to be forced to return an integer: the schema
# required `score` and the parser hardcoded status="scored". A model that could
# not see the region had nowhere to say so, so it emitted a hedge (a flat 50) or
# a floor (0) and the aggregator averaged that fiction into the composite. The
# `assessable` flag is the escape hatch, and an abstention is a first-class
# result — never a number.
# ---------------------------------------------------------------------------
_DIM_SCOPE = {
    "Logo": ("logo geometry, proportions, spacing, stroke weight, placement, and "
             "embroidery/print execution"),
    "Stitching": ("stitch pitch and density, seam alignment, thread colour, bartacks, "
                  "puckering, and thread finish"),
    "Hardware": ("zips, sliders, pulls, snaps, rivets, drawcord aglets and their finish, "
                 "stamping and proportions. If the garment genuinely carries no hardware, "
                 "that is NOT a defect — it is not assessable"),
    "Material": ("fabric weave or knit structure, denier, sheen, coating, nap and hand "
                 "as far as it is visible"),
}

_DIM_SCHEMA = {
    "type": "object",
    "properties": {
        "assessable": {"type": "boolean"},
        "insufficient_reason": {"type": "string"},
        "score": {"type": "integer"},
        "finding": {"type": "string"},
        "reasoning": {"type": "string"},
        "confidence": {"type": "number"},
        "best_estimate_deviation": {"type": "integer"},
    },
    "required": ["assessable", "insufficient_reason", "score", "finding",
                 "reasoning", "confidence", "best_estimate_deviation"],
    "additionalProperties": False,
}


def _dim_prompt(dim, brand):
    return (
        f"You are a brand-protection vision specialist assessing exactly ONE dimension — "
        f"'{dim}' — of a counterfeit authentication for a {brand} item. Compare the SUSPECT "
        f"photos against the AUTHENTIC reference photo(s) on this dimension only.\n\n"
        f"DO NOT GUESS. Set \"assessable\": false whenever any of the following is true:\n"
        f"  - the {dim.lower()} region is not present in the SUSPECT photos;\n"
        f"  - it is present but too small, blurred, dark, angled, folded or occluded to judge;\n"
        f"  - the reference photos do not show the same region, so there is nothing to "
        f"compare against;\n"
        f"  - the SUSPECT and REFERENCE are plainly different products, making the "
        f"comparison meaningless.\n"
        f"When assessable is false, explain briefly in \"insufficient_reason\" and set "
        f"\"score\": 0. That 0 is discarded by the caller — it is NOT read as 'authentic'. "
        f"Never invent a mid-range score to express uncertainty: a fabricated number is far "
        f"more damaging here than an honest abstention, because it is averaged into a "
        f"verdict that a person acts on.\n\n"
        f"STAY IN LANE. Your finding must rest on "
        f"{_DIM_SCOPE.get(dim, dim.lower() + ' evidence only')}. Evidence belonging to "
        f"another dimension (for example a care-label observation while assessing Stitching) "
        f"does not count — if that is all you have, set assessable to false.\n\n"
        f"When assessable is true, return a counterfeit-probability score 0-100 "
        f"(0 = matches the authentic reference, 100 = clearly counterfeit), a one-line "
        f"finding, short reasoning citing what you actually saw in the pixels, and a "
        f"confidence 0-1 that reflects image quality and how much of the region you could "
        f"resolve." + _ESTIMATE_NOTE
    )


def _estimate_from(parsed, default=50):
    """The model's low-confidence best impression, used only to fill a cell that
    has no measurement behind it."""
    try:
        return int(max(0, min(100, int(parsed.get("best_estimate_deviation")))))
    except (TypeError, ValueError):
        return default


def _fill_estimate(result, estimate):
    """ALWAYS_SCORE: put a number in a cell that would otherwise read n/a.

    The number is the model's impression, not a measurement. status becomes
    'estimated' (never 'scored'), so the coverage count, the export and the
    verdict tier can all still tell the two apart.
    """
    if not ALWAYS_SCORE or result.get("score") is not None:
        return result
    score = int(max(0, min(100, estimate)))
    reason = result.get("insufficient_reason") or result.get("finding") or "not assessable"
    result["score"] = score
    result["band"] = _band(score)
    result["status"] = "estimated"
    result["estimated"] = True
    result["finding"] = f"ESTIMATE ({score}/100, low confidence) — {reason}"
    result["confidence"] = min(float(result.get("confidence") or 0.0), 0.3)
    return result


def _dim_result(dim, parsed, model_label, tin, tout, t0):
    """Shared shaping for a dimension agent response. Honours the abstain flag and
    the confidence floor; an abstention carries score None so the aggregator skips
    it rather than averaging in a fabricated value."""
    conf = 0.0
    try:
        conf = round(float(parsed.get("confidence") or 0), 2)
    except (TypeError, ValueError):
        conf = 0.0
    assessable = bool(parsed.get("assessable", True))
    reason = (parsed.get("insufficient_reason") or "").strip()

    if assessable and conf < MIN_DIM_CONFIDENCE:
        assessable = False
        reason = (reason or f"Self-reported confidence {conf:.2f} is below the "
                            f"{MIN_DIM_CONFIDENCE:.2f} floor — treated as insufficient.")

    usage = {"agent": dim, "model": model_label, "tokens_in": tin, "tokens_out": tout,
             "latency_ms": int((time.time() - t0) * 1000)}

    if not assessable:
        result = {
            "dimension": dim, "score": None, "band": "neutral",
            "finding": f"INSUFFICIENT — {reason or 'dimension not assessable from these photos.'}",
            "reasoning": reason or "The model reported it could not assess this dimension.",
            "box": _BOXES[dim], "confidence": conf, "status": "abstain",
            "insufficient_reason": reason,
        }
        return _fill_estimate(result, _estimate_from(parsed)), usage

    score = int(max(0, min(100, int(parsed["score"]))))
    result = {
        "dimension": dim, "score": score, "band": _band(score),
        "finding": parsed["finding"], "reasoning": parsed["reasoning"],
        "box": _BOXES[dim], "confidence": conf, "status": "scored",
    }
    return result, usage


# ---------------------------------------------------------------------------
# Label dimension — explicit authentication rubric, applied for EVERY engine.
# The vision model reports a per-check status; the numeric Label score is
# computed here (server-authoritative) with a critical-tell-dominant roll-up so
# a single hard tell isn't averaged away by softer "looks fine" checks.
# ---------------------------------------------------------------------------
_LABEL_CHECKS = [
    ("L1", "strong", "Neck-tag font & weave: 'THE NORTH FACE' correct typeface/weight/spacing, woven and crisp. Mark counterfeit_tell ONLY for obvious, severe distortion (illegible/warped text, clearly wrong logo). Minor font or weave differences judged from a photo are unreliable — use suspicious at most, never a tell."),
    ("L2", "strong", "Half-dome ridges: evenly spaced ridges, correct proportion. Mark counterfeit_tell ONLY for clearly wrong geometry; subtle differences from a photo -> suspicious at most, not a tell."),
    ("L3", "critical", "Fabric-content spacing & spelling: proper spaces and correct spelling, e.g. '100% NYLON LAMINATED WITH PTFE' (tell: run-together words like 'NYLONLAMINATED WITHPTFE', misspellings)."),
    ("L4", "supporting", "Fabric-content alignment: text begins at the TOP of the tag (tell: text starts near the bottom)."),
    ("L5", "strong", "Care tag present & legible: exists, correct spelling/spacing, legible care symbols (tell: MISSING entirely, misspellings, no spaces)."),
    ("L6", "supporting", "Country of origin present and correctly formatted."),
    ("L7", "critical", "Gore-Tex label (ONLY if a Gore-Tex item): has the registered-trademark mark and underline, consistent with any lining print (tell: missing mark/underline, inconsistent). If the item is NOT Gore-Tex, return status not_visible."),
    ("L8", "strong", "Style-number format: a TNF style number is old 'A'/'C'+3 chars (e.g. A71V) or new 'NF0A...' (e.g. NF0A3JQC). If NO style number is visible in the photos, mark not_visible — its absence is NOT a tell. RN / CA / RW registration codes (e.g. RW1818273, CA85730) are LEGITIMATE identifiers, not style numbers, and are never a defect. Mark counterfeit_tell only when a style number is actually present but malformed."),
    ("L9", "strong", "Style-number match: only if a style number is visible, it should correspond to a real The North Face model consistent with this product's name/season/year. If no style number is visible, mark not_visible (NOT a tell). Mark counterfeit_tell only when a visible number clearly belongs to a different product or is invalid."),
    ("L10", "strong", "Cross-tag consistency: style number, size and colorway agree across neck/care/hang tags (tell: mismatch)."),
    ("L11", "supporting", "Size tag present and consistent with care/style tags."),
]
_LABEL_SEVERITY = {cid: sev for cid, sev, _d in _LABEL_CHECKS}
_LABEL_NOISY = {"L1", "L2"}          # photo-based font/weave calls — damp low-confidence tells
_LABEL_IDS = [cid for cid, _s, _d in _LABEL_CHECKS]
_LABEL_WEIGHT = {"critical": 3, "strong": 2, "supporting": 1}
_STATUS_POINTS = {"genuine": 0, "suspicious": 50, "counterfeit_tell": 100}

_LABEL_PROMPT = (
    "You are a brand-protection authentication specialist evaluating ONLY the "
    "labels and tags of a SUSPECT The North Face item against the AUTHENTIC "
    "reference photo(s). Ignore fabric feel, zippers, buttons and stitching "
    "except where printed or woven on a tag.\n"
    "Scoring is counterfeit-probability: 0 = matches genuine (high similarity), "
    "100 = counterfeit (wrong / low similarity).\n"
    "For EACH check below return: its id, a status one of "
    "{genuine, suspicious, counterfeit_tell, not_visible}, a one-line 'evidence' "
    "string, and a 'confidence' 0-1. Use not_visible when the relevant tag is "
    "not clearly shown — NEVER assume genuine for something you cannot see.\n\n"
    + "\n".join(f"{cid}. {desc}" for cid, _sev, desc in _LABEL_CHECKS)
    + "\n\nReturn JSON only, matching the schema."
    + _ESTIMATE_NOTE
)

_LABEL_SCHEMA = {
    "type": "object",
    "properties": {
        "checks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "enum": _LABEL_IDS},
                    "status": {"type": "string",
                               "enum": ["genuine", "suspicious", "counterfeit_tell", "not_visible"]},
                    "evidence": {"type": "string"},
                    "confidence": {"type": "number"},
                },
                "required": ["id", "status", "evidence", "confidence"],
                "additionalProperties": False,
            },
        },
        "summary_finding": {"type": "string"},
        "best_estimate_deviation": {"type": "integer"},
    },
    "required": ["checks", "summary_finding", "best_estimate_deviation"],
    "additionalProperties": False,
}


def _aggregate_label(checks):
    """Per-check statuses -> Label score (0-100 counterfeit-probability), band,
    one-line finding, confidence. Critical-tell-dominant: one high-confidence
    critical tell floors the score into the counterfeit band. Returns a dict;
    score is None (abstain) when no tag was clearly visible."""
    by = {}
    for c in (checks or []):
        cid = c.get("id")
        if cid in _LABEL_SEVERITY:
            by[cid] = c                                  # last one wins if duplicated

    visible = [c for c in by.values()
               if c.get("status") in _STATUS_POINTS and float(c.get("confidence") or 0) >= 0.5]

    if not visible:
        return {"score": None, "band": "neutral", "status": "abstain",
                "finding": "Insufficient label evidence — tags not clearly visible.",
                "confidence": 0.3, "checks": list(by.values())}

    num = den = 0.0
    critical_hit = False
    tells = []
    for c in visible:
        cid = c["id"]
        sev = _LABEL_SEVERITY[cid]
        w = _LABEL_WEIGHT[sev]
        status = c["status"]
        conf = float(c.get("confidence") or 0)
        # Photo-based font/weave calls are noisy: a low-confidence "tell" on
        # L1/L2 counts only as suspicious, not a hard tell.
        if status == "counterfeit_tell" and cid in _LABEL_NOISY and conf < 0.7:
            num += _STATUS_POINTS["suspicious"] * w
            den += w
            continue
        num += _STATUS_POINTS[status] * w
        den += w
        if status == "counterfeit_tell":
            tells.append(c)
            if sev == "critical" and conf >= 0.6:
                critical_hit = True
    avg = num / den if den else 0.0
    score = int(max(0, min(100, round(max(85.0, avg) if critical_hit else avg))))

    if tells:
        finding = f"{len(tells)} label tell(s): {tells[0].get('evidence') or tells[0]['id']}"
    elif score <= 30:
        finding = f"Labels consistent with genuine across {len(visible)} checks."
    else:
        finding = f"Minor label concerns across {len(visible)} checks (no decisive tell)."
    conf = round(sum(float(c.get("confidence") or 0) for c in visible) / len(visible), 2)
    if len(visible) < 2:                                 # low-confidence gate: too few tags seen
        conf = round(min(conf, 0.5), 2)
    return {"score": score, "band": _band(score), "status": "scored",
            "finding": finding, "confidence": conf, "checks": list(by.values())}


def _band(score):
    if score is None:
        return "neutral"
    if score <= 30:
        return "authentic"
    if score <= 60:
        return "caution"
    return "counterfeit"


# ---------------------------------------------------------------------------
# Logo dimension — forensic primitive rubric.
#
# The model reports per-primitive deviation + evidence; the composite is
# computed HERE, server-side, so the roll-up is auditable and identical across
# engines. Mirrors the Label dimension's split of "model observes / server
# scores".
#
# Weights follow the rubric: application evidence is 3x geometry and placement,
# because how the mark was APPLIED is far harder to fake than its outline.
# ---------------------------------------------------------------------------
_LOGO_GEOMETRY = [
    "arc_radius_ratios", "inter_arc_gap", "stroke_uniformity", "terminal_geometry",
    "arc_eccentricity", "bounding_box_ratio", "wordmark_kerning", "letterform_contour",
    "cap_height_stroke_ratio", "baseline_deviation", "lockup_variant",
]
_LOGO_APPLICATION = {
    "embroidery": ["satin_angle_consistency", "stitch_density_cv", "underlay_present",
                   "edge_thread_creep", "thread_sheen", "backing_visible_reverse"],
    "rubberised": ["edge_bevel_profile", "mould_parting_line", "flash_trim",
                   "surface_gloss", "thickness_width_ratio"],
    "screen": ["ink_edge_raggedness", "halftone_structure", "layer_registration_offset",
               "ink_sits_on_or_in_weave"],
    # The rubric defines no primitive list for 'transfer'; whatever the model
    # reports is accepted and weighted as application evidence.
    "transfer": [],
}
_LOGO_PLACEMENT = ["offset_from_placket", "offset_from_shoulder_seam",
                   "rotation_vs_grainline", "multi_logo_consistency"]

_LOGO_APPLICATION_ALL = {n for names in _LOGO_APPLICATION.values() for n in names}
_LOGO_PRIMITIVE_NAMES = sorted(set(_LOGO_GEOMETRY) | _LOGO_APPLICATION_ALL | set(_LOGO_PLACEMENT))

_LOGO_WEIGHT_GEOMETRY = 1
_LOGO_WEIGHT_APPLICATION = 3
_LOGO_WEIGHT_PLACEMENT = 1

# Cutoffs for the rubric's assessment vocabulary. Aligned with the pipeline's
# existing bands (<=30 / <=60 / above) so the Logo card reads consistently with
# every other dimension.
_LOGO_MINOR_AT = 30
_LOGO_SIGNIFICANT_AT = 60

_LOGO_CONF_POINTS = {"high": 0.9, "medium": 0.6, "low": 0.3}

_LOGO_PROMPT = """ROLE
You are a forensic logo analyst. You compare a SUBMITTED logo against
verified-authentic REFERENCE images of the same logo variant, and report
measurable deviation. You do not determine authenticity of the product.

INPUTS
- SUBMITTED: macro crop(s) of the logo. May include a reverse-side image.
- REFERENCE: verified-authentic images of the same variant, same product
  family, same season.
- METADATA: product family, season, expected lockup variant.

HARD RULES
1. If no REFERENCE image is provided, return
   {"error": "NO_REFERENCE"} and nothing else. Never assess from memory
   of what the brand's logo looks like.
2. If a primitive is not resolvable in the supplied pixels, return
   "INSUFFICIENT" for it. Do not estimate. Do not infer a value from
   what is typical for this brand. An INSUFFICIENT is a correct answer.
3. Report evidence BEFORE assigning any score. The evidence must
   describe what you observe in the submitted image and how it differs
   from the reference, in spatial terms.
4. Use only ratios and normalised measurements, never absolute pixel
   sizes, so results are scale-invariant.
5. Ignore all context: seller, price, packaging, wear, background,
   image quality as a proxy for legitimacy. Assess the logo only.
6. Never output a counterfeit verdict. Logo evidence alone is
   insufficient. Your maximum adverse output is
   "deviation_significant".
7. Do not be swayed by overall impression. Score each primitive
   independently before producing any summary.

PRIMITIVES — GEOMETRY (weight 1x)
  arc_radius_ratios        r1:r2:r3 of the three nested arcs
  inter_arc_gap            gap width / stroke width
  stroke_uniformity        coefficient of variation of stroke width
  terminal_geometry        cut angle and style at arc ends
  arc_eccentricity         ellipse axis ratio per arc
  bounding_box_ratio       full mark W:H
  wordmark_kerning         inter-letter distance vector, cosine vs ref
  letterform_contour       per-glyph IoU, prioritise R, G, S
  cap_height_stroke_ratio
  baseline_deviation       max vertical drift / cap height
  lockup_variant           which variant; is it valid for this
                           family and season

PRIMITIVES — APPLICATION (weight 3x)
  Determine method first: embroidery | rubberised | screen | transfer.
  Score only the primitives for the detected method.

  embroidery:  satin_angle_consistency, stitch_density_cv,
               underlay_present, edge_thread_creep, thread_sheen,
               backing_visible_reverse
  rubberised:  edge_bevel_profile, mould_parting_line, flash_trim,
               surface_gloss, thickness_width_ratio
  screen:      ink_edge_raggedness, halftone_structure,
               layer_registration_offset, ink_sits_on_or_in_weave

PRIMITIVES — PLACEMENT (weight 1x)
  offset_from_placket / chest_width
  offset_from_shoulder_seam
  rotation_vs_grainline
  multi_logo_consistency   chest vs back vs sleeve

SCORING
Each primitive: deviation 0-100 (0 = indistinguishable from reference)
and confidence high|medium|low. Compute the weighted mean over
resolvable primitives only, then take:

  logo_deviation = max(weighted_mean, 0.85 * max_single_deviation)

If 3 or more primitives are INSUFFICIENT, or if any application
primitive is INSUFFICIENT, set assessment to "INSUFFICIENT_CAPTURE"
and request specific recapture.

OUTPUT — valid JSON only, no preamble, no markdown fences
{
  "reference_used": true,
  "application_method": "embroidery|rubberised|screen|transfer|UNKNOWN",
  "primitives": [
    {"name": "...", "deviation": 0-100 | "INSUFFICIENT",
     "evidence": "...", "confidence": "high|medium|low"}
  ],
  "logo_deviation": 0-100 | null,
  "assessment": "consistent_with_reference | minor_deviation |
                 deviation_significant | INSUFFICIENT_CAPTURE",
  "top_deviations": ["primitive names, worst first"],
  "capture_issues": ["..."],
  "recapture_instructions": ["specific, actionable"]
}

TRANSPORT NOTE: the response schema is enforced, so every key above must be
present. To signal HARD RULE 1 under that constraint, set "error" to
"NO_REFERENCE" and "reference_used" to false; leave "primitives" empty. In
every other case set "error" to "".""" + _ESTIMATE_NOTE  # noqa: E501

# ---------------------------------------------------------------------------
# Stitching dimension — forensic primitive rubric.
# Same contract as Logo: the 3x group is CONSTRUCTION, because a counterfeiter
# can approximate stitch pitch by eye but cannot cheaply change machine class.
# ---------------------------------------------------------------------------
_STITCH_METRICS = [
    "stitch_pitch_ratio", "pitch_uniformity_cv", "seam_allowance_ratio",
    "seam_straightness", "topstitch_row_spacing", "topstitch_row_count",
    "corner_junction_handling", "bartack_presence_length",
]
_STITCH_COMMON = [
    "skipped_or_broken_stitches", "seam_pucker_index", "raw_edge_finish",
    "thread_type_and_twist", "needle_penetration_angle",
]
_STITCH_CONSTRUCTION = {
    "lockstitch": ["tension_balance", "bobbin_interlock_position", "needle_thread_ratio"]
                  + _STITCH_COMMON,
    "coverstitch": ["tension_balance", "bobbin_interlock_position", "needle_thread_ratio"]
                   + _STITCH_COMMON,
    "chainstitch": ["tension_balance", "needle_thread_ratio"] + _STITCH_COMMON,
    "overlock": ["thread_count_in_overlock", "looper_thread_balance",
                 "edge_encasement_completeness"] + _STITCH_COMMON,
    "flatlock": ["thread_count_in_overlock", "looper_thread_balance",
                 "edge_encasement_completeness"] + _STITCH_COMMON,
    "bonded": ["bond_line_width_ratio", "bond_edge_lift"] + _STITCH_COMMON,
}
_STITCH_ALIGNMENT = ["panel_seam_alignment", "pattern_match_across_seam",
                     "bilateral_symmetry", "seam_to_hardware_registration"]

_STITCHING_PROMPT = """ROLE
You are a forensic garment-construction analyst. You compare SUBMITTED
stitching against verified-authentic REFERENCE images of the same seam on
the same product family, and report measurable deviation. You do not
determine authenticity of the product.

INPUTS
- SUBMITTED: macro crop(s) of the seam. Interior views preferred.
- REFERENCE: verified-authentic images of the same seam, same product
  family, same season.
- METADATA: product family, season, expected seam class.

HARD RULES
1. If no REFERENCE image is provided, return
   {"error": "NO_REFERENCE"} and nothing else. Never assess from memory
   of how this brand normally stitches.
2. If a primitive is not resolvable in the supplied pixels, return
   "INSUFFICIENT" for it. Do not estimate. Do not infer a value from
   what is typical for this brand. An INSUFFICIENT is a correct answer.
3. Report evidence BEFORE assigning any score. The evidence must
   describe what you observe in the submitted image and how it differs
   from the reference, in spatial terms.
4. Use only ratios and normalised measurements — stitches per unit of a
   named reference feature, widths as ratios to seam allowance — never
   absolute pixel sizes, so results are scale-invariant.
5. Ignore all context: seller, price, packaging, wear, background,
   image quality as a proxy for legitimacy. Assess the stitching only.
6. Never output a counterfeit verdict. Stitching evidence alone is
   insufficient. Your maximum adverse output is
   "deviation_significant".
7. Do not be swayed by overall impression. Score each primitive
   independently before producing any summary.
8. Laundering, wear, creasing and pressing marks are CONDITION, not
   deviation. Score construction only.

PRIMITIVES — METRICS (weight 1x)
  stitch_pitch_ratio        stitches per seam-allowance width vs ref
  pitch_uniformity_cv       coefficient of variation of stitch length
  seam_allowance_ratio      allowance width / adjacent panel feature
  seam_straightness         max lateral drift / seam run length
  topstitch_row_spacing     row gap / stitch length
  topstitch_row_count       number of parallel rows
  corner_junction_handling  how rows terminate, turn and cross
  bartack_presence_length   bartack length / seam width at stress points

PRIMITIVES — CONSTRUCTION (weight 3x)
  Determine seam class first: lockstitch | chainstitch | overlock |
  flatlock | coverstitch | bonded.
  Score only the primitives for the detected class.

  overlock / flatlock:      thread_count_in_overlock, looper_thread_balance,
                            edge_encasement_completeness
  lockstitch / coverstitch: tension_balance, bobbin_interlock_position,
                            needle_thread_ratio
  chainstitch:              tension_balance, needle_thread_ratio
  bonded:                   bond_line_width_ratio, bond_edge_lift
  every class:              skipped_or_broken_stitches, seam_pucker_index,
                            raw_edge_finish, thread_type_and_twist,
                            needle_penetration_angle

PRIMITIVES — ALIGNMENT (weight 1x)
  panel_seam_alignment
  pattern_match_across_seam
  bilateral_symmetry           left vs right equivalent seams
  seam_to_hardware_registration

SCORING
Each primitive: deviation 0-100 (0 = indistinguishable from reference)
and confidence high|medium|low. Compute the weighted mean over
resolvable primitives only, then take:

  stitching_deviation = max(weighted_mean, 0.85 * max_single_deviation)

If 3 or more primitives are INSUFFICIENT, or if any construction
primitive is INSUFFICIENT, set assessment to "INSUFFICIENT_CAPTURE"
and request specific recapture."""


# ---------------------------------------------------------------------------
# Hardware dimension — forensic primitive rubric.
# The 3x group is MARKING & FINISH: foundry stamps, plating and casting
# artefacts are the components a counterfeit supply chain most often gets wrong.
# ---------------------------------------------------------------------------
_HW_GEOMETRY = [
    "pull_dimension_ratios", "slider_body_proportions", "tooth_pitch_width_ratio",
    "tape_width_ratio", "component_diameter_ratio", "head_profile_curvature",
    "bounding_box_ratio",
]
_HW_COMMON = [
    "brand_stamp_present", "stamp_typography", "stamp_depth_uniformity",
    "plating_type_and_wear", "edge_burr_or_flash", "surface_finish_gloss",
    "colour_delta_vs_reference",
]
_HW_MARKING = {
    "zip": ["foundry_code", "tooth_material_cues", "slider_lock_mechanism"] + _HW_COMMON,
    "snap": ["socket_stud_registration", "spring_ring_visible"] + _HW_COMMON,
    "rivet": ["setting_deformation", "back_plate_marking"] + _HW_COMMON,
    "buckle": ["casting_parting_line", "load_bar_thickness_ratio"] + _HW_COMMON,
    "drawcord_aglet": ["crimp_pattern", "aglet_wall_thickness_ratio"] + _HW_COMMON,
    "button": ["shank_or_hole_pattern", "rim_profile"] + _HW_COMMON,
}
_HW_ASSEMBLY = ["box_and_pin_alignment", "stop_placement", "garage_or_pocket_present",
                "attachment_method", "operation_smoothness_cues"]

_HARDWARE_PROMPT = """ROLE
You are a forensic trim-and-hardware analyst. You compare SUBMITTED
hardware against verified-authentic REFERENCE images of the same
component on the same product family, and report measurable deviation.
You do not determine authenticity of the product.

INPUTS
- SUBMITTED: macro crop(s) of the hardware. Both faces where available.
- REFERENCE: verified-authentic images of the same component, same
  product family, same season.
- METADATA: product family, season, expected component supplier.

HARD RULES
1. If no REFERENCE image is provided, return
   {"error": "NO_REFERENCE"} and nothing else. Never assess from memory
   of what this brand's hardware looks like.
2. If a primitive is not resolvable in the supplied pixels, return
   "INSUFFICIENT" for it. Do not estimate. Do not infer a value from
   what is typical for this brand. An INSUFFICIENT is a correct answer.
3. Report evidence BEFORE assigning any score. The evidence must
   describe what you observe in the submitted image and how it differs
   from the reference, in spatial terms.
4. Use only ratios and normalised measurements, never absolute pixel
   sizes, so results are scale-invariant.
5. Ignore all context: seller, price, packaging, wear, background,
   image quality as a proxy for legitimacy. Assess the hardware only.
6. Never output a counterfeit verdict. Hardware evidence alone is
   insufficient. Your maximum adverse output is
   "deviation_significant".
7. Do not be swayed by overall impression. Score each primitive
   independently before producing any summary.
8. If the garment carries NO hardware of the expected type, that is not
   a defect: set component_type to "UNKNOWN" and return
   INSUFFICIENT_CAPTURE rather than scoring an absence as deviation.
9. Tarnish, scratching and plating wear are CONDITION, not deviation,
   except where the wear pattern itself reveals a different plating
   process than the reference.

PRIMITIVES — GEOMETRY (weight 1x)
  pull_dimension_ratios        pull L:W:thickness
  slider_body_proportions      body L:W vs tape width
  tooth_pitch_width_ratio      tooth pitch / tooth width
  tape_width_ratio             tape width / slider body width
  component_diameter_ratio     snap/rivet/button diameter vs a named feature
  head_profile_curvature       dome or bevel profile of the visible head
  bounding_box_ratio           full component W:H

PRIMITIVES — MARKING & FINISH (weight 3x)
  Determine component first: zip | snap | rivet | buckle |
  drawcord_aglet | button.
  Score only the primitives for the detected component.

  zip:             foundry_code, tooth_material_cues, slider_lock_mechanism
  snap:            socket_stud_registration, spring_ring_visible
  rivet:           setting_deformation, back_plate_marking
  buckle:          casting_parting_line, load_bar_thickness_ratio
  drawcord_aglet:  crimp_pattern, aglet_wall_thickness_ratio
  button:          shank_or_hole_pattern, rim_profile
  every component: brand_stamp_present, stamp_typography,
                   stamp_depth_uniformity, plating_type_and_wear,
                   edge_burr_or_flash, surface_finish_gloss,
                   colour_delta_vs_reference

PRIMITIVES — ASSEMBLY (weight 1x)
  box_and_pin_alignment
  stop_placement
  garage_or_pocket_present
  attachment_method            stitched | riveted | moulded
  operation_smoothness_cues    visible binding, gaping, misalignment

SCORING
Each primitive: deviation 0-100 (0 = indistinguishable from reference)
and confidence high|medium|low. Compute the weighted mean over
resolvable primitives only, then take:

  hardware_deviation = max(weighted_mean, 0.85 * max_single_deviation)

If 3 or more primitives are INSUFFICIENT, or if any marking-and-finish
primitive is INSUFFICIENT, set assessment to "INSUFFICIENT_CAPTURE"
and request specific recapture."""


# ---------------------------------------------------------------------------
# Material dimension — forensic primitive rubric.
# The 3x group is STRUCTURE: weave/knit architecture is set by the loom and is
# the hardest property to substitute without changing the whole supply chain.
# ---------------------------------------------------------------------------
_MAT_COMMON = ["yarn_twist_direction", "yarn_width_uniformity", "surface_regularity"]
_MAT_STRUCTURE = {
    "woven": ["weave_type", "thread_count_ratio", "ripstop_grid_pitch_ratio",
              "float_length"] + _MAT_COMMON,
    "knit": ["knit_type", "gauge_ratio", "wale_course_ratio", "loop_shape"] + _MAT_COMMON,
    "fleece_pile": ["pile_height_ratio", "pile_density", "backing_structure"] + _MAT_COMMON,
    "coated_laminate": ["coating_continuity", "laminate_layer_count",
                        "membrane_visible_at_edge"] + _MAT_COMMON,
    "nonwoven": ["fibre_orientation_randomness", "bond_point_pattern"] + _MAT_COMMON,
}
_MAT_SURFACE = ["sheen_at_angle", "coating_presence", "dwr_beading_cues",
                "drape_fold_radius", "dye_penetration", "nap_direction"]
_MAT_CONSISTENCY = ["panel_to_panel_consistency", "shell_lining_consistency",
                    "content_matches_care_label", "hand_feel_proxy"]

_MATERIAL_PROMPT = """ROLE
You are a forensic textile analyst. You compare SUBMITTED fabric against
verified-authentic REFERENCE images of the same material on the same
product family, and report measurable deviation. You do not determine
authenticity of the product.

INPUTS
- SUBMITTED: macro crop(s) of the fabric. Raking-light and edge views help.
- REFERENCE: verified-authentic images of the same material, same product
  family, same season.
- METADATA: product family, season, expected fabric specification.

HARD RULES
1. If no REFERENCE image is provided, return
   {"error": "NO_REFERENCE"} and nothing else. Never assess from memory
   of what this brand's fabric normally is.
2. If a primitive is not resolvable in the supplied pixels, return
   "INSUFFICIENT" for it. Do not estimate. Do not infer a value from
   what is typical for this brand. An INSUFFICIENT is a correct answer.
3. Report evidence BEFORE assigning any score. The evidence must
   describe what you observe in the submitted image and how it differs
   from the reference, in spatial terms.
4. Use only ratios and normalised measurements — counts per unit of a
   named reference feature, ratios between yarn and gap widths — never
   absolute pixel sizes, so results are scale-invariant.
5. Ignore all context: seller, price, packaging, wear, background,
   image quality as a proxy for legitimacy. Assess the material only.
6. Never output a counterfeit verdict. Material evidence alone is
   insufficient. Your maximum adverse output is
   "deviation_significant".
7. Do not be swayed by overall impression. Score each primitive
   independently before producing any summary.
8. Compression, laundering, pilling and creasing are CONDITION, not
   deviation. Score the structure, not the state.
9. Photographic white balance and exposure are NOT colour deviation.
   Only score colour where a neutral in-frame anchor makes it reliable.

PRIMITIVES — STRUCTURE (weight 3x)
  Determine structure first: woven | knit | fleece_pile |
  coated_laminate | nonwoven.
  Score only the primitives for the detected structure.

  woven:           weave_type, thread_count_ratio,
                   ripstop_grid_pitch_ratio, float_length
  knit:            knit_type, gauge_ratio, wale_course_ratio, loop_shape
  fleece_pile:     pile_height_ratio, pile_density, backing_structure
  coated_laminate: coating_continuity, laminate_layer_count,
                   membrane_visible_at_edge
  nonwoven:        fibre_orientation_randomness, bond_point_pattern
  every structure: yarn_twist_direction, yarn_width_uniformity,
                   surface_regularity

PRIMITIVES — SURFACE & FINISH (weight 1x)
  sheen_at_angle          specular response under raking light
  coating_presence        face or back coating visible at a cut edge
  dwr_beading_cues        only if moisture is present in frame
  drape_fold_radius       fold radius / fabric thickness proxy
  dye_penetration         face vs reverse colour depth
  nap_direction

PRIMITIVES — CONSISTENCY (weight 1x)
  panel_to_panel_consistency
  shell_lining_consistency
  content_matches_care_label   only if the care label is legible in the
                               SUBMITTED images; otherwise INSUFFICIENT
  hand_feel_proxy              inferred from drape and fold behaviour only

SCORING
Each primitive: deviation 0-100 (0 = indistinguishable from reference)
and confidence high|medium|low. Compute the weighted mean over
resolvable primitives only, then take:

  material_deviation = max(weighted_mean, 0.85 * max_single_deviation)

If 3 or more primitives are INSUFFICIENT, or if any structure
primitive is INSUFFICIENT, set assessment to "INSUFFICIENT_CAPTURE"
and request specific recapture."""


# The shared tail every rubric ends with — the OUTPUT contract. Only the
# discriminator key and the deviation key differ per dimension.
_RUBRIC_OUTPUT_TMPL = """

OUTPUT — valid JSON only, no preamble, no markdown fences
{{
  "reference_used": true,
  "{method_key}": "{method_enum}|UNKNOWN",
  "primitives": [
    {{"name": "...", "deviation": 0-100 | "INSUFFICIENT",
     "evidence": "...", "confidence": "high|medium|low"}}
  ],
  "{dev_key}": 0-100 | null,
  "assessment": "consistent_with_reference | minor_deviation |
                 deviation_significant | INSUFFICIENT_CAPTURE",
  "top_deviations": ["primitive names, worst first"],
  "capture_issues": ["..."],
  "recapture_instructions": ["specific, actionable"]
}}

TRANSPORT NOTE: the response schema is enforced, so every key above must be
present. To signal HARD RULE 1 under that constraint, set "error" to
"NO_REFERENCE" and "reference_used" to false; leave "primitives" empty. In
every other case set "error" to ""."""


# dim -> rubric spec. `heavy` primitives are the 3x group, selected by the
# dimension's discriminator; `light` groups are always 1x.
_RUBRICS = {
    "Logo": {
        "method_key": "application_method", "method_word": "application",
        "heavy": _LOGO_APPLICATION,
        "light": {"geometry": _LOGO_GEOMETRY, "placement": _LOGO_PLACEMENT},
        "dev_key": "logo_deviation", "prompt": _LOGO_PROMPT,
    },
    "Stitching": {
        "method_key": "construction_class", "method_word": "construction",
        "heavy": _STITCH_CONSTRUCTION,
        "light": {"metrics": _STITCH_METRICS, "alignment": _STITCH_ALIGNMENT},
        "dev_key": "stitching_deviation",
        "prompt": _ESTIMATE_NOTE.join([_STITCHING_PROMPT + _RUBRIC_OUTPUT_TMPL.format(
            method_key="construction_class", dev_key="stitching_deviation",
            method_enum="lockstitch|chainstitch|overlock|flatlock|coverstitch|bonded"), ""]),
    },
    "Hardware": {
        "method_key": "component_type", "method_word": "marking-and-finish",
        "heavy": _HW_MARKING,
        "light": {"geometry": _HW_GEOMETRY, "assembly": _HW_ASSEMBLY},
        "dev_key": "hardware_deviation",
        "prompt": _ESTIMATE_NOTE.join([_HARDWARE_PROMPT + _RUBRIC_OUTPUT_TMPL.format(
            method_key="component_type", dev_key="hardware_deviation",
            method_enum="zip|snap|rivet|buckle|drawcord_aglet|button"), ""]),
    },
    "Material": {
        "method_key": "structure_type", "method_word": "structure",
        "heavy": _MAT_STRUCTURE,
        "light": {"surface": _MAT_SURFACE, "consistency": _MAT_CONSISTENCY},
        "dev_key": "material_deviation",
        "prompt": _ESTIMATE_NOTE.join([_MATERIAL_PROMPT + _RUBRIC_OUTPUT_TMPL.format(
            method_key="structure_type", dev_key="material_deviation",
            method_enum="woven|knit|fleece_pile|coated_laminate|nonwoven"), ""]),
    },
}

RUBRIC_DIMENSIONS = tuple(_RUBRICS)


def _heavy_names(dim):
    return {n for names in _RUBRICS[dim]["heavy"].values() for n in names}


def _rubric_schema(dim):
    spec = _RUBRICS[dim]
    return {
        "type": "object",
        "properties": {
            # HARD RULE 1's {"error": "NO_REFERENCE"} signal, made expressible
            # under a schema that requires every key to be present.
            "error": {"type": "string", "enum": ["", "NO_REFERENCE"]},
            "reference_used": {"type": "boolean"},
            spec["method_key"]: {"type": "string",
                                 "enum": list(spec["heavy"]) + ["UNKNOWN"]},
            "primitives": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        # 0-100, or the literal "INSUFFICIENT" — the contract.
                        "deviation": {"anyOf": [{"type": "integer"},
                                                {"type": "string",
                                                 "enum": ["INSUFFICIENT"]}]},
                        "evidence": {"type": "string"},
                        "confidence": {"type": "string",
                                       "enum": ["high", "medium", "low"]},
                    },
                    "required": ["name", "deviation", "evidence", "confidence"],
                    "additionalProperties": False,
                },
            },
            spec["dev_key"]: {"anyOf": [{"type": "integer"}, {"type": "null"}]},
            "assessment": {"type": "string",
                           "enum": ["consistent_with_reference", "minor_deviation",
                                    "deviation_significant", "INSUFFICIENT_CAPTURE"]},
            "top_deviations": {"type": "array", "items": {"type": "string"}},
            "capture_issues": {"type": "array", "items": {"type": "string"}},
            "recapture_instructions": {"type": "array", "items": {"type": "string"}},
            "best_estimate_deviation": {"type": "integer"},
        },
        "required": ["best_estimate_deviation",
                     "error", "reference_used", spec["method_key"], "primitives",
                     spec["dev_key"], "assessment", "top_deviations", "capture_issues",
                     "recapture_instructions"],
        "additionalProperties": False,
    }


_LOGO_SCHEMA = _rubric_schema("Logo")


def _rubric_weight(dim, name, method):
    """Rubric weight for a primitive. The dimension's heavy group counts 3x."""
    spec = _RUBRICS[dim]
    for names in spec["light"].values():
        if name in names:
            return _LOGO_WEIGHT_GEOMETRY               # every light group is 1x
    if name in _heavy_names(dim):
        return _LOGO_WEIGHT_APPLICATION
    # An unlisted name reported under a detected method (e.g. Logo's 'transfer',
    # for which the rubric defines no list) still counts as heavy evidence.
    if method in spec["heavy"] and method != "UNKNOWN":
        return _LOGO_WEIGHT_APPLICATION
    return _LOGO_WEIGHT_GEOMETRY


def _aggregate_rubric(dim, parsed):
    """Primitive rows -> deviation, assessment, finding, confidence.

    Server-authoritative and shared by every rubric dimension: applies the
    roll-up `max(weighted_mean, 0.85 * max_single_deviation)` over RESOLVABLE
    primitives only, then the INSUFFICIENT_CAPTURE gates. Returns score None
    for any insufficient outcome — the caller turns that into an abstention.
    """
    spec = _RUBRICS[dim]
    heavy_all = _heavy_names(dim)
    method = (parsed.get(spec["method_key"]) or "UNKNOWN").strip()
    prims = [p for p in (parsed.get("primitives") or []) if isinstance(p, dict)]
    capture_issues = [str(x) for x in (parsed.get("capture_issues") or [])]
    recapture = [str(x) for x in (parsed.get("recapture_instructions") or [])]

    def _insufficient(finding, extra_issue=None):
        issues = capture_issues + ([extra_issue] if extra_issue else [])
        return {"score": None, "band": "neutral", "status": "abstain",
                "assessment": "INSUFFICIENT_CAPTURE", "finding": finding,
                "confidence": 0.3, "method": method, "primitives": prims,
                "capture_issues": issues, "recapture_instructions": recapture,
                "top_deviations": []}

    # HARD RULE 1 — never assess from memory of the brand.
    if str(parsed.get("error") or "").upper() == "NO_REFERENCE" \
            or not parsed.get("reference_used", False):
        return {**_insufficient(f"NO_REFERENCE — no verified-authentic reference image "
                                f"was available, so no {dim.lower()} comparison was made."),
                "assessment": "NO_REFERENCE"}

    resolvable, insufficient_names = [], []
    for p in prims:
        name = str(p.get("name") or "").strip()
        dev = p.get("deviation")
        if isinstance(dev, bool) or dev is None or isinstance(dev, str):
            insufficient_names.append(name)          # "INSUFFICIENT" (or unusable)
            continue
        try:
            val = float(dev)
        except (TypeError, ValueError):
            insufficient_names.append(name)
            continue
        resolvable.append((name, max(0.0, min(100.0, val)),
                           str(p.get("confidence") or "low").lower(),
                           str(p.get("evidence") or "")))

    if not resolvable:
        return _insufficient(f"INSUFFICIENT_CAPTURE — no {dim.lower()} primitive was "
                             f"resolvable in the supplied photos.")

    # SCORING gate: any unresolved HEAVY primitive, or 3+ unresolved overall.
    word = spec["method_word"]
    heavy_insufficient = [n for n in insufficient_names if n in heavy_all]
    if method == "UNKNOWN":
        return _insufficient(
            f"INSUFFICIENT_CAPTURE — {spec['method_key'].replace('_', ' ')} "
            f"({' / '.join(spec['heavy'])}) could not be determined, so the "
            f"3x-weighted {word} evidence is unavailable.",
            f"{spec['method_key']} not resolvable")
    if heavy_insufficient:
        return _insufficient(
            f"INSUFFICIENT_CAPTURE — {word} primitive(s) not resolvable: "
            f"{', '.join(sorted(set(heavy_insufficient)))}.",
            f"{word} primitives not resolvable")
    if len(insufficient_names) >= 3:
        return _insufficient(
            f"INSUFFICIENT_CAPTURE — {len(insufficient_names)} primitives were not "
            f"resolvable: {', '.join(sorted(set(n for n in insufficient_names if n)))}.")

    num = den = 0.0
    for name, val, _conf, _ev in resolvable:
        w = _rubric_weight(dim, name, method)
        num += val * w
        den += w
    weighted_mean = num / den if den else 0.0
    worst_name, worst_val = max(((n, v) for n, v, _c, _e in resolvable), key=lambda t: t[1])
    # Rubric roll-up: one severe primitive must not be averaged away by many
    # unremarkable ones.
    score = int(round(max(0.0, min(100.0, max(weighted_mean, 0.85 * worst_val)))))

    if score <= _LOGO_MINOR_AT:
        assessment = "consistent_with_reference"
    elif score <= _LOGO_SIGNIFICANT_AT:
        assessment = "minor_deviation"
    else:
        assessment = "deviation_significant"      # HARD RULE 6 — the adverse ceiling

    ranked = sorted(resolvable, key=lambda t: -t[1])
    top = [n for n, v, _c, _e in ranked if v > 0][:3]
    lead = next((e for _n, _v, _c, e in ranked if e), "")
    finding = f"{assessment.replace('_', ' ')} ({score}/100)"
    if top:
        finding += f" — worst: {top[0]}"
    if lead:
        finding += f". {lead}"
    conf = round(sum(_LOGO_CONF_POINTS.get(c, 0.3) for _n, _v, c, _e in resolvable)
                 / len(resolvable), 2)
    return {"score": score, "band": _band(score), "status": "scored",
            "assessment": assessment, "finding": finding, "confidence": conf,
            "method": method, "primitives": prims, "capture_issues": capture_issues,
            "recapture_instructions": recapture, "top_deviations": top,
            "worst_primitive": worst_name, "weighted_mean": round(weighted_mean, 1)}


def _rubric_dimension(cfg, dim, suspect_b64s, ref_b64s, t0):
    """A rubric dimension for any OpenAI-compatible engine: runs the forensic
    primitive rubric, then computes the deviation server-side."""
    spec = _RUBRICS[dim]
    # HARD RULE 1 enforced before spending a call — with no reference there is
    # nothing to compare against, and assessing from brand memory is exactly
    # what the rubric forbids.
    if not ref_b64s:
        agg = _aggregate_rubric(dim, {"reference_used": False})
        usage = {"agent": dim, "model": cfg["label"], "tokens_in": 0, "tokens_out": 0,
                 "latency_ms": int((time.time() - t0) * 1000)}
        return _rubric_result(dim, agg), usage

    content = [{"type": "text", "text": spec["prompt"]}]
    for i, b in enumerate(suspect_b64s):
        content.append({"type": "text", "text": f"SUBMITTED photo {i + 1}:"})
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b}", "detail": "high"}})
    for j, rb in enumerate(ref_b64s):
        content.append({"type": "text", "text": f"REFERENCE (verified authentic) {j + 1}:"})
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{rb}", "detail": "high"}})
    parsed, tin, tout = _chat(cfg, content, _rubric_schema(dim), dim.lower(), CHAT_TIMEOUT)
    estimate = _estimate_from(parsed)
    # json_object fallback mode may return the rubric's bare error object.
    if str(parsed.get("error") or "").upper() == "NO_REFERENCE":
        parsed = {"reference_used": False}
    agg = _aggregate_rubric(dim, parsed)
    agg["estimate"] = estimate
    usage = {"agent": dim, "model": cfg["label"], "tokens_in": tin, "tokens_out": tout,
             "latency_ms": int((time.time() - t0) * 1000)}
    return _rubric_result(dim, agg), usage


def _rubric_result(dim, agg):
    spec = _RUBRICS[dim]
    result = {
        "dimension": dim, "score": agg["score"], "band": agg["band"],
        "finding": agg["finding"],
        "reasoning": (f"{spec['method_key'].replace('_', ' ').capitalize()}: "
                      f"{agg['method']}. "
                      + ("; ".join(agg.get("recapture_instructions") or [])
                         if agg["score"] is None
                         else f"Weighted mean {agg.get('weighted_mean')}, worst primitive "
                              f"{agg.get('worst_primitive')}.")),
        "box": _BOXES[dim], "confidence": agg["confidence"], "status": agg["status"],
        "assessment": agg["assessment"], "method": agg["method"],
        spec["method_key"]: agg["method"],
        "primitives": agg.get("primitives") or [],
        "top_deviations": agg.get("top_deviations") or [],
        "capture_issues": agg.get("capture_issues") or [],
        "recapture_instructions": agg.get("recapture_instructions") or [],
        "insufficient_reason": (agg["finding"] if agg["score"] is None else ""),
    }
    return _fill_estimate(result, agg.get("estimate", 50))


# Back-compat aliases — the Logo rubric landed first and is referenced by name.
def _aggregate_logo(parsed):
    return _aggregate_rubric("Logo", parsed)


def _logo_result(agg):
    return _rubric_result("Logo", agg)


def _logo_dimension(cfg, suspect_b64s, ref_b64s, t0):
    return _rubric_dimension(cfg, "Logo", suspect_b64s, ref_b64s, t0)


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
            return _chat_dimension(cfg, dim, imgs, refs, t0, brand)
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


def _gemini_dimension(dim, suspect_b64s, reference_file, t0, brand="TNF"):
    ref_b64 = _img_b64(reference_file)
    schema = {k: v for k, v in _DIM_SCHEMA.items() if k != "additionalProperties"}
    prompt = _dim_prompt(dim, brand)
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
    return _dim_result(dim, parsed, GEMINI_LABEL,
                       um.get("promptTokenCount", 0), um.get("candidatesTokenCount", 0), t0)


def _label_dimension(cfg, suspect_b64s, ref_b64s, t0):
    """Label dimension for any OpenAI-compatible engine: runs the authentication
    rubric, then computes the score server-side via _aggregate_label."""
    content = [{"type": "text", "text": _LABEL_PROMPT}]
    for i, b in enumerate(suspect_b64s):
        content.append({"type": "text", "text": f"SUSPECT photo {i + 1}:"})
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b}", "detail": "high"}})
    for j, rb in enumerate(ref_b64s):
        content.append({"type": "text", "text": f"AUTHENTIC REFERENCE {j + 1}:"})
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{rb}", "detail": "high"}})
    parsed, tin, tout = _chat(cfg, content, _LABEL_SCHEMA, "label", CHAT_TIMEOUT)
    agg = _aggregate_label(parsed.get("checks"))
    label_estimate = _estimate_from(parsed)
    summary = (parsed.get("summary_finding") or "").strip()
    usage = {"agent": "Label", "model": cfg["label"], "tokens_in": tin, "tokens_out": tout,
             "latency_ms": int((time.time() - t0) * 1000)}
    result = {
        "dimension": "Label", "score": agg["score"], "band": agg["band"],
        "finding": agg["finding"], "reasoning": summary or agg["finding"],
        "box": _BOXES["Label"], "confidence": agg["confidence"],
        "status": agg["status"], "checks": agg["checks"],
    }
    return _fill_estimate(result, label_estimate), usage


def _chat_dimension(cfg, dim, suspect_b64s, ref_b64s, t0, brand="TNF"):
    # Every dimension now runs an explicit rubric whose roll-up is computed
    # server-side. Label uses its own check-list form; Logo, Stitching, Hardware
    # and Material share the forensic-primitive form. The generic comparison
    # prompt below survives only as a fallback for an unrecognised dimension.
    if dim == "Label":
        return _label_dimension(cfg, suspect_b64s, ref_b64s, t0)
    if dim in _RUBRICS:
        return _rubric_dimension(cfg, dim, suspect_b64s, ref_b64s, t0)
    content = [{"type": "text", "text": _dim_prompt(dim, brand)}]
    for i, b in enumerate(suspect_b64s):
        content.append({"type": "text", "text": f"SUSPECT photo {i + 1}:"})
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b}", "detail": "high"}})
    for j, rb in enumerate(ref_b64s):
        content.append({"type": "text", "text": f"AUTHENTIC REFERENCE {j + 1}:"})
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{rb}", "detail": "high"}})
    if not ref_b64s:
        content.append({"type": "text", "text":
                        "NOTE: no authentic reference photo is available for this dimension. "
                        "You have nothing to compare against — set assessable to false."})
    parsed, tin, tout = _chat(cfg, content, _DIM_SCHEMA, "dimension", CHAT_TIMEOUT)
    return _dim_result(dim, parsed, cfg["label"], tin, tout, t0)


# ---------------------------------------------------------------------------
# Label identity — OCR by the model, VALIDATION by deterministic rules.
#
# The model's only job here is to read fields off the tag. Everything that
# decides anything lives in label_rules.py: fibre sums, style syntax, RN
# resolution against the FTC registry, cross-field agreement, batch duplicates.
# A hard fail from those rules is auditable and needs no vision confidence.
# ---------------------------------------------------------------------------
_LABEL_ID_SCHEMA = {
    "type": "object",
    "properties": {
        "rn": {"type": "string"}, "ca": {"type": "string"},
        "style_number": {"type": "string"}, "fiber_content": {"type": "string"},
        "country_of_origin": {"type": "string"},
        "size_neck": {"type": "string"}, "size_care": {"type": "string"},
        "care_text": {"type": "string"}, "product_family": {"type": "string"},
        "legible": {"type": "boolean"},
    },
    "required": ["rn", "ca", "style_number", "fiber_content", "country_of_origin",
                 "size_neck", "size_care", "care_text", "product_family", "legible"],
    "additionalProperties": False,
}

_LABEL_ID_PROMPT = (
    "TRANSCRIBE ONLY. You are an OCR step, not an analyst. Read the label and tag "
    "text in these photos and return the fields verbatim.\n\n"
    "Rules:\n"
    "- Copy characters EXACTLY as printed, including spacing, punctuation and any "
    "misspellings. Do NOT correct anything — a misspelling is evidence, and "
    "silently fixing it destroys it.\n"
    "- Return an EMPTY STRING for any field you cannot read. Never guess, never "
    "infer from what this brand usually prints.\n"
    "- rn / ca: digits only, without the 'RN'/'CA' prefix.\n"
    "- style_number: the product style code only (e.g. NF0A3C8D or A71V). RN, CA "
    "and RW codes are NOT style numbers — leave style_number empty if only those "
    "are visible.\n"
    "- fiber_content: the whole fibre declaration verbatim, including component "
    "headers like 'SHELL:' and phrases like 'EXCLUSIVE OF DECORATION'.\n"
    "- size_neck / size_care: size as printed on the neck tag and on the care tag "
    "respectively; leave a field empty if that tag is not shown.\n"
    "- care_text: the care/washing instruction text verbatim.\n"
    "- legible: true only if at least one tag was clearly readable.\n\n"
    "Return JSON only."
)


def run_label_identity(brand, suspect_images, prior=None, provider="openai"):
    """OCR the label, then validate deterministically. Returns (result, usage|None).

    The returned dict always carries `validation` from label_rules — including
    when OCR was impossible, in which case every check reports UNKNOWN rather
    than passing by default."""
    t0 = time.time()
    imgs = [b for b in (suspect_images or []) if b][:4]
    cfg = _cfg(provider)
    if not cfg or not imgs:
        return {"ran": False, "fields": {}, "legible": False,
                "validation": label_rules.validate({}, brand=brand, prior=prior),
                "note": "Label identity not run (no provider or no suspect photo)."}, None

    content = [{"type": "text", "text": _LABEL_ID_PROMPT}]
    for i, b in enumerate(imgs):
        content.append({"type": "text", "text": f"LABEL photo {i + 1}:"})
        content.append({"type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{b}", "detail": "high"}})
    try:
        parsed, tin, tout = _chat(cfg, content, _LABEL_ID_SCHEMA, "label_identity",
                                  CHAT_TIMEOUT)
    except Exception as e:
        # OCR failure must not fabricate a result; every rule reports UNKNOWN.
        return {"ran": False, "fields": {}, "legible": False,
                "validation": label_rules.validate({}, brand=brand, prior=prior),
                "note": f"Label OCR failed ({e}); deterministic checks unavailable."}, None

    fields = {k: (str(parsed.get(k) or "").strip()) for k in _LABEL_ID_SCHEMA["properties"]
              if k != "legible"}
    validation = label_rules.validate(fields, brand=brand, prior=prior)
    usage = {"agent": "Label ID", "model": cfg["label"], "tokens_in": tin,
             "tokens_out": tout, "latency_ms": int((time.time() - t0) * 1000)}
    return {"ran": True, "fields": fields, "legible": bool(parsed.get("legible")),
            "validation": validation, "note": validation["summary"]}, usage


# ---------------------------------------------------------------------------
# Pairing gate — runs BEFORE any dimension scoring.
#
# Every dimension score is a statement about deviation from a reference. If the
# reference is a different product, the deviation is meaningless and the whole
# run is void. This gate catches that case up front instead of letting five
# agents produce confident numbers about an incomparable pair.
# ---------------------------------------------------------------------------
PAIRING_MIN_CONFIDENCE = float(os.environ.get("PAIRING_MIN_CONFIDENCE", "0.7"))

_PAIRING_SCHEMA = {
    "type": "object",
    "properties": {
        "same_product_type": {"type": "boolean"},
        "suspect_item": {"type": "string"},
        "reference_item": {"type": "string"},
        "confidence": {"type": "number"},
        "note": {"type": "string"},
    },
    "required": ["same_product_type", "suspect_item", "reference_item", "confidence", "note"],
    "additionalProperties": False,
}

_PAIRING_PROMPT = (
    "You are validating the INPUTS to a counterfeit authentication before any scoring runs.\n\n"
    "Decide one thing: do the SUSPECT photos and the AUTHENTIC REFERENCE photos show the "
    "same kind of product, such that a like-for-like forensic comparison is meaningful?\n\n"
    "Name the item in each set as specifically as the photos allow (e.g. 'down puffer "
    "jacket', 'cotton crew-neck t-shirt', 'knit beanie', 'close-up of an interior care "
    "label'). Then set same_product_type:\n"
    "  - false — ONLY when the two sets positively show DIFFERENT product categories, "
    "e.g. the suspect is plainly a t-shirt and the reference is plainly a jacket.\n"
    "  - true  — in every other case.\n\n"
    "IMPORTANT: submissions routinely pair close-up evidence shots of the SUSPECT (care "
    "label, neck tag, stitching, a zip) against a full-garment REFERENCE photo. That is "
    "the NORMAL shape of this work, not a mismatch. A detail shot simply does not reveal "
    "the category, so it cannot contradict the reference — return true with a low "
    "confidence and say in 'note' that the category was not determinable. Do the same "
    "whenever you are unsure for any other reason.\n\n"
    "Report confidence 0-1. Judge ONLY what the photos show — never infer from a product "
    "title. A confident mismatch voids the entire analysis and discards every dimension "
    "score, so reserve it for a contradiction you can actually see."
)


def run_pairing_check(brand, suspect_images, ref_b64s, provider="openai"):
    """Verify suspect and reference are comparable. Returns (result, usage|None).

    status is 'ok' (comparable), 'mismatch' (do not score), or 'skipped' (could
    not run the check — the pipeline proceeds rather than blocking on it)."""
    t0 = time.time()
    imgs = [b for b in (suspect_images or []) if b][:3]
    refs = [b for b in (ref_b64s or []) if b][:2]
    cfg = _cfg(provider)
    skipped = {"status": "skipped", "same_product": None, "suspect_item": "",
               "reference_item": "", "confidence": 0.0,
               "note": "Pairing check not run (no reference, no suspect photo, or no provider)."}
    if not cfg or not imgs or not refs:
        return skipped, None

    content = [{"type": "text", "text": _PAIRING_PROMPT}]
    for i, b in enumerate(imgs):
        content.append({"type": "text", "text": f"SUSPECT photo {i + 1}:"})
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b}", "detail": "low"}})
    for j, rb in enumerate(refs):
        content.append({"type": "text", "text": f"AUTHENTIC REFERENCE {j + 1}:"})
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{rb}", "detail": "low"}})
    try:
        parsed, tin, tout = _chat(cfg, content, _PAIRING_SCHEMA, "pairing", CHAT_TIMEOUT)
    except Exception as e:
        # A failed gate must not take the run down — fall through to scoring and
        # say so, rather than blocking on an input check.
        if not ALLOW_MOCK:
            print(f"[{provider}] pairing check failed, proceeding unchecked: {e}")
        return {**skipped, "note": f"Pairing check failed ({e}) — analysis proceeded unchecked."}, None

    try:
        conf = round(float(parsed.get("confidence") or 0), 2)
    except (TypeError, ValueError):
        conf = 0.0
    same = bool(parsed.get("same_product_type", True))
    sus = (parsed.get("suspect_item") or "").strip()
    ref = (parsed.get("reference_item") or "").strip()
    note = (parsed.get("note") or "").strip()

    # Only a CONFIDENT mismatch blocks the run; an unsure gate lets it through.
    if not same and conf >= PAIRING_MIN_CONFIDENCE:
        status = "mismatch"
        note = (f"Suspect appears to be '{sus or 'unknown'}' but the authentic reference is "
                f"'{ref or 'unknown'}'. A like-for-like comparison is not possible, so no "
                f"dimension scores were produced." + (f" {note}" if note else ""))
    else:
        status = "ok"
    usage = {"agent": "Pairing", "model": cfg["label"], "tokens_in": tin, "tokens_out": tout,
             "latency_ms": int((time.time() - t0) * 1000)}
    return {"status": status, "same_product": same, "suspect_item": sus,
            "reference_item": ref, "confidence": conf, "note": note}, usage


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
    # No UPC image supplied. This is the ABSENCE of a check, not a failed one —
    # it must never nudge the composite. 'nomatch' used to be returned here and
    # graph.py added +6 to every score in the corpus as a result.
    expected = _MASTER_UPC.get(brand, "")
    usage = {"agent": "UPC / Tag", "model": f"{_label_for(provider)} (mock)",
             "tokens_in": 0, "tokens_out": 0, "latency_ms": int((time.time() - t0) * 1000)}
    return {"status": "not_provided",
            "note": "No UPC image provided — upload a barcode photo to extract and verify. "
                    "Not counted as evidence either way.",
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
        # Unreadable is a capture failure, not a counterfeit signal — keep it
        # distinct from 'nomatch' (a code that really is absent from the PIM).
        status, note = "unreadable", "No readable UPC could be extracted from the uploaded image."
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
    # Synthesis plus VERIFY_VOTES independent adversarial reviews, all concurrent.
    # The tally is computed here: a single call asked to report its own "votes"
    # string simply invents one, which is not a second opinion.
    n = VERIFY_VOTES
    with ThreadPoolExecutor(max_workers=1 + n) as ex:
        fs = ex.submit(_verdict_call, provider, "synthesize", brand, composite, dimensions, upc)
        fvs = [ex.submit(_verdict_call, provider, "verify", brand, composite, dimensions, upc)
               for _ in range(n)]
        synth, u1 = fs.result()
        votes = []
        usages = [u1]
        for f in fvs:
            v, u = f.result()
            votes.append(bool(v["confirmed"]))
            usages.append(u)
    yes = sum(votes)
    verdict = {
        "label": composite["verdict_label"],
        "summary": synth["summary"],
        "escalated": composite["band"] not in ("authentic", "insufficient", "mismatch"),
        "verifier_confirmed": yes * 2 > len(votes),      # strict majority
        "verifier_votes": f"{yes}/{len(votes)}",
        "key_evidence": synth["key_evidence"],
    }
    return verdict, usages


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
    score = composite.get("score")
    rnd = _rng(f"{brand}|{score}|{kind}")
    score_txt = "not computed (insufficient evidence)" if score is None else f"{score}/100"
    if kind == "synthesize":
        out = {
            "summary": (f"Composite counterfeit probability {score_txt} "
                        f"({composite['verdict_label']}). UPC {upc['status']}. "
                        f"{_coverage_line(dimensions)}"),
            "key_evidence": _top_findings(dimensions),
        }
        toks_out = 640 + int(rnd() * 160)
    else:
        # No score means nothing to confirm — an unverifiable verdict is refuted.
        confirmed = score is not None and score >= 55
        out = {"confirmed": confirmed,
               "reason": "mock reviewer" if confirmed else "mock reviewer — unsupported or no score"}
        toks_out = 480 + int(rnd() * 140)
    usage = {
        "agent": "Verdict synth." if kind == "synthesize" else "Verify",
        "model": f"{label} (mock)",
        "tokens_in": 3000 + int(rnd() * 300), "tokens_out": toks_out,
        "latency_ms": int((time.time() - t0) * 1000) + 3800 + int(rnd() * 600),
    }
    return out, usage


def _coverage_line(dimensions):
    """Tell the verdict tier exactly what was and was not assessed, so it cannot
    write a confident summary over dimensions that abstained."""
    scored = [d["dimension"] for d in dimensions if d.get("score") is not None]
    absent = [d["dimension"] for d in dimensions if d.get("score") is None]
    line = f"Dimensions actually assessed: {', '.join(scored) or 'NONE'} ({len(scored)}/{len(dimensions)})."
    if absent:
        line += (f" NOT assessable (no evidence — treat as unknown, never as clean): "
                 f"{', '.join(absent)}.")
    return line


def _chat_verdict(cfg, kind, brand, composite, dimensions, upc, t0):
    findings = "; ".join(_top_findings(dimensions, 6)) or "none"
    coverage = _coverage_line(dimensions)
    score_txt = ("not computed — insufficient evidence" if composite.get("score") is None
                 else f"{composite['score']}/100")
    if kind == "synthesize":
        schema = {"type": "object", "properties": {
            "summary": {"type": "string"},
            "key_evidence": {"type": "array", "items": {"type": "string"}}},
            "required": ["summary", "key_evidence"], "additionalProperties": False}
        prompt = (f"Brand {brand}. Composite score {score_txt} "
                  f"({composite['verdict_label']}). UPC status: {upc['status']}. {coverage} "
                  f"Findings from assessed dimensions: {findings}.\n\n"
                  f"Write a concise litigation-ready verdict summary and up to 3 key-evidence "
                  f"bullets. Ground every claim in an assessed dimension. Do NOT describe an "
                  f"unassessed dimension as consistent, clean or authentic — say explicitly "
                  f"that it could not be evaluated. If little was assessed, the honest summary "
                  f"is that the evidence is insufficient and a recapture is required.")
    else:
        schema = {"type": "object", "properties": {
            "confirmed": {"type": "boolean"}, "reason": {"type": "string"}},
            "required": ["confirmed", "reason"], "additionalProperties": False}
        prompt = (f"You are an adversarial reviewer. Your job is to REFUTE the following "
                  f"counterfeit conclusion if it is not fully supported.\n\n"
                  f"Brand {brand}. Composite {score_txt} ({composite['verdict_label']}). "
                  f"UPC status: {upc['status']}. {coverage} Findings: {findings}.\n\n"
                  f"Set confirmed=false if the conclusion rests on too few assessed dimensions, "
                  f"on an unassessable dimension being read as clean, on evidence cited under "
                  f"the wrong dimension, or on any claim the findings do not actually support. "
                  f"Default to confirmed=false when uncertain. Give a one-line reason.")
    out, tin, tout = _chat(cfg, prompt, schema, "verdict", CHAT_TIMEOUT)
    usage = {
        "agent": "Verdict synth." if kind == "synthesize" else "Verify",
        "model": cfg["label"], "tokens_in": tin, "tokens_out": tout,
        "latency_ms": int((time.time() - t0) * 1000),
    }
    return out, usage
