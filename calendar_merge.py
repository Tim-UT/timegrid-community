"""Three-way merging with explicit conflicts; deletions are never silently revived."""

from copy import deepcopy
import json
from calendar_domain import semantic


def merge_calendar(base, current, proposed, resolutions=None):
    resolutions = resolutions or {}
    conflicts = []

    def choose(key, label, before, live, incoming, event=False):
        same = lambda a, b: (
            {e.get("recurrence_id", ""): semantic(e) for e in a} if event and a else a
        ) == (
            {e.get("recurrence_id", ""): semantic(e) for e in b} if event and b else b
        )
        if same(live, incoming):
            return deepcopy(live)
        if same(before, incoming):
            return deepcopy(live)
        if same(before, live):
            return deepcopy(incoming)
        choice = resolutions.get(key)
        conflicts.append(
            {
                "id": key,
                "label": label,
                "base": before,
                "current": live,
                "proposal": incoming,
                "resolved": choice in ("current", "proposal"),
                "event": event,
            }
        )
        return deepcopy(incoming if choice == "proposal" else live)

    result = deepcopy(current)
    for field in ("title", "description", "timezones"):
        result[field] = choose(
            field,
            field.title(),
            base.get(field),
            current.get(field),
            proposed.get(field),
        )
    # Set membership merges independent additions/removals, without resurrecting deleted tags.
    for field in ("hashtags", "sources"):
        key = lambda x: json.dumps(x, sort_keys=True)
        maps = [
            {key(x): x for x in c.get(field, [])} for c in (base, current, proposed)
        ]
        b, c, p = maps
        result[field] = [
            (p.get(k) or c.get(k) or b[k])
            for k in dict.fromkeys([*c, *p])
            if (k in c and k in p) or (k not in b and (k in c or k in p))
        ]

    def groups(content):
        result = {}
        for e in content.get("events", []):
            result.setdefault(e["uid"], []).append(e)
        return result

    b, c, p = (groups(x) for x in (base, current, proposed))
    result["events"] = []
    for k in dict.fromkeys([*c, *p, *b]):
        group = p.get(k) or c.get(k) or b[k]
        events = choose(
            "event:" + k,
            group[0].get("title", "Entry"),
            b.get(k),
            c.get(k),
            p.get(k),
            True,
        )
        if events is not None:
            result["events"].extend(events)
    return result, conflicts
