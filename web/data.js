/*
 * data.js
 * -------
 * Everything that talks to Supabase, plus the validation rules.
 *
 * Validation lives here rather than in the views, so the rules are stated once
 * and the interface only has to render the resulting message. That mirrors the
 * desktop app's models.py.
 *
 * Note what this file does *not* do: it never decides who may read what. Every
 * query below is sent as the signed-in user, and Postgres applies the row-level
 * security policies. A bug in this file cannot leak somebody else's finances.
 */

import { createClient } from "https://esm.sh/@supabase/supabase-js@2.45.4";
import { SUPABASE_URL, SUPABASE_ANON_KEY } from "./config.js";

export const supabase = createClient(SUPABASE_URL, SUPABASE_ANON_KEY);

export const CATEGORIES = [
  "Housing", "Utilities", "Auto", "Insurance", "Subscriptions",
  "Debt", "Groceries", "Transportation", "Healthcare", "Other",
];

/** Suggested relationship labels. Free text is allowed — this is only a list. */
export const RELATIONSHIPS = [
  "Partner", "Spouse", "Wife", "Husband", "Fiancée", "Fiancé",
  "Girlfriend", "Boyfriend", "Parent", "Child", "Sibling",
  "Housemate", "Roommate", "Friend", "Loved one", "Other",
];

export class ValidationError extends Error {}

/* ----------------------------------------------------------- validation */

export function parseName(value, noun) {
  const name = String(value ?? "").trim();
  if (!name) throw new ValidationError(`Enter a name for the ${noun}.`);
  if (name.length > 80) throw new ValidationError("That name is too long.");
  return name;
}

export function parseAmount(value) {
  const raw = String(value ?? "").trim().replace(/^\$/, "").replace(/,/g, "");
  const amount = Number(raw);
  if (raw === "" || !Number.isFinite(amount)) {
    throw new ValidationError(`"${value}" is not a valid amount.`);
  }
  if (amount <= 0) throw new ValidationError("Amount must be greater than zero.");
  return Math.round(amount * 100) / 100;
}

export function parseDay(value, label) {
  const day = Number(String(value ?? "").trim());
  if (!Number.isInteger(day)) {
    throw new ValidationError(`"${value}" is not a valid day of the month.`);
  }
  if (day < 1 || day > 31) {
    throw new ValidationError(`${label} must be between 1 and 31.`);
  }
  return day;
}

/* -------------------------------------------------------------- helpers */

export const money = (value) =>
  (value < 0 ? "-$" : "$") +
  Math.abs(value).toLocaleString("en-US", {
    minimumFractionDigits: 2, maximumFractionDigits: 2,
  });

export const ordinal = (day) => {
  if (day % 100 >= 11 && day % 100 <= 13) return `${day}th`;
  return day + ({ 1: "st", 2: "nd", 3: "rd" }[day % 10] || "th");
};

/** Whole days until the next occurrence of `day`, clamped to short months. */
export function daysUntil(day, today = new Date()) {
  const clamp = (year, month) =>
    new Date(year, month, Math.min(day, new Date(year, month + 1, 0).getDate()));
  const start = new Date(today.getFullYear(), today.getMonth(), today.getDate());
  let target = clamp(start.getFullYear(), start.getMonth());
  if (target < start) target = clamp(start.getFullYear(), start.getMonth() + 1);
  return Math.round((target - start) / 86400000);
}

/* -------------------------------------------------------- pay schedules */

// Two menus, one mechanism. Pay arrives on cycles measured in weeks; bills
// more often land on cycles measured in months. `every` says which unit the
// cycle steps in, so one date routine serves both.
export const INCOME_FREQUENCIES = [
  { key: "monthly",     label: "Monthly",       perYear: 12, every: null },
  { key: "semimonthly", label: "Twice a month", perYear: 24, every: null },
  { key: "biweekly",    label: "Every 2 weeks", perYear: 26, every: { days: 14 } },
  { key: "weekly",      label: "Weekly",        perYear: 52, every: { days: 7 } },
];

