/*
 * charts.js
 * ---------
 * The data marks, drawn as inline SVG.
 *
 * Same rules as the desktop app. Magnitude uses one hue's ramp, so the bars
 * read as a single measure. Identity uses fixed categorical slots, so a
 * category keeps its colour when the data changes. Past eight categories the
 * tail folds into "Other" rather than inventing a ninth hue that nothing could
 * reliably tell apart.
 */

import { byCategory, money, monthlyEquivalent } from "./data.js";

const MAX_BARS = 11;
const MAX_SLOTS = 7;
const BAR_HEIGHT = 15;
const BAR_PITCH = 30;

const SERIES = Array.from({ length: 8 }, (_, i) => `var(--series-${i + 1})`);
// The readable ink for each fill, measured rather than assumed — on the light
// palette several series are dark enough that black text would fail.
const SERIES_INK = Array.from({ length: 8 }, (_, i) => `var(--series-${i + 1}-ink)`);

const svgEl = (name, attrs = {}) => {
  const node = document.createElementNS("http://www.w3.org/2000/svg", name);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  return node;
};

/** Category totals, with the tail beyond the slot ceiling folded into "Other". */
export function foldedCategories(bills) {
  const shares = byCategory(bills);
  if (shares.length <= MAX_SLOTS + 1) return shares;

  const head = shares.slice(0, MAX_SLOTS);
  const tail = shares.slice(MAX_SLOTS).reduce((sum, [, value]) => sum + value, 0);
  const existing = head.find(([name]) => name === "Other");
  if (existing) existing[1] += tail;
  else head.push(["Other", tail]);
  // Absorbing the tail can push "Other" above a category that outranked it,
  // so re-sort — otherwise the ribbon runs 45%, 15%, 16% and looks broken.
  return head.sort((a, b) => b[1] - a[1]);
}

export function categoryColour(bills, category) {
  const index = foldedCategories(bills).findIndex(([name]) => name === category);
  return index === -1 ? "var(--ink-muted)" : SERIES[index % SERIES.length];
}

/* ------------------------------------------------------------------ bars */

/**
 * One bar per bill, largest first. Returns how many were drawn so the caller
 * can say when it is showing only the top of the list — a silent top-N reads
 * as "this is everything" when it is not.
 */
export function renderBars(host, bills) {
  host.replaceChildren();
  if (!bills.length) {
    host.innerHTML =
      '<p class="empty"><strong>Nothing to chart yet</strong>Add a bill to see the breakdown</p>';
    return 0;
  }

  // Bars compare what each bill costs per month, so a yearly premium sits
  // beside a monthly one honestly instead of dwarfing it twelvefold.
  const shown = [...bills]
    .map((bill) => ({ ...bill, monthly: monthlyEquivalent(bill) }))
    .sort((a, b) => b.monthly - a.monthly)
    .slice(0, MAX_BARS);
  const largest = shown[0].monthly;
  const height = shown.length * BAR_PITCH;

  const svg = svgEl("svg", {
    viewBox: `0 0 1000 ${height}`,
    width: "100%",
    height,
    preserveAspectRatio: "none",
    role: "img",
    "aria-label": `Bills by amount, ${shown.length} shown`,
  });

  const gutter = 190;
  const valueSpace = 130;
  const span = 1000 - gutter - valueSpace;

  shown.forEach((bill, index) => {
    const y = index * BAR_PITCH;
    const share = bill.monthly / largest;
    const width = Math.max(4, span * share);
    // Step a single hue by the value's share of the largest, easing the low
    // end so small bars stay clear of the surface.
    const step = Math.round(Math.pow(share, 0.62) * 8) + 1;

    const label = svgEl("text", {
      x: gutter - 14, y: y + BAR_HEIGHT / 2 + 1,
      "text-anchor": "end", "dominant-baseline": "middle",
      class: "bar-label",
    });
    label.textContent = bill.name;
    svg.append(label);

    svg.append(svgEl("rect", {
      x: gutter, y, width, height: BAR_HEIGHT,
      rx: BAR_HEIGHT / 2, ry: BAR_HEIGHT / 2,
      fill: `var(--ramp-${step})`,
    }));

    const value = svgEl("text", {
      x: gutter + width + 12, y: y + BAR_HEIGHT / 2 + 1,
      "dominant-baseline": "middle", class: "bar-value",
    });
    value.textContent = money(bill.monthly);
    svg.append(value);
  });

  host.append(svg);
  return shown.length;
}

