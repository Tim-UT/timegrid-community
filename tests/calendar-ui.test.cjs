const { test } = require("node:test");
const assert = require("node:assert/strict");
const UI = require("../static/calendar-ui.js");
test("recurrence is readable, including imported week-start syntax", () => {
  const text = UI.recurrenceText(
    "FREQ=WEEKLY;UNTIL=20261208T235959;WKST=MO",
    "2026-09-14T17:00:00-04:00",
  );
  assert.equal(text, "Every week on Monday until Dec 8, 2026");
  assert(!text.includes("FREQ"));
});
test("intervals, weekdays and counts read naturally", () => {
  assert.equal(
    UI.recurrenceText("FREQ=WEEKLY;INTERVAL=2;BYDAY=MO,WE;COUNT=10"),
    "Every 2 weeks on Monday, Wednesday, 10 occurrences",
  );
  assert.equal(UI.recurrenceText(""), "Does not repeat");
});
test("deadline and notice dates do not invent the missing endpoint", () => {
  assert.equal(UI.kind({ end: "2026-09-16" }), "deadline");
  assert.equal(
    UI.timing({ type: "deadline", end: "2026-09-16" }),
    "Due Sep 16, 2026",
  );
  assert.equal(
    UI.timing({ type: "notice", start: "2026-09-16" }),
    "Sep 16, 2026",
  );
});
