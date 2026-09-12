import copy, io, sqlite3
import pytest
from app import create_app


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("TIMEGRID_DATA_DIR", str(tmp_path))
    app = create_app(
        {
            "TESTING": True,
            "DATABASE": str(tmp_path / "test.db"),
            "SECRET_KEY": "test-secret",
            "SESSION_COOKIE_SECURE": False,
        }
    )
    manager = app.test_cli_runner().invoke(
        args=["create-manager", "manager", "--password", "manager-password-long"]
    )
    assert manager.exit_code == 0, manager.output
    return app


def client(app, name=None):
    c = app.test_client()
    if name:
        path = "/api/login" if name == "manager" else "/api/register"
        password = (
            "manager-password-long"
            if name == "manager"
            else "contributor-password-long"
        )
        assert post(c, path, {"username": name, "password": password}).status_code in (
            200,
            201,
        )
    return c


def post(c, path, data):
    token = c.get("/api/session").json["csrf"]
    return c.post(path, json=data, headers={"X-CSRF-Token": token})


def content(title="Campus calendar"):
    return {
        "title": title,
        "description": "Public events",
        "hashtags": ["Campus"],
        "timezones": [],
        "sources": [],
        "events": [
            {
                "uid": "one@example",
                "title": "Orientation",
                "start": "2026-09-15T09:00:00-04:00",
                "end": "2026-09-15T10:00:00-04:00",
                "location": "Hall",
                "description": "Welcome",
            }
        ],
    }


def propose(c, value=None, target=None, base=0):
    r = post(
        c,
        "/api/proposals",
        {
            "content": value or content(),
            "target": target,
            "base_revision": base,
            "message": "Verified dates against source.",
        },
    )
    assert r.status_code == 201, r.json
    return r.json["id"]


def publish(app, value=None):
    a = client(app, "author")
    m = client(app, "manager")
    pid = propose(a, value)
    r = post(
        m,
        f"/api/proposals/{pid}/review",
        {"decision": "accept", "hashtags": ["reviewed"]},
    )
    assert r.status_code == 200, r.json
    return a, m, r.json["slug"]


def test_public_subscription_and_review(setup):
    a = client(setup, "author")
    m = client(setup, "manager")
    anon = client(setup)
    pid = propose(a)
    assert anon.get("/api/calendars").json["calendars"] == []
    assert (
        post(a, f"/api/proposals/{pid}/review", {"decision": "accept"}).status_code
        == 403
    )
    assert anon.get("/api/proposals").status_code == 401
    r = post(
        m,
        f"/api/proposals/{pid}/review",
        {"decision": "accept", "hashtags": ["approved"]},
    )
    assert r.status_code == 200, r.json
    slug = r.json["slug"]
    c = anon.get("/api/calendars/" + slug).json
    assert c["content"]["hashtags"] == ["approved"]
    feed = anon.get("/feeds/" + slug + ".ics")
    assert feed.status_code == 200
    assert b"Orientation" in feed.data
    assert (
        anon.get(
            "/feeds/" + slug + ".ics", headers={"If-None-Match": feed.headers["ETag"]}
        ).status_code
        == 304
    )
    assert anon.get("/api/calendars?tag=approved").json["calendars"]


def test_edit_reject_accept_and_stale(setup):
    a, m, slug = publish(setup)
    anon = client(setup)
    c = anon.get("/api/calendars/" + slug).json
    edited = copy.deepcopy(c["content"])
    edited["events"][0]["title"] = "Updated orientation"
    p1 = propose(a, edited, c["id"], 1)
    p2 = propose(a, edited, c["id"], 1)
    assert b"Updated orientation" not in anon.get("/feeds/" + slug + ".ics").data
    assert (
        post(m, f"/api/proposals/{p1}/review", {"decision": "accept"}).status_code
        == 200
    )
    assert (
        post(m, f"/api/proposals/{p2}/review", {"decision": "accept"}).status_code
        == 409
    )
    assert (
        post(m, f"/api/proposals/{p2}/review", {"decision": "reject"}).status_code
        == 400
    )
    assert (
        post(
            m,
            f"/api/proposals/{p2}/review",
            {"decision": "reject", "reason": "Please rebase."},
        ).status_code
        == 200
    )
    feed = anon.get("/feeds/" + slug + ".ics")
    assert b"Updated orientation" in feed.data
    assert b"SEQUENCE:2" in feed.data
    assert (
        post(m, f"/api/proposals/{p1}/review", {"decision": "accept"}).status_code
        == 409
    )
    assert len(anon.get("/api/calendars/" + slug + "/revisions").json["revisions"]) == 2


