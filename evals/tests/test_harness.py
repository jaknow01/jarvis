"""Unit tests for the eval harness — pure helpers only (no network, no API keys).

Covers the parts most likely to silently break a comparison: provider-agnostic token
extraction, price-table lookup (including the reported-without-provider case), cost math,
env-override isolation, and usage/tool aggregation.
"""


from evals.harness.instrument import UsageCapture, LlmCall, ToolInvocation, _extract_tokens
from evals.harness.cost import PriceTable, ModelPrice, cost_from_usage, total_usd
from evals.harness.config import EvalConfig, RoleModel, apply_overrides


# --------------------------------------------------------------- token extraction
def test_extract_tokens_openai_chat_flat():
    assert _extract_tokens({"prompt_tokens": 10, "completion_tokens": 3}) == (10, 3)


def test_extract_tokens_responses_nested_usage():
    class Usage:  # mimics ResponseUsage
        input_tokens = 869
        output_tokens = 15

    assert _extract_tokens({"usage": Usage()}) == (869, 15)


def test_extract_tokens_flat_responses_style():
    assert _extract_tokens({"input_tokens": 5, "output_tokens": 7}) == (5, 7)


def test_extract_tokens_missing_is_zero():
    assert _extract_tokens({}) == (0, 0)
    assert _extract_tokens({"other": 1}) == (0, 0)


# --------------------------------------------------------------- price table
def _table():
    return PriceTable(
        {
            "openai/gpt-4o-mini": ModelPrice(0.15, 0.60),
            "openrouter/deepseek/deepseek-v4-flash-0731": ModelPrice(0.14, 0.28),
        }
    )


def test_price_exact_and_bare_openai():
    t = _table()
    # bare model resolves to openai/<model>
    rc = t.price_tokens("weather_agent", "gpt-4o-mini", 1_000_000, 1_000_000)
    assert rc.input_cost_usd == 0.15 and rc.output_cost_usd == 0.60


def test_price_reported_without_provider_prefix():
    # OpenAILike reports "deepseek/deepseek-v4-flash-0731" (no leading openrouter/).
    t = _table()
    rc = t.price_tokens("weather_agent", "deepseek/deepseek-v4-flash-0731", 2_000_000, 0)
    assert rc.input_cost_usd == 0.28 and not rc.unpriced


def test_price_unknown_is_unpriced_not_zero():
    t = _table()
    rc = t.price_tokens("composer", "some/unknown-model", 1000, 1000)
    assert rc.unpriced and rc.total_cost_usd is None


def test_cost_from_usage_and_total():
    t = _table()
    usage = {("composer", "gpt-4o-mini"): {"prompt_tokens": 1_000_000, "completion_tokens": 0}}
    costs = cost_from_usage(usage, t)
    assert len(costs) == 1 and costs[0].input_cost_usd == 0.15
    assert total_usd(costs) == 0.15


def test_total_usd_all_unpriced_is_none():
    t = _table()
    usage = {("x", "unknown"): {"prompt_tokens": 10, "completion_tokens": 10}}
    assert total_usd(cost_from_usage(usage, t)) is None


# --------------------------------------------------------------- config overrides
def _profiles(tmp_path, monkeypatch):
    (tmp_path / "base.yaml").write_text(
        "default: {model: openrouter/a, reasoning: off}\nagents: {composer: openai/c}\n")
    monkeypatch.setenv("MODEL_PROFILES_DIR", str(tmp_path))
    monkeypatch.setenv("MODEL_PROFILE", "base")


def test_config_profile_merges_overrides(tmp_path, monkeypatch):
    _profiles(tmp_path, monkeypatch)
    cfg = EvalConfig(
        label="t",
        overrides={"composer": RoleModel("composer", "openai/gpt-4.1-mini", "low"),
                   "default": RoleModel("default", "openai-compat/q")},
    )
    p = cfg.profile()
    assert p.for_agent("composer").model == "openai/gpt-4.1-mini"
    assert p.for_agent("composer").reasoning == "low"
    assert p.for_agent("weather_agent").model == "openai-compat/q"
    assert p.for_agent("weather_agent").reasoning == "off"  # inherited from the base default


def test_apply_overrides_is_scoped(tmp_path, monkeypatch):
    from lib.core.model_profiles import active_profile
    _profiles(tmp_path, monkeypatch)
    cfg = EvalConfig(label="t", overrides={"composer": RoleModel("composer", "openai/x")})
    with apply_overrides(cfg):
        assert active_profile().for_agent("composer").model == "openai/x"
    assert active_profile().for_agent("composer").model == "openai/c"


def test_explicit_base_profile(tmp_path, monkeypatch):
    _profiles(tmp_path, monkeypatch)
    (tmp_path / "other.yaml").write_text("default: openai/o\n")
    assert EvalConfig(label="t", base_profile="other").profile().default.model == "openai/o"


# --------------------------------------------------------------- capture aggregation
def test_mock_tools_swaps_and_restores():
    from lib.core import TOOLS_BY_AGENT
    from evals.harness.mocks import mock_tools

    before = TOOLS_BY_AGENT["weather_agent"]
    before_names = [t.metadata.name for t in before]
    with mock_tools():
        during = TOOLS_BY_AGENT["weather_agent"]
        # same tools (names/schema preserved) but different (mocked) objects
        assert [t.metadata.name for t in during] == before_names
        assert during is not before
    # restored to the exact original list object on exit
    assert TOOLS_BY_AGENT["weather_agent"] is before


