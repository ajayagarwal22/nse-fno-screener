"""FastAPI application entry point.

Exposes REST endpoints for market data, signals, and exports,
plus a WebSocket endpoint that pushes new signals in real time.
"""
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from kiteconnect.exceptions import TokenException

from app.config import settings
from app.routers import market, scan, signals, option_chain, export, auth, paper_trades
from app.routers.auth import token_is_valid
from app.routers.signals import update_signals
from app.scheduler import init_scheduler, scheduler, set_signals_callback

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# WebSocket connection manager
# ---------------------------------------------------------------------------

class ConnectionManager:
    def __init__(self):
        self._connections: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self._connections.append(ws)
        logger.info(f"WebSocket client connected. Total: {len(self._connections)}")

    def disconnect(self, ws: WebSocket):
        if ws in self._connections:
            self._connections.remove(ws)
        logger.info(f"WebSocket client disconnected. Total: {len(self._connections)}")

    async def broadcast(self, payload: dict):
        dead = []
        for ws in self._connections:
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._connections.remove(ws)


ws_manager = ConnectionManager()


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

# Read at module import time (synchronous, main thread).
# DASHBOARD_HTML_PATH points to a /tmp copy staged by run-screener.sh,
# avoiding iCloud Drive EDEADLK when launchd starts at login.
def _load_dashboard_html() -> str:
    import time as _time
    path = (
        os.environ.get("DASHBOARD_HTML_PATH")
        or os.path.join(os.path.dirname(__file__), "dashboard.html")
    )
    for _ in range(30):
        try:
            with open(path, encoding="utf-8") as f:
                return f.read()
        except OSError:
            _time.sleep(2)
    raise RuntimeError(f"Cannot read dashboard HTML from {path}")

_dashboard_html: str = _load_dashboard_html()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting NSE F&O Screener...")

    async def on_signals(sigs):
        update_signals(sigs)
        from app.routers.signals import _serialize
        await ws_manager.broadcast({
            "type": "SIGNALS",
            "count": len(sigs),
            "signals": [_serialize(s) for s in sigs],
        })

    set_signals_callback(on_signals)
    init_scheduler(settings.scan_interval_minutes)

    import paper_trader
    from app.data.kite_client import kite_client
    paper_trader.init(kite=kite_client.kite)

    yield

    scheduler.shutdown(wait=False)
    logger.info("Scheduler shut down.")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="NSE F&O Options Screener",
    description=(
        "Professional-grade 8-layer NSE F&O options screener. "
        "Answers: 'Is the probability-adjusted risk-to-reward favorable RIGHT NOW?'"
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(market.router)
app.include_router(scan.router)
app.include_router(signals.router)
app.include_router(option_chain.router)
app.include_router(export.router)
app.include_router(paper_trades.router)


@app.exception_handler(TokenException)
async def handle_token_exception(request: Request, exc: TokenException):
    """Redirect browser requests to Kite login when token expires mid-session."""
    if "text/html" in request.headers.get("accept", ""):
        return RedirectResponse(url="/auth/login")
    return JSONResponse(
        status_code=401,
        content={"error": "token_expired", "login_url": "/auth/login"},
    )


@app.get("/", tags=["dashboard"])
async def dashboard():
    """Serve the visual dashboard, redirecting to Kite login if token is expired."""
    if not await token_is_valid():
        return RedirectResponse(url="/auth/login")
    return HTMLResponse(content=_dashboard_html)


@app.get("/api", tags=["root"])
async def root():
    return {
        "service": "NSE F&O Options Screener",
        "version": "1.0.0",
        "philosophy": "Filter OUT low-probability trades. Only emit when all 8 layers align.",
        "endpoints": {
            "market_regime": "GET /market/regime",
            "market_breadth": "GET /market/breadth",
            "macro_risk": "GET /market/macro-risk",
            "scan": "POST /scan?trade_type=INTRADAY",
            "signals": "GET /signals?confidence=A%2B&direction=CALL",
            "signal_detail": "GET /signals/{id}",
            "option_chain": "GET /option-chain/{symbol}",
            "export_csv": "GET /export/signals.csv",
            "export_json": "GET /export/signals.json",
            "websocket_alerts": "ws://<host>/ws/alerts",
            "kite_login": "GET /auth/login",
            "kite_callback": "GET /auth/callback?request_token=...",
            "docs": "/docs",
        },
    }


@app.websocket("/ws/alerts")
async def websocket_alerts(websocket: WebSocket):
    """Real-time signal push. Sends JSON payload on each scan that produces signals."""
    await ws_manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
