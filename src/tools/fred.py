from __future__ import annotations

from datetime import date
from typing import Union

import pandas as pd

from src.types import Episode, MacroSnapshot, ToolError


def fred_get_series(
    series_id: str, start: date, end: date
) -> Union[pd.DataFrame, ToolError]:
    """Fetch a FRED series in [start, end]. Cached on disk."""
    raise NotImplementedError


def fred_find_extrema(
    series_id: str, threshold_zscore: float, window: int
) -> Union[list[Episode], ToolError]:
    """Return episodes where the series moved by at least threshold_zscore in window."""
    raise NotImplementedError


def macro_snapshot(at: date) -> Union[MacroSnapshot, ToolError]:
    """Pull a fixed bundle of FRED series at a date and pack into MacroSnapshot."""
    raise NotImplementedError
