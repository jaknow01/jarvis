# MCP — narzędzia Jarvisa jako niezależne serwery

Wszystkie narzędzia Jarvisa działają jako **osobne serwery MCP w osobnych publicznych
repozytoriach**. Jarvis jest jednym z ich klientów — obok Claude Code właściciela i aplikacji
Claude znajomych (FPL). Każdy serwer można postawić gdziekolwiek; Jarvis łączy się po HTTP.

```
github.com/jaknow01/
├── jarvis           ← harness: coordinator, composer, silnik tury, historia, pamięć,
│                      scheduler (+ watche), Messenger, admin. Klient MCP.
├── jarvis-mcp-kit   ← biblioteka: klucze API, klienci, X-On-Behalf-Of, limity, logi bez kluczy
├── mcp-fpl          ← FPL + strona /join + historia ligi (własna baza)       [publiczny]
├── mcp-finance      ← kursy, notowania, tickery, historia cen (własna baza)
├── mcp-weather      ← pogoda (OpenWeather) i prognoza (Open-Meteo)
├── mcp-news         ← wyszukiwanie newsów (Tavily)
├── mcp-math         ← dokładne obliczenia
├── mcp-maps         ← trasy + zapisane miejsca per gospodarstwo
├── mcp-calendar     ← kalendarz iCloud per użytkownik
├── mcp-home         ← oświetlenie Tuya per gospodarstwo; działa W DOMU
├── homelab-gitops   ← (prywatne) manifesty k3s dla Argo CD
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

## Wdrożenie: k3s przez GitOps (Argo CD)

Klaster czyta stan z **prywatnego repo `jaknow01/homelab-gitops`** (app-of-apps dla Argo CD,
Kustomize na każdą usługę, sekrety jako Sealed Secrets). Szczegóły i bootstrap: README tego repo.

```
kod (publiczne repo) ──tag v*──► GitHub Actions ──► ghcr.io/jaknow01/<repo>:<tag> (prywatny)
                                       └──commit "newTag"──► homelab-gitops ──► Argo CD ──► k3s
```

- **Namespace `mcp`**: wszystkie serwery poza oświetleniem; `mcp-fpl` i `mcp-finance` mają
  **własne kontenery Postgresa** (StatefulSet + PVC) i CronJoby (`collect`, `warm-cache`).
- **Namespace `jarvis`**: Jarvis z własnym Postgresem oraz `home-gateway` (WireGuard do routera
  domowego → `mcp-home` na Raspberry; NetworkPolicy wpuszcza tylko Jarvisa).
- **Wejście z internetu: Cloudflare Tunnel** (cloudflared w klastrze, hostnames w panelu):
  `mcp-fpl.1nogaulica-2czerwonydywan.win` → `mcp-fpl.mcp.svc` (Claude znajomych: `/mcp/<klucz>`,
  strona kluczy: `/join`) oraz `jarvis.1nogaulica-2czerwonydywan.win` ze ścieżką `^/webhook`
  (Messenger). Reszta tylko w klastrze; `/internal/notify` nigdy nie jest publiczne.
- **Jarvis łączy się z serwerami po DNS klastra** (`http://mcp-<x>.mcp.svc.cluster.local/mcp/<x>`),
  klucze `MCP_<X>_KEY` są w sekrecie `jarvis-env`.
- **Wydanie**: tag w repo usługi → CI buduje obraz i podbija tag w repo GitOps (sekret
  `GITOPS_TOKEN` w repo usługi) → Argo wdraża. Wycofanie = `git revert` w repo GitOps.

## Lokalnie: docker compose

`docker-compose.yml` podnosi Jarvisa i wszystkie serwery z obrazów GHCR na jednej maszynie
(do testów). Konfiguracja serwerów w `mcp-config/<serwer>/` (gitignored: `.env`, `clients.yaml`,
`places.yaml`, `accounts.yaml`, `home.yaml`), bazy wg `deploy/postgres/README.md`, kolektory
przez `docker compose run --rm mcp-fpl collect` (cron). Klucz Jarvisa dla serwera:
`docker compose run --rm --no-deps --entrypoint jarvis-mcp-keys mcp-<serwer> new jarvis --servers <serwer> --trusted`.

## Bezpieczeństwo (skrót)

- Klucze tylko jako SHA-256 po stronie serwerów; klucz w URL-u działa, ale logi go nie widzą
  (access log wyłączony, redakcja `jvk_…` w loggerach — także `httpx2` w Jarvisie).
- Sekrety zewnętrznych API są wyłącznie przy serwerach; Jarvis ma tylko klucze MCP.
- Serwery z danymi osobistymi zwracają aliasy/nazwy, nigdy adresów czy kluczy urządzeń, i
  dane wyłącznie danego principala; nieznany principal dostaje nic.
- Błędy z narzędzi wracają jako typ wyjątku, bez treści (która potrafiła zawierać URL z kluczem).
