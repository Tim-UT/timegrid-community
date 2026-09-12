"use strict";
const $ = (s) => document.querySelector(s),
  app = $("#app");
let auth = { user: null, csrf: "" },
  register = false,
  draft = null,
  target = null,
  base = 0,
  dirty = false;
const esc = (x) =>
  String(x ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const tags = (items) =>
  items.map((t) => `<span class="tag">#${esc(t)}</span>`).join("");
function notify(message) {
  $("#notice").textContent = message;
  $("#notice").className = "notice";
  setTimeout(() => {
    $("#notice").className = "";
    $("#notice").textContent = "";
  }, 6000);
}
async function api(path, options = {}) {
  const headers = { ...options.headers };
  if (options.method && options.method !== "GET")
    headers["X-CSRF-Token"] = auth.csrf;
  if (options.body && !(options.body instanceof FormData)) {
    headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(options.body);
  }
  const r = await fetch(path, { ...options, headers });
  const data = await r.json();
  if (!r.ok) throw Error(data.error || "Request failed.");
  return data;
}
function safe(fn) {
  return async (...args) => {
    try {
      await fn(...args);
    } catch (e) {
      notify(e.message);
    }
  };
}
function eventView(e) {
  return `<div class="event"><span class="pill">${esc(CalendarUI.kind(e))}</span><div class="event-date">${esc(CalendarUI.timing(e))}</div><h3>${esc(e.title)}</h3>${e.location ? `<p>${esc(e.location)}</p>` : ""}${e.description ? `<p>${esc(e.description)}</p>` : ""}${e.recurrence ? `<span class="pill">${esc(CalendarUI.recurrenceText(e.recurrence, e.start || e.end))}</span>` : ""}</div>`;
}
function accounts() {
  const u = auth.user;
  $("#account").innerHTML = u
    ? `<span>${esc(u.username)}</span><button id="logout" class="quiet">Sign out</button>`
    : '<button id="signin" class="quiet">Sign in</button>';
  $("#manage-link").hidden = u?.role !== "manager";
  if (u)
    $("#logout").onclick = safe(async () => {
      if (dirty && !confirm("Discard unsent edits and sign out?")) return;
      dirty = false;
      auth = await api("/api/logout", { method: "POST" });
      accounts();
      await route();
    });
  else $("#signin").onclick = () => $("#auth").showModal();
}
$("#close-auth").onclick = () => $("#auth").close();
$("#toggle-auth").onclick = () => {
  register = !register;
  $("#auth-title").textContent = register ? "Join TimeGrid" : "Welcome back";
  $("#auth-form button[type=submit]").textContent = register
    ? "Create account"
    : "Sign in";
  $("#toggle-auth").textContent = register
    ? "Already have an account?"
    : "Create an account";
  $("#auth-form [name=password]").autocomplete = register
    ? "new-password"
    : "current-password";
};
$("#auth-form").onsubmit = async (e) => {
  e.preventDefault();
  const form = e.currentTarget;
  try {
    auth = await api(register ? "/api/register" : "/api/login", {
      method: "POST",
      body: Object.fromEntries(new FormData(form)),
    });
    accounts();
    $("#auth").close();
    form.reset();
    await route();
  } catch (err) {
    $("#auth-error").textContent = err.message;
  }
};
function gate(manager = false) {
  if (!auth.user) {
    app.innerHTML =
      '<div class="empty"><h1>Contribute to the calendar library</h1><p>Sign in to upload a calendar, propose edits, or track your submissions.</p><button id="join">Sign in or create an account</button></div>';
    $("#join").onclick = () => $("#auth").showModal();
    return false;
  }
  if (manager && auth.user.role !== "manager") {
    app.innerHTML =
      '<div class="empty"><h1>Manager access required</h1><p>Your account can submit proposals from the contribution editor.</p></div>';
    return false;
  }
  return true;
}
async function explore() {
  app.innerHTML = `<div class="top"><div><p class="eyebrow">THE PUBLIC CALENDAR REPOSITORY</p><h1>Find your next calendar.</h1><p>Browse, subscribe, and stay in sync. No account needed.</p></div><a class="button" href="/contribute">+ Contribute a calendar</a></div><form class="tools" id="search"><input aria-label="Search calendars" name="q" placeholder="Search calendars or hashtags"><button>Search</button><button type="button" class="quiet" id="combine">Combine selected</button></form><div id="results"></div>`;
  const load = async () => {
    const data = await api(
      "/api/calendars?q=" + encodeURIComponent($("#search [name=q]").value),
    );
    $("#results").innerHTML = data.calendars.length
      ? `<div class="grid">${data.calendars.map((c) => `<article class="card"><div class="row between"><span class="meta">${c.event_count} events · revision ${c.revision}</span><label class="check"><input type="checkbox" name="combine" value="${esc(c.id)}" aria-label="Select ${esc(c.title)} for combination"></label></div><h2><a href="/calendars/${esc(c.slug)}">${esc(c.title)}</a></h2><p>${esc(c.description || "A community-maintained calendar.")}</p><div class="tags">${tags(c.hashtags)}</div><div class="bottom"><a href="/calendars/${esc(c.slug)}">View calendar ↗</a><a class="quiet button" href="/feeds/${esc(c.slug)}.ics">↓ ICS</a></div></article>`).join("")}</div>`
      : '<div class="empty"><h2>No published calendars yet</h2><p>Upload the first calendar for review, or try a different search.</p><a href="/contribute">Contribute a calendar →</a></div>';
  };
  $("#search").onsubmit = safe(async (e) => {
    e.preventDefault();
    await load();
  });
  $("#combine").onclick = () => {
    const ids = [...document.querySelectorAll("[name=combine]:checked")].map(
      (e) => e.value,
    );
    if (ids.length < 2) {
      notify("Select at least two calendars.");
      return;
    }
    location.href = "/contribute?combine=" + encodeURIComponent(ids.join(","));
  };
  await load();
}
async function detail(slug) {
  const c = await api("/api/calendars/" + encodeURIComponent(slug)),
    content = c.content;
  const url = location.origin + "/feeds/" + c.slug + ".ics";
  app.innerHTML = `<div class="top"><div><a href="/">← Calendar repository</a><h1>${esc(content.title)}</h1><p>${esc(content.description)}</p><div class="tags">${tags(content.hashtags)}</div></div><a class="button" href="/contribute?edit=${esc(c.slug)}">Propose an edit</a></div><div class="split"><section class="panel"><div class="row between"><h2>Events</h2><span class="meta">${content.events.length} event definitions</span></div>${
    content.events.length
      ? content.events
          .slice()
          .sort((a, b) => (a.start || a.end).localeCompare(b.start || b.end))
          .map(eventView)
          .join("")
      : "<p>This calendar has no events.</p>"
  }</section><aside class="stack"><section class="panel"><p class="eyebrow">SUBSCRIBE</p><h2>Keep your calendar in sync</h2><p class="hint">Paste this subscription URL into Apple Calendar, Google Calendar, Outlook, or another calendar app. Accepted updates appear when your app refreshes.</p><label>Subscription URL<input class="feed-url" id="feed-url" readonly value="${esc(url)}"></label><div class="row"><button id="copy-feed">Copy URL</button><a href="${esc(url)}" class="button quiet">Download ICS</a></div><p class="hint">An ICS download is a snapshot. A URL subscription receives updates.</p></section><section class="panel"><h3>Publication history</h3><p class="meta">Current revision ${c.revision}</p><div id="history"></div>${content.sources.length ? `<h3>Combined from</h3>${content.sources.map((s) => `<p class="meta"><a href="/calendars/${esc(s.id)}">Source calendar</a> · revision ${s.revision}</p>`).join("")}<p class="hint">Combined calendars are reviewed snapshots of their sources.</p>` : ""}</section></aside></div>`;
  $("#copy-feed").onclick = safe(async () => {
    await navigator.clipboard.writeText(url);
    notify("Subscription URL copied.");
  });
  const h = await api("/api/calendars/" + c.slug + "/revisions");
  $("#history").innerHTML = h.revisions
    .map(
      (r) =>
        `<p class="meta">Revision ${r.revision} · ${esc(r.created_at.slice(0, 10))}</p>`,
    )
    .join("");
}
function blank() {
  return {
    title: "",
    description: "",
    hashtags: [],
    events: [],
    timezones: [],
    sources: [],
  };
}
async function editor() {
  if (!gate()) return;
  const UI = CalendarUI,
    params = new URLSearchParams(location.search);
  const catalog = (await api("/api/calendars")).calendars;
  target = null;
  base = 0;
  draft = blank();
  let mode = params.has("edit") ? "proposal" : "create",
    previewPanel,
    timer;
  const normalize = (e) => ({
    ...e,
    type: UI.kind(e),
    timezone:
      e.timezone ||
      e.raw?.match(/(?:DTSTART|DUE);TZID=([^:;\r\n]+)/)?.[1] ||
      ((e.start || e.end || "").endsWith("Z") ? "UTC" : ""),
  });
  if (params.get("edit")) {
    const c = await api(
      "/api/calendars/" + encodeURIComponent(params.get("edit")),
    );
    draft = c.content;
    target = c.id;
    base = c.revision;
  } else if (params.get("combine"))
    draft = await api("/api/combine", {
      method: "POST",
      body: { ids: params.get("combine").split(",") },
    });
  draft.events = draft.events.map(normalize);
  const options =
    '<option value="">Choose a published calendar</option>' +
    catalog
      .map(
        (c) =>
          `<option value="${esc(c.id)}">${esc(c.title)} · revision ${c.revision}</option>`,
      )
      .join("");
  app.innerHTML = `<div class="top"><div><p class="eyebrow">CALENDAR EDITOR</p><h1 id="editor-heading"></h1><p id="mode-help"></p></div><div class="mode-switch" role="group" aria-label="Editor mode"><button type="button" data-mode="create">Create mode</button><button type="button" data-mode="proposal">Proposal mode</button></div></div><div class="proposal-source panel" id="proposal-source"><label>Calendar to update<select id="target-calendar">${options}</select></label><button type="button" class="quiet" id="load-target">Load calendar to edit</button><p class="hint" id="target-status"></p></div><div class="editor-layout"><form id="editor" class="panel editor-form"><label>Calendar title<input name="title" required maxlength="160"></label><label>Description<textarea name="description" maxlength="10000"></textarea></label><label>Hashtags<input name="hashtags" placeholder="university, toronto, deadlines"></label><div class="row between"><h2>Entries <span class="meta" id="event-count"></span></h2><button type="button" id="add-event" class="quiet">+ Add entry</button></div><details class="import-tools"><summary>Import entries</summary><div class="import-switch" role="group" aria-label="Import source"><button type="button" class="quiet" data-import="device" aria-pressed="true">Upload from device</button><button type="button" class="quiet" data-import="site" aria-pressed="false">From this site</button><button type="button" class="quiet" data-import="link" aria-pressed="false">Subscription link</button></div><div id="device-import"><label>Calendar file<input id="upload" type="file" accept=".ics,.ical,text/calendar"></label></div><div id="site-import" hidden><p class="hint">Browse published calendars and select one or more sources.</p><button type="button" class="quiet" id="browse-sources">Browse calendars</button></div><div id="link-import" hidden><label>Calendar subscription URL<input id="subscription-url" type="url" placeholder="https://… or webcal://…"></label><button type="button" class="quiet" id="import-link">Import from link</button><p class="hint">Use the provider’s Subscribe or iCal link, such as a sports schedule. This imports its current entries; published updates still go through review.</p></div><p class="hint">Imported entries are added to this draft. Your existing entries stay in place.</p></details><div id="events"></div><div class="submit-bar"><button type="submit" id="submit-draft"></button><p class="hint">A manager reviews the calendar before it is published.</p></div></form><section class="panel editor-preview" id="editor-preview" aria-label="Draft calendar preview"></section></div><dialog id="source-picker" class="source-picker"><div class="row between"><h2>Explore calendar sources</h2><button type="button" class="quiet" id="close-sources" aria-label="Close source browser">×</button></div><label>Search calendars<input id="source-search" type="search" placeholder="Search titles, descriptions, or hashtags"></label><div class="source-picker-tools"><label class="check"><input type="checkbox" id="select-visible-sources">Select search results</label><span id="source-selection-count" class="meta" role="status">0 selected</span></div><div id="source-results" class="source-results"></div><div class="source-picker-footer"><p id="source-error" role="alert"></p><button type="button" id="import-selected-sources" disabled>Add selected calendars</button></div></dialog>`;
  const form = $("#editor");
  function metadata() {
    form.elements.title.value = draft.title;
    form.elements.description.value = draft.description;
    form.elements.hashtags.value = draft.hashtags.join(", ");
  }
  function sync() {
    document.querySelectorAll(".event-editor").forEach((box) => {
      const e = draft.events[Number(box.dataset.index)];
      const allDay = box.querySelector("[data-all-day]").checked;
      box.querySelectorAll("[data-field]").forEach((input) => {
        const field = input.dataset.field;
        let val = input.value;
        e[field] = val;
      });
      for (const field of ["start", "end"]) {
        const date = box.querySelector(`[data-date-for="${field}"]`),
          time = box.querySelector(`[data-time-for="${field}"]`);
        const old = e[field] || "";
        let value = date.disabled ? "" : date.value;
        if (value && !allDay)
          value = time.value ? value + "T" + time.value : "";
        if (
          value &&
          (allDay
            ? old.length === 10 && old === value
            : old.slice(0, 16) === value)
        )
          value = old;
        e[field] = value;
      }
      e.type = box.querySelector("[data-kind]").value;
      if (e.type === "deadline") e.start = "";
      if (e.type === "notice") e.end = "";
      const freq = box.querySelector("[data-repeat]").value;
      if (freq !== "keep") {
        if (freq === "none") e.recurrence = "";
        else {
          const interval = Number(box.querySelector("[data-interval]").value);
          let r = `FREQ=${freq};INTERVAL=${interval || 1}`;
          if (freq === "WEEKLY") {
            const days = [
              ...box.querySelectorAll("[data-weekday]:checked"),
            ].map((x) => x.value);
            if (days.length) r += ";BYDAY=" + days.join(",") + ";WKST=MO";
          }
          const ending = box.querySelector("[data-ending]").value;
          if (ending === "count")
            r += ";COUNT=" + box.querySelector("[data-count]").value;
          if (ending === "until") {
            const date = box
              .querySelector("[data-until]")
              .value.replaceAll("-", "");
            if (date) r += ";UNTIL=" + date + (allDay ? "" : "T235959");
          }
          e.recurrence = r;
        }
      }
      box.querySelector(".entry-title").textContent =
        e.title || "Untitled entry";
      box.querySelector(".entry-summary").textContent = UI.timing(e);
      box.querySelector(".repeat-summary").textContent = UI.recurrenceText(
        e.recurrence,
        e.start || e.end,
      );
    });
    draft.title = form.elements.title.value;
    draft.description = form.elements.description.value;
    draft.hashtags = form.elements.hashtags.value
      .split(/[,\s]+/)
      .filter(Boolean);
  }
  function schedule() {
    clearTimeout(timer);
    timer = setTimeout(() => previewPanel?.refresh(), 450);
  }
  function modeUI() {
    document
      .querySelectorAll("[data-mode]")
      .forEach((b) =>
        b.setAttribute("aria-pressed", String(b.dataset.mode === mode)),
      );
    $("#proposal-source").hidden = mode !== "proposal";
    $("#editor-heading").textContent =
      mode === "create" ? "Create a calendar" : "Propose a calendar update";
    $("#mode-help").textContent =
      mode === "create"
        ? "Build a new source from your own entries or existing calendars."
        : "Load a published calendar, then propose your changes.";
    $("#submit-draft").textContent =
      mode === "create" ? "Submit new calendar" : "Submit update proposal";
    $("#target-status").textContent = target
      ? `Editing revision ${base}. Switching modes keeps your current draft.`
      : "Choose and load a calendar before submitting an update.";
    if (target) $("#target-calendar").value = target;
  }
  document.querySelectorAll("[data-mode]").forEach(
    (b) =>
      (b.onclick = () => {
        sync();
        mode = b.dataset.mode;
        modeUI();
        dirty = true;
      }),
  );
  $("#load-target").onclick = safe(async () => {
    const id = $("#target-calendar").value;
    if (!id) throw Error("Choose a calendar to update.");
    if (dirty && !confirm("Load this calendar and replace the current draft?"))
      return;
    const c = await api("/api/calendars/" + encodeURIComponent(id));
    target = c.id;
    base = c.revision;
    draft = c.content;
    draft.events = draft.events.map(normalize);
    metadata();
    renderEntries();
    modeUI();
    dirty = false;
    previewPanel.focus(draft.events[0]?.start || draft.events[0]?.end);
  });
  function renderEntries() {
    $("#event-count").textContent = `(${draft.events.length})`;
    $("#events").innerHTML =
      draft.events
        .map((e, i) => {
          const allDay = (e.start || e.end || "").length === 10,
            t = e.type;
          return `<details class="event-editor" data-index="${i}"><summary><span class="entry-title">${esc(e.title || "Untitled entry")}</span><span class="meta entry-summary">${esc(UI.timing(e))}</span></summary><label>Entry type<select data-kind>${["event", "deadline", "notice"].map((k) => `<option value="${k}" ${t === k ? "selected" : ""}>${k === "event" ? "Event — start and end" : k === "deadline" ? "Deadline — due date only" : "Notice — start only"}</option>`).join("")}</select></label><label>Title<input data-field="title" value="${esc(e.title)}" maxlength="300" required></label><label class="check"><input type="checkbox" data-all-day ${allDay ? "checked" : ""}>All day (no specific time)</label><div class="entry-date-fields">${[
            ["start", "Start", t === "deadline"],
            ["end", t === "deadline" ? "Due" : "End", t === "notice"],
          ]
            .map(
              ([field, label, absent]) =>
                `<div class="date-time-row" data-${field}-label ${absent ? "hidden" : ""}><label>${label} date<input data-date-for="${field}" type="date" value="${esc((e[field] || "").slice(0, 10))}" ${absent ? "disabled" : "required"}></label><label>${label} time<input data-time-for="${field}" type="time" value="${esc((e[field] || "").includes("T") ? e[field].slice(11, 16) : "09:00")}" ${absent || allDay ? "disabled" : "required"}></label></div>`,
            )
            .join(
              "",
            )}</div><p class="hint date-help">${allDay && t === "event" ? "All-day end dates are exclusive: a one-day event ends on the following date." : t === "deadline" ? "This entry appears at its due date." : t === "notice" ? "This entry appears at its start." : "Set the start and end of this event."}</p><label class="zone-label" ${allDay ? "hidden" : ""}>Time zone<input data-field="timezone" value="${esc(e.timezone || "")}" placeholder="America/Toronto"></label><label>Location<input data-field="location" value="${esc(e.location)}"></label><label>Description<textarea data-field="description">${esc(e.description)}</textarea></label><fieldset class="repeat-settings"><legend>Repeat</legend><p class="hint repeat-summary">${esc(UI.recurrenceText(e.recurrence, e.start || e.end))}</p><label>Repeats<select data-repeat>${e.recurrence ? '<option value="keep">Keep existing schedule</option>' : ""}<option value="none">Does not repeat</option><option value="DAILY">Daily</option><option value="WEEKLY">Weekly</option><option value="MONTHLY">Monthly</option><option value="YEARLY">Yearly</option></select></label><div class="repeat-options" hidden><label>Repeat every<input data-interval type="number" min="1" max="999" value="1"><span class="hint interval-unit"></span></label><div class="weekday-options" hidden><p class="weekday-caption">Repeat on these days</p><div class="weekdays">${Object.entries(
            {
              MO: "Mon",
              TU: "Tue",
              WE: "Wed",
              TH: "Thu",
              FR: "Fri",
              SA: "Sat",
              SU: "Sun",
            },
          )
            .map(
              ([k, v]) =>
                `<label class="weekday-chip"><input type="checkbox" data-weekday value="${k}" aria-label="${{ MO: "Monday", TU: "Tuesday", WE: "Wednesday", TH: "Thursday", FR: "Friday", SA: "Saturday", SU: "Sunday" }[k]}"><span>${v}</span></label>`,
            )
            .join(
              "",
            )}</div><p class="hint">Choose one or more days. Weeks start on Monday; each occurrence uses the time above.</p></div><label>Ends<select data-ending><option value="never">Never</option><option value="until">On a date</option><option value="count">After a number of occurrences</option></select></label><label class="until-label" hidden>Last date<input type="date" data-until disabled></label><label class="count-label" hidden>Occurrences<input type="number" data-count min="1" max="10000" value="10" disabled></label></div></fieldset><button type="button" class="danger delete-event">Delete entry</button></details>`;
        })
        .join("") ||
      '<p class="hint">Add an entry or import a calendar to get started.</p>';
    document.querySelectorAll(".event-editor").forEach((box) => {
      const index = Number(box.dataset.index);
      box.querySelector(".delete-event").onclick = () => {
        sync();
        draft.events.splice(index, 1);
        dirty = true;
        renderEntries();
        schedule();
      };
      box.querySelector("[data-kind]").onchange = () => {
        const e = draft.events[index],
          next = box.querySelector("[data-kind]").value;
        const anchor = e.start || e.end;
        sync();
        e.type = next;
        if (next === "deadline") {
          e.end = e.end || anchor;
          e.start = "";
        } else if (next === "notice") {
          e.start = e.start || anchor;
          e.end = "";
        } else {
          e.start = e.start || anchor;
          if (!e.end || e.end === e.start) e.end = nextEnd(e.start);
        }
        renderEntries();
        const updated = document.querySelector(
          `.event-editor[data-index="${index}"]`,
        );
        updated.open = true;
        dirty = true;
        schedule();
      };
      box.querySelector("[data-all-day]").onchange = () => {
        const checked = box.querySelector("[data-all-day]").checked;
        const e = draft.events[index];
        const previous = { start: e.start, end: e.end };
        sync();
        for (const k of ["start", "end"])
          if (e[k])
            e[k] = checked
              ? e[k].slice(0, 10)
              : e[k].slice(0, 10) + "T" + (e._savedTimes?.[k] || "09:00");
        if (
          !checked &&
          e._savedEndDate &&
          previous.end === e._savedEndDate &&
          e._originalEndDate
        )
          e.end = e._originalEndDate + "T" + (e._savedTimes?.end || "10:00");
        if (checked)
          e._savedTimes = {
            start: (previous.start || "").slice(11, 16),
            end: (previous.end || "").slice(11, 16),
          };
        if (e.type === "event" && e.start === e.end) e.end = nextEnd(e.start);
        if (checked) {
          e._originalEndDate = (previous.end || "").slice(0, 10);
          e._savedEndDate = e.end;
        }
        renderEntries();
        document.querySelector(`.event-editor[data-index="${index}"]`).open =
          true;
        dirty = true;
        schedule();
      };
      const repeatUI = () => {
        const freq = box.querySelector("[data-repeat]").value,
          active = !["keep", "none"].includes(freq);
        box.querySelector(".repeat-options").hidden = !active;
        box.querySelector(".weekday-options").hidden = freq !== "WEEKLY";
        if (freq === "WEEKLY" && !box.querySelector("[data-weekday]:checked")) {
          const anchor = draft.events[index].start || draft.events[index].end;
          const weekday =
            ["SU", "MO", "TU", "WE", "TH", "FR", "SA"][
              new Date(anchor.slice(0, 10) + "T12:00:00").getDay()
            ] || "MO";
          box.querySelector(`[data-weekday][value="${weekday}"]`).checked =
            true;
        }
        box.querySelector(".interval-unit").textContent =
          {
            DAILY: "day(s)",
            WEEKLY: "week(s)",
            MONTHLY: "month(s)",
            YEARLY: "year(s)",
          }[freq] || "";
        const ending = box.querySelector("[data-ending]").value;
        for (const name of ["until", "count"]) {
          const enabled = active && ending === name;
          box.querySelector("." + name + "-label").hidden = !enabled;
          box.querySelector("[data-" + name + "]").disabled = !enabled;
          box.querySelector("[data-" + name + "]").required = enabled;
        }
        box.querySelector("[data-interval]").disabled = !active;
      };
      box.querySelector("[data-repeat]").onchange = () => {
        repeatUI();
        sync();
        box.querySelector(".repeat-summary").textContent = UI.recurrenceText(
          draft.events[index].recurrence,
          draft.events[index].start || draft.events[index].end,
        );
        dirty = true;
        schedule();
      };
      box.querySelectorAll("[data-weekday]").forEach(
        (input) =>
          (input.onchange = () => {
            if (!box.querySelector("[data-weekday]:checked")) {
              input.checked = true;
              notify("Keep at least one weekday selected.");
            }
            sync();
            dirty = true;
            schedule();
          }),
      );
      box.querySelector("[data-ending]").onchange = () => {
        repeatUI();
        dirty = true;
        schedule();
      };
    });
  }
  function nextEnd(start) {
    const day = start?.slice(0, 10) || new Date().toISOString().slice(0, 10);
    if (start?.includes("T")) {
      const d = new Date(start);
      d.setHours(d.getHours() + 1);
      return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}T${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
    }
    const d = new Date(day + "T12:00:00");
    d.setDate(d.getDate() + 1);
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  }
  $("#add-event").onclick = () => {
    sync();
    const today = new Date();
    const start = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}-${String(today.getDate()).padStart(2, "0")}T09:00`;
    draft.events.push({
      uid: crypto.randomUUID() + "@timegrid",
      title: "New event",
      type: "event",
      start,
      end: nextEnd(start),
      timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
      description: "",
      location: "",
      recurrence: "",
      raw: "",
    });
    dirty = true;
    renderEntries();
    const last = $("#events").lastElementChild;
    last.open = true;
    last.querySelector("[data-field=title]").focus();
    previewPanel.focus(start);
  };
  function mergeImported(imported, source) {
    sync();
    const combined = structuredClone(draft),
      map = new Map(combined.events.map((e) => [UI.key(e), e]));
    for (const incoming of imported.events.map(normalize)) {
      const old = map.get(UI.key(incoming));
      if (old) {
        const fields = [
          "title",
          "type",
          "start",
          "end",
          "description",
          "location",
          "recurrence",
        ];
        const exceptions = (e) =>
          (e.raw || "")
            .replace(/\r?\n[ \t]/g, "")
            .split(/\r?\n/)
            .filter((line) => /^(EXDATE|RDATE|RECURRENCE-ID)[;:]/.test(line))
            .sort()
            .join("|");
        if (
          fields.some((k) => (old[k] || "") !== (incoming[k] || "")) ||
          exceptions(old) !== exceptions(incoming)
        )
          throw Error(
            "An imported entry conflicts with one in your draft. Resolve the source first.",
          );
      } else {
        combined.events.push(incoming);
        map.set(UI.key(incoming), incoming);
      }
    }
    const zones = new Map();
    for (const raw of [...combined.timezones, ...imported.timezones]) {
      const id = raw.match(/TZID:([^\r\n]+)/)?.[1] || raw;
      if (zones.has(id) && zones.get(id) !== raw)
        throw Error("These calendars use conflicting time zone definitions.");
      zones.set(id, raw);
    }
    combined.timezones = [...zones.values()];
    if (!combined.title) combined.title = imported.title;
    if (!combined.description) combined.description = imported.description;
    for (const ref of [
      ...(imported.sources || []),
      ...(source ? [source] : []),
    ])
      if (
        !combined.sources.some(
          (s) => s.id === ref.id && s.revision === ref.revision,
        )
      )
        combined.sources.push(ref);
    combined.hashtags = [
      ...new Set([...combined.hashtags, ...imported.hashtags]),
    ].slice(0, 12);
    draft = combined;
    metadata();
    renderEntries();
    dirty = true;
    previewPanel.focus(imported.events[0]?.start || imported.events[0]?.end);
    notify("Entries added to your draft.");
  }
  document.querySelectorAll("[data-import]").forEach(
    (b) =>
      (b.onclick = () => {
        document
          .querySelectorAll("[data-import]")
          .forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
        $("#device-import").hidden = b.dataset.import !== "device";
        $("#site-import").hidden = b.dataset.import !== "site";
        $("#link-import").hidden = b.dataset.import !== "link";
      }),
  );
  $("#upload").onchange = safe(async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    if (file.size > 5 * 1024 * 1024)
      throw Error("Choose a file smaller than 5 MB.");
    const body = new FormData();
    body.append("file", file);
    mergeImported(await api("/api/import", { method: "POST", body }));
    e.target.value = "";
  });
  const chosen = new Set();
  let shown = [];
  function sourceResults() {
    const query = $("#source-search").value.trim().toLowerCase();
    shown = catalog.filter((c) =>
      (c.title + " " + c.description + " " + c.hashtags.join(" "))
        .toLowerCase()
        .includes(query),
    );
    $("#source-results").innerHTML = shown.length
      ? shown
          .map(
            (c) =>
              `<label class="source-choice"><input type="checkbox" data-source-id="${esc(c.id)}" ${chosen.has(c.id) ? "checked" : ""}><span><strong>${esc(c.title)}</strong><span class="source-description">${esc(c.description || "Public calendar")}</span><span class="tags">${tags(c.hashtags)}</span><span class="meta">${c.event_count} entries · revision ${c.revision}</span></span></label>`,
          )
          .join("")
      : '<p class="hint">No matching calendars. Try another title or hashtag.</p>';
    $("#source-selection-count").textContent = `${chosen.size} selected`;
    $("#import-selected-sources").disabled = !chosen.size;
    $("#select-visible-sources").checked =
      shown.length > 0 && shown.every((c) => chosen.has(c.id));
    $("#select-visible-sources").indeterminate =
      shown.some((c) => chosen.has(c.id)) &&
      !$("#select-visible-sources").checked;
    document.querySelectorAll("[data-source-id]").forEach(
      (input) =>
        (input.onchange = () => {
          if (input.checked) chosen.add(input.dataset.sourceId);
          else chosen.delete(input.dataset.sourceId);
          sourceResults();
        }),
    );
  }
  $("#browse-sources").onclick = () => {
    sourceResults();
    $("#source-error").textContent = "";
    $("#source-picker").showModal();
    $("#source-search").focus();
  };
  $("#close-sources").onclick = () => $("#source-picker").close();
  $("#source-search").oninput = sourceResults;
  $("#select-visible-sources").onchange = (e) => {
    shown.forEach((c) =>
      e.target.checked ? chosen.add(c.id) : chosen.delete(c.id),
    );
    sourceResults();
  };
  $("#import-selected-sources").onclick = async () => {
    const button = $("#import-selected-sources");
    button.disabled = true;
    $("#source-error").textContent = "";
    try {
      if (chosen.size > 20) throw Error("Select up to 20 calendars at a time.");
      let imported;
      if (chosen.size === 1) {
        const c = await api(
          "/api/calendars/" + encodeURIComponent([...chosen][0]),
        );
        imported = {
          ...c.content,
          sources: [{ id: c.id, revision: c.revision }],
        };
      } else
        imported = await api("/api/combine", {
          method: "POST",
          body: { ids: [...chosen] },
        });
      mergeImported(imported);
      chosen.clear();
      $("#source-picker").close();
    } catch (e) {
      $("#source-error").textContent = e.message;
    } finally {
      button.disabled = !chosen.size;
    }
  };
  $("#import-link").onclick = safe(async () => {
    const url = $("#subscription-url").value.trim();
    if (!url) throw Error("Paste a calendar subscription link.");
    const button = $("#import-link");
    button.disabled = true;
    button.textContent = "Importing…";
    try {
      mergeImported(
        await api("/api/import-url", { method: "POST", body: { url } }),
      );
      $("#subscription-url").value = "";
    } finally {
      button.disabled = false;
      button.textContent = "Import from link";
    }
  });
  form.addEventListener("input", (event) => {
    if (event.target.matches("[data-kind], [data-all-day]")) return;
    dirty = true;
    sync();
    schedule();
  });
  form.addEventListener("change", () => {
    dirty = true;
    sync();
    schedule();
  });
  form.onsubmit = safe(async (e) => {
    e.preventDefault();
    sync();
    if (
      mode === "proposal" &&
      (!target || $("#target-calendar").value !== target)
    )
      throw Error("Load the published calendar you want to update.");
    const button = $("#submit-draft");
    button.disabled = true;
    try {
      await api("/api/proposals", {
        method: "POST",
        body: {
          target: mode === "proposal" ? target : null,
          base_revision: mode === "proposal" ? base : 0,
          content: draft,
        },
      });
      dirty = false;
      location.href = "/dashboard";
    } finally {
      button.disabled = false;
    }
  });
  metadata();
  renderEntries();
  modeUI();
  previewPanel = UI.monthPreview($("#editor-preview"), {
    initial: draft.events[0]?.start || draft.events[0]?.end,
    load: async (start, end) => {
      sync();
      const visible = structuredClone(draft);
      visible.events = visible.events.filter(
        (e) =>
          e.title &&
          (e.type === "event"
            ? e.start && e.end
            : e.type === "deadline"
              ? e.end
              : e.start),
      );
      const result = await api("/api/preview", {
        method: "POST",
        body: { content: visible, start, end },
      });
      if (visible.events.length !== draft.events.length)
        result.warnings.push(
          "Finish the dates and title of incomplete entries to see them here.",
        );
      return result;
    },
    onSelect: (e) => {
      const i = draft.events.findIndex((item) => UI.key(item) === e.key);
      const box = document.querySelector(`.event-editor[data-index="${i}"]`);
      if (box) {
        box.open = true;
        box.scrollIntoView({ behavior: "smooth", block: "center" });
        box.querySelector("[data-field=title]").focus();
      }
    },
  });
}

function diffView(d) {
  const UI = CalendarUI;
  const names = {
    title: "Title",
    type: "Entry type",
    start: "Start",
    end: "End / due",
    location: "Location",
    description: "Description",
    recurrence: "Repeats",
    timezone: "Time zone",
    exceptions: "Occurrence exceptions",
    hashtags: "Hashtags",
    sources: "Sources",
  };
  function value(field, v, e) {
    if (field === "recurrence") return UI.recurrenceText(v, e?.start || e?.end);
    if (field === "start" || field === "end")
      return v ? UI.dateText(v) : "None";
    if (field === "hashtags")
      return v?.length ? v.map((t) => "#" + t).join(", ") : "None";
    if (field === "sources")
      return v?.length
        ? v.map((s) => "Source calendar · revision " + s.revision).join("; ")
        : "None";
    if (field === "exceptions") return "Occurrence dates updated";
    return v || "None";
  }
  const metadata = Object.entries(d.metadata)
    .map(
      ([field, v]) =>
        `<div class="field-diff change-edited"><strong>${esc(names[field] || field)}</strong><span><del>${esc(value(field, v.before))}</del> → <ins>${esc(value(field, v.after))}</ins></span></div>`,
    )
    .join("");
  const entries = [
    ...(d.unchanged || []).map((e) => ({ e, status: "unchanged" })),
    ...d.added.map((e) => ({ e, status: "added" })),
    ...d.deleted.map((e) => ({ e, status: "deleted" })),
    ...d.edited.map((x) => ({
      e: x.after,
      status: "edited",
      before: x.before,
      fields: x.fields,
    })),
  ].sort((a, b) => (a.e.start || a.e.end).localeCompare(b.e.start || b.e.end));
  return `<div class="change-legend"><span>Unchanged</span><span class="change-added">+ Added</span><span class="change-deleted">− Deleted</span><span class="change-edited">~ Changed</span></div>${metadata}<div class="review-entries">${entries
    .map(({ e, status, before, fields }) =>
      status === "edited"
        ? `<article class="review-entry"><p class="meta">Changed entry</p>${[
            "title",
            "type",
            "start",
            "end",
            "location",
            "description",
            "recurrence",
            "timezone",
          ]
            .map((field) => {
              const changed = fields?.includes(field);
              if (!changed && !e[field] && field !== "type") return "";
              return `<div class="field-diff ${changed ? "change-edited" : ""}"><strong>${esc(names[field])}</strong><span>${changed ? `<del>${esc(value(field, before[field], before))}</del> → <ins>${esc(value(field, e[field], e))}</ins>` : esc(value(field, field === "type" ? UI.kind(e) : e[field], e))}</span></div>`;
            })
            .join(
              "",
            )}${fields?.includes("exceptions") ? '<p class="change-edited">Occurrence exception dates changed.</p>' : ""}</article>`
        : `<article class="review-entry change-${status}"><strong class="review-status">${status === "added" ? "+ Added" : status === "deleted" ? "− Deleted" : "Unchanged"}</strong>${eventView(e)}</article>`,
    )
    .join("")}</div>`;
}
async function dashboard(manager = false) {
  if (!gate(manager)) return;
  const { proposals } = await api("/api/proposals");
  const rows = manager
    ? proposals
    : proposals.filter((p) => p.author === auth.user.id);
  app.innerHTML = `<div class="top"><div><p class="eyebrow">${manager ? "MANAGER WORKSPACE" : "CONTRIBUTOR WORKSPACE"}</p><h1>${manager ? "Review the next revision." : "Your contributions."}</h1><p>${manager ? "Review changes in the calendar and check the highlighted fields before publishing." : "Follow your submissions and read manager feedback."}</p></div><a class="button" href="/contribute">+ New calendar</a></div><div class="tools"><span class="pill pending">${rows.filter((p) => p.status === "pending").length} pending</span><span class="pill accepted">${rows.filter((p) => p.status === "accepted").length} accepted</span><span class="pill rejected">${rows.filter((p) => p.status === "rejected").length} rejected</span></div><section>${rows.map((p, i) => `<article class="panel review"><div class="row between"><h2>${esc(p.content.title)}</h2><span class="pill ${p.status}">${esc(p.status)}</span></div><p class="meta">${esc(p.username)} · ${esc(CalendarUI.dateText(p.created_at.slice(0, 10)))} · ${p.target ? "Update to revision " + p.base_revision : "New calendar"}</p>${p.message ? `<p>${esc(p.message)}</p>` : ""}<div class="tags">${tags(p.content.hashtags)}</div><p class="meta">${p.changes.unchanged?.length || 0} unchanged · +${p.changes.added.length} added · ${p.changes.edited.length} edited · −${p.changes.deleted.length} deleted</p><details class="review-calendar-toggle" data-id="${p.id}" ${i === 0 ? "open" : ""}><summary>Calendar preview</summary><div class="review-calendar"></div></details><details class="review-diff"><summary>Compare all entries and fields</summary>${diffView(p.changes)}</details>${p.reason ? `<p><strong>Manager feedback:</strong> ${esc(p.reason)}</p>` : ""}${manager && p.status === "pending" ? `<form class="review-form" data-id="${p.id}"><label>Approved hashtags<input name="hashtags" value="${esc(p.content.hashtags.join(", "))}"></label><label>Review note (required for rejection)<textarea name="reason" maxlength="4000"></textarea></label><div class="row"><button name="decision" value="accept">Accept & publish</button><button name="decision" value="reject" class="danger">Reject proposal</button></div></form>` : ""}</article>`).join("") || '<div class="empty"><h2>No proposals yet</h2><p>New submissions will appear here.</p></div>'}</section>`;
  document.querySelectorAll(".review-calendar-toggle").forEach((toggle) => {
    let loaded = false;
    const load = () => {
      if (!toggle.open || loaded) return;
      loaded = true;
      const p = rows.find((p) => p.id === toggle.dataset.id);
      const statuses = new Map([
        ...p.changes.added.map((e) => [CalendarUI.key(e), "added"]),
        ...p.changes.edited.map((x) => [CalendarUI.key(x.after), "edited"]),
      ]);
      CalendarUI.monthPreview(toggle.querySelector(".review-calendar"), {
        review: true,
        initial:
          p.content.events[0]?.start ||
          p.content.events[0]?.end ||
          p.changes.deleted[0]?.start ||
          p.changes.deleted[0]?.end,
        load: async (start, end) => {
          const current = await api("/api/preview", {
            method: "POST",
            body: { content: p.content, start, end },
          });
          current.events = current.events.map((e) => ({
            ...e,
            change: statuses.get(e.key) || "unchanged",
          }));
          if (p.changes.deleted.length) {
            const old = await api("/api/preview", {
              method: "POST",
              body: {
                content: { ...p.before, events: p.changes.deleted },
                start,
                end,
              },
            });
            current.events.push(
              ...old.events.map((e) => ({ ...e, change: "deleted" })),
            );
            current.warnings.push(...old.warnings);
          }
          return current;
        },
      });
    };
    toggle.addEventListener("toggle", load);
    load();
  });
  document.querySelectorAll(".review-form").forEach(
    (form) =>
      (form.onsubmit = safe(async (e) => {
        e.preventDefault();
        const decision = e.submitter.value;
        e.submitter.disabled = true;
        try {
          await api("/api/proposals/" + form.dataset.id + "/review", {
            method: "POST",
            body: {
              decision,
              reason: form.elements.reason.value,
              hashtags: form.elements.hashtags.value
                .split(/[,\s]+/)
                .filter(Boolean),
            },
          });
          notify(
            decision === "accept"
              ? "Calendar published."
              : "Proposal rejected.",
          );
          await dashboard(manager);
        } finally {
          e.submitter.disabled = false;
        }
      })),
  );
}

async function route() {
  const path = location.pathname;
  document
    .querySelectorAll("nav a")
    .forEach((a) => a.classList.toggle("active", a.pathname === path));
  if (path.startsWith("/calendars/") || path.startsWith("/p/"))
    await detail(path.split("/")[2]);
  else if (path === "/contribute") await editor();
  else if (path === "/dashboard") await dashboard();
  else if (path === "/manage") await dashboard(true);
  else await explore();
}
window.addEventListener("beforeunload", (e) => {
  if (dirty) {
    e.preventDefault();
    e.returnValue = "";
  }
});
(async () => {
  try {
    auth = await api("/api/session");
    accounts();
    await route();
  } catch (e) {
    app.innerHTML = `<div class="empty"><h1>Unable to load this page</h1><p>${esc(e.message)}</p><a href="/">Return to calendars</a></div>`;
  }
})();
