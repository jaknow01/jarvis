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
       ICLOUD_CALENDAR_NAME=Personal                  (default calendar to read/write)

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


def _pick_calendar(principal: "caldav.Principal", name: str | None):
    """Choose the target calendar. ``name`` (or ICLOUD_CALENDAR_NAME) is matched
    case-insensitively by display name; otherwise the first calendar is used."""
    calendars = principal.calendars()
    if not calendars:
        raise RuntimeError("No calendars found on the iCloud account.")
    wanted = (name or os.getenv("ICLOUD_CALENDAR_NAME") or "").strip().lower()
    if wanted:
        for cal in calendars:
            try:
                if str(cal.name or "").strip().lower() == wanted:
                    return cal
            except Exception:  # noqa: BLE001 - name lookup can PROPFIND and fail
                continue
        logger.warning("Calendar %r not found; falling back to the first one.", name)
    return calendars[0]


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


def event_to_dict(event: "caldav.Event") -> dict:
    """Project a raw CalDAV event down to the safe fields the model may see."""
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
    principal = _client().principal()
    cal = _pick_calendar(principal, calendar_name)
    start, end = _parse_dt(start_iso), _parse_dt(end_iso)
    found = cal.search(start=start, end=end, event=True, expand=True)
    events = [event_to_dict(e) for e in found]
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
    cal = _pick_calendar(principal, calendar_name)
    saved = cal.save_event(ical)
    return event_to_dict(saved)


def _find_event(cal, event_id: str):
    try:
        return cal.event_by_uid(event_id)
    except Exception:  # noqa: BLE001 - some servers 404 unknown uids
        return None


def get_event(event_id: str, calendar_name: str | None = None) -> dict | None:
    principal = _client().principal()
    cal = _pick_calendar(principal, calendar_name)
    ev = _find_event(cal, event_id)
    return event_to_dict(ev) if ev is not None else None


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
    cal = _pick_calendar(principal, calendar_name)
    ev = _find_event(cal, event_id)
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
    return event_to_dict(ev)


def delete_event(event_id: str, calendar_name: str | None = None) -> bool:
    principal = _client().principal()
    cal = _pick_calendar(principal, calendar_name)
    ev = _find_event(cal, event_id)
    if ev is None:
        return False
    ev.delete()
    return True
