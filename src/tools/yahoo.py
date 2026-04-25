from __future__ import annotations

from datetime import date
from typing import Any, Union

import pandas as pd

from src.types import ToolError


def yahoo_fundamentals(
    ticker: str, fields: list[str]
) -> Union[dict[str, Any], ToolError]:
    """Pull selected fundamentals for a ticker."""
    raise NotImplementedError


def yahoo_prices(
    ticker: str, start: date, end: date
) -> Union[pd.DataFrame, ToolError]:
    """Pull OHLCV for a ticker in [start, end]."""
    raise NotImplementedError
