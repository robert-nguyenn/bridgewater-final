from __future__ import annotations

import logging
from dataclasses import asdict
from datetime import date, datetime, timedelta
from typing import Any, Optional, Union

import pandas as pd

from src.config import FRED_API_KEY
from src.tools.cache import disk_cache
from src.types import Episode, MacroSnapshot, ToolError

logger = logging.getLogger(__name__)

# FRED series IDs used by macro_snapshot. Edit MACRO_SNAPSHOT_SERIES to extend.
# `units="pc1"` requests "Percent Change from Year Ago" directly from FRED.
MACRO_SNAPSHOT_SERIES: dict[str, tuple[str, Optional[str]]] = {
    "cpi_yoy": ("CPIAUCSL", "pc1"),
    "core_pce_yoy": ("PCEPILFE", "pc1"),
    "fed_funds": ("DFF", None),
    "ten_year": ("DGS10", None),
    "dxy": ("DTWEXBGS", None),
    "unemployment": ("UNRATE", None),
    "real_gdp_yoy": ("GDPC1", "pc1"),
}


def _client():
    """Lazy fredapi client. Errors surface as ToolError, not at import time."""
    if not FRED_API_KEY:
        raise RuntimeError("FRED_API_KEY missing. See .env (set it next to ANTHROPIC_API_KEY).")
    from fredapi import Fred  # local import keeps fredapi optional at import time

    return Fred(api_key=FRED_API_KEY)


def _to_date(x: Any) -> date:
    if isinstance(x, datetime):
        return x.date()
    if isinstance(x, date):
        return x
    return pd.Timestamp(x).date()


@disk_cache("fred_series")
def fred_get_series(
    series_id: str, start: date, end: date, units: Optional[str] = None
) -> Union[pd.DataFrame, ToolError]:
    """Fetch a FRED series in [start, end]. Cached on disk by (id, start, end, units).

    Returns a DataFrame with a single column `value`, indexed by date. On any
    error returns a ToolError. Never raises."""
    try:
        fred = _client()
    except Exception as exc:
        return ToolError(
            tool="fred",
            args={"series_id": series_id, "start": start, "end": end},
            message=f"client init failed: {exc}",
        )

    try:
        kwargs: dict[str, Any] = {
            "observation_start": pd.Timestamp(start),
            "observation_end": pd.Timestamp(end),
        }
        if units:
            kwargs["units"] = units
        series = fred.get_series(series_id, **kwargs)
    except Exception as exc:
        return ToolError(
            tool="fred",
            args={"series_id": series_id, "start": start, "end": end, "units": units},
            message=f"get_series failed: {exc}",
        )

    if series is None or series.empty:
        return ToolError(
            tool="fred",
            args={"series_id": series_id, "start": start, "end": end, "units": units},
            message="empty series",
        )

    df = series.to_frame(name="value")
    df.index = pd.to_datetime(df.index)
    df.index.name = "date"
    return df


@disk_cache("fred_extrema")
def fred_find_extrema(
    series_id: str,
    threshold_zscore: float,
    window: int,
    *,
    history_start: Optional[date] = None,
    history_end: Optional[date] = None,
    min_episode_gap_days: int = 30,
) -> Union[list[Episode], ToolError]:
    """Find episodes where rolling-z exceeded `threshold_zscore`.

    Pulls the series across `[history_start, history_end]` (default: 1980-01-01
    through today), computes a rolling-window z-score, finds dates where
    abs(z) >= threshold, and groups runs separated by at least
    `min_episode_gap_days` into Episodes.

    Returns the Episode list (possibly empty) or a ToolError."""
    start = history_start or date(1980, 1, 1)
    end = history_end or date.today()

    df = fred_get_series(series_id, start, end)
    if isinstance(df, ToolError):
        return df

    s = df["value"].dropna()
    if s.empty or len(s) < window + 1:
        return []

    rolling_mean = s.rolling(window).mean()
    rolling_std = s.rolling(window).std()
    z = (s - rolling_mean) / rolling_std
    z = z.replace([float("inf"), float("-inf")], pd.NA).dropna()

    flagged = z[z.abs() >= threshold_zscore]
    if flagged.empty:
        return []

    episodes: list[Episode] = []
    cur_start = cur_end = flagged.index[0]
    cur_peak_z = float(flagged.iloc[0])
    gap = pd.Timedelta(days=min_episode_gap_days)

    for ts, val in flagged.iloc[1:].items():
        if ts - cur_end <= gap:
            cur_end = ts
            if abs(val) > abs(cur_peak_z):
                cur_peak_z = float(val)
        else:
            episodes.append(
                Episode(
                    series_id=series_id,
                    start=_to_date(cur_start),
                    end=_to_date(cur_end),
                    magnitude=cur_peak_z,
                )
            )
            cur_start = cur_end = ts
            cur_peak_z = float(val)

    episodes.append(
        Episode(
            series_id=series_id,
            start=_to_date(cur_start),
            end=_to_date(cur_end),
            magnitude=cur_peak_z,
        )
    )
    return episodes


