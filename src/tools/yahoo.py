from __future__ import annotations

import contextlib
import io
import logging
from datetime import date
from typing import Any, Union

import pandas as pd

from src.tools.cache import disk_cache
from src.types import ToolError

logger = logging.getLogger(__name__)

# Silence yfinance's chatty "$X: possibly delisted" / "1 Failed download:" logs
# at import time. We surface the same information as a typed ToolError instead.
logging.getLogger("yfinance").setLevel(logging.CRITICAL)


@disk_cache("yahoo_fundamentals")
def yahoo_fundamentals(
    ticker: str, fields: list[str]
) -> Union[dict[str, Any], ToolError]:
    """Pull selected fundamentals for a ticker. Cached on disk."""
    try:
        import yfinance as yf
    except ImportError as exc:
        return ToolError(
            tool="yahoo",
            args={"ticker": ticker, "fields": fields},
            message=f"yfinance not installed: {exc}",
        )

    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            info = yf.Ticker(ticker).info
    except Exception as exc:
        return ToolError(
            tool="yahoo",
            args={"ticker": ticker, "fields": fields},
            message=f"info fetch failed: {exc}",
        )

    if not info:
        return ToolError(
            tool="yahoo",
            args={"ticker": ticker, "fields": fields},
            message="empty info dict",
        )

    return {f: info.get(f) for f in fields}


@disk_cache("yahoo_prices")
def yahoo_prices(
    ticker: str, start: date, end: date
) -> Union[pd.DataFrame, ToolError]:
    """Pull OHLCV for a ticker in [start, end]. Cached on disk.

    Suppresses yfinance's chatty stderr/stdout for delisted or pre-inception
    requests; the failure surfaces as a ToolError instead. Always returns a
    DataFrame with single-level columns (yfinance's newer versions return a
    MultiIndex even for single-ticker requests; we flatten it here).
    """
    try:
        import yfinance as yf
    except ImportError as exc:
        return ToolError(
            tool="yahoo",
            args={"ticker": ticker, "start": start, "end": end},
            message=f"yfinance not installed: {exc}",
        )

    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            df = yf.download(
                ticker,
                start=str(start),
                end=str(end),
                progress=False,
                auto_adjust=False,
                threads=False,
            )
    except Exception as exc:
        return ToolError(
            tool="yahoo",
            args={"ticker": ticker, "start": start, "end": end},
            message=f"download failed: {exc}",
        )

    if df is None or df.empty:
        return ToolError(
            tool="yahoo",
            args={"ticker": ticker, "start": start, "end": end},
            message="empty dataframe (likely delisted, pre-inception, or no data in range)",
        )

    # Flatten yfinance's MultiIndex columns so df["Close"] returns a Series.
    if isinstance(df.columns, pd.MultiIndex):
        df = df.copy()
        df.columns = df.columns.get_level_values(0)

    return df


__all__ = ["yahoo_fundamentals", "yahoo_prices"]
