# Podłączenie Jarvisa do Facebook Messengera

Instrukcja krok po kroku: **co musisz zrobić po swojej stronie** (w panelu Meta) i
**jakie sekrety dostarczyć do `.env`**, żeby program miał prawo działać przez
Messengera. Dokument jest napisany pod scenariusz Jarvisa: **jeden użytkownik —
właściciel**, więc pomijamy App Review i weryfikację biznesową (patrz sekcja
[Dlaczego bez App Review](#dlaczego-nie-potrzebujesz-app-review)).

> **Model działania w skrócie:** piszesz do **Strony na Facebooku** → Meta robi
> POST na Twój **publiczny webhook HTTPS** → Jarvis przetwarza wiadomość →
> odpowiada przez **Send API**. Webhook musi odpowiedzieć w **< 5 s**, więc samo
> uruchomienie agenta idzie w tło.

---

## Zanim zaczniesz — czego potrzebujesz

- Konto na Facebooku (Twoje prywatne — będziesz adminem aplikacji).
- 15–20 minut w panelu [developers.facebook.com](https://developers.facebook.com/).
- Na czas testów lokalnych: **publiczny tunel HTTPS** do Twojej maszyny.
  Rekomendacja: [`ngrok`](https://ngrok.com/) (`ngrok http 8002`) albo
  `cloudflared`. Meta **nie przyjmie** adresu `http://localhost`.

---

## Część A — Rzeczy do wyklikania w panelu Meta

Wszystkie kroki robisz **Ty, ręcznie**. Efektem są 3 sekrety, które trafią do
`.env` (Część B).

### Krok 1. Utwórz Stronę na Facebooku

Bot ma tożsamość **Strony** (Page), nie prywatnego profilu — piszesz *do Strony*.

1. [facebook.com/pages/create](https://www.facebook.com/pages/create) → dowolna
   nazwa (np. „Jarvis"), kategoria dowolna.
2. Strona może zostać prywatna/niepublikowana — do testów właściciela to bez
   znaczenia.
3. Zapisz **Page ID** (Ustawienia Strony → *About/Informacje* → na dole „Page ID",
   albo później odczytasz go z tokena).

### Krok 2. Utwórz aplikację Meta

1. [developers.facebook.com/apps](https://developers.facebook.com/apps) →
   **Create App**.
2. Typ aplikacji: **Business**.
3. Po utworzeniu aplikacja domyślnie jest w trybie **Development** — i tak ma
   zostać. W tym trybie bot działa **w pełni**, ale tylko dla osób z rolą w
   aplikacji (czyli dla Ciebie).

### Krok 3. Otwórz „Messenger API Setup"

> ⚠️ **Panel Meta się zmienił (2025/2026).** Nie ma już osobnego **Add Product →
> Messenger** ani zakładki **Messenger → Settings**. Wszystko jest scalone na
> jednej stronie **Messenger API Setup** (lewe menu → *Messenger from Meta* →
> **Messenger API Settings**). Znajdziesz tam sekcje **1. Configure webhooks** i
> **2. Generate access tokens** — to jest odpowiednik dawnych kroków 3–4/7.

### Krok 4. Wygeneruj Page Access Token

1. Na stronie **Messenger API Setup** → sekcja **2. Generate access tokens** →
   połącz Stronę z Kroku 1 (nadaj wymagane uprawnienia, w tym zarządzanie
   wiadomościami) i **wygeneruj token**.
2. **Skopiuj token od razu** — to jest `MESSENGER_PAGE_ACCESS_TOKEN`.
   (Widoczny tylko raz; jak zgubisz, wygeneruj ponownie.)

> Token wygenerowany w ten sposób jest długożyjący (page token). Trzymaj go jak
> hasło — daje pełne prawo pisania z Twojej Strony.

### Krok 5. Zdobądź App Secret

1. Panel aplikacji → **Settings → Basic**.
2. Pole **App Secret** → *Show* → skopiuj. To jest `MESSENGER_APP_SECRET`.
   Służy do weryfikacji podpisu `X-Hub-Signature-256` — dzięki temu program wie,
   że POST naprawdę pochodzi od Meta, a nie od kogoś obcego.

### Krok 6. Wymyśl Verify Token

To **Twój własny, dowolny** sekret (np. wygeneruj `openssl rand -hex 16`). Nie
pochodzi od Meta — używa go tylko handshake przy podłączaniu webhooka: Meta
odeśle Ci go w parametrze `hub.verify_token`, a Twój endpoint sprawdza, czy się
zgadza. To jest `MESSENGER_VERIFY_TOKEN`.

### Krok 7. Podłącz webhook

> Ten krok wykonaj **dopiero gdy serwer Jarvisa działa i tunel HTTPS jest
> podniesiony** (patrz Część C) — Meta od razu zweryfikuje URL.

1. Wystaw serwer publicznie pod HTTPS. Docelowo: serwer domowy (Raspberry) +
   DuckDNS + Let's Encrypt — patrz [LOCAL_SERVER.md](LOCAL_SERVER.md). Na szybki
   test: `ngrok http 8002` → adres typu `https://abcd-1234.ngrok-free.app`.
2. **Messenger API Setup → sekcja 1. Configure webhooks**:
   - **Callback URL:** `https://<twoja-domena>/webhook`
   - **Verify token:** ta sama wartość co `MESSENGER_VERIFY_TOKEN` z Kroku 6.
   - kliknij **Verify and save**.
3. Meta wyśle GET na Twój endpoint; jeśli tokeny się zgadzają — zapis przejdzie
   (zobaczysz *Verified*). Serwer Jarvisa **musi już działać** (patrz Część C).
4. W polu subskrypcji (**Webhook fields / Add subscriptions**) zaznacz co
   najmniej **`messages`** (opcjonalnie `messaging_postbacks`,
   `message_deliveries`, `message_reads`).
5. Upewnij się, że **Strona** z Kroku 1 jest subskrybowana do tego webhooka.

### Krok 8. (Zwykle nie trzeba) Role aplikacji

Ponieważ jesteś adminem aplikacji, masz dostęp od razu. Gdyby z innego konta ktoś
miał testować — dodaj je w **App Roles → Roles** jako *Tester/Developer*. Bez tego
w trybie Development bot nie odpowie obcemu kontu.

---

## Część B — Co dopisać do `.env`

Dodaj do pliku `.env` (i do `.env_template` jako puste klucze):

```dotenv
# --- Facebook Messenger ---
MESSENGER_PAGE_ACCESS_TOKEN=   # Krok 4 — token Strony (Send API)
MESSENGER_APP_SECRET=          # Krok 5 — weryfikacja podpisu X-Hub-Signature-256
MESSENGER_VERIFY_TOKEN=        # Krok 6 — Twój dowolny sekret do handshake'u webhooka
MESSENGER_PAGE_ID=             # Krok 1 — ID Strony (opcjonalne, przydatne do logów/filtrowania)

# Opcjonalnie: ogranicz bota do jednego rozmówcy (Twój PSID — patrz niżej)
MESSENGER_ALLOWED_SENDER_ID=
```

### Skąd wziąć `MESSENGER_ALLOWED_SENDER_ID` (Twój PSID)

To **nie** jest Twoje zwykłe ID z Facebooka, tylko **PSID** (Page-Scoped ID) —
identyfikator Twojej osoby *w kontekście tej Strony*. Zobaczysz go dopiero **po
pierwszej wiadomości**: gdy napiszesz do Strony, w payloadzie webhooka przyjdzie
`sender.id`. Zaloguj tę wartość przy pierwszym uruchomieniu i wpisz do `.env` —
dzięki temu Jarvis będzie ignorował kogokolwiek innego (dodatkowa warstwa
bezpieczeństwa dla osobistego asystenta).

---

## Część C — Jak to spina się z kodem Jarvisa

Nic z powyższego nie zmienia rdzenia. Messenger to **kolejny adapter kanału** na
tym samym szwie, który opisuje `CLAUDE.md` (*handle one message → return one
reply*). Docelowo:

```
Ty ──piszesz──► Strona FB ──POST──► /webhook (FastAPI, port 8002)
                                        │  1. weryfikuj podpis (APP_SECRET)
                                        │  2. wyciągnij sender.id + text
                                        │  3. odpowiedz 200 OK w <5 s
                                        ▼
                                  background task
                                        │  handle_message(sender_id, text)
                                        │  = ten sam Runner.run(coordinator, …)
                                        │    co REPL; previous_response_id
                                        │    w Redis pod kluczem messenger:{sender_id}
                                        ▼
                             Send API (PAGE_ACCESS_TOKEN)  ──► wiadomość wraca do Ciebie
```

Punkty zaczepienia w istniejącym kodzie:
- **Wejście:** nowy moduł webhooka (FastAPI) słuchający na porcie **8002** — już
  zarezerwowany w `docker-compose.yml`.
- **Rdzeń:** wyodrębnić z `lib/chatbot.py` funkcję `handle_message(user_id, text)
  -> reply` i wołać ją zarówno z REPL, jak i z webhooka.
- **Ciągłość rozmowy:** `previous_response_id` już trzymacie w Redis
  (`lib/cache.py`) — zmienia się tylko klucz na `messenger:{sender_id}`.
- **Wyjście:** POST na `https://graph.facebook.com/v22.0/me/messages` z
  `access_token=MESSENGER_PAGE_ACCESS_TOKEN`.

**Stan implementacji: zrobione.** Adapter istnieje:
- `lib/engine.py` — transport-agnostyczny rdzeń `handle_message(conversation_id,
  text, ctx)`; woła go i REPL (`lib/chatbot.py`, id `"repl"`), i webhook
  (id `"messenger:{psid}"`). Ciągłość rozmowy per-użytkownik pod kluczem
  `previous_response_id:{conversation_id}` w Redis.
- `lib/messenger.py` — weryfikacja podpisu `X-Hub-Signature-256`, handshake GET,
  parsowanie eventów, wysyłka przez Send API (chunkowanie do 2000 znaków).
- `app/webhook.py` — aplikacja FastAPI (`/webhook` GET+POST, `/health`),
  weryfikacja podpisu, natychmiastowy 200, przetwarzanie w tle, opcjonalny filtr
  `MESSENGER_ALLOWED_SENDER_ID`.

Uruchomienie serwera webhooka (wymaga działającego Redis):

```bash
poetry run uvicorn app.webhook:app --host 0.0.0.0 --port 8002
```

REPL (`app/main.py`) działa dalej niezależnie — oba wejścia dzielą ten sam rdzeń.

---

## Kwestia okna 24 h (proaktywny brief poranny)

Meta pozwala botowi swobodnie odpowiadać tylko przez **24 h od Twojej ostatniej
wiadomości**. Dla zwykłej konwersacji (Ty piszesz → bot odpowiada) to **żaden
problem** — okno zawsze jest otwarte.

Problem pojawia się tylko przy **proaktywnym pushu** (np. brief o 7:00, gdy nie
pisałeś od >24 h). Sposoby obejścia, od najprostszego:

1. **Napisz cokolwiek do bota** (choćby „ok") — resetuje okno na 24 h. Dla
   osobistego użytku często wystarcza.
2. **Human Agent tag** — pozwala pisać przez **7 dni** od Twojej ostatniej
   wiadomości. Najbardziej praktyczna furtka dla asystenta; ustawia się flagą
   `messaging_type: MESSAGE_TAG` + `tag: HUMAN_AGENT` w wywołaniu Send API.
3. **One-Time Notification** — jednorazowa zgoda na jedno powiadomienie w
   przyszłości.

> ⚠️ Nie buduj na starych tagach `CONFIRMED_EVENT_UPDATE`, `ACCOUNT_UPDATE`,
> `POST_PURCHASE_UPDATE` — **przestają działać od 27 kwietnia 2026**.

Do 90% codziennego użytku (Ty inicjujesz rozmowę) temat okna Cię nie dotyczy;
dotyczy tylko schedulera z briefami.

---

## Dlaczego nie potrzebujesz App Review

Normalnie uprawnienie `pages_messaging` wymaga recenzji aplikacji przez Meta
(~5 dni) i uzasadnienia use case. **Ale** aplikacja w trybie **Development**
działa w pełni dla osób z **rolą w aplikacji** (Admin/Developer/Tester). Jarvis to
asystent dla jednej osoby — właściciela — więc jako admin własnej aplikacji
piszesz do własnej Strony **bez App Review i bez weryfikacji biznesowej**.

App Review będzie potrzebne dopiero, gdyby z bota miały korzystać **osoby
niezwiązane** z aplikacją (publiczne udostępnienie) — co nie jest celem projektu.

---

## Checklista sekretów do przekazania

Po przejściu Części A będziesz mieć komplet do wklejenia w `.env`:

- [ ] `MESSENGER_PAGE_ACCESS_TOKEN` — Krok 4
- [ ] `MESSENGER_APP_SECRET` — Krok 5
- [ ] `MESSENGER_VERIFY_TOKEN` — Krok 6 (wymyślasz sam)
- [ ] `MESSENGER_PAGE_ID` — Krok 1 (opcjonalne)
- [ ] `MESSENGER_ALLOWED_SENDER_ID` — po pierwszej wiadomości (opcjonalne, zalecane)

Webhook (Krok 7) podłączasz na końcu, gdy serwer + tunel HTTPS już działają.

---

## Szybki test „czy działa"

1. Serwer Jarvisa podniesiony, `ngrok http 8002` aktywny, webhook *Verified*.
2. Wejdź na swoją Stronę na Facebooku → **Wyślij wiadomość** → napisz „test".
3. W logach powinieneś zobaczyć POST na `/webhook` z `sender.id` i `text=test`.
4. Bot odpisuje przez Send API → wiadomość wraca w oknie Messengera.

Jeśli webhook nie przechodzi weryfikacji — najczęstsze przyczyny: zły
`VERIFY_TOKEN`, endpoint nie odpowiada na GET, albo tunel HTTPS padł.

---

### Źródła

- [Send API — Messenger Platform](https://developers.facebook.com/docs/messenger-platform/reference/send-api/)
- [Getting Started / Overview](https://developers.facebook.com/documentation/business-messaging/messenger-platform/overview)
- [Polityka platformy (okno 24 h, message tags)](https://developers.facebook.com/documentation/business-messaging/messenger-platform/policy)
- [App Review — Messenger Platform](https://developers.facebook.com/docs/messenger-platform/app-review/)
- [Webhook events: messages](https://developers.facebook.com/docs/messenger-platform/reference/webhook-events/messages/)
