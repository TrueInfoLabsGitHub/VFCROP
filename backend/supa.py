"""Product catalog backed by Supabase Storage.

STRICTLY non-invasive: everything lives in one dedicated bucket
(SUPABASE_BUCKET, default 'veritas-products'). Each product is a folder:

    <product_id>/meta.json          {id, name, brand, images:[...], created_at}
    <product_id>/img_0.jpg ...       the authentic reference images

No database tables are created or touched. Uses the Storage REST API with the
service-role key (server-side only).
"""
import base64
import json
import os
import re
import time
import uuid

import httpx
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))


def _url():
    return os.environ.get("SUPABASE_URL", "").strip().rstrip("/")


def _key():
    return os.environ.get("SUPABASE_SERVICE_KEY", "").strip()


def _bucket():
    return os.environ.get("SUPABASE_BUCKET", "veritas-products").strip()


def available():
    return bool(_url() and _key())


def _h(extra=None):
    h = {"Authorization": f"Bearer {_key()}", "apikey": _key()}
    if extra:
        h.update(extra)
    return h


def _slug(s):
    s = re.sub(r"[^a-z0-9]+", "-", (s or "product").lower()).strip("-")
    return (s or "product")[:32]


# ---- bucket ---------------------------------------------------------------
def ensure_bucket():
    """Create the dedicated bucket if it doesn't exist (idempotent, additive).
    No mime restriction (we store images + a meta.json per product)."""
    b = _bucket()
    cfg = {"public": False, "file_size_limit": 10485760, "allowed_mime_types": None}
    r = httpx.get(f"{_url()}/storage/v1/bucket/{b}", headers=_h(), timeout=30)
    if r.status_code == 200:
        httpx.put(f"{_url()}/storage/v1/bucket/{b}", headers=_h(), json=cfg, timeout=30)
        return True
    c = httpx.post(f"{_url()}/storage/v1/bucket", headers=_h(),
                   json={"id": b, "name": b, **cfg}, timeout=30)
    return c.status_code in (200, 201, 409)


# ---- objects --------------------------------------------------------------
def _upload(path, data, content_type):
    r = httpx.post(f"{_url()}/storage/v1/object/{_bucket()}/{path}",
                   headers=_h({"Content-Type": content_type, "x-upsert": "true"}),
                   content=data, timeout=90)
    r.raise_for_status()


def _get_bytes(path):
    r = httpx.get(f"{_url()}/storage/v1/object/{_bucket()}/{path}", headers=_h(), timeout=45)
    return r.content if r.status_code == 200 else None


def _get_json(path):
    b = _get_bytes(path)
    if b is None:
        return None
    try:
        return json.loads(b)
    except ValueError:
        return None


def _list(prefix=""):
    r = httpx.post(f"{_url()}/storage/v1/object/list/{_bucket()}", headers=_h(),
                   json={"prefix": prefix, "limit": 1000, "offset": 0,
                         "sortBy": {"column": "name", "order": "asc"}}, timeout=30)
    r.raise_for_status()
    return r.json()


# ---- product CRUD ---------------------------------------------------------
def create_product(name, brand, images_b64):
    ensure_bucket()
    pid = f"{_slug(name)}-{uuid.uuid4().hex[:6]}"
    files = []
    for i, b in enumerate(images_b64 or []):
        if not b:
            continue
        fn = f"img_{i}.jpg"
        _upload(f"{pid}/{fn}", base64.b64decode(b), "image/jpeg")
        files.append(fn)
    meta = {"id": pid, "name": name, "brand": brand or "", "images": files,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    _upload(f"{pid}/meta.json", json.dumps(meta).encode(), "application/json")
    return meta


def list_products():
    out = []
    for it in _list(""):
        name = it.get("name")
        if name and it.get("id") is None:          # a folder = a product
            meta = _get_json(f"{name}/meta.json")
            if meta:
                out.append(meta)
    out.sort(key=lambda m: m.get("created_at", ""), reverse=True)
    return out


def product_images_b64(pid, cap=3):
    meta = _get_json(f"{pid}/meta.json")
    if not meta:
        return []
    out = []
    for fn in (meta.get("images") or [])[:cap]:
        b = _get_bytes(f"{pid}/{fn}")
        if b:
            out.append(base64.b64encode(b).decode())
    return out


def image_bytes(pid, filename):
    return _get_bytes(f"{pid}/{filename}")


def delete_product(pid):
    names = [f"{pid}/{it['name']}" for it in _list(f"{pid}/") if it.get("name")]
    if names:
        httpx.request("DELETE", f"{_url()}/storage/v1/object/{_bucket()}",
                      headers=_h({"Content-Type": "application/json"}),
                      content=json.dumps({"prefixes": names}), timeout=45)
    return True
