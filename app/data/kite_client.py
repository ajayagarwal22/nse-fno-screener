"""Zerodha KiteConnect wrapper providing clean async-friendly interfaces."""
import asyncio
from datetime import date, datetime, timedelta
from functools import lru_cache
from typing import Optional

import pandas as pd
from kiteconnect import KiteConnect

from app.config import settings
from app.data import cache


class KiteClient:
    def __init__(self):
        self._kite: Optional[KiteConnect] = None

    def _connect(self) -> KiteConnect:
        if self._kite is None:
            self._kite = KiteConnect(api_key=settings.kite_api_key)
            self._kite.set_access_token(settings.kite_access_token)
        return self._kite

    @property
    def kite(self) -> KiteConnect:
        return self._connect()

    # ------------------------------------------------------------------
    # Instruments
    # ------------------------------------------------------------------

    @lru_cache(maxsize=1)
    def get_fno_instruments(self) -> pd.DataFrame:
        """Return all NSE F&O instruments as a DataFrame. Cached for the session."""
        instruments = self.kite.instruments("NFO")
        df = pd.DataFrame(instruments)
        return df

    def get_fno_stock_symbols(self) -> list[str]:
        """Return unique underlying symbols in the F&O universe."""
        df = self.get_fno_instruments()
        return sorted(df["name"].unique().tolist())

    # ------------------------------------------------------------------
    # OHLCV
    # ------------------------------------------------------------------

    def get_ohlcv(
        self,
        instrument_token: int,
        interval: str = "5minute",
        from_date: Optional[date] = None,
        to_date: Optional[date] = None,
    ) -> pd.DataFrame:
        """Fetch historical OHLCV candles for an instrument."""
        if to_date is None:
            to_date = datetime.now()
        if from_date is None:
            from_date = to_date - timedelta(days=5)

        records = self.kite.historical_data(
            instrument_token=instrument_token,
            from_date=from_date,
            to_date=to_date,
            interval=interval,
        )
        df = pd.DataFrame(records)
        if df.empty:
            return df
        df.set_index("date", inplace=True)
        df.index = pd.to_datetime(df.index)
        return df

    # ------------------------------------------------------------------
    # LTP
    # ------------------------------------------------------------------

    def get_ltp(self, trading_symbols: list[str], exchange: str = "NSE") -> dict[str, float]:
        """Return last traded prices for a list of symbols."""
        instrument_keys = [f"{exchange}:{sym}" for sym in trading_symbols]
        data = self.kite.ltp(instrument_keys)
        result = {}
        for key, val in data.items():
            sym = key.split(":")[1]
            price = val["last_price"]
            result[sym] = price
            cache.set_ltp(sym, price)
        return result

    # ------------------------------------------------------------------
    # Option chain
    # ------------------------------------------------------------------

    def get_option_chain(self, underlying: str, expiry: Optional[date] = None) -> pd.DataFrame:
        """
        Return a DataFrame of the option chain for the given underlying.
        Columns: strike, expiry, ce_oi, ce_volume, ce_iv, ce_ltp, pe_oi, pe_volume, pe_iv, pe_ltp
        """
        instruments = self.get_fno_instruments()
        chain = instruments[
            (instruments["name"] == underlying) & (instruments["instrument_type"].isin(["CE", "PE"]))
        ].copy()

        if expiry:
            chain = chain[chain["expiry"] == pd.Timestamp(expiry)]

        if chain.empty:
            return pd.DataFrame()

        tokens = chain["instrument_token"].tolist()
        keys = [f"NFO:{row['tradingsymbol']}" for _, row in chain.iterrows()]

        try:
            quotes = self.kite.quote(keys)
        except Exception:
            return pd.DataFrame()

        rows = []
        for _, row in chain.iterrows():
            key = f"NFO:{row['tradingsymbol']}"
            q = quotes.get(key, {})
            ohlc = q.get("ohlc", {})
            rows.append({
                "strike": row["strike"],
                "expiry": row["expiry"],
                "type": row["instrument_type"],
                "oi": q.get("oi", 0),
                "volume": q.get("volume", 0),
                "iv": q.get("implied_volatility", 0.0),
                "ltp": q.get("last_price", 0.0),
                "bid": q.get("depth", {}).get("buy", [{}])[0].get("price", 0),
                "ask": q.get("depth", {}).get("sell", [{}])[0].get("price", 0),
                "tradingsymbol": row["tradingsymbol"],
            })

        df = pd.DataFrame(rows)
        cache.set_oi(underlying, df)
        return df

    # ------------------------------------------------------------------
    # Generate access token (OAuth flow)
    # ------------------------------------------------------------------

    def generate_access_token(self, request_token: str) -> str:
        data = self.kite.generate_session(request_token, api_secret=settings.kite_api_secret)
        return data["access_token"]


kite_client = KiteClient()
