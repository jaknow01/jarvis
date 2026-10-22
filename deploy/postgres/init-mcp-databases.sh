#!/bin/sh
# Per-service databases for the stateful MCP servers (docs/MCP.md): each gets its own
# database and login, and no login can connect to another service's database.
#
# Runs automatically on a FRESH postgres volume (docker-entrypoint-initdb.d). For an
# existing volume run it once by hand (see deploy/postgres/README.md). Passwords come from
# MCP_FPL_DB_PASSWORD / MCP_FINANCE_DB_PASSWORD (defaults are for local use only).
set -eu
FPL_PW="${MCP_FPL_DB_PASSWORD:-mcp_fpl}"
FIN_PW="${MCP_FINANCE_DB_PASSWORD:-mcp_finance}"
DB_USER="${POSTGRES_USER:-jarvis}"

psql -v ON_ERROR_STOP=1 --username "$DB_USER" --dbname postgres \
     -v fpl_pw="$FPL_PW" -v fin_pw="$FIN_PW" -v owner="$DB_USER" <<'SQL'
SELECT format('CREATE ROLE mcp_fpl LOGIN PASSWORD %L', :'fpl_pw')
  WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mcp_fpl') \gexec
SELECT format('CREATE ROLE mcp_finance LOGIN PASSWORD %L', :'fin_pw')
  WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mcp_finance') \gexec
SELECT 'CREATE DATABASE mcp_fpl OWNER mcp_fpl'
  WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'mcp_fpl') \gexec
SELECT 'CREATE DATABASE mcp_finance OWNER mcp_finance'
  WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'mcp_finance') \gexec

-- Only each database's owner may connect (the superuser always can).
REVOKE CONNECT ON DATABASE mcp_fpl FROM PUBLIC;
REVOKE CONNECT ON DATABASE mcp_finance FROM PUBLIC;
SELECT format('REVOKE CONNECT ON DATABASE %I FROM PUBLIC', :'owner') \gexec
SQL
echo "mcp_fpl / mcp_finance databases ready"