def test_mock_fixture_lookup():
    from evals.harness.mocks import _fixture_for

    assert _fixture_for("current_weather")["city"] == "Warszawa"
    # unknown tool -> generic confirmation, never a crash
    assert _fixture_for("some_unknown_tool") == {"status": "ok", "_mock": "some_unknown_tool"}


def test_sweep_config_parsing():
    from evals.harness.sweep_config import SweepConfig

    raw = {
        "name": "t",
        "role": "composer",
        "judge_model": "gpt-4o",
        "dataset": "labeled",
        "limit": 3,
        "baseline": {"coordinator": "openai/gpt-5.6-luna"},
        "candidates": [
            "openai/gpt-4.1-mini",                          # bare string form
            {"model": "openai/gpt-5.6-luna", "reasoning_effort": "low"},  # dict form
        ],
    }
    cfg = SweepConfig.from_dict(raw)
    assert cfg.role == "composer" and cfg.limit == 3
    assert [c.model for c in cfg.candidates] == ["openai/gpt-4.1-mini", "openai/gpt-5.6-luna"]
    assert cfg.candidates[1].reasoning_effort == "low"
    # every candidate is bound to the swept role
    assert all(c.role == "composer" for c in cfg.candidates)
    assert cfg.judge().model == "gpt-4o"
    assert cfg.baseline_config().overrides["coordinator"].model == "openai/gpt-5.6-luna"


def test_sweep_config_select_goldens_limit():
    from evals.harness.sweep_config import SweepConfig

    cfg = SweepConfig.from_dict({"role": "composer", "dataset": "labeled",
                                 "limit": 2, "candidates": ["openai/x"]})
    assert len(cfg.select_goldens()) == 2

    cfg2 = SweepConfig.from_dict({"role": "composer", "dataset": "labeled",
                                  "only_ids": ["sh_weather_now"], "candidates": ["openai/x"]})
    picked = cfg2.select_goldens()
    assert len(picked) == 1 and picked[0].id == "sh_weather_now"


def test_sweep_config_conversations_and_goldens_optional():
    from evals.harness.sweep_config import SweepConfig

    # goldens + conversations together
    cfg = SweepConfig.from_dict({"role": "composer", "dataset": "labeled",
                                 "conversations": "conversations", "candidates": ["openai/x"]})
    assert cfg.dataset == "labeled" and cfg.conversations == "conversations"

    # conversations only: dataset defaults to None (no goldens) when omitted
    conv_only = SweepConfig.from_dict({"role": "composer", "conversations": "conversations",
                                       "candidates": ["openai/x"]})
    assert conv_only.dataset is None and conv_only.select_goldens() == []
    assert len(conv_only.select_conversations()) >= 1

    # neither given -> back-compat default to labeled goldens
    default = SweepConfig.from_dict({"role": "composer", "candidates": ["openai/x"]})
    assert default.dataset == "labeled"


def test_sweep_config_simulations_and_candidate_config():
    from evals.harness.sweep_config import SweepConfig

    cfg = SweepConfig.from_dict({
        "role": "composer",
        "simulations": "simulations",
        "simulator_model": "openrouter/deepseek/deepseek-v4-flash-0731",
        "max_turns": 4,
        "candidates": ["openai/gpt-4.1-mini"],
    })
    assert cfg.max_turns == 4 and cfg.simulator_model.startswith("openrouter/")
    assert len(cfg.select_simulations()) >= 1
    # candidate_config injects the candidate as the swept role's override
    label, ec = cfg.candidate_config(cfg.candidates[0])
    assert label == "composer=openai/gpt-4.1-mini"
    assert ec.overrides["composer"].model == "openai/gpt-4.1-mini"


def test_judge_bare_model_is_native_string():
    from evals.harness.config import JudgeConfig

    # a bare OpenAI id stays a plain string (deepeval's native path, no adapter)
    assert JudgeConfig(model="gpt-4o").deepeval_model() == "gpt-4o"


def test_unwrap_simulated_input():
    from evals.harness.simulator import _unwrap_simulated_input

    # JSON-wrapped opening turn -> plain text
    assert _unwrap_simulated_input('{"simulated_input": "Jaka pogoda?"}') == "Jaka pogoda?"
    # already-plain text is untouched
    assert _unwrap_simulated_input("Jaka pogoda?") == "Jaka pogoda?"
    # malformed json -> returned as-is, never crashes
    assert _unwrap_simulated_input('{simulated_input oops') == '{simulated_input oops'


def test_usage_and_tool_aggregation():
    cap = UsageCapture()
    cap.calls = [
        LlmCall("coordinator", "m", "p", 100, 10),
        LlmCall("coordinator", "m", "p", 50, 5),
        LlmCall("weather_agent", "m2", "p", 200, 20),
    ]
    cap.tools = [
        ToolInvocation("coordinator", "weather_agent"),
        ToolInvocation("weather_agent", "current_weather"),
        ToolInvocation("weather_agent", "weather_forecast"),
    ]
    by_role = cap.by_role()
    assert by_role["coordinator"] == {"prompt_tokens": 150, "completion_tokens": 15, "calls": 2}
    assert cap.tools_by_caller()["weather_agent"] == ["current_weather", "weather_forecast"]
