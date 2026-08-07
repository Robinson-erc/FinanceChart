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

export const FREQUENCIES = [
  { key: "monthly",     label: "Monthly",           perYear: 12 },
  { key: "semimonthly", label: "Twice a month",     perYear: 24 },
  { key: "biweekly",    label: "Every 2 weeks",     perYear: 26 },
  { key: "weekly",      label: "Weekly",            perYear: 52 },
];

const PER_YEAR = Object.fromEntries(FREQUENCIES.map((f) => [f.key, f.perYear]));

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

/** The next date this source pays out, whatever its schedule. */
export function nextPayDate(row, today = new Date()) {
  const start = midnight(today);

  if (row.frequency === "biweekly" || row.frequency === "weekly") {
    if (!row.anchor_date) return null;
    const step = row.frequency === "weekly" ? 7 : 14;
    const anchor = parseDateOnly(row.anchor_date);
    if (anchor >= start) return anchor;
    const elapsed = Math.round((start - anchor) / 86400000);
    const cycles = Math.ceil(elapsed / step);
    return new Date(anchor.getFullYear(), anchor.getMonth(),
                    anchor.getDate() + cycles * step);
  }

  const days = row.frequency === "semimonthly" && row.pay_day_2
    ? [row.pay_day, row.pay_day_2]
    : [row.pay_day];
  const candidates = days
    .filter(Boolean)
    .map((day) => {
      const offset = daysUntil(day, start);
      return new Date(start.getFullYear(), start.getMonth(), start.getDate() + offset);
    });
  return candidates.sort((a, b) => a - b)[0] ?? null;
}

/** Plain-English schedule, e.g. "Every 2 weeks · next Thu 20 Aug". */
export function describeSchedule(row, today = new Date()) {
  const label = FREQUENCIES.find((f) => f.key === row.frequency)?.label ?? "Monthly";
  const next = nextPayDate(row, today);
  if (!next) return label;
  if (row.frequency === "monthly") return `Monthly · ${ordinal(row.pay_day)}`;
  if (row.frequency === "semimonthly" && row.pay_day_2) {
    const [first, second] = [row.pay_day, row.pay_day_2].sort((a, b) => a - b);
    return `Twice a month · ${ordinal(first)} and ${ordinal(second)}`;
  }
  const when = next.toLocaleDateString(undefined,
    { weekday: "short", day: "numeric", month: "short" });
  return `${label} · next ${when}`;
}

export const total = (rows) => rows.reduce((sum, row) => sum + Number(row.amount), 0);

/** Monthly total of income, with each source normalised to its own frequency. */
export const monthlyIncome = (rows) =>
  rows.reduce((sum, row) => sum + monthlyEquivalent(row), 0);

export function byCategory(bills) {
  const totals = new Map();
  for (const bill of bills) {
    totals.set(bill.category, (totals.get(bill.category) ?? 0) + Number(bill.amount));
  }
  return [...totals.entries()].sort((a, b) => b[1] - a[1]);
}

export function nextDue(rows, dayField) {
  if (!rows.length) return null;
  return [...rows].sort(
    (a, b) => daysUntil(a[dayField]) - daysUntil(b[dayField]) || b.amount - a.amount
  )[0];
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
    // Cycle-based pay is counted from one known date, so it is required.
    const anchor = String(values.anchor_date ?? "").trim();
    if (!anchor) {
      throw new ValidationError("Pick the date you were last paid.");
    }
    const parsed = parseDateOnly(anchor);
    if (Number.isNaN(parsed.getTime())) {
      throw new ValidationError(`"${anchor}" is not a date I can read.`);
    }
    row.anchor_date = toDateOnly(parsed);
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
  const payload =
    kind === "bills"
      ? {
          name: parseName(values.name, "bill"),
          amount: parseAmount(values.amount),
          category: values.category || "Other",
          due_day: parseDay(values.due_day, "Due day"),
        }
      : buildIncome(values);

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
