"""Layer 5 — Option Selection Engine.

Selects the optimal strike and expiry based on trade type (intraday vs swing),
current ATM, IV regime, and liquidity filters.
"""
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Optional

import pandas as pd

from app.config import settings
from app.data.kite_client import kite_client
from app.engines.market_regime import MarketRegime


class TradeType(str, Enum):
    INTRADAY = "INTRADAY"
    SWING = "SWING"


class OptionType(str, Enum):
    CE = "CE"
    PE = "PE"


@dataclass
class OptionTarget:
    symbol: str
    tradingsymbol: str
    strike: float
    expiry: date
    option_type: OptionType
    current_premium: float
    iv: float
    oi: int
    instrument_token: int
    days_to_expiry: int
    is_atm: bool


def _find_atm_strike(chain: pd.DataFrame, spot: float) -> float:
    strikes = chain["strike"].unique()
    return float(min(strikes, key=lambda s: abs(s - spot)))


def _nearest_expiries(instruments: pd.DataFrame, underlying: str) -> list[date]:
    """Return sorted list of upcoming expiry dates for the underlying."""
    opts = instruments[
        (instruments["name"] == underlying)
        & (instruments["instrument_type"].isin(["CE", "PE"]))
    ]
    today = date.today()
    expiries = sorted(
        {e.date() for e in pd.to_datetime(opts["expiry"]) if e.date() >= today}
    )
    return expiries


def _select_expiry(expiries: list[date], trade_type: TradeType) -> Optional[date]:
    if not expiries:
        return None
    today = date.today()
    if trade_type == TradeType.INTRADAY:
        # nearest weekly expiry with at least 1 day remaining
        for exp in expiries:
            if (exp - today).days >= 1:
                return exp
    else:
        # swing: 2–4 weeks out
        for exp in expiries:
            dte = (exp - today).days
            if 14 <= dte <= 35:
                return exp
        # fallback: first expiry beyond 2 weeks
        for exp in expiries:
            if (exp - today).days >= 14:
                return exp
    return expiries[0]


def _select_strike(chain: pd.DataFrame, spot: float, option_type: OptionType, trade_type: TradeType) -> float:
    """
    Intraday: ATM or 1-strike ITM.
    Swing: 1-strike ITM.
    """
    atm = _find_atm_strike(chain, spot)
    strikes = sorted(chain["strike"].unique())

    if option_type == OptionType.CE:
        itm_strikes = [s for s in strikes if s < spot]
        atm_itm = sorted(itm_strikes + [atm], reverse=True)
    else:
        itm_strikes = [s for s in strikes if s > spot]
        atm_itm = sorted(itm_strikes + [atm])

    if not atm_itm:
        return atm

    if trade_type == TradeType.INTRADAY:
        return atm_itm[0]  # ATM or 1 ITM
    else:
        return atm_itm[min(1, len(atm_itm) - 1)]  # 1-strike ITM


def select_option(
    symbol: str,
    spot: float,
    option_type: OptionType,
    trade_type: TradeType,
    regime: MarketRegime,
) -> Optional[OptionTarget]:
    """Return the best option contract for the given setup, or None if nothing qualifies."""
    instruments = kite_client.get_fno_instruments()
    expiries = _nearest_expiries(instruments, symbol)
    target_expiry = _select_expiry(expiries, trade_type)

    if target_expiry is None:
        return None

    chain = kite_client.get_option_chain(symbol, target_expiry)
    if chain.empty:
        return None

    # Filter by option type and liquidity
    filtered = chain[
        (chain["type"] == option_type.value)
        & (chain["oi"] >= settings.min_oi_threshold)
        & (chain["ltp"] > 0)
    ].copy()

    if filtered.empty:
        return None

    # Spread filter
    filtered["spread_pct"] = ((filtered["ask"] - filtered["bid"]) / filtered["ltp"].clip(lower=0.01)) * 100
    filtered = filtered[filtered["spread_pct"] < settings.max_bid_ask_spread_pct * 5]

    if filtered.empty:
        return None

    strike = _select_strike(filtered, spot, option_type, trade_type)
    row = filtered[filtered["strike"] == strike]

    if row.empty:
        row = filtered.iloc[(filtered["strike"] - spot).abs().argsort()[:1]]

    row = row.iloc[0]
    atm_strike = _find_atm_strike(filtered, spot)
    dte = (target_expiry - date.today()).days

    # Find instrument token
    inst_row = instruments[
        (instruments["name"] == symbol)
        & (instruments["instrument_type"] == option_type.value)
        & (instruments["strike"] == row["strike"])
        & (pd.to_datetime(instruments["expiry"]).dt.date == target_expiry)
    ]
    token = int(inst_row["instrument_token"].iloc[0]) if not inst_row.empty else 0

    return OptionTarget(
        symbol=symbol,
        tradingsymbol=str(row.get("tradingsymbol", "")),
        strike=float(row["strike"]),
        expiry=target_expiry,
        option_type=option_type,
        current_premium=float(row["ltp"]),
        iv=float(row.get("iv", 0.0)),
        oi=int(row["oi"]),
        instrument_token=token,
        days_to_expiry=dte,
        is_atm=abs(row["strike"] - atm_strike) < 1e-6,
    )
