"""Rank public calendar content with phrase and field filters."""

import re


def score(content, args):
    fields = {
        "hashtag": content.get("hashtags", []),
        "calendar": [content.get("title", "")],
        "description": [content.get("description", "")],
        "event": [],
        "location": [],
    }
    for event in content.get("events", []):
        fields["event"].append(event.get("title", ""))
        fields["description"].append(event.get("description", ""))
        fields["location"].append(event.get("location", ""))
    fields = {k: [v.casefold() for v in values] for k, values in fields.items()}
    query = args.get("q", "").casefold().strip()
    tokens = re.findall(r'"([^"]+)"|(\S+)', query)
    terms = [a or b for a, b in tokens]
    weights = {
        "hashtag": 100,
        "calendar": 50,
        "event": 40,
        "location": 30,
        "description": 20,
    }
    priority = args.get("priority", "hashtag")
    if priority in weights:
        weights[priority] = 200
    total = 0
    for term in terms:
        term = term.lstrip("#")
        matches = [
            weights[k] + (10 if term in values else 0)
            for k, values in fields.items()
            if any(term in v for v in values)
        ]
        if not matches:
            return None
        total += max(matches)
    for key in fields:
        value = args.get(key, "").casefold().strip().lstrip("#")
        if value and not any(value in v for v in fields[key]):
            return None
    phrase = args.get("phrase", "").casefold().strip()
    if phrase and not any(phrase in v for values in fields.values() for v in values):
        return None
    excluded = args.get("exclude", "").casefold().split()
    if any(
        term in v for term in excluded for values in fields.values() for v in values
    ):
        return None
    return total
