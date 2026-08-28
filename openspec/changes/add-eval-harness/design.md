## Context

Jarvis to sieć agentów na LlamaIndex: `engine.handle_message` buduje świeżego koordynatora
co turn, koordynator woła subagentów **jako narzędzia** (`agent_as_tool`), a `composer`
deterministycznie pisze finalną odpowiedź po polsku. Każda rola ma własny model+reasoning
przez provider-agnostyczny rejestr `LLM_BY_AGENT`, konfigurowany zmiennymi
`AGENT_<NAME>_MODEL` / `AGENT_<NAME>_REASONING_EFFORT` (rozwiązywanymi w `lib/core/llm.py`,
z reasoning normalizowanym per provider w `lib/core/reasoning.py`). Istnieje już
obserwowalność przez MLflow (`instrument`-podobny `mlflow.llama_index.autolog()` w
`lib/runtime/tracing.py`), co dowodzi, że dispatcher LlamaIndex daje się nieinwazyjnie
instrumentować.

Brakuje warstwy pomiaru: nie wiemy obiektywnie, która rola dobrze routuje/woła narzędzia,
dobrze przetwarza i dobrze odpowiada, ani ile tokenów/pieniędzy to kosztuje. Ten dokument
opisuje **jak** zbudować niezależny harness ewaluacyjny na `deepeval`, który to mierzy i
umożliwia sweep modeli per rola. Motywacja i zakres — patrz `proposal.md`; wymagania —
`specs/`.

Architektura wprowadza trzy poziomy oceny, wynikające wprost z topologii aplikacji:

```
POZIOM 1 (routing)   coordinator → czy wybrał właściwych subagentów (subagent = narzędzie)
POZIOM 2 (execution) subagent    → czy wybrał właściwe realne narzędzia + argumenty
POZIOM 3 (kompozycja) composer   → czy finalna odpowiedź PL jest trafna/wierna/bez halucynacji
```

## Goals / Non-Goals

**Goals:**
- Niezależny moduł `evals/` z własnym venv; `deepeval` nie rusza produkcyjnego lockfile.
- Nieinwazyjny pomiar: zero zmian w `lib/`/`app/` (żadnych `@observe` w prod).
- Metryki jakości per rola/subagent (reference-free) + atrybucja tokenów i koszt (USD).
- Silnik sweepu: dla jednej roli zamiata siatkę `{model, reasoning}` i zestawia jakość×koszt.
- Reprodukowalne runy nad utrwalonym datasetem; raporty jako pliki JSON+MD.

**Non-Goals:**
- Integracja z Confident AI (chmura), zapis do Postgresa, przeliczanie na PLN — poza zakresem.
- Ręcznie etykietowany dataset `expected_tools` na starcie (ToolCorrectness opcjonalna).
- Uruchamianie w CI produkcyjnym; automatyczne bramkowanie merge’y.
- Zmiana zachowania produkcyjnego czy refaktor agentów pod testowalność.

## Decisions

### D1: Osobny venv + `jarvis` jako path-dependency (izolacja bez duplikacji)
`evals/pyproject.toml` deklaruje `deepeval` oraz `jarvis = { path = "..", develop = true }`.
Harness musi uruchamiać **realnych** agentów, więc jego venv i tak potrzebuje wszystkich
zależności produkcyjnych — path-dependency ściąga je tranzytywnie, a ciężki `deepeval`
siedzi tylko w evalowym lockfile.
- *Alternatywy:* (a) grupa Poetry `eval` w tym samym pyproject — współdzieli bolesny prod
  lockfile, ryzyko churnu (odrzucone: sprzeczne z życzeniem izolacji); (b) czysty
  `requirements.txt` — brak zarządzania wersjami spójnego z resztą (odrzucone jako mniej
  trwałe). Path-dependency godzi izolację z koniecznością importu `lib/`.

