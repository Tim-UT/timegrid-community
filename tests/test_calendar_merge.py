from copy import deepcopy
from calendar_merge import merge_calendar


def content():
    return {
        "title": "Base",
        "description": "",
        "hashtags": ["old", "keep"],
        "sources": [],
        "timezones": [],
        "events": [
            {"uid": "one", "title": "One", "start": "2026-09-01", "end": "2026-09-02"}
        ],
    }


def test_independent_additions_and_tag_deletion():
    b = content()
    c = deepcopy(b)
    p = deepcopy(b)
    c["events"].append({"uid": "two", "title": "Two"})
    p["events"].append({"uid": "three", "title": "Three"})
    c["hashtags"].append("live")
    p["hashtags"] = ["keep", "incoming"]
    result, conflicts = merge_calendar(b, c, p)
    assert not conflicts
    assert {e["uid"] for e in result["events"]} == {"one", "two", "three"}
    assert set(result["hashtags"]) == {"keep", "live", "incoming"}


def test_edit_delete_conflict_and_explicit_resolution():
    b = content()
    c = deepcopy(b)
    p = deepcopy(b)
    c["events"][0]["title"] = "Edited"
    p["events"] = []
    result, conflicts = merge_calendar(b, c, p)
    assert len(conflicts) == 1 and not conflicts[0]["resolved"]
    result, conflicts = merge_calendar(b, c, p, {"event:one": "proposal"})
    assert not result["events"] and conflicts[0]["resolved"]
    result, _ = merge_calendar(b, c, p, {"event:one": "current"})
    assert result["events"][0]["title"] == "Edited"


def test_recurring_series_is_not_partially_deleted():
    b = content()
    c = deepcopy(b)
    p = deepcopy(b)
    c["events"].append(
        {"uid": "one", "recurrence_id": "2026-09-08", "title": "Moved occurrence"}
    )
    p["events"] = []
    result, conflicts = merge_calendar(b, c, p)
    assert len(conflicts) == 1 and len(result["events"]) == 2


def test_same_edit_and_independent_metadata():
    b = content()
    c = deepcopy(b)
    p = deepcopy(b)
    c["events"][0]["title"] = p["events"][0]["title"] = "Same"
    c["title"] = "New title"
    p["description"] = "New description"
    result, conflicts = merge_calendar(b, c, p)
    assert (
        not conflicts
        and result["title"] == "New title"
        and result["description"] == "New description"
    )
