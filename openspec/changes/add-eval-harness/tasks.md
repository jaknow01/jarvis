## 1. Spike: verify deepeval ↔ LlamaIndex instrumentation (sierpień 2026)

- [x] 1.1 W izolowanym venv zainstalować `deepeval`; potwierdzić dostępność
      `instrument_llama_index(get_dispatcher())` oraz nazw `AgentSpanContext`/`LlmSpanContext`
      (zajrzeć do kodu pakietu, nie tylko docs).
- [x] 1.2 Minimalny skrypt: zainstrumentować dispatcher, wykonać jeden `handle_message` przez
      koordynatora, wypisać drzewo spanów; potwierdzić rozróżnialne spany coordinator vs
      poszczególne subagenty.
- [x] 1.3 Potwierdzić, że spany LLM niosą usage (input/output tokens) dla ≥1 providera; jeśli
      brak — zanotować potrzebę fallbacku (`TokenCountingHandler` keyed po aktywnym agencie).

## 2. Izolowane środowisko modułu `evals/`

- [x] 2.1 Utworzyć katalog `evals/` z `pyproject.toml`: zależność `deepeval` +
      `jarvis = { path = "..", develop = true }`; własny lockfile/venv.
- [x] 2.2 Zweryfikować izolację: instalacja/upgrade `deepeval` zmienia tylko evalowy lockfile,
      produkcyjny `poetry.lock` bez zmian.
- [x] 2.3 Sanity-import: z evalowego venv zaimportować `from lib.runtime.engine import
      handle_message` i wykonać pusty przebieg (bez metryk).
- [x] 2.4 `evals/README.md`: jak zainstalować venv, ustawić klucze providerów + `EVAL_JUDGE_MODEL`,
      i uruchomić run/sweep.

## 3. Instrumentacja i driver (uruchamianie realnych agentów)

- [x] 3.1 `evals/harness/instrument.py`: włącz/wyłącz `instrument_llama_index(get_dispatcher())`
      jako kontekst używany tylko na czas evala (zero importów deepeval w `lib/`/`app/`).
- [x] 3.2 `evals/harness/driver.py`: funkcja stosująca konfigurację przez nadpisania env
      (`AGENT_<NAME>_MODEL` / `AGENT_<NAME>_REASONING_EFFORT`) z twardym save/restore (lub
      subproces per grid-point), następnie wywołująca `engine.handle_message` na wejściu goldena.
- [x] 3.3 Driver zwraca finalną odpowiedź + przechwycony trace/spany, gotowe pod metryki i koszt.
- [x] 3.4 Test izolacji: nadpisanie modelu jednej roli nie wycieka do kolejnego runu
      (Scenario „override does not leak”).

## 4. Dataset: Synthesizer + persona właściciela

- [x] 4.1 `evals/datasets/`: seed kontekstu + generacja goldenów przez `Synthesizer`; utrwalić
      wygenerowane goldeny na dysku (reprodukowalność).
- [~] 4.2 Zdefiniować personę właściciela (Warszawa, PL) i scenariusze pod metryki
      task-completion / wielotorowe (`ConversationSimulator` gdzie potrzeba).
      (Persona + SCENARIO zdefiniowane i użyte w Synthesizerze; `ConversationSimulator`
      wielotorowy NIE zaimplementowany — obecne metryki są jednoturowe/reference-free.)
- [x] 4.3 Loader datasetu: ten sam zestaw goldenów ładowany bez regeneracji.

## 5. Metryki reference-free per rola/subagent

- [~] 5.1 `evals/harness/metrics.py`: skonfigurować `TaskCompletion`, `ArgumentCorrectness`,
      `ToolUse`, oraz `GEval` po polsku na composerze (trafność / wierność do zebranych danych /
      brak halucynacji / ton).
      (Zaimplementowane i zwalidowane: `GEval` PL na composerze z CONTEXT = zebrane dane, oraz
      `ToolCorrectness`. `TaskCompletion`/`ArgumentCorrectness`/`ToolUse` — DO DOŁOŻENIA:
      TaskCompletion/ToolUse są trace-based i wymagają flow ewaluacyjnego deepeval; patrz
      docstring metrics.py.)
- [~] 5.2 Podpiąć metryki do właściwych spanów przez `AgentSpanContext`/`LlmSpanContext`
      (osobno coordinator = routing, subagenci = execution, composer = kompozycja).
      (Rozbicie na 3 poziomy zrealizowane INACZEJ niż przez span-context deepeval: z własnego
      przechwytywania — routing = tool-calls koordynatora, execution = tool-calls subagenta,
      kompozycja = GEval na composerze. Span-context deepeval nie użyty, bo trace nie jest
      retencjonowany offline; patrz Spike findings w design.md.)
- [x] 5.3 Opcjonalna warstwa `ToolCorrectness`: liczona tylko dla goldenów z `expected_tools`;
      jej brak nie wywala runu.
- [x] 5.4 Konfigurowalne progi pass/fail per metryka; agregacja per rola (średnia + rozrzut).

## 6. Rozliczanie tokenów i kosztu (USD)

- [x] 6.1 `evals/price_table.yaml`: tabela `provider/model → (cost_in, cost_out)` w USD.
- [x] 6.2 `evals/harness/cost.py`: atrybucja tokenów in/out per agent ze spanów (provider-agnostic;
      fallback `TokenCountingHandler` keyed po aktywnym agencie jeśli span nie niesie usage).
- [x] 6.3 Przeliczenie na koszt per rola/turn; model spoza tabeli raportowany jako `unpriced`
      (nie zero).
- [~] 6.4 Koszt modelu-sędziego liczony i raportowany **osobno** od kosztu agentów.
      (Strukturalnie WYKLUCZONY z kosztu agentów: sędzia działa przez własnego klienta deepeval,
      poza przechwytywaniem per-agent, więc jego tokeny nie wchodzą do kosztu agentów — raport
      to zaznacza. Osobny LICZBOWY line-item kosztu sędziego jeszcze nie sumowany — do dołożenia.)

## 7. Silnik sweepu (jakość × koszt per rola)

- [x] 7.1 `evals/harness/sweep.py`: siatka `{model, reasoning}` dla wybranej roli; reszta ról na
      stałym baseline; jeden stały `EVAL_JUDGE_MODEL` w całym sweepie.
- [x] 7.2 Wykonać grid nad utrwalonym datasetem; zebrać metryki + koszt per grid-point.
- [x] 7.3 Agregacja do tabeli porównawczej per rola (jeden wiersz na kandydata: metryki + koszt).

## 8. Raportowanie i weryfikacja end-to-end

- [x] 8.1 `evals/harness/report.py`: zapis wyników jako JSON (surowe) + markdown (tabela
      jakość × koszt per rola/model) pod `evals/`; działa bez chmury i bez Postgresa.
- [x] 8.2 Uruchomić pełny przykładowy sweep dla jednej roli (np. `composer`) i zweryfikować
      poprawność tabeli oraz rozdzielności kosztu sędziego.
- [x] 8.3 Krótka sekcja w `evals/README.md` z interpretacją raportu i wyborem optymalnej
      konfiguracji per rola.
