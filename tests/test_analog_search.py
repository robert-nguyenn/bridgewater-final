from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

from src.agents import analog_search
from src.agents.analog_search import _dedupe, _filter_direction
from src.types import Episode, Node, ToolBundle, ToolError


def _node(label: str = "Oil prices rise", layer: int = 1) -> Node:
    return Node(
        id="n_oil_up",
        label=label,
        description=f"{label} (test description)",
        layer=layer,
    )


def _resp(text: str) -> MagicMock:
    msg = MagicMock()
    msg.content = [MagicMock(text=text)]
    return msg


def _fake_client(*responses: str) -> MagicMock:
    client = MagicMock()
    client.messages.create.side_effect = [_resp(r) for r in responses]
    return client


def _ep(series: str, year: int, month: int, day: int, mag: float) -> Episode:
    d = date(year, month, day)
    return Episode(series_id=series, start=d, end=d, magnitude=mag)


PLAN_OK = """```json
{
  "primary_series": "DCOILWTICO",
  "rationale": "WTI is the cleanest proxy for oil shocks",
  "threshold_zscore": 2.0,
  "window_obs": 60,
  "lookback_years": 40,
  "direction": "up"
}
```"""

PLAN_NULL = """```json
{
  "primary_series": null,
  "rationale": "no clean single proxy",
  "threshold_zscore": 2.0,
  "window_obs": 60,
  "lookback_years": 40,
  "direction": "either"
}
```"""

LABEL_TWO = """```json
{
  "episodes": [
    {"start": "2022-02-24", "end": "2022-06-14", "candidate_event": "Russia invades Ukraine"},
    {"start": "2008-07-03", "end": "2008-11-28", "candidate_event": "2008 GFC oil unwind"}
  ]
}
```"""


def test_module_has_run():
    assert hasattr(analog_search, "run")


def test_filter_direction_up_keeps_only_positive():
    eps = [_ep("X", 2020, 1, 1, 2.5), _ep("X", 2010, 1, 1, -2.5)]
    out = _filter_direction(eps, "up")
    assert len(out) == 1 and out[0].magnitude == 2.5


def test_filter_direction_down_keeps_only_negative():
    eps = [_ep("X", 2020, 1, 1, 2.5), _ep("X", 2010, 1, 1, -2.5)]
    out = _filter_direction(eps, "down")
    assert len(out) == 1 and out[0].magnitude == -2.5


def test_filter_direction_either_passes_through():
    eps = [_ep("X", 2020, 1, 1, 2.5), _ep("X", 2010, 1, 1, -2.5)]
    assert len(_filter_direction(eps, "either")) == 2


def test_dedupe_drops_close_episodes_on_same_series():
    eps = [
        _ep("X", 2022, 3, 1, 3.0),
        _ep("X", 2022, 6, 1, 2.5),  # ~92 days from kept, drop
        _ep("X", 2008, 9, 1, 2.8),  # far, keep
    ]
    out = _dedupe(eps, window_days=180)
    starts = [e.start for e in out]
    assert date(2022, 3, 1) in starts
    assert date(2008, 9, 1) in starts
    assert date(2022, 6, 1) not in starts


def test_dedupe_does_not_clash_across_series():
    eps = [
        _ep("X", 2022, 3, 1, 3.0),
        _ep("Y", 2022, 4, 1, 2.5),  # close in time but different series, keep
    ]
    assert len(_dedupe(eps, window_days=180)) == 2


def test_run_happy_path_attaches_labels():
    fake_fred = MagicMock()
    fake_fred.fred_find_extrema.return_value = [
        _ep("DCOILWTICO", 2022, 2, 24, 3.4),
        _ep("DCOILWTICO", 2008, 7, 3, 2.8),
    ]
    tools = ToolBundle(fred=fake_fred)
    client = _fake_client(PLAN_OK, LABEL_TWO)

    out = analog_search.run(_node(), tools=tools, client=client, k=4, enable_web_search=False)

    assert len(out) == 2
    assert out[0].candidate_event == "Russia invades Ukraine"
    assert out[1].candidate_event == "2008 GFC oil unwind"
    assert client.messages.create.call_count == 2

    call = fake_fred.fred_find_extrema.call_args
    assert call.args[0] == "DCOILWTICO"
    assert call.args[1] == 2.0
    assert call.args[2] == 60
    assert "history_start" in call.kwargs
    assert "history_end" in call.kwargs


