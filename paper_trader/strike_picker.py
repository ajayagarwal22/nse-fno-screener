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
    option_type = "CE" if direction == "CALL" else "PE"

    expiry = inst.get_nearest_expiry(symbol, min_days=1)
    if expiry is None:
        logger.warning(f"[StrikePicker] No valid expiry for {symbol}")
        return None

    # Pick directly from available strikes in the chain — avoids interval
    # mis-detection when the instruments list contains old contracts with
    # different step sizes (e.g. BHEL 2.5-pt historical vs 10-pt current).
    result = inst.pick_itm_strike(symbol, spot, option_type, expiry, ITM_STEPS)
    if result is None:
        # Retry with next expiry
        expiry2 = inst.get_nearest_expiry(symbol, min_days=2)
        if expiry2 and expiry2 != expiry:
            result = inst.pick_itm_strike(symbol, spot, option_type, expiry2, ITM_STEPS)
            if result:
                expiry = expiry2

    if result is None:
        logger.warning(
            f"[StrikePicker] Contract not found: {symbol} {option_type} near {spot:.2f} exp={expiry}"
        )
        return None

    strike, token = result
    logger.info(
        f"[StrikePicker] Selected {symbol} {strike}{option_type} "
        f"exp={expiry} token={token}"
    )
    return strike, option_type, expiry, token
