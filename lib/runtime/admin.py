"""Admin / ops command surface — a technical window into the running service.

Special messages prefixed with ``/`` let the owner inspect and nudge the live program
from the phone (Messenger) or the REPL, without SSHing into the Pi to read logs. The
router is intercepted **before** the agent stack (in ``app/webhook.py`` and the REPL
loop), so these commands still work when the coordinator/LLM is exactly the thing that
is broken — and they cost nothing (no LLM call) and cannot hallucinate.

Access is implicitly owner-only: the webhook already restricts inbound to
``MESSENGER_ALLOWED_SENDER_ID`` and the REPL is local.

Output is formatted for a **mobile chat bubble**: plain text (Messenger renders no
Markdown), short lines, emoji as visual anchors, blank lines between sections, no tables.
Long replies are chunked to 2000 chars by ``send_message`` downstream.

Add a command by writing an ``async def _cmd_x(args, *, conversation_id, channel) -> str``
handler and registering it in ``_COMMANDS`` with its group/usage/summary — ``/help`` is
generated from that metadata, so it never drifts.
"""

from __future__ import annotations

import glob
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional

import lib.runtime.diag as diag
import lib.runtime.observations_runner as observations_runner
import lib.runtime.progress as progress
import lib.runtime.scheduler_runner as scheduler_runner
import lib.runtime.tracing as tracing
from lib.core import agent_enabled, llm, active_profile, available_profiles, ModelProfileError
from lib.core.registry import AGENTS
from lib.domain import (
    conversations,
    describe_schedule,
    memory,
    now_tz,
    store,
    tz,
    principals,
    agent_config,
    resolve_profile,
    is_admin,
    new_user_id,
    new_group_id,
    DEFAULT_GROUP_ID,
    DEFAULT_GROUP_NAME,
    GROUP_SCOPE,
    USER_SCOPE,
    mcp_keys,
)

logger = logging.getLogger(__name__)

PREFIX = "/"


# =============================== formatting helpers ================================

def _fmt_delta(seconds: float) -> str:
    """Compact human duration: '2h 14m', '3d 4h', '12m 3s', '45s'."""
    s = int(max(0, seconds))
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    if d:
        return f"{d}d {h}h"
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


def _now_str() -> str:
    return datetime.now(tz()).strftime("%Y-%m-%d %H:%M") + f" ({os.getenv('SCHEDULER_TIMEZONE') or 'Europe/Warsaw'})"


def _ago(dt: Optional[datetime]) -> str:
    if dt is None:
        return "—"
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return f"{_fmt_delta((now - dt).total_seconds())} temu"


def _when(iso: Optional[str]) -> str:
    """Render a stored ISO timestamp as 'za 5m' / '3m temu' relative to now."""
    if not iso:
        return "—"
    try:
        dt = datetime.fromisoformat(iso)
    except (ValueError, TypeError):
        return str(iso)
    now = now_tz()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=now.tzinfo)
    secs = (dt - now).total_seconds()
    return f"za {_fmt_delta(secs)}" if secs >= 0 else f"{_fmt_delta(-secs)} temu"


def _preview(text: str, limit: int = 60) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _parse_int(args: list[str], default: int, cap: int) -> int:
    for a in args:
        if a.isdigit():
            return max(1, min(cap, int(a)))
    return default


# ================================ health / liveness ===============================

async def _cmd_ping(args, *, conversation_id, channel) -> str:
    return f"🏓 pong — jestem tu.\n🕐 {_now_str()}"


async def _cmd_status(args, *, conversation_id, channel) -> str:
    lines = ["📊 STATUS", ""]

    # Service liveness / uptime.
    lines.append(f"🟢 Serwis: żyje — uptime {_fmt_delta(diag.uptime_seconds())}")

    # Scheduler loop.
    if not scheduler_runner.scheduler_enabled():
        sched = "🔴 wyłączony (SCHEDULER_ENABLED)"
    elif scheduler_runner.is_paused():
        sched = "⏸ wstrzymany (/scheduler resume)"
    elif scheduler_runner.scheduler_running():
        sched = f"🟢 działa (tick {scheduler_runner.tick_seconds()}s)"
    else:
        sched = "🔴 nie tyka (brak heartbeatu)"
    lines.append(f"   Scheduler: {sched}")
    try:
        active = store.active()
        watches = sum(1 for j in active if j.get("kind") == "watch")
        lines.append(f"   🔔 zadania: {len(active) - watches} · 👁 watche: {watches}")
    except Exception as e:
        lines.append(f"   ⚠️ zadania: błąd odczytu ({type(e).__name__})")

    # Database backend + reachability.
    lines.append(f"   Baza: {_probe_db()}")

    # Tracing + progress ping config.
    tr = "on (MLflow)" if tracing.tracing_enabled() and os.getenv("MLFLOW_TRACKING_URI") else "off"
    lines.append(f"   Tracing: {tr}")
    pp = f"on ({int(progress.ping_after_seconds())}s)" if progress.ping_enabled() else "off"
    lines.append(f"   Progress ping: {pp}")

    lines += ["", f"🕐 {_now_str()}"]
    return "\n".join(lines)


