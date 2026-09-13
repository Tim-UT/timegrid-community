from tools.export_utoronto import meeting_dates


def meeting(day, session="20269", repetition="WEEKLY"):
    return {
        "sessionCode": session,
        "start": {"day": day, "millisofday": 32400000},
        "end": {"day": day, "millisofday": 36000000},
        "repetition": repetition,
        "repetitionTime": "ONCE_A_WEEK",
    }


def test_dates_exclude_holidays_and_bound_sessions():
    dates, warnings = meeting_dates(meeting(1), {})
    assert not warnings
    assert str(dates[0]) == "2026-09-14" and str(dates[-1]) == "2026-12-07"
    assert "2026-10-12" not in map(str, dates) and "2026-10-26" not in map(str, dates)
    dates, _ = meeting_dates(meeting(5, "20271"), {})
    assert "2027-03-26" not in map(str, dates) and "2027-02-19" not in map(str, dates)


def test_undated_alternating_slots_are_not_fabricated():
    dates, warnings = meeting_dates(meeting(2, repetition="BI_WEEKLY"), {})
    assert not dates and warnings
