"""
ITM strike selection.

CALL signals → first strike BELOW spot (1 ITM)
  Example: spot=973, interval=10 → 970 CE

PUT signals  → first strike ABOVE spot (1 ITM)
  Example: spot=973, interval=10 → 980 PE

Always uses the nearest weekly expiry with ≥1 trading day remaining.
Returns None (with a logged warning) if the exact contract is not found
in the instruments cache — caller should skip the trade.
"""
import logging
import math
from datetime import date
from typing import Optional, Tuple

from paper_trader import instruments as inst
from paper_trader.config import ITM_STEPS

logger = logging.getLogger("paper_trader.strike_picker")

# Return type: (strike, option_type, expiry, instrument_token)
PickResult = Tuple[float, str, date, int]


def pick(symbol: str, spot: float, direction: str) -> Optional[PickResult]:
    """
    Select the ITM option contract for a given signal.

    Args:
        symbol:    Underlying symbol, e.g. "NIFTY", "SBIN"
        spot:      Current spot price of the underlying
        direction: "CALL" or "PUT"

    Returns:
        (strike, option_type, expiry, instrument_token) or None on failure.
    """
    interval   = inst.get_strike_interval(symbol)
    option_type = "CE" if direction == "CALL" else "PE"

    strike = _select_strike(spot, interval, direction)
    logger.debug(
        f"[StrikePicker] {symbol} spot={spot:.2f} direction={direction} "
        f"interval={interval} → strike={strike} {option_type}"
    )

    expiry = inst.get_nearest_expiry(symbol, min_days=1)
    if expiry is None:
        logger.warning(f"[StrikePicker] No valid expiry for {symbol}")
        return None

    token = inst.resolve_token(symbol, strike, option_type, expiry)
    if token is None:
        # One retry: try next expiry in case nearest has no contracts yet
        expiry = inst.get_nearest_expiry(symbol, min_days=2)
        if expiry:
            token = inst.resolve_token(symbol, strike, option_type, expiry)

    if token is None:
        logger.warning(
            f"[StrikePicker] Contract not found: {symbol} {strike}{option_type} {expiry}"
        )
        return None

    logger.info(
        f"[StrikePicker] Selected {symbol} {strike}{option_type} "
        f"exp={expiry} token={token}"
    )
    return strike, option_type, expiry, token


def _select_strike(spot: float, interval: int, direction: str) -> float:
    """
    Compute the ITM strike.
    CALL: round DOWN to nearest interval (first strike below spot)
    PUT:  round UP   to nearest interval (first strike above spot)

    ITM_STEPS > 1 goes deeper ITM by that many intervals.
    """
    if direction == "CALL":
        atm = math.floor(spot / interval) * interval
        return atm - (ITM_STEPS - 1) * interval
    else:
        atm = math.ceil(spot / interval) * interval
        # If spot is exactly on a strike, go one interval higher
        if atm == spot:
            atm += interval
        return atm + (ITM_STEPS - 1) * interval