def _probe_db() -> str:
    try:
        from app.db.connection import is_configured
    except Exception:
        return "🟡 JSON (pliki data/)"
    if not is_configured():
        return "🟡 JSON (pliki data/)"
    try:
        from app.db.connection import connection

        with connection() as conn:
            conn.execute("SELECT 1")
        return "🟢 Postgres OK"
    except Exception as e:
        return f"🔴 Postgres błąd: {type(e).__name__}"


async def _cmd_version(args, *, conversation_id, channel) -> str:
    lines = ["🏷 WERSJA", ""]
    lines.append(f"⏱ uptime: {_fmt_delta(diag.uptime_seconds())}")
    lines.append(f"🚀 start: {diag.started_at().astimezone(tz()).strftime('%Y-%m-%d %H:%M')}")

    # Freshness of the live (bind-mounted) code — the reliable 'did my deploy land' signal
    # inside the container, where .git usually isn't available.
    paths = glob.glob("lib/**/*.py", recursive=True) + glob.glob("app/**/*.py", recursive=True)
    mtimes = [os.path.getmtime(p) for p in paths if os.path.exists(p)]
    if mtimes:
        newest = datetime.fromtimestamp(max(mtimes), tz=timezone.utc)
        lines.append(f"📦 kod: najnowszy plik {_ago(newest)}")

    sha, branch = _git_version()
    if sha:
        lines.append(f"🔖 git: {sha}" + (f" ({branch})" if branch else ""))
    else:
        lines.append("🔖 git: niedostępny (brak .git w kontenerze)")
    return "\n".join(lines)


def _git_version() -> tuple[str, str]:
    try:
        import subprocess

        sha = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=2
        )
        branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True, text=True, timeout=2
        )
        if sha.returncode == 0:
            return sha.stdout.strip(), (branch.stdout.strip() if branch.returncode == 0 else "")
    except Exception:
        pass
    return os.getenv("GIT_SHA", "")[:12], os.getenv("GIT_BRANCH", "")


async def _cmd_probe(args, *, conversation_id, channel) -> str:
    import asyncio

    import httpx

    async def reach(name: str, url: str, method: str = "GET") -> tuple[str, str]:
        try:
            async with httpx.AsyncClient(timeout=6.0, follow_redirects=True) as c:
                r = await c.request(method, url)
            return name, ("🟢 OK" if r.status_code < 400 else f"🔴 HTTP {r.status_code}")
        except Exception as e:
            return name, f"🔴 {type(e).__name__}"

    coros = [
        reach("Frankfurter (FX)", "https://api.frankfurter.app/latest"),
        reach("FPL", "https://fantasy.premierleague.com/api/bootstrap-static/", "HEAD"),
        reach("Yahoo Finance", "https://query1.finance.yahoo.com/v8/finance/chart/AAPL?range=1d&interval=1d"),
    ]
    static: list[tuple[str, str]] = []
    if os.getenv("OPENWEATHER_API_KEY"):
        coros.append(reach(
            "OpenWeather",
            f"https://api.openweathermap.org/data/2.5/weather?q=Warsaw&appid={os.getenv('OPENWEATHER_API_KEY')}",
        ))
    else:
        static.append(("OpenWeather", "⚪ brak klucza"))
    if os.getenv("GOOGLE_MAPS_API_KEY"):
        coros.append(reach(
            "Google Maps",
            f"https://maps.googleapis.com/maps/api/directions/json?origin=Warsaw&destination=Krakow&key={os.getenv('GOOGLE_MAPS_API_KEY')}",
        ))
    else:
        static.append(("Google Maps", "⚪ brak klucza"))
    # Tavily has no cheap GET probe; report key presence only (avoids burning quota).
    static.append(("Tavily (news)", "🔑 klucz jest" if os.getenv("TAVILY_API_KEY") else "⚪ brak klucza"))

    results = list(await asyncio.gather(*coros)) + static
    lines = ["🌐 API — dostępność", ""]
    lines += [f"• {name}: {stat}" for name, stat in results]
    return "\n".join(lines)


# ==================================== logs ========================================

def _latest_log() -> Optional[str]:
    files = glob.glob(os.path.join("logs", "*.log"))
    return max(files, key=os.path.getmtime) if files else None


def _tail(path: str, n: int) -> list[str]:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except Exception:
        return []
    return [ln.rstrip("\n")[:300] for ln in lines[-n:]]


async def _cmd_logs(args, *, conversation_id, channel) -> str:
    n = _parse_int(args, default=20, cap=80)
    path = _latest_log()
    if not path:
        return "📜 Brak plików logów."
    tail = _tail(path, n)
    header = f"📜 LOGI — ostatnie {len(tail)} linii\n(plik: {os.path.basename(path)})\n"
    return header + "\n".join(tail) if tail else header + "(pusty)"


_ERR_RE = re.compile(r"\[(ERROR|CRITICAL)\]|Traceback \(most recent call last\)|"
                     r"\b[A-Za-z_]+(Error|Exception)\b")


async def _cmd_errors(args, *, conversation_id, channel) -> str:
    n = _parse_int(args, default=10, cap=40)
    path = _latest_log()
    if not path:
        return "🚨 Brak plików logów."
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            matches = [ln.rstrip("\n")[:300] for ln in f if _ERR_RE.search(ln)]
    except Exception as e:
        return f"🚨 Nie mogę odczytać logu: {type(e).__name__}"
    if not matches:
        return f"✅ Brak błędów w bieżącym logu ({os.path.basename(path)})."
    tail = matches[-n:]
    return f"🚨 BŁĘDY — ostatnie {len(tail)}\n(plik: {os.path.basename(path)})\n" + "\n".join(tail)


