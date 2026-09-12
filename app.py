"""TimeGrid: public subscription calendars with reviewed contributions."""

from __future__ import annotations
from calendar_search import score as search_score
from calendar_merge import merge_calendar
import hashlib, json, os, re, secrets, sqlite3, time, uuid
from datetime import date, datetime, timezone, timedelta
from functools import wraps
from pathlib import Path
import click
from flask import Flask, abort, g, jsonify, render_template, request, session
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix
from icalendar import Calendar, Event, Todo, Timezone, vRecur
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from calendar_domain import entry_type, semantic, component, preview
from subscription_import import fetch_calendar

ROOT = Path(__file__).parent


def now():
    return datetime.now(timezone.utc).isoformat()


def dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def problem(message, status=400):
    abort(status, description=message)


def identifier():
    return uuid.uuid4().hex


def parse_date(value):
    if not isinstance(value, str):
        problem("Event dates must be ISO dates or date-times.")
    try:
        return (
            date.fromisoformat(value)
            if len(value) == 10
            else datetime.fromisoformat(value.replace("Z", "+00:00"))
        )
    except ValueError:
        problem("Invalid event date: " + value[:80])


def event_key(event):
    return event["uid"] + "|" + event.get("recurrence_id", "")


def decode_ics(raw):
    try:
        cal = Calendar.from_ical(raw)
        if cal.name != "VCALENDAR":
            raise ValueError()
        events = []
        for ev in (c for c in cal.subcomponents if c.name in ("VEVENT", "VTODO")):
            kind = (
                "deadline"
                if ev.name == "VTODO"
                else "event" if ev.get("DTEND") or ev.get("DURATION") else "notice"
            )
            start = ev.decoded("DTSTART", None) if kind != "deadline" else None
            end = (
                ev.decoded("DUE", None)
                if kind == "deadline"
                else ev.decoded("DTEND", None)
            )
            if end is None and start is not None and ev.get("DURATION"):
                end = start + ev.decoded("DURATION")
            if not start and not end:
                raise ValueError("Missing date")
            date_prop = ev.get("DUE") if kind == "deadline" else ev.get("DTSTART")
            events.append(
                {
                    "uid": str(ev.get("UID") or identifier() + "@timegrid"),
                    "title": str(ev.get("SUMMARY", "Untitled event")),
                    "type": kind,
                    "timezone": (
                        str(date_prop.params.get("TZID", "")) if date_prop else ""
                    ),
                    "start": start.isoformat() if start else "",
                    "end": end.isoformat() if end else "",
                    "description": str(ev.get("DESCRIPTION", "")),
                    "location": str(ev.get("LOCATION", "")),
                    "recurrence_id": str(ev.get("RECURRENCE-ID", "")),
                    "recurrence": (
                        ev.get("RRULE").to_ical().decode() if ev.get("RRULE") else ""
                    ),
                    "raw": ev.to_ical().decode(),
                }
            )
        return {
            "title": str(cal.get("X-WR-CALNAME", "Imported calendar")),
            "description": str(cal.get("X-WR-CALDESC", "")),
            "hashtags": [],
            "events": events,
            "timezones": [tz.to_ical().decode() for tz in cal.walk("VTIMEZONE")],
            "sources": [],
        }
    except Exception as exc:
        problem("Could not read this ICS calendar. Check its events, dates and format.")


