"""
NSE F&O Screener — Admin Panel (standalone, port 9001).
Runs independently of the main app. Stores settings in admin_config.json.
"""
import json
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse

ADMIN_DIR = Path(__file__).parent
CONFIG_FILE = ADMIN_DIR / "admin_config.json"
HTML_FILE = ADMIN_DIR / "admin.html"

# ── Defaults ─────────────────────────────────────────────────────────────────

DEFAULTS: dict = {
    # ── Scan ──────────────────────────────────────────────────────────────────
    "scan": {
        "interval_minutes":       5,
        "min_confidence":         "B",
        "min_oi_threshold":       1500,
        "min_volume_multiplier":  2.0,
        "max_bid_ask_spread_pct": 0.3,
        "high_risk_window_hours": 24,
    },

    # ── Layer 1: Market Regime ────────────────────────────────────────────────
    "layer1": {
        "gates": {
            "vix_extreme":     True,
            "vix_spike":       True,
            "event_risk":      True,
            "theta_decay":     True,
            "call_vix_cap":    True,
            "breadth":         True,
        },
        "thresholds": {
            "vix_extreme":              25.0,
            "vix_elevated":             18.0,
            "vix_spike_pct":             5.0,
            "vix_low_premium":          13.0,
            "vix_call_buying_cap":      20.0,
            "breadth_bullish_min":      60.0,
            "breadth_bearish_max":      40.0,
            "index_bullish_signals_min": 4,
            "index_bearish_signals_max": 1,
            "overall_bullish_min":       4,
        },
    },

    # ── Layer 2: Stock Selector ───────────────────────────────────────────────
    "layer2": {
        "top_n":                  10,
        "rs_threshold":           0.04,
        "rs_index_threshold":     0.02,
        "liquidity_ttl_s":        1800,
        "volume_lookback_days":   20,
    },

    # ── Layer 3: Technical ────────────────────────────────────────────────────
    "layer3": {
        "gates": {
            "htf_trend":        True,
            "rsi_divergence":   True,
            "macd_cross":       True,
            "vwap":             True,
            "volume_expansion": True,
            "candle_pattern":   True,
        },
        "weights": {
            "htf_trend":        20,
            "rsi_divergence":   30,
            "macd_cross":       20,
            "vwap":             15,
            "volume_expansion": 10,
            "candle_pattern":    5,
        },
        "thresholds": {
            "min_score":                    60,
            "rsi_period":                   14,
            "divergence_lookback":          80,
            "pivot_left":                    3,
            "pivot_right":                   0,
            "rsi_bullish_penalty_above":    55,
            "rsi_bearish_penalty_below":    45,
            "divergence_strength_penalty":   0.6,
            "macd_fast":                    12,
            "macd_slow":                    26,
            "macd_signal_period":            9,
            "volume_expansion_mult":         1.7,
            "volume_avg_bars":              20,
            "supertrend_period":            10,
            "supertrend_mult":               3.0,
            "htf_bullish_signals_min":       4,
            "htf_bearish_signals_max":       1,
            "ema_short":                    20,
            "ema_mid":                      50,
            "ema_long":                    200,
        },
    },

    # ── Layer 4: Derivatives ──────────────────────────────────────────────────
    "layer4": {
        "gates": {
            "pcr":           True,
            "max_pain":      True,
            "writing_bias":  True,
            "iv_skew":       True,
        },
        "thresholds": {
            "pcr_bearish_below":        0.85,
            "pcr_bullish_above":        1.15,
            "pcr_overheated_above":     1.5,
            "atm_range_pct":            2.0,
            "writing_dominance_ratio":  1.5,
        },
    },

    # ── Layer 6: Entry Trigger ────────────────────────────────────────────────
    "layer6": {
        "gates": {
            "regime":           True,
            "rs":               True,
            "rsi_divergence":   True,
            "htf_trend":        True,
            "macd_cross":       True,
            "vwap":             True,
            "volume_expansion": True,
            "derivatives":      True,
            "pcr":              True,
        },
        "weights": {
            "regime":           10,
            "rs":                5,
            "rsi_divergence":   30,
            "htf_trend":        15,
            "macd_cross":       15,
            "vwap":             10,
            "volume_expansion":  5,
            "derivatives":       5,
            "pcr":               5,
        },
        "thresholds": {
            "a_plus_score":              78,
            "a_minus_score":             70,
            "b_score":                   60,
            "a_plus_requires_divergence": False,
            "rs_positive_min":           0.01,
            "rs_negative_min":           0.01,
            "pcr_call_supportive_min":   0.8,
            "pcr_put_bearish_max":       1.0,
            "atr_entry_mult":            0.25,
            "atr_sl_mult":               0.7,
            "atr_t1_mult":               1.5,
            "atr_t2_mult":               2.5,
            "atr_min_pct":               0.005,
        },
    },

    # ── Layer 7: Exit Engine ──────────────────────────────────────────────────
    "layer7": {
        "gates": {
            "time_exit":             True,
            "vwap_exit":             True,
            "rsi_divergence_exit":   True,
            "volume_exhaustion":     True,
            "partial_booking":       True,
        },
        "thresholds": {
            "time_exit_minutes":               45,
            "momentum_threshold_pct":           0.15,
            "rsi_divergence_call_exit_below":  60,
            "rsi_divergence_put_exit_above":   40,
            "volume_exhaustion_ratio":          0.5,
            "volume_exhaustion_min_minutes":   30,
            "t1_partial_pct":                  50,
            "t1_atr_mult":                      1.0,
            "t2_atr_mult":                      2.0,
        },
    },

    # ── Layer 8: Macro Risk ───────────────────────────────────────────────────
    "layer8": {
        "gates": {
            "event_calendar": True,
            "usdinr":         True,
            "fii_flow":       True,
        },
        "thresholds": {
            "event_window_hours":    24,
            "usdinr_stress_above":   85.0,
            "fii_selling_below":  -3000,
        },
    },

    # ── Layer 8 Suppression Filters ──────────────────────────────────────────
    "layer8_filters": {
        "time_of_day_filter": {
            "enabled": True,
            "block_windows": [
                {"start": "11:50", "end": "12:45", "reason": "lunch_chop"}
            ]
        },
        "symbol_cooldown_min": 90,
        "max_simultaneous_per_sector": 2,
        "max_trades_per_day": 10,
        "daily_trend_call_veto": True,
        "daily_trend_index": "NIFTY",
        "daily_trend_ema_period": 20,
    },

    # ── Paper Trader ──────────────────────────────────────────────────────────
    "paper_trader": {
        "enabled":                  True,
        "auto_entry":               True,
        "auto_exit":                True,
        "itm_steps":                1,
        "market_open":              "09:15",
        "exit_before_close":        "15:25",
        "market_close_hard":        "15:30",
        "instruments_refresh_hours": 24,
        "retry_attempts":           3,
        "retry_delay_s":            2,

        # ── Position sizing ───────────────────────────────────────────────────
        "sizing_mode":              "risk_inr",   # "lots" | "capital" | "risk_inr"
        "default_lots":             1,        # lots per trade (overrides instrument default)
        "max_capital_per_trade":    10000,    # INR: max premium deployed per trade
        "max_loss_per_trade":       2000,     # INR: hard max loss per trade (all lots)

        # ── Stop Loss (choose one mode) ───────────────────────────────────────
        # mode: "spot_atr" (current default) | "premium_pct" | "premium_amount"
        "sl_mode":                  "premium_pct",
        "sl_premium_pct":           40.0,     # SL at 40% of entry premium (mode=premium_pct)
        "sl_premium_amount":        2000,     # SL if premium drops by this INR (mode=premium_amount)

        # ── Target 1 (partial exit at T1) ─────────────────────────────────────
        # mode: "spot_atr" | "premium_pct" | "premium_amount"
        "t1_mode":                  "spot_atr",
        "t1_premium_pct":           100.0,    # T1 when premium doubles (mode=premium_pct)
        "t1_premium_amount":        2000,     # T1 at +this INR gain on full position
        "t1_partial_pct":           50,       # % of position to book at T1

        # ── Target 2 (full exit at T2) ────────────────────────────────────────
        # mode: "spot_atr" | "premium_pct" | "premium_amount"
        "t2_mode":                  "spot_atr",
        "t2_premium_pct":           200.0,    # T2 when premium triples (mode=premium_pct)
        "t2_premium_amount":        4000,     # T2 at +this INR gain on full position
    },
}


