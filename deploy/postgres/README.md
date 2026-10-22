# Postgres for Jarvis and the stateful MCP servers

One Postgres instance, one database + login per service:

| Database | Login | Used by |
|---|---|---|
| `jarvis` | `jarvis` | Jarvis: conversations, memory, scheduled jobs, principals |
| `mcp_fpl` | `mcp_fpl` | mcp-fpl: reference/league caches, daily league history, self-service keys |
| `mcp_finance` | `mcp_finance` | mcp-finance: daily observations |

`init-mcp-databases.sh` creates the two service databases and logins and revokes `CONNECT`
from `PUBLIC`, so a service's login cannot open another service's database.

- **Fresh volume:** docker-compose mounts it into `/docker-entrypoint-initdb.d`, so it runs on
  first start.
- **Existing volume** (your current deployment): run it once:

  ```bash
  docker compose exec -T -e MCP_FPL_DB_PASSWORD=... -e MCP_FINANCE_DB_PASSWORD=... postgres sh < deploy/postgres/init-mcp-databases.sh
  ```

Then set in `mcp-config/fpl/.env`: `DATABASE_URL=postgresql://mcp_fpl:<pw>@postgres:5432/mcp_fpl`
(and likewise for finance), and fill the history once:

```bash
docker compose run --rm mcp-fpl backfill
docker compose run --rm mcp-finance backfill
```

The old `fpl_*`, `observations` and `mcp_api_keys` tables in the `jarvis` database are no
longer used; drop them once the new setup has run cleanly for a while.