export const BILL_FREQUENCIES = [
  { key: "monthly",    label: "Monthly",       perYear: 12, every: null },
  { key: "weekly",     label: "Weekly",        perYear: 52, every: { days: 7 } },
  { key: "biweekly",   label: "Every 2 weeks", perYear: 26, every: { days: 14 } },
  { key: "quarterly",  label: "Every 3 months", perYear: 4, every: { months: 3 } },
  { key: "semiannual", label: "Every 6 months", perYear: 2, every: { months: 6 } },
  { key: "annual",     label: "Yearly",         perYear: 1, every: { months: 12 } },
];

/** Kept for compatibility with anything still importing the old name. */
export const FREQUENCIES = INCOME_FREQUENCIES;

const ALL = [...INCOME_FREQUENCIES, ...BILL_FREQUENCIES];
const SPECS = Object.fromEntries(ALL.map((f) => [f.key, f]));
const PER_YEAR = Object.fromEntries(ALL.map((f) => [f.key, f.perYear]));

/** Does this frequency need an anchor date, or a day of the month? */
export const needsAnchor = (frequency) => Boolean(SPECS[frequency]?.every);

/** A "YYYY-MM-DD" string as a local date, avoiding the UTC off-by-one. */
export function parseDateOnly(text) {
  const [year, month, day] = String(text).split("-").map(Number);
  return new Date(year, month - 1, day);
}

export const toDateOnly = (date) =>
  `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-` +
  `${String(date.getDate()).padStart(2, "0")}`;

const midnight = (date) =>
  new Date(date.getFullYear(), date.getMonth(), date.getDate());

/**
 * What one income source is worth per month.
 *
 * Every frequency is normalised through a year, which is the only way the
 * figures stay honest: paid every two weeks you receive 26 packets, so the
 * monthly equivalent is amount x 26 / 12 — noticeably more than twice the
 * packet. Some months genuinely carry three paydays; averaging over the year
 * is what makes a monthly budget meaningful.
 */
export function monthlyEquivalent(row) {
  const perYear = PER_YEAR[row.frequency] ?? 12;
  return (Number(row.amount) * perYear) / 12;
}

/**
 * The next date a record falls due, whatever its schedule.
 *
 * Cycle-based rows count forward from their anchor, so the anchor may be a
 * past date (the last payslip) or a future one (a renewal you already know) —
 * both land on the same answer.
 */
export function nextDate(row, today = new Date()) {
  const start = midnight(today);
  const spec = SPECS[row.frequency ?? "monthly"];

  if (spec?.every) {
    if (!row.anchor_date) return null;
    const anchor = parseDateOnly(row.anchor_date);
    if (anchor >= start) return anchor;

    if (spec.every.days) {
      const elapsed = Math.round((start - anchor) / 86400000);
      const cycles = Math.ceil(elapsed / spec.every.days);
      return new Date(anchor.getFullYear(), anchor.getMonth(),
                      anchor.getDate() + cycles * spec.every.days);
    }

    // Month-based cycles step whole months, clamping to the month's last day
    // so a bill anchored on the 31st still resolves in February.
    const step = spec.every.months;
    const monthsApart =
      (start.getFullYear() - anchor.getFullYear()) * 12 +
      (start.getMonth() - anchor.getMonth());
    let cycles = Math.max(0, Math.floor(monthsApart / step));
    for (let guard = 0; guard < 240; guard += 1) {
      const target = addMonthsClamped(anchor, cycles * step);
      if (target >= start) return target;
      cycles += 1;
    }
    return null;
  }

  const days = row.frequency === "semimonthly" && row.pay_day_2
    ? [row.pay_day, row.pay_day_2]
    : [row.pay_day ?? row.due_day];
  const candidates = days
    .filter(Boolean)
    .map((day) => {
      const offset = daysUntil(day, start);
      return new Date(start.getFullYear(), start.getMonth(), start.getDate() + offset);
    });
  return candidates.sort((a, b) => a - b)[0] ?? null;
}

