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

Pola (zob. `lib/scheduler.py`): `id`, `prompt`, `channel` (`messenger`/`log`), `target`
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

Zabezpieczenie w kodzie: jeśli wysyłka z tagiem zostanie odrzucona, `lib/messenger.py`
ponawia raz jako zwykłe `RESPONSE` (zadziała, gdy okno 24h akurat otwarte), a przy
dalszym niepowodzeniu loguje wskazówkę. Najprostsza furtka awaryjna: napisz cokolwiek do
bota, by odświeżyć okno.

## Odporność na restart

`next_run_at` jest trwałe, więc po restarcie serwisu joby wznawiają się same. Dla cronów
kolejny czas liczony jest od **teraz** (croniter), więc okazje pominięte podczas przestoju
**nie są odtwarzane hurtem** — bierzemy najbliższą przyszłą (polityka skip-missed).

## Weryfikacja

```bash
poetry run pytest tests/test_scheduler.py     # schedule maths, store, runner tick (bez sieci)
```

Ręcznie (lokalnie, kanał `log`): poproś „przypomnij mi za 1 minutę o kawie" → job trafia do
store, a po minucie pętla loguje `[SCHEDULED ...]` z odpowiedzią. Przez Messengera: ustaw
testowo `cron_expr` na najbliższą minutę i sprawdź, że wiadomość przychodzi w wątku.
