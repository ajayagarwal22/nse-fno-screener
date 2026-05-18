"""Kite Connect OAuth flow.

Flow (fully automatic):
  1. Open http://localhost:9000  → server checks token validity
  2. If token is invalid → auto-redirect to /auth/login → Zerodha login page
  3. After Zerodha login → /auth/callback saves token, marks it valid, redirects to /

Redirect URL to register in Kite developer console:
  http://localhost:9000/auth/callback
"""
import asyncio
import os
import re
import time
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, RedirectResponse

from app.config import settings

router = APIRouter(prefix="/auth", tags=["auth"])

_ENV_PATH = Path(__file__).parent.parent.parent / ".env"

# ---------------------------------------------------------------------------
# Token validity cache (avoids a Kite API call on every page load)
# ---------------------------------------------------------------------------

_token_valid_until: float = 0.0
_TOKEN_TTL = 300  # re-check every 5 minutes


async def token_is_valid() -> bool:
    """Return True if the Kite access token is (still) valid."""
    global _token_valid_until
    if time.time() < _token_valid_until:
        return True
    try:
        from app.data.kite_client import kite_client
        await asyncio.to_thread(kite_client.kite.profile)
        _token_valid_until = time.time() + _TOKEN_TTL
        return True
    except Exception:
        _token_valid_until = 0.0
        return False


def _mark_token_valid() -> None:
    global _token_valid_until
    _token_valid_until = time.time() + _TOKEN_TTL


def _update_env_token(token: str) -> None:
    """Overwrite KITE_ACCESS_TOKEN in .env without touching other lines."""
    text = _ENV_PATH.read_text() if _ENV_PATH.exists() else ""
    line = f"KITE_ACCESS_TOKEN={token}"
    if re.search(r"^KITE_ACCESS_TOKEN=", text, re.MULTILINE):
        text = re.sub(r"^KITE_ACCESS_TOKEN=.*$", line, text, flags=re.MULTILINE)
    else:
        text = text.rstrip("\n") + f"\n{line}\n"
    _ENV_PATH.write_text(text)


def _apply_token_live(token: str) -> None:
    """Hot-swap the access token on the running kite_client without restart."""
    from app.data.kite_client import kite_client
    settings.kite_access_token = token
    kite_client._kite = None          # force reconnect on next kite property access
    _ = kite_client.kite              # re-initialise with new token
    kite_client.get_fno_instruments.cache_clear()
    kite_client.get_nse_index_tokens.cache_clear()
    import paper_trader; paper_trader.restart_ticker(kite_client.kite)


@router.get("/status")
async def auth_status():
    """Check whether the current Kite token is valid."""
    valid = await token_is_valid()
    return {"valid": valid}


@router.get("/", response_class=HTMLResponse)
async def auth_page():
    token_preview = (
        settings.kite_access_token[:6] + "…"
        if settings.kite_access_token else "not set"
    )
    return HTMLResponse(f"""<!DOCTYPE html>
<html>
<head>
  <title>Kite Auth</title>
  <style>
    body {{ font-family: -apple-system, sans-serif; background:#080b12; color:#e2e8f0;
           display:flex; align-items:center; justify-content:center; height:100vh; margin:0; }}
    .card {{ background:#0d1117; border:1px solid #1e2533; border-radius:12px;
             padding:36px 48px; text-align:center; max-width:420px; }}
    h2 {{ color:#a78bfa; margin-bottom:6px; }}
    .sub {{ color:#64748b; font-size:13px; margin-bottom:28px; }}
    .token {{ font-size:12px; color:#334155; margin-bottom:24px; }}
    a.btn {{ display:inline-block; background:#7c3aed; color:#fff; text-decoration:none;
             padding:10px 28px; border-radius:8px; font-weight:600; font-size:14px; }}
    a.btn:hover {{ background:#6d28d9; }}
    .note {{ margin-top:20px; font-size:11px; color:#475569; line-height:1.6; }}
  </style>
</head>
<body>
  <div class="card">
    <h2>NSE F&amp;O Screener</h2>
    <div class="sub">Daily Kite token refresh</div>
    <div class="token">Current token: <strong>{token_preview}</strong></div>
    <a class="btn" href="/auth/login">Login with Zerodha →</a>
    <div class="note">
      Opens Zerodha login in this tab.<br>
      Token is saved to <code>.env</code> and applied immediately.
    </div>
  </div>
</body>
</html>""")


@router.get("/login")
async def kite_login():
    url = (
        f"https://kite.zerodha.com/connect/login"
        f"?api_key={settings.kite_api_key}&v=3"
    )
    return RedirectResponse(url)


@router.get("/callback", response_class=HTMLResponse)
async def kite_callback(request_token: str = "", status: str = ""):
    if status != "success" or not request_token:
        return HTMLResponse(_error_page(f"Login failed or cancelled (status={status!r})."))
    try:
        from app.data.kite_client import kite_client
        access_token = await asyncio.to_thread(
            kite_client.generate_access_token, request_token
        )
        _update_env_token(access_token)
        _apply_token_live(access_token)
        _mark_token_valid()
        return RedirectResponse(url="/")
    except Exception as exc:
        return HTMLResponse(_error_page(str(exc)))


def _error_page(message: str) -> str:
    return f"""<!DOCTYPE html>
<html>
<head>
  <title>Kite Auth Error</title>
  <meta http-equiv="refresh" content="4;url=/auth/login">
  <style>
    body {{ font-family:-apple-system,sans-serif; background:#080b12; color:#e2e8f0;
           display:flex; align-items:center; justify-content:center; height:100vh; margin:0; }}
    .card {{ background:#0d1117; border:1px solid #3b1c1c; border-radius:12px;
             padding:36px 48px; text-align:center; max-width:460px; }}
    .icon {{ font-size:40px; margin-bottom:12px; }}
    .msg {{ color:#ef4444; font-size:14px; margin-bottom:16px; }}
    .sub {{ color:#475569; font-size:12px; }}
  </style>
</head>
<body>
  <div class="card">
    <div class="icon">❌</div>
    <div class="msg">{message}</div>
    <div class="sub">Retrying login in 4 seconds…</div>
  </div>
</body>
</html>"""