function addMonthsClamped(date, months) {
  const year = date.getFullYear();
  const month = date.getMonth() + months;
  const lastDay = new Date(year, month + 1, 0).getDate();
  return new Date(year, month, Math.min(date.getDate(), lastDay));
}

/** Kept for compatibility; income used to have its own date routine. */
export const nextPayDate = nextDate;

/** Plain-English schedule, e.g. "Every 2 weeks · next Thu 20 Aug". */
export function describeSchedule(row, today = new Date()) {
  const frequency = row.frequency ?? "monthly";
  const label = SPECS[frequency]?.label ?? "Monthly";
  const day = row.pay_day ?? row.due_day;

  if (frequency === "monthly") return `Monthly · ${ordinal(day)}`;
  if (frequency === "semimonthly" && row.pay_day_2) {
    const [first, second] = [row.pay_day, row.pay_day_2].sort((a, b) => a - b);
    return `Twice a month · ${ordinal(first)} and ${ordinal(second)}`;
  }

  const next = nextDate(row, today);
  if (!next) return label;
  const when = next.toLocaleDateString(undefined,
    { weekday: "short", day: "numeric", month: "short" });
  return `${label} · next ${when}`;
}

export const total = (rows) => rows.reduce((sum, row) => sum + Number(row.amount), 0);

/** Monthly total, with every row normalised through its own frequency. */
export const monthlyTotal = (rows) =>
  rows.reduce((sum, row) => sum + monthlyEquivalent(row), 0);

/** Kept for compatibility; the income-only name predates bills having schedules. */
export const monthlyIncome = monthlyTotal;

/**
 * Category totals, in monthly equivalents.
 *
 * Raw amounts would be misleading here: a yearly insurance premium alongside
 * a monthly phone bill would dominate the split by a factor of twelve without
 * actually costing that much each month.
 */
export function byCategory(bills) {
  const totals = new Map();
  for (const bill of bills) {
    totals.set(bill.category,
               (totals.get(bill.category) ?? 0) + monthlyEquivalent(bill));
  }
  return [...totals.entries()].sort((a, b) => b[1] - a[1]);
}

/** Whichever row falls due soonest, across whatever schedules they keep. */
export function nextDue(rows) {
  const dated = rows
    .map((row) => ({ row, at: nextDate(row) }))
    .filter((entry) => entry.at);
  if (!dated.length) return null;
  dated.sort((a, b) => a.at - b.at || monthlyEquivalent(b.row) - monthlyEquivalent(a.row));
  return dated[0];
}

/* ----------------------------------------------------------------- auth */

export async function signUp(email, password, displayName) {
  const { data, error } = await supabase.auth.signUp({
    email: email.trim(),
    password,
    options: { data: { display_name: displayName.trim() } },
  });
  if (error) throw new ValidationError(error.message);
  return data;
}

export async function signIn(email, password) {
  const { data, error } = await supabase.auth.signInWithPassword({
    email: email.trim(), password,
  });
  if (error) throw new ValidationError(error.message);
  return data;
}

export const signOut = () => supabase.auth.signOut();

export async function sendPasswordReset(email) {
  const { error } = await supabase.auth.resetPasswordForEmail(email.trim(), {
    redirectTo: window.location.href.split("#")[0],
  });
  if (error) throw new ValidationError(error.message);
}

export async function currentProfile() {
  const { data: auth } = await supabase.auth.getUser();
  if (!auth?.user) return null;
  const { data, error } = await supabase
    .from("profiles")
    .select("id, display_name, is_admin")
    .eq("id", auth.user.id)
    .single();
  if (error) return { id: auth.user.id, display_name: "", is_admin: false };
  return { ...data, email: auth.user.email };
}

export async function updateDisplayName(name) {
  const { data: auth } = await supabase.auth.getUser();
  const { error } = await supabase
    .from("profiles")
    .update({ display_name: name.trim() })
    .eq("id", auth.user.id);
  if (error) throw new ValidationError(error.message);
}

/* ------------------------------------------------------- bills / income */

const TABLES = { bills: "bills", income: "income" };

