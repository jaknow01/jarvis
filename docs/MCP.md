# MCP — narzędzia Jarvisa jako niezależne serwery

Wszystkie narzędzia Jarvisa działają jako **osobne serwery MCP w osobnych publicznych
repozytoriach**. Jarvis jest jednym z ich klientów — obok Claude Code właściciela i aplikacji
Claude znajomych (FPL). Każdy serwer można postawić gdziekolwiek; Jarvis łączy się po HTTP.

```
github.com/jaknow01/
├── jarvis           ← harness: coordinator, composer, silnik tury, historia, pamięć,
│                      scheduler (+ watche), Messenger, admin. Klient MCP.
├── jarvis-mcp-kit   ← biblioteka: klucze API, klienci, X-On-Behalf-Of, limity, logi bez kluczy
├── mcp-fpl          ← FPL + strona /mcp/join + historia ligi (własna baza)   [publiczny]
├── mcp-finance      ← kursy, notowania, tickery, historia cen (własna baza)
├── mcp-weather      ← pogoda (OpenWeather) i prognoza (Open-Meteo)
├── mcp-news         ← wyszukiwanie newsów (Tavily)
├── mcp-math         ← dokładne obliczenia
├── mcp-maps         ← trasy + zapisane miejsca per gospodarstwo
├── mcp-calendar     ← kalendarz iCloud per użytkownik
├── mcp-home         ← oświetlenie Tuya per gospodarstwo; działa W DOMU
└── (mcp-web)        ← przeglądanie stron — po zmergowaniu gałęzi web-browsing
```

Spec i decyzje: `openspec/changes/extract-tools-to-mcp-servers/`.

## Jarvis jako klient

**Sub-agenci z konfiguracji** — `config/subagents.yaml` (commitowany, bez sekretów):

```yaml
subagents:
  maps_agent:
    description: "…"          # co coordinator wie o tym sub-agencie
    url_env: MCP_MAPS_URL     # adres serwera
    key_env: MCP_MAPS_KEY     # klucz Jarvisa na tym serwerze (`trusted: true`)
    identity: group           # none | user | group → X-On-Behalf-Of
    persona: ""               # opcjonalnie, ponad przewodnik serwera
    max_calls_per_turn: {}    # opcjonalne budżety wywołań na turę
```

Nazwy sub-agentów (`fpl_agent`, `iot_operator`, …) zostały — rostery w `principals.yaml`
i profile modeli działają bez zmian. Dodanie serwera = wpis w tym pliku + URL/klucz w `.env`.

- **Przewodnik po narzędziach jest przy serwerze**: prompt sub-agenta = preambuła środowiska +
  `instructions` serwera + `persona` + reguły narzędzi. Zmiana na serwerze zmienia agenta.
- **Listing w cache** (`MCP_TOOLS_TTL_SECONDS`=300) odświeżany przed turą, równolegle z
  historią. Serwer, który nigdy nie odpowiedział → sub-agenta **nie ma w tej turze**; serwer,
  który padł później → wywołania zwracają `ERROR: … unavailable` i odpowiedź mówi, że danych
  brak. Nie ma już narzędzi lokalnych.
- **Tożsamość**: silnik wiąże na turę `user_id` i gospodarstwo (`group_id`); klient MCP
  wysyła `X-On-Behalf-Of` wg `identity`. Serwer uznaje nagłówek tylko od klucza `trusted`.
  Mapy i oświetlenie → gospodarstwo (wspólny „Dom” i wspólne lampy), kalendarz → osoba.
- **Watche** (`scheduler_agent/watch_sources.py`) wołają te same narzędzia przez MCP — te same
  identyfikatory źródeł (`finance.fx`, `fpl.live`, …), właściciel watcha jako principal.
- **`/mcp`** (admin): stan serwerów; `/mcp keys` i `/mcp revoke <ID>` — klucze FPL z
  samoobsługi (narzędzia admina `mcp-fpl`; klucz Jarvisa tam musi mieć `admin: true`).
- **`POST /internal/notify`** (Bearer `INTERNAL_NOTIFY_TOKEN`): serwery wysyłają tu krótkie
  komunikaty dla właściciela (np. `mcp-fpl` przy każdym nowym kluczu) → Messenger.
- **Evale** nie potrzebują serwerów: `evals/fixtures/mcp_tools.json` to snapshot listingów
  (odśwież: `poetry run python -m evals.snapshot_tools`), wywołania zwracają fixtury.

