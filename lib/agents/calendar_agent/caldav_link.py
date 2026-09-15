"""calendar_agent private helper: the CalDAV / iCloud link.

Owns the connection to Apple iCloud Calendar over CalDAV and the translation between raw
iCalendar (VEVENT) data and the safe dicts the agent tools hand back to the model.

Secrets stay here, server-side — exactly like ``iot_operator``'s ``tuya_link`` (device
local keys) and the maps address resolution: the Apple ID, the app-specific password and
the CalDAV URL come from the environment and NEVER reach the LLM. The model works with
event summaries, times, locations and opaque event ids (iCalendar UIDs) only.

iCloud setup (owner, one-time):
  1. appleid.apple.com -> Sign-In and Security -> App-Specific Passwords -> generate one
     (label it e.g. "Jarvis"). This is NOT your main Apple ID password, and it can be
     revoked independently at any time.
  2. Put the Apple ID e-mail + that app password in ``.env``::
       ICLOUD_CALDAV_USER=you@icloud.com
       ICLOUD_APP_PASSWORD=xxxx-xxxx-xxxx-xxxx
     Optional::
       ICLOUD_CALDAV_URL=https://caldav.icloud.com/   (default)
       ICLOUD_CALENDAR_NAME=Asystent                  (calendar NEW events are written to)
       ICLOUD_READ_CALENDARS=Asystent,Dom             (calendars reads span; empty = ALL)

Read vs write are separate: writes go to the single ICLOUD_CALENDAR_NAME calendar, but
reads (listing, and finding an event to edit/delete) span every calendar by default — or
the ICLOUD_READ_CALENDARS subset (always including the write calendar). So Jarvis can add
to 'Asystent' yet still see 'Dom', 'Praca', etc. If ICLOUD_CALENDAR_NAME names a calendar
that does not exist yet, it is created on iCloud on first write (never on a read).

The ``caldav`` library is synchronous (``requests`` under the hood). Every public helper
here is a plain blocking function; the async tools call them via ``asyncio.to_thread`` so
they never block the event loop. A fresh client per call is fine at personal scale;
pooling/caching the principal is a later optimization, not correctness.
"""

import logging
import os
import uuid
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import caldav
from icalendar import Calendar as ICalendar
from icalendar import Event as IEvent

logger = logging.getLogger(__name__)

# Owner default; matches lib/core/env.py TIMEZONE. Naive datetimes coming from the model
# are interpreted in this zone before being sent to iCloud.
_TZ = ZoneInfo(os.getenv("SCHEDULER_TIMEZONE") or "Europe/Warsaw")
_DEFAULT_URL = "https://caldav.icloud.com/"


class CalendarConfigError(RuntimeError):
    """Raised when the iCloud CalDAV credentials are not configured."""


# --------------------------------------------------------------------------- connection


def _client() -> caldav.DAVClient:
    user = os.getenv("ICLOUD_CALDAV_USER")
    password = os.getenv("ICLOUD_APP_PASSWORD")
    if not user or not password:
        raise CalendarConfigError(
            "iCloud calendar is not configured. Set ICLOUD_CALDAV_USER and "
            "ICLOUD_APP_PASSWORD (an app-specific password from appleid.apple.com) in .env."
        )
    url = os.getenv("ICLOUD_CALDAV_URL") or _DEFAULT_URL
    return caldav.DAVClient(url=url, username=user, password=password)


def _cal_name(cal) -> str:
    try:
        return str(cal.name or "")
    except Exception:  # noqa: BLE001 - name lookup can PROPFIND and fail
        return ""


def _all_calendars(principal: "caldav.Principal") -> list:
    calendars = principal.calendars()
    if not calendars:
        raise RuntimeError("No calendars found on the iCloud account.")
    return calendars


