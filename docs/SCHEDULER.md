# Scheduler — proaktywne joby (przypomnienia i cykliczne briefy)

Ten moduł daje Jarvisowi **własną, alternatywną drogę wejścia**: zamiast czekać na
Twoją wiadomość, potrafi sam odpalić zadanie o wyznaczonym czasie i **napisać do Ciebie**
z wynikiem. Dwa scenariusze docelowe:

- „przypomnij mi za dwie godziny o X" → jednorazowy job → po 2h bot pisze przypomnienie,
- „codziennie przez miesiąc rano o 8 dawaj mi pogodę + briefing wiadomości + czy grają
  moi zawodnicy FPL" → cykliczny job (cron) z granicą czasu.

## Jak to działa

```
utworzenie:  Ty ──► coordinator ──► scheduler_agent.create_scheduled_job(...)
                                          │  kanał+target z ctx.conversation_id
                                          ▼
                                     store jobów (Postgres / JSON)   next_run_at ← croniter

odpalenie:   pętla schedulera (w serwisie webhooka, tick co N s)
                 │ znajduje joby z next_run_at <= teraz
                 ▼
             engine.handle_message(job.conversation_id, job.prompt, origin="system")
                 │  = ten sam coordinator → subagenci → composer co zwykła tura
                 ▼
             dostawa na kanał joba
                 ├─ messenger → Send API z tagiem HUMAN_AGENT  (fallback: RESPONSE → log)
                 └─ log/repl  → wpis w logu (brak żywego gniazda do pushu)
```

Kluczowe: **prompt joba to zwykłe zadanie po polsku dla całego Jarvisa**. Odpala je
istniejący koordynator ze wszystkimi subagentami — scheduler nie wie nic o pogodzie/FPL,
tylko *kiedy* i *co* uruchomić oraz *gdzie* dostarczyć odpowiedź.

## Model joba

Pola (zob. `lib/domain/scheduler.py`): `id`, `prompt`, `channel` (`messenger`/`log`), `target`
(PSID — trzymany server-side, model go nie widzi), `conversation_id`, `kind`
(`once`/`cron`), `cron_expr`, `run_at`, `next_run_at`, `until`, `max_runs`, `run_count`,
`last_run_at`, `status` (`active`/`done`).

