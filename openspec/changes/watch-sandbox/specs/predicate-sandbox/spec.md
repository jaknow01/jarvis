## ADDED Requirements

### Requirement: Predicate calling convention

The sandbox SHALL execute a predicate expressed as Starlark source that defines a function
`check(data) -> bool`, invoked by the host with the fetched data. The sandbox SHALL inject a
fixed set of host helper functions (at minimum a safe path accessor such as
`get(data, "a.b.c", default)`) into the predicate's environment. The predicate MUST NOT be
required to import anything or perform I/O.

#### Scenario: A def check predicate is evaluated

- **WHEN** the sandbox is given source defining `def check(data): return data["status"] == "HT"` and data `{"status": "HT"}`
- **THEN** it returns `true`

#### Scenario: Injected helper is callable from the predicate

- **WHEN** a predicate calls the injected `get(data, "a.b", default)` helper on nested/missing data
- **THEN** the helper resolves the path or returns the default, without the predicate importing anything

### Requirement: Two-mode contract (runtime and authoring)

The sandbox SHALL expose the same underlying evaluation in two response shapes: a **runtime**
mode returning `true`, `false`, or `unknown` (any failure collapses to `unknown` and the error
detail is discarded), and an **authoring** mode — `validate(code)` returning ok-or-error and
`evaluate(code, sample)` returning result-or-error — where failures carry the interpreter's
**error message** so a caller can repair the code.

#### Scenario: Runtime failure collapses to unknown

- **WHEN** runtime evaluation of a predicate raises or fails to compile
- **THEN** the result is `unknown` and no error text is surfaced to the runtime caller

#### Scenario: Authoring failure returns the error text

- **WHEN** `validate(code)` is called on code that does not compile
- **THEN** it returns an error result containing the interpreter's diagnostic message

#### Scenario: Authoring smoke evaluation returns a concrete result

- **WHEN** `evaluate(code, sample)` is called with valid code and sample data
- **THEN** it returns the boolean result (usable to show the user what the predicate currently yields)

### Requirement: Deterministic, hermetic execution

The sandbox SHALL run predicates in a deterministic, hermetic language runtime (Starlark) in
which predicate code cannot access the filesystem, network, system clock, host process, or any
host object not explicitly injected. Execution SHALL be bounded so that non-terminating or
runaway predicate code cannot run unbounded.

#### Scenario: Predicate cannot reach the network or filesystem

- **WHEN** a predicate attempts any I/O, import, or host access beyond injected helpers and `data`
- **THEN** the attempt fails within the runtime rather than reaching the host

#### Scenario: Runaway predicate is bounded

- **WHEN** a predicate would execute without terminating (or beyond configured step/time limits)
- **THEN** the evaluation is stopped and reported as a failure rather than hanging the service

### Requirement: Isolated warm service over a local socket

The sandbox SHALL run as a long-lived (warm) service reachable only over a local unix socket on
the shared host, using a fresh evaluation environment per call. The execution environment MUST
be isolated with, at minimum, no network access, a non-root user, a read-only root filesystem,
and resource limits (memory, process count). These are required **properties**; the concrete
configuration that realizes them is defined privately (see the hardening-privacy requirement).

#### Scenario: Communication only via the local socket

- **WHEN** the runtime evaluates a watch
- **THEN** it reaches the sandbox exclusively over the local unix socket, with no network path involved

#### Scenario: Fresh environment per evaluation

- **WHEN** two predicates are evaluated in sequence by the warm service
- **THEN** neither can observe state left behind by the other

### Requirement: Sandbox unavailability maps to unknown

When the sandbox service is unreachable or fails to respond, the runtime read SHALL resolve to
`unknown` (never `false`), so a watch skips and retries rather than mis-firing or dying.

#### Scenario: Service down during a poll

- **WHEN** a watch is polled while the sandbox service is unavailable
- **THEN** the read resolves to `unknown`, the watch does not fire, and it remains active to retry

### Requirement: Hardening configuration kept out of the public repository

The concrete security configuration of the sandbox SHALL be maintained privately under the
git-ignored `sandbox/` directory (container/network settings, capability and seccomp policy,
resource limits, supervision unit, and the layering that realizes isolation) and MUST NOT be
committed to the public repository. The public repository SHALL contain only the contract client
and the socket protocol definition.

#### Scenario: Public repo excludes the hardening method

- **WHEN** the public repository is inspected
- **THEN** it contains the contract client and protocol, but not the concrete container/network/seccomp
  configuration, which lives only under the git-ignored `sandbox/`