async def _cmd_loglevel(args, *, conversation_id, channel) -> str:
    root = logging.getLogger()
    if not args:
        return f"🔊 Poziom logowania: {logging.getLevelName(root.level)}"
    level = args[0].upper()
    if level not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
        return f"⚠️ Nieznany poziom '{args[0]}'. Użyj: debug/info/warning/error."
    root.setLevel(getattr(logging, level))
    return f"✅ Poziom logowania ustawiony na {level}."


# ================================== scheduler =====================================

def _resolve_job(job_id: str) -> Optional[dict]:
    """Resolve a job by exact id or a unique id-prefix (mobile-friendly short ids)."""
    job = store.get(job_id)
    if job:
        return job
    matches = [j for j in store.all() if j.get("id", "").startswith(job_id)]
    return matches[0] if len(matches) == 1 else None


def _job_kind_tag(j: dict) -> str:
    """Explicit, plain-language kind marker so a cron is unmistakable from a watch."""
    kind = j.get("kind")
    if kind == "watch":
        return "👁 watch " + ("(powtarzalny)" if j.get("repeating") else "(jednorazowy)")
    if kind == "cron":
        return "🔁 cron (cykliczny)"
    return "⏰ jednorazowe"


def _render_job(j: dict) -> list[str]:
    out = [f"• {j['id'][:8]} — {_job_kind_tag(j)}", f"  {describe_schedule(j)}"]
    if j.get("kind") == "watch":
        lr = j.get("last_result")
        lr_s = "nieznany" if lr is None else ("spełniony ✅" if lr else "niespełniony")
        out.append(f"  warunek: {lr_s} · ⏭ {_when(j.get('next_run_at'))} · {j.get('status')}")
    else:
        out.append(f"  ⏭ {_when(j.get('next_run_at'))} · {j.get('status')}")
    out.append(f"  „{_preview(j.get('prompt', ''))}”")
    return out


async def _cmd_jobs(args, *, conversation_id, channel) -> str:
    show_all = bool(args) and args[0].lower() in ("all", "wszystkie", "-a")
    try:
        every = store.all()
    except Exception as e:
        return f"⚠️ Nie mogę odczytać zadań: {type(e).__name__}"
    active = [j for j in every if j.get("status") == "active"]
    inactive = [j for j in every if j.get("status") != "active"]

    if not active and not (show_all and inactive):
        return "📭 Brak aktywnych zadań." + ("" if show_all else f"\n(„{PREFIX}jobs all" + "” pokaże też zakończone)")

    lines = ["🗓 ZADANIA" + (" (wszystkie)" if show_all else " (aktywne)"), ""]
    lines.append(f"🟢 Aktywne ({len(active)}):")
    for j in active:
        lines += _render_job(j)
    if not active:
        lines.append("  (brak)")
    if show_all:
        lines += ["", f"⚪ Zakończone/wyłączone ({len(inactive)}):"]
        for j in inactive:
            lines += _render_job(j)
        if not inactive:
            lines.append("  (brak)")
        lines += ["", "ℹ️ Anulowane zadania są usuwane — nieobecność = anulowane. "
                      "status „done” = zadanie samo dobiegło końca (np. watch jednorazowy się wyzwolił)."]
    return "\n".join(lines)


async def _cmd_fire(args, *, conversation_id, channel) -> str:
    if not args:
        return f"🔥 Użycie: {PREFIX}fire <id-zadania>\n(id z {PREFIX}jobs, wystarczy prefiks)"
    job = _resolve_job(args[0])
    if not job:
        return f"❓ Nie znalazłem zadania „{args[0]}” (albo prefiks jest niejednoznaczny)."
    job_ret, reply = await scheduler_runner.fire_now(job["id"])
    if job_ret is None:
        return f"❓ Zadanie {job['id'][:8]} zniknęło."
    head = f"🔥 Odpalono {job['id'][:8]} — dostarczono na kanał zadania."
    if reply:
        return head + "\n\n📩 Odpowiedź:\n" + _preview(reply, 600)
    return head + "\n\n(brak treści odpowiedzi)"


async def _cmd_watch(args, *, conversation_id, channel) -> str:
    if not args:
        return f"👁 Użycie: {PREFIX}watch <id-watcha>"
    job = _resolve_job(args[0])
    if not job or job.get("kind") != "watch":
        return f"❓ Nie znalazłem watcha „{args[0]}”."
    lr = job.get("last_result")
    lr_s = "nieznany" if lr is None else ("true ✅" if lr else "false")
    return "\n".join([
        f"👁 WATCH {job['id'][:8]}",
        "",
        f"źródło: {job.get('source')}",
        f"co {job.get('poll_interval')}s",
        f"ostatni wynik: {lr_s}",
        f"następny sprawdzian: {_when(job.get('next_run_at'))}",
        f"status: {job.get('status')}",
        f"„{_preview(job.get('prompt', ''), 100)}”",
    ])


