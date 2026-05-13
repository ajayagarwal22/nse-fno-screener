"""Kite Connect OAuth flow.

Morning routine:
  1. Open http://localhost:8000/auth/
  2. Click "Login with Zerodha"
  3. Complete Zerodha login
  4. Token is auto-saved to .env and applied live — no restart needed.

Redirect URL to register in Kite developer console:
  http://localhost:8000/auth/callback
"""
import os
import re
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, RedirectResponse

from app.config import settings

router = APIRouter(prefix="/auth", tags=["auth"])

_ENV_PATH = Path(__file__).parent.parent.parent / ".env"


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
        return HTMLResponse(_result_page(
            ok=False,
            message=f"Login failed or cancelled (status={status!r}).",
        ))
    try:
        from app.data.kite_client import kite_client
        access_token = kite_client.generate_access_token(request_token)
        _update_env_token(access_token)
        _apply_token_live(access_token)
        return HTMLResponse(_result_page(
            ok=True,
            message=f"Token saved and applied. Preview: <code>{access_token[:8]}…</code>",
        ))
    except Exception as exc:
        return HTMLResponse(_result_page(ok=False, message=str(exc)))


def _result_page(ok: bool, message: str) -> str:
    icon = "✅" if ok else "❌"
    color = "#22c55e" if ok else "#ef4444"
    return f"""<!DOCTYPE html>
<html>
<head>
  <title>Kite Auth</title>
  <meta http-equiv="refresh" content="3;url=/">
  <style>
    body {{ font-family:-apple-system,sans-serif; background:#080b12; color:#e2e8f0;
           display:flex; align-items:center; justify-content:center; height:100vh; margin:0; }}
    .card {{ background:#0d1117; border:1px solid #1e2533; border-radius:12px;
             padding:36px 48px; text-align:center; max-width:460px; }}
    .icon {{ font-size:40px; margin-bottom:12px; }}
    .msg {{ color:{color}; font-size:14px; margin-bottom:16px; }}
    .sub {{ color:#475569; font-size:12px; }}
  </style>
</head>
<body>
  <div class="card">
    <div class="icon">{icon}</div>
    <div class="msg">{message}</div>
    <div class="sub">Redirecting to dashboard in 3 seconds…</div>
  </div>
</body>
</html>"""
