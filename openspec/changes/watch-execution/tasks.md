## 1. Model danych watcha (store + schema)

- [x] 1.1 `lib/domain/scheduler.py`: dodać `"watch"` do `VALID_KINDS`; rozszerzyć `build_job`
      (lub dodać `build_watch`) o pola `source`, `params` (dict), `check_code` (str),
      `message` (str), `poll_interval` (int, s), `last_result` (init: unknown/None); walidacja
      (niepusty `source`/`check_code`, `poll_interval > 0`).
- [x] 1.2 `next_run_at` watcha liczone jako `now + poll_interval`; re-arm po pollu też o
      `poll_interval` (nie przez croniter). Potwierdzić, że `due()`/`active()` obejmują watche.
- [x] 1.3 `app/db/schema.py`: kolumny `source`, `params (JSONB)`, `check_code`, `message`,
      `poll_interval`, `last_result` na `scheduled_jobs` przez `ADD COLUMN IF NOT EXISTS`.
- [x] 1.4 `app/db/scheduler_repo.py`: odczyt/zapis nowych pól (spójnie z JSON-owym `JobStore`);
      round-trip watcha w obu backendach.
- [x] 1.5 `public_view`/`describe_schedule`: czytelny opis watcha („watch on <source> co <n>s"),
      bez wystawiania surowych `params`/`check_code` do modelu, jeśli wrażliwe.

## 2. Rejestr źródeł (reuse fetchów subagentów)

- [x] 2.1 Nowy moduł rejestru: mapa `source-id -> fetcher(params) -> dict`. Preferować ctx-free
      rdzenie fetchujące (np. `get_forecast`); gdzie brak rdzenia — cienki wrapper wołający
      `FunctionTool.fn` z atrapą `ctx` (uzasadnione D5: `app_context` czyta contextvar).
- [x] 2.2 Ustalić i zarejestrować startowy zestaw źródeł v1 (te toole, które zwracają „czyste"
      dicty z polami do predykatów; kandydaci: finance FX/quote, weather forecast, FPL reference).
- [x] 2.3 Rozwiązywanie `source-id`: nieznane źródło → jasny błąd (używane przy `create_watch`
      i w runnerze).
- [x] 2.4 Warstwa 3-stanowa: `fetch_source(source, params) -> (state, data)` gdzie
      `state ∈ {ok, unknown}`; wykrycie błędu (wyjątek fetchera **lub** dict z `Error`) → `unknown`.

## 3. Kontrakt ewaluatora + in-process STUB

- [x] 3.1 Zdefiniować stały kontrakt `evaluate(code: str, data: dict) -> bool` (sygnatura +
      dokumentacja: wyjątek ewaluatora traktowany przez runner jako `unknown`).
- [x] 3.2 In-process stub implementujący kontrakt (deterministyczny, do dev/testów) — jawnie
      oznaczony jako **niezabezpieczony**; produkcję kontraktu przejmuje `watch-sandbox`.
- [x] 3.3 Punkt wstrzyknięcia ewaluatora w runnerze (parametr/rejestr), by dało się podmienić
      stub na prawdziwy sandbox bez zmian w logice runnera.

## 4. Gałąź runnera dla `kind="watch"`

- [x] 4.1 `lib/runtime/scheduler_runner.py`: w `tick_once`/`_run_job` rozgałęzić po `kind`.
      Dla watcha: `fetch_source → (unknown? → skip) → evaluate(code, data) → bool`.
- [x] 4.2 Edge-trigger: wysyłka `deliver(job, message)` tylko na `false→true` względem
      `last_result`; aktualizacja `last_result`; brak wysyłki przy `true→true`/`false→false`.
- [x] 4.3 `unknown`: brak wysyłki, `last_result` bez zmian, watch pozostaje aktywny (retry).
- [x] 4.4 Terminacja: po wyzwoleniu (v1 jednowarunkowy) `status="done"`, `next_run_at=None`;
      w przeciwnym razie re-arm o `poll_interval`. Błędy fetchu/eval łapane per-job (nie wywalają pętli).

## 5. Narzędzie `create_watch`

- [x] 5.1 `lib/agents/scheduler_agent/tools.py`: `@tool_ownership("scheduler_agent")` +
      `@function_tool` `create_watch(ctx, source, params, check_code, message, poll_interval)` —
      waliduje `source` przez rejestr, zapisuje joba (kod **verbatim**), zwraca `public_view`.
- [x] 5.2 Docstring narzędzia (spec dla LLM) w istniejącym szczegółowym stylu; zaznaczyć, że
      generowanie/naprawa `check_code` NIE należą do tego narzędzia (osobny change).
- [x] 5.3 Potwierdzić, że `scheduler_agent` faktycznie podpina nowe narzędzie (rejestracja
      przez import side-effect; agent bierze `TOOLS_BY_AGENT["scheduler_agent"]`).

## 6. Testy (bez sieci/API)

- [x] 6.1 Store: round-trip watcha (JSON), pola i `last_result`, `due()`/re-arm o `poll_interval`.
- [x] 6.2 Rejestr źródeł: rozwiązywanie `source-id`, nieznane → błąd, mapowanie błędu fetchera
      i dict-`Error` na `unknown` (z fake-fetcherami).
- [x] 6.3 Edge-trigger (czysta logika): sekwencje wyników `false→true` (1 wysyłka), `true→true`
      (0), `false→false` (0), `unknown` (0, stan bez zmian) — ze stubem i fake-deliver/fetch.
- [x] 6.4 Gałąź runnera end-to-end na stubie: watch przechodzi na `true` → dostarcza raz →
      `done`; watch z `unknown` → nie dostarcza, aktywny.
- [x] 6.5 `create_watch`: zapis verbatim `check_code`, walidacja nieznanego `source`.
- [x] 6.6 Pełny `pytest` zielony; brak regresji w istniejących testach schedulera.

## 7. Dokumentacja

- [x] 7.1 `docs/SCHEDULER.md`: sekcja o watchach (model, edge-trigger, 3 stany, kontrakt
      `{code,data}->bool`, że v1 jest jednowarunkowy i używa stubu; sandbox/authoring osobno).
- [x] 7.2 `CLAUDE.md`: krótka wzmianka o `kind="watch"` i szwie kontraktu ewaluatora.
- [x] 7.3 `.env_template`: ewentualny minimalny `poll_interval` / przełączniki, jeśli wprowadzone.
