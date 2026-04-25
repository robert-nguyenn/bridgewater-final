from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from src.agents import scenario
from src.agents.scenario import (
    KalshiMarket,
    NewsItem,
    TailScenario,
    _is_meaningful_market,
    _market_from_raw,
    _parse_dollar,
    _parse_news_meta,
    _post_process,
)
from src.types import ToolBundle


def test_scenario_module_has_run():
    assert hasattr(scenario, "run")


def test_tail_scenario_constructs():
    s = TailScenario(
        text="Fed cuts 50bp at the March 2026 meeting",
        probability=0.12,
        probability_source="kalshi_exact",
        kalshi_market_ticker="FED-MAR26-CUT50",
        kalshi_anchor_price=0.12,
        policy_axis="monetary",
        time_horizon_days=60,
    )
    assert 0.02 <= s.probability <= 0.5
    assert s.feedback_event == s.text


def test_tail_scenario_explicit_feedback_overrides_default():
    s = TailScenario(
        text="Five Chinese chip firms added to entity list",
        probability=0.08,
        feedback_event="US Commerce adds five named Chinese semiconductor firms to the entity list",
    )
    assert "Commerce" in s.feedback_event


def test_kalshi_market_constructs():
    m = KalshiMarket(ticker="X", event_ticker="E", title="t", yes_price=0.27)
    assert m.yes_price == 0.27


def test_parse_dollar_handles_strings_and_none():
    assert _parse_dollar("0.4500") == 0.45
    assert _parse_dollar("0") == 0.0
    assert _parse_dollar(None) is None
    assert _parse_dollar("") is None
    assert _parse_dollar("garbage") is None


def test_market_from_raw_maps_dollar_fields():
    raw = {
        "ticker": "KXFED-26MAR-CUT50",
        "event_ticker": "KXFED-26MAR",
        "title": "Fed cuts 50bp in March",
        "last_price_dollars": "0.1200",
        "yes_bid_dollars": "0.1100",
        "yes_ask_dollars": "0.1300",
        "yes_sub_title": "yes 50bp cut",
        "no_sub_title": "no 50bp cut",
        "close_time": "2026-03-19T18:00:00Z",
    }
    m = _market_from_raw(raw)
    assert m.yes_price == 0.12
    assert m.yes_bid == 0.11
    assert m.event_ticker == "KXFED-26MAR"


def test_news_item_constructs():
    n = NewsItem(id="42", text="some speech text", date="2025-06-26", source="FED")
    assert n.source == "FED"


def test_parse_news_meta_extracts_date_and_source():
    text = "On 2025-06-26 the FED delivered a speech where the following facts were extracted:* x"
    date, source = _parse_news_meta(text)
    assert date == "2025-06-26"
    assert source == "FED"


def test_parse_news_meta_returns_none_on_no_match():
    date, source = _parse_news_meta("random text without prefix")
    assert date is None
    assert source is None


def test_hf_news_search_empty_query_returns_empty():
    from src.agents.scenario import hf_news_search

    assert hf_news_search("a") == []


# --- kalshi meaningful-market filter tests ----------------------------------


def test_is_meaningful_market_accepts_traded_mid_priced_market():
    m = KalshiMarket(ticker="T", event_ticker="E", title="t", yes_price=0.18, volume_24h=42.0)
    assert _is_meaningful_market(m) is True


def test_is_meaningful_market_rejects_no_price():
    m = KalshiMarket(ticker="T", event_ticker="E", title="t", yes_price=None, volume_24h=42.0)
    assert _is_meaningful_market(m) is False


def test_is_meaningful_market_rejects_resolved_zero_or_one():
    m_zero = KalshiMarket(ticker="T", event_ticker="E", title="t", yes_price=0.0, volume_24h=10.0)
    m_one = KalshiMarket(ticker="T", event_ticker="E", title="t", yes_price=1.0, volume_24h=10.0)
    assert _is_meaningful_market(m_zero) is False
    assert _is_meaningful_market(m_one) is False


def test_is_meaningful_market_rejects_zero_volume():
    m = KalshiMarket(ticker="T", event_ticker="E", title="t", yes_price=0.5, volume_24h=0.0)
    assert _is_meaningful_market(m) is False
    m_none = KalshiMarket(ticker="T", event_ticker="E", title="t", yes_price=0.5, volume_24h=None)
    assert _is_meaningful_market(m_none) is False


# --- post-processing contract tests -----------------------------------------


def _market(ticker: str, price: float) -> KalshiMarket:
    return KalshiMarket(ticker=ticker, event_ticker="E", title=ticker, yes_price=price)


def test_post_process_kalshi_exact_overwrites_probability_with_anchor():
    raw = [
        {
            "text": "Fed cuts 50bp",
            "probability": 0.30,  # model claims 0.30
            "probability_source": "kalshi_exact",
            "kalshi_market_ticker": "TICK1",
            "policy_axis": "monetary",
            "time_horizon_days": 60,
            "feedback_event": "Fed cuts 50bp",
            "rationale": "r",
        }
    ]
    out = _post_process(raw, kalshi_by_ticker={"TICK1": _market("TICK1", 0.12)}, valid_news_ids=set())
    assert out[0].probability == 0.12  # anchor wins
    assert out[0].kalshi_anchor_price == 0.12
    assert out[0].delta_rationale is None


