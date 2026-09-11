# TimeGrid Community

A public calendar repository with reviewed contributions. Anyone can browse calendars, download ICS snapshots, or subscribe to stable feed URLs without an account. Contributors sign in to upload calendars, combine published calendars, and propose event additions, edits, or deletions. Managers review event diffs and hashtags, then accept or reject proposals.

This is a separate reconstruction of [TimeGrid](https://github.com/Tim-UT/timegrid). It has no Mastodon service, OAuth dependency, or social feed. The previous production source is preserved on the original repository's `archive/production-2026-09-11` branch.

## Run locally

Python 3.11 or newer is required.

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
COOKIE_SECURE=0 .venv/bin/python app.py
```

Open http://127.0.0.1:9200. The catalog starts empty. Create a contributor account in the interface, or provision a manager locally:

```sh
.venv/bin/flask --app app:create_app create-manager your_username
```

The command securely prompts for a password. Existing accounts are never silently promoted. Public registration always creates contributors. Accounts use local password authentication; no external identity provider is required. Password recovery and email verification are not implemented; administrators should provision a replacement account if access is lost.

`TIMEGRID_DATA_DIR` defaults to `data/`. It holds a SQLite database and a private signing key. A stable `SECRET_KEY` environment variable can override that key. Real credentials, user data and session keys must never be committed. The app reads process environment variables; it does not automatically load `.env`. The systemd unit loads the production environment file.

## Workflow

- **Explore**: search titles, descriptions and hashtags; view published events; copy a subscription URL or download ICS.
- **Contribute**: start a calendar, import an ICS file, or choose calendars in Explore and combine them. Proposed edits start from a specific published revision.
- **My proposals**: contributors see only their own submissions and manager feedback.
- **Manage**: managers see the review queue and full event/metadata diffs. They can correct hashtags during approval or reject with a reason.

Approval is one SQLite transaction. Pending/rejected proposals do not affect feeds. An update whose base revision is stale cannot overwrite a newer publication. A rejected contributor can reload the current calendar and submit a new proposal. Drafts stay in the current page until submission; leaving the editor prompts before discarding unsent changes.

Combining calendars creates a snapshot with source revision references. Identical events are deduplicated; conflicting versions of the same UID/recurrence ID are rejected. Combined calendars do not automatically follow later source changes: publish another reviewed revision to refresh them.

The editor has a create/proposal mode switch, a month preview, and three entry types: events (start and end), deadlines (due date only), and notices (start only). Neither new-calendar submissions nor update proposals require a change explanation. Imports live beside the entry tools and can append an ICS file from the device or pull a published source from this site. A source must be explicitly loaded before submitting an update.

Repeat controls support daily, weekly, monthly and yearly schedules, intervals, selected weekdays, end dates and occurrence counts. Imported rules are shown in ordinary words and preserved unless the contributor changes them. Date/time controls retain calendar timezones; all-day end dates are exclusive. The month preview expands recurring entries, exclusions and occurrence overrides, including daylight-saving transitions. Preview expansion is bounded to six weeks and 2,000 displayed occurrences, with a visible warning for unsupported or truncated rules. It does not offer drag-and-drop editing.

Manager review includes a calendar preview and a full comparison: unchanged entries stay neutral, added entries are green, deleted entries red, and changed fields yellow with before/after values. Comparison ignores serialization timestamps, so unchanged entries do not appear as edits just because they were exported again.

Deadlines are exported as standard ICS VTODO components with DUE and no DTSTART. Calendar clients vary in their support for tasks; the website preview always displays them. Notices are VEVENT components with DTSTART and no DTEND. Uploaded alarms are removed. Files are limited to 5 MB and calendars to 5,000 entry definitions.

## Deployment on your server

The replacement uses a separate service and data directory. Do not switch the existing domain until the legacy archive is complete and the imported calendars have been reviewed.

```sh
sudo useradd --system --home /opt/timegrid-community --shell /usr/sbin/nologin timegrid-community
sudo install -d -o timegrid-community -g timegrid-community -m 750 /opt/timegrid-community
sudo install -d -o timegrid-community -g timegrid-community -m 700 /var/lib/timegrid-community
# Copy this repository into /opt/timegrid-community, excluding .git, .venv, data and .env.
sudo -u timegrid-community python3 -m venv /opt/timegrid-community/.venv
sudo -u timegrid-community /opt/timegrid-community/.venv/bin/pip install -r /opt/timegrid-community/requirements.txt
sudo install -m 600 .env.example /etc/timegrid-community.env
# Edit /etc/timegrid-community.env: set SECRET_KEY to a cryptographically random value.
sudo cp deploy/timegrid-community.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now timegrid-community
curl --fail http://127.0.0.1:9210/health
```

Create the manager with `TIMEGRID_DATA_DIR=/var/lib/timegrid-community` under the `timegrid-community` operating-system user. A generated key in that directory is private and stable if `SECRET_KEY` is unset. Use `deploy/Caddyfile.example` after validating the backup and migration. `TRUST_PROXY=1` is appropriate only behind one trusted reverse proxy with the application bound to loopback. The supplied service does this; preserve that binding. Caddy must replace untrusted forwarded headers. `COOKIE_SECURE=1` is required for the HTTPS deployment.

The existing Mastodon containers and legacy calendar service are not needed by the replacement. Retain them until the new deployment is verified and rollback is no longer required.

## Migration and old subscriptions

Export the **current** legacy public calendars to ICS and prepare a JSON manifest beside the exported files:

```json
[
  {
    "slug": "existing-calendar-slug",
    "title": "Academic dates",
    "description": "Published academic dates",
    "hashtags": ["university"],
    "visibility": "public",
    "listed": true,
    "archived": false,
    "owner_detached": false,
    "file": "academic-dates.ics"
  }
]
```

```sh
.venv/bin/flask --app app:create_app import-public /private/export/manifest.json
```

The importer checks explicit public visibility, active/listed status, contained file paths, calendar validity, and slug collisions before committing. Any failure rolls back the entire import. It never imports user profiles, credentials, private calendars, or invited calendars. Old public `/bundle/<slug>.ics` subscriptions and `/p/<slug>` links remain available when the original domain is switched. Private/export-token/personal feed routes are deliberately not converted into public feeds.

At reconstruction time, the live Supabase API and SQL connection timed out. Current production calendars have **not** been migrated, and this project has **not** replaced the running site. The legacy local JSON snapshot is older and is not assumed to be current.

## Backup and restore

```sh
.venv/bin/flask --app app:create_app backup /private/backups/timegrid.sqlite3
```

The backup uses SQLite's online backup API. The destination must not already exist. For restore, stop the replacement service, move aside its database plus `-wal`/`-shm` files, copy the backup to `TIMEGRID_DATA_DIR/timegrid.sqlite3`, set ownership to the service user, and restart. Keep the signing key or `SECRET_KEY` with your private configuration backup. A changed signing key signs everyone out without changing calendar data.

## Validation

```sh
.venv/bin/python -m pytest -q
node --check static/app.js
node --test tests/calendar-ui.test.cjs
```

Tests exercise anonymous subscriptions and ETags, publication, authorization, CSRF enforcement, proposal privacy, rejection, stale approvals, recurring imports, named timezones, combination provenance, deletion, invalid events, migration privacy, legacy URLs and database restore integrity.

The UI uses plain JavaScript and CSS with no frontend build step. The backend is Flask, SQLite and `icalendar`, served through Gunicorn in production. Dependencies are pinned in `requirements.txt`.

## License

AGPL-3.0; see LICENSE. Third-party dependencies retain their own licenses.
