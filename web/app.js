/*
 * app.js
 * ------
 * Views, state and event wiring.
 *
 * The rule this file follows: it never decides who may see what. It asks for
 * data and renders whatever comes back. Postgres decides what comes back. If
 * you are viewing someone else's ledger it is because they turned sharing on,
 * and the editing controls are hidden because the database would reject the
 * write anyway.
 */

import * as db from "./data.js";
import { isConfigured } from "./config.js";
import {
  renderBars, renderRibbon, renderMeter, meterCaption, categoryColour,
} from "./charts.js";

const $ = (id) => document.getElementById(id);

const state = {
  profile: null,
  kind: "bills",             // which ledger tab
  view: "budget",
  editingId: null,
  sort: { bills: "due_day", income: "pay_day" },
  reverse: false,
  search: "",
  bills: [],
  income: [],
  connections: [],
  viewingId: null,           // whose ledger is on screen
};

const SPEC = {
  bills: {
    noun: "bill",
    addLabel: "Add a bill",
    dayField: "due_day",
    columns: [
      { key: "name", label: "Bill", align: "l" },
      { key: "due_day", label: "Due", align: "c" },
      { key: "amount", label: "Amount", align: "r" },
    ],
    fields: [
      { key: "name", label: "Name", type: "text" },
      { key: "amount", label: "Amount ($)", type: "text", inputmode: "decimal" },
      { key: "category", label: "Category", type: "select", options: db.CATEGORIES, default: "Other" },
      { key: "due_day", label: "Due day", type: "number", default: "1" },
    ],
    searchText: (r) => `${r.name} ${r.category} ${db.ordinal(r.due_day)}`,
  },
  income: {
    noun: "income source",
    addLabel: "Add income",
    dayField: "pay_day",
    columns: [
      { key: "name", label: "Source", align: "l" },
      { key: "pay_day", label: "Paid", align: "c" },
      { key: "amount", label: "Amount", align: "r" },
    ],
    fields: [
      { key: "name", label: "Source", type: "text" },
      { key: "amount", label: "Amount ($)", type: "text", inputmode: "decimal" },
      { key: "pay_day", label: "Paid on", type: "number", default: "1" },
    ],
    searchText: (r) => `${r.name} ${db.ordinal(r.pay_day)}`,
  },
};

const isOwnLedger = () => state.viewingId === state.profile?.id;
const records = () => (state.kind === "bills" ? state.bills : state.income);

/* ---------------------------------------------------------------- chrome */

function toast(title, message, tone = "accent") {
  const node = document.createElement("div");
  node.className = `panel toast is-${tone}`;
  node.innerHTML = `<div class="t"></div><div class="m"></div>`;
  node.querySelector(".t").textContent = title;
  node.querySelector(".m").textContent = message;
  $("toasts").append(node);
  setTimeout(() => node.remove(), 4200);
}

function confirmAction(message, confirmLabel = "Delete") {
  return new Promise((resolve) => {
    const host = $("modal-host");
    host.innerHTML = `
      <div class="scrim">
        <div class="panel modal" role="dialog" aria-modal="true">
          <h3>Please confirm</h3>
          <p></p>
          <div class="row" style="justify-content:flex-end">
            <button class="btn" data-no>Cancel</button>
            <button class="btn btn-primary" data-yes></button>
          </div>
        </div>
      </div>`;
    host.querySelector("p").textContent = message;
    host.querySelector("[data-yes]").textContent = confirmLabel;

    const finish = (value) => { host.innerHTML = ""; resolve(value); };
    host.querySelector("[data-yes]").onclick = () => finish(true);
    host.querySelector("[data-no]").onclick = () => finish(false);
    host.querySelector(".scrim").onclick = (e) => {
      if (e.target === host.querySelector(".scrim")) finish(false);
    };
    host.querySelector("[data-yes]").focus();
  });
}

function setTheme(mode) {
  document.documentElement.dataset.theme = mode;
  $("theme-toggle").textContent = mode === "dark" ? "☀" : "☾";
  localStorage.setItem("fc-theme", mode);
}

/* ------------------------------------------------------------------ auth */

let authMode = "signin";