def test_proposal_privacy_csrf_and_roles(setup):
    a = client(setup, "alice")
    b = client(setup, "bravo")
    propose(a)
    assert b.get("/api/proposals").json["proposals"] == []
    assert a.post("/api/proposals", json={}).status_code == 403
    assert (
        post(
            b,
            "/api/register",
            {"username": "evil", "password": "long-password-here", "role": "manager"},
        ).json["user"]["role"]
        == "contributor"
    )


def test_import_recurring_and_combine(setup):
    a = client(setup, "author")
    m = client(setup, "manager")
    raw = b"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:recurring@example\r\nDTSTART;VALUE=DATE:20260915\r\nDTEND;VALUE=DATE:20260916\r\nRRULE:FREQ=WEEKLY;COUNT=5\r\nSUMMARY:Seminar\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    token = a.get("/api/session").json["csrf"]
    r = a.post(
        "/api/import",
        data={"file": (io.BytesIO(raw), "test.ics")},
        headers={"X-CSRF-Token": token},
    )
    assert r.status_code == 200, r.json
    p = propose(a, r.json)
    r = post(m, f"/api/proposals/{p}/review", {"decision": "accept"})
    s1 = r.json["slug"]
    p = propose(a)
    s2 = post(m, f"/api/proposals/{p}/review", {"decision": "accept"}).json["slug"]
    merged = post(a, "/api/combine", {"ids": [s1, s2]})
    assert merged.status_code == 200, merged.json
    assert len(merged.json["events"]) == 2
    p = propose(a, merged.json)
    s3 = post(m, f"/api/proposals/{p}/review", {"decision": "accept"}).json["slug"]
    feed = a.get("/feeds/" + s3 + ".ics").data
    assert b"RRULE:FREQ=WEEKLY;COUNT=5" in feed
    assert b"DTSTART;VALUE=DATE:20260915" in feed
    assert len(a.get("/api/calendars/" + s3).json["content"]["sources"]) == 2


def test_deletion_and_validation(setup):
    a, m, slug = publish(setup)
    c = a.get("/api/calendars/" + slug).json
    c["content"]["events"] = []
    p = propose(a, c["content"], c["id"], 1)
    assert (
        post(m, f"/api/proposals/{p}/review", {"decision": "accept"}).status_code == 200
    )
    assert b"BEGIN:VEVENT" not in a.get("/feeds/" + slug + ".ics").data
    bad = content()
    bad["events"][0]["end"] = "2020-01-01"
    assert (
        post(a, "/api/proposals", {"content": bad, "message": "Bad dates"}).status_code
        == 400
    )
    bad = content()
    bad["events"] *= 2
    assert (
        post(a, "/api/proposals", {"content": bad, "message": "Duplicate"}).status_code
        == 400
    )


def test_backup_restores(setup, tmp_path):
    publish(setup)
    dest = tmp_path / "backup.db"
    r = setup.test_cli_runner().invoke(args=["backup", str(dest)])
    assert r.exit_code == 0, r.output
    with sqlite3.connect(dest) as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert db.execute("SELECT count(*) FROM calendars").fetchone()[0] == 1


def test_named_timezone_and_recurrence_survive_title_edit(setup):
    a = client(setup, "author")
    m = client(setup, "manager")
    raw = b"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:zone@example\r\nDTSTART;TZID=America/Toronto:20260915T090000\r\nDTEND;TZID=America/Toronto:20260915T100000\r\nRRULE:FREQ=WEEKLY;COUNT=10\r\nSUMMARY:Weekly class\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    token = a.get("/api/session").json["csrf"]
    r = a.post(
        "/api/import",
        data={"file": (io.BytesIO(raw), "zone.ics")},
        headers={"X-CSRF-Token": token},
    )
    assert r.status_code == 200, r.json
    r.json["events"][0]["title"] = "Renamed class"
    p = propose(a, r.json)
    slug = post(m, f"/api/proposals/{p}/review", {"decision": "accept"}).json["slug"]
    feed = a.get("/feeds/" + slug + ".ics").data
    assert b"DTSTART;TZID=America/Toronto:20260915T090000" in feed
    assert b"RRULE:FREQ=WEEKLY;COUNT=10" in feed
    assert b"DTSTAMP:" in feed