def clean_content(value):
    if not isinstance(value, dict):
        problem("Calendar content is required.")
    title = str(value.get("title", "")).strip()
    if not title or len(title) > 160:
        problem("Use a calendar title between 1 and 160 characters.")
    tags = value.get("hashtags", [])
    if not isinstance(tags, list) or len(tags) > 12:
        problem("Use at most 12 hashtags.")
    tags = sorted(
        set(str(t).strip().lstrip("#").lower() for t in tags if str(t).strip())
    )
    if any(not re.fullmatch(r"[\w-]{1,40}", t) for t in tags):
        problem("Hashtags may contain letters, numbers, underscores and hyphens.")
    events = value.get("events", [])
    if not isinstance(events, list) or len(events) > 5000:
        problem("A calendar can contain at most 5,000 events.")
    out = []
    keys = set()
    for item in events:
        if not isinstance(item, dict):
            problem("Invalid event.")
        uid = str(item.get("uid") or identifier() + "@timegrid")
        if len(uid) > 255 or "\n" in uid or "\r" in uid:
            problem("Invalid event identifier.")
        title_ev = str(item.get("title", "")).strip()
        if not title_ev or len(title_ev) > 300:
            problem("Each event needs a title of at most 300 characters.")
        kind = entry_type(item)
        if kind not in ("event", "deadline", "notice"):
            problem("Choose event, deadline, or notice.")
        if kind == "event" and (not item.get("start") or not item.get("end")):
            problem("An event needs both a start and an end.")
        if kind == "deadline" and (item.get("start") or not item.get("end")):
            problem("A deadline needs only a due date.")
        if kind == "notice" and (not item.get("start") or item.get("end")):
            problem("A notice needs only a start date.")
        start = parse_date(item["start"]) if item.get("start") else None
        end = parse_date(item["end"]) if item.get("end") else None
        zone = str(item.get("timezone", ""))
        if zone:
            try:
                fixed = re.fullmatch(r"UTC([+-])(\d{2}):(\d{2})", zone)
                if fixed:
                    minutes = int(fixed[2]) * 60 + int(fixed[3])
                    if int(fixed[3]) > 59 or minutes >= 24 * 60:
                        raise ValueError()
                    tz = timezone(
                        timedelta(minutes=minutes * (1 if fixed[1] == "+" else -1))
                    )
                else:
                    tz = ZoneInfo(zone)
            except (ZoneInfoNotFoundError, ValueError):
                try:
                    original = component(
                        item.get("raw", ""), value.get("timezones", [])
                    )
                    prop = original.get("DUE") or original.get("DTSTART")
                    if str(prop.params.get("TZID", "")) != zone:
                        raise ValueError()
                    tz = prop.dt.tzinfo
                    if tz is None:
                        raise ValueError()
                except Exception:
                    problem("Choose a valid time zone.")
            if isinstance(start, datetime):
                start = (
                    start.astimezone(tz) if start.tzinfo else start.replace(tzinfo=tz)
                )
            if isinstance(end, datetime):
                end = end.astimezone(tz) if end.tzinfo else end.replace(tzinfo=tz)
        if start is not None and end is not None:
            try:
                if type(start) != type(end) or end <= start:
                    problem(
                        "Event end must be after its start, with matching date types."
                    )
            except TypeError:
                problem("Start and end must use matching timezone formats.")
        try:
            ev = (
                component(item["raw"], value.get("timezones", []))
                if item.get("raw")
                else (Todo() if kind == "deadline" else Event())
            )
        except Exception:
            problem("Invalid preserved event data.")
        if (ev.name == "VTODO") != (kind == "deadline"):
            converted = Todo() if kind == "deadline" else Event()
            for field, val in ev.items():
                if field not in ("DTSTART", "DTEND", "DUE", "DURATION"):
                    converted[field] = val
            ev = converted
        ev.pop("DUE" if kind != "deadline" else "DTEND", None)
        if kind == "deadline":
            ev.pop("DTSTART", None)
        for field, val in [
            ("UID", uid),
            ("SUMMARY", title_ev),
            ("DESCRIPTION", str(item.get("description", ""))[:20000]),
            ("LOCATION", str(item.get("location", ""))[:2000]),
        ]:
            ev.pop(field, None)
            ev.add(field, val)
        for field, val in (
            [("DUE", end)]
            if kind == "deadline"
            else [("DTSTART", start), ("DTEND", end)]
        ):
            previous = ev.get(field)
            # Preserve named zones and custom VTIMEZONE definitions when a date is unchanged.
            unchanged = previous is not None and previous.dt.isoformat() == (
                val.isoformat() if val is not None else None
            )
            if not unchanged:
                ev.pop(field, None)
                if val is not None:
                    encoded = val
                    if (
                        isinstance(val, datetime)
                        and val.tzinfo
                        and not getattr(val.tzinfo, "key", None)
                        and (not zone or zone.startswith("UTC"))
                    ):
                        encoded = val.astimezone(timezone.utc)
                    ev.add(field, encoded)
        ev.pop("DURATION", None)
        if "recurrence" in item:
            rule = str(item["recurrence"]).strip()
            if len(rule) > 1000:
                problem("Repeat settings are too long.")
            previous = ev.get("RRULE").to_ical().decode() if ev.get("RRULE") else ""
            if rule != previous:
                ev.pop("RRULE", None)
                if rule:
                    try:
                        parsed = vRecur.from_ical(rule)
                        if len(parsed.get("FREQ", [])) != 1 or parsed["FREQ"][
                            0
                        ] not in ("DAILY", "WEEKLY", "MONTHLY", "YEARLY"):
                            raise ValueError()
                        if any(
                            int(parsed.get(k, [1])[0]) < 1
                            for k in ("INTERVAL", "COUNT")
                        ):
                            raise ValueError()
                        if parsed.get("COUNT") and parsed.get("UNTIL"):
                            raise ValueError()
                        ev.add("rrule", parsed)
                        from dateutil.rrule import rrulestr

                        check = (
                            parsed.to_ical().decode()
                            if hasattr(parsed, "to_ical")
                            else ev["RRULE"].to_ical().decode()
                        )
                        anchor = start or end
                        if not isinstance(anchor, datetime):
                            anchor = datetime.combine(anchor, datetime.min.time())
                        # Validate rule syntax independently of legacy UNTIL timezone conventions.
                        check = re.sub(r";?UNTIL=[^;]+", "", check)
                        rrulestr(check, dtstart=anchor)
                    except Exception:
                        problem("Invalid repeat settings.")
                if not rule:
                    ev.pop("EXDATE", None)
                    ev.pop("RDATE", None)

        # Calendar subscriptions must never execute alarms supplied by a contributor.
        ev.subcomponents = []
        if not ev.get("DTSTAMP"):
            ev.add("dtstamp", datetime.now(timezone.utc))
        rid = str(ev.get("RECURRENCE-ID", ""))
        result = {
            "uid": uid,
            "title": title_ev,
            "type": kind,
            "timezone": zone
            or str((ev.get("DUE") or ev.get("DTSTART")).params.get("TZID", "")),
            "start": start.isoformat() if start else "",
            "end": end.isoformat() if end else "",
            "description": str(ev.get("DESCRIPTION", "")),
            "location": str(ev.get("LOCATION", "")),
            "raw": ev.to_ical().decode(),
            "recurrence_id": rid,
            "recurrence": ev.get("RRULE").to_ical().decode() if ev.get("RRULE") else "",
        }
        key = event_key(result)
        if key in keys:
            problem(
                "Duplicate event UID and recurrence ID. Resolve this conflict before submitting."
            )
        keys.add(key)
        out.append(result)
    zones = value.get("timezones", [])
    if not isinstance(zones, list) or len(zones) > 100:
        problem("Invalid timezone definitions.")
    try:
        for raw in zones:
            if Timezone.from_ical(raw).name != "VTIMEZONE":
                raise ValueError()
    except Exception:
        problem("Invalid timezone definition.")
    sources = value.get("sources", [])
    if not isinstance(sources, list) or len(sources) > 50:
        problem("Invalid calendar sources.")
    if any(
        not isinstance(s, dict)
        or not isinstance(s.get("id"), str)
        or not isinstance(s.get("revision"), int)
        or s["revision"] < 1
        for s in sources
    ):
        problem("Invalid source revision.")
    return {
        "title": title,
        "description": str(value.get("description", ""))[:10000],
        "hashtags": tags,
        "events": out,
        "timezones": zones,
        "sources": [
            {"id": str(s["id"]), "revision": int(s["revision"])}
            for s in sources
            if isinstance(s, dict) and "id" in s and "revision" in s
        ],
    }