function renderAuthMode() {
  const signup = authMode === "signup";
  $("auth-title").textContent = signup ? "Create your account" : "Welcome back";
  $("auth-lede").textContent = signup
    ? "Track your bills privately. Share only what you choose, with people you choose."
    : "Sign in to see where your month is going.";
  $("auth-submit").textContent = signup ? "Create account" : "Sign in";
  $("name-field").hidden = !signup;
  $("auth-name").required = signup;
  $("auth-password").autocomplete = signup ? "new-password" : "current-password";
  $("auth-switch-text").textContent = signup ? "Already have an account?" : "New here?";
  $("auth-switch").textContent = signup ? "Sign in" : "Create an account";
  $("auth-error").textContent = "";
}

function wireAuth() {
  $("auth-switch").onclick = () => {
    authMode = authMode === "signin" ? "signup" : "signin";
    renderAuthMode();
  };

  $("auth-form").onsubmit = async (event) => {
    event.preventDefault();
    const button = $("auth-submit");
    button.disabled = true;
    $("auth-error").textContent = "";
    try {
      if (authMode === "signup") {
        const { session } = await db.signUp(
          $("auth-email").value, $("auth-password").value, $("auth-name").value
        );
        if (!session) {
          toast("Check your email", "Confirm your address to finish signing up.", "good");
          authMode = "signin";
          renderAuthMode();
          return;
        }
      } else {
        await db.signIn($("auth-email").value, $("auth-password").value);
      }
      await start();
    } catch (error) {
      $("auth-error").textContent = error.message;
    } finally {
      button.disabled = false;
    }
  };

  $("auth-forgot").onclick = async () => {
    const email = $("auth-email").value.trim();
    if (!email) {
      $("auth-error").textContent = "Enter your email first, then click again.";
      return;
    }
    try {
      await db.sendPasswordReset(email);
      toast("Check your email", "We sent a link to reset your password.", "good");
    } catch (error) {
      $("auth-error").textContent = error.message;
    }
  };
}

/* --------------------------------------------------------------- ledgers */

function renderEditorFields() {
  const spec = SPEC[state.kind];
  const host = $("editor-fields");
  host.replaceChildren();

  for (const field of spec.fields) {
    const label = document.createElement("label");
    label.className = "field";
    const caption = document.createElement("span");
    caption.textContent = field.label;
    label.append(caption);

    let input;
    if (field.type === "select") {
      input = document.createElement("select");
      for (const option of field.options) {
        input.append(new Option(option, option));
      }
      input.value = field.default;
    } else {
      input = document.createElement("input");
      input.type = field.type === "number" ? "number" : "text";
      if (field.type === "number") { input.min = 1; input.max = 31; }
      if (field.inputmode) input.inputMode = field.inputmode;
      input.value = field.default ?? "";
    }
    input.id = `f-${field.key}`;
    label.append(input);
    host.append(label);
  }
}

function editorValues() {
  const values = {};
  for (const field of SPEC[state.kind].fields) values[field.key] = $(`f-${field.key}`).value;
  return values;
}

function resetEditor() {
  state.editingId = null;
  for (const field of SPEC[state.kind].fields) {
    $(`f-${field.key}`).value = field.default ?? "";
  }
  $("record-error").textContent = "";
  $("editor-title").textContent = SPEC[state.kind].addLabel;
  $("save-btn").textContent = SPEC[state.kind].addLabel;
  $("selection-hint").textContent = "";
  renderLedger();
}

function loadIntoEditor(record) {
  state.editingId = record.id;
  for (const field of SPEC[state.kind].fields) {
    $(`f-${field.key}`).value = record[field.key] ?? field.default ?? "";
  }
  $("record-error").textContent = "";
  $("editor-title").textContent = `Editing ${record.name}`;
  $("save-btn").textContent = "Save changes";
  $("selection-hint").textContent = `Editing ${record.name}`;
  renderLedger();
}

