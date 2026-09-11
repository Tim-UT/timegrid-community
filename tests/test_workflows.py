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
