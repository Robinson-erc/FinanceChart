"""
ui.py
-----
The painted components: geometry helpers and the draw routines for every
control on screen.

Nothing here is a Tk widget. Each function draws onto an RGBA image and, where
it is interactive, the caller records the rectangle it occupies so clicks and
hovers can be routed back. Keeping the drawing separate from the event plumbing
means the whole interface can be rendered and inspected without a window.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from PIL import Image

import paint
from theme import RADIUS, TYPE, Palette


@dataclass(frozen=True)
class Rect:
    """An axis-aligned rectangle in image coordinates."""

    x: float
    y: float
    w: float
    h: float

    @property
    def box(self) -> tuple[float, float, float, float]:
        return (self.x, self.y, self.x + self.w, self.y + self.h)

    @property
    def right(self) -> float:
        return self.x + self.w

    @property
    def bottom(self) -> float:
        return self.y + self.h

    @property
    def centre(self) -> tuple[float, float]:
        return (self.x + self.w / 2, self.y + self.h / 2)

    @property
    def size(self) -> tuple[int, int]:
        return (max(1, int(self.w)), max(1, int(self.h)))

    def contains(self, x: float, y: float) -> bool:
        return self.x <= x < self.right and self.y <= y < self.bottom

    def inset(self, dx: float, dy: float | None = None) -> "Rect":
        dy = dx if dy is None else dy
        return Rect(self.x + dx, self.y + dy, self.w - dx * 2, self.h - dy * 2)

    def moved(self, dx: float, dy: float) -> "Rect":
        return Rect(self.x + dx, self.y + dy, self.w, self.h)

    def cut_top(self, amount: float, gap: float = 0) -> tuple["Rect", "Rect"]:
        """Slice `amount` off the top; return (slice, remainder)."""
        top = Rect(self.x, self.y, self.w, amount)
        rest = Rect(self.x, self.y + amount + gap, self.w, self.h - amount - gap)
        return top, rest

    def cut_left(self, amount: float, gap: float = 0) -> tuple["Rect", "Rect"]:
        left = Rect(self.x, self.y, amount, self.h)
        rest = Rect(self.x + amount + gap, self.y, self.w - amount - gap, self.h)
        return left, rest


@dataclass
class Hits:
    """Where every interactive region ended up during the last render."""

    regions: list[tuple[Rect, str]] = field(default_factory=list)

    def add(self, rect: Rect, target: str) -> None:
        self.regions.append((rect, target))

    def at(self, x: float, y: float) -> str | None:
        """The topmost target under the point, or None. Later wins."""
        for rect, target in reversed(self.regions):
            if rect.contains(x, y):
                return target
        return None

    def rect_of(self, target: str) -> Rect | None:
        for rect, name in self.regions:
            if name == target:
                return rect
        return None


# ------------------------------------------------------------------ surfaces


def panel(
    image: Image.Image,
    plane_size: tuple[int, int],
    rect: Rect,
    palette: Palette,
    radius: int | None = None,
    elevation: str = "panel",
) -> None:
    """Composite a frosted-glass panel at `rect`, shadow included."""
    radius = RADIUS["panel"] if radius is None else radius
    box = (int(rect.x), int(rect.y), int(rect.right), int(rect.bottom))
    tile = paint.frost(plane_size, box, radius, palette.mode, elevation)
    pad = paint.frost_origin(elevation)
    image.alpha_composite(tile, (box[0] - pad, box[1] - pad))


def well(
    image: Image.Image, rect: Rect, palette: Palette, focused: bool = False
) -> None:
    """A recessed field background - inputs, the search box, hovered rows."""
    paint.rounded_rect(image, rect.box, RADIUS["well"], fill=palette.inset)
    if focused:
        paint.rounded_rect(image, rect.box, RADIUS["well"],
                           outline=paint.rgba(palette.accent, 190), width=2)


def eyebrow(image: Image.Image, x: float, y: float, text: str, palette: Palette) -> None:
    """A small tracked-out caps label - the section heading of this interface."""
    paint.draw_text(image, (x, y), text.upper(), TYPE["eyebrow"], palette.ink_muted)


def divider(image: Image.Image, rect: Rect, palette: Palette) -> None:
    paint.rounded_rect(image, (rect.x, rect.y, rect.right, rect.y + 1), 0,
                       fill=palette.stroke)


# ------------------------------------------------------------------- marks


def wordmark(image: Image.Image, x: float, y: float, palette: Palette) -> None:
    """A small gradient lozenge plus the product name."""
    badge = paint.linear_gradient((26, 26), (palette.accent, palette.accent_soft),
                                  horizontal=True)
    image.paste(badge, (int(x), int(y)), paint.rounded_mask((26, 26), 9))
    paint.draw_text(image, (x + 8, y + 5), "L", TYPE["body_strong"],
                    paint.readable_on(palette.accent))
    paint.draw_text(image, (x + 38, y - 1), "LEDGER", TYPE["section"], palette.ink)
    paint.draw_text(image, (x + 38, y + 15), "monthly budget", TYPE["small"],
                    palette.ink_muted)


def tab_switcher(
    image: Image.Image,
    rect: Rect,
    labels: list[str],
    active: int,
    palette: Palette,
    hits: Hits,
    hovered: str | None = None,
) -> None:
    """A segmented pill. The active segment carries a solid, lit indicator."""
    paint.rounded_rect(image, rect.box, RADIUS["pill"], fill=palette.inset)
    width = rect.w / len(labels)

    indicator = Rect(rect.x + active * width, rect.y, width, rect.h).inset(3)
    halo = paint.glow(indicator.size, RADIUS["pill"], 9, palette.accent, 95)
    image.alpha_composite(halo, (int(indicator.x) - 18, int(indicator.y) - 18))
    fill = paint.linear_gradient(indicator.size, (palette.accent, palette.accent_soft),
                                 horizontal=True)
    image.paste(fill, (int(indicator.x), int(indicator.y)),
                paint.rounded_mask(indicator.size, RADIUS["pill"]))

    for index, label in enumerate(labels):
        cell = Rect(rect.x + index * width, rect.y, width, rect.h)
        if index == active:
            colour = paint.readable_on(palette.accent)
        elif hovered == f"tab:{index}":
            colour = palette.ink
        else:
            colour = palette.ink_muted
        paint.draw_text(image, cell.centre, label, TYPE["small_strong"], colour,
                        anchor="mm")
        hits.add(cell, f"tab:{index}")


def button(
    image: Image.Image,
    rect: Rect,
    label: str,
    palette: Palette,
    hits: Hits,
    target: str,
    variant: str = "ghost",
    hovered: bool = False,
) -> None:
    """A pill button. `variant` is primary, ghost or danger."""
    if variant == "primary":
        if hovered:
            halo = paint.glow(rect.size, RADIUS["pill"], 12, palette.accent, 130)
            image.alpha_composite(halo, (int(rect.x) - 24, int(rect.y) - 24))
        top = palette.button_active if hovered else palette.button
        fill = paint.linear_gradient(rect.size, (top, palette.accent), horizontal=True)
        image.paste(fill, (int(rect.x), int(rect.y)),
                    paint.rounded_mask(rect.size, RADIUS["pill"]))
        paint.draw_text(image, rect.centre, label, TYPE["small_strong"],
                        palette.button_ink, anchor="mm")
    else:
        ink = palette.critical if variant == "danger" else palette.ink_secondary
        edge = paint.rgba(ink, 150 if hovered else 90)
        if hovered:
            paint.rounded_rect(image, rect.box, RADIUS["pill"], fill=palette.inset)
        paint.rounded_rect(image, rect.box, RADIUS["pill"], outline=edge, width=1)
        paint.draw_text(image, rect.centre, label, TYPE["small_strong"],
                        palette.ink if hovered else ink, anchor="mm")
    hits.add(rect, target)


def theme_button(
    image: Image.Image,
    rect: Rect,
    palette: Palette,
    hits: Hits,
    target: str,
    hovered: bool = False,
) -> None:
    """Toggle between modes. The glyph is drawn, not typeset - see paint.triangle."""
    paint.rounded_rect(image, rect.box, RADIUS["pill"],
                       fill=palette.inset if hovered else None,
                       outline=paint.rgba(palette.ink_muted, 110), width=1)
    colour = palette.ink if hovered else palette.ink_secondary
    # Dark mode offers the sun (switch to light); light mode offers the moon.
    paint.sun_or_moon(image, rect.centre, 7, paint.rgba(colour),
                      moon=not palette.is_dark)
    hits.add(rect, target)


# -------------------------------------------------------------------- data


def stat_chip(
    image: Image.Image,
    rect: Rect,
    label: str,
    value: str,
    note: str,
    palette: Palette,
    accent: str | None = None,
) -> None:
    """A compact KPI: tracked caps label, mono figure, supporting note."""
    paint.rounded_rect(image, rect.box, RADIUS["card"], fill=palette.inset)
    inner = rect.inset(15, 13)
    eyebrow(image, inner.x, inner.y, label, palette)
    paint.draw_text(image, (inner.x, inner.y + 19),
                    _fit(value, TYPE["figure_large"], inner.w),
                    TYPE["figure_large"], accent or palette.ink)
    paint.draw_text(image, (inner.x, inner.y + 48),
                    _fit(note, TYPE["small"], inner.w), TYPE["small"],
                    palette.ink_muted)


def hero_figure(
    image: Image.Image,
    x: float,
    y: float,
    value: str,
    palette: Palette,
    tone: str = "normal",
) -> None:
    """The headline number, in condensed heavy display type.

    Positive balances take the accent gradient; a negative one takes the
    critical colour flat, so the exceptional case never looks celebratory. A
    placeholder is drawn flat too - a gradient through an em dash reads as a
    stray swatch rather than an absent value.
    """
    face = TYPE["hero"]
    if tone == "critical":
        paint.draw_text(image, (x, y), value, face, palette.critical)
        return
    if tone == "empty":
        paint.draw_text(image, (x, y), value, face, palette.ink_muted)
        return
    size = paint.measure(value, face)
    plate = paint.gradient_text((size[0] + 8, size[1] + 14), value, face,
                                (palette.ink, palette.accent))
    image.alpha_composite(plate, (int(x), int(y)))


def table_header(
    image: Image.Image,
    rect: Rect,
    columns: list[tuple[str, float, str]],
    sort_key: str,
    reverse: bool,
    palette: Palette,
    hits: Hits,
    hovered: str | None = None,
) -> None:
    """Column captions, each a sort control."""
    for key, width, align in columns:
        cell = Rect(rect.x, rect.y, width, rect.h)
        caption = key.upper()
        sorted_by = sort_key == key
        active = sorted_by or hovered == f"sort:{key}"
        colour = palette.ink_secondary if active else palette.ink_muted
        anchor = {"l": "lm", "r": "rm", "c": "mm"}[align]
        # Leave room for the caret so it never sits on top of the caption.
        caret_room = 13 if sorted_by else 0
        x = {"l": cell.x, "r": cell.right - caret_room,
             "c": cell.centre[0] - caret_room / 2}[align]
        paint.draw_text(image, (x, cell.centre[1]), caption, TYPE["eyebrow"], colour,
                        anchor=anchor)
        if sorted_by:
            span = paint.measure(caption, TYPE["eyebrow"])[0]
            edge = {"l": cell.x + span, "r": cell.right - caret_room,
                    "c": cell.centre[0] - caret_room / 2 + span / 2}[align]
            paint.triangle(image, (edge + 7, cell.centre[1]), 7,
                           paint.rgba(colour), down=reverse)
        hits.add(cell, f"sort:{key}")
        rect = Rect(rect.x + width, rect.y, rect.w - width, rect.h)


def table_row(
    image: Image.Image,
    rect: Rect,
    cells: list[tuple[str, float, str, str]],
    palette: Palette,
    accent: str | None = None,
    selected: bool = False,
    hovered: bool = False,
) -> None:
    """One record, with its category colour carried in a leading dot."""
    if selected:
        paint.rounded_rect(image, rect.box, RADIUS["well"],
                           fill=paint.rgba(palette.accent, 38))
        paint.rounded_rect(image, (rect.x, rect.y + 5, rect.x + 3, rect.bottom - 5),
                           2, fill=paint.rgba(palette.accent))
    elif hovered:
        paint.rounded_rect(image, rect.box, RADIUS["well"], fill=palette.inset)

    x = rect.x + 12
    for text, width, align, role in cells:
        face = TYPE["figure"] if role == "figure" else TYPE["body"]
        colour = {
            "figure": palette.ink,
            "primary": palette.ink,
            "muted": palette.ink_muted,
        }.get(role, palette.ink_secondary)
        anchor = {"l": "lm", "r": "rm", "c": "mm"}[align]
        at = {"l": x, "r": x + width - 12, "c": x + width / 2}[align]
        if role == "dot" and accent:
            paint.rounded_rect(image, (x, rect.centre[1] - 4, x + 8, rect.centre[1] + 4),
                               3, fill=paint.rgba(accent))
            paint.draw_text(image, (x + 15, rect.centre[1]), text, TYPE["body"],
                            palette.ink_secondary, anchor="lm")
        else:
            paint.draw_text(image, (at, rect.centre[1]),
                            _fit(text, face, width - 16), face, colour, anchor=anchor)
        x += width


def _fit(text: str, face, limit: float) -> str:
    if limit <= 0 or paint.measure(text, face)[0] <= limit:
        return text
    while text and paint.measure(text + "…", face)[0] > limit:
        text = text[:-1]
    return text + "…"


def empty_state(
    image: Image.Image, rect: Rect, headline: str, detail: str, palette: Palette
) -> None:
    centre_x, centre_y = rect.centre
    paint.draw_text(image, (centre_x, centre_y - 12), headline, TYPE["body_strong"],
                    palette.ink_secondary, anchor="mm")
    paint.draw_text(image, (centre_x, centre_y + 10), detail, TYPE["small"],
                    palette.ink_muted, anchor="mm")


def dropdown(
    image: Image.Image,
    rect: Rect,
    value: str,
    palette: Palette,
    hits: Hits,
    target: str,
    open_: bool = False,
    hovered: bool = False,
) -> None:
    """A closed select control. The open list is drawn separately, on top."""
    well(image, rect, palette, focused=open_)
    if hovered and not open_:
        paint.rounded_rect(image, rect.box, RADIUS["well"],
                           outline=paint.rgba(palette.ink_muted, 110), width=1)
    paint.draw_text(image, (rect.x + 12, rect.centre[1]), value, TYPE["body"],
                    palette.ink, anchor="lm")
    paint.triangle(image, (rect.right - 16, rect.centre[1]), 8,
                   paint.rgba(palette.ink_muted), down=not open_)
    hits.add(rect, target)


def dropdown_list(
    image: Image.Image,
    plane_size: tuple[int, int],
    rect: Rect,
    options: list[str],
    selected: str,
    palette: Palette,
    hits: Hits,
    prefix: str,
    hovered: str | None = None,
) -> None:
    """The open option list, floated above everything else."""
    panel(image, plane_size, rect, palette, radius=RADIUS["card"], elevation="raised")
    row_height = (rect.h - 12) / max(1, len(options))
    for index, option in enumerate(options):
        row = Rect(rect.x + 6, rect.y + 6 + index * row_height, rect.w - 12, row_height)
        target = f"{prefix}:{option}"
        if option == selected:
            paint.rounded_rect(image, row.box, RADIUS["chip"],
                               fill=paint.rgba(palette.accent, 46))
        elif hovered == target:
            paint.rounded_rect(image, row.box, RADIUS["chip"], fill=palette.inset)
        paint.draw_text(image, (row.x + 10, row.centre[1]), option, TYPE["body"],
                        palette.ink if option == selected else palette.ink_secondary,
                        anchor="lm")
        hits.add(row, target)


def toast(
    image: Image.Image,
    plane_size: tuple[int, int],
    rect: Rect,
    title: str,
    message: str,
    palette: Palette,
    tone: str = "accent",
) -> None:
    """A transient confirmation in the corner."""
    panel(image, plane_size, rect, palette, radius=RADIUS["card"], elevation="raised")
    stripe = {"accent": palette.accent, "critical": palette.critical,
              "good": palette.good}.get(tone, palette.accent)
    paint.rounded_rect(image, (rect.x + 14, rect.y + 14, rect.x + 17, rect.bottom - 14),
                       2, fill=paint.rgba(stripe))
    paint.draw_text(image, (rect.x + 28, rect.y + 15), title, TYPE["small_strong"],
                    palette.ink)
    paint.draw_text(image, (rect.x + 28, rect.y + 33),
                    _fit(message, TYPE["small"], rect.w - 44), TYPE["small"],
                    palette.ink_muted)


def modal(
    image: Image.Image,
    plane_size: tuple[int, int],
    size: tuple[int, int],
    rect: Rect,
    message: str,
    palette: Palette,
    hits: Hits,
    hovered: str | None = None,
) -> None:
    """A confirmation dialog over a scrim that dims the whole window."""
    scrim = Image.new("RGBA", size, (0, 0, 0, 150 if palette.is_dark else 96))
    image.alpha_composite(scrim, (0, 0))
    hits.add(Rect(0, 0, size[0], size[1]), "confirm:no")

    panel(image, plane_size, rect, palette, radius=RADIUS["panel"], elevation="panel")
    eyebrow(image, rect.x + 26, rect.y + 24, "Please confirm", palette)
    for index, line in enumerate(_wrap(message, TYPE["body"], rect.w - 52)):
        paint.draw_text(image, (rect.x + 26, rect.y + 48 + index * 21), line,
                        TYPE["body"], palette.ink)

    width, height = 96, 36
    yes = Rect(rect.right - 26 - width, rect.bottom - 26 - height, width, height)
    no = Rect(yes.x - 12 - width, yes.y, width, height)
    button(image, no, "Cancel", palette, hits, "confirm:no",
           hovered=hovered == "confirm:no")
    button(image, yes, "Delete", palette, hits, "confirm:yes", variant="primary",
           hovered=hovered == "confirm:yes")


def _wrap(text: str, face, limit: float) -> list[str]:
    lines, line = [], ""
    for word in text.split():
        candidate = f"{line} {word}".strip()
        if paint.measure(candidate, face)[0] > limit and line:
            lines.append(line)
            line = word
        else:
            line = candidate
    if line:
        lines.append(line)
    return lines