async def _cmd_stop(args, *, conversation_id, channel) -> str:
    """Interrupt the turn currently being processed for THIS conversation.

    Cancels the in-flight ``handle_message`` task (registered by the transport) — use it
    when a turn is taking too long. Only affects live user turns for this conversation;
    scheduled jobs are not touched.
    """
    n = diag.cancel_running(conversation_id)
    if n:
        return "⏹ Przerywam bieżące zadanie."
    return "ℹ️ Nic teraz nie przetwarzam w tej rozmowie."


async def _cmd_scheduler(args, *, conversation_id, channel) -> str:
    if not args:
        state = "⏸ wstrzymany" if scheduler_runner.is_paused() else "▶️ aktywny"
        return f"⚙️ Scheduler: {state}\nUżycie: {PREFIX}scheduler pause | resume"
    sub = args[0].lower()
    if sub == "pause":
        scheduler_runner.pause()
        return "⏸ Scheduler wstrzymany — pętla tyka dalej, ale nie odpala zadań."
    if sub == "resume":
        scheduler_runner.resume()
        return "▶️ Scheduler wznowiony."
    return f"⚠️ Nieznany argument '{sub}'. Użyj: pause / resume."


# ================================== collectors ====================================

async def _cmd_collect(args, *, conversation_id, channel) -> str:
    """Run the daily observation collectors NOW (finance and/or FPL), out of their schedule.
    Useful to seed/refresh data on demand. Idempotent (upserts by date)."""
    which = args[0].lower() if args else "all"
    if which not in ("all", "finance", "fin", "fpl"):
        return f"⚠️ Użycie: {PREFIX}collect [finance|fpl]  (bez argumentu = oba)"
    lines = ["📥 KOLEKTORY (ręczne uruchomienie)", ""]
    if which in ("all", "finance", "fin"):
        n = await observations_runner.collect_finance_once()
        lines.append(f"💹 finanse: zapisano {n} serii" if n else "💹 finanse: 0 (błąd/pusto — zob. /errors)")
    if which in ("all", "fpl"):
        ok = await observations_runner.collect_fpl_once()
        lines.append("⚽ FPL: zapisano dzienny zrzut ligi" if ok else "⚽ FPL: pominięto (brak ligi/GW lub błąd)")
    lines += ["", f"🕐 {_now_str()}"]
    return "\n".join(lines)


# =================================== MCP keys =====================================

async def _cmd_mcp(args, *, conversation_id, channel) -> str:
    """List MCP API keys (self-service + config file) or revoke a self-service one."""
    import asyncio

    from lib.runtime.mcp_keys import McpClientRegistry

    sub = args[0].lower() if args else "list"
    if sub == "revoke":
        if len(args) < 2 or not args[1].isdigit():
            return f"⚠️ Użycie: {PREFIX}mcp revoke <ID drużyny FPL>"
        n = await asyncio.to_thread(mcp_keys.revoke_entry, int(args[1]))
        if not n:
            return f"ℹ️ Drużyna {args[1]} nie ma aktywnego klucza z samoobsługi."
        return (f"🚫 Odwołano klucz drużyny {args[1]}. Serwer MCP przestanie go przyjmować w "
                "ciągu ~15 s. (Klucze z config/mcp_clients.yaml odwołujesz w pliku.)")
    if sub != "list":
        return f"⚠️ Użycie: {PREFIX}mcp [list] | {PREFIX}mcp revoke <ID drużyny>"

    rows = await asyncio.to_thread(mcp_keys.active)
    lines = ["🔑 KLUCZE MCP", "", f"Z samoobsługi ({len(rows)}):"]
    for r in rows:
        since = str(r.get("created_at") or "")[:10]
        lines.append(f"• {r.get('team_name') or '?'} — {r.get('manager_name') or '?'} "
                     f"(ID {r.get('entry_id')}) · od {since}")
    if not rows:
        lines.append("• brak")
    try:
        configured = await asyncio.to_thread(lambda: McpClientRegistry().clients())
        lines += ["", f"Z pliku config ({len(configured)}): "
                  + (", ".join(c.client_id for c in configured) or "brak")]
    except Exception as e:  # noqa: BLE001
        lines += ["", f"Z pliku config: nie da się odczytać ({type(e).__name__})"]
    base = (os.getenv("MCP_PUBLIC_URL") or "").rstrip("/")
    lines += ["", f"Strona: {base + '/mcp/join' if base else '/mcp/join na domenie serwera'}",
              f"Odwołanie: {PREFIX}mcp revoke <ID>"]
    return "\n".join(lines)


# ================================ configuration ===================================

def _agent_names() -> list[str]:
    return [n for n in sorted(AGENTS) if n not in ("coordinator", "composer")]


async def _cmd_agents(args, *, conversation_id, channel) -> str:
    lines = ["🤖 SUBAGENCI", "(nazwy do użycia w config/principals.yaml → agents:)", ""]
    for name in _agent_names():
        dot = "🟢" if agent_enabled(name) else "🔴"
        lines.append(f"{dot} {name}")
    return "\n".join(lines)