def test_post_process_kalshi_adjusted_keeps_probability_within_band():
    raw = [
        {
            "text": "Fed cuts 75bp",
            "probability": 0.18,
            "probability_source": "kalshi_adjusted",
            "kalshi_market_ticker": "TICK1",
            "delta_rationale": "market asks any cut, our scenario is more specific",
            "policy_axis": "monetary",
            "time_horizon_days": 60,
            "feedback_event": "Fed cuts 75bp",
            "rationale": "r",
        }
    ]
    out = _post_process(raw, kalshi_by_ticker={"TICK1": _market("TICK1", 0.25)}, valid_news_ids=set())
    assert out[0].probability == 0.18
    assert out[0].kalshi_anchor_price == 0.25
    assert out[0].probability_source == "kalshi_adjusted"


def test_post_process_violated_delta_falls_back_to_overwrite():
    raw = [
        {
            "text": "x",
            "probability": 0.40,  # delta = 0.40 - 0.10 = 0.30, exceeds 0.15 cap
            "probability_source": "kalshi_adjusted",
            "kalshi_market_ticker": "TICK1",
            "delta_rationale": "trying to escape",
            "policy_axis": "monetary",
            "time_horizon_days": 60,
            "feedback_event": "x",
            "rationale": "r",
        }
    ]
    out = _post_process(raw, kalshi_by_ticker={"TICK1": _market("TICK1", 0.10)}, valid_news_ids=set())
    assert out[0].probability == 0.10
    assert out[0].probability_source == "kalshi_exact"
    assert out[0].delta_rationale is None


def test_post_process_invented_ticker_strips_kalshi_claims():
    raw = [
        {
            "text": "x",
            "probability": 0.15,
            "probability_source": "kalshi_exact",
            "kalshi_market_ticker": "MADE_UP_TICKER",
            "policy_axis": "monetary",
            "time_horizon_days": 60,
            "feedback_event": "x",
            "rationale": "r",
        }
    ]
    out = _post_process(raw, kalshi_by_ticker={}, valid_news_ids=set())
    assert out[0].kalshi_market_ticker is None
    assert out[0].probability_source == "llm_calibrated"
    assert out[0].kalshi_anchor_price is None


def test_post_process_filters_invalid_news_ids():
    raw = [
        {
            "text": "x",
            "probability": 0.15,
            "probability_source": "llm_calibrated",
            "news_citations": ["valid-1", "made-up-2", "valid-3"],
            "policy_axis": "monetary",
            "time_horizon_days": 60,
            "feedback_event": "x",
            "rationale": "r",
        }
    ]
    out = _post_process(raw, kalshi_by_ticker={}, valid_news_ids={"valid-1", "valid-3"})
    assert out[0].news_citations == ["valid-1", "valid-3"]


# --- run() smoke with a mocked client ---------------------------------------


def test_run_with_mocked_client_returns_parsed_scenarios(monkeypatch):
    # Stub Kalshi + news so we don't hit the network
    monkeypatch.setattr(
        scenario,
        "kalshi_policy_search",
        lambda q: [_market("TICK1", 0.12)],
    )
    monkeypatch.setattr(
        scenario,
        "hf_news_search",
        lambda q, limit=25: [NewsItem(id="N1", text="news text", date="2026-01-01", source="FED")],
    )

    fake_tool_use = SimpleNamespace(
        type="tool_use",
        name="submit_tail_scenarios",
        input={
            "scenarios": [
                {
                    "text": "Fed cuts 50bp at March 2026 meeting",
                    "probability": 0.30,  # will be overwritten by anchor
                    "probability_source": "kalshi_exact",
                    "kalshi_market_ticker": "TICK1",
                    "news_citations": ["N1", "BOGUS"],
                    "policy_axis": "monetary",
                    "time_horizon_days": 60,
                    "feedback_event": "Fed cuts 50bp at March 2026 meeting",
                    "rationale": "r",
                },
                {
                    "text": "Treasury sanctions on a major Chinese tech firm",
                    "probability": 0.07,
                    "probability_source": "llm_calibrated",
                    "policy_axis": "geopolitical",
                    "time_horizon_days": 90,
                    "feedback_event": "Treasury sanctions on a major Chinese tech firm",
                    "rationale": "r",
                },
            ]
        },
    )
    fake_response = SimpleNamespace(
        content=[fake_tool_use],
        usage=SimpleNamespace(input_tokens=100, output_tokens=200),
    )
    fake_client = MagicMock()
    fake_client.messages.create.return_value = fake_response

    out = scenario.run(
        "25% tariff on Chinese semiconductors",
        tools=ToolBundle(),
        model="claude-opus-4-7",
        client=fake_client,
    )

    assert len(out) == 2
    assert out[0].probability == 0.12  # anchor enforced
    assert out[0].news_citations == ["N1"]  # bogus dropped
    assert out[1].kalshi_market_ticker is None  # llm_calibrated has no ticker
    fake_client.messages.create.assert_called_once()