function visibleRows() {
  const spec = SPEC[state.kind];
  const query = state.search.trim().toLowerCase();
  let rows = records();
  if (query) rows = rows.filter((r) => spec.searchText(r).toLowerCase().includes(query));

  const key = state.sort[state.kind];
  return [...rows].sort((a, b) => {
    const [x, y] = [a[key], b[key]];
    const cmp = typeof x === "string"
      ? x.localeCompare(y)
      : Number(x) - Number(y);
    return state.reverse ? -cmp : cmp;
  });
}

function renderLedger() {
  const spec = SPEC[state.kind];
  const rows = visibleRows();

  $("ledger-title").textContent = state.kind === "bills" ? "Bills" : "Income";
  $("search").placeholder = `Search ${state.kind}…`;
  $("ledger-sub").textContent = records().length
    ? `${db.money(db.total(records()))} per month`
    : `No ${state.kind} yet`;

  // Header
  const head = $("ledger-head");
  head.replaceChildren();
  const headRow = document.createElement("tr");
  for (const column of spec.columns) {
    const cell = document.createElement("th");
    cell.className = column.align === "r" ? "num" : column.align === "c" ? "mid" : "";
    cell.textContent = column.label.toUpperCase();
    if (state.sort[state.kind] === column.key) {
      cell.textContent += state.reverse ? "  ▾" : "  ▴";
    }
    cell.onclick = () => {
      if (state.sort[state.kind] === column.key) state.reverse = !state.reverse;
      else { state.sort[state.kind] = column.key; state.reverse = column.key === "amount"; }
      renderLedger();
    };
    headRow.append(cell);
  }
  head.append(headRow);

  // Body
  const body = $("ledger-body");
  body.replaceChildren();
  $("ledger-empty").replaceChildren();

  if (!rows.length) {
    const message = records().length
      ? ["No matches", "Try a different search"]
      : [`No ${state.kind} yet`, isOwnLedger() ? "Add your first one below" : "Nothing shared here yet"];
    $("ledger-empty").innerHTML =
      `<p class="empty"><strong></strong><span></span></p>`;
    $("ledger-empty").querySelector("strong").textContent = message[0];
    $("ledger-empty").querySelector("span").textContent = message[1];
    return;
  }

  for (const record of rows) {
    const tr = document.createElement("tr");
    if (record.id === state.editingId) tr.className = "is-selected";

    for (const column of spec.columns) {
      const td = document.createElement("td");
      if (column.key === "amount") {
        td.className = "num";
        td.textContent = db.money(Number(record.amount));
      } else if (column.key === spec.dayField) {
        td.className = "mid muted";
        td.textContent = db.ordinal(record[column.key]);
      } else {
        const dot = document.createElement("span");
        dot.className = "dot";
        dot.style.background = state.kind === "bills"
          ? categoryColour(state.bills, record.category)
          : "var(--accent)";
        td.append(dot, document.createTextNode(record.name));
      }
      tr.append(td);
    }

    if (isOwnLedger()) tr.onclick = () => loadIntoEditor(record);
    else tr.style.cursor = "default";
    body.append(tr);
  }
}

function renderHero() {
  const incoming = db.total(state.income);
  const outgoing = db.total(state.bills);
  const spare = incoming - outgoing;

  const hero = $("hero-value");
  hero.classList.remove("is-critical", "is-empty");
  if (!state.income.length) {
    hero.textContent = "—";
    hero.classList.add("is-empty");
  } else {
    hero.textContent = db.money(spare);
    if (spare < 0) hero.classList.add("is-critical");
  }

  $("chip-income").textContent = state.income.length ? db.money(incoming) : "—";
  $("chip-income-note").textContent = state.income.length
    ? `${state.income.length} source${state.income.length === 1 ? "" : "s"}`
    : "none yet";
  $("chip-bills").textContent = state.bills.length ? db.money(outgoing) : "—";
  $("chip-bills-note").textContent = state.bills.length
    ? `${state.bills.length} bill${state.bills.length === 1 ? "" : "s"}`
    : "none yet";

  const upcomingBill = db.nextDue(state.bills, "due_day");
  const upcomingPay = db.nextDue(state.income, "pay_day");
  const when = (days) => (days === 0 ? "Today" : days === 1 ? "Tomorrow" : `In ${days} days`);

  if (upcomingBill) {
    const days = db.daysUntil(upcomingBill.due_day);
    $("chip-next-label").textContent = "Next bill due";
    $("chip-next").textContent = when(days);
    $("chip-next-note").textContent = `${upcomingBill.name} · ${db.ordinal(upcomingBill.due_day)}`;
  } else if (upcomingPay) {
    $("chip-next-label").textContent = "Next payday";
    $("chip-next").textContent = when(db.daysUntil(upcomingPay.pay_day));
    $("chip-next-note").textContent = `${upcomingPay.name} · ${db.ordinal(upcomingPay.pay_day)}`;
  } else {
    $("chip-next-label").textContent = "Next due";
    $("chip-next").textContent = "—";
    $("chip-next-note").textContent = "nothing scheduled";
  }

  renderMeter($("meter-fill"), $("meter-over"), incoming, outgoing);
  const caption = meterCaption(incoming, outgoing);
  $("meter-caption").textContent = caption.text;
  $("meter-caption").classList.toggle("is-critical", caption.tone === "critical");
}

