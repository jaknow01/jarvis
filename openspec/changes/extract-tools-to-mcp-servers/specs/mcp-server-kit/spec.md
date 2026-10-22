## ADDED Requirements

### Requirement: Every request is authenticated by an API key

A server built on the kit SHALL accept a request to a tool endpoint only when it carries a
known, enabled API key, read from `Authorization: Bearer <key>`, `X-API-Key: <key>`, or the
key-in-URL form `/<server-path>/<key>`. Keys SHALL have a fixed recognizable prefix and be
compared by SHA-256 hash; the server SHALL NOT store plaintext keys.

#### Scenario: Missing or unknown key is rejected

- **WHEN** a request reaches a tool endpoint with no key, a malformed key, or a key whose
  hash is not registered
- **THEN** the server responds 401 with a `WWW-Authenticate: Bearer` header and runs no tool

#### Scenario: Key in the URL is accepted

- **WHEN** a client that cannot set headers calls `/<server-path>/<key>` with a valid key
- **THEN** the request is served as if the key had been sent in the `Authorization` header,
  and the key segment is stripped before the request reaches the MCP transport

### Requirement: Clients come from a hot-reloaded config file and an optional key store

The kit SHALL resolve a key to a client from two sources: a YAML clients file (hashes,
display name, enabled flag, rate limit, trust flag, per-server settings) re-read when its
mtime changes, and an optional store of issued keys refreshed at a configurable interval
(default 15 s). A malformed clients file SHALL keep the last valid version in force; a
missing file SHALL authenticate no file-based client.

#### Scenario: Revoking a key in the file takes effect without restart

- **WHEN** the owner deletes a client's block (or sets `enabled: false`) in the clients file
- **THEN** the next request with that key is rejected with 401, without restarting the server

#### Scenario: Broken file does not open or close the door

- **WHEN** the clients file is edited into invalid YAML
- **THEN** keys valid before the edit keep working, an error is logged, and no new key is
  accepted until the file is fixed

### Requirement: Trusted callers may act on behalf of a user

The kit SHALL expose the acting principal of a call as the key's own client unless the key
is marked `trusted: true` and the request carries `X-On-Behalf-Of: <principal_id>` (a user or
household group id chosen by the caller), in which case the acting principal SHALL be that
id. An `X-On-Behalf-Of` header from a non-trusted key SHALL be ignored.

#### Scenario: Jarvis forwards the end user

- **WHEN** Jarvis's trusted key calls a tool with `X-On-Behalf-Of: ala`
- **THEN** the tool sees `ala` as the acting principal

#### Scenario: A friend cannot impersonate

- **WHEN** a non-trusted key sends `X-On-Behalf-Of: owner`
- **THEN** the header is ignored and the tool sees the key's own client as the acting
  principal

### Requirement: Per-client rate limiting

The kit SHALL limit tool calls per client within a sliding one-minute window, using the
client's own limit when set and a server default otherwise, and SHALL answer an over-limit
call with a tool error rather than running the tool.

#### Scenario: Over the limit

- **WHEN** a client with a limit of 30 calls/minute makes its 31st call within a minute
- **THEN** that call returns an error result saying the rate limit was exceeded

### Requirement: Keys never reach logs

The kit SHALL disable HTTP access logging and SHALL log each tool call by client id, tool
name and duration only. Error results returned to callers SHALL NOT include exception
messages or tracebacks.

#### Scenario: Key-in-URL request is logged safely

- **WHEN** a call is made with the key in the URL
- **THEN** no log line written by the server contains the key

### Requirement: Tool functions are served over Streamable HTTP

The kit SHALL serve a set of documented async tool functions as an MCP server over stateless
Streamable HTTP with JSON responses, using each function's docstring as its description and
its signature as its input schema, together with server-level `instructions`, read-only
annotations where declared, and a `whoami` tool reporting the caller's client and defaults.
It SHALL expose an unauthenticated `GET /health` endpoint.

#### Scenario: A client lists tools

- **WHEN** an authenticated client calls `tools/list`
- **THEN** it receives every registered tool with its description and JSON schema, plus
  `whoami`, and the server's `instructions` on initialization

#### Scenario: Liveness probe

- **WHEN** a probe requests `GET /health` without a key
- **THEN** the server answers 200 with its name and status
