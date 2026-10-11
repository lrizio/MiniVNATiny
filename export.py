"""Export helpers for sweep data: Touchstone (.s1p) and CSV.

Pure functions, no GUI/hardware dependency -- usable standalone or from
gui_app.py.
"""
from __future__ import annotations

import csv
import math
from typing import Sequence


def export_touchstone_s1p(
    path: str,
    frequencies_hz: Sequence[float],
    gamma: Sequence[complex],
    z0: float = 50.0,
    comment: str = "",
) -> None:
    """Write a 1-port Touchstone file (S11 only) in Real/Imaginary format.

    Standard Touchstone v1 format:
        # <freq-unit> S RI R <z0>
        <freq> <re> <im>
        ...
    """
    if len(frequencies_hz) != len(gamma):
        raise ValueError("frequencies_hz and gamma must be the same length")
    with open(path, "w", newline="\n") as f:
        if comment:
            for line in comment.splitlines():
                f.write(f"! {line}\n")
        f.write(f"# MHz S RI R {z0:g}\n")
        for freq_hz, g in zip(frequencies_hz, gamma):
            f.write(f"{freq_hz / 1e6:.6f} {g.real:.6f} {g.imag:.6f}\n")


def export_csv(
    path: str,
    frequencies_hz: Sequence[float],
    values: Sequence[complex],
    label: str = "S",
) -> None:
    """Write freq/real/imag/magnitude/db/phase columns to CSV."""
    if len(frequencies_hz) != len(values):
        raise ValueError("frequencies_hz and values must be the same length")
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "frequency_hz", f"{label}_re", f"{label}_im",
            f"{label}_mag", f"{label}_db", f"{label}_phase_deg",
        ])
        for freq_hz, v in zip(frequencies_hz, values):
            mag = abs(v)
            db = 20.0 * math.log10(mag) if mag > 0 else float("-inf")
            phase = math.degrees(math.atan2(v.imag, v.real))
            w.writerow([freq_hz, v.real, v.imag, mag, db, phase])
