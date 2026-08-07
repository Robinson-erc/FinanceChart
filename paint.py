"""
paint.py
--------
The drawing primitives everything else is built from.

Tk gives us rectangles and system widgets; this module gives us the rest -
gradient planes, real frosted glass, soft shadows, glows and variable-weight
type - by compositing with Pillow and handing the result to a canvas as an
image. Panels are painted against the actual pixels beneath them, so rounded
corners and blur read correctly over the gradient.

Everything expensive is cached by the arguments that produced it, because
these run on every resize and theme change.
"""

from __future__ import annotations

import functools
import math
import random

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from theme import (
    MONO_FALLBACK,
    MONO_VARIABLE,
    SANS_FALLBACK,
    SANS_VARIABLE,
    Face,
    Palette,
)

SUPERSAMPLE = 4  # corners and marks are drawn large, then reduced, to antialias


# ------------------------------------------------------------------ colour


def rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def rgba(value: str, alpha: int = 255) -> tuple[int, int, int, int]:
    return (*rgb(value), alpha)


def mix(first: str, second: str, amount: float) -> str:
    """Blend two hex colours in sRGB. `amount` 0 returns `first`."""
    a, b = rgb(first), rgb(second)
    blended = tuple(round(a[i] + (b[i] - a[i]) * amount) for i in range(3))
    return "#%02x%02x%02x" % blended


def readable_on(background: str) -> str:
    """Near-black or near-white, whichever carries on `background`."""
    r, g, b = (channel / 255 for channel in rgb(background))
    def linear(c):
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    luminance = 0.2126 * linear(r) + 0.7152 * linear(g) + 0.0722 * linear(b)
    return "#0B0B0B" if luminance > 0.42 else "#FFFFFF"


# ------------------------------------------------------------------- type


@functools.lru_cache(maxsize=256)
def font_for(size: int, weight: int, width: int, mono: bool) -> ImageFont.FreeTypeFont:
    """A font at the requested variable-axis settings, falling back gracefully."""
    variable = MONO_VARIABLE if mono else SANS_VARIABLE
    fallback = MONO_FALLBACK if mono else SANS_FALLBACK
    try:
        font = ImageFont.truetype(variable, size)
        axes = [axis["name"] for axis in font.get_variation_axes()]
        settings = []
        for axis in axes:
            name = axis.decode() if isinstance(axis, bytes) else str(axis)
            settings.append(float(width) if name.lower().startswith("wid") else float(weight))
        font.set_variation_by_axes(settings)
        return font
    except (OSError, ValueError):
        try:
            return ImageFont.truetype(fallback, size)
        except OSError:
            return ImageFont.load_default()


def face_font(face: Face) -> ImageFont.FreeTypeFont:
    return font_for(face.size, face.weight, face.width, face.mono)


@functools.lru_cache(maxsize=2048)
def measure(text: str, face: Face) -> tuple[int, int]:
    """Rendered (width, height) of `text`, including any tracking.

    Width is the advance width rather than the ink bounding box, so it matches
    what the tracked drawing path actually steps through and stays consistent
    between the two paths.
    """
    if not text:
        return (0, 0)
    font = face_font(face)
    width = font.getlength(text)
    if face.tracking:
        width += face.tracking * (len(text) - 1)
    ascent, descent = font.getmetrics()
    return (int(math.ceil(width)), ascent + descent)


def _baseline(y: float, vertical: str, font: ImageFont.FreeTypeFont) -> float:
    """Convert a Pillow vertical anchor to an absolute baseline position.

    Tracked text is drawn a glyph at a time, and each glyph must sit on the
    same baseline - anchoring every one to its own top would lift commas and
    periods to the cap line.
    """
    ascent, descent = font.getmetrics()
    if vertical == "t":
        return y + ascent
    if vertical == "m":
        return y - (ascent + descent) / 2 + ascent
    if vertical == "b":
        return y - descent
    return y  # "s" - already a baseline


TEXT_PAD = 6  # room for glyphs that overhang their advance width


