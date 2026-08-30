## Why

W `watch-execution` narzędzie `create_watch` zapisuje `check_code` **verbatim** — zakłada,
że ktoś poda gotowy, poprawny predykat. W praktyce kod pisze **LLM**, a Starlark jest
okrojonym Pythonem: model odruchowo sięga po `try/except`, f-stringi, `import`, których
Starlark nie ma, więc kod potrafi się nie skompilować. Potrzebny jest **pipeline
authoringu** (jednorazowy, przy tworzeniu watcha), który niezawodnie zamienia intencję w
**działający, zwalidowany** predykat — i który łapie nie tylko błędy składni, ale i
semantyki (predykat, który się kompiluje, ale czyta złe pole / zawsze zwraca `false`).

## What Changes

- **Generator** (istniejący `scheduler_agent`): pisze predykat w Starlarku wg konwencji
  `def check(data) -> bool`, z promptem zawierającym **zakazy** (czego w Starlarku nie ma),
  dostępne **helpery** (`get(data, "a.b", default)`, ew. czasowe) i few-shot.
- **Normalizer** (deterministyczny, bez LLM): mechanicznie naprawia **najczęstsze Python-izmy**,
  na których model się wywala (np. f-string → `.format()`), zanim cokolwiek pójdzie do walidacji.
  Świadomie minimalny.
- **Walidacja przez sandbox**: `validate(code)` leci do `predicate-sandbox` (change
  `watch-sandbox`) — **repair-LLM nie ma własnego Starlarka**, walidacja zawsze jest „prawdziwa".
- **Repair-LLM** (osobny, „kodowy" model przez `LLM_BY_AGENT["watch_coder"]`): dostaje zepsuty kod
  + **treść błędu z sandboxa** + krótki opis intencji + reguły Starlarka, i naprawia „na ślepo".
  Proces **iteracyjny**: jeśli poprawka też nie przejdzie, wraca do repair-LLM — z **twardym capem**
  (max N rund) i **fail-closed** (nie uzbrajaj watcha, poproś usera o doprecyzowanie).
- **Smoke-test** (semantyka, nie tylko składnia): pobierz raz przez rejestr źródeł
  (`watch-execution`), wykonaj `evaluate(code, sample)` w sandboxie, **pokaż użytkownikowi**, co
  predykat teraz zwraca (i na jakich danych) → **potwierdzenie** przed uzbrojeniem.
- **`create_watch` zmienia zachowanie**: zamiast „zapisz verbatim" wykonuje pełny pipeline
  (generate → normalize → validate → repair(cap) → smoke-test → confirm) i **dopiero potem** zapisuje.
  To zastępuje verbatim-owe zachowanie z `watch-execution`.

Cały pipeline jest **authoring-time** (jednorazowy, przy tworzeniu) — runtime (polling) pozostaje
bez zmian i bez kosztu naprawy.

## Capabilities

### New Capabilities
- `watch-authoring`: pipeline tworzenia predykatu — generator (Starlark wg konwencji + prompt z
  zakazami/helperami), deterministyczny normalizer najczęstszych Python-izmów, walidacja przez
  `predicate-sandbox`, iteracyjny repair-LLM (osobny „kodowy" model, treść błędu jako paliwo,
  cap + fail-closed), oraz smoke-test na realnej próbce danych z potwierdzeniem użytkownika przed
  uzbrojeniem watcha.

### Modified Capabilities
<!-- Formalna delta odłożona: `conditional-watches` (z `watch-execution`) nie jest jeszcze w
     openspec/specs/ (change nie zarchiwizowany), więc nie modyfikujemy go tu delta-spekiem.
     Zmiana zachowania `create_watch` (verbatim → authored) jest opisana jako wymaganie w
     `watch-authoring` i zostanie pogodzona ze specem `conditional-watches` przy archiwizacji. -->

## Impact

- **Kod (publiczne repo)**:
  - Nowy moduł pipeline'u authoringu (generator-orchestration, normalizer, pętla walidacja↔repair,
    smoke-test).
  - `lib/agents/scheduler_agent/tools.py` — `create_watch` routuje przez pipeline zamiast zapisu verbatim.
  - Prompt/reguły Starlarka + few-shot dla generatora; instrukcje dla repair-LLM.
  - **Konfiguracja modelu** `watch_coder` przez istniejący `LLM_BY_AGENT` / `AGENT_WATCH_CODER_MODEL`
    (zero nowej instalacji — jak `history_compressor`).
- **Zależności czasowe**: wymaga **żywego `predicate-sandbox`** przy tworzeniu (validate + smoke-test
  lecą do sandboxa) → `watch-authoring` wdraża się **po** `watch-sandbox`. Używa rejestru źródeł i
  kontraktu z `watch-execution`.
- **Koszt LLM**: tylko authoring-time (generator raz + repair 0..N razy, N zależne od capu); runtime
  bez zmian.
- **Testy**: normalizer (mechaniczne transformacje), pętla repair z fake-walidatorem (zbieżność w N,
  fail-closed po przekroczeniu), smoke-test z fake-fetch + fake-sandbox, `create_watch` end-to-end na
  stubie/fake.
- **Poza zakresem**: sam sandbox/Starlark (`watch-sandbox`); logika runtime watchy (`watch-execution`);
  uczenie się/ulepszanie promptu generatora w czasie; wielojęzyczne predykaty.
