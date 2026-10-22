## ADDED Requirements

### Requirement: League members get a key without the owner

`mcp-fpl` SHALL serve a `/mcp/join` page where a person enters an FPL entry id, team name or
manager name; if it matches exactly one manager of a configured league, the server SHALL
issue an API key linked to that entry and league and show it once together with a
ready-to-paste connector URL and a Claude Code command. Name matching SHALL be case- and
diacritic-insensitive. No proof of ownership is required (league tables are public).

#### Scenario: Member gets a key

- **WHEN** someone enters the team name of a manager in the configured league
- **THEN** the page shows a new key, the connector URL containing it, and the CLI command,
  and the key's `whoami` reports that entry and league as defaults

#### Scenario: Not a league member

- **WHEN** the entered id or name matches no manager of the configured leagues
- **THEN** no key is issued and the page explains how to find the entry id

#### Scenario: Ambiguous name

- **WHEN** the entered text matches several managers
- **THEN** the page lists them as choices and issues nothing until one is chosen

### Requirement: One active key per entry, replaced only after confirmation

The server SHALL keep at most one active self-service key per FPL entry. Requesting a key for
an entry that has one SHALL first show a confirmation; confirming SHALL revoke the old key
and issue a new one.

#### Scenario: Lost key

- **WHEN** a member requests a key again and confirms the replacement
- **THEN** a new key is shown and the previous key is rejected from then on

#### Scenario: Accidental request

- **WHEN** a key is requested for an entry that already has one and the person does not
  confirm
- **THEN** the existing key keeps working and no new key is issued

### Requirement: The owner is notified and can revoke

The server SHALL send a notification for every issued key (manager, team, entry id, league,
whether it replaced a key) to a configured webhook URL, and SHALL provide a way for the owner
to list and revoke self-service keys by entry id.

#### Scenario: Notification on issue

- **WHEN** a key is issued
- **THEN** the configured webhook receives a message naming the entry and how to revoke it

#### Scenario: Revocation

- **WHEN** the owner revokes the key of entry 102
- **THEN** requests with that key are rejected within the key-store refresh interval

### Requirement: The page resists abuse and leaks nothing

The page SHALL limit attempts per client IP per hour, SHALL HTML-escape every user-
controlled string (team and manager names), and SHALL send the key page with
`Cache-Control: no-store`, `Referrer-Policy: no-referrer` and a CSP without external
resources.

#### Scenario: Malicious team name

- **WHEN** a league member's team name contains `<script>`
- **THEN** it is rendered as text, not executed
