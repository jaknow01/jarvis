# MCP — narzędzia Jarvisa jako serwery MCP po HTTP

Wybrane narzędzia Jarvisa (na razie: **FPL**) są wystawione jako serwer MCP
(Streamable HTTP), z którego mogą korzystać:

- **sam Jarvis** — `fpl_agent` z `FPL_TOOLS_SOURCE=mcp` bierze narzędzia z serwera zamiast
  z procesu;
- **Claude Code** — `claude mcp add --transport http …`;
- **aplikacje Claude znajomych** (web / desktop / mobile) — jako *custom connector*.

Dostęp mają tylko posiadacze **kluczy API** (`jvk_…`), a każdy klucz ma przypisane własne
domyślne `entry_id` / `league_id` — „moja drużyna” u znajomego to *jego* drużyna.

```
Claude (znajomi) ─┐                          ┌─ /mcp/fpl ──► narzędzia z TOOLS_BY_AGENT["fpl_agent"]
Claude Code ──────┼─► Caddy (TLS) ─► mcp:8010│     (klucz → klient → domyślne id FPL)
Jarvis (agent) ───┘   /mcp/*                 └─ /mcp/health
```

## Adresy

| Adres | Klucz |
|---|---|
| `POST https://<domena>/mcp/fpl` | nagłówek `Authorization: Bearer jvk_…` (albo `X-API-Key`) |
| `POST https://<domena>/mcp/fpl/jvk_…` | w ścieżce — dla klientów, które nie ustawiają nagłówków (custom connectors w aplikacjach Claude) |
| `GET https://<domena>/mcp/health` | bez klucza |

Błędy bramki: brak/zły klucz → **401**, klucz bez dostępu do serwera → **403**, nieznany
serwer → **404**. Serwer jest bezstanowy (`stateless_http`, odpowiedzi JSON) — każde
żądanie jest samodzielne, restart niczego nie gubi.

## Co jest wystawione

Tylko agenci z listy `EXPOSABLE` w `lib/runtime/mcp_server.py`. Lista jest **w kodzie, nie
w konfiguracji** — agentów właściciela (światła, kalendarz, pamięć, mapy, scheduler) nie da
się wystawić jedną zmienną env. Serwer `fpl` udostępnia wszystkie narzędzia `fpl_agent`
(docstringi = opisy narzędzi MCP, wszystkie tylko do odczytu) plus `whoami` (kto jest
właścicielem klucza i jakie ma domyślne id).

Dodanie kolejnego serwera (np. `finance`) = wpis w `EXPOSABLE` (+ ewentualnie `bind_client`,
jeśli narzędzia mają dane „właściciela”) i nazwa w `servers:` u klientów.

## Klucze API

Klienci są w `config/mcp_clients.yaml` (gitignored; wzór: `config/mcp_clients.example.yaml`,
ścieżka zmienialna przez `MCP_CLIENTS_CONFIG`). Plik trzyma **tylko SHA-256** klucza —
wyciek pliku nie daje dostępu. Zmiany są czytane w locie (mtime), bez restartu; zepsuty plik
zostawia w mocy poprzednią poprawną wersję (i loguje błąd), brak pliku = nikt nie wchodzi.

```yaml
clients:
  marek:
    name: Marek
    key_sha256: "3f1c…"
    servers: [fpl]
    rate_limit_per_minute: 30      # opcjonalnie (domyślnie MCP_RATE_LIMIT_PER_MINUTE=60)
    fpl:
      entry_id: 1234567            # jego drużyna FPL
      league_id: 765432            # jego domyślna mini-liga
```

Nowy klucz:

```bash
docker compose exec mcp poetry run python -m scripts.mcp_key new marek --name Marek --entry-id 1234567 --league-id 765432
```

Skrypt wypisuje klucz **raz** (nigdzie go nie zapisuje) i blok YAML do wklejenia.
Odwołanie: usuń blok albo ustaw `enabled: false`. Podgląd: `scripts.mcp_key list`.

### Kolejność rozstrzygania „mojej” drużyny / ligi (`fpl_agent/identity.py`)

1. jawny argument narzędzia (`entry_id`, `league_id`) — np. „pokaż drużynę rywala”;
2. domyślne id przypisane do klucza (serwer MCP wiąże je na czas wywołania);
3. `FPL_ENTRY_ID` / `FPL_LEAGUE_ID` z env — **tylko w procesie Jarvisa**, gdy nic nie jest
   związane. Na serwerze MCP związana tożsamość jest rozstrzygająca: klient bez `entry_id`
   dostaje wskazówkę „podaj entry_id”, nigdy drużynę właściciela.