def test_run_returns_empty_when_planner_picks_no_series():
    fake_fred = MagicMock()
    tools = ToolBundle(fred=fake_fred)
    client = _fake_client(PLAN_NULL)

    out = analog_search.run(_node(), tools=tools, client=client, enable_web_search=False)

    assert out == []
    fake_fred.fred_find_extrema.assert_not_called()


def test_run_returns_empty_on_unparseable_plan():
    fake_fred = MagicMock()
    tools = ToolBundle(fred=fake_fred)
    client = _fake_client("not valid json at all")

    out = analog_search.run(_node(), tools=tools, client=client, enable_web_search=False)

    assert out == []
    fake_fred.fred_find_extrema.assert_not_called()


def test_run_returns_empty_on_tool_error():
    fake_fred = MagicMock()
    fake_fred.fred_find_extrema.return_value = ToolError(
        tool="fred", args={}, message="rate limit"
    )
    tools = ToolBundle(fred=fake_fred)
    client = _fake_client(PLAN_OK)

    out = analog_search.run(_node(), tools=tools, client=client, enable_web_search=False)

    assert out == []
    # Plan call happened but no label call (no episodes to label).
    assert client.messages.create.call_count == 1


def test_run_filters_by_direction_from_plan():
    fake_fred = MagicMock()
    fake_fred.fred_find_extrema.return_value = [
        _ep("DCOILWTICO", 2022, 2, 24, 3.4),     # up, kept
        _ep("DCOILWTICO", 2014, 11, 28, -2.8),   # down, dropped (plan said "up")
    ]
    tools = ToolBundle(fred=fake_fred)
    label_one = (
        '```json\n{"episodes": ['
        '{"start": "2022-02-24", "end": "2022-02-24", "candidate_event": "x"}'
        ']}\n```'
    )
    client = _fake_client(PLAN_OK, label_one)

    out = analog_search.run(_node(), tools=tools, client=client, enable_web_search=False)

    assert len(out) == 1
    assert out[0].magnitude == 3.4


def test_run_caps_at_k_by_magnitude():
    fake_fred = MagicMock()
    fake_fred.fred_find_extrema.return_value = [
        _ep("DCOILWTICO", 2022, 1, 1, 3.5),
        _ep("DCOILWTICO", 2018, 1, 1, 3.0),
        _ep("DCOILWTICO", 2014, 1, 1, 2.8),
        _ep("DCOILWTICO", 2010, 1, 1, 2.6),
        _ep("DCOILWTICO", 2005, 1, 1, 2.4),
    ]
    tools = ToolBundle(fred=fake_fred)
    label_two = (
        '```json\n{"episodes": ['
        '{"start": "2022-01-01", "end": "2022-01-01", "candidate_event": "a"},'
        '{"start": "2018-01-01", "end": "2018-01-01", "candidate_event": "b"}'
        ']}\n```'
    )
    client = _fake_client(PLAN_OK, label_two)

    out = analog_search.run(_node(), tools=tools, client=client, k=2, enable_web_search=False)

    assert len(out) == 2
    assert out[0].magnitude == 3.5
    assert out[1].magnitude == 3.0
    assert out[0].candidate_event == "a"
    assert out[1].candidate_event == "b"


def test_run_handles_missing_fred_tool_gracefully():
    out = analog_search.run(_node(), tools=ToolBundle(), enable_web_search=False)  # fred is None
    assert out == []


def test_run_label_response_with_fewer_entries_pads_with_none():
    fake_fred = MagicMock()
    fake_fred.fred_find_extrema.return_value = [
        _ep("DCOILWTICO", 2022, 1, 1, 3.5),
        _ep("DCOILWTICO", 2018, 1, 1, 3.0),
    ]
    tools = ToolBundle(fred=fake_fred)
    # Only one label returned for two episodes.
    label_short = (
        '```json\n{"episodes": ['
        '{"start": "2022-01-01", "end": "2022-01-01", "candidate_event": "only one"}'
        ']}\n```'
    )
    client = _fake_client(PLAN_OK, label_short)

    out = analog_search.run(_node(), tools=tools, client=client, enable_web_search=False)

    assert len(out) == 2
    assert out[0].candidate_event == "only one"
    assert out[1].candidate_event is None
