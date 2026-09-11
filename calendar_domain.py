"""Calendar entries, semantic comparisons and bounded recurrence previews."""

from datetime import date, datetime, time, timedelta, timezone
from itertools import islice
from dateutil.rrule import rrulestr
from icalendar import Calendar, Event, Todo

DISPLAY_FIELDS = (
    "title",
    "type",
    "start",
    "end",
    "location",
    "description",
    "recurrence",
    "timezone",
)


def entry_type(item):
    return item.get("type") or (
        "event"
        if item.get("start") and item.get("end")
        else "deadline" if item.get("end") else "notice"
    )


def component(raw, zones=()):
    cal = Calendar.from_ical(
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\n"
        + "\r\n".join(zones)
        + "\r\n"
        + raw
        + "\r\nEND:VCALENDAR\r\n"
    )
    parts = [c for c in cal.subcomponents if c.name in ("VEVENT", "VTODO")]
    if len(parts) != 1:
        raise ValueError("Expected one calendar entry")
    return parts[0]


def semantic(item):
    result = {k: item.get(k, "") for k in DISPLAY_FIELDS}
    result["type"] = entry_type(item)
    # Recurrence exception dates matter; transport timestamps and serialization order do not.
    if item.get("raw"):
        try:
            ev = component(item["raw"])
            prop = ev.get("DUE") or ev.get("DTSTART")
            if prop is not None and not result.get("timezone"):
                result["timezone"] = str(prop.params.get("TZID", ""))
            result["exceptions"] = {
                k: sorted(
                    v.to_ical().decode()
                    for v in (ev[k] if isinstance(ev[k], list) else [ev[k]])
                )
                for k in ("EXDATE", "RDATE", "RECURRENCE-ID")
                if ev.get(k)
            }
        except (ValueError, TypeError):
            pass
    return result


def preview(content, first, last):
    """Expand only a bounded view. Keep floating/all-day dates in calendar-local time."""
    lower = date.fromisoformat(first)
    upper = date.fromisoformat(last)
    if not 0 < (upper - lower).days <= 42:
        raise ValueError("Choose a calendar window of up to six weeks.")
    rows = []
    warnings = set()
    entries = content.get("events", [])
    zones = content.get("timezones", [])
    overrides = set()
    for item in entries:
        try:
            ev = component(item["raw"], zones)
            if ev.get("RECURRENCE-ID"):
                overrides.add((item["uid"], ev.decoded("RECURRENCE-ID").isoformat()))
        except (KeyError, ValueError, TypeError):
            pass
    for item in entries:
        kind = entry_type(item)
        try:
            ev = component(item["raw"], zones)
            anchor = ev.decoded("DUE") if kind == "deadline" else ev.decoded("DTSTART")
            if isinstance(anchor, datetime) and (
                not item.get("timezone") or item.get("timezone", "").startswith("UTC")
            ):
                text = item.get("end") if kind == "deadline" else item.get("start")
                original = datetime.fromisoformat(text.replace("Z", "+00:00"))
                if original.tzinfo:
                    anchor = original
            all_day = not isinstance(anchor, datetime)
            start_dt = datetime.combine(anchor, time()) if all_day else anchor
            start = ev.decoded("DTSTART", None)
            end = ev.decoded("DTEND", None)
            duration = (
                (end - start)
                if kind == "event" and start is not None and end is not None
                else timedelta()
            )
            # Very long spans are bounded by the maximum date range Python supports.
            since = datetime.combine(lower, time(), tzinfo=start_dt.tzinfo) - min(
                duration, timedelta(days=36600)
            )
            until = datetime.combine(upper, time(), tzinfo=start_dt.tzinfo)
            dates = {start_dt}
            rule = ev.get("RRULE")
            if rule and not ev.get("RECURRENCE-ID"):
                rule_text = rule.to_ical().decode()
                freq = str(rule.get("FREQ", [""])[0])
                if (
                    freq not in ("DAILY", "WEEKLY", "MONTHLY", "YEARLY")
                    or any(rule.get(k) for k in ("BYSECOND", "BYMINUTE", "BYHOUR"))
                    or abs((since - start_dt).days) > 36600
                ):
                    warnings.add(
                        "Some imported repeat patterns are not expanded in this preview. Their original rules are kept in the subscription."
                    )
                else:
                    if start_dt.tzinfo and rule.get("UNTIL"):
                        stop = rule["UNTIL"][0]
                        if not isinstance(stop, datetime):
                            stop = datetime.combine(stop, time(23, 59, 59))
                        if not stop.tzinfo:
                            stop = stop.replace(tzinfo=start_dt.tzinfo)
                        import re

                        rule_text = re.sub(
                            r"UNTIL=[^;]+",
                            "UNTIL="
                            + stop.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
                            rule_text,
                        )
                    dates = set()
                    expanded = rrulestr(rule_text, dtstart=start_dt).xafter(
                        since, inc=True
                    )
                    for occurrence in islice(expanded, 1501):
                        if occurrence >= until:
                            break
                        dates.add(occurrence)
                    if len(dates) > 1500:
                        dates = set(sorted(dates)[:1500])
                        warnings.add("Preview limited to 1,500 occurrences per entry.")

            def extra_dates(name):
                props = ev.get(name, [])
                props = props if isinstance(props, list) else [props]
                for prop in props:
                    for value in prop.dts:
                        d = value.dt
                        if isinstance(d, tuple):
                            d = d[0]
                        yield (
                            datetime.combine(d, time(), tzinfo=start_dt.tzinfo)
                            if not isinstance(d, datetime)
                            else d
                        )

            dates.update(extra_dates("RDATE"))
            dates.difference_update(extra_dates("EXDATE"))
            for occurrence in sorted(dates):
                original = (occurrence.date() if all_day else occurrence).isoformat()
                if not ev.get("RECURRENCE-ID") and (item["uid"], original) in overrides:
                    continue
                finish = occurrence + duration
                if occurrence >= until or (
                    finish <= datetime.combine(lower, time(), tzinfo=start_dt.tzinfo)
                    if duration
                    else occurrence
                    < datetime.combine(lower, time(), tzinfo=start_dt.tzinfo)
                ):
                    continue
                shown = dict(item)
                shown["start"] = "" if kind == "deadline" else original
                shown["end"] = (
                    original
                    if kind == "deadline"
                    else (
                        (finish.date() if all_day else finish).isoformat()
                        if kind == "event"
                        else ""
                    )
                )
                shown["type"] = kind
                shown.pop("raw", None)
                shown["key"] = item["uid"] + "|" + item.get("recurrence_id", "")
                rows.append(shown)
                if len(rows) >= 2000:
                    return {
                        "events": rows,
                        "warnings": sorted(
                            warnings
                            | {
                                "Preview limited to 2,000 occurrences. Narrow the date range to see more."
                            }
                        ),
                    }
        except (ValueError, TypeError, KeyError, OverflowError):
            warnings.add(
                "An imported entry could not be expanded. Its dates and repeat pattern remain available in the event list."
            )
    return {
        "events": sorted(rows, key=lambda e: e["start"] or e["end"]),
        "warnings": sorted(warnings),
    }