## Bezpieczeństwo

- **Klucz w URL-u** to „hasło w linku”: działa wszędzie, ale trafia wszędzie tam, gdzie
  trafia URL. Dlatego: uvicorn działa **bez access logu**, Caddy nie ma dyrektywy `log`,
  aplikacja loguje wywołania po `client_id` (nigdy po kluczu), a klient Jarvisa redaguje
  `jvk_…` w logach i komunikatach. Gdy wariant z URL-em przestanie wystarczać, kolejny krok
  to OAuth (patrz niżej) — narzędzia się nie zmieniają.
- **Limit zapytań** per klient (okno 1 min, w pamięci procesu) chroni nieoficjalne API FPL.
- **Timeout** jednego wywołania: `MCP_TOOL_TIMEOUT_SECONDS` (domyślnie 90 s — skany lig).
- Błąd narzędzia wraca do klienta jako `ERROR: <tool> failed (<Typ>)` — bez treści wyjątku.
- Ochrona przed DNS rebinding w SDK jest wyłączona świadomie: każde żądanie wymaga klucza,
  a za proxy nagłówek `Host` to publiczna domena.

## Wdrożenie

`docker-compose.yml` ma usługę `mcp` (ten sam obraz, osobny proces — awaria tu nie dotyka
webhooka), a `Caddyfile` kieruje `/mcp/*` do `mcp:8010`. Postgres jest współdzielony, więc
cache FPL (referencje, skany lig) działa dla wszystkich klientów.

```bash
cp config/mcp_clients.example.yaml config/mcp_clients.yaml   # i wyczyść przykłady
docker compose up -d --build mcp caddy
curl https://ogarniacz.duckdns.org/mcp/health
```

Lokalnie: `poetry run python -m app.mcp_server` (port `MCP_PORT`, domyślnie 8010).

## Podłączanie klientów

**Claude Code** (nagłówek):

```bash
claude mcp add --transport http jarvis-fpl https://ogarniacz.duckdns.org/mcp/fpl --header "Authorization: Bearer jvk_..."
```

**Aplikacje Claude (znajomi):** na claude.ai (web/desktop) → *Settings → Connectors → Add
custom connector* → URL z kluczem w ścieżce `https://ogarniacz.duckdns.org/mcp/fpl/jvk_…`,
bez OAuth. Po dodaniu connector jest dostępny także w aplikacji mobilnej. Połączenie robią
serwery Anthropic, nie telefon — dlatego serwer musi być publiczny (jest, przez Caddy).

**Jarvis** (`.env`):

```
FPL_TOOLS_SOURCE=mcp
FPL_MCP_URL=http://mcp:8010/mcp/fpl      # w sieci compose, bez TLS
FPL_MCP_API_KEY=jvk_...                  # klient z entry_id/league_id właściciela
```

Silnik przed każdą turą odświeża listę zdalnych narzędzi (`refresh_mcp_toolsets`, równolegle
z ładowaniem historii; cache `MCP_TOOLS_TTL_SECONDS`=300 s). Gdy serwer nie odpowiada, lista
z poprzedniego odczytu zostaje, kolejna próba po `MCP_RETRY_SECONDS`=30 s, a jeśli listy nigdy
nie było — `fpl_agent` **wraca do narzędzi lokalnych** (warning w logu). Każde wywołanie to
krótkie połączenie HTTP (3 POST-y na wywołanie w obecnym SDK) — pomijalne przy czasach LLM.

Ograniczenie pilota: Jarvis łączy się jednym kluczem, więc w trybie `mcp` „moja drużyna”
oznacza drużynę z tego klucza dla wszystkich użytkowników Jarvisa — tak samo jak dziś
`FPL_ENTRY_ID` w trybie lokalnym. Per-użytkownik: przypisać id w profilu i przekazywać je
jawnie jako argument.

## Następne kroki

- **OAuth** zamiast klucza w URL-u (SDK `mcp` ma `AuthSettings`/`TokenVerifier`): ekran
  logowania prosi raz o klucz, a dalej idzie token — klucz nie żyje w URL-u.
- Kolejne serwery (`finance`, `weather`, `math`) — ich narzędzia nie używają `Ctx`.
- Per-użytkownik FPL w Jarvisie (patrz wyżej) i komenda admina `/mcp` (klienci, ostatnie
  wywołania, stan toolsetów).