/* ---------------------------------------------------------------- ribbon */

/** A stacked bar of the monthly total, split by category, plus its legend. */
export function renderRibbon(barHost, legendHost, bills) {
  barHost.replaceChildren();
  legendHost.replaceChildren();
  if (!bills.length) return;

  const shares = foldedCategories(bills);
  const grand = shares.reduce((sum, [, value]) => sum + value, 0);
  const gap = 3;

  const svg = svgEl("svg", {
    viewBox: "0 0 1000 30", width: "100%", height: 30,
    preserveAspectRatio: "none", role: "img",
    "aria-label": "Share of monthly bills by category",
  });

  let cursor = 0;
  shares.forEach(([name, amount], index) => {
    const width = (amount / grand) * 1000;
    const x = cursor + (index ? gap / 2 : 0);
    const w = Math.max(2, width - (index ? gap / 2 : 0) - (index < shares.length - 1 ? gap / 2 : 0));
    const first = index === 0;
    const last = index === shares.length - 1;

    svg.append(svgEl("rect", {
      x, y: 0, width: w, height: 30,
      rx: first || last ? 15 : 5, ry: first || last ? 15 : 5,
      fill: SERIES[index % SERIES.length],
    }));

    const percent = amount / grand;
    if (w > 62) {
      const text = svgEl("text", {
        x: x + w / 2, y: 16, "text-anchor": "middle",
        "dominant-baseline": "middle", class: "ribbon-label",
        fill: SERIES_INK[index % SERIES_INK.length],
      });
      text.textContent = `${Math.round(percent * 100)}%`;
      svg.append(text);
    }
    cursor += width;
  });
  barHost.append(svg);

  // The legend is always present, so identity is never carried by colour alone.
  for (const [index, [name, amount]] of shares.entries()) {
    const row = document.createElement("div");
    const swatch = document.createElement("span");
    swatch.className = "dot";
    swatch.style.background = SERIES[index % SERIES.length];
    row.append(swatch, document.createTextNode(name + " "));
    const amt = document.createElement("span");
    amt.className = "amt";
    amt.textContent = "$" + Math.round(amount).toLocaleString("en-US");
    row.append(amt);
    legendHost.append(row);
  }
}

/* ----------------------------------------------------------------- meter */

/**
 * How much of the month's income the bills consume.
 *
 * A single ratio against a limit is a meter, not a chart. When bills exceed
 * income the overspend is drawn past the end of the track, so the bar can
 * never silently cap at 100%.
 */
export function renderMeter(fill, over, incoming, outgoing) {
  if (incoming <= 0) {
    fill.style.width = "0%";
    over.style.display = "none";
    return;
  }
  const span = Math.max(incoming, outgoing);
  fill.style.width = `${(Math.min(outgoing, incoming) / span) * 100}%`;

  if (outgoing > incoming) {
    over.style.display = "block";
    over.style.left = `${(incoming / span) * 100}%`;
    over.style.width = `${((outgoing - incoming) / span) * 100}%`;
  } else {
    over.style.display = "none";
  }
}

/**
 * The words beside the meter, and which tone should colour them. The status
 * colour never carries the meaning alone, so the overspend case is spelled out.
 */
export function meterCaption(incoming, outgoing) {
  if (incoming <= 0) {
    return { text: "Add your income to see what's left over", tone: "muted" };
  }
  const remaining = incoming - outgoing;
  const share = Math.round((outgoing / incoming) * 100);
  if (remaining < 0) {
    return {
      text: `Bills exceed income by ${money(-remaining)} · ${share}% of ${money(incoming)}`,
      tone: "critical",
    };
  }
  return {
    text: `${money(remaining)} left over · bills take ${share}% of ${money(incoming)}`,
    tone: "normal",
  };
}