# ── Config I/O ────────────────────────────────────────────────────────────────

def _deep_merge(base: dict, override: dict) -> dict:
    """Merge override into base (adds missing keys, keeps extras)."""
    result = dict(base)
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE) as f:
                saved = json.load(f)
            saved.pop("_meta", None)
            return _deep_merge(DEFAULTS, saved)
        except Exception:
            pass
    return dict(DEFAULTS)


def save_config(config: dict) -> None:
    config = dict(config)
    config["_meta"] = {
        "version": "1.0",
        "last_modified": datetime.now().isoformat(),
    }
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(title="NSE Admin Panel", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    return HTMLResponse(content=HTML_FILE.read_text(encoding="utf-8"))


@app.get("/api/config")
async def get_config():
    return JSONResponse(content=load_config())


@app.get("/api/defaults")
async def get_defaults():
    return JSONResponse(content=DEFAULTS)


@app.post("/api/config")
async def post_config(request: Request):
    data = await request.json()
    data.pop("_meta", None)
    save_config(data)
    return {"status": "saved", "timestamp": datetime.now().isoformat()}


@app.post("/api/config/reset")
async def reset_config():
    save_config(dict(DEFAULTS))
    return JSONResponse(content=load_config())


@app.get("/api/status")
async def status():
    cfg_exists = CONFIG_FILE.exists()
    last_modified = None
    if cfg_exists:
        try:
            with open(CONFIG_FILE) as f:
                d = json.load(f)
            last_modified = d.get("_meta", {}).get("last_modified")
        except Exception:
            pass
    return {
        "status": "ok",
        "config_file": str(CONFIG_FILE),
        "has_saved_config": cfg_exists,
        "last_modified": last_modified,
    }
