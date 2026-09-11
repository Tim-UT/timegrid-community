/* Shared user-facing calendar language and accessible month previews. */
(function (root) {
  const html = (x) =>
    String(x ?? "").replace(
      /[&<>"']/g,
      (c) =>
        ({
          "&": "&amp;",
          "<": "&lt;",
          ">": "&gt;",
          '"': "&quot;",
          "'": "&#39;",
        })[c],
    );
  const kind = (e) =>
    e.type || (e.start && e.end ? "event" : e.end ? "deadline" : "notice");
  const key = (e) => e.uid + "|" + (e.recurrence_id || "");
  const dayNames = {
    MO: "Monday",
    TU: "Tuesday",
    WE: "Wednesday",
    TH: "Thursday",
    FR: "Friday",
    SA: "Saturday",
    SU: "Sunday",
  };
  function dateText(value) {
    if (!value) return "Not set";
    const raw = value.slice(0, 10);
    const d = new Date(raw + "T12:00:00");
    if (Number.isNaN(+d)) return "Not set";
    let text = d.toLocaleDateString("en", {
      month: "short",
      day: "numeric",
      year: "numeric",
    });
    if (value.includes("T")) {
      const t = value.slice(11, 16).split(":");
      const h = Number(t[0]);
      text += `, ${h % 12 || 12}:${t[1]} ${h >= 12 ? "PM" : "AM"}`;
    }
    return text;
  }
  function recurrenceText(rule, start = "") {
    if (!rule) return "Does not repeat";
    const r = Object.fromEntries(rule.split(";").map((x) => x.split("=")));
    const n = Number(r.INTERVAL) || 1;
    const units = {
      DAILY: "day",
      WEEKLY: "week",
      MONTHLY: "month",
      YEARLY: "year",
      HOURLY: "hour",
      MINUTELY: "minute",
      SECONDLY: "second",
    };
    let text = units[r.FREQ]
      ? n === 1
        ? `Every ${units[r.FREQ]}`
        : `Every ${n} ${units[r.FREQ]}s`
      : "Custom repeating schedule";
    if (r.BYDAY) {
      const days = r.BYDAY.split(",")
        .map((d) => {
          const m = d.match(/^(-?\d+)?([A-Z]{2})$/);
          if (!m) return "";
          const ord = {
            1: "first",
            2: "second",
            3: "third",
            4: "fourth",
            5: "fifth",
            "-1": "last",
            "-2": "second-last",
          };
          return (
            (m[1] ? (ord[m[1]] || m[1]) + " " : "") + (dayNames[m[2]] || "day")
          );
        })
        .filter(Boolean);
      if (days.length) text += " on " + days.join(", ");
    } else if (r.FREQ === "WEEKLY" && start) {
      const d = new Date(start.slice(0, 10) + "T12:00:00");
      if (!Number.isNaN(+d))
        text += " on " + d.toLocaleDateString("en", { weekday: "long" });
    }
    if (r.BYSETPOS)
      text +=
        " (" +
        r.BYSETPOS.split(",")
          .map((n) =>
            n === "-1"
              ? "last matching day"
              : n === "1"
                ? "first matching day"
                : "matching day " + n,
          )
          .join(", ") +
        ")";
    if (r.BYYEARDAY) text += " on day " + r.BYYEARDAY + " of the year";
    if (r.BYWEEKNO) text += " in week " + r.BYWEEKNO + " of the year";
    if (r.BYMONTHDAY)
      text +=
        " on day " +
        r.BYMONTHDAY.split(",")
          .map((d) => (d === "-1" ? "the last day" : d))
          .join(", ");
    if (r.BYMONTH)
      text +=
        " in " +
        r.BYMONTH.split(",")
          .map((m) =>
            new Date(2000, Number(m) - 1, 1).toLocaleDateString("en", {
              month: "long",
            }),
          )
          .join(", ");
    if (r.COUNT)
      text += `, ${r.COUNT} ${r.COUNT === "1" ? "occurrence" : "occurrences"}`;
    if (r.UNTIL) {
      const v = r.UNTIL;
      text +=
        " until " +
        dateText(`${v.slice(0, 4)}-${v.slice(4, 6)}-${v.slice(6, 8)}`);
    }
    return text;
  }
  function timing(e) {
    const t = kind(e);
    return t === "deadline"
      ? "Due " + dateText(e.end)
      : t === "notice"
        ? dateText(e.start)
        : dateText(e.start) + " – " + dateText(e.end);
  }
  function monthPreview(node, { load, initial, onSelect, review = false }) {
    let current = new Date(
      (initial || new Date().toISOString().slice(0, 10)).slice(0, 7) +
        "-01T12:00:00",
    );
    if (Number.isNaN(+current)) current = new Date();
    current.setDate(1);
    let serial = 0,
      selected = "",
      lastRows = [];
    const iso = (d) =>
      `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
    node.innerHTML = `<div class="calendar-heading"><h2>Calendar preview</h2><div class="row"><button type="button" class="quiet cal-prev" aria-label="Previous month">‹</button><strong class="cal-month"></strong><button type="button" class="quiet cal-next" aria-label="Next month">›</button><button type="button" class="quiet cal-today">Today</button></div></div>${review ? '<div class="change-legend"><span>Unchanged</span><span class="change-added">+ Added</span><span class="change-deleted">− Deleted</span><span class="change-edited">~ Changed</span></div>' : ""}<p class="cal-status hint" role="status"></p><div class="calendar-scroll"><div class="month-grid" role="grid" aria-label="Calendar month"></div></div><div class="cal-selection"></div>`;
    const q = (s) => node.querySelector(s);
    const detail = (e) => {
      q(".cal-selection").innerHTML =
        `<div class="event change-${html(e.change || "unchanged")}"><span class="pill">${html(kind(e))}</span><h3>${html(e.title)}</h3><p>${html(timing(e))}</p>${e.timezone ? `<p class="hint">${html(e.timezone)}</p>` : ""}${e.location ? `<p>${html(e.location)}</p>` : ""}<p>${html(recurrenceText(e.recurrence, e.start || e.end))}</p>${e.description ? `<p>${html(e.description)}</p>` : ""}${onSelect ? '<button type="button" class="quiet edit-preview-entry">Edit entry</button>' : ""}</div>`;
      if (onSelect) q(".edit-preview-entry").onclick = () => onSelect(e);
    };
    async function refresh() {
      const request = ++serial;
      q(".cal-month").textContent = current.toLocaleDateString("en", {
        month: "long",
        year: "numeric",
      });
      const first = new Date(current);
      first.setDate(first.getDate() - ((first.getDay() + 6) % 7));
      const until = new Date(first);
      until.setDate(until.getDate() + 42);
      q(".cal-status").textContent = "Updating preview…";
      try {
        const result = await load(iso(first), iso(until));
        if (request !== serial) return;
        lastRows = result.events;
        q(".cal-status").textContent =
          result.warnings?.join(" ") ||
          (lastRows.length
            ? `${lastRows.length} occurrences in view. Select an entry for details.`
            : "No entries in this month.");
        let grid = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
          .map((d) => `<div class="weekday" role="columnheader">${d}</div>`)
          .join("");
        for (let i = 0; i < 42; i++) {
          const date = new Date(first);
          date.setDate(date.getDate() + i);
          const day = iso(date);
          const entries = lastRows
            .map((e, index) => ({ e, index }))
            .filter(({ e }) => {
              const a = (e.start || e.end).slice(0, 10),
                b = (e.end || e.start).slice(0, 10);
              if (kind(e) !== "event") return day === a;
              return (
                day >= a &&
                (day < b ||
                  (day === b &&
                    e.end.includes("T") &&
                    e.end.slice(11, 19) !== "00:00:00" &&
                    e.end.slice(11, 16) !== "00:00"))
              );
            });
          grid += `<div class="month-day ${date.getMonth() !== current.getMonth() ? "outside" : ""}" role="gridcell" aria-label="${html(dateText(day))}"><span class="day-number">${date.getDate()}</span>${entries.map(({ e, index }) => `<button type="button" class="calendar-entry change-${html(e.change || "unchanged")}" data-entry="${index}" title="${html(timing(e) + " · " + e.title)}"><span class="entry-symbol">${kind(e) === "deadline" ? "◆" : kind(e) === "notice" ? "●" : "▰"}</span>${e.change && e.change !== "unchanged" ? `<span class="sr-only">${html(e.change)}: </span>` : ""}<span>${html(e.title)}</span></button>`).join("")}</div>`;
        }
        q(".month-grid").innerHTML = grid;
        q(".cal-selection").innerHTML = "";
        node
          .querySelectorAll("[data-entry]")
          .forEach(
            (b) =>
              (b.onclick = () => detail(lastRows[Number(b.dataset.entry)])),
          );
      } catch (e) {
        if (request !== serial) return;
        q(".cal-status").textContent = e.message;
        q(".month-grid").innerHTML = "";
        q(".cal-selection").innerHTML = "";
      }
    }
    q(".cal-prev").onclick = () => {
      current.setMonth(current.getMonth() - 1);
      refresh();
    };
    q(".cal-next").onclick = () => {
      current.setMonth(current.getMonth() + 1);
      refresh();
    };
    q(".cal-today").onclick = () => {
      current = new Date();
      current.setDate(1);
      refresh();
    };
    refresh();
    return {
      refresh,
      focus(value) {
        if (value) {
          current = new Date(value.slice(0, 7) + "-01T12:00:00");
          refresh();
        }
      },
    };
  }
  const api = {
    html,
    kind,
    key,
    dateText,
    recurrenceText,
    timing,
    monthPreview,
  };
  if (typeof module !== "undefined") module.exports = api;
  else root.CalendarUI = api;
})(typeof window !== "undefined" ? window : globalThis);
