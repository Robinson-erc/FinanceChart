"""
theme.py
--------
Every colour, type size and metric the interface draws with.

The look is "aurora ink": a near-black (or warm bone) plane lit by two soft
colour blooms, with frosted panels floating over it. Nothing here is a ttk
theme - the interface is painted onto canvases, so these are plain values that
`paint.py`, `widgets.py` and `charts.py` consume directly.

Colour choices are measured, not eyeballed. `palette_check.py` audits contrast
and colour-vision separation for both modes; run it after changing anything.
"""

from __future__ import annotations

from dataclasses import dataclass, field

RGBA = tuple[int, int, int, int]


@dataclass(frozen=True)
class Palette:
    """Every colour for one mode."""

    mode: str

    # Plane - the window's painted backdrop.
    plane_top: str
    plane_bottom: str
    bloom_a: str          # upper-left light
    bloom_b: str          # lower-right light
    bloom_alpha: int      # 0-255, how strongly the blooms read
    grain: int            # 0-255 alpha of the film grain

    # Panels - frosted glass floating over the plane.
    surface: str          # the flat equivalent, used for contrast maths
    glass: RGBA           # tint composited over the blurred plane
    stroke: RGBA          # hairline edge
    highlight: RGBA       # top-edge catch light
    shadow: RGBA          # drop shadow under panels
    inset: RGBA           # recessed wells (inputs, table hover)

    # Ink.
    ink: str
    ink_secondary: str
    ink_muted: str

    # Accents.
    accent: str
    accent_soft: str      # the accent's quieter partner, for gradient ends
    button: str
    button_active: str
    button_ink: str

    # Status. Always paired with words or an icon, never colour alone.
    good: str
    warning: str
    critical: str

    # Data.
    series: tuple[str, ...]      # categorical slots, assigned in fixed order
    sequential: tuple[str, ...]  # one hue, ordered small -> large magnitude

    # Chart chrome.
    grid: str
    track: RGBA           # unfilled portion of meters and gauges

    @property
    def is_dark(self) -> bool:
        return self.mode == "dark"


# The categorical hues keep the ordering of a set already proven to hold up
# under colour-vision deficiency; the steps are re-pitched brighter for an ink
# background and re-measured by `palette_check.py`.
_SERIES_DARK = (
    "#6E8BFF",  # periwinkle
    "#FF7A45",  # orange
    "#2FD4A7",  # mint
    "#FFC24B",  # amber
    "#FF6FA5",  # pink
    "#4CC93F",  # green
    "#B239F7",  # violet
    "#FF5C5C",  # red
)
_SERIES_LIGHT = (
    "#3D5BD9",
    "#D9531F",
    "#0E9E78",
    "#C98200",
    "#D6417C",
    "#2E8B22",
    "#5B1D96",
    "#D13A3A",
)

# Single-hue ramps for magnitude. Dark runs deep -> bright, light runs pale ->
# deep, so in both modes "more" reads as "further from the surface".
_RAMP_DARK = (
    "#41528f", "#4d5e9c", "#596aaa", "#6576b8", "#7283c6",
    "#7e90d4", "#8b9de2", "#99aaf0", "#a6b7ff",
)
_RAMP_LIGHT = (
    "#8fa4e2", "#8196d7", "#7389cc", "#657bc2", "#576eb7",
    "#4a61ac", "#3e54a1", "#314697", "#26398c",
)


DARK = Palette(
    mode="dark",
    plane_top="#080A10",
    plane_bottom="#0C1119",
    bloom_a="#6D5EF0",
    bloom_b="#12A8A0",
    bloom_alpha=132,
    grain=9,
    surface="#141A24",
    glass=(26, 33, 46, 214),
    stroke=(255, 255, 255, 28),
    highlight=(255, 255, 255, 40),
    shadow=(0, 0, 0, 150),
    inset=(255, 255, 255, 12),
    ink="#F3F6FB",
    ink_secondary="#A9B4C4",
    ink_muted="#79839A",
    accent="#9A8CFF",
    accent_soft="#5FE0D0",
    button="#6D5EF0",
    button_active="#5B4CDE",
    button_ink="#FFFFFF",
    good="#35D9A0",
    warning="#FFC24B",
    critical="#FF6B85",
    series=_SERIES_DARK,
    sequential=_RAMP_DARK,
    grid="#222B3A",
    track=(255, 255, 255, 22),
)

