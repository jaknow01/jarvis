## Why

Dzisiejszy `scheduler_agent` potrafi tworzyć wyłącznie joby **wyzwalane czasem**
(`once` / `cron`). Nie da się poprosić Jarvisa o powiadomienie **warunkowe** —
zależne od stanu świata, nie od zegara („daj znać, gdy kurs USD/PLN spadnie poniżej
X"). Taki warunek to regularne odpytywanie danych + deterministyczna ocena predykatu +
wysłanie **dokładnie raz**, gdy warunek się spełni. Ten change buduje rdzeń tego
mechanizmu po stronie agentowej, tak by dało się go w całości przetestować **zanim**
powstanie prawdziwy sandbox (`watch-sandbox`) i pipeline generowania kodu
(`watch-authoring`).

## What Changes

- Nowy rodzaj joba **`kind="watch"`** w schedulerze: obok `once`/`cron` dochodzi job
  „obserwacyjny" — źródło danych + interwał pollingu + predykat + wiadomość do wysłania.
  Zakres v1: **wyłącznie jednowarunkowy** (jeden warunek → jedna wiadomość → koniec);
  sekwencje/FSM są poza zakresem.
- **Rejestr źródeł** (`source registry`): cienka mapa `source-id → istniejący fetch
  subagenta`, zwracająca `dict` (te same dane, po które sięgają dziś toole finance/
  weather/fpl/…). Rozróżnia trzy stany odczytu: `true` / `false` / **`unknown`** (fetch
  zwrócił błąd — np. `{"Error": …}` — więc **nie** wyzwalamy).
- **Rozszerzenie `scheduler_runner`** o gałąź dla `kind="watch"`: co tick dla due-watcha
  wykonaj `fetch(source, params) → evaluate(code, data) → bool`, i **edge-trigger** na
  przejściu `false→true` (porównanie z zapisanym `last_result`), a po wyzwoleniu
  `deliver(message)` istniejącym kanałem i zakończ joba.
- **Kontrakt sandboxa `{code, data} -> bool`** jako szew między change'ami. W tym change
  dostarczamy go w postaci **in-process STUBA** (deterministyczny ewaluator w Pythonie),
  żeby przepływ dało się testować bez infry. Produkcyjną, izolowaną implementację tego
  samego kontraktu dostarcza `watch-sandbox`.
- Narzędzie **`create_watch`** dla `scheduler_agent`: przyjmuje `source`, `params`,
  `check_code` (predykat jako tekst), `message`, `poll_interval` i zapisuje joba do
  store'u. (Generowanie/walidacja/naprawa `check_code` to osobny change `watch-authoring`;
  tutaj tool zapisuje kod „jak jest".)
- **Trwałość stanu watcha**: pola `source`, `params`, `check_code`, `poll_interval`,
  `last_result` w store'rze (JSON + Postgres), spójnie z obecnym wzorcem JSON/Postgres
  schedulera.

Brak zmian w zachowaniu istniejących jobów `once`/`cron`; change jest addytywny.

## Capabilities

### New Capabilities
- `conditional-watches`: obserwacyjny rodzaj joba (`kind="watch"`) — jego cykl życia
  (utworzenie przez `create_watch`, polling wg interwału, edge-trigger `false→true` przez
  `last_result`, dostarczenie wiadomości, zakończenie), trwałość stanu w store/schema
  (JSON + Postgres), rozszerzenie pętli `scheduler_runner`, oraz **kontrakt ewaluacji
  predykatu `{code, data} -> bool`** wraz z in-process stubem (produkcję kontraktu
  przejmuje `watch-sandbox`). Zakres v1: pojedynczy warunek.
- `watch-sources`: rejestr nazwanych, parametryzowanych źródeł danych dla predykatów,
  re-używający **istniejących fetchów subagentów** (zwracających `dict`), z jawnym
  rozróżnieniem `true`/`false`/`unknown` (odczyt błędny → brak wyzwolenia).

### Modified Capabilities
<!-- Brak zmian na poziomie speców. Scheduler (lib/domain/scheduler.py,
     lib/runtime/scheduler_runner.py) nie jest opisany formalnym capability w
     openspec/specs/, więc jego rozszerzenie jest czysto kodowe/addytywne, bez delty. -->

## Impact

- **Kod (płaszczyzna agentowa, publiczne repo)**:
  - `lib/domain/scheduler.py` + `app/db/scheduler_repo.py` + `app/db/schema.py` — nowy
    `kind="watch"` i pola stanu watcha (`source`, `params`, `check_code`, `poll_interval`,
    `last_result`); kolumny dodane idempotentnie (`ADD COLUMN IF NOT EXISTS`).
  - `lib/runtime/scheduler_runner.py` — gałąź `kind="watch"` (fetch → eval → edge → deliver);
    ponowne uzbrojenie o `poll_interval`, zakończenie po wyzwoleniu.
  - **Nowy** rejestr źródeł (mapa `source-id → fetch subagenta`) — re-użycie istniejących
    funkcji danych (`ctx` w toolach jest dekoracyjny: `app_context` czyta contextvar, nie
    argument — więc fetch wołany jest deterministycznie).
  - **Nowy** in-process stub ewaluatora implementujący kontrakt `{code, data} -> bool`.
  - `lib/agents/scheduler_agent/tools.py` — narzędzie `create_watch`.
- **Zależności**: **żadnych nowych** — stub jest czystym Pythonem; prawdziwy Starlark/
  Podman należą do `watch-sandbox`.
- **Testy**: jednostkowe na czystej logice edge-trigger/stanów (`true/false/unknown`),
  rejestrze źródeł i gałęzi runnera (z wstrzykniętym stubem i fake-fetch), spójnie z
  obecną filozofią testów (bez sieci/API).
- **Poza zakresem (kolejne change'y)**: prawdziwy izolowany sandbox + Starlark
  (`watch-sandbox`); generowanie/normalizacja/walidacja/naprawa `check_code` i smoke-test
  (`watch-authoring`); sekwencje wielokrokowe / FSM; `active_window`; nowe integracje API
  (ograniczamy się do danych, które subagenci już potrafią pobrać).
