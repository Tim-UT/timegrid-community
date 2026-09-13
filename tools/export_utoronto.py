#!/usr/bin/env python3
"""Export public TTB sections; never guess unknown room numbers or alternating dates."""

import argparse, csv, hashlib, html, json, re, time
from collections import Counter
from datetime import date, datetime, time as clock, timedelta, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo
from icalendar import Calendar, Event, Timezone

API = "https://api.easi.utoronto.ca/ttb/"
DATES_URL = "https://engineering.calendar.utoronto.ca/sessional-dates"
ZONE = ZoneInfo("America/Toronto")
TERMS = {"20269": ("2026-09-08", "2026-12-08"), "20271": ("2027-01-11", "2027-04-13")}
EXCLUDED = (
    {"2026-10-12", "2027-03-26"}
    | {(date(2026, 10, 26) + timedelta(days=i)).isoformat() for i in range(5)}
    | {(date(2027, 2, 15) + timedelta(days=i)).isoformat() for i in range(5)}
)


def get_json(endpoint, body=None):
    for attempt in range(4):
        try:
            req = Request(
                API + endpoint,
                data=json.dumps(body).encode() if body is not None else None,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "User-Agent": "TimeGrid public timetable exporter",
                },
            )
            with urlopen(req, timeout=60) as response:
                return json.load(response)
        except Exception:
            if attempt == 3:
                raise
            time.sleep(2**attempt)


def fetch(out, division, sessions):
    raw = out / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    ref = get_json("reference-data")
    (raw / "reference.json").write_text(json.dumps(ref, indent=2))
    query = {
        "divisions": [division],
        "sessions": sessions,
        "page": 1,
        "pageSize": 20,
        "direction": "asc",
        "courseCodeAndTitleProps": {
            "courseCode": "",
            "courseTitle": "",
            "courseSectionCode": "",
            "searchCourseDescription": True,
        },
        "departmentProps": [],
        "campuses": [],
        "requirementProps": [],
        "instructor": "",
        "courseLevels": [],
        "deliveryModes": [],
        "dayPreferences": [],
        "timePreferences": [],
        "creditWeights": [],
        "availableSpace": False,
        "waitListable": False,
    }
    courses = []
    page = 1
    total = None
    while True:
        query["page"] = page
        data = get_json("getPageableCourses", query)
        p = data["payload"]["pageableCourse"]
        if total is None:
            total = p["total"]
        if total != p["total"]:
            raise RuntimeError(
                "Course count changed during fetch; rerun to obtain a consistent snapshot."
            )
        (raw / f"page-{page:03}.json").write_text(json.dumps(data, ensure_ascii=False))
        batch = p["courses"]
        courses.extend(batch)
        print(f"Page {page}: {len(courses)}/{total}", flush=True)
        if len(courses) >= total:
            break
        if not batch:
            raise RuntimeError("Unexpected empty results page")
        page += 1
        time.sleep(0.4)
    if len(courses) != total or len({c["id"] for c in courses}) != total:
        raise RuntimeError("Missing or duplicated courses in pagination")
    (raw / "fetch-manifest.json").write_text(
        json.dumps(
            {
                "pages": page,
                "total": total,
                "query": query,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            },
            indent=2,
        )
    )
    return courses


def load_cached(out):
    raw = out / "raw"
    manifest = raw / "fetch-manifest.json"
    paths = (
        [
            raw / f"page-{i:03}.json"
            for i in range(1, json.loads(manifest.read_text())["pages"] + 1)
        ]
        if manifest.exists()
        else sorted(raw.glob("page-*.json"))
    )
    courses = []
    total = None
    for path in paths:
        p = json.loads(path.read_text())["payload"]["pageableCourse"]
        total = p["total"]
        courses.extend(p["courses"])
    if not courses or len(courses) != total or len({c["id"] for c in courses}) != total:
        raise RuntimeError("Incomplete or duplicate cached course pages")
    return courses


def plain(value):
    return html.unescape(re.sub("<[^>]+>", " ", str(value or ""))).strip()


def safe(value):
    return re.sub(r"[^\w .-]+", "_", value).strip()