@disk_cache("fred_macro_snapshot")
def macro_snapshot(at: date) -> Union[MacroSnapshot, ToolError]:
    """Pull the standard MACRO_SNAPSHOT_SERIES bundle at `at`.

    For each field, fetches a 90-day window ending at `at` and uses the most
    recent observation. Missing fields stay None. Returns a ToolError only if
    the FRED client itself cannot be constructed."""
    try:
        _client()
    except Exception as exc:
        return ToolError(
            tool="fred",
            args={"at": at},
            message=f"client init failed: {exc}",
        )

    window_start = at - timedelta(days=90)
    snapshot_kwargs: dict[str, Optional[float]] = {}

    for field, (series_id, units) in MACRO_SNAPSHOT_SERIES.items():
        df = fred_get_series(series_id, window_start, at, units=units)
        if isinstance(df, ToolError):
            snapshot_kwargs[field] = None
            continue
        s = df["value"].dropna()
        if s.empty:
            snapshot_kwargs[field] = None
            continue
        snapshot_kwargs[field] = float(s.iloc[-1])

    return MacroSnapshot(**snapshot_kwargs)


# Reference series for the "what moved during this window" summary the
# TreeBuilder feeds into its propose prompt. Edit to extend coverage.
WINDOW_MOVERS_SERIES: list[str] = [
    "CPIAUCSL",       # headline CPI
    "PCEPILFE",       # core PCE
    "DFF",            # fed funds effective
    "DGS10",          # 10y UST
    "DGS2",           # 2y UST
    "DTWEXBGS",       # USD broad index
    "DCOILWTICO",     # WTI crude
    "BAMLH0A0HYM2",   # HY OAS
    "VIXCLS",         # VIX
    "UNRATE",         # unemployment
]


@disk_cache("fred_window_movers")
def window_movers(
    start: date,
    end: date,
    series_ids: Optional[list[str]] = None,
) -> Union[list[dict[str, Any]], ToolError]:
    """Find which FRED series moved most during ``[start, end]``.

    For each candidate series, computes pre-event mean/std (the 90 days before
    `start`), then peak deviation during the window in pre-event sigmas. Returns
    movers ranked by ``abs(peak_z)`` descending, capped at 10 items. Cached.

    Used by TreeBuilder to ground the propose prompt in observed movers."""
    series_ids = series_ids or WINDOW_MOVERS_SERIES
    pre_start = start - timedelta(days=90)
    fetch_end = end + timedelta(days=14)

    movers: list[dict[str, Any]] = []
    for sid in series_ids:
        df = fred_get_series(sid, pre_start, fetch_end)
        if isinstance(df, ToolError):
            continue
        s = df["value"].dropna()
        if isinstance(s, pd.DataFrame):
            if s.shape[1] == 1:
                s = s.iloc[:, 0]
            else:
                continue
        if s.empty:
            continue
        s.index = pd.to_datetime(s.index)
        t0 = pd.Timestamp(start)
        t1 = pd.Timestamp(end)
        pre = s[s.index < t0]
        post = s[(s.index >= t0) & (s.index <= t1)]
        if pre.empty or post.empty or len(pre) < 5:
            continue
        pre_mean = float(pre.mean())
        pre_std = float(pre.std()) if len(pre) > 1 else 0.0
        if pre_std <= 1e-9:
            continue
        deviations = post - pre_mean
        idx = deviations.abs().idxmax()
        peak_dev = float(deviations.loc[idx])
        peak_z = peak_dev / pre_std
        movers.append({
            "series_id": sid,
            "peak_z": round(peak_z, 2),
            "peak_deviation": round(peak_dev, 4),
            "direction": "up" if peak_z > 0 else "down",
        })

    movers.sort(key=lambda m: -abs(m["peak_z"]))
    return movers[:10]


# Re-exports so tests and integrators can introspect the contract.
__all__ = [
    "fred_get_series",
    "fred_find_extrema",
    "macro_snapshot",
    "window_movers",
    "MACRO_SNAPSHOT_SERIES",
    "WINDOW_MOVERS_SERIES",
]