## Wdrożenie dziś: serwer domowy (docker compose)

`docker-compose.yml` podnosi Jarvisa i wszystkie serwery z obrazów `ghcr.io/jaknow01/mcp-*`.
Konfiguracja serwerów leży w `mcp-config/<serwer>/` (gitignored):

```
mcp-config/
├── fpl/       .env (DATABASE_URL, FPL_LEAGUES, MCP_PUBLIC_URL, JOIN_NOTIFY_*), clients.yaml
├── finance/   .env (DATABASE_URL, OBSERVATIONS_*), clients.yaml
├── weather/   .env (OPENWEATHER_API_KEY), clients.yaml
├── news/      .env (TAVILY_API_KEY), clients.yaml
├── math/      clients.yaml
├── maps/      .env (GOOGLE_MAPS_API_KEY), clients.yaml, places.yaml
├── calendar/  .env (ICLOUD_CALDAV_USER, ICLOUD_APP_PASSWORD), clients.yaml, accounts.yaml
└── home/      clients.yaml, home.yaml       (urządzenia z data/smart_device_data/)
```

1. **Klucze Jarvisa** — dla każdego serwera (CLI jest w każdym obrazie):
   `docker compose run --rm --no-deps --entrypoint jarvis-mcp-keys mcp-<serwer> new jarvis --servers <serwer> --trusted`
   (dla `fpl` dodaj `--admin`). Wydrukowany blok wklej do `mcp-config/<serwer>/clients.yaml`,
   a klucz do `.env` Jarvisa jako `MCP_<SERWER>_KEY`. Twój stary klucz FPL i klucze znajomych z `/mcp/join`
   trzeba wydać ponownie (stara tabela `mcp_api_keys` jest w bazie Jarvisa).
2. **Dane osobiste**: `mcp-maps import-legacy data/maps_data/maps_memory.json --principals <id gospodarstwa>`
   → `places.yaml`; `accounts.yaml` i `home.yaml` wg przykładów w repo serwerów.
   Id gospodarstwa to `group_id` z `principals` (np. `default`) — zobacz `/group list`.
3. **Bazy**: `deploy/postgres/README.md` (osobna baza i login dla FPL i finansów), potem
   `docker compose run --rm mcp-fpl backfill` i `docker compose run --rm mcp-finance backfill`.
4. **Kolektory** — crontab hosta:
   ```
   0 4 * * *    cd /ścieżka/jarvis && docker compose run --rm mcp-fpl collect
   15 */3 * * * cd /ścieżka/jarvis && docker compose run --rm mcp-fpl warm-cache
   0 23 * * *   cd /ścieżka/jarvis && docker compose run --rm mcp-finance collect
   ```
5. `docker compose up -d` — Caddy wystawia na zewnątrz tylko `/mcp/fpl` i `/mcp/join`;
   `/mcp` w Messengerze pokazuje, czy wszystkie serwery odpowiadają.

## Docelowo: k3s

- Serwery: `deploy/kubernetes.yaml` w każdym repo (Deployment + Service; FPL i finance z
  CronJobami; FPL z Ingressem). Sekrety per serwis jako k8s Secret.
- Postgres: jedna instancja, bazy/loginy jak w `deploy/postgres/`.
- Jarvis: `deploy/k8s/jarvis.yaml` (namespace `jarvis`, etykieta `app: jarvis` — wymagana
  przez NetworkPolicy bramki domowej).
- Oświetlenie: `mcp-home` zostaje na Raspberry (docker, sieć hosta), klaster dochodzi do niego
  przez pod `home-gateway` z WireGuardem do routera; router wpuszcza tylko Pi:8010, a
  NetworkPolicy tylko Jarvisa — patrz README `mcp-home`.

## Bezpieczeństwo (skrót)

- Klucze tylko jako SHA-256 po stronie serwerów; klucz w URL-u działa, ale logi go nie widzą
  (access log wyłączony, redakcja `jvk_…` w loggerach — także `httpx2` w Jarvisie).
- Sekrety zewnętrznych API są wyłącznie przy serwerach; Jarvis ma tylko klucze MCP.
- Serwery z danymi osobistymi zwracają aliasy/nazwy, nigdy adresów czy kluczy urządzeń, i
  dane wyłącznie danego principala; nieznany principal dostaje nic.
- Błędy z narzędzi wracają jako typ wyjątku, bez treści (która potrafiła zawierać URL z kluczem).