/** Validate and normalise the anchor date a cycle-based schedule counts from. */
function parseAnchor(value, prompt) {
  const anchor = String(value ?? "").trim();
  if (!anchor) throw new ValidationError(prompt);
  const parsed = parseDateOnly(anchor);
  if (Number.isNaN(parsed.getTime())) {
    throw new ValidationError(`"${anchor}" is not a date I can read.`);
  }
  return toDateOnly(parsed);
}

/** Turn the income form's values into a row, per the chosen frequency. */
function buildIncome(values) {
  const frequency = values.frequency || "monthly";
  if (!PER_YEAR[frequency]) {
    throw new ValidationError(`${frequency} is not a pay frequency I know.`);
  }

  const row = {
    name: parseName(values.name, "income source"),
    amount: parseAmount(values.amount),
    frequency,
    pay_day: 1,
    pay_day_2: null,
    anchor_date: null,
  };

  if (frequency === "monthly") {
    row.pay_day = parseDay(values.pay_day, "Pay day");
  } else if (frequency === "semimonthly") {
    row.pay_day = parseDay(values.pay_day, "First pay day");
    row.pay_day_2 = parseDay(values.pay_day_2, "Second pay day");
    if (row.pay_day === row.pay_day_2) {
      throw new ValidationError("The two pay days need to be different.");
    }
  } else {
    row.anchor_date = parseAnchor(values.anchor_date,
                                  "Pick the date you were last paid.");
  }
  return row;
}

/** Turn the bill form's values into a row, per the chosen frequency. */
function buildBill(values) {
  const frequency = values.frequency || "monthly";
  if (!PER_YEAR[frequency]) {
    throw new ValidationError(`${frequency} is not a billing cycle I know.`);
  }

  const row = {
    name: parseName(values.name, "bill"),
    amount: parseAmount(values.amount),
    category: values.category || "Other",
    frequency,
    due_day: 1,
    anchor_date: null,
  };

  if (frequency === "monthly") {
    row.due_day = parseDay(values.due_day, "Due day");
  } else {
    row.anchor_date = parseAnchor(values.anchor_date,
                                  "Pick the date this is next due.");
  }
  return row;
}

export async function listRecords(kind, userId) {
  const dayField = kind === "bills" ? "due_day" : "pay_day";
  const { data, error } = await supabase
    .from(TABLES[kind])
    .select("*")
    .eq("user_id", userId)
    .order(dayField, { ascending: true });
  if (error) throw new ValidationError(error.message);
  return data ?? [];
}

export async function saveRecord(kind, values, id = null) {
  const { data: auth } = await supabase.auth.getUser();
  const payload = kind === "bills" ? buildBill(values) : buildIncome(values);

  const query = id
    ? supabase.from(TABLES[kind]).update(payload).eq("id", id)
    : supabase.from(TABLES[kind]).insert({ ...payload, user_id: auth.user.id });

  const { error } = await query;
  if (error) throw new ValidationError(error.message);
}

export async function deleteRecord(kind, id) {
  const { error } = await supabase.from(TABLES[kind]).delete().eq("id", id);
  if (error) throw new ValidationError(error.message);
}

/* ---------------------------------------------------------- connections */

export async function listConnections() {
  const { data, error } = await supabase
    .from("connections")
    .select("*")
    .order("created_at", { ascending: false });
  if (error) throw new ValidationError(error.message);

  const rows = data ?? [];
  const ids = [...new Set(rows.flatMap((r) => [r.requester_id, r.addressee_id]))];
  if (!ids.length) return [];

  const { data: people } = await supabase
    .from("profiles")
    .select("id, display_name")
    .in("id", ids);
  const names = new Map((people ?? []).map((p) => [p.id, p.display_name]));

  const { data: auth } = await supabase.auth.getUser();
  const me = auth.user.id;

  return rows.map((row) => {
    const outgoing = row.requester_id === me;
    const otherId = outgoing ? row.addressee_id : row.requester_id;
    return {
      ...row,
      outgoing,
      otherId,
      otherName: names.get(otherId) || "Someone",
      // What I share with them, and what they share with me.
      iShare: outgoing ? row.requester_shares : row.addressee_shares,
      theyShare: outgoing ? row.addressee_shares : row.requester_shares,
    };
  });
}

