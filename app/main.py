"""FastAPI application entry point.

Exposes REST endpoints for market data, signals, and exports,
plus a WebSocket endpoint that pushes new signals in real time.
"""
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse

from app.config import settings
from app.routers import market, scan, signals, option_chain, export, auth, paper_trades
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


@app.get("/", tags=["dashboard"])
async def dashboard():
    """Serve the visual dashboard."""
    html_path = os.path.join(os.path.dirname(__file__), "dashboard.html")
    return FileResponse(html_path, media_type="text/html")


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
            "kite_login": "GET /auth/login-url",
            "kite_callback": "GET /auth/callback?request_token=...",
            "docs": "/docs",
        },
    }


@app.get("/auth/login-url", tags=["auth"])
async def kite_login_url():
    """Redirect directly to Kite Connect login page."""
    from kiteconnect import KiteConnect
    kite = KiteConnect(api_key=settings.kite_api_key)
    return RedirectResponse(url=kite.login_url())


@app.get("/auth/callback", tags=["auth"])
async def kite_callback(request_token: str = Query(...)):
    """Exchange request token, save to .env, restart ticker, redirect to dashboard."""
    from app.data.kite_client import kite_client
    from fastapi import HTTPException
    import re
    try:
        access_token = kite_client.generate_access_token(request_token)

        # Persist new token to .env
        env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
        if os.path.exists(env_path):
            text = open(env_path).read()
            if re.search(r"^KITE_ACCESS_TOKEN\s*=", text, re.MULTILINE):
                text = re.sub(
                    r"^KITE_ACCESS_TOKEN\s*=.*$",
                    f"KITE_ACCESS_TOKEN={access_token}",
                    text, flags=re.MULTILINE,
                )
            else:
                text += f"\nKITE_ACCESS_TOKEN={access_token}\n"
            open(env_path, "w").write(text)

        # Hot-reload token in kite_client
        kite_client._kite.set_access_token(access_token)
        settings.kite_access_token = access_token

        # Restart KiteTicker with new token
        import paper_trader
        paper_trader.restart_ticker(kite_client.kite)

        logger.info(f"[Auth] Kite token refreshed and .env updated")
        return RedirectResponse(url="/")
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


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