def test_migration_refuses_private_and_preserves_old_urls(setup, tmp_path):
    import json

    raw = "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:legacy@example\r\nDTSTART;VALUE=DATE:20260915\r\nSUMMARY:Legacy\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    (tmp_path / "calendar.ics").write_text(raw)
    record = {
        "visibility": "private",
        "listed": True,
        "slug": "legacy",
        "title": "Legacy",
        "file": "calendar.ics",
    }
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps([record]))
    r = setup.test_cli_runner().invoke(args=["import-public", str(manifest)])
    assert r.exit_code != 0
    assert client(setup).get("/api/calendars").json["calendars"] == []
    record["visibility"] = "public"
    manifest.write_text(json.dumps([record]))
    r = setup.test_cli_runner().invoke(args=["import-public", str(manifest)])
    assert r.exit_code == 0, r.output
    assert client(setup).get("/bundle/legacy.ics").status_code == 200
    r = setup.test_cli_runner().invoke(args=["import-public", str(manifest)])
    assert r.exit_code != 0
    assert len(client(setup).get("/api/calendars").json["calendars"]) == 1


def test_three_entry_types_repeat_preview_and_no_explanation(setup):
    a = client(setup, "author")
    m = client(setup, "manager")
    c = content()
    c["events"] = [
        {
            "uid": "event",
            "type": "event",
            "title": "Workshop",
            "start": "2026-09-15T09:00",
            "end": "2026-09-15T10:00",
            "timezone": "America/Toronto",
            "recurrence": "FREQ=WEEKLY;COUNT=3",
        },
        {
            "uid": "deadline",
            "type": "deadline",
            "title": "Application due",
            "start": "",
            "end": "2026-09-16T17:00",
            "timezone": "America/Toronto",
            "recurrence": "FREQ=WEEKLY;COUNT=2",
        },
        {
            "uid": "notice",
            "type": "notice",
            "title": "Results announced",
            "start": "2026-09-18",
            "end": "",
            "recurrence": "",
        },
    ]
    result = post(a, "/api/proposals", {"content": c})
    assert result.status_code == 201, result.json
    pid = result.json["id"]
    record = a.get("/api/proposals").json["proposals"][0]
    assert record["message"] == ""
    view = post(
        a,
        "/api/preview",
        {"content": record["content"], "start": "2026-09-01", "end": "2026-10-01"},
    )
    assert view.status_code == 200, view.json
    assert len(view.json["events"]) == 6, view.json
    assert not view.json["warnings"]
    slug = post(m, f"/api/proposals/{pid}/review", {"decision": "accept"}).json["slug"]
    raw = a.get("/feeds/" + slug + ".ics").data
    from app import decode_ics

    with setup.app_context():
        roundtrip = decode_ics(raw)
    due = next(e for e in roundtrip["events"] if e["type"] == "deadline")
    assert not due["start"] and due["end"]
    assert b"BEGIN:VTODO" in raw and b"DUE;TZID=America/Toronto:" in raw
    current = a.get("/api/calendars/" + slug).json
    current["content"]["events"][0]["title"] = "Workshop updated"
    result = post(
        a,
        "/api/proposals",
        {"content": current["content"], "target": current["id"], "base_revision": 1},
    )
    assert result.status_code == 201, result.json


def test_semantic_diff_preserves_neutral_and_highlights_only_changed_fields(setup):
    from app import changes, clean_content

    with setup.app_context():
        old = clean_content(content())
        new = copy.deepcopy(old)
        new["events"][0]["raw"] = new["events"][0]["raw"].replace(
            "DTSTAMP:", "LAST-MODIFIED:"
        )
        assert len(changes(old, new)["unchanged"]) == 1
        new["events"][0]["start"] = "2026-09-15T09:30:00-04:00"
        diff = changes(old, new)
        assert diff["edited"][0]["fields"] == ["start"]
        assert not diff["unchanged"]


def test_preview_exceptions_and_dst(setup):
    a = client(setup, "author")
    raw = b"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:weekly\r\nDTSTART;TZID=America/Toronto:20261025T090000\r\nDTEND;TZID=America/Toronto:20261025T100000\r\nRRULE:FREQ=WEEKLY;UNTIL=20261122T235959\r\nEXDATE;TZID=America/Toronto:20261108T090000\r\nSUMMARY:Weekly\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    token = a.get("/api/session").json["csrf"]
    r = a.post(
        "/api/import",
        data={"file": (io.BytesIO(raw), "dst.ics")},
        headers={"X-CSRF-Token": token},
    )
    assert r.status_code == 200, r.json
    view = post(
        a,
        "/api/preview",
        {"content": r.json, "start": "2026-10-25", "end": "2026-11-30"},
    )
    assert view.status_code == 200, view.json
    assert len(view.json["events"]) == 4, view.json
    assert view.json["events"][0]["start"].endswith("-04:00")
    assert view.json["events"][1]["start"].endswith("-05:00")
    assert all("T09:00:00" in e["start"] for e in view.json["events"])
    assert not view.json["warnings"]