LIGHT = Palette(
    mode="light",
    plane_top="#F6F2EA",
    plane_bottom="#EAE4D9",
    bloom_a="#8C7BF0",
    bloom_b="#4FC5B4",
    bloom_alpha=74,
    grain=7,
    surface="#FCFAF6",
    glass=(255, 253, 249, 226),
    stroke=(18, 16, 14, 26),
    highlight=(255, 255, 255, 190),
    shadow=(60, 48, 30, 46),
    inset=(18, 16, 14, 12),
    ink="#141210",
    ink_secondary="#514B42",
    ink_muted="#7E776C",
    accent="#5B4BD6",
    accent_soft="#0E9E78",
    button="#5B4BD6",
    button_active="#4A3BC0",
    button_ink="#FFFFFF",
    good="#0F8A5F",
    warning="#B07400",
    critical="#C8324F",
    series=_SERIES_LIGHT,
    sequential=_RAMP_LIGHT,
    grid="#DED7CA",
    track=(18, 16, 14, 26),
)

PALETTES = {"light": LIGHT, "dark": DARK}


# ------------------------------------------------------------------ typography

#: Variable font: weight 100-800, width 75-100. The display sizes run condensed
#: and heavy, which is where the editorial feel comes from.
SANS_VARIABLE = "/usr/share/fonts/truetype/ubuntu/UbuntuSans[wdth,wght].ttf"
MONO_VARIABLE = "/usr/share/fonts/truetype/ubuntu/UbuntuSansMono[wght].ttf"
SANS_FALLBACK = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
MONO_FALLBACK = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"

#: Tk needs family names rather than paths, for the few real input widgets.
TK_SANS = "Ubuntu Sans"
TK_MONO = "Ubuntu Sans Mono"


@dataclass(frozen=True)
class Face:
    """One resolved type style: file, size, and variable-axis settings."""

    size: int
    weight: int = 400
    width: int = 100
    mono: bool = False
    tracking: float = 0.0  # extra px between glyphs; negative tightens


TYPE = {
    # Display - condensed and heavy, used for the hero figure only.
    "hero": Face(74, weight=800, width=75, tracking=-1.6),
    "hero_small": Face(44, weight=800, width=78, tracking=-0.8),
    "display": Face(30, weight=700, width=82, tracking=-0.4),

    # Titles.
    "title": Face(19, weight=700, width=88, tracking=-0.2),
    "section": Face(12, weight=700, tracking=1.5),   # small caps eyebrow
    "eyebrow": Face(10, weight=600, tracking=1.9),

    # Body.
    "body": Face(13, weight=400),
    "body_strong": Face(13, weight=600),
    "small": Face(11, weight=400),
    "small_strong": Face(11, weight=600),

    # Figures - mono keeps columns of money aligned.
    "figure": Face(14, weight=500, mono=True),
    "figure_small": Face(11, weight=500, mono=True),
    "figure_large": Face(21, weight=600, mono=True, tracking=-0.3),
}


# ---------------------------------------------------------------------- metric

RADIUS = {"panel": 20, "card": 16, "pill": 999, "well": 11, "chip": 9}

SPACE = {"page": 22, "panel": 22, "gap": 16, "row": 11, "tight": 8}

#: Panel elevation presets: (blur radius, y offset, spread)
ELEVATION = {"panel": (26, 10, 0), "raised": (14, 5, 0), "flat": (0, 0, 0)}


def palette_for(mode: str) -> Palette:
    return PALETTES.get(mode, DARK)