def meeting_dates(m, section):
    term = m.get("sessionCode")
    warnings = []
    if term not in TERMS:
        return [], ["Unknown session date bounds: " + str(term)]
    if m.get("repetition") != "WEEKLY":
        return [], [
            "Exact dates unavailable for "
            + str(m.get("repetition"))
            + " / "
            + str(m.get("repetitionTime"))
        ]
    start, end = m.get("start") or {}, m.get("end") or {}
    if (
        not 1 <= start.get("day", 0) <= 7
        or not 0 <= start.get("millisofday", -1) < 86400000
        or end.get("day") != start["day"]
        or not start["millisofday"] < end.get("millisofday", 0) <= 86400000
    ):
        return [], ["Missing or invalid day/time"]
    lower, upper = map(date.fromisoformat, TERMS[term])
    first = section.get("firstMeeting")
    if first:
        lower = max(lower, date.fromisoformat(first[:10]))
    d = lower + timedelta(days=(start["day"] - 1 - lower.weekday()) % 7)
    dates = []
    while d <= upper:
        if d.isoformat() not in EXCLUDED:
            dates.append(d)
        d += timedelta(days=7)
    return dates, warnings


def export_section(course, section, out, stamp, suffix=""):
    name = f"{course['code']} {course['sectionCode']} {section['name']}"
    folder = out / "ics" / safe(course["sectionCode"]) / safe(course["code"])
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (safe(name) + suffix + ".ics")
    metadata = {
        "course": {k: v for k, v in course.items() if k != "sections"},
        "section": section,
        "source": "https://ttb.utoronto.ca/",
        "sessional_dates": DATES_URL,
    }
    path.with_suffix(".json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2)
    )
    warnings = []
    cal = Calendar()
    cal.add("prodid", "-//TimeGrid//UofT Engineering Section Export//EN")
    cal.add("version", "2.0")
    cal.add("calscale", "GREGORIAN")
    cal.add("x-wr-calname", name)
    cal.add(
        "x-ttb-metadata",
        json.dumps(metadata, ensure_ascii=False, separators=(",", ":")),
    )
    cal.add_component(
        Timezone.from_tzinfo(
            ZONE, first_date=date(2026, 1, 1), last_date=date(2028, 1, 1)
        )
    )
    info = course.get("cmCourseInfo") or {}
    instructors = (
        ", ".join(
            " ".join(filter(None, [i.get("firstName"), i.get("lastName")]))
            for i in section.get("instructors", [])
        )
        or "TBA"
    )
    description = f"{course['name']}\n{name}\nFaculty: {course['faculty']['name']}\nDepartment: {course['department']['name']}\nCampus: {course['campus']}\nInstructors: {instructors}\nDelivery: {json.dumps(section.get('deliveryModes',[]))}\nEnrolment: {section.get('currentEnrolment')} / {section.get('maxEnrolment')}\nWaitlist: {section.get('waitlistInd')} ({section.get('currentWaitlist')})\n{plain(info.get('description'))}\nPrerequisites: {plain(info.get('prerequisitesText'))}\nCorequisites: {plain(info.get('corequisitesText'))}\nExclusions: {plain(info.get('exclusionsText'))}\nNotes: {plain(info.get('note'))} {plain(course.get('notes'))} {plain(section.get('notes'))}\nPublic source: https://ttb.utoronto.ca/\nDates: {DATES_URL}\nOptional makeup classes are not included without instructor confirmation."
    slots_text = []
    for m in section.get("meetingTimes", []):
        start = m.get("start") or {}
        end = m.get("end") or {}
        b = m.get("building") or {}

        def hhmm(ms):
            return (
                f"{ms//3600000:02}:{(ms%3600000)//60000:02}"
                if isinstance(ms, int)
                else "TBA"
            )

        weekday = (
            [
                "TBA",
                "Monday",
                "Tuesday",
                "Wednesday",
                "Thursday",
                "Friday",
                "Saturday",
                "Sunday",
            ][start.get("day", 0)]
            if start.get("day", 0) in range(8)
            else "TBA"
        )
        slots_text.append(
            f"{m.get('sessionCode')}: {weekday} {hhmm(start.get('millisofday'))}-{hhmm(end.get('millisofday'))}; {m.get('repetition')} / {m.get('repetitionTime')}; {b.get('buildingCode') or 'Location TBA'} {b.get('buildingRoomNumber') or '(room available on ACORN)'}; {b.get('buildingUrl') or ''}"
        )
    description += "\nPublished meeting slots:\n" + "\n".join(dict.fromkeys(slots_text))
    groups = {}
    for m in section.get("meetingTimes", []):
        key = json.dumps(
            {k: v for k, v in m.items() if k != "building"}, sort_keys=True
        )
        groups.setdefault(key, []).append(m)
    count = 0
    slots = 0
    for key, meetings in groups.items():
        m = meetings[0]
        dates, issues = meeting_dates(m, section)
        warnings.extend(issues)
        if not dates:
            continue
        locations = []
        maps = []
        for mt in meetings:
            b = mt.get("building") or {}
            room = (b.get("buildingRoomNumber") or "") + (
                b.get("buildingRoomSuffix") or ""
            )
            loc = " ".join(
                filter(None, [b.get("buildingCode"), room, b.get("buildingName")])
            )
            if not room:
                loc = (
                    (loc + " — Room information available on ACORN")
                    if loc
                    else "Location TBA"
                )
                warnings.append("Room number not publicly provided")
            if loc not in locations:
                locations.append(loc)
            if b.get("buildingUrl") and b["buildingUrl"] not in maps:
                maps.append(b["buildingUrl"])
        slots += 1
        for day in dates:
            ev = Event()
            uid = (
                hashlib.sha256(
                    (
                        course["id"]
                        + "|"
                        + section["name"]
                        + "|"
                        + key
                        + "|"
                        + day.isoformat()
                    ).encode()
                ).hexdigest()
                + "@ttb.timegrid"
            )
            ev.add("uid", uid)
            ev.add("dtstamp", stamp)
            ev.add("summary", name)
            for field, point in [("dtstart", m["start"]), ("dtend", m["end"])]:
                ev.add(
                    field,
                    datetime.combine(day, clock(), ZONE)
                    + timedelta(milliseconds=point["millisofday"]),
                )
            ev.add("location", " / ".join(locations))
            ev.add("description", description + "\nBuilding maps: " + " ".join(maps))
            ev.add("url", "https://ttb.utoronto.ca/")
            if section.get("cancelInd") == "Y" or course.get("cancelInd") == "Y":
                ev.add("status", "CANCELLED")
            cal.add_component(ev)
            count += 1
    if not section.get("meetingTimes"):
        warnings.append(
            "No scheduled meeting times (TBA, asynchronous, or independent study)"
        )
    if not count:
        warnings.append("Metadata-only ICS: no reliably dated events can be generated")
    cal.add(
        "x-wr-caldesc", description + "\nWarnings: " + "; ".join(sorted(set(warnings)))
    )
    path.write_bytes(cal.to_ical())
    return {
        "course": course["code"],
        "term": course["sectionCode"],
        "section": section["name"],
        "course_id": course["id"],
        "file": str(path.relative_to(out)),
        "events": count,
        "scheduled_slots": slots,
        "warnings": "; ".join(sorted(set(warnings))),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cached", action="store_true")
    args = parser.parse_args()
    out = args.output
    out.mkdir(parents=True, exist_ok=True)
    courses = (
        load_cached(out)
        if args.cached
        else fetch(out, "APSC", ["20269", "20271", "20269-20271"])
    )
    names = Counter(
        (c["code"], c["sectionCode"], s["name"]) for c in courses for s in c["sections"]
    )
    rows = []
    stamp = datetime.now(timezone.utc)
    for c in courses:
        for section in c["sections"]:
            suffix = (
                " " + c["id"]
                if names[(c["code"], c["sectionCode"], section["name"])] > 1
                else ""
            )
            rows.append(export_section(c, section, out, stamp, suffix))
    with (out / "index.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    summary = {
        "courses": len(courses),
        "sections": len(rows),
        "events": sum(r["events"] for r in rows),
        "metadata_only_sections": sum(r["events"] == 0 for r in rows),
        "sections_with_warnings": sum(bool(r["warnings"]) for r in rows),
        "generated_at": stamp.isoformat(),
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
