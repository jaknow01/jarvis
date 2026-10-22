## ADDED Requirements

### Requirement: Evals run against a committed snapshot of server tool schemas

The eval harness SHALL build mock sub-agent tools from a schema snapshot file committed to
the Jarvis repository — each server's tools (name, description, input schema) and
`instructions` — so benchmarks run without any MCP server. A command SHALL refresh the
snapshot from live servers.

#### Scenario: Benchmark offline

- **WHEN** the eval suite runs with no MCP server reachable
- **THEN** every sub-agent is built from the snapshot and the model sees the same tools it
  would see in production

#### Scenario: Refresh snapshot

- **WHEN** the owner runs the snapshot command with server URLs and keys available
- **THEN** the snapshot file is rewritten from the servers' current listings

### Requirement: Mock calls return fixtures and are recorded

A mocked tool call SHALL return a deterministic fixture for that tool and SHALL be recorded
for routing/execution metrics exactly as before extraction, without side effects or network
access.

#### Scenario: Routing metric

- **WHEN** a benchmark question about FPL is evaluated
- **THEN** the capture shows the call to the FPL tool with its arguments and the fixture
  result, and no request leaves the process
