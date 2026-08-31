# Komendy techniczne (admin / ops)

Specjalne wiadomości zaczynające się od `/`, dzięki którym podejrzysz i lekko
poszturchasz działający program **z telefonu** — bez SSH na Pi i grzebania w logach.

## Jak to działa

Router (`lib/runtime/admin.py`) jest wpięty **przed stosem agentów** — w
[`app/webhook.py`](../app/webhook.py) (`_process`) i w pętli REPL
([`lib/runtime/chatbot.py`](../lib/runtime/chatbot.py)). Konsekwencje z założenia:

- **Działa, gdy agenci NIE działają.** Komendy nie przechodzą przez koordynatora/LLM,
  więc `/status` i `/errors` odpowiedzą nawet wtedy, gdy to właśnie LLM się wysypał.
- **Zero kosztu i latencji** — brak wywołania modelu, brak halucynacji (surowe dane).
- **Tylko właściciel** — webhook i tak ogranicza wejście do `MESSENGER_ALLOWED_SENDER_ID`,
  a REPL jest lokalny. Nie ma osobnego gatingu.

Jeśli wiadomość nie zaczyna się od `/`, router zwraca `None` i wiadomość leci normalnym
torem do koordynatora.

Wyjście jest formatowane **pod bąbelek czatu na telefonie**: czysty tekst (Messenger nie
renderuje Markdown), krótkie linie, emoji jako kotwice wzrokowe, puste linie między
sekcjami. Długie odpowiedzi tnie na 2000 znaków `send_message`.

## Lista komend

Wpisz `/help`, żeby dostać tę listę na telefonie. Skrót:

| Grupa | Komenda | Co zwraca |
|-------|---------|-----------|
| Sterowanie | `/stop` | przerywa turę, którą agent **teraz** przetwarza w tej rozmowie |
| Zdrowie | `/status` | uptime, stan schedulera, baza, tracing, progress ping |
| | `/version` | uptime, świeżość plików kodu (czy deploy wszedł), git SHA |
| | `/ping` | potwierdzenie round-tripu bot → Send API |
| | `/probe` | dostępność zewnętrznych API (FX, FPL, Yahoo, OpenWeather, Maps, Tavily) |
| Logi | `/logs [N]` | ostatnie N linii bieżącego pliku logu (domyślnie 20) |
| | `/errors [N]` | ostatnie N linii z błędami/tracebackami (domyślnie 10) |
| | `/loglevel [poziom]` | pokaż/ustaw poziom logowania w locie (debug/info/…) |
| Scheduler | `/jobs` | aktywne zadania i watche z terminami i podglądem promptu |
| | `/fire <id>` | odpal zadanie **teraz** (bez zmiany harmonogramu) i dostarcz |
| | `/watch <id>` | szczegóły watcha: źródło, ostatni wynik, następny poll |
| | `/scheduler [pause\|resume]` | wstrzymaj/wznów odpalanie zadań (bez restartu kontenera) |
| Konfiguracja | `/agents` | którzy subagenci włączeni/wyłączeni (`AGENT_*_ENABLED`) |
| | `/models` | model + reasoning effort per agent (bez tworzenia klienta) |
| | `/env` | istotne zmienne env — **sekrety zamaskowane** |
| Rozmowa i pamięć | `/memory` | co pamięć długoterminowa o Tobie trzyma |
| | `/summary` | bieżące rolling-summary tej rozmowy |
| | `/reset confirm` | wyczyść historię + podsumowanie tej rozmowy (wymaga `confirm`) |
| Diagnostyka | `/last` | ostatnia tura: czas trwania, rozmowa, MLflow trace id |
| | `/window` | szacunek okna 24h Messengera (na podstawie ostatniej wiadomości) |
| | `/help` | ta lista |

`id` w `/fire`/`/watch` można podać **prefiksem** (np. pierwsze 8 znaków z `/jobs`), o ile
jest jednoznaczny.

## `/stop` — przerwanie trwającej tury

W webhooku każda wiadomość to osobny background task uvicorna, więc `/stop` biegnie
**równolegle** do trwającej tury. Długa tura rejestruje swój `asyncio.Task` w
[`diag.py`](../lib/runtime/diag.py) (`register_running`), a `/stop` go anuluje
(`cancel_running`) — `CancelledError` przerywa bieżące `await` w `handle_message` (w tym
zapytanie do LLM), tura nie jest zapisywana, a `_process` łyka wyjątek na granicy zadania.
Dotyczy tylko żywych tur użytkownika w tej rozmowie; zadania schedulera nie są ruszane. W
REPL `/stop` jest bezczynne (pętla `input()` blokuje, więc nie da się pisać w trakcie tury).

## Uwagi

- **Stan diagnostyczny jest w pamięci procesu** i zeruje się po restarcie: uptime,
  `/last`, heartbeat schedulera, tracker `/window` żyją w [`lib/runtime/diag.py`](../lib/runtime/diag.py).
  `/scheduler pause` też jest procesowe (nie przetrwa restartu kontenera).
- **`/version` a git w kontenerze:** w obrazie zwykle nie ma `.git` (montowane są tylko
  `app/` i `lib/`), więc SHA bywa niedostępne — dlatego jest też „najnowszy plik kodu",
  które i tak wprost mówi, czy `git pull` + restart wczytał nowy kod.
- **`/probe` robi realne zapytania** do zewnętrznych API z krótkim timeoutem; serwisy bez
  klucza raportuje jako „brak klucza", a keyless (Frankfurter/FPL/Yahoo) sprawdza na żywo.
- **Brak `/restart`** — świadomie pominięte. Restart rób przez `docker compose restart agent`.

## Dodanie nowej komendy

Napisz `async def _cmd_x(args, *, conversation_id, channel) -> str` i zarejestruj w
`_COMMANDS` (grupa/usage/summary). `/help` generuje się z tych metadanych, więc nie
rozjedzie się z rzeczywistością. Trwały stan diagnostyczny wkładaj do `diag.py`, żeby nie
robić cyklu importów przez `admin`.
