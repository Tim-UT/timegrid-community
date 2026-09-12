from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from icalendar import Todo, Alarm, Calendar
from calendar_compat import calendar_event, restore_deadline


def task(due):
    t = Todo()
    t.add("uid", "deadline@example")
    t.add("summary", "Submit assignment")
    t.add("due", due)
    t.add("description", "Upload the report")
    t.add("location", "Portal")
    return t


def test_timed_deadline_marker_keeps_due_zone_recurrence_and_alarm():
    due = datetime(2026, 10, 26, 17, tzinfo=ZoneInfo("America/Toronto"))
    t = task(due)
    t.add("rrule", {"freq": "weekly", "count": 3})
    t.add("exdate", due + timedelta(days=7))
    alarm = Alarm()
    alarm.add("action", "DISPLAY")
    alarm.add("description", "Due soon")
    alarm.add("trigger", timedelta(minutes=-15), parameters={"RELATED": "END"})
    t.add_component(alarm)
    e = calendar_event(t)
    assert e.name == "VEVENT" and e.decoded("DTSTART") == due
    assert e.decoded("DTEND") - e.decoded("DTSTART") == timedelta(minutes=1)
    assert e["DTSTART"].params["TZID"] == "America/Toronto"
    assert e["RRULE"] == t["RRULE"] and e["EXDATE"] == t["EXDATE"]
    assert e.subcomponents[0]["TRIGGER"].params["RELATED"] == "START"
    assert str(e["SUMMARY"]) == "[Deadline] Submit assignment"
    assert not e.get("DUE") and e["TRANSP"] == "TRANSPARENT"
    restored = restore_deadline(e)
    assert restored.decoded("DUE") == due and not restored.get("DTSTART")
    assert (
        restored["SUMMARY"] == t["SUMMARY"]
        and restored["DESCRIPTION"] == t["DESCRIPTION"]
    )
    assert t.name == "VTODO" and not t.get("DTSTART")


def test_all_day_due_marker_and_overrides():
    t = task(date(2026, 9, 30))
    t.add("recurrence-id", date(2026, 9, 29))
    t.add("status", "NEEDS-ACTION")
    e = calendar_event(t)
    assert e.decoded("DTSTART") == date(2026, 9, 30)
    assert e.decoded("DTEND") == date(2026, 10, 1)
    assert e["RECURRENCE-ID"] == t["RECURRENCE-ID"] and not e.get("STATUS")
    assert restore_deadline(e).decoded("DUE") == date(2026, 9, 30)