export async function inviteByEmail(email, relationship) {
  const { data: auth } = await supabase.auth.getUser();
  const { data: otherId, error: lookupError } = await supabase.rpc(
    "find_user_by_email",
    { lookup_email: email }
  );
  if (lookupError) throw new ValidationError(lookupError.message);
  if (!otherId) {
    throw new ValidationError(
      "No account uses that email yet. Ask them to sign up first."
    );
  }
  if (otherId === auth.user.id) {
    throw new ValidationError("That is your own account.");
  }

  // The unique constraint only catches a repeat in the same direction. Two
  // people who invite each other before either accepts would otherwise end up
  // with two rows for one relationship, so check both directions first.
  const { data: existing } = await supabase
    .from("connections")
    .select("id, status, requester_id")
    .or(`and(requester_id.eq.${auth.user.id},addressee_id.eq.${otherId}),` +
        `and(requester_id.eq.${otherId},addressee_id.eq.${auth.user.id})`)
    .maybeSingle();

  if (existing) {
    if (existing.status === "pending" && existing.requester_id === otherId) {
      throw new ValidationError(
        "They have already invited you — accept their invitation instead."
      );
    }
    throw new ValidationError("You already have a connection with that person.");
  }

  const { error } = await supabase.from("connections").insert({
    requester_id: auth.user.id,
    addressee_id: otherId,
    relationship: relationship || "Partner",
  });
  if (error) {
    throw new ValidationError(
      error.code === "23505"
        ? "You already have a connection with that person."
        : error.message
    );
  }
}

export async function respondToInvite(id, accept) {
  const { error } = await supabase
    .from("connections")
    .update({ status: accept ? "accepted" : "declined" })
    .eq("id", id);
  if (error) throw new ValidationError(error.message);
}

/** Turn my own sharing on or off for one connection. */
export async function setSharing(connection, share) {
  const column = connection.outgoing ? "requester_shares" : "addressee_shares";
  const { error } = await supabase
    .from("connections")
    .update({ [column]: share })
    .eq("id", connection.id);
  if (error) throw new ValidationError(error.message);
}

export async function removeConnection(id) {
  const { error } = await supabase.from("connections").delete().eq("id", id);
  if (error) throw new ValidationError(error.message);
}

/* --------------------------------------------------------------- export */

/**
 * Your own data, in full.
 *
 * This is not the anonymised reporting export — it is your records, so it
 * includes the descriptions you typed. Everyone gets this; the privacy note
 * promises you can always take your data with you.
 */
export async function exportMyData() {
  const { data: auth } = await supabase.auth.getUser();
  const [bills, income] = await Promise.all([
    listRecords("bills", auth.user.id),
    listRecords("income", auth.user.id),
  ]);
  return { bills, income };
}

/**
 * Anonymised export for reporting.
 *
 * The server returns amounts, categories and dates keyed by an opaque
 * household id — never a name, an email, or the free-text description of a
 * bill, which is often identifying on its own. The admin check happens in
 * Postgres; calling this without the flag raises.
 */
export async function exportAnonymised() {
  const [bills, income] = await Promise.all([
    supabase.rpc("export_bills"),
    supabase.rpc("export_income"),
  ]);
  if (bills.error) throw new ValidationError(bills.error.message);
  if (income.error) throw new ValidationError(income.error.message);
  return { bills: bills.data ?? [], income: income.data ?? [] };
}

export function toCsv(rows, columns) {
  const escape = (value) => {
    const text = value === null || value === undefined ? "" : String(value);
    return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
  };
  return [
    columns.join(","),
    ...rows.map((row) => columns.map((c) => escape(row[c])).join(",")),
  ].join("\n");
}

export function downloadCsv(filename, csv) {
  const url = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
  const link = Object.assign(document.createElement("a"), { href: url, download: filename });
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}
