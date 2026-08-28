## Why

Jarvis działa, ale każda z ~9 ról (coordinator, composer, 7 subagentów) ma własny,
provider-agnostyczny model + reasoning, dobierany dziś "na czuja" przez zmienne
`AGENT_<NAME>_MODEL`. Nie ma sposobu, by **obiektywnie** zmierzyć, czy dana rola dobrze
routuje/woła narzędzia, dobrze przetwarza i dobrze odpowiada — ani ile to kosztuje w
tokenach. Bez tego optymalizacja jakość × koszt jest zgadywaniem. Potrzebny jest
niezależny harness ewaluacyjny (deepeval), który to zmierzy i pozwoli porównać
kandydatów per rola.

## What Changes

- Nowy, **niezależny moduł `evals/`** z **własnym pyproject + venv**, w którym `jarvis`
  jest deklarowany jako **path-dependency** (`{ path = "..", develop = true }`) — ciężki
  `deepeval` nie dotyka produkcyjnego `poetry.lock`, a harness nadal importuje `lib/` i
  uruchamia realnych agentów.
- **Nieinwazyjna instrumentacja**: harness włącza `instrument_llama_index(get_dispatcher())`
  (deepeval) **tylko na czas evala** — component-level spany per subagent, **bez** dodawania
  `@observe` do kodu produkcyjnego (`lib/` pozostaje nietknięte).
- **Metryki agenta i subagentów** (reference-free, bo dataset syntetyczny): `TaskCompletion`
  (trajektoria), `ArgumentCorrectness` (argumenty narzędzi), `GEval` po polsku
  (trafność / wierność do zebranych danych / brak halucynacji / ton) na composerze, oraz
  `ToolUse`. `ToolCorrectness` (wymaga `expected_tools`) — opcjonalna, dla ewentualnego
  ręcznie oetykietowanego podzbioru.
- **Rozliczanie kosztów per-agent**: tokeny in/out czytane ze spanów per aktywny agent ×
  tabela cen (`price_table.yaml`, per `provider/model`) → koszt w **USD** na turn i na
  rolę. Koszt modelu-sędziego raportowany **osobno** od kosztu agentów.
- **Silnik sweepu**: dla wybranej roli zamiata siatkę `{model, reasoning}` (nadpisując
  `AGENT_<ROLA>_MODEL` / `AGENT_<ROLA>_REASONING_EFFORT` w env, resztę trzymając fixed) i
  produkuje tabelę **jakość × koszt** do wyboru optymalnej konfiguracji.
- **Dane testowe**: `Synthesizer` (deepeval) generuje goldeny; persona właściciela
  (Warszawa, PL) + scenariusze zasilają metryki wielotorowe / task completion.
- **Raporty**: pliki w `evals/` — surowy `JSON` + czytelny `markdown` (tabela jakość × koszt
  per rola/model). Bez zależności od chmury i bez produkcyjnego Postgresa.
- **Sędzia**: jeden mocny, **stały** model-sędzia dla wszystkich metryk w całym sweepie
  (sprawiedliwe porównania).

Brak zmian w zachowaniu produkcyjnym: harness jest osobnym modułem, uruchamianym ręcznie;
`lib/` i `app/` nie zyskują nowych zależności ani nowych ścieżek wykonania.

## Capabilities

### New Capabilities
- `eval-harness`: niezależny moduł ewaluacyjny — izolacja środowiska (osobny venv +
  path-dependency), nieinwazyjna instrumentacja LlamaIndex/deepeval, driver uruchamiający
  realnych agentów na goldenach, zestaw metryk reference-free per rola/subagent, oraz
  raportowanie wyników do plików JSON/MD.
- `eval-cost-accounting`: przypisanie zużycia tokenów in/out do konkretnego agenta na
  podstawie spanów, przeliczenie na koszt (USD) wg konfigurowalnej tabeli cen
  `provider/model`, z kosztem sędziego liczonym rozdzielnie.
- `model-sweep`: silnik porównawczy, który dla danej roli zamiata siatkę modeli i poziomów
  reasoning (przez nadpisania env, resztę trzymając stałą) i zestawia wyniki jakość × koszt
  w jednej tabeli, aby wybrać optymalną konfigurację per rola.

### Modified Capabilities
<!-- Brak. Change jest czysto addytywny; istniejący spec `model-providers` (mechanizm
     AGENT_<NAME>_MODEL / reasoning) jest tylko WYKORZYSTYWANY przez sweep, nie zmieniany. -->

## Impact

- **Nowy kod**: katalog `evals/` (własny `pyproject.toml` + lockfile, `harness/`,
  `datasets/`, `price_table.yaml`, `README`). Poza `evals/` — zero zmian w `lib/`, `app/`.
- **Zależności**: `deepeval` (+ jego tranzytywne) izolowane w evalowym venv; produkcyjny
  `poetry.lock` bez zmian. Path-dependency ściąga prod-deps tranzytywnie do venv evala.
- **Klucze/API i koszt**: harness woła realne API modeli (agentów) oraz model-sędziego —
  zużywa tokeny/pieniądze przy uruchomieniu; wymaga kluczy providerów + sędziego w env.
  Uruchamiany ręcznie, nie w CI produkcyjnym (na tym etapie).
- **Wykorzystywane (bez modyfikacji)**: istniejący mechanizm `LLM_BY_AGENT` /
  `AGENT_<NAME>_MODEL` / `lib/core/reasoning.py` (sweep nadpisuje env), `engine.handle_message`
  (świeży build koordynatora co turn), oraz dispatcher instrumentacji LlamaIndex.
- **Poza zakresem (na razie)**: integracja z Confident AI (chmura), zapis wyników do
  Postgresa, przeliczanie kosztu na PLN, ręcznie etykietowany dataset `expected_tools`,
  uruchamianie w CI. Wszystkie do rozważenia w kolejnych iteracjach.
