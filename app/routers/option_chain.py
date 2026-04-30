from fastapi import APIRouter, HTTPException
from app.data.kite_client import kite_client
from app.engines.derivatives import analyze_derivatives

router = APIRouter(prefix="/option-chain", tags=["option-chain"])


@router.get("/{symbol}")
async def get_option_chain(symbol: str):
    symbol = symbol.upper()
    try:
        ltp_map = kite_client.get_ltp([symbol])
        spot = ltp_map.get(symbol, 0.0)
        chain = kite_client.get_option_chain(symbol)
        if chain.empty:
            raise HTTPException(status_code=404, detail=f"No option chain data for {symbol}")

        deriv = analyze_derivatives(symbol, spot)
        chain_records = chain.to_dict(orient="records")

        return {
            "symbol": symbol,
            "spot": spot,
            "pcr": deriv.pcr,
            "pcr_signal": deriv.pcr_signal.value,
            "max_pain": deriv.max_pain,
            "oi_interpretation": deriv.oi_interpretation.value,
            "writing_bias": deriv.writing_bias.value,
            "iv_skew": deriv.iv_skew,
            "call_wall": deriv.strong_call_wall,
            "put_wall": deriv.strong_put_wall,
            "supports_call_buy": deriv.supports_call_buy,
            "supports_put_buy": deriv.supports_put_buy,
            "chain": chain_records,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