async def _cmd_models(args, *, conversation_id, channel) -> str:
    try:
        profile = active_profile()
    except ModelProfileError as e:
        return f"🧩 MODELE\n\n⚠️ {e}"
    others = [p for p in available_profiles() if p != profile.name]
    lines = [f"🧩 MODELE — profil: {profile.name}"]
    if profile.description:
        lines.append(profile.description)
    if others:
        lines.append(f"(inne profile: {', '.join(others)} — zmiana: MODEL_PROFILE w .env + restart)")
    lines.append("")
    for name in sorted(AGENTS):
        cfg = llm.resolve_llm_config(name)
        if not cfg["model"]:
            lines.append(f"• {name}: — (brak konfiguracji)")
            continue
        model = f"{cfg['provider']}/{cfg['model']}"
        effort = cfg["effort"] or "off"
        lines.append(f"• {name}: {model} · effort {effort}")
    return "\n".join(lines)


_ENV_PREFIXES = (
    "PROGRESS_PING", "SCHEDULER_", "TRACING_ENABLED", "MLFLOW_", "HISTORY_",
    "MODEL_PROFILE", "AGENT_", "DATABASE_URL", "REDIS_URL",
    "MESSENGER_ALLOWED_SENDER_ID", "WATCH_", "FPL_",
)


def _mask(key: str, val: str) -> str:
    up = key.upper()
    if any(t in up for t in ("KEY", "SECRET", "TOKEN", "PASSWORD")):
        return f"…{val[-4:]}" if len(val) > 4 else "****"
    if "URL" in up and "://" in val:
        return re.sub(r"://([^:/@]+):([^@]+)@", r"://\1:***@", val)
    return val


async def _cmd_env(args, *, conversation_id, channel) -> str:
    items = {
        k: v for k, v in os.environ.items()
        if any(k.startswith(p) for p in _ENV_PREFIXES)
    }
    if not items:
        return "⚙️ Brak istotnych zmiennych środowiskowych."
    lines = ["⚙️ ENV (istotne, sekrety zamaskowane)", ""]
    for k in sorted(items):
        lines.append(f"{k}={_mask(k, items[k])}")
    return "\n".join(lines)


# ============================== users / groups (owner-only) =======================

async def _cmd_user(args, *, conversation_id, channel) -> str:
    """Manage users + the identity whitelist: list / add / assign to groups."""
    sub = args[0].lower() if args else "list"
    if sub == "list":
        users = principals.list_users()
        if not users:
            return "👥 Brak użytkowników."
        lines = ["👥 UŻYTKOWNICY", ""]
        for u in users:
            ids = principals.identities_for(u["id"])
            grps = [g["name"] for g in principals.groups_for_user(u["id"])]
            id_s = ", ".join(f"{i['channel']}:{i['external_id']}" for i in ids) or "—"
            lines.append(f"• {u['id']} „{u.get('display_name') or ''}” [{u.get('status')}]")
            lines.append(f"    tożsamości: {id_s}")
            lines.append(f"    grupy: {', '.join(grps) or '—'}")
        return "\n".join(lines)
    if sub == "add":
        # /user add <external_id> [display name...]  (channel defaults to messenger)
        if len(args) < 2:
            return f"➕ Użycie: {PREFIX}user add <external_id> [nazwa]"
        external_id = args[1]
        display = " ".join(args[2:]) or None
        if principals.resolve_user("messenger", external_id) is not None:
            return f"ℹ️ Ta tożsamość (messenger:{external_id}) jest już przypisana."
        uid = new_user_id()
        principals.add_user(uid, display)
        principals.add_identity(uid, "messenger", external_id)
        # Opt-in model: a new user starts with NO groups (empty roster). Assign them to
        # groups that enable the agents they should have. (The `default` group is the
        # owner's full-access group and is intentionally not auto-assigned.)
        return (f"✅ Dodano użytkownika {uid} „{display or ''}”\n"
                f"   tożsamość: messenger:{external_id}\n"
                f"   grupy: (brak — roster jest pusty do czasu przypisania)\n"
                f"   ➡️ przypisz do grup: {PREFIX}user assign {uid} <grupa,...>")
    if sub == "assign":
        # /user assign <user_id> <group_name>[,group_name...]
        if len(args) < 3:
            return f"🔗 Użycie: {PREFIX}user assign <user_id> <grupa[,grupa]>"
        uid = args[1]
        if principals.get_user(uid) is None:
            return f"❓ Nie znam użytkownika „{uid}”."
        names = [n.strip() for n in args[2].split(",") if n.strip()]
        done, missing = [], []
        for name in names:
            g = principals.get_group_by_name(name)
            if not g:
                missing.append(name)
                continue
            principals.add_membership(uid, g["id"])
            done.append(name)
        out = [f"✅ Przypisano {uid} do: {', '.join(done)}" if done else "ℹ️ Nic nie przypisano."]
        if missing:
            out.append(f"⚠️ Nie ma takich grup: {', '.join(missing)} (utwórz: {PREFIX}group add <nazwa>)")
        return "\n".join(out)
    return f"⚠️ Nieznany podkomenda '{sub}'. Użyj: list | add | assign"


