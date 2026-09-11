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
  return `<div class="event"><div class="event-date">${esc(e.start)}${e.end ? " → " + esc(e.end) : ""}</div><h3>${esc(e.title)}</h3>${e.location ? `<p>${esc(e.location)}</p>` : ""}${e.description ? `<p>${esc(e.description)}</p>` : ""}${e.recurrence ? `<span class="pill">Repeats · ${esc(e.recurrence)}</span>` : ""}</div>`;
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
          .sort((a, b) => a.start.localeCompare(b.start))
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
  const params = new URLSearchParams(location.search);
  target = null;
  base = 0;
  draft = blank();
  if (params.get("edit")) {
    const c = await api(
      "/api/calendars/" + encodeURIComponent(params.get("edit")),
    );
    draft = c.content;
    target = c.id;
    base = c.revision;
  } else if (params.get("combine")) {
    draft = await api("/api/combine", {
      method: "POST",
      body: { ids: params.get("combine").split(",") },
    });
  }
  app.innerHTML = `<div class="top"><div><p class="eyebrow">CONTRIBUTION EDITOR</p><h1>${target ? "Propose a calendar update" : "Build a calendar worth sharing."}</h1><p>${target ? `Editing published revision ${base}. Your changes go to a manager for review.` : "Upload an ICS calendar or start with your own events."}</p></div><a href="/">← Explore calendars</a></div><div class="split"><form id="editor" class="panel"><label>Calendar title<input name="title" maxlength="160" required></label><label>Description<textarea name="description" maxlength="10000"></textarea></label><label>Hashtags<input name="hashtags" placeholder="university, toronto, deadlines"></label><div class="row between"><h2>Events <span class="meta" id="event-count"></span></h2><button type="button" id="add-event" class="quiet">+ Add event</button></div><div id="events"></div><label>What does this proposal change?<textarea name="message" maxlength="4000" required placeholder="Explain the source and the changes for the manager."></textarea></label><button type="submit">Submit for review</button></form><aside class="stack"><section class="panel"><p class="eyebrow">IMPORT</p><h3>Bring your calendar</h3><p class="hint">ICS files preserve recurrence rules and timezones. Import replaces the events in this draft.</p><label>Calendar file<input id="upload" type="file" accept=".ics,.ical,text/calendar"></label></section><section class="panel"><h3>How review works</h3><p class="hint">1. Prepare your events and hashtags.<br>2. Submit a proposal with a short explanation.<br>3. A manager reviews the changes and publishes or rejects them.</p><p class="hint">Published calendars stay unchanged while a proposal is pending. Track feedback in My proposals.</p>${draft.sources.length ? `<p class="hint">Combined from ${draft.sources.length} published calendar revisions.</p>` : ""}</section><section class="panel"><h3>Dates & recurrence</h3><p class="hint">Use YYYY-MM-DD for all-day events, or an ISO date-time such as 2026-09-15T09:00:00-04:00. All-day end dates are exclusive. Imported recurrence rules stay attached to their event; editing a recurring event changes its series.</p></section></aside></div>`;
  const form = $("#editor");
  function metadata() {
    form.elements.title.value = draft.title;
    form.elements.description.value = draft.description;
    form.elements.hashtags.value = draft.hashtags.join(", ");
  }
  metadata();
  renderEvents();
  form.oninput = () => {
    dirty = true;
  };
  $("#add-event").onclick = () => {
    syncEvents();
    draft.events.push({
      uid: crypto.randomUUID() + "@timegrid",
      title: "New event",
      start: new Date().toISOString().slice(0, 10),
      end: "",
      description: "",
      location: "",
      raw: "",
      recurrence: "",
    });
    dirty = true;
    renderEvents();
    const last = $("#events").lastElementChild;
    last.open = true;
    last.querySelector("input").focus();
  };
  $("#upload").onchange = safe(async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    if (file.size > 5 * 1024 * 1024)
      throw Error("Choose a file smaller than 5 MB.");
    if (
      draft.events.length &&
      !confirm("Replace the events in this draft with the uploaded calendar?")
    )
      return;
    const body = new FormData();
    body.append("file", file);
    const imported = await api("/api/import", { method: "POST", body });
    draft = { ...imported, sources: draft.sources };
    metadata();
    renderEvents();
    dirty = true;
    notify("Calendar imported. Review the events before submitting.");
  });
  form.onsubmit = safe(async (e) => {
    e.preventDefault();
    syncEvents();
    draft.title = form.elements.title.value;
    draft.description = form.elements.description.value;
    draft.hashtags = form.elements.hashtags.value
      .split(/[,\s]+/)
      .filter(Boolean);
    const button = form.querySelector("[type=submit]");
    button.disabled = true;
    try {
      await api("/api/proposals", {
        method: "POST",
        body: {
          target,
          base_revision: base,
          content: draft,
          message: form.elements.message.value,
        },
      });
      dirty = false;
      location.href = "/dashboard";
    } finally {
      button.disabled = false;
    }
  });
}
function syncEvents() {
  document.querySelectorAll(".event-editor").forEach((box) => {
    const e = draft.events[Number(box.dataset.index)];
    box
      .querySelectorAll("[data-field]")
      .forEach((input) => (e[input.dataset.field] = input.value));
  });
}
function renderEvents() {
  $("#event-count").textContent = `(${draft.events.length})`;
  $("#events").innerHTML =
    draft.events
      .map(
        (e, i) =>
          `<details class="event-editor" data-index="${i}"><summary>${esc(e.title)} <span class="meta">· ${esc(e.start)}</span></summary><div class="event-grid">${[
            ["title", "Event title"],
            ["start", "Start"],
            ["end", "End (optional)"],
            ["location", "Location"],
          ]
            .map(
              ([k, label]) =>
                `<label>${label}<input data-field="${k}" value="${esc(e[k])}" ${k === "title" || k === "start" ? "required" : ""}></label>`,
            )
            .join(
              "",
            )}</div><label>Description<textarea data-field="description">${esc(e.description)}</textarea></label>${e.recurrence ? `<p class="hint">Preserved recurrence: ${esc(e.recurrence)}</p>` : ""}${e.recurrence_id ? `<p class="hint">Occurrence override: ${esc(e.recurrence_id)}</p>` : ""}<button type="button" class="danger delete-event" data-index="${i}">Delete event</button></details>`,
      )
      .join("") ||
    '<p class="hint">No events yet. Add an event or import an ICS file.</p>';
  document.querySelectorAll(".delete-event").forEach(
    (b) =>
      (b.onclick = () => {
        syncEvents();
        draft.events.splice(Number(b.dataset.index), 1);
        dirty = true;
        renderEvents();
      }),
  );
}
function diffView(d) {
  return `${Object.entries(d.metadata)
    .map(
      ([k, v]) =>
        `<div class="diff"><strong>${esc(k)}</strong><p class="hint">Before: ${esc(JSON.stringify(v.before ?? ""))}<br>After: ${esc(JSON.stringify(v.after))}</p></div>`,
    )
    .join(
      "",
    )}${d.added.map((e) => `<div class="diff add"><strong>Added</strong>${eventView(e)}</div>`).join("")}${d.deleted.map((e) => `<div class="diff delete"><strong>Deleted</strong>${eventView(e)}</div>`).join("")}${d.edited.map((e) => `<div class="diff"><strong>Edited · before</strong>${eventView(e.before)}<strong>After</strong>${eventView(e.after)}</div>`).join("")}`;
}
async function dashboard(manager = false) {
  if (!gate(manager)) return;
  const { proposals } = await api("/api/proposals");
  const rows = manager
    ? proposals
    : proposals.filter((p) => p.author === auth.user.id);
  app.innerHTML = `<div class="top"><div><p class="eyebrow">${manager ? "MANAGER WORKSPACE" : "CONTRIBUTOR WORKSPACE"}</p><h1>${manager ? "Review the next revision." : "Your contributions."}</h1><p>${manager ? "Check event changes, sources, and hashtags before publishing." : "Follow your submissions and read manager feedback."}</p></div><a class="button" href="/contribute">+ New calendar</a></div><div class="tools"><span class="pill pending">${rows.filter((p) => p.status === "pending").length} pending</span><span class="pill accepted">${rows.filter((p) => p.status === "accepted").length} accepted</span><span class="pill rejected">${rows.filter((p) => p.status === "rejected").length} rejected</span></div><section>${rows.map((p) => `<article class="panel review"><div class="row between"><h2>${esc(p.content.title)}</h2><span class="pill ${p.status}">${esc(p.status)}</span></div><p class="meta">${esc(p.username)} · ${esc(p.created_at.slice(0, 10))} · ${p.target ? "Update to revision " + p.base_revision : "New calendar"}</p><p>${esc(p.message)}</p><div class="tags">${tags(p.content.hashtags)}</div><p class="meta">+${p.changes.added.length} added · ${p.changes.edited.length} edited · −${p.changes.deleted.length} deleted</p><details><summary>Review all changes</summary>${diffView(p.changes)}</details>${p.reason ? `<p><strong>Manager feedback:</strong> ${esc(p.reason)}</p>` : ""}${manager && p.status === "pending" ? `<form class="review-form" data-id="${p.id}"><label>Approved hashtags<input name="hashtags" value="${esc(p.content.hashtags.join(", "))}"></label><label>Review note (required for rejection)<textarea name="reason" maxlength="4000"></textarea></label><div class="row"><button name="decision" value="accept">Accept & publish</button><button name="decision" value="reject" class="danger">Reject proposal</button></div></form>` : ""}</article>`).join("") || '<div class="empty"><h2>No proposals yet</h2><p>New submissions will appear here.</p></div>'}</section>`;
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