def _write_calendar(principal: "caldav.Principal", name: str | None):
    """The single calendar new events are written to: the explicit ``name`` arg, else
    ICLOUD_CALENDAR_NAME, else the account's first calendar. Matching is case-insensitive
    by display name.

    Auto-create: if a name IS configured but no such calendar exists, create it on iCloud
    (the owner's stated intent — "use calendar X" — is to have Jarvis own that calendar).
    Only ever called on the write path, so a read never creates anything. If creation
    fails, fall back to the first calendar so writes still succeed (the caller returns the
    actual 'calendar' it wrote to)."""
    calendars = _all_calendars(principal)
    wanted_raw = (name or os.getenv("ICLOUD_CALENDAR_NAME") or "").strip()
    if wanted_raw:
        wanted = wanted_raw.lower()
        for cal in calendars:
            if _cal_name(cal).strip().lower() == wanted:
                return cal
        try:
            created = principal.make_calendar(name=wanted_raw)
            logger.info("Write calendar %r did not exist; created it on iCloud.", wanted_raw)
            return created
        except Exception:  # noqa: BLE001 - creation can fail (perms/server); stay writable
            logger.exception(
                "Could not create calendar %r; falling back to the first one.", wanted_raw
            )
    return calendars[0]


def _read_calendars(principal: "caldav.Principal", name: str | None = None) -> list:
    """The calendars reads (list/find) span. Precedence:
    1. an explicit ``name`` arg -> just that calendar (a caller narrowing the scope);
    2. ICLOUD_READ_CALENDARS (comma-separated display names) -> those, PLUS the write
       calendar so Jarvis always sees the events it created;
    3. neither set -> ALL calendars on the account.
    So reading is broad by default and writing stays pinned to one calendar."""
    calendars = _all_calendars(principal)
    if name:
        wanted = name.strip().lower()
        picked = [c for c in calendars if _cal_name(c).strip().lower() == wanted]
        if picked:
            return picked
        logger.warning("Read calendar %r not found; reading all instead.", name)
        return calendars

    raw = (os.getenv("ICLOUD_READ_CALENDARS") or "").strip()
    if not raw:
        return calendars  # default: every calendar

    wanted = {n.strip().lower() for n in raw.split(",") if n.strip()}
    # Always include the write calendar so Jarvis sees its own events — by NAME only, so a
    # read never resolves/creates the write calendar (that stays a write-path side effect).
    write_name = (os.getenv("ICLOUD_CALENDAR_NAME") or "").strip().lower()
    if write_name:
        wanted.add(write_name)
    picked = [c for c in calendars if _cal_name(c).strip().lower() in wanted]
    return picked or calendars


# --------------------------------------------------------------------------- (de)serialize


def _as_aware(value: datetime | date) -> datetime:
    """Normalize a date/naive-datetime into a tz-aware datetime in the owner's zone."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=_TZ)
    # all-day date -> midnight local
    return datetime(value.year, value.month, value.day, tzinfo=_TZ)


def _parse_dt(value: str) -> datetime:
    """Parse an ISO-8601 string from the model; attach the owner's zone if naive."""
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=_TZ)


def event_to_dict(event: "caldav.Event", calendar_name: str | None = None) -> dict:
    """Project a raw CalDAV event down to the safe fields the model may see.
    ``calendar_name`` tags which calendar the event came from (so a merged multi-calendar
    listing stays legible, e.g. 'Dom' vs 'Asystent')."""
    comp = event.icalendar_component or {}

    def _text(key: str) -> str | None:
        v = comp.get(key)
        return str(v) if v is not None else None

    def _time(key: str) -> tuple[str | None, bool]:
        node = comp.get(key)
        if node is None:
            return None, False
        val = node.dt
        all_day = not isinstance(val, datetime)
        return _as_aware(val).isoformat(), all_day

    start, all_day = _time("dtstart")
    end, _ = _time("dtend")
    return {
        "id": _text("uid"),
        "summary": _text("summary"),
        "start": start,
        "end": end,
        "all_day": all_day,
        "location": _text("location"),
        "description": _text("description"),
        "status": _text("status"),
        "calendar": calendar_name,
    }


