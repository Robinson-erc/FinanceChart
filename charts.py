"""
charts.py
---------
The three data marks, drawn directly with Pillow.

Each renderer is a pure function - size, data, palette in; an RGBA image out -
so they can be rendered and inspected without opening a window.

Drawing these by hand rather than through a plotting library is what lets the
marks carry gradients, glow and rounded data-ends while staying honest: colour
is still assigned by the job it does. Magnitude uses one hue's ramp; identity
uses the fixed categorical slots so a category keeps its colour as data
changes; overspend uses the reserved critical colour and always ships with
words beside it.
"""

from __future__ import annotations

from PIL import Image

import paint
from models import Bill, Income, by_category, total
from theme import TYPE, Palette

MAX_BARS = 11
MAX_CATEGORY_SLOTS = 7
BAR_HEIGHT = 15
BAR_GAP = 12
LABEL_GUTTER = 132
VALUE_GUTTER = 92
SEGMENT_GAP = 3
RIBBON_HEIGHT = 30


def _blank(size: tuple[int, int]) -> Image.Image:
    return Image.new("RGBA", size, (0, 0, 0, 0))


def bar_capacity(height: int) -> int:
    """How many bars fit in `height`.

    Exposed so the caller can say when the chart is showing only part of the
    list - a silent top-N reads as "this is everything" when it isn't.
    """
    return max(1, min(MAX_BARS, (height + BAR_GAP) // (BAR_HEIGHT + BAR_GAP)))


def _money(value: float) -> str:
    """Currency with the sign outside the symbol: -$920.73, not $-920.73."""
    return f"-${abs(value):,.2f}" if value < 0 else f"${value:,.2f}"


def _truncate(text: str, face, limit: int) -> str:
    if paint.measure(text, face)[0] <= limit:
        return text
    while text and paint.measure(text + "…", face)[0] > limit:
        text = text[:-1]
    return text + "…"


# ------------------------------------------------------------------- meter


def income_meter(
    size: tuple[int, int], bills: list[Bill], incomes: list[Income], palette: Palette
) -> Image.Image:
    """How much of the month's income the bills consume.

    A single ratio against a limit is a meter, not a chart: one track whose
    full width is income, filled to the share the bills take. Overspend is
    drawn past the track's end in the critical colour, so the bar can never
    silently cap at 100%.
    """
    image = _blank(size)
    width, height = size
    outgoing, incoming = total(bills), total(incomes)
    track_height = 14
    top = (height - track_height) // 2

    if incoming <= 0:
        paint.rounded_rect(image, (0, top, width, top + track_height),
                           track_height // 2, fill=palette.track)
        return image

    span = max(incoming, outgoing)
    scale = width / span
    filled = min(outgoing, incoming) * scale
    overspent = outgoing > incoming

    paint.rounded_rect(image, (0, top, incoming * scale, top + track_height),
                       track_height // 2, fill=palette.track)

    if filled > 2:
        # Glow first, so the filled portion reads as lit rather than painted.
        halo = paint.glow((int(filled), track_height), track_height // 2, 11,
                          palette.accent, 120)
        image.alpha_composite(halo, (-22, top - 22))
        bar = paint.linear_gradient((max(2, int(filled)), track_height),
                                    (palette.accent, palette.accent_soft), horizontal=True)
        image.paste(bar, (0, top),
                    paint.rounded_mask((max(2, int(filled)), track_height),
                                       track_height // 2))

    if overspent:
        start, end = incoming * scale, outgoing * scale
        over = paint.glow((int(end - start), track_height), track_height // 2, 10,
                          palette.critical, 150)
        image.alpha_composite(over, (int(start) - 20, top - 20))
        paint.rounded_rect(image, (start + SEGMENT_GAP, top, end, top + track_height),
                           track_height // 2, fill=paint.rgba(palette.critical))
    return image


def meter_caption(bills: list[Bill], incomes: list[Income]) -> tuple[str, str]:
    """The words beside the meter, and which palette role should colour them.

    The status colour never carries the meaning alone, so the overspend case is
    stated in words here rather than left to the red bar.
    """
    outgoing, incoming = total(bills), total(incomes)
    if incoming <= 0:
        return ("Add your income to see what's left over", "muted")
    remaining = incoming - outgoing
    share = outgoing / incoming
    if remaining < 0:
        return (f"Bills exceed income by {_money(-remaining)} · "
                f"{share:.0%} of {_money(incoming)}", "critical")
    return (f"{_money(remaining)} left over · bills take {share:.0%} "
            f"of {_money(incoming)}", "secondary")


# -------------------------------------------------------------------- bars


def bills_by_amount(
    size: tuple[int, int], bills: list[Bill], palette: Palette
) -> Image.Image:
    """One bar per bill, largest first.

    The job is comparing magnitude, so colour is sequential - a single hue's
    ramp, stepped by each bill's share of the largest - not a different hue per
    bill, which would imply the bills are unrelated categories.
    """
    image = _blank(size)
    width, height = size
    if not bills:
        paint.draw_text(image, (width // 2, height // 2),
                        "Add a bill to see the breakdown", TYPE["body"],
                        palette.ink_muted, anchor="mm")
        return image

    ranked = sorted(bills, key=lambda bill: bill.amount, reverse=True)
    shown = ranked[:bar_capacity(height)]
    largest = shown[0].amount
    span = max(1, width - LABEL_GUTTER - VALUE_GUTTER)

    # Spread the rows across the height available rather than stacking them at
    # a fixed pitch and leaving a slab of dead space beneath.
    pitch = BAR_HEIGHT + BAR_GAP
    if len(shown) > 1:
        pitch = min(BAR_HEIGHT + 30, max(pitch, (height - BAR_HEIGHT) / (len(shown) - 1)))

    name_face, value_face = TYPE["small"], TYPE["figure_small"]
    for index, bill in enumerate(shown):
        y = int(index * pitch)  # pastes take integer boxes
        length = max(3, round(span * (bill.amount / largest)))
        color = _ramp_step(palette.sequential, bill.amount / largest)

        paint.draw_text(image, (LABEL_GUTTER - 14, y + BAR_HEIGHT / 2),
                        _truncate(bill.name, name_face, LABEL_GUTTER - 22),
                        name_face, palette.ink_secondary, anchor="rm")

        if index == 0:
            halo = paint.glow((length, BAR_HEIGHT), BAR_HEIGHT // 2, 10, color, 105)
            image.alpha_composite(halo, (LABEL_GUTTER - 20, y - 20))

        # A gradient along the bar keeps the mark from reading as a flat slab.
        # Only the data end is rounded: the bar stays anchored to its zero.
        bar = paint.linear_gradient(
            (length, BAR_HEIGHT),
            (paint.mix(color, palette.surface, 0.18), color),
            horizontal=True,
        )
        image.paste(bar, (LABEL_GUTTER, y),
                    paint.rounded_mask((length, BAR_HEIGHT), BAR_HEIGHT // 2,
                                       corners=(False, True, True, False)))

        paint.draw_text(image, (LABEL_GUTTER + length + 12, y + BAR_HEIGHT / 2),
                        _money(bill.amount), value_face, palette.ink, anchor="lm")
    return image


def _ramp_step(ramp: tuple[str, ...], share: float) -> str:
    """Pick a ramp step for a value's share of the largest, easing the low end."""
    position = max(0.0, min(1.0, share)) ** 0.62
    return ramp[min(len(ramp) - 1, round(position * (len(ramp) - 1)))]


# ------------------------------------------------------------------ ribbon


def category_ribbon(
    size: tuple[int, int], bills: list[Bill], palette: Palette
) -> Image.Image:
    """A stacked bar of the monthly total, split by category, plus its legend.

    Part-to-whole across named categories, so colour is categorical and drawn
    from fixed slots. A stacked bar rather than a pie: shares stay comparable
    against a common baseline and long category names can be labelled directly.
    """
    image = _blank(size)
    width, height = size
    if not bills:
        return image

    shares = _folded(bills)
    grand_total = sum(shares.values())
    scale = width / grand_total

    entries = []
    cursor = 0.0
    items = list(shares.items())
    for index, (name, amount) in enumerate(items):
        color = palette.series[index % len(palette.series)]
        start = cursor * scale + (SEGMENT_GAP / 2 if index else 0)
        end = (cursor + amount) * scale - (SEGMENT_GAP / 2 if index < len(items) - 1 else 0)
        radius = RIBBON_HEIGHT // 2 if index in (0, len(items) - 1) else 5
        paint.rounded_rect(image, (start, 0, end, RIBBON_HEIGHT), radius,
                           fill=paint.rgba(color))

        share = amount / grand_total
        label = f"{share:.0%}"
        if (end - start) > paint.measure(label, TYPE["small_strong"])[0] + 18:
            paint.draw_text(image, ((start + end) / 2, RIBBON_HEIGHT / 2), label,
                            TYPE["small_strong"], paint.readable_on(color), anchor="mm")
        entries.append((name, amount, color))
        cursor += amount

    _legend(image, entries, palette, top=RIBBON_HEIGHT + 18, width=width)
    return image


def _folded(bills: list[Bill]) -> dict[str, float]:
    """Category totals, with everything past the slot ceiling folded into 'Other'.

    A generated ninth hue would be indistinguishable from an existing one under
    colour-vision deficiency, so the tail is merged rather than given a colour.
    """
    shares = by_category(bills)
    if len(shares) <= MAX_CATEGORY_SLOTS + 1:
        return shares
    head = dict(list(shares.items())[:MAX_CATEGORY_SLOTS])
    head["Other"] = head.get("Other", 0.0) + sum(list(shares.values())[MAX_CATEGORY_SLOTS:])
    # Absorbing the tail can push "Other" above a category that outranked it,
    # so re-sort - otherwise the ribbon runs 45%, 15%, 16% and looks broken.
    return dict(sorted(head.items(), key=lambda kv: kv[1], reverse=True))


def _legend(
    image: Image.Image, entries: list, palette: Palette, top: int, width: int
) -> None:
    """Swatch, name and amount in a grid - identity is never colour alone."""
    columns = 3 if width < 520 else 4
    column_width = width // columns
    name_face, value_face = TYPE["small"], TYPE["figure_small"]

    for index, (name, amount, color) in enumerate(entries):
        x = (index % columns) * column_width
        y = top + (index // columns) * 22
        paint.rounded_rect(image, (x, y + 3, x + 9, y + 12), 3, fill=paint.rgba(color))
        # Amount follows the name directly rather than being flushed to the
        # column edge, so each entry reads as one unit instead of two columns.
        amount_text = f"${amount:,.0f}"
        budget = column_width - 30 - paint.measure(amount_text, value_face)[0]
        label = _truncate(name, name_face, budget)
        paint.draw_text(image, (x + 16, y + 1), label, name_face, palette.ink_secondary)
        paint.draw_text(image, (x + 22 + paint.measure(label, name_face)[0], y + 1),
                        amount_text, value_face, palette.ink_muted)


def legend_rows(bills: list[Bill], width: int) -> int:
    """How many legend rows `category_ribbon` will draw, for height budgeting."""
    if not bills:
        return 0
    columns = 3 if width < 520 else 4
    return (len(_folded(bills)) + columns - 1) // columns
