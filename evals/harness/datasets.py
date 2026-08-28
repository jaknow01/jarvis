"""Evaluation goldens: an owner-persona seed plus a deepeval ``Synthesizer`` wrapper.

A golden here is one user turn to run through the network. Metrics are reference-free, so a
golden needs only an ``input``; optional ``expected_routing`` / ``expected_execution`` labels
enable the (optional) ToolCorrectness metrics for a hand-labelled subset.

Two sources (per the proposal): a curated seed (reliable base, in Polish, tailored to the
owner in Warsaw) and ``generate_goldens`` — a thin wrapper over deepeval's ``Synthesizer``
that expands coverage by generating fresh inputs styled to the same persona. Generated and
seed goldens are persisted under ``evals/datasets/`` so runs are reproducible.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

_DATASETS_DIR = Path(__file__).resolve().parent.parent / "datasets"

# The single owner this assistant serves (see CLAUDE.md): Warsaw, Polish, PLN base currency.
PERSONA = (
    "Właściciel asystenta Jarvis: jedna osoba mieszkająca w Warszawie, mówiąca po polsku, "
    "bazowa waluta PLN. Pyta krótko i naturalnie, jak do osobistego asystenta w telefonie."
)
SCENARIO = (
    "Codzienne, jednozdaniowe prośby do osobistego asystenta: pogoda, kursy walut i giełda, "
    "trasy i nawigacja, newsy, sterowanie światłem w domu, Fantasy Premier League, zapamiętanie "
    "preferencji oraz przypomnienia/harmonogram."
)


@dataclass
class EvalGolden:
    """One evaluation input, optionally labelled for tool-correctness."""

    id: str
    input: str
    # OPTIONAL ground truth for the optional ToolCorrectness metrics:
    expected_routing: list[str] | None = None            # sub-agents the coordinator should call
    expected_execution: dict[str, list[str]] | None = None  # {subagent: [expected real tools]}
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "EvalGolden":
        return cls(
            id=d["id"],
            input=d["input"],
            expected_routing=d.get("expected_routing"),
            expected_execution=d.get("expected_execution"),
            metadata=d.get("metadata", {}),
        )


# --------------------------------------------------------------------------- seed
# A small curated base. Most are reference-free (no labels); a couple carry expected_routing
# so the optional routing metric is exercised. Keep inputs short and Polish, like the owner.
SEED_GOLDENS: list[EvalGolden] = [
    EvalGolden("seed_greeting", "Cześć! Krótko przywitaj się ze mną."),
    EvalGolden(
        "seed_weather_waw",
        "Jaka jest teraz pogoda w Warszawie?",
        expected_routing=["weather_agent"],
    ),
    EvalGolden(
        "seed_fx_eurpln",
        "Ile teraz kosztuje euro w złotówkach?",
        expected_routing=["finance_agent"],
    ),
    EvalGolden(
        "seed_route_home_work",
        "Ile zajmie mi teraz dojazd z domu do pracy?",
        expected_routing=["maps_agent"],
    ),
    EvalGolden(
        "seed_news_today",
        "Co ważnego dzieje się dziś na świecie? Streść w kilku zdaniach.",
        expected_routing=["news_agent"],
    ),
    EvalGolden(
        "seed_memory_pref",
        "Zapamiętaj, że rano piję kawę bez cukru.",
        expected_routing=["memory_operator"],
    ),
    EvalGolden(
        "seed_multidomain",
        "Jaka jest pogoda w Warszawie i ile kosztuje dolar?",
        expected_routing=["weather_agent", "finance_agent"],
    ),
]


# ------------------------------------------------------------------- persistence
def save_goldens(goldens: list[EvalGolden], name: str) -> Path:
    """Persist goldens as ``evals/datasets/<name>.json`` and return the path."""
    _DATASETS_DIR.mkdir(parents=True, exist_ok=True)
    path = _DATASETS_DIR / f"{name}.json"
    path.write_text(json.dumps([g.to_dict() for g in goldens], ensure_ascii=False, indent=2))
    return path


def load_goldens(name: str) -> list[EvalGolden]:
    """Load goldens from ``evals/datasets/<name>.json`` (raises if missing)."""
    path = _DATASETS_DIR / f"{name}.json"
    data = json.loads(path.read_text())
    return [EvalGolden.from_dict(d) for d in data]


def ensure_seed(name: str = "seed") -> list[EvalGolden]:
    """Return the seed goldens, writing them to disk on first use for reproducibility."""
    path = _DATASETS_DIR / f"{name}.json"
    if path.exists():
        return load_goldens(name)
    save_goldens(SEED_GOLDENS, name)
    return SEED_GOLDENS


# --------------------------------------------------------------- synthetic (deepeval)
async def generate_goldens(
    num_goldens: int,
    model: str,
    name: str | None = None,
) -> list[EvalGolden]:
    """Generate fresh, persona-styled inputs via deepeval's ``Synthesizer`` (async).

    Produces reference-free goldens (input only; no tool labels — label a subset by hand if
    you want the optional ToolCorrectness metrics). Optionally persists them under
    ``evals/datasets/<name>.json``.
    """
    from deepeval.synthesizer import Synthesizer
    from deepeval.synthesizer.config import StylingConfig

    from evals.harness.deepeval_model import deepeval_model_for

    styling = StylingConfig(
        scenario=SCENARIO,
        task="Odpowiadanie na codzienne prośby właściciela jako osobisty asystent Jarvis.",
        input_format="Krótka, naturalna wiadomość po polsku od właściciela (1 zdanie).",
        expected_output_format="Zwięzła odpowiedź asystenta po polsku.",
    )
    # `model` may be a bare OpenAI id or a provider/model string (e.g. an OpenRouter deepseek);
    # the adapter routes provider-prefixed models through the app's own LLM builders.
    synth = Synthesizer(model=deepeval_model_for(model), styling_config=styling)
    raw = await synth.a_generate_goldens_from_scratch(num_goldens=num_goldens)

    goldens = [
        EvalGolden(id=f"synth_{i:03d}", input=g.input, metadata={"source": "synthesizer"})
        for i, g in enumerate(raw)
    ]
    if name:
        save_goldens(goldens, name)
    return goldens