def export_ics(content, revision):
    cal = Calendar()
    cal.add("prodid", "-//TimeGrid//Community Calendars//EN")
    cal.add("version", "2.0")
    cal.add("calscale", "GREGORIAN")
    cal.add("x-wr-calname", content["title"])
    cal.add("x-wr-caldesc", content["description"])
    for raw in content["timezones"]:
        cal.add_component(Timezone.from_ical(raw))
    for item in content["events"]:
        ev = component(item["raw"], content["timezones"])
        ev.pop("SEQUENCE", None)
        ev.add("sequence", item.get("_sequence", revision))
        cal.add_component(ev)
    return cal.to_ical()


def changes(before, after):
    old = {event_key(e): e for e in before.get("events", [])}
    new = {event_key(e): e for e in after["events"]}
    common = old.keys() & new.keys()
    return {
        "added": [new[k] for k in new if k not in old],
        "deleted": [old[k] for k in old if k not in new],
        "unchanged": [
            new[k] for k in new if k in common and semantic(old[k]) == semantic(new[k])
        ],
        "edited": [
            {
                "before": old[k],
                "after": new[k],
                "fields": [
                    f
                    for f in semantic(new[k])
                    if semantic(old[k]).get(f) != semantic(new[k]).get(f)
                ],
            }
            for k in new
            if k in common and semantic(old[k]) != semantic(new[k])
        ],
        "metadata": {
            k: {
                "before": before.get(k, [] if k in ("hashtags", "sources") else ""),
                "after": after[k],
            }
            for k in ("title", "description", "hashtags", "sources")
            if before.get(k, [] if k in ("hashtags", "sources") else "") != after[k]
        },
    }