### D2: Nieinwazyjna instrumentacja przez dispatcher (`instrument_llama_index`)
Harness wywołuje `instrument_llama_index(get_dispatcher())` **tylko** w procesie evala,
przed uruchomieniem turnu. deepeval rejestruje `BaseEventHandler`/`BaseSpanHandler` na
dispatcherze LlamaIndex i produkuje component-level spany (agent/tool/llm) — dokładnie jak
istniejący MLflow autolog. `AgentSpanContext`/`LlmSpanContext` pozwalają podpiąć metryki do
konkretnego spanu subagenta.
- *Alternatywy:* `@observe` w kodzie produkcyjnym (kanoniczny component-level, ale wkłada
  zależność evalową i dekoratory do `lib/` — odrzucone, łamie izolację); czytanie z trace’u
  MLflow po turnie (wiąże z działającym MLflow i jego raportowaniem usage — odrzucone jako
  zależność zewnętrzna). Dispatcher daje spany bez dotykania produkcji.

### D3: Sweep przez nadpisania env, nie przez nowy kod
Driver ustawia `AGENT_<ROLA>_MODEL` / `AGENT_<ROLA>_REASONING_EFFORT` w środowisku, po czym
woła `engine.handle_message`. Ponieważ `engine` buduje świeżego koordynatora co turn, a
`LLM_BY_AGENT` czyta env przy budowie, zmiana modelu **nie wymaga ani linijki w prod**.
Izolacja runów: zapis/restore zmiennych wokół każdego grid-pointu (lub subproces per punkt,
by uniknąć wycieku stanu contextvar/procesu).
- *Alternatywy:* wstrzykiwanie LLM przez `@llm_usage` z kodu harnessa (działa, ale duplikuje
  logikę resolucji — env jest natywnym, już przetestowanym kanałem konfiguracji).

### D4: Metryki reference-free (dopasowane do datasetu syntetycznego)
Bazowy zestaw bez ground-truth: `TaskCompletion` (alignment Task↔Outcome z trace), 
`ArgumentCorrectness` (argumenty narzędzi, LLM-judge bez referencji), `ToolUse` (selekcja
narzędzi), oraz `GEval` po polsku na composerze (trafność / wierność do zebranych danych /
brak halucynacji / ton). `ToolCorrectness` (wymaga `expected_tools`) — opcjonalna warstwa
dla ewentualnego oetykietowanego podzbioru.
- *Uzasadnienie:* dataset pochodzi z `Synthesizer` (bez etykiet narzędzi), więc metryki nie
  mogą zależeć od `expected_*`. Progi pass/fail konfigurowalne per metryka.

### D5: Jeden stały, mocny sędzia; koszt sędziego liczony osobno
Wszystkie metryki LLM-judge używają jednego modelu-sędziego (`EVAL_JUDGE_MODEL`), stałego
w całym sweepie → porównania między kandydatami są sprawiedliwe (ta sama linijka). Tokeny
sędziego rozliczane rozdzielnie od kosztu agentów, by nie zaniečyszczać porównania kosztu.

### D6: Koszt w USD z konfigurowalnej tabeli `price_table.yaml`
`cost.py` mapuje tokeny per-agent (ze spanów) przez tabelę `provider/model → (cost_in,
cost_out)` na koszt w USD. Model spoza tabeli jest raportowany jako **unpriced** (nie zero).
- *Alternatywy:* auto-koszt deepeval — działa tylko dla OpenAI/Anthropic/Gemini, zostawiałby
  luki dla OpenRouter/groq/xai (odrzucone jako niespójne). PLN pominięty świadomie (łatwo
  dołożyć przelicznik później).

### D7: Raporty jako pliki JSON + Markdown w `evals/`
Surowy JSON (pełne wyniki per golden/rola/metryka/koszt) + czytelna tabela markdown
(jakość × koszt per rola/model). Bez chmury i bez produkcyjnego Postgresa — spójne z
izolacją modułu i trzymane w repo.

## Risks / Trade-offs

- **API deepeval dla LlamaIndex mogło się zmienić (sierpień 2026)** → wczesny spike:
  minimalny `instrument_llama_index(get_dispatcher())` + jeden turn + odczyt spanów, zanim
  zbudujemy resztę. Nazwy (`AgentSpanContext`/`LlmSpanContext`) zweryfikować w kodzie pakietu.