async def _cmd_group(args, *, conversation_id, channel) -> str:
    """Manage groups: list / add / priority / agents (per-group roster config)."""
    sub = args[0].lower() if args else "list"
    if sub == "list":
        groups = principals.list_groups()
        if not groups:
            return "🏘 Brak grup."
        lines = ["🏘 GRUPY (wg priorytetu)", ""]
        for g in groups:
            members = principals.members_of(g["id"])
            cfg = agent_config.get_layer(GROUP_SCOPE, g["id"])
            cfg_s = ", ".join(sorted(cfg)) or "—"
            lines.append(f"• {g['name']} (priorytet {g['priority']}, {len(members)} os.)")
            lines.append(f"    konfiguracja agentów: {cfg_s}")
        return "\n".join(lines)
    if sub == "add":
        if len(args) < 2:
            return f"➕ Użycie: {PREFIX}group add <nazwa> [priorytet]"
        name = args[1]
        priority = int(args[2]) if len(args) > 2 and args[2].lstrip("-").isdigit() else 0
        g = principals.add_group(new_group_id(), name, priority)
        return f"✅ Grupa „{name}” (priorytet {g['priority']})."
    if sub == "priority":
        if len(args) < 3 or not args[2].lstrip("-").isdigit():
            return f"🔢 Użycie: {PREFIX}group priority <nazwa> <liczba>"
        g = principals.get_group_by_name(args[1])
        if not g:
            return f"❓ Nie ma grupy „{args[1]}”."
        principals.set_priority(g["id"], int(args[2]))
        return f"✅ Priorytet grupy „{args[1]}” = {int(args[2])}."
    if sub == "agents":
        # /group agents <nazwa> +news_agent -iot_operator finance_agent=anthropic/claude
        if len(args) < 3:
            return (f"🤖 Użycie: {PREFIX}group agents <nazwa> +agent -agent agent=provider/model\n"
                    f"  +nazwa = włącz, -nazwa = wyłącz, nazwa=model = ustaw model")
        g = principals.get_group_by_name(args[1])
        if not g:
            return f"❓ Nie ma grupy „{args[1]}”."
        changed = []
        for tok in args[2:]:
            if tok.startswith("+"):
                agent_config.set(GROUP_SCOPE, g["id"], tok[1:], enabled=True)
                changed.append(f"+{tok[1:]}")
            elif tok.startswith("-"):
                agent_config.set(GROUP_SCOPE, g["id"], tok[1:], enabled=False)
                changed.append(f"-{tok[1:]}")
            elif "=" in tok:
                agent, model = tok.split("=", 1)
                agent_config.set(GROUP_SCOPE, g["id"], agent, model=model)
                changed.append(f"{agent}={model}")
            else:
                changed.append(f"?{tok}(zignorowano)")
        return f"✅ Grupa „{args[1]}”: {', '.join(changed)}"
    return f"⚠️ Nieznana podkomenda '{sub}'. Użyj: list | add | priority | agents"


async def _cmd_whoami(args, *, conversation_id, channel) -> str:
    """Tell the caller their Messenger PSID + resolved user_id — the one command a
    non-admin (even a not-yet-whitelisted sender) may use, so anyone can self-service the
    id the owner needs for `/user add`."""
    psid = conversation_id.split(":", 1)[1] if conversation_id.startswith("messenger:") else None
    lines = ["🪪 KIM JESTEŚ", ""]
    if psid:
        lines.append(f"Twój Messenger ID (PSID): {psid}")
    else:
        lines.append(f"Kanał: {channel} (poza Messengerem — brak PSID)")
    uid = principals.resolve_user("messenger", psid) if psid else None
    if uid:
        user = principals.get_user(uid) or {}
        lines.append(f"user_id: {uid}" + (f" („{user.get('display_name')}”)" if user.get("display_name") else ""))
        grps = [g["name"] for g in principals.groups_for_user(uid)]
        lines.append(f"grupy: {', '.join(grps) or '—'}")
        lines.append(f"admin: {'tak' if is_admin(uid) else 'nie'}")
    else:
        lines.append("")
        lines.append("Nie jesteś jeszcze dodany. Podaj powyższy PSID właścicielowi")
    return "\n".join(lines)


async def _cmd_reload(args, *, conversation_id, channel) -> str:
    """Re-apply the declarative principals config file (config/principals.yaml) now."""
    from lib.domain import apply_config_file, config_path
    summary = apply_config_file()
    if summary is None:
        return (f"ℹ️ Brak pliku configu ({config_path()}).\n"
                f"Utwórz go (patrz config/principals.example.yaml), aby konfigurować deklaratywnie.")
    lines = ["♻️ RELOAD principals config", "",
             f"grupy: {summary['groups']} · użytkownicy: {summary['users']} · nowe tożsamości: {summary['identities']}"]
    if summary["warnings"]:
        lines += ["", "⚠️ Ostrzeżenia:"] + [f"• {w}" for w in summary["warnings"][:15]]
    return "\n".join(lines)


async def _cmd_profile(args, *, conversation_id, channel) -> str:
    """Show a user's resolved roster + per-agent model/reasoning with the winning layer."""
    from lib.agents import subagent_names  # runtime→agents; imported lazily to avoid cost
    if not args:
        return f"🪪 Użycie: {PREFIX}profile <user_id>"
    uid = args[0]
    if principals.get_user(uid) is None:
        return f"❓ Nie znam użytkownika „{uid}”."
    prof = resolve_profile(uid, subagent_names())
    grps = [f"{g['name']}({g['priority']})" for g in principals.groups_for_user(uid)]
    lines = [f"🪪 PROFIL {uid}", f"grupy: {', '.join(grps) or '—'}", "",
             f"roster ({len(prof.roster())}): {', '.join(prof.roster()) or '—'}", ""]
    for name in prof.agents:
        ap = prof.agents[name]
        dot = "🟢" if ap.enabled.value else "🔴"
        model = ap.model.value or "(env)"
        lines.append(f"{dot} {name}")
        lines.append(f"    enabled={ap.enabled.value} ⟵ {ap.enabled.source}")
        lines.append(f"    model={model} ⟵ {ap.model.source}"
                     + (f" · effort={ap.reasoning.value} ⟵ {ap.reasoning.source}" if ap.reasoning.value else ""))
    return "\n".join(lines)