def _build_ical(
    *,
    summary: str,
    start: datetime,
    end: datetime,
    all_day: bool,
    location: str | None,
    description: str | None,
    uid: str,
) -> bytes:
    cal = ICalendar()
    cal.add("prodid", "-//Jarvis//Calendar Agent//EN")
    cal.add("version", "2.0")
    ev = IEvent()
    ev.add("uid", uid)
    ev.add("summary", summary)
    if all_day:
        ev.add("dtstart", start.date())
        ev.add("dtend", (end or start).date())
    else:
        ev.add("dtstart", start)
        ev.add("dtend", end)
    if location:
        ev.add("location", location)
    if description:
        ev.add("description", description)
    ev.add("dtstamp", datetime.now(_TZ))
    cal.add_component(ev)
    return cal.to_ical()


# --------------------------------------------------------------------------- operations
# All blocking; call via asyncio.to_thread from the async tools.


def list_events(start_iso: str, end_iso: str, calendar_name: str | None = None) -> list[dict]:
    """List events in the window across the read scope (all calendars by default, or the
    ICLOUD_READ_CALENDARS subset; ``calendar_name`` narrows to one). Each event is tagged
    with its source calendar."""
    principal = _client().principal()
    start, end = _parse_dt(start_iso), _parse_dt(end_iso)
    events: list[dict] = []
    for cal in _read_calendars(principal, calendar_name):
        name = _cal_name(cal)
        try:
            found = cal.search(start=start, end=end, event=True, expand=True)
        except Exception:  # noqa: BLE001 - one bad calendar must not sink the whole read
            logger.exception("Search failed on calendar %r; skipping it.", name)
            continue
        events.extend(event_to_dict(e, name) for e in found)
    events.sort(key=lambda e: e["start"] or "")
    return events


def create_event(
    *,
    summary: str,
    start_iso: str,
    end_iso: str | None,
    all_day: bool = False,
    location: str | None = None,
    description: str | None = None,
    calendar_name: str | None = None,
) -> dict:
    start = _parse_dt(start_iso)
    if end_iso:
        end = _parse_dt(end_iso)
    elif all_day:
        end = start + timedelta(days=1)
    else:
        end = start + timedelta(hours=1)  # sane default duration
    uid = f"{uuid.uuid4()}@jarvis"
    ical = _build_ical(
        summary=summary, start=start, end=end, all_day=all_day,
        location=location, description=description, uid=uid,
    )
    principal = _client().principal()
    cal = _write_calendar(principal, calendar_name)
    saved = cal.save_event(ical)
    return event_to_dict(saved, _cal_name(cal))


def _find_event_across(principal, event_id: str, calendar_name: str | None = None):
    """Locate an event by UID across the read scope, so an event the user saw in any
    calendar can be edited/deleted. Returns (event, calendar_name) or (None, None)."""
    for cal in _read_calendars(principal, calendar_name):
        try:
            ev = cal.event_by_uid(event_id)
        except Exception:  # noqa: BLE001 - servers 404 unknown uids per calendar
            continue
        if ev is not None:
            return ev, _cal_name(cal)
    return None, None


def get_event(event_id: str, calendar_name: str | None = None) -> dict | None:
    principal = _client().principal()
    ev, name = _find_event_across(principal, event_id, calendar_name)
    return event_to_dict(ev, name) if ev is not None else None


def update_event(
    event_id: str,
    *,
    summary: str | None = None,
    start_iso: str | None = None,
    end_iso: str | None = None,
    location: str | None = None,
    description: str | None = None,
    calendar_name: str | None = None,
) -> dict | None:
    principal = _client().principal()
    ev, name = _find_event_across(principal, event_id, calendar_name)
    if ev is None:
        return None
    comp = ev.icalendar_component

    def _set(key: str, value):
        if key in comp:
            del comp[key]
        comp.add(key, value)

    if summary is not None:
        _set("summary", summary)
    if start_iso is not None:
        _set("dtstart", _parse_dt(start_iso))
    if end_iso is not None:
        _set("dtend", _parse_dt(end_iso))
    if location is not None:
        _set("location", location)
    if description is not None:
        _set("description", description)
    ev.save()
    return event_to_dict(ev, name)


def delete_event(event_id: str, calendar_name: str | None = None) -> bool:
    principal = _client().principal()
    ev, _ = _find_event_across(principal, event_id, calendar_name)
    if ev is None:
        return False
    ev.delete()
    return True