- **Atrybucja tokenów per-agent może być niepełna** (np. spany bez usage dla części
  providerów OpenAI-compat) → mitygacja: fallback na własny callback `TokenCountingHandler`
  keyed po contextvarze aktywnego agenta; raportować „unknown usage” zamiast zera.
- **Wyciek env między grid-pointami** (contextvar/proces) → mitygacja: subproces per punkt
  lub twardy save/restore env; testy izolacji (Scenario „override does not leak”).
- **Koszt i niedeterminizm** — harness woła realne API (agentów i sędziego), zużywa
  pieniądze i daje zmienne wyniki → mitygacja: mały, utrwalony dataset; uśrednianie po
  goldenach; uruchamianie ręczne; jasne oznaczenie kosztu runu.
- **Metryki reference-free bywają hałaśliwe** (LLM-judge) → mitygacja: stały sędzia, jawne
  kryteria GEval po polsku, raportowanie rozrzutu, a nie tylko średniej.
- **Persony/scenariusze mogą nie oddawać realnego użycia właściciela** → mitygacja: persona
  osadzona w kontekście (Warszawa, PL, PLN); iteracyjne dostrajanie scenariuszy.

## Migration Plan

Zmiana jest addytywna i odseparowana — brak migracji danych ani ścieżek produkcyjnych.
Wdrożenie: (1) utworzyć `evals/` z własnym pyproject/venv i path-dep na `jarvis`;
(2) spike instrumentacji; (3) driver + metryki + cost + report; (4) sweep. Rollback =
usunięcie katalogu `evals/` (produkcja nietknięta).

## Spike findings & design refinements (implementation)

The spike (task group 1) validated the approach and forced three concrete refinements,
now reflected in the code:

- **Cost capture is our own, not deepeval's.** deepeval flushes and clears its traces after
  each run when there is no Confident AI key, so ``trace_manager.get_all_traces()`` is empty
  offline. Token/cost attribution therefore rides on our own LlamaIndex event + span handlers
  (``harness/instrument.py``) — the fallback anticipated in Risks became the *primary*
  mechanism. deepeval's ``instrument_llama_index`` is still enabled for the metrics path.
- **Attribution key = ``FunctionAgent`` ``name``.** The app names every agent
  (coordinator/composer/weather_agent/…), so each ``FunctionAgent.run`` span carries the role
  via ``instance.name``. Each ``LLMChatEndEvent`` (which has ``span_id``) is walked up the
  parent map to the nearest named agent; a sub-agent nested in the coordinator's tool call
  resolves to the sub-agent. Tool calls are captured the same way (``FunctionTool`` span →
  nearest agent), giving the two-level tool picture (routing vs execution) directly.
- **Provider-agnostic token shapes differ.** OpenAI Responses reports usage as a nested
  ``ResponseUsage`` (``input_tokens``/``output_tokens``) while OpenAI-compatible/OpenRouter
  reports flat ``prompt_tokens``/``completion_tokens``; ``_extract_tokens`` handles both (and
  Anthropic-style nesting). Prices are matched even when the runtime drops the provider prefix
  (OpenAILike reports ``deepseek/…`` for the app's ``openrouter/deepseek/…``).
- **Metrics use async ``a_measure``.** deepeval's sync ``measure`` uses ``nest_asyncio``,
  which breaks under Python 3.14's asyncio; scoring awaits ``a_measure`` inside the harness
  loop instead. (The eval venv defaults to Python 3.12 in the bootstrap for the same reason.)
- **Faithfulness needs the gathered data.** The composer ``GEval`` receives the coordinator's
  gathered output as ``CONTEXT``, so faithfulness is judged against what the sub-agents
  actually returned (without it, the judge wrongly penalises every specific number).

## Open Questions

- Konkretny zestaw kandydatów w siatce per rola (które modele/providerzy na start).
- Progi pass/fail per metryka i sposób agregacji (średnia vs mediana + rozrzut).
- Czy grid-pointy izolować subprocesem (pewniejsze) czy save/restore env (szybsze).
- Jak rozbić koszt sędziego pomiędzy role (całościowo per run vs proporcjonalnie).
- Wybór modelu-sędziego (mocny, ale rozsądny kosztowo) i jego stałość między iteracjami.