@functools.lru_cache(maxsize=1024)
def _text_layer(text: str, face: Face, fill) -> Image.Image:
    """`text` rendered once into its own transparent layer, then cached.

    Tracked text has to be drawn a glyph at a time (Pillow has no
    letter-spacing), which made labels the single biggest cost in a repaint.
    The same strings recur every frame, so the rasterised result is kept.
    Callers composite from it and must not mutate it.
    """
    font = face_font(face)
    width, height = measure(text, face)
    layer = Image.new("RGBA", (width + TEXT_PAD * 2, height + TEXT_PAD * 2),
                      (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    ascent, _ = font.getmetrics()
    baseline = TEXT_PAD + ascent

    if face.tracking:
        x = float(TEXT_PAD)
        for character in text:
            draw.text((x, baseline), character, font=font, fill=fill, anchor="ls")
            x += font.getlength(character) + face.tracking
    else:
        draw.text((TEXT_PAD, baseline), text, font=font, fill=fill, anchor="ls")
    return layer


def draw_text(
    image: Image.Image,
    xy: tuple[float, float],
    text: str,
    face: Face,
    fill: str,
    anchor: str = "lt",
) -> tuple[int, int]:
    """Draw `text` and return its size. `anchor` is Pillow's two-letter form."""
    if not text:
        return (0, 0)
    width, height = measure(text, face)
    layer = _text_layer(text, face, fill)

    x, y = xy
    horizontal, vertical = anchor[0], anchor[1]
    if horizontal == "m":
        x -= width / 2
    elif horizontal == "r":
        x -= width
    # The layer's baseline sits TEXT_PAD + ascent below its top edge, so shift
    # the requested anchor back to that layer origin.
    top = _baseline(y, vertical, face_font(face)) - face_font(face).getmetrics()[0]
    image.alpha_composite(layer, (int(x - TEXT_PAD), int(top - TEXT_PAD)))
    return (width, height)


def gradient_text(
    size: tuple[int, int],
    text: str,
    face: Face,
    colors: tuple[str, str],
) -> Image.Image:
    """`text` rendered as an RGBA image with a horizontal gradient through it."""
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    glyphs = _text_layer(text, face, "#ffffff")
    mask = Image.new("L", size, 0)
    # Shift the padding back out so the text box starts at the image origin.
    mask.paste(glyphs.getchannel("A"), (-TEXT_PAD, -TEXT_PAD))
    image.paste(linear_gradient(size, colors, horizontal=True), (0, 0), mask)
    return image


# --------------------------------------------------------------- gradients


def linear_gradient(
    size: tuple[int, int], colors: tuple[str, str], horizontal: bool = False
) -> Image.Image:
    """A two-stop linear gradient, built one row (or column) at a time."""
    width, height = size
    steps = max(2, width if horizontal else height)
    strip = Image.new("RGB", (steps, 1) if horizontal else (1, steps))
    pixels = strip.load()
    start, end = rgb(colors[0]), rgb(colors[1])
    for index in range(steps):
        t = index / (steps - 1)
        value = tuple(round(start[i] + (end[i] - start[i]) * t) for i in range(3))
        if horizontal:
            pixels[index, 0] = value
        else:
            pixels[0, index] = value
    return strip.resize(size, Image.BILINEAR).convert("RGBA")


@functools.lru_cache(maxsize=8)
def _radial_falloff(resolution: int = 96) -> Image.Image:
    """A small radial alpha falloff, scaled up when a bloom is needed.

    Building it at low resolution and enlarging is far cheaper than evaluating
    the falloff per pixel, and the result is a soft light either way.
    """
    falloff = Image.new("L", (resolution, resolution), 0)
    pixels = falloff.load()
    centre = (resolution - 1) / 2
    for y in range(resolution):
        for x in range(resolution):
            distance = math.hypot(x - centre, y - centre) / centre
            pixels[x, y] = max(0, round(255 * (1 - min(distance, 1.0)) ** 2.2))
    return falloff


def bloom(
    image: Image.Image, centre: tuple[float, float], radius: float, color: str, alpha: int
) -> None:
    """Add a soft radial light to `image`, in place."""
    diameter = max(8, int(radius * 2))
    falloff = _radial_falloff().resize((diameter, diameter), Image.BILINEAR)
    if alpha < 255:
        falloff = falloff.point(lambda value: value * alpha // 255)
    patch = Image.new("RGB", (diameter, diameter), rgb(color))
    image.paste(patch, (int(centre[0] - radius), int(centre[1] - radius)), falloff)


@functools.lru_cache(maxsize=4)
def _grain_tile(size: int = 128, seed: int = 7) -> Image.Image:
    """A tileable monochrome noise patch, composited to break up flat gradients."""
    generator = random.Random(seed)
    tile = Image.new("L", (size, size))
    tile.putdata([generator.randint(0, 255) for _ in range(size * size)])
    return tile


# ------------------------------------------------------------------- shapes


@functools.lru_cache(maxsize=128)
def rounded_mask(
    size: tuple[int, int], radius: int, corners: tuple[bool, bool, bool, bool] | None = None
) -> Image.Image:
    """An antialiased rounded-rectangle alpha mask."""
    width, height = size
    radius = min(radius, width // 2, height // 2)
    large = Image.new("L", (width * SUPERSAMPLE, height * SUPERSAMPLE), 0)
    ImageDraw.Draw(large).rounded_rectangle(
        (0, 0, width * SUPERSAMPLE - 1, height * SUPERSAMPLE - 1),
        radius=radius * SUPERSAMPLE,
        fill=255,
        corners=corners,
    )
    return large.resize((width, height), Image.LANCZOS)


def _norm(colour):
    """Make a colour hashable so shape layers can be cached by it."""
    if colour is None or isinstance(colour, tuple):
        return colour
    return tuple(colour) if isinstance(colour, list) else str(colour)


@functools.lru_cache(maxsize=512)
def _rounded_layer(
    size: tuple[int, int],
    radius: int,
    fill,
    outline,
    width: int,
    corners: tuple[bool, bool, bool, bool] | None,
) -> Image.Image:
    """A cached rounded-rectangle layer.

    Every shape is drawn at 4x and reduced, which is expensive enough that
    doing it per frame made hovering visibly laggy. The same handful of
    (size, radius, colour) combinations recur constantly, so they are cached.
    Callers composite from the result and must not mutate it.
    """
    scale = SUPERSAMPLE
    big = Image.new("RGBA", (size[0] * scale, size[1] * scale), (0, 0, 0, 0))
    ImageDraw.Draw(big).rounded_rectangle(
        (0, 0, size[0] * scale - 1, size[1] * scale - 1),
        radius=min(radius, size[0] // 2, size[1] // 2) * scale,
        fill=fill,
        outline=outline,
        width=max(1, width) * scale,
        corners=corners,
    )
    return big.resize(size, Image.LANCZOS)


def rounded_rect(
    image: Image.Image,
    box: tuple[int, int, int, int],
    radius: int,
    fill=None,
    outline=None,
    width: int = 1,
    corners: tuple[bool, bool, bool, bool] | None = None,
) -> None:
    """Draw a rounded rectangle onto an RGBA image with clean edges.

    `corners` is (top-left, top-right, bottom-right, bottom-left); pass it to
    round only the end a bar's data reaches, leaving the baseline end square.
    """
    x0, y0, x1, y1 = box
    size = (max(1, int(x1 - x0)), max(1, int(y1 - y0)))
    layer = _rounded_layer(size, int(radius), _norm(fill), _norm(outline),
                           int(width), corners)
    image.alpha_composite(layer, (int(x0), int(y0)))


def triangle(
    image: Image.Image, centre: tuple[float, float], size: float, fill, down: bool = True
) -> None:
    """A small solid caret.

    Drawn rather than typeset: the UI font has no arrow glyphs, and a missing
    glyph renders as a tofu box.
    """
    x, y = centre
    half = size / 2
    rise = size * 0.5
    points = ([(x - half, y - rise / 2), (x + half, y - rise / 2), (x, y + rise / 2)]
              if down else
              [(x - half, y + rise / 2), (x + half, y + rise / 2), (x, y - rise / 2)])
    scale = SUPERSAMPLE
    pad = int(size * 2)
    big = Image.new("RGBA", (pad * scale, pad * scale), (0, 0, 0, 0))
    ImageDraw.Draw(big).polygon(
        [((px - x + pad / 2) * scale, (py - y + pad / 2) * scale) for px, py in points],
        fill=fill,
    )
    image.alpha_composite(big.resize((pad, pad), Image.LANCZOS),
                          (int(x - pad / 2), int(y - pad / 2)))


def sun_or_moon(
    image: Image.Image, centre: tuple[float, float], radius: float, fill, moon: bool
) -> None:
    """A theme-toggle glyph, drawn for the same reason as `triangle`."""
    scale = SUPERSAMPLE
    pad = int(radius * 6)
    big = Image.new("RGBA", (pad * scale, pad * scale), (0, 0, 0, 0))
    draw = ImageDraw.Draw(big)
    cx = cy = pad * scale / 2
    r = radius * scale

    if moon:
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=fill)
        # Bite a smaller disc out of one side to leave a crescent.
        offset = r * 0.62
        draw.ellipse((cx - r + offset, cy - r * 1.02, cx + r + offset, cy + r * 1.02),
                     fill=(0, 0, 0, 0))
    else:
        draw.ellipse((cx - r * 0.62, cy - r * 0.62, cx + r * 0.62, cy + r * 0.62),
                     fill=fill)
        for index in range(8):
            angle = index * math.pi / 4
            inner, outer = r * 0.86, r * 1.25
            draw.line(
                (cx + math.cos(angle) * inner, cy + math.sin(angle) * inner,
                 cx + math.cos(angle) * outer, cy + math.sin(angle) * outer),
                fill=fill, width=max(1, int(scale * 1.2)),
            )
    image.alpha_composite(big.resize((pad, pad), Image.LANCZOS),
                          (int(centre[0] - pad / 2), int(centre[1] - pad / 2)))


@functools.lru_cache(maxsize=192)
def soft_shadow(
    size: tuple[int, int], radius: int, blur: int, color: tuple[int, int, int, int]
) -> Image.Image:
    """A blurred rounded silhouette, sized to include the blur margin.

    Cached: the Gaussian blur is the single most expensive operation here and
    the same shadows and glows recur every frame. Treat the result as read-only.
    """
    width, height = size
    pad = blur * 2
    canvas = Image.new("RGBA", (width + pad * 2, height + pad * 2), (0, 0, 0, 0))
    shape = Image.new("RGBA", (width, height), color)
    canvas.paste(shape, (pad, pad), rounded_mask((width, height), radius))
    return canvas.filter(ImageFilter.GaussianBlur(blur))


def glow(
    size: tuple[int, int], radius: int, blur: int, color: str, alpha: int = 160
) -> Image.Image:
    """A coloured bloom shaped like the mark it sits behind."""
    return soft_shadow(size, radius, blur, (*rgb(color), alpha))


# -------------------------------------------------------------- the plane


@functools.lru_cache(maxsize=6)
def plane(size: tuple[int, int], mode: str) -> Image.Image:
    """The window's painted backdrop: gradient, two colour blooms, film grain."""
    from theme import palette_for

    palette = palette_for(mode)
    width, height = size
    image = linear_gradient(size, (palette.plane_top, palette.plane_bottom)).convert("RGB")

    reach = max(width, height)
    bloom(image, (width * 0.16, height * 0.04), reach * 0.62,
          palette.bloom_a, palette.bloom_alpha)
    bloom(image, (width * 0.92, height * 0.86), reach * 0.55,
          palette.bloom_b, int(palette.bloom_alpha * 0.72))
    bloom(image, (width * 0.62, height * 0.30), reach * 0.34,
          palette.bloom_a, int(palette.bloom_alpha * 0.34))

    if palette.grain:
        tile = _grain_tile()
        noise = Image.new("L", size)
        for y in range(0, height, tile.height):
            for x in range(0, width, tile.width):
                noise.paste(tile, (x, y))
        speckle = Image.new("RGB", size, (255, 255, 255))
        image.paste(speckle, (0, 0), noise.point(lambda v: v * palette.grain // 255))

    return image.convert("RGBA")


@functools.lru_cache(maxsize=48)
def frost(
    plane_size: tuple[int, int],
    box: tuple[int, int, int, int],
    radius: int,
    mode: str,
    elevation: str = "panel",
) -> Image.Image:
    """A frosted-glass panel, composited against the plane pixels beneath it.

    The region of the plane under `box` is blurred and tinted, so the colour
    blooms bleed through the panel the way real glass would, then the rounded
    edge, catch light and drop shadow are drawn on top.
    """
    from theme import ELEVATION, palette_for

    palette = palette_for(mode)
    x0, y0, x1, y1 = box
    width, height = max(1, x1 - x0), max(1, y1 - y0)
    blur, offset, _ = ELEVATION.get(elevation, ELEVATION["panel"])

    backdrop = plane(plane_size, mode).crop((x0, y0, x0 + width, y0 + height))
    backdrop = backdrop.filter(ImageFilter.GaussianBlur(18))

    tint = Image.new("RGBA", (width, height), palette.glass)
    glass = Image.alpha_composite(backdrop.convert("RGBA"), tint)

    panel = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    panel.paste(glass, (0, 0), rounded_mask((width, height), radius))

    # Hairline edge, plus a brighter catch light along the top curve.
    rounded_rect(panel, (0, 0, width, height), radius, outline=palette.stroke, width=1)
    edge = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    rounded_rect(edge, (0, 0, width, height), radius, outline=palette.highlight, width=1)
    fade = linear_gradient((width, height), ("#ffffff", "#000000")).convert("L")
    panel.paste(edge, (0, 0), Image.composite(fade, Image.new("L", (width, height), 0),
                                              edge.getchannel("A")))

    if blur:
        shadow = soft_shadow((width, height), radius, blur, palette.shadow)
        canvas = Image.new("RGBA", shadow.size, (0, 0, 0, 0))
        canvas.alpha_composite(shadow, (0, offset))
        canvas.alpha_composite(panel, (blur * 2, blur * 2))
        return canvas
    return panel


def frost_origin(elevation: str = "panel") -> int:
    """How far a frosted panel's image extends past the panel box, for placement."""
    from theme import ELEVATION

    return ELEVATION.get(elevation, ELEVATION["panel"])[0] * 2


def to_photo(image: Image.Image):
    """Hand a Pillow image to Tk. The caller must keep the reference alive."""
    from PIL import ImageTk

    return ImageTk.PhotoImage(image)