# ============================== conversation / memory =============================

async def _cmd_memory(args, *, conversation_id, channel) -> str:
    try:
        s = memory.summary()
    except Exception as e:
        return f"🧠 Nie mogę odczytać pamięci: {type(e).__name__}"
    return "🧠 PAMIĘĆ DŁUGOTERMINOWA\n\n" + (s.strip() if s and s.strip() else "(pusta)")


async def _cmd_summary(args, *, conversation_id, channel) -> str:
    text, upto = conversations.load_summary(conversation_id)
    hist = conversations.load_history(conversation_id)
    head = f"📝 PODSUMOWANIE ROZMOWY\n(wiadomości: {len(hist)}, zwinięte: {upto})\n"
    return head + "\n" + (text.strip() if text and text.strip() else "(brak — krótka rozmowa)")


async def _cmd_reset(args, *, conversation_id, channel) -> str:
    if not (args and args[0].lower() == "confirm"):
        return (
            "⚠️ To wyczyści historię i podsumowanie TEJ rozmowy.\n"
            f"Aby potwierdzić: {PREFIX}reset confirm"
        )
    ok = conversations.reset(conversation_id)
    return "🧹 Wyczyszczono historię tej rozmowy." if ok else "ℹ️ Nie było czego czyścić."


# =================================== last turn ====================================

async def _cmd_last(args, *, conversation_id, channel) -> str:
    st = diag.last_turn_stats()
    if not st:
        return "📈 Brak zarejestrowanej tury w tej sesji procesu."
    lines = [
        "📈 OSTATNIA TURA",
        "",
        f"⏱ czas: {st['duration_s']}s",
        f"🗨 rozmowa: {st['conversation_id']}",
        f"🕐 {_ago(st['at'])}",
    ]
    if st["trace_id"]:
        lines.append(f"🔎 trace: {st['trace_id']}")
        uri = os.getenv("MLFLOW_TRACKING_URI")
        if uri:
            lines.append(f"MLflow: {uri}")
    else:
        lines.append("🔎 trace: — (tracing off)")
    return "\n".join(lines)


async def _cmd_window(args, *, conversation_id, channel) -> str:
    prev = diag.previous_inbound_at(conversation_id)
    lines = ["📨 OKNO MESSENGERA", ""]
    if prev is None:
        lines.append("Nie widziałem wcześniejszej wiadomości w tej sesji procesu —")
        lines.append("okna 24h nie umiem oszacować.")
    else:
        secs = (datetime.now(timezone.utc) - prev).total_seconds()
        is_open = secs < 24 * 3600
        lines.append(f"ostatnia Twoja wiadomość: {_fmt_delta(secs)} temu")
        lines.append(("🟢 okno 24h RESPONSE: otwarte" if is_open else "🔴 okno 24h RESPONSE: zamknięte"))
    lines += ["", "ℹ️ Proaktywne briefy i tak idą tagiem HUMAN_AGENT (okno 7 dni),", "więc scheduler dostarcza niezależnie od okna 24h."]
    return "\n".join(lines)


# ================================== help / manual =================================

@dataclass
class _Cmd:
    fn: Callable[..., Awaitable[str]]
    group: str
    usage: str
    summary: str
    public: bool = False  # available to non-admins (and not-yet-whitelisted senders)


