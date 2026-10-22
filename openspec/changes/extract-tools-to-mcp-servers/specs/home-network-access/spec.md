## ADDED Requirements

### Requirement: The home server stays outside the cluster

`mcp-home` SHALL run on a device inside the home network (the Raspberry Pi, plain Docker),
reach smart devices over the home LAN, and SHALL NOT be a node of, or managed by, the k3s
cluster.

#### Scenario: Lights controlled from the home LAN

- **WHEN** `mcp-home` turns a light on
- **THEN** the command goes from the Pi to the device over the local network

### Requirement: The cluster reaches home only through a dedicated WireGuard gateway

The cluster SHALL reach `mcp-home` through a `home-gateway` pod that holds a WireGuard peer
of the home router and forwards to the Pi's MCP port, exposed in-cluster as a ClusterIP
service. Jarvis SHALL address it like any other in-cluster MCP server.

#### Scenario: Jarvis calls the home server

- **WHEN** Jarvis calls the `mcp-home` service URL inside the cluster
- **THEN** the request travels over the WireGuard tunnel to the Pi and the response returns
  the same way

### Requirement: Least privilege on both ends

The home router SHALL allow the cluster's WireGuard peer to reach only the Pi's MCP port; a
NetworkPolicy SHALL allow only Jarvis pods to connect to the `home-gateway`; and `mcp-home`
SHALL still require Jarvis's API key.

#### Scenario: Tunnel cannot reach other home devices

- **WHEN** traffic from the cluster peer targets any home address other than the Pi's MCP
  port
- **THEN** the router drops it

#### Scenario: Other pods cannot use the gateway

- **WHEN** the `mcp-fpl` pod tries to connect to the `home-gateway` service
- **THEN** the connection is refused by the NetworkPolicy

#### Scenario: Tunnel without key

- **WHEN** a request arrives at `mcp-home` over the tunnel without a valid key
- **THEN** it is rejected with 401

### Requirement: Home outage is contained

When the home link is down, the home sub-agent's calls SHALL fail with an unavailable error
and every other sub-agent SHALL keep working.

#### Scenario: Home internet down

- **WHEN** the home router is offline and the user asks for the weather and to turn on a light
- **THEN** the weather is answered and the reply says the lights are unavailable