def test_invalid_entry_types_and_preview_window(setup):
    a = client(setup, "author")
    for kind, start, end in [
        ("deadline", "2026-09-01", "2026-09-02"),
        ("notice", "2026-09-01", "2026-09-02"),
        ("event", "2026-09-01", ""),
    ]:
        c = content()
        c["events"] = [
            {"uid": "bad", "title": "Bad", "type": kind, "start": start, "end": end}
        ]
        assert post(a, "/api/proposals", {"content": c}).status_code == 400
    assert (
        post(
            a,
            "/api/preview",
            {"content": content(), "start": "2020-01-01", "end": "2030-01-01"},
        ).status_code
        == 400
    )
    c = content()
    c["events"][0]["recurrence"] = "FREQ=WEEKLY;INTERVAL=0"
    assert post(a, "/api/proposals", {"content": c}).status_code == 400


def test_retimed_named_zone_entry_remains_in_preview(setup):
    a = client(setup, "author")
    c = content()
    c["events"][0].update(timezone="America/Toronto", start="2026-09-15T09:30:00-04:00")
    r = post(
        a, "/api/preview", {"content": c, "start": "2026-09-01", "end": "2026-10-01"}
    )
    assert r.status_code == 200, r.json
    assert len(r.json["events"]) == 1 and not r.json["warnings"], r.json
    assert r.json["events"][0]["start"] == "2026-09-15T09:30:00-04:00"


def test_subscription_url_import_is_authenticated_and_not_persisted(setup, monkeypatch):
    raw = b"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:away-game\r\nDTSTART:20260920T190000Z\r\nDTEND:20260920T210000Z\r\nSUMMARY:Away game\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    called = []
    monkeypatch.setattr("app.fetch_calendar", lambda url: (called.append(url) or raw))
    anon = client(setup)
    assert (
        post(
            anon, "/api/import-url", {"url": "https://calendar.example.org/sports.ics"}
        ).status_code
        == 401
    )
    assert not called
    a = client(setup, "author")
    r = post(
        a,
        "/api/import-url",
        {"url": "https://calendar.example.org/sports.ics?token=private"},
    )
    assert r.status_code == 200, r.json
    assert r.json["events"][0]["title"] == "Away game"
    assert "token=private" not in str(r.json)


def test_public_event_search_and_preview(setup):
    _, _, slug = publish(setup)
    c = client(setup)
    for q in ["Orientation", "Welcome", "Hall", '"Public events"']:
        result = c.get("/api/calendars", query_string={"q": q})
        assert [x["slug"] for x in result.json["calendars"]] == [slug]
    assert not c.get("/api/calendars?q=Orientation&location=Elsewhere").json[
        "calendars"
    ]
    r = c.get(f"/api/calendars/{slug}/preview?start=2026-09-01&end=2026-10-01")
    assert r.status_code == 200
    assert "Orientation" in r.get_data(as_text=True)
    assert c.get(f"/api/calendars/{slug}/preview?start=bad&end=bad").status_code == 400


def test_parallel_proposals_merge_and_revision_guard(setup):
    a, m, slug = publish(setup)
    original = a.get("/api/calendars/" + slug).json
    first = copy.deepcopy(original["content"])
    second = copy.deepcopy(first)
    first["events"][0]["title"] = "Changed by first"
    second["events"].append(
        {**second["events"][0], "uid": "second", "title": "Added by second"}
    )
    p1 = propose(a, first, original["id"], 1)
    p2 = propose(a, second, original["id"], 1)
    assert (
        post(m, f"/api/proposals/{p1}/review", {"decision": "accept"}).status_code
        == 200
    )
    preview = post(m, f"/api/proposals/{p2}/resolve", {"strategy": "merge"})
    assert preview.status_code == 200 and not preview.json["conflicts"]
    assert {e["title"] for e in preview.json["content"]["events"]} == {
        "Changed by first",
        "Added by second",
    }
    assert (
        post(a, f"/api/proposals/{p2}/resolve", {"strategy": "merge"}).status_code
        == 403
    )
    assert (
        post(
            m,
            f"/api/proposals/{p2}/review",
            {"decision": "accept", "strategy": "merge", "expected_revision": 1},
        ).status_code
        == 409
    )
    assert (
        post(
            m,
            f"/api/proposals/{p2}/review",
            {"decision": "accept", "strategy": "merge", "expected_revision": 2},
        ).status_code
        == 200
    )


