## Context

`scheduler_agent` + `lib/runtime/scheduler_runner.py` już dają „alternatywną ścieżkę
wejścia" do Jarvisa: pętla ticka budzi się co `SCHEDULER_TICK_SECONDS`, bierze due-joby
(`store.due(now)`) i re-armuje je (`next_after`/`is_expired`). Dziś joby są tylko czasowe
(`once`/`cron`). Ten change dokłada **warunkowy** rodzaj joba, re-używając tej samej pętli,
store'u i kanału dostarczania — bez nowej infry.

Kluczowe ograniczenie architektoniczne: **cała „inteligencja czasowa" (stan, wykrywanie
zmiany, terminacja) należy do zaufanego runtime**, a wykonanie predykatu jest odseparowane
za stabilnym kontraktem, tak by dało się je najpierw zastubować, a docelowo odizolować
(`watch-sandbox`).

## Goals / Non-Goals

**Goals**
- `kind="watch"` w schedulerze: źródło + interwał + predykat + wiadomość, jednowarunkowo.
- Edge-trigger `false→true` „dokładnie raz", ze stanem `last_result` w store.
- Rejestr źródeł re-używający istniejących fetchów subagentów (dict), z 3. stanem `unknown`.
- Kontrakt `{code, data} -> bool` + **in-process stub**, żeby przepływ był testowalny end-to-end
  bez sandboxa i bez pipeline'u authoringu.
- Zero nowych zależności; spójność z wzorcem JSON/Postgres schedulera.

**Non-Goals**
- Sekwencje wielokrokowe / FSM (`steps[]`, `current_step`) — kolejna iteracja.
- Prawdziwy izolowany sandbox + Starlark → `watch-sandbox`.
- Generowanie / normalizacja / walidacja / naprawa `check_code` + smoke-test → `watch-authoring`.
- `active_window`, nowe integracje API, dane spoza zasięgu obecnych subagentów.

## Decisions

### D1: Watch jako trzeci `kind` w istniejącym schedulerze (nie osobny podsystem)
Re-używamy `store.due`/`next_after`/`is_expired`/`save` i pętli `tick_once`. Watch różni się
tym, że „re-arm" liczy się z `poll_interval` (nie z crona), a przed dostarczeniem stoi
`fetch → eval → edge-check`.
- *Alternatywa:* osobny „watcher runner". Odrzucona — duplikuje pętlę, store i delivery bez zysku.

### D2: Edge-trigger przez `last_result` w zaufanym runtime
Dostarczamy na przejściu `false→true`. `last_result ∈ {unknown, false, true}` trzymany na
jobie; runner porównuje bieżący wynik z zapisanym. To *jedyne* miejsce decyzji o wysyłce —
predykat pozostaje bezstanowy (`data -> bool`).
- *Alternatywa:* level-trigger (wysyłaj gdy prawda). Odrzucona — zalewa użytkownika co poll.

### D3: Trzy stany odczytu (`true`/`false`/`unknown`), błąd ≠ fałsz
Fetch bywa zawodny; subagenci sygnalizują błąd **jako dict** (`{"Error": …}`), nie wyjątkiem.
`unknown` → brak wysyłki, brak zmiany `last_result`, retry następnym tickiem. To chroni przed
fałszywym „przejściem" po chwilowym błędzie API i przed zgubieniem prawdziwego zdarzenia.
- *Alternatywa:* traktować błąd jak `false`. Odrzucona — generuje fałszywe krawędzie i szum.

### D4: Kontrakt `{code, data} -> bool` + in-process STUB (szew między change'ami)
Runner nie wie, *jak* wykonywany jest predykat — woła ewaluator za stałym interfejsem.
W tym change ewaluator to deterministyczny stub w Pythonie (do testów przepływu). Produkcyjną,
izolowaną implementację tego samego kontraktu dostarcza `watch-sandbox`. Dzięki temu change 2
buduje i testuje się **niezależnie** od infry.
- *Alternatywa:* od razu prawdziwy sandbox. Odrzucona — blokuje stronę agentową na infrze;
  szew pozwala budować równolegle.