Tworząc job, podaj **dokładnie jedno** z:
- `delay_minutes` — względne jednorazowe („za dwie godziny" → 120),
- `run_at` — bezwzględne jednorazowe (ISO, „jutro o 9" → `2026-08-23T09:00`),
- `cron_expr` — cykliczne (5-polowy cron w strefie `SCHEDULER_TIMEZONE`), np.
  `0 8 * * *` (codziennie 8:00), `0 8 * * 1-5` (dni robocze 8:00), `0 * * * *` (co godzinę).

Dla cyklicznych opcjonalnie `until` (ISO data/‑czas, granica włącznie — „przez miesiąc")
i/lub `max_runs` (limit powtórzeń).

## Narzędzia agenta

`scheduler_agent` (subagent koordynatora) udostępnia: `create_scheduled_job`,
`list_scheduled_jobs`, `delete_scheduled_job`, `update_scheduled_job`. Koordynator
deleguje do niego, gdy wykryje prośbę o przypomnienie/cykliczny brief.

## Konfiguracja (`.env`)

| Zmienna | Znaczenie | Domyślnie |
|---|---|---|
| `SCHEDULER_ENABLED` | master wyłącznik pętli (0/false/no/off = off) | on |
| `SCHEDULER_TICK_SECONDS` | co ile sekund sprawdzać due joby | 30 |
| `SCHEDULER_TIMEZONE` | strefa dla cron/`run_at` | Europe/Warsaw |
| `AGENT_SCHEDULER_AGENT_ENABLED` | ukryj subagenta przed koordynatorem | on |

Dostawa proaktywna reużywa `MESSENGER_PAGE_ACCESS_TOKEN`. Store idzie do **Postgres**,
gdy ustawione `DATABASE_URL` (tabela `scheduled_jobs`), inaczej do
`data/scheduler_data/jobs.json`.

## Panel Meta (okno 24h a proaktywny push)

Meta pozwala pisać swobodnie tylko przez **24h od Twojej ostatniej wiadomości**. Brief
poranny wypada zwykle poza tym oknem, dlatego proaktywna dostawa używa
`messaging_type=MESSAGE_TAG` + `tag=HUMAN_AGENT` (okno **7 dni**). W trybie **Development**
dla właściciela z rolą w aplikacji to działa — upewnij się tylko w **Messenger API Setup**,
że Strona jest zasubskrybowana i token ma prawo do wiadomości (zob.
[MESSENGER.md](MESSENGER.md#kwestia-okna-24h-proaktywny-brief-poranny)).

Zabezpieczenie w kodzie: jeśli wysyłka z tagiem zostanie odrzucona, `lib/runtime/messenger.py`
ponawia raz jako zwykłe `RESPONSE` (zadziała, gdy okno 24h akurat otwarte), a przy
dalszym niepowodzeniu loguje wskazówkę. Najprostsza furtka awaryjna: napisz cokolwiek do
bota, by odświeżyć okno.

## Odporność na restart

`next_run_at` jest trwałe, więc po restarcie serwisu joby wznawiają się same. Dla cronów
kolejny czas liczony jest od **teraz** (croniter), więc okazje pominięte podczas przestoju
**nie są odtwarzane hurtem** — bierzemy najbliższą przyszłą (polityka skip-missed).

## Warunkowe watche (`kind="watch"`) — powiadomienia zależne od stanu, nie od zegara

Obok jobów czasowych (`once`/`cron`) jest trzeci rodzaj — **watch**: „daj znać, **gdy**
kurs USD/PLN spadnie poniżej 3,90". Zamiast odpalać o czasie, watch **regularnie odpytuje
źródło danych** i powiadamia **dokładnie raz**, gdy warunek staje się prawdą.

```
utworzenie:  Ty ──► coordinator ──► scheduler_agent.create_watch(
                        message, source, check_code, poll_interval, params)
                                          ▼
                                     store (kind="watch": source, params, check_code,
                                            poll_interval, last_result)

polling:     pętla schedulera, co tick dla due-watcha:
   1. FETCH   watch_sources.fetch_source(source, params) → ("ok", data) | ("unknown", None)
   2. EVAL    evaluator.evaluate(check_code, data) → bool           (kontrakt {code,data}->bool)
   3. EDGE    dostawa message TYLKO na przejściu last_result: false→true
   4. KONIEC  po wyzwoleniu status="done" (v1 jednowarunkowy); inaczej re-arm o poll_interval
```

- **Trzy stany odczytu:** `true` / `false` / **`unknown`** (źródło niedostępne lub zwróciło
  błąd, np. `{"Error": …}`). `unknown` → **nic nie wysyłamy, `last_result` bez zmian**, retry
  następnym tickiem. Błąd fetchu nigdy nie jest mylony z `false`.
- **Edge-trigger:** wysyłka tylko na `false→true` (porównanie z `last_result`), więc przy
  pollingu co minutę **nie zalewa** wiadomościami. Błąd dostawy **nie konsumuje krawędzi** —
  `last_result` zostaje, powiadomienie ponawia się przy następnym pollu.
- **Źródła** (`lib/agents/scheduler_agent/watch_sources.py`): rejestr `source-id → fetch
  subagenta` (zwraca `dict`). Źródło to po prostu **read-only tool asystenta** użyty jako
  odpytywalne dane — katalog ma być **tak szeroki jak narzędzia do zewn. API**, więc dodanie
  źródła to **jeden wiersz** w `_WATCHABLE` (`(source-id, agent, tool, hint)`), a nie osobny
  fetcher. Aktualnie: `finance.fx`, `finance.quote`, `weather.current`, `weather.forecast`,
  `fpl.live` (mecze + żywe punkty zawodników), `fpl.player`, `fpl.fixtures`,
  `fpl.league_standings`, `news.search`, `maps.route`. Narzędzia **mutujące** (IoT on/off,
  zapisy pamięci, sam scheduler) są celowo **poza** katalogiem — watch tylko odpytuje.
  Katalog id→opis jest **własnością warstwy agents** (dynamiczny, z zarejestrowanych toolów) i
  jest jedynym źródłem prawdy dla walidacji `source` (`is_known`/`describe_sources`) oraz dla
  listy wstrzykiwanej w prompt `scheduler_agent`. `build_watch` (domain) już nie waliduje
  `source` względem katalogu — trzyma tylko wybrany string. Fetch jest **deterministyczny**
  (bez LLM); `ctx` toolów jest dekoracyjny.
- **Ewaluator** (`lib/runtime/watch_evaluator.py`): kontrakt `evaluate(code, data) -> bool`.
  W tym change to **in-process STUB** (wykonuje predykat jako Python; **NIE jest granicą
  bezpieczeństwa**, tylko dev/test). Produkcyjny, izolowany ewaluator (Starlark w kontenerze)
  dostarcza osobny change `watch-sandbox`; generowanie/walidacja `check_code` — `watch-authoring`.
- **Zakres v1:** watche **jednowarunkowe** (jeden warunek → jedna wiadomość → koniec).
  Sekwencje/FSM (np. „koniec 1. połowy, potem 2.") — kolejna iteracja.

### Authoring predykatu (dwa kroki, potwierdzenie przed uzbrojeniem)

Kod predykatu pisze LLM, a Starlark jest okrojonym Pythonem — dlatego tworzenie watcha idzie
przez **pipeline authoringu** (`lib/agents/scheduler_agent/authoring.py`), całość
**przy tworzeniu** (runtime pollingu jest bez kosztu LLM):

```
prepare_watch(message, source, check_code, poll_interval, intent, params)
   1. normalize   (deterministycznie: zdejmij ``` fence, no-op f-string)  — bez LLM
   2. validate    przez sandbox/stub (validate → ok | treść błędu)
   3. repair      przy błędzie: LLM_BY_AGENT["watch_coder"] dostaje kod+błąd+intencję+reguły
                  → iteracja z capem (WATCH_AUTHORING_MAX_REPAIRS, dflt 3) → fail-closed
   4. smoke-test  pobierz próbkę (rejestr źródeł) → evaluate(code, sample) → pokaż wynik
   5. explain     LLM_BY_AGENT["watch_coder"] dostaje ZWALIDOWANY kod + intencję i pisze po
                  ludzku (PL), CO watch realnie zrobi w czasie (kiedy odpali, jak często,
                  jakie progi/bramki są w kodzie); jeśli kod rozjeżdża się z intencją, kończy
                  linią „Uwaga: …". Best-effort — błąd tu NIE blokuje poprawnego watcha.
   → zwraca {prepared: {check_code(zwalidowany), current_result, explanation, data_sample}}
     — NIE uzbraja. `data_sample` to przycięta próbka realnych danych źródła (prawdziwe
     klucze), którą LLM widzi, by poprawić predykat zamiast zgadywać nazwy pól; ta sama
     próbka trafia do kroku `explain`, żeby opis był oparty o rzeczywistość.

   ↓ (bramka nadzoru: LLM pokazuje userowi explanation + current_result i CZEKA na wyraźne
      „tak"; „popraw" → nowy prepare_watch; rezygnacja → nic nie uzbraja)

create_watch(message, source, check_code, poll_interval, params)
   re-walidacja (safety net) → build_watch → store   — DOPIERO TU uzbrojenie
```

**Krok `explain` to bramka nadzoru** — walidacja jest wyłącznie składniowa, więc semantycznie
błędny predykat (np. „powiadom, gdy zawodnik zdobędzie punkt" zbudowany tak, że odpala co
minutę przez cały mecz) przeszedłby bez niej. Coder po raz pierwszy dostaje kod *i* intencję,
i tłumaczy zachowanie na ludzki język, żeby user wychwycił rozjazd zanim watch zostanie uzbrojony.

**Routing cron vs watch** (system prompt `scheduler_agent`): wyzwalacz czasowy → `create_scheduled_job`;
wyzwalacz „gdy/jeśli <stan stanie się prawdą>" → watch, ale **tylko** gdy istnieje źródło dla
danej domeny (v1: `finance.fx`, `finance.quote`). Dla domeny bez źródła (mecz, FPL, pogoda)
agent **nie fabrykuje crona co minutę** zalewającego usera — mówi wprost, że warunkowe watche
pokrywają na razie tylko FX i kursy akcji.

- **Fail-closed:** jeśli kod nie przejdzie walidacji w limicie prób — **nie uzbrajamy**,
  prosimy usera o doprecyzowanie. Tak samo gdy sandbox/źródło niedostępne.
- **Generator = `scheduler_agent`** (pisze `check_code` + `intent`); **repair = osobny
  „kodowy" model** przez `AGENT_WATCH_CODER_MODEL` (fallback `DEFAULT_MODEL`).
- **Walidacja „na ślepo":** repair-LLM nie ma własnego Starlarka — faktyczna kompilacja
  zawsze przez sandbox (w dev: stub). Reguły Starlarka: `authoring.STARLARK_RULES`.

## Weryfikacja

```bash
poetry run pytest tests/test_scheduler.py     # schedule maths, store, runner tick (bez sieci)
poetry run pytest tests/test_watch_*.py       # watche: store, źródła, stub, runner, create_watch
```

Ręcznie (lokalnie, kanał `log`): poproś „przypomnij mi za 1 minutę o kawie" → job trafia do
store, a po minucie pętla loguje `[SCHEDULED ...]` z odpowiedzią. Przez Messengera: ustaw
testowo `cron_expr` na najbliższą minutę i sprawdź, że wiadomość przychodzi w wątku.