_COMMANDS: dict[str, _Cmd] = {
    # Sterowanie
    "stop": _Cmd(_cmd_stop, "Sterowanie", "/stop", "przerwij zadanie, które agent teraz przetwarza"),
    # Zdrowie
    "status": _Cmd(_cmd_status, "Zdrowie", "/status", "żywotność: uptime, scheduler, baza, tracing, ping"),
    "version": _Cmd(_cmd_version, "Zdrowie", "/version", "wersja kodu: uptime, świeżość plików, git SHA"),
    "ping": _Cmd(_cmd_ping, "Zdrowie", "/ping", "sprawdza round-trip (bot → Send API)"),
    "probe": _Cmd(_cmd_probe, "Zdrowie", "/probe", "dostępność zewnętrznych API (pogoda, mapy, FX…)"),
    # Logi
    "logs": _Cmd(_cmd_logs, "Logi", "/logs [N]", "ostatnie N linii bieżącego logu (domyślnie 20)"),
    "errors": _Cmd(_cmd_errors, "Logi", "/errors [N]", "ostatnie N błędów/tracebacków (domyślnie 10)"),
    "loglevel": _Cmd(_cmd_loglevel, "Logi", "/loglevel [poziom]", "pokaż/ustaw poziom logowania w locie"),
    # Scheduler
    "jobs": _Cmd(_cmd_jobs, "Scheduler", "/jobs [all]", "zadania i watche z rodzajem (cron/watch) i terminami; 'all' pokaże też zakończone"),
    "fire": _Cmd(_cmd_fire, "Scheduler", "/fire <id>", "odpal zadanie TERAZ (bez zmiany harmonogramu)"),
    "watch": _Cmd(_cmd_watch, "Scheduler", "/watch <id>", "szczegóły watcha: źródło, wynik, następny poll"),
    "scheduler": _Cmd(_cmd_scheduler, "Scheduler", "/scheduler [pause|resume]", "wstrzymaj/wznów odpalanie zadań"),
    # Kolektory
    "collect": _Cmd(_cmd_collect, "Kolektory", "/collect [finance|fpl]", "ręcznie zbierz dzienne dane (obserwacje)"),
    # MCP
    "mcp": _Cmd(_cmd_mcp, "MCP", "/mcp [list|revoke <ID>]", "klucze API serwerów MCP (samoobsługa + plik) i odwoływanie"),
    # Użytkownicy (multi-user)
    "user": _Cmd(_cmd_user, "Użytkownicy", "/user [list|add|assign]", "użytkownicy + whitelista tożsamości i przypisania do grup"),
    "group": _Cmd(_cmd_group, "Użytkownicy", "/group [list|add|priority|agents]", "grupy: priorytet i konfiguracja rostera agentów"),
    "profile": _Cmd(_cmd_profile, "Użytkownicy", "/profile <user_id>", "rozwiązany roster + model per agent (z warstwą-źródłem)"),
    "reload": _Cmd(_cmd_reload, "Użytkownicy", "/reload", "wczytaj ponownie plik config/principals.yaml (deklaratywny)"),
    "whoami": _Cmd(_cmd_whoami, "Użytkownicy", "/whoami", "pokaż swój Messenger PSID i user_id (dostępne dla każdego)", public=True),
    # Konfiguracja
    "agents": _Cmd(_cmd_agents, "Konfiguracja", "/agents", "którzy subagenci włączeni/wyłączeni"),
    "models": _Cmd(_cmd_models, "Konfiguracja", "/models", "model + reasoning effort per agent"),
    "env": _Cmd(_cmd_env, "Konfiguracja", "/env", "istotne zmienne env (sekrety zamaskowane)"),
    # Rozmowa i pamięć
    "memory": _Cmd(_cmd_memory, "Rozmowa i pamięć", "/memory", "co pamięć długoterminowa o Tobie trzyma"),
    "summary": _Cmd(_cmd_summary, "Rozmowa i pamięć", "/summary", "bieżące rolling-summary tej rozmowy"),
    "reset": _Cmd(_cmd_reset, "Rozmowa i pamięć", "/reset confirm", "wyczyść historię+podsumowanie tej rozmowy"),
    # Diagnostyka tury
    "last": _Cmd(_cmd_last, "Diagnostyka", "/last", "ostatnia tura: czas, rozmowa, trace id"),
    "window": _Cmd(_cmd_window, "Diagnostyka", "/window", "szacunek okna 24h Messengera"),
}

_GROUP_ORDER = ["Sterowanie", "Zdrowie", "Logi", "Scheduler", "Kolektory", "MCP", "Użytkownicy", "Konfiguracja", "Rozmowa i pamięć", "Diagnostyka"]


async def _cmd_help(args, *, conversation_id, channel) -> str:
    lines = ["📖 KOMENDY TECHNICZNE", "", "Wpisz komendę zaczynając od /", ""]
    for group in _GROUP_ORDER:
        lines.append(f"— {group} —")
        for name, cmd in _COMMANDS.items():
            if cmd.group == group:
                lines.append(f"{cmd.usage} — {cmd.summary}")
        lines.append("")
    lines.append("/help — ta lista")
    return "\n".join(lines).rstrip()


_COMMANDS["help"] = _Cmd(_cmd_help, "", "/help", "ta lista komend")


# ==================================== router ======================================

async def maybe_handle(text: str, *, conversation_id: str, channel: str = "messenger",
                       is_admin: bool = True) -> Optional[str]:
    """If ``text`` is a command (starts with ``/``), run it and return the reply.

    Returns None when ``text`` is a normal message, so the caller falls through to the
    regular agent pipeline. Never raises: a handler error is caught and reported inline.

    ``is_admin`` gates the surface: admins get every command; a non-admin may run only
    ``public`` commands (currently just ``/whoami``) — any other ``/command`` returns None
    and falls through to the agent. Defaults to True so the local REPL (owner) and existing
    callers are unaffected.
    """
    if not text or not text.strip().startswith(PREFIX):
        return None
    parts = text.strip()[len(PREFIX):].split()
    if not parts:
        return await _cmd_help([], conversation_id=conversation_id, channel=channel) if is_admin else None
    name, args = parts[0].lower(), parts[1:]
    cmd = _COMMANDS.get(name)
    if not cmd:
        # Don't leak the command catalog to non-admins; just fall through.
        return (f"❓ Nieznana komenda: {PREFIX}{name}\nWpisz {PREFIX}help, żeby zobaczyć listę."
                if is_admin else None)
    if not is_admin and not cmd.public:
        return None  # non-admins may only use public commands; others fall through
    try:
        return await cmd.fn(args, conversation_id=conversation_id, channel=channel)
    except Exception as e:
        logger.exception("Admin command %s failed", name)
        return f"⚠️ Komenda {PREFIX}{name} wywaliła się: {type(e).__name__}: {e}"
