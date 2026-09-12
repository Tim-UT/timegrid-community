from calendar_search import score

CONTENT = {
    "title": "Campus",
    "hashtags": ["engineering"],
    "events": [
        {
            "title": "MIE312 Fluid Mechanics",
            "description": "Weekly laboratory session",
            "location": "Wallberg Building",
        }
    ],
}


def test_search_event_fields_and_phrases():
    for query in ["mie", "Wallberg", "laboratory", '"Fluid Mechanics"', "mie wallberg"]:
        assert score(CONTENT, {"q": query}) is not None
    assert score(CONTENT, {"q": '"mechanics fluid"'}) is None
    assert score(CONTENT, {"q": "mie", "exclude": "laboratory"}) is None
    assert score(CONTENT, {"q": "mie", "location": "Wallberg"}) is not None
    assert score(CONTENT, {"q": "mie", "location": "elsewhere"}) is None


def test_ranking_priority():
    tag = {"hashtags": ["sports"]}
    event = {"events": [{"title": "sports"}]}
    assert score(tag, {"q": "sports"}) > score(event, {"q": "sports"})
    assert score(event, {"q": "sports", "priority": "event"}) > score(
        tag, {"q": "sports", "priority": "event"}
    )
