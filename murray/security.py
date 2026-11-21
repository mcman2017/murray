"""Sessions, rate limits, admin authentication and upload handling for a site that is open to the internet.

Per-IP limits are best effort (the address comes from proxy headers). The guards that actually bound abuse are
site-wide: the concurrency caps, the visitor spend rate and the daily spend cap in murray.llm.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import time
from pathlib import Path

from fastapi import HTTPException, Request, Response

from . import config, imaging
from .db import DB, dumps, now

COOKIE = "murray_sid"
SID_RE = re.compile(r"^[a-f0-9]{32}$")
_SALT = secrets.token_bytes(16)


def client_ip(request: Request) -> str:
    h = request.headers
    ip = h.get("cf-connecting-ip") or (h.get("x-forwarded-for") or "").split(",")[0].strip()
    return ip or (request.client.host if request.client else "unknown")


def ip_hash(request: Request) -> str:
    return hashlib.sha256(_SALT + client_ip(request).encode()).hexdigest()[:20]


def session(db: DB, request: Request, response: Response) -> str:
    """The caller's session id, creating the session and its cookie when needed."""
    sid = request.cookies.get(COOKIE, "")
    if not SID_RE.match(sid) or db.one("SELECT 1 FROM sessions WHERE id=?", (sid,)) is None:
        sid = secrets.token_hex(16)
        db.insert("sessions", id=sid, kind="visitor", ip_hash=ip_hash(request), created_at=now(), last_seen=now())
        secure = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
        response.set_cookie(COOKIE, sid, max_age=30 * 86400, httponly=True, secure=secure, samesite="lax", path="/")
    else:
        db.update("sessions", sid, last_seen=now())
    return sid


def scope_of(sid: str) -> str:
    return f"s:{sid}"


def is_admin(request: Request) -> bool:
    token = config.ADMIN_TOKEN
    auth = request.headers.get("authorization", "")
    if not token or not auth.lower().startswith("bearer "):
        return False
    return hmac.compare_digest(auth[7:].strip().encode(), token.encode())


def require_admin(request: Request) -> None:
    if not is_admin(request):
        raise HTTPException(status_code=401, detail="admin token required")


def require_json(request: Request) -> None:
    if "application/json" not in request.headers.get("content-type", "").lower():
        raise HTTPException(status_code=415, detail="send application/json")


def check_rate(db: DB, request: Request, sid: str, admin: bool) -> None:
    """Raises 429 with a plain message when a per-session or per-address limit is hit."""
    if admin:
        return
    hour_ago = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 3600))
    ten_ago = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 600))
    if db.val("SELECT COUNT(*) FROM turns WHERE session_id=? AND status='running'", (sid,), 0) > 0:
        raise HTTPException(status_code=409, detail="Murray is still answering your previous message.")
    if db.val("SELECT COUNT(*) FROM turns WHERE session_id=? AND created_at>=?", (sid, hour_ago), 0) >= config.SESSION_TURNS_PER_HOUR:
        raise HTTPException(status_code=429, detail="That is a lot of conversation for one hour. Come back in a little while.")
    recent_from_ip = db.val(
        "SELECT COUNT(*) FROM turns t JOIN sessions s ON s.id=t.session_id WHERE s.ip_hash=? AND t.created_at>=?",
        (ip_hash(request), ten_ago), 0)
    if recent_from_ip >= config.IP_TURNS_PER_10MIN:
        raise HTTPException(status_code=429, detail="Too many messages from this address in ten minutes. Give it a few minutes.")


def render_allowance(db: DB, sid: str, admin: bool) -> bool:
    if admin:
        return True
    hour_ago = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 3600))
    used = db.val("SELECT COUNT(*) FROM render_jobs WHERE session_id=? AND created_at>=?", (sid, hour_ago), 0)
    return used < config.SESSION_RENDERS_PER_HOUR


def accept_upload(db: DB, sid: str, image_b64: str | None, uploads_dir: Path) -> tuple[bytes | None, int | None]:
    """Decode, bound and re-encode an uploaded image. Returns (PNG bytes, upload id)."""
    if not image_b64:
        return None, None
    if "," in image_b64[:80]:
        image_b64 = image_b64.split(",", 1)[1]          # tolerate a data: URL
    if len(image_b64) > config.MAX_UPLOAD_BYTES * 4 // 3 + 16:
        raise HTTPException(status_code=413, detail="That image is too large (the limit is 4 MB).")
    try:
        raw = base64.b64decode(image_b64, validate=True)
        img = imaging.normalize_upload(raw)
    except imaging.ImageRejected as exc:
        raise HTTPException(status_code=400, detail=f"I could not read that image: {exc}.") from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="I could not read that image.") from exc
    png = imaging.to_png(img)
    uploads_dir.mkdir(parents=True, exist_ok=True)
    name = secrets.token_hex(12) + ".png"
    (uploads_dir / name).write_bytes(png)
    st = imaging.stats(img)
    upload_id = db.insert("uploads", session_id=sid, path=name, sha256=hashlib.sha256(png).hexdigest(), width=img.width,
                          height=img.height, stats_json=dumps(st), created_at=now())
    return imaging.for_model(img), upload_id
