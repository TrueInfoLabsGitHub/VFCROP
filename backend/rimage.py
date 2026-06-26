"""Google reverse-image reference collection (SerpAPI · Google Lens).

Flow: host the suspect image -> Google Lens reverse search -> download the
visual matches -> vision-curate to clean PRODUCT-ONLY shots (no human models)
-> return those as authentic references for the analysis.

SERPAPI_API_KEY enables it; with no key the caller falls back to local data/
references. The curation step reuses the selected analysis engine via
providers._chat (imported lazily to avoid a circular import).
"""
import base64
import os
import time

import httpx
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))


def _key():
    return os.environ.get("SERPAPI_API_KEY", "").strip()


def available():
    return bool(_key())


def _search_cost():
    """SerpAPI bills per successful search (free within your monthly quota).
    Set SERPAPI_COST_PER_SEARCH (e.g. 0.01) to reflect a paid plan in the report."""
    try:
        return float(os.environ.get("SERPAPI_COST_PER_SEARCH", "0") or 0)
    except ValueError:
        return 0.0


def _host_image(b64):
    """Upload the suspect image to catbox.moe (anonymous) -> public URL.
    SerpAPI's Google Lens needs a reachable image URL, not raw bytes."""
    raw = base64.b64decode(b64)
    files = {"reqtype": (None, "fileupload"),
             "fileToUpload": ("suspect.jpg", raw, "image/jpeg")}
    r = httpx.post("https://catbox.moe/user/api.php", files=files, timeout=45)
    r.raise_for_status()
    url = r.text.strip()
    if not url.startswith("http"):
        raise RuntimeError("image host failed: " + url[:120])
    return url


def _lens(image_url, n=15):
    """Run Google Lens via SerpAPI -> list of candidate image URLs."""
    r = httpx.get("https://serpapi.com/search.json",
                  params={"engine": "google_lens", "url": image_url, "api_key": _key()},
                  timeout=60)
    r.raise_for_status()
    data = r.json()
    urls = []
    for m in (data.get("visual_matches") or []):
        u = m.get("image") or m.get("thumbnail")
        if u:
            urls.append(u)
        if len(urls) >= n:
            break
    return urls


def _download(url):
    try:
        r = httpx.get(url, timeout=25, follow_redirects=True)
        if r.status_code == 200 and r.headers.get("content-type", "").startswith("image"):
            return base64.b64encode(r.content).decode()
    except Exception:
        return None
    return None


def _curate(cfg, brand, cand_b64s, keep):
    """One vision call: keep only clean product-only photos (no human models)."""
    from providers import _chat   # lazy import avoids circular dependency
    schema = {"type": "object",
              "properties": {"keep_indices": {"type": "array", "items": {"type": "integer"}}},
              "required": ["keep_indices"], "additionalProperties": False}
    prompt = (
        f"These are reverse-image-search results for a {brand} product. Return keep_indices: "
        f"the 0-based indices of images that are CLEAN PRODUCT-ONLY photos of the {brand} item — "
        f"flat-lay, hanging, mannequin-free, or on a plain background — with NO human model wearing "
        f"or holding it, and that clearly show the actual product. Reject any image with a person, "
        f"collages, logo-only crops, packaging, or unrelated items. Keep at most {keep}.")
    content = [{"type": "text", "text": prompt}]
    for i, b in enumerate(cand_b64s):
        content.append({"type": "text", "text": f"Image {i}:"})
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b}", "detail": "low"}})
    parsed, tin, tout = _chat(cfg, content, schema, "curate", 120)
    idx = [i for i in parsed.get("keep_indices", []) if 0 <= i < len(cand_b64s)][:keep]
    return idx, tin, tout


def fetch_authentic_references(suspect_b64, brand, cfg, keep=5):
    """-> (ref_b64s, meta, usage_rows). Never raises — degrades to []. """
    usage = []
    if not _key() or not suspect_b64:
        return [], {"used": False, "reason": "no SerpAPI key or no suspect image"}, usage

    t0 = time.time()
    try:
        host_url = _host_image(suspect_b64)
        cand_urls = _lens(host_url, 15)
        cands, src = [], []
        for u in cand_urls:
            b = _download(u)
            if b:
                cands.append(b)
                src.append(u)
            if len(cands) >= 12:
                break
    except Exception as e:
        print("[rimage] reverse search failed:", e)
        usage.append({"agent": "Reverse image", "model": "SerpAPI · Google Lens",
                      "tokens_in": 0, "tokens_out": 0, "latency_ms": int((time.time() - t0) * 1000)})
        return [], {"used": True, "error": str(e)[:160], "candidates": 0, "kept": 0, "sources": []}, usage

    usage.append({"agent": "Reverse image", "model": "SerpAPI · Google Lens",
                  "tokens_in": 0, "tokens_out": 0, "cost": _search_cost(),
                  "latency_ms": int((time.time() - t0) * 1000)})
    if not cands:
        return [], {"used": True, "candidates": 0, "kept": 0, "sources": []}, usage

    keep_b64, kept_src = cands[:keep], src[:keep]
    if cfg:
        t1 = time.time()
        try:
            idx, tin, tout = _curate(cfg, brand, cands, keep)
            if idx:
                keep_b64 = [cands[i] for i in idx]
                kept_src = [src[i] for i in idx]
            usage.append({"agent": "Curate refs", "model": cfg["label"],
                          "tokens_in": tin, "tokens_out": tout, "latency_ms": int((time.time() - t1) * 1000)})
        except Exception as e:
            print("[rimage] curate failed:", e)

    meta = {"used": True, "candidates": len(cands), "kept": len(keep_b64),
            "sources": kept_src, "host": host_url}
    return keep_b64, meta, usage
