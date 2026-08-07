"""
palette_check.py
----------------
Design-time validator for the colors in `theme.py`.

Color choices here are measured, not eyeballed. Run it after touching any
palette value:

    python palette_check.py

It reports, per mode:

  * contrast of every ink and accent against the surface it sits on (WCAG 2.1),
  * separation of adjacent categorical slots for normal vision and for each
    of the three dichromacies, as OKLab dE x100.

Thresholds follow the usual data-visualisation gates: adjacent series must
stay >= 8 dE under colour-vision deficiency and >= 15 for normal vision, text
wants >= 4.5:1, and a mark or control wants >= 3:1. Anything below prints FAIL
and exits non-zero.
"""

from __future__ import annotations

import sys

CVD_MIN = 8.0
NORMAL_MIN = 15.0
TEXT_MIN = 4.5
MARK_MIN = 3.0


# --------------------------------------------------------------- conversions


def to_rgb(value: str) -> tuple[float, float, float]:
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) / 255 for i in (0, 2, 4))


def to_linear(channel: float) -> float:
    return channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4


def to_srgb(channel: float) -> float:
    channel = min(max(channel, 0.0), 1.0)
    return channel * 12.92 if channel <= 0.0031308 else 1.055 * channel ** (1 / 2.4) - 0.055


def linear_rgb(hex_color: str) -> tuple[float, float, float]:
    return tuple(to_linear(c) for c in to_rgb(hex_color))


def luminance(hex_color: str) -> float:
    r, g, b = linear_rgb(hex_color)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def oklab(hex_color: str) -> tuple[float, float, float]:
    """sRGB hex -> OKLab. Perceptually uniform, so Euclidean distance is dE."""
    r, g, b = linear_rgb(hex_color)
    l = (0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b) ** (1 / 3)
    m = (0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b) ** (1 / 3)
    s = (0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b) ** (1 / 3)
    return (
        0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s,
        1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
        0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s,
    )


def delta_e(a: str, b: str) -> float:
    """OKLab distance, x100 so the numbers read like the familiar dE scale."""
    first, second = oklab(a), oklab(b)
    return 100 * sum((x - y) ** 2 for x, y in zip(first, second)) ** 0.5


# ----------------------------------------------------- colour-vision deficiency

# Vienot, Brettel & Mollon (1999): project onto the dichromat's surviving plane
# in LMS, then return to sRGB.
_TO_LMS = (
    (0.31399022, 0.63951294, 0.04649755),
    (0.15537241, 0.75789446, 0.08670142),
    (0.01775239, 0.10944209, 0.87256922),
)
_FROM_LMS = (
    (5.47221206, -4.64196010, 0.16963708),
    (-1.12524190, 2.29317094, -0.16789520),
    (0.02980165, -0.19318073, 1.16364789),
)


def _apply(matrix, vector):
    return tuple(sum(row[i] * vector[i] for i in range(3)) for row in matrix)


def simulate(hex_color: str, kind: str) -> str:
    lms = _apply(_TO_LMS, linear_rgb(hex_color))
    long, medium, short = lms
    if kind == "protan":
        lms = (2.02344 * medium - 2.52581 * short, medium, short)
    elif kind == "deutan":
        lms = (long, 0.494207 * long + 1.24827 * short, short)
    elif kind == "tritan":
        lms = (long, medium, -0.395913 * long + 0.801109 * medium)
    channels = _apply(_FROM_LMS, lms)
    return "#" + "".join(f"{round(to_srgb(c) * 255):02x}" for c in channels)


# ------------------------------------------------------------------- reporting


class Report:
    def __init__(self) -> None:
        self.failures: list[str] = []

    def check(self, label: str, value: float, minimum: float, unit: str = "") -> None:
        ok = value >= minimum
        status = "pass" if ok else "FAIL"
        print(f"  [{status}] {label:<44} {value:6.2f}{unit} (min {minimum}{unit})")
        if not ok:
            self.failures.append(f"{label}: {value:.2f} < {minimum}")

    def note(self, label: str, value: float, unit: str = "") -> None:
        print(f"  [note] {label:<44} {value:6.2f}{unit}")


def audit(mode: str, palette, report: Report) -> None:
    print(f"\n=== {mode} ===")
    surface = palette.surface

    print("\n text and controls against their surface")
    for label, color, minimum in (
        ("primary ink", palette.ink, TEXT_MIN),
        ("secondary ink", palette.ink_secondary, TEXT_MIN),
        ("muted ink", palette.ink_muted, MARK_MIN),
        ("accent", palette.accent, MARK_MIN),
        ("button fill", palette.button, MARK_MIN),
        ("good", palette.good, MARK_MIN),
        ("critical", palette.critical, MARK_MIN),
    ):
        report.check(f"{label} vs surface", contrast(color, surface), minimum, ":1")

    report.check("button label on button fill",
                 contrast(palette.button_ink, palette.button), TEXT_MIN, ":1")

    print("\n adjacent categorical slots")
    slots = palette.series
    for index in range(len(slots) - 1):
        first, second = slots[index], slots[index + 1]
        pair = f"slot {index + 1} vs {index + 2}"
        report.check(f"{pair} normal vision", delta_e(first, second), NORMAL_MIN)
        for kind in ("protan", "deutan", "tritan"):
            report.check(f"{pair} {kind}",
                         delta_e(simulate(first, kind), simulate(second, kind)), CVD_MIN)

    print("\n sequential ramp endpoints")
    ramp = palette.sequential
    report.check("ramp start vs surface", contrast(ramp[0], surface), 2.0, ":1")
    report.note("ramp end vs surface", contrast(ramp[-1], surface), ":1")
    report.check("ramp start vs end", delta_e(ramp[0], ramp[-1]), 25.0)


def main() -> int:
    import theme

    report = Report()
    audit("light", theme.LIGHT, report)
    audit("dark", theme.DARK, report)

    print()
    if report.failures:
        print(f"{len(report.failures)} FAILED:")
        for failure in report.failures:
            print(f"  - {failure}")
        return 1
    print("all checks pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