function renderCharts() {
  const shown = renderBars($("bars"), state.bills);
  $("bars-title").textContent =
    state.bills.length > shown
      ? `Bills by amount · top ${shown} of ${state.bills.length}`
      : "Bills by amount";
  renderRibbon($("ribbon"), $("ribbon-legend"), state.bills);
}

function renderBudget() {
  renderHero();
  renderLedger();
  renderCharts();

  // Someone else's ledger is read-only, because the database would refuse the
  // write anyway. Hiding the controls keeps that honest rather than surprising.
  const own = isOwnLedger();
  $("record-form").classList.toggle("hidden", !own);
  $("delete-btn").classList.toggle("hidden", !own);
}

/* --------------------------------------------------------------- people */

function renderConnections() {
  const host = $("connections");
  host.replaceChildren();

  if (!state.connections.length) {
    host.innerHTML =
      '<p class="empty"><strong>Nobody yet</strong>Invite someone to plan together</p>';
    return;
  }

  for (const connection of state.connections) {
    const card = document.createElement("div");
    card.className = "well";
    card.style.cssText = "padding:16px;margin-bottom:12px";

    const head = document.createElement("div");
    head.className = "row";
    const name = document.createElement("strong");
    name.textContent = connection.otherName;
    const tag = document.createElement("span");
    tag.className = "tag";
    tag.textContent = connection.relationship;
    head.append(name, tag);

    const status = document.createElement("span");
    status.className = `tag ${connection.status === "accepted" ? "is-good" : "is-warning"}`;
    status.textContent = connection.status;
    head.append(document.createElement("div")).className = "spacer";
    head.append(status);
    card.append(head);

    if (connection.status === "pending" && !connection.outgoing) {
      const actions = document.createElement("div");
      actions.className = "row";
      actions.style.marginTop = "12px";
      const accept = Object.assign(document.createElement("button"),
        { className: "btn btn-primary", textContent: "Accept" });
      const decline = Object.assign(document.createElement("button"),
        { className: "btn", textContent: "Decline" });
      accept.onclick = () => respond(connection, true);
      decline.onclick = () => respond(connection, false);
      actions.append(accept, decline);
      card.append(actions);
    } else if (connection.status === "pending") {
      const note = document.createElement("p");
      note.className = "small muted";
      note.style.margin = "10px 0 0";
      note.textContent = "Waiting for them to accept.";
      card.append(note);
    } else if (connection.status === "accepted") {
      const toggleRow = document.createElement("label");
      toggleRow.className = "row";
      toggleRow.style.cssText = "margin-top:12px;cursor:pointer;gap:8px";
      const box = document.createElement("input");
      box.type = "checkbox";
      box.checked = connection.iShare;
      box.style.cssText = "width:auto;margin:0";
      box.onchange = () => share(connection, box.checked);
      const text = document.createElement("span");
      text.className = "small";
      text.textContent = "Share my figures with them";
      toggleRow.append(box, text);
      card.append(toggleRow);

      const theirs = document.createElement("p");
      theirs.className = "small muted";
      theirs.style.margin = "8px 0 0";
      theirs.textContent = connection.theyShare
        ? "They share their figures with you — pick them in “Viewing” above."
        : "They have not shared their figures with you.";
      card.append(theirs);
    }

    const remove = Object.assign(document.createElement("button"), {
      className: "btn btn-quiet small", textContent: "Remove",
    });
    remove.style.cssText = "margin-top:12px;padding:6px 12px";
    remove.onclick = async () => {
      if (!await confirmAction(
        `Remove your connection with ${connection.otherName}? Neither of you will see the other's figures.`,
        "Remove")) return;
      await db.removeConnection(connection.id);
      toast("Connection removed", connection.otherName);
      await loadConnections();
    };
    card.append(remove);
    host.append(card);
  }
}

