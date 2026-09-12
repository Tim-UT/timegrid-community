"""Calendar-app projection of deadline tasks, reversible on TimeGrid import."""

from copy import deepcopy
from datetime import datetime, timedelta
from icalendar import Event, Todo


def calendar_event(task):
    due = task.get("DUE")
    if due is None:
        raise ValueError("Deadline is missing its due date")
    ev = Event()
    for key, value in task.items():
        if key not in (
            "DUE",
            "DTSTART",
            "DTEND",
            "DURATION",
            "COMPLETED",
            "PERCENT-COMPLETE",
            "STATUS",
        ):
            ev[key] = deepcopy(value)
    ev.subcomponents = deepcopy(task.subcomponents)
    ev["DTSTART"] = deepcopy(due)
    end = due.dt + (
        timedelta(minutes=1) if isinstance(due.dt, datetime) else timedelta(days=1)
    )
    ev.add("DTEND", end)
    ev["DTEND"].params.update(due.params)
    ev["X-TIMEGRID-TYPE"] = "deadline"
    ev["X-TIMEGRID-ORIGINAL-SUMMARY"] = str(task.get("SUMMARY", "Deadline"))
    ev["SUMMARY"] = "[Deadline] " + str(task.get("SUMMARY", "Deadline"))
    ev["TRANSP"] = "TRANSPARENT"
    if str(task.get("STATUS", "")) == "CANCELLED":
        ev["STATUS"] = "CANCELLED"
    for alarm in ev.subcomponents:
        trigger = alarm.get("TRIGGER")
        if trigger is not None and trigger.params.get("RELATED") == "END":
            trigger.params["RELATED"] = "START"
    return ev


def restore_deadline(ev):
    if (
        ev.name != "VEVENT"
        or str(ev.get("X-TIMEGRID-TYPE", "")) != "deadline"
        or not ev.get("DTSTART")
    ):
        return ev
    task = Todo()
    for key, value in ev.items():
        if key not in (
            "DTSTART",
            "DTEND",
            "DURATION",
            "TRANSP",
            "X-TIMEGRID-TYPE",
            "X-TIMEGRID-ORIGINAL-SUMMARY",
        ):
            task[key] = deepcopy(value)
    task["DUE"] = deepcopy(ev["DTSTART"])
    task["SUMMARY"] = str(
        ev.get("X-TIMEGRID-ORIGINAL-SUMMARY", ev.get("SUMMARY", "Deadline"))
    )
    task.subcomponents = deepcopy(ev.subcomponents)
    for alarm in task.subcomponents:
        trigger = alarm.get("TRIGGER")
        if trigger is not None and trigger.params.get("RELATED", "START") == "START":
            trigger.params["RELATED"] = "END"
    return task
