# NSE F&O Options Screener

Professional-grade 8-layer NSE F&O options screener. Answers: *"Is the probability-adjusted risk-to-reward favorable RIGHT NOW?"*

---

## Setup

### Prerequisites

- Python 3.11+
- Docker Desktop (for TimescaleDB)
- A [Kite Connect](https://developers.kite.trade/) app (API key + secret)
- A Telegram bot token + chat ID (for alerts)

### 1. Install Docker Desktop

Download and install Docker Desktop for your platform:

- **macOS (Apple Silicon):** Download the DMG from [docker.com](https://www.docker.com/products/docker-desktop/), drag to Applications, and open it once to let it initialize.
- **macOS (Intel):** Same as above but select the Intel build.
- **Windows/Linux:** Follow the instructions on [docker.com](https://www.docker.com/products/docker-desktop/).

### 2. Install Python dependencies

```bash
pip install fastapi "uvicorn[standard]" pydantic-settings python-dotenv kiteconnect \
    pandas numpy httpx "SQLAlchemy[asyncio]" asyncpg alembic APScheduler \
    python-telegram-bot cachetools pytest pytest-asyncio pytest-mock
```

> **Note:** TA-Lib is optional. The app falls back to a pure-Python implementation if it is not installed.

### 3. Configure environment

```bash
cp .env.example .env
```

Open `.env` and fill in:

```
KITE_API_KEY=<your api key from kite developer console>
KITE_API_SECRET=<your api secret from kite developer console>
TELEGRAM_BOT_TOKEN=<your bot token>
TELEGRAM_CHAT_ID=<your chat id>
```

Leave `KITE_ACCESS_TOKEN` blank for now — step 5 handles it.

### 4. Start TimescaleDB and run migrations

```bash
docker compose up -d db
```

Wait a few seconds for the database to be ready, then:

```bash
alembic upgrade head
```

### 5. Get your Kite access token (do this every day)

Kite access tokens expire daily at 6:00 AM IST. Run the auth helper to log in and save the token automatically:

```bash
python3 kite_auth.py
```

This will:
1. Open the Zerodha login page in your browser
2. Wait on `http://127.0.0.1:5050` for the OAuth redirect
3. Exchange the request token instantly and write `KITE_ACCESS_TOKEN` to `.env`

### 6. Start the server

```bash
uvicorn app.main:app --reload
```

The API is now live at `http://localhost:8000`.

---

## Usage

| Action | Command |
|---|---|
| Interactive API docs | Open `http://localhost:8000/docs` |
| Run a scan | `POST http://localhost:8000/scan?trade_type=INTRADAY` |
| View signals | `GET http://localhost:8000/signals` |
| Market regime | `GET http://localhost:8000/market/regime` |
| Live alerts | WebSocket `ws://localhost:8000/ws/alerts` |

---

## Daily routine

Every morning before 6:00 AM IST, refresh the Kite access token:

```bash
python3 kite_auth.py
```

Then restart the server if it is already running:

```bash
pkill -f "uvicorn app.main:app"
uvicorn app.main:app --reload
```

---

## Run tests

```bash
pytest tests/
```