async function respond(connection, accept) {
  await db.respondToInvite(connection.id, accept);
  toast(accept ? "Invitation accepted" : "Invitation declined", connection.otherName);
  await loadConnections();
}

async function share(connection, on) {
  await db.setSharing(connection, on);
  toast(on ? "Sharing on" : "Sharing off",
    on ? `${connection.otherName} can now see your figures.`
       : `${connection.otherName} can no longer see your figures.`);
  await loadConnections();
}

function renderViewingPicker() {
  const shared = state.connections.filter((c) => c.status === "accepted" && c.theyShare);
  const wrap = $("viewing-wrap");
  wrap.hidden = shared.length === 0;
  if (!shared.length) {
    state.viewingId = state.profile.id;
    return;
  }
  const select = $("viewing");
  select.replaceChildren();
  select.append(new Option("My budget", state.profile.id));
  for (const connection of shared) {
    select.append(new Option(`${connection.otherName} (${connection.relationship})`, connection.otherId));
  }
  select.value = state.viewingId ?? state.profile.id;
}

/* ---------------------------------------------------------------- loading */

async function loadLedgers() {
  const owner = state.viewingId ?? state.profile.id;
  const [bills, income] = await Promise.all([
    db.listRecords("bills", owner),
    db.listRecords("income", owner),
  ]);
  state.bills = bills;
  state.income = income;

  const whose = isOwnLedger()
    ? "monthly budget"
    : `viewing ${state.connections.find((c) => c.otherId === owner)?.otherName ?? "shared"}`;
  $("whose").textContent = whose;
  $("hero-label").textContent = isOwnLedger()
    ? "Left over this month" : "Their month, left over";
  renderBudget();
}

async function loadConnections() {
  state.connections = await db.listConnections();
  renderConnections();
  renderViewingPicker();
}

/* ------------------------------------------------------------------ views */

function showView(view) {
  state.view = view;
  for (const name of ["budget", "people", "account"]) {
    $(`view-${name}`).classList.toggle("hidden", name !== view);
  }
  for (const button of $("view-tabs").children) {
    button.setAttribute("aria-selected", String(button.dataset.view === view));
  }
}

/* ------------------------------------------------------------------ wiring */