def test_parallel_conflict_and_overwrite(setup):
    a, m, slug = publish(setup)
    original = a.get("/api/calendars/" + slug).json
    first = copy.deepcopy(original["content"])
    second = copy.deepcopy(first)
    first["events"][0]["title"] = "Current"
    second["events"] = []
    p1 = propose(a, first, original["id"], 1)
    p2 = propose(a, second, original["id"], 1)
    post(m, f"/api/proposals/{p1}/review", {"decision": "accept"})
    r = post(m, f"/api/proposals/{p2}/resolve", {"strategy": "merge"})
    assert len(r.json["conflicts"]) == 1
    assert (
        post(
            m,
            f"/api/proposals/{p2}/review",
            {"decision": "accept", "strategy": "merge", "expected_revision": 2},
        ).status_code
        == 409
    )
    r = post(m, f"/api/proposals/{p2}/resolve", {"strategy": "overwrite"})
    assert not r.json["content"]["events"]
    assert (
        post(
            m,
            f"/api/proposals/{p2}/review",
            {"decision": "accept", "strategy": "overwrite", "expected_revision": 2},
        ).status_code
        == 200
    )
    assert not a.get("/api/calendars/" + slug).json["content"]["events"]


def test_personal_folders_live_feed_privacy_and_lifecycle(setup):
    a, m, slug = publish(setup)
    other = client(setup, "other")
    anon = client(setup)
    source = a.get("/api/calendars/" + slug).json
    assert anon.get("/api/folders").status_code == 401
    r = post(a, "/api/folders", {"name": "Life"})
    assert r.status_code == 201
    fid = r.json["id"]
    work = post(a, "/api/folders", {"name": "Work"}).json["id"]
    assert (
        post(a, "/api/folders/" + fid, {"sources": [source["id"]]}).status_code == 200
    )
    folder = next(f for f in a.get("/api/folders").json["folders"] if f["id"] == fid)
    link = "/personal/" + folder["token"] + ".ics"
    assert other.get("/api/folders").json["folders"] == []
    assert post(other, "/api/folders/" + fid, {"name": "stolen"}).status_code == 404
    assert (
        other.get(
            "/api/folders/" + fid + "/preview?start=2026-09-01&end=2026-10-01"
        ).status_code
        == 404
    )
    first = anon.get(link)
    assert first.status_code == 200 and b"Orientation" in first.data
    assert (
        anon.get(link, headers={"If-None-Match": first.headers["ETag"]}).status_code
        == 304
    )
    updated = copy.deepcopy(source["content"])
    updated["events"][0]["title"] = "Latest source update"
    pid = propose(a, updated, source["id"], 1)
    post(m, "/api/proposals/" + pid + "/review", {"decision": "accept"})
    latest = anon.get(link, headers={"If-None-Match": first.headers["ETag"]})
    assert latest.status_code == 200 and b"Latest source update" in latest.data
    assert b"Orientation" not in latest.data
    assert post(a, "/api/folders/" + fid, {"sources": []}).status_code == 200
    assert b"BEGIN:VEVENT" not in anon.get(link).data
    assert post(a, "/api/folders/" + fid + "/rotate", {}).status_code == 200
    assert anon.get(link).status_code == 404
    token = next(
        f["token"] for f in a.get("/api/folders").json["folders"] if f["id"] == fid
    )
    csrf = a.get("/api/session").json["csrf"]
    assert (
        a.delete("/api/folders/" + fid, headers={"X-CSRF-Token": csrf}).status_code
        == 200
    )
    assert anon.get("/personal/" + token + ".ics").status_code == 404
    assert a.get("/api/folders").json["folders"][0]["id"] == work


def test_folder_combines_multiple_sources_with_distinct_uids(setup):
    a, m, slug = publish(setup)
    p = propose(a, content("Second source"))
    r = post(m, "/api/proposals/" + p + "/review", {"decision": "accept"})
    second = a.get("/api/calendars/" + r.json["slug"]).json
    first = a.get("/api/calendars/" + slug).json
    fid = post(a, "/api/folders", {"name": "Combined"}).json["id"]
    post(a, "/api/folders/" + fid, {"sources": [first["id"], second["id"]]})
    token = a.get("/api/folders").json["folders"][0]["token"]
    from icalendar import Calendar

    events = Calendar.from_ical(
        client(setup).get("/personal/" + token + ".ics").data
    ).walk("VEVENT")
    assert len(events) == 2 and len({str(e["uid"]) for e in events}) == 2