### D5: Rejestr źródeł re-używa fetchów subagentów; `ctx` jest dekoracyjny
`source-id → fetcher(params) -> dict`. Fetchery to istniejące funkcje danych subagentów.
Ustalone z kodu: `app_context(ctx)` czyta **contextvar** (`_CURRENT_CTX.get()`), a argument
`ctx` jest ignorowany („kept for signature symmetry"). Dla toolów finance/fpl/news `ctx`
jest w ogóle nieużywany; dla weather/maps/iot używany, ale te i tak nie są celami v1.
Preferowany kształt: rejestrować **ctx-free rdzenie fetchujące** (jak `get_forecast`), a nie
opakowane `FunctionTool` — omija to i `ctx`, i wrapper.
- *Alternatywa A:* wołać `FunctionTool.fn` z atrapą `ctx`. Działa, ale wciąga martwe plumbing.
- *Alternatywa B:* uruchamiać subagenta (LLM) po dane. Odrzucona — koszt/niedeterminizm na każdym pollu.

### D6: Model danych watcha (pola, bez FSM)
Na jobie: `source`, `params` (dict), `check_code` (tekst), `message`, `poll_interval` (s),
`last_result`. Postgres: kolumny przez `ADD COLUMN IF NOT EXISTS`; JSON: pola w rekordzie —
identyczny wzorzec jak reszta schedulera.
- *Alternatywa:* od razu `steps[]`+`current_step`. Odłożone (Non-Goal) — v1 jednowarunkowy.

## Risks / Trade-offs

- **Stub ≠ bezpieczeństwo** → stub wykonuje kod bez izolacji; jest **tylko** do dev/testów.
  Mitigacja: jawne oznaczenie, brak wystawiania na produkcję; realny ewaluator to `watch-sandbox`.
- **Predykat czyta zły klucz / zawsze `false`** → walidacja składni tego nie wykryje.
  Mitigacja (v1): to domena `watch-authoring` (smoke-test + potwierdzenie); tu poza zakresem.
- **Koszt/limit API przy krótkim `poll_interval`** → częsty fetch bije w źródła.
  Mitigacja: rozsądny minimalny `poll_interval`; cache w istniejących fetcherach (np. weather
  ma `requests_cache`); `active_window` odłożone, ale świadomie.
- **Latencja fetchu w pętli** → wolne źródło opóźnia tick. Mitigacja: fetch/eval per-job
  best-effort z łapaniem wyjątków (jak dziś `_run_job`), błąd → `unknown`, nie wywala pętli.
- **Rozjazd kontraktu ze `watch-sandbox`** → dwie implementacje muszą zgadzać się na
  `{code, data} -> bool` (+ reprezentacja `unknown`/błędu). Mitigacja: kontrakt zdefiniowany
  i otestowany tutaj; sandbox implementuje go 1:1.

## Migration Plan

- Czysto addytywne. Nowe kolumny `ADD COLUMN IF NOT EXISTS` (bez migracji danych); istniejące
  joby `once`/`cron` działają bez zmian.
- Rollback: usunięcie gałęzi `kind="watch"` z runnera i narzędzia `create_watch` przywraca
  poprzednie zachowanie; osierocone kolumny/pola są nieszkodliwe.
- Feature-gating: watch tworzy się tylko przez `create_watch`; brak wywołań = brak nowej ścieżki.

## Open Questions

- **Rejestr źródeł na v1:** który dokładnie zestaw `source-id` wystawiamy (które toole mają
  „czyste" dicty i sensowne pola do predykatów)? Rozstrzygnięcie przy implementacji.
- **Kształt fetchera w rejestrze:** ctx-free rdzeń vs `FunctionTool.fn` — D5 preferuje rdzeń;
  potwierdzić per źródło (nie wszystkie mają wydzielony rdzeń).
- **Reprezentacja `unknown` na kontrakcie:** ewaluator zwraca `bool`, więc `unknown` rozstrzyga
  **warstwa fetchu** (przed ewaluacją) — czy runner powinien też umieć dostać `unknown` z samego
  ewaluatora (np. wyjątek w kodzie)? Domyślnie: wyjątek ewaluatora → `unknown`.
- **Minimalny `poll_interval`:** czy narzucamy dolną granicę (koszt/limity), i jaką.