function wireApp() {
  $("theme-toggle").onclick = () =>
    setTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");

  for (const button of $("view-tabs").children) {
    button.onclick = () => showView(button.dataset.view);
  }

  for (const button of $("kind-tabs").children) {
    button.onclick = () => {
      state.kind = button.dataset.kind;
      state.editingId = null;
      state.search = "";
      state.sort[state.kind] = SPEC[state.kind].dayField;
      state.reverse = false;
      $("search").value = "";
      for (const other of $("kind-tabs").children) {
        other.setAttribute("aria-selected", String(other.dataset.kind === state.kind));
      }
      renderEditorFields();
      resetEditor();
    };
  }

  $("search").oninput = (event) => { state.search = event.target.value; renderLedger(); };
  $("clear-btn").onclick = resetEditor;

  $("record-form").onsubmit = async (event) => {
    event.preventDefault();
    $("record-error").textContent = "";
    try {
      await db.saveRecord(state.kind, editorValues(), state.editingId);
      const created = !state.editingId;
      resetEditor();
      await loadLedgers();
      toast(created ? "Saved" : "Updated",
        `Your ${SPEC[state.kind].noun} list is up to date.`, "good");
    } catch (error) {
      $("record-error").textContent = error.message;
    }
  };

  $("delete-btn").onclick = async () => {
    const record = records().find((r) => r.id === state.editingId);
    if (!record) {
      toast("Nothing selected", `Pick a ${SPEC[state.kind].noun} in the table first.`, "critical");
      return;
    }
    if (!await confirmAction(`Delete “${record.name}”? This cannot be undone.`)) return;
    await db.deleteRecord(state.kind, record.id);
    resetEditor();
    await loadLedgers();
    toast("Deleted", record.name);
  };

  $("viewing").onchange = async (event) => {
    state.viewingId = event.target.value;
    state.editingId = null;
    await loadLedgers();
  };

  $("invite-form").onsubmit = async (event) => {
    event.preventDefault();
    $("invite-error").textContent = "";
    try {
      await db.inviteByEmail($("invite-email").value, $("invite-relationship").value);
      $("invite-email").value = "";
      $("invite-relationship").value = "";
      toast("Invitation sent", "They will see it next time they sign in.", "good");
      await loadConnections();
    } catch (error) {
      $("invite-error").textContent = error.message;
    }
  };

  $("profile-form").onsubmit = async (event) => {
    event.preventDefault();
    $("profile-error").textContent = "";
    try {
      await db.updateDisplayName($("profile-name").value);
      state.profile.display_name = $("profile-name").value.trim();
      toast("Saved", "Your display name is updated.", "good");
      await loadConnections();
    } catch (error) {
      $("profile-error").textContent = error.message;
    }
  };

  $("signout-btn").onclick = async () => {
    await db.signOut();
    location.reload();
  };

  $("export-bills").onclick = () => runExport("bills");
  $("export-income").onclick = () => runExport("income");

  $("export-mine").onclick = async () => {
    $("mine-error").textContent = "";
    try {
      const mine = await db.exportMyData();
      const stamp = new Date().toISOString().slice(0, 10);
      db.downloadCsv(`my-bills-${stamp}.csv`,
        db.toCsv(mine.bills, ["name", "amount", "category", "due_day"]));
      db.downloadCsv(`my-income-${stamp}.csv`,
        db.toCsv(mine.income, ["name", "amount", "pay_day"]));
      toast("Downloaded", "Two files: your bills and your income.", "good");
    } catch (error) {
      $("mine-error").textContent = error.message;
    }
  };
}

async function runExport(which) {
  $("export-error").textContent = "";
  try {
    const data = await db.exportAnonymised();
    const stamp = new Date().toISOString().slice(0, 10);
    if (which === "bills") {
      db.downloadCsv(`financechart-bills-${stamp}.csv`,
        db.toCsv(data.bills, ["household", "category", "amount", "due_day", "created_month"]));
    } else {
      db.downloadCsv(`financechart-income-${stamp}.csv`,
        db.toCsv(data.income, ["household", "amount", "pay_day", "created_month"]));
    }
    toast("Export ready", "Anonymised — no names or emails included.", "good");
  } catch (error) {
    $("export-error").textContent = error.message;
  }
}

/* ------------------------------------------------------------------ start */

async function start() {
  const profile = await db.currentProfile();
  if (!profile) {
    $("auth").classList.remove("hidden");
    $("app").classList.add("hidden");
    renderAuthMode();
    return;
  }

  state.profile = profile;
  state.viewingId = profile.id;
  $("auth").classList.add("hidden");
  $("app").classList.remove("hidden");

  $("account-email").textContent = profile.email ?? "";
  $("profile-name").value = profile.display_name ?? "";
  $("admin-pane").classList.toggle("hidden", !profile.is_admin);

  $("relationships").replaceChildren();
  for (const label of db.RELATIONSHIPS) $("relationships").append(new Option(label));

  renderEditorFields();
  resetEditor();
  await loadConnections();
  await loadLedgers();
}

function boot() {
  setTheme(localStorage.getItem("fc-theme") || "dark");

  if (!isConfigured()) {
    $("setup").classList.remove("hidden");
    return;
  }
  wireAuth();
  wireApp();
  start().catch((error) => {
    $("auth").classList.remove("hidden");
    $("auth-error").textContent = error.message;
    renderAuthMode();
  });
}

boot();
