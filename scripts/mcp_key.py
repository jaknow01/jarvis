"""Generate and inspect API keys for the public MCP servers (see docs/MCP.md).

    poetry run python -m scripts.mcp_key new marek --name Marek --entry-id 1234567 --league-id 765432
    poetry run python -m scripts.mcp_key hash jvk_...        # SHA-256 of an existing key
    poetry run python -m scripts.mcp_key list                # clients in config/mcp_clients.yaml

`new` prints the key ONCE plus a YAML block to paste into config/mcp_clients.yaml under
`clients:`. Only the hash goes into the file; hand the key to its holder privately. To
revoke, delete the client's block (or set `enabled: false`) — the server picks the change
up without a restart.
"""

import argparse

import yaml

from lib.runtime import McpClientRegistry, MCP_EXPOSABLE, hash_mcp_key, new_mcp_key


def _cmd_new(args) -> None:
    key = new_mcp_key()
    servers = args.servers.split(",") if args.servers else ["fpl"]
    unknown = [s for s in servers if s not in MCP_EXPOSABLE]
    if unknown:
        raise SystemExit(f"error: unknown server(s) {unknown}; exposable: {sorted(MCP_EXPOSABLE)}")
    block: dict = {"name": args.name or args.client_id, "key_sha256": hash_mcp_key(key), "servers": servers}
    fpl = {k: v for k, v in (("entry_id", args.entry_id), ("league_id", args.league_id)) if v is not None}
    if fpl:
        block["fpl"] = fpl
    print("API key (shown once — give it to the client, it is not stored anywhere):\n")
    print(f"  {key}\n")
    print("Paste under `clients:` in config/mcp_clients.yaml:\n")
    print("\n".join("  " + line for line in yaml.safe_dump({args.client_id: block}, sort_keys=False).splitlines()))


def _cmd_hash(args) -> None:
    print(hash_mcp_key(args.key))


def _cmd_list(_args) -> None:
    registry = McpClientRegistry(known_servers=set(MCP_EXPOSABLE))
    clients = registry.clients()
    if not clients:
        print(f"no enabled clients in {registry.path}")
    for c in clients:
        print(f"{c.client_id:<16} {c.name:<20} servers={','.join(sorted(c.servers))} "
              f"key=…{c.key_sha256[-8:]} {c.settings or ''}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_new = sub.add_parser("new", help="generate a key for a new client")
    p_new.add_argument("client_id")
    p_new.add_argument("--name")
    p_new.add_argument("--servers", help="comma list (default: fpl)")
    p_new.add_argument("--entry-id", type=int, help="the client's FPL manager id")
    p_new.add_argument("--league-id", type=int, help="the client's default FPL mini-league id")
    p_new.set_defaults(func=_cmd_new)
    p_hash = sub.add_parser("hash", help="print the SHA-256 of a key")
    p_hash.add_argument("key")
    p_hash.set_defaults(func=_cmd_hash)
    sub.add_parser("list", help="list enabled clients").set_defaults(func=_cmd_list)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