def create_app(test_config=None):
    app = Flask(__name__)
    if os.environ.get("TRUST_PROXY") == "1":
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)
    data = Path(os.environ.get("TIMEGRID_DATA_DIR", ROOT / "data"))
    data.mkdir(parents=True, exist_ok=True, mode=0o700)
    secret = os.environ.get("SECRET_KEY")
    if not secret:
        secretfile = data / "session-key"
        try:
            fd = os.open(secretfile, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(secrets.token_hex(32))
        except FileExistsError:
            pass
        secret = secretfile.read_text()
    app.config.update(
        SECRET_KEY=secret,
        DATABASE=str(data / "timegrid.sqlite3"),
        MAX_CONTENT_LENGTH=5 * 1024 * 1024,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE", "1") != "0",
        PERMANENT_SESSION_LIFETIME=86400,
    )
    if test_config:
        app.config.update(test_config)

    def db():
        if "db" not in g:
            g.db = sqlite3.connect(app.config["DATABASE"], timeout=20)
            g.db.row_factory = sqlite3.Row
            g.db.execute("PRAGMA foreign_keys=ON")
        return g.db

    @app.teardown_appcontext
    def close(error):
        if "db" in g:
            g.db.close()

    with app.app_context():
        db().executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY,username TEXT UNIQUE NOT NULL,password TEXT NOT NULL,role TEXT NOT NULL CHECK(role IN ('contributor','manager')));
        CREATE TABLE IF NOT EXISTS calendars(id TEXT PRIMARY KEY,slug TEXT UNIQUE NOT NULL,revision INTEGER NOT NULL,content TEXT NOT NULL,updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS proposals(id TEXT PRIMARY KEY,author TEXT NOT NULL REFERENCES users(id),target TEXT REFERENCES calendars(id),base_revision INTEGER NOT NULL,content TEXT NOT NULL,message TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','accepted','rejected')),created_at TEXT NOT NULL,reviewer TEXT REFERENCES users(id),reviewed_at TEXT,reason TEXT NOT NULL DEFAULT '');
        CREATE INDEX IF NOT EXISTS proposals_author_status ON proposals(author,status);
        CREATE INDEX IF NOT EXISTS proposals_status ON proposals(status);
        CREATE TABLE IF NOT EXISTS revisions(calendar_id TEXT NOT NULL REFERENCES calendars(id),revision INTEGER NOT NULL,content TEXT NOT NULL,proposal_id TEXT REFERENCES proposals(id),created_at TEXT NOT NULL,PRIMARY KEY(calendar_id,revision));
        CREATE TABLE IF NOT EXISTS folders(id TEXT PRIMARY KEY,owner TEXT NOT NULL REFERENCES users(id),name TEXT NOT NULL,sources TEXT NOT NULL,token TEXT UNIQUE NOT NULL,revision INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS attempts(key TEXT PRIMARY KEY,count INTEGER NOT NULL,expires INTEGER NOT NULL);
        """)
        db().commit()

    def user():
        return (
            db()
            .execute(
                "SELECT id,username,role FROM users WHERE id=?",
                (session.get("user_id"),),
            )
            .fetchone()
        )

    def require(manager=False):
        def deco(fn):
            @wraps(fn)
            def wrapped(*args, **kwargs):
                current = user()
                if not current:
                    problem("Sign in to contribute.", 401)
                if manager and current["role"] != "manager":
                    problem("Manager access required.", 403)
                return fn(*args, **kwargs)

            return wrapped

        return deco

    def limit(label, maximum):
        key = hashlib.sha256(
            (label + "|" + (request.remote_addr or "")).encode()
        ).hexdigest()
        t = int(time.time())
        db().execute("DELETE FROM attempts WHERE expires<?", (t,))
        db().execute(
            "INSERT INTO attempts VALUES(?,1,?) ON CONFLICT(key) DO UPDATE SET count=count+1",
            (key, t + 900),
        )
        db().commit()
        if (
            db().execute("SELECT count FROM attempts WHERE key=?", (key,)).fetchone()[0]
            > maximum
        ):
            problem("Too many attempts. Please try again in 15 minutes.", 429)

    @app.before_request
    def csrf():
        if request.method in ("POST", "PATCH", "DELETE", "PUT"):
            if not secrets.compare_digest(
                request.headers.get("X-CSRF-Token", ""),
                session.get("csrf") or secrets.token_hex(32),
            ):
                problem("Refresh the page before trying again.", 403)

    @app.after_request
    def headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
        )
        if request.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.errorhandler(400)
    @app.errorhandler(401)
    @app.errorhandler(403)
    @app.errorhandler(404)
    @app.errorhandler(409)
    @app.errorhandler(413)
    @app.errorhandler(429)
    def error(exc):
        return jsonify(error=exc.description), exc.code

    def body():
        value = request.get_json(silent=True)
        if not isinstance(value, dict):
            problem("Expected a JSON object.")
        return value

    @app.get("/")
    @app.get("/p/<slug>")
    @app.get("/calendars/<slug>")
    @app.get("/contribute")
    @app.get("/dashboard")
    @app.get("/my-calendars")
    @app.get("/manage")
    def index(slug=None):
        return render_template("index.html")

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.get("/api/session")
    def who():
        session.setdefault("csrf", secrets.token_hex(32))
        u = user()
        return {"user": dict(u) if u else None, "csrf": session["csrf"]}

    @app.post("/api/register")
    def register():
        limit("register", 10)
        b = body()
        name = str(b.get("username", "")).strip().lower()
        password = b.get("password", "")
        if not re.fullmatch("[a-z0-9_-]{3,40}", name):
            problem(
                "Username: 3–40 lowercase letters, numbers, underscores or hyphens."
            )
        if not isinstance(password, str) or not 12 <= len(password) <= 200:
            problem("Use a password of 12–200 characters.")
        uid = identifier()
        try:
            db().execute(
                "INSERT INTO users VALUES(?,?,?,?)",
                (uid, name, generate_password_hash(password), "contributor"),
            )
            db().commit()
        except sqlite3.IntegrityError:
            problem("This username is already taken.", 409)
        session.clear()
        session.update(user_id=uid, csrf=secrets.token_hex(32))
        session.permanent = True
        return who(), 201

    @app.post("/api/login")
    def login():
        limit("login", 30)
        b = body()
        u = (
            db()
            .execute(
                "SELECT * FROM users WHERE username=?",
                (str(b.get("username", "")).lower().strip(),),
            )
            .fetchone()
        )
        password = b.get("password", "")
        if not isinstance(password, str) or len(password) > 200:
            problem("Invalid username or password.", 401)
        valid = check_password_hash(
            u["password"] if u else generate_password_hash("dummy-password"), password
        )
        if not u or not valid:
            problem("Invalid username or password.", 401)
        session.clear()
        session.update(user_id=u["id"], csrf=secrets.token_hex(32))
        session.permanent = True
        return who()

    @app.post("/api/logout")
    def logout():
        session.clear()
        return who()

    def calendar(slug):
        row = (
            db()
            .execute("SELECT * FROM calendars WHERE slug=? OR id=?", (slug, slug))
            .fetchone()
        )
        if not row:
            problem("Calendar not found.", 404)
        return row

    def public(row):
        return {**dict(row), "content": json.loads(row["content"])}

    @app.get("/api/calendars")
    def catalog():
        q = request.args.get("q", "").lower()
        tag = request.args.get("tag", "").lower().lstrip("#")
        results = []
        for row in db().execute("SELECT * FROM calendars ORDER BY updated_at DESC"):
            c = json.loads(row["content"])
            rank = search_score(c, request.args)
            if rank is None:
                continue
            if tag and tag not in c["hashtags"]:
                continue
            results.append(
                {
                    "id": row["id"],
                    "slug": row["slug"],
                    "revision": row["revision"],
                    "updated_at": row["updated_at"],
                    "title": c["title"],
                    "description": c["description"],
                    "hashtags": c["hashtags"],
                    "event_count": len(c["events"]),
                    "sample_entries": [
                        {"title": e["title"], "type": entry_type(e)}
                        for e in c["events"][:3]
                    ],
                    "score": rank,
                }
            )
        results.sort(key=lambda c: c["score"], reverse=True)
        return {"calendars": results}

    @app.get("/api/calendars/<slug>")
    def get_calendar(slug):
        return public(calendar(slug))

    @app.get("/api/calendars/<slug>/preview")
    def public_preview(slug):
        c = get_calendar(slug)
        try:
            return preview(
                c["content"], request.args.get("start", ""), request.args.get("end", "")
            )
        except (ValueError, TypeError):
            problem("Choose a valid calendar date range of up to six weeks.")

    @app.get("/api/calendars/<slug>/revisions")
    def history(slug):
        row = calendar(slug)
        return {
            "revisions": [
                {"revision": r["revision"], "created_at": r["created_at"]}
                for r in db().execute(
                    "SELECT revision,created_at FROM revisions WHERE calendar_id=? ORDER BY revision DESC",
                    (row["id"],),
                )
            ]
        }

    def folder(fid):
        row = (
            db()
            .execute(
                "SELECT * FROM folders WHERE id=? AND owner=?", (fid, user()["id"])
            )
            .fetchone()
        )
        if not row:
            problem("Folder not found.", 404)
        return row

    def folder_content(row):
        result = {
            "title": row["name"],
            "description": "Your selected TimeGrid calendars",
            "hashtags": [],
            "sources": [],
            "timezones": [],
            "events": [],
        }
        sequence = row["revision"]
        for cid in json.loads(row["sources"]):
            source = calendar(cid)
            content = json.loads(source["content"])
            sequence += source["revision"]
            result["sources"].append({"id": cid, "revision": source["revision"]})
            for zone in content["timezones"]:
                if zone not in result["timezones"]:
                    result["timezones"].append(zone)
            for original in content["events"]:
                e = dict(original)
                e["_sequence"] = source["revision"]
                e["uid"] = (
                    hashlib.sha256(
                        (row["id"] + "|" + cid + "|" + e["uid"]).encode()
                    ).hexdigest()
                    + "@timegrid"
                )
                ev = component(e["raw"], content["timezones"])
                ev.pop("UID", None)
                ev.add("UID", e["uid"])
                e["raw"] = ev.to_ical().decode()
                result["events"].append(e)
        return result, sequence

    @app.get("/api/folders")
    @require()
    def folders():
        return {
            "folders": [
                {**dict(r), "sources": json.loads(r["sources"])}
                for r in db().execute(
                    "SELECT * FROM folders WHERE owner=? ORDER BY name", (user()["id"],)
                )
            ]
        }

    @app.post("/api/folders")
    @require()
    def create_folder():
        name = str(body().get("name", "")).strip()[:100]
        if not name:
            problem("Name your calendar folder.")
        if (
            db()
            .execute("SELECT count(*) FROM folders WHERE owner=?", (user()["id"],))
            .fetchone()[0]
            >= 30
        ):
            problem("You can manage up to 30 folders.")
        fid = identifier()
        db().execute(
            "INSERT INTO folders VALUES(?,?,?,?,?,?)",
            (fid, user()["id"], name, "[]", secrets.token_urlsafe(32), 1),
        )
        db().commit()
        return {"id": fid}, 201

    @app.post("/api/folders/<fid>")
    @require()
    def update_folder(fid):
        row = folder(fid)
        b = body()
        name = str(b.get("name", row["name"])).strip()[:100]
        ids = b.get("sources", json.loads(row["sources"]))
        if (
            not name
            or not isinstance(ids, list)
            or len(ids) > 50
            or any(not isinstance(x, str) for x in ids)
        ):
            problem("Choose a name and up to 50 source calendars.")
        ids = list(dict.fromkeys(calendar(x)["id"] for x in ids))
        db().execute(
            "UPDATE folders SET name=?,sources=?,revision=revision+1 WHERE id=?",
            (name, dump(ids), fid),
        )
        db().commit()
        return {"ok": True}

    @app.delete("/api/folders/<fid>")
    @require()
    def delete_folder(fid):
        folder(fid)
        db().execute("DELETE FROM folders WHERE id=?", (fid,))
        db().commit()
        return {"ok": True}

    @app.post("/api/folders/<fid>/rotate")
    @require()
    def rotate_folder(fid):
        folder(fid)
        db().execute(
            "UPDATE folders SET token=? WHERE id=?", (secrets.token_urlsafe(32), fid)
        )
        db().commit()
        return {"ok": True}

    @app.get("/api/folders/<fid>/preview")
    @require()
    def folder_preview(fid):
        content, _ = folder_content(folder(fid))
        try:
            return preview(
                content, request.args.get("start", ""), request.args.get("end", "")
            )
        except (ValueError, TypeError):
            problem("Choose a valid date range of up to six weeks.")

    @app.get("/personal/<token>.ics")
    def personal_feed(token):
        row = db().execute("SELECT * FROM folders WHERE token=?", (token,)).fetchone()
        if not row:
            problem("Subscription not found.", 404)
        content, sequence = folder_content(row)
        response = app.response_class(
            export_ics(content, sequence), mimetype="text/calendar"
        )
        response.headers["Cache-Control"] = "private, no-cache"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.set_etag(hashlib.sha256(response.data).hexdigest())
        return response.make_conditional(request)

    @app.get("/bundle/<slug>.ics")
    @app.get("/feeds/<slug>.ics")
    def feed(slug):
        row = calendar(slug)
        result = app.response_class(
            export_ics(json.loads(row["content"]), row["revision"]),
            mimetype="text/calendar",
        )
        result.headers["Content-Disposition"] = (
            'inline; filename="' + row["slug"] + '.ics"'
        )
        result.headers["Cache-Control"] = "public, max-age=60"
        result.set_etag(hashlib.sha256(result.data).hexdigest())
        return result.make_conditional(request)

    @app.post("/api/preview")
    @require()
    def calendar_preview():
        b = body()
        content = b.get("content")
        if not isinstance(content, dict):
            problem("Calendar content is required.")
        content = dict(content)
        content["title"] = content.get("title") or "Calendar preview"
        cleaned = clean_content(content)
        try:
            return preview(cleaned, b.get("start", ""), b.get("end", ""))
        except (ValueError, TypeError):
            problem("Choose a valid calendar date range of up to six weeks.")

    @app.post("/api/import")
    @require()
    def import_calendar():
        limit("import", 60)
        if "file" not in request.files:
            problem("Choose an ICS file.")
        return clean_content(decode_ics(request.files["file"].read()))

    @app.post("/api/import-url")
    @require()
    def import_subscription():
        limit("import-url", 20)
        try:
            raw = fetch_calendar(body().get("url"))
        except ValueError as exc:
            problem(str(exc))
        # Private feed tokens are never saved in public calendar metadata.
        return clean_content(decode_ics(raw))

    @app.post("/api/combine")
    @require()
    def combine():
        ids = body().get("ids", [])
        if (
            not isinstance(ids, list)
            or any(not isinstance(i, str) for i in ids)
            or not 2 <= len(set(ids)) <= 20
        ):
            problem("Choose 2–20 different calendars to combine.")
        result = {
            "title": "Combined calendar",
            "description": "",
            "hashtags": [],
            "events": [],
            "timezones": [],
            "sources": [],
        }
        seen = {}
        zones = {}
        for slug in dict.fromkeys(ids):
            row = calendar(slug)
            c = json.loads(row["content"])
            result["sources"].append({"id": row["id"], "revision": row["revision"]})
            result["hashtags"] += c["hashtags"]
            for e in c["events"]:
                key = event_key(e)
                if key in seen and seen[key] != e:
                    problem(
                        "These calendars contain conflicting versions of the same event. Resolve their source revisions first.",
                        409,
                    )
                if key not in seen:
                    seen[key] = e
                    result["events"].append(e)
            for tz in c["timezones"]:
                name = str(Timezone.from_ical(tz).get("TZID"))
                if name in zones and zones[name] != tz:
                    problem(
                        "These calendars contain conflicting timezone definitions.", 409
                    )
                zones[name] = tz
        result["timezones"] = list(zones.values())
        result["hashtags"] = sorted(set(result["hashtags"]))[:12]
        return clean_content(result)

    @app.post("/api/proposals")
    @require()
    def propose():
        limit("proposal", 30)
        b = body()
        c = clean_content(b.get("content"))
        target = b.get("target")
        base = b.get("base_revision", 0)
        if not isinstance(base, int):
            problem("Invalid base revision.")
        if target:
            row = calendar(target)
            target = row["id"]
            if row["revision"] != base:
                problem(
                    "This calendar changed. Reload its latest revision and reapply your edits.",
                    409,
                )
        elif base != 0:
            problem("New calendars must start at revision zero.")
        for source in c["sources"]:
            if (
                not db()
                .execute(
                    "SELECT 1 FROM revisions WHERE calendar_id=? AND revision=?",
                    (source["id"], source["revision"]),
                )
                .fetchone()
            ):
                problem("Unknown source revision.")
        message = str(b.get("message", "")).strip()
        if len(message) > 4000:
            problem("Use at most 4,000 characters.")
        pid = identifier()
        db().execute(
            "INSERT INTO proposals(id,author,target,base_revision,content,message,created_at) VALUES(?,?,?,?,?,?,?)",
            (pid, user()["id"], target, base, dump(c), message, now()),
        )
        db().commit()
        return {"id": pid, "status": "pending"}, 201

    def proposal_record(row):
        p = dict(row)
        p["content"] = json.loads(p["content"])
        old = {}
        if p["target"]:
            revision = (
                db()
                .execute(
                    "SELECT content FROM revisions WHERE calendar_id=? AND revision=?",
                    (p["target"], p["base_revision"]),
                )
                .fetchone()
            )
            if revision:
                old = json.loads(revision["content"])
        p["changes"] = changes(old, p["content"])
        p["before"] = old
        return p

    @app.get("/api/proposals")
    @require()
    def proposals():
        u = user()
        sql = "SELECT p.*,u.username FROM proposals p JOIN users u ON p.author=u.id"
        params = ()
        if u["role"] != "manager":
            sql += " WHERE p.author=?"
            params = (u["id"],)
        return {
            "proposals": [
                proposal_record(r)
                for r in db().execute(sql + " ORDER BY p.created_at DESC", params)
            ]
        }

    def resolve_proposal(p, b):
        current = calendar(p["target"])
        live = json.loads(current["content"])
        proposed = json.loads(p["content"])
        base_row = (
            db()
            .execute(
                "SELECT content FROM revisions WHERE calendar_id=? AND revision=?",
                (p["target"], p["base_revision"]),
            )
            .fetchone()
        )
        if not base_row:
            problem("Original revision is unavailable.", 409)
        if b.get("strategy") == "overwrite":
            result, conflicts = proposed, []
        elif b.get("strategy") == "merge":
            if not isinstance(b.get("resolutions", {}), dict):
                problem("Invalid conflict choices.")
            result, conflicts = merge_calendar(
                json.loads(base_row["content"]), live, proposed, b.get("resolutions")
            )
        else:
            problem("Choose merge or overwrite.", 409)
        if "hashtags" in b:
            result["hashtags"] = b["hashtags"]
        return current, result, conflicts

    @app.post("/api/proposals/<pid>/resolve")
    @require(manager=True)
    def resolve_preview(pid):
        p = db().execute("SELECT * FROM proposals WHERE id=?", (pid,)).fetchone()
        if not p:
            problem("Proposal not found.", 404)
        if p["status"] != "pending" or not p["target"]:
            problem("This proposal cannot be merged.", 409)
        current, result, conflicts = resolve_proposal(p, body())
        result = clean_content(result)
        return {
            "revision": current["revision"],
            "content": result,
            "conflicts": conflicts,
            "changes": changes(json.loads(current["content"]), result),
        }

    @app.post("/api/proposals/<pid>/review")
    @require(manager=True)
    def review(pid):
        b = body()
        decision = b.get("decision")
        reason = str(b.get("reason", "")).strip()[:4000]
        if decision not in ("accept", "reject"):
            problem("Choose accept or reject.")
        if decision == "reject" and not reason:
            problem("Explain why this proposal is rejected.")
        conn = db()
        conn.execute("BEGIN IMMEDIATE")
        try:
            p = conn.execute("SELECT * FROM proposals WHERE id=?", (pid,)).fetchone()
            if not p:
                problem("Proposal not found.", 404)
            if p["status"] != "pending":
                problem("This proposal has already been reviewed.", 409)
            target = p["target"]
            stamp = now()
            slug = None
            if decision == "accept":
                c = json.loads(p["content"])
                if "hashtags" in b:
                    c["hashtags"] = b["hashtags"]
                c = clean_content(c)
                payload = dump(c)
                if target:
                    old = conn.execute(
                        "SELECT * FROM calendars WHERE id=?", (target,)
                    ).fetchone()
                    if old["revision"] != p["base_revision"] or b.get("strategy"):
                        if b.get("expected_revision") != old["revision"]:
                            problem(
                                "The published calendar changed. Preview the merge again before publishing.",
                                409,
                            )
                        _, c, conflicts = resolve_proposal(p, b)
                        if any(not x["resolved"] for x in conflicts):
                            problem(
                                "Resolve every conflicting entry or field before publishing.",
                                409,
                            )
                        c = clean_content(c)
                        payload = dump(c)
                    revision = old["revision"] + 1
                    slug = old["slug"]
                    conn.execute(
                        "UPDATE calendars SET revision=?,content=?,updated_at=? WHERE id=?",
                        (revision, payload, stamp, target),
                    )
                else:
                    target = identifier()
                    revision = 1
                    slug = (
                        (
                            re.sub("[^a-z0-9]+", "-", c["title"].lower()).strip("-")[
                                :60
                            ]
                            or "calendar"
                        )
                        + "-"
                        + target[:8]
                    )
                    conn.execute(
                        "INSERT INTO calendars VALUES(?,?,?,?,?)",
                        (target, slug, revision, payload, stamp),
                    )
                conn.execute(
                    "INSERT INTO revisions VALUES(?,?,?,?,?)",
                    (target, revision, payload, pid, stamp),
                )
            conn.execute(
                "UPDATE proposals SET status=?,reviewer=?,reviewed_at=?,reason=? WHERE id=?",
                (
                    "accepted" if decision == "accept" else "rejected",
                    user()["id"],
                    stamp,
                    reason,
                    pid,
                ),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        return {
            "status": "accepted" if decision == "accept" else "rejected",
            "slug": slug,
        }

    @app.cli.command("create-manager")
    @click.argument("username")
    @click.password_option(confirmation_prompt=True)
    def manager(username, password):
        if (
            not re.fullmatch("[a-z0-9_-]{3,40}", username)
            or not 12 <= len(password) <= 200
        ):
            raise click.ClickException(
                "Use a valid username and a 12–200 character password."
            )
        try:
            db().execute(
                "INSERT INTO users VALUES(?,?,?,?)",
                (identifier(), username, generate_password_hash(password), "manager"),
            )
            db().commit()
        except sqlite3.IntegrityError:
            raise click.ClickException("Username already exists. No account changed.")
        click.echo("Manager created.")

    @app.cli.command("import-public")
    @click.argument("manifest_path", type=click.Path(exists=True))
    def import_public(manifest_path):
        """Import an audited manifest of public legacy calendars and local ICS files."""
        manifest = Path(manifest_path).resolve()
        records = json.loads(manifest.read_text())
        if not isinstance(records, list):
            raise click.ClickException("Manifest must be an array.")
        prepared = []
        for item in records:
            if (
                item.get("visibility") != "public"
                or item.get("listed") is not True
                or item.get("archived")
                or item.get("owner_detached")
            ):
                raise click.ClickException(
                    "Every record must be explicitly public, listed, active and attached."
                )
            slug = item.get("slug", "")
            if not re.fullmatch(r"[a-zA-Z0-9_-]{1,120}", slug):
                raise click.ClickException("Invalid legacy slug.")
            path = (manifest.parent / item["file"]).resolve()
            if not path.is_relative_to(manifest.parent) or not path.is_file():
                raise click.ClickException(
                    "ICS files must be inside the manifest directory."
                )
            if path.stat().st_size > 5 * 1024 * 1024:
                raise click.ClickException("ICS file exceeds 5 MB.")
            value = decode_ics(path.read_bytes())
            value.update(
                title=item["title"],
                description=item.get("description", ""),
                hashtags=item.get("hashtags", []),
            )
            prepared.append((slug, clean_content(value)))
        conn = db()
        conn.execute("BEGIN IMMEDIATE")
        try:
            for slug, value in prepared:
                if conn.execute(
                    "SELECT 1 FROM calendars WHERE slug=?", (slug,)
                ).fetchone():
                    raise click.ClickException(
                        "Slug already exists; no calendars imported: " + slug
                    )
                cid = identifier()
                stamp = now()
                payload = dump(value)
                conn.execute(
                    "INSERT INTO calendars VALUES(?,?,?,?,?)",
                    (cid, slug, 1, payload, stamp),
                )
                conn.execute(
                    "INSERT INTO revisions VALUES(?,?,?,?,?)",
                    (cid, 1, payload, None, stamp),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        click.echo(
            f"Imported {len(prepared)} public calendars. Legacy /bundle/<slug>.ics links are preserved."
        )

    @app.cli.command("backup")
    @click.argument("destination", type=click.Path())
    def backup(destination):
        if Path(destination).exists():
            raise click.ClickException("Destination already exists.")
        with sqlite3.connect(destination) as out:
            db().backup(out)
        os.chmod(destination, 0o600)
        click.echo("Verified SQLite backup written.")

    return app


if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=int(os.environ.get("PORT", "9200")))
