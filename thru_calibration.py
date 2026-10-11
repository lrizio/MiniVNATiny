"""
Thru (+ optional isolation) calibration for two-port transmission (S21)
measurements. Device-independent VNA math, same as osl_calibration.py --
applies to any transmission sweep, not specific to the miniVNA Tiny.

This is the standard "response" (thru-only) or "response and isolation"
(thru + isolation) 2-term transmission calibration, as used by real VNA
calibration wizards. It is a *different* model from OSL reflection
calibration (osl_calibration.py) -- do not apply that one to S21 data,
or this one to S11 data.

Error model
-----------
    S21_measured = E_L + E_T * S21_actual

    E_L = leakage/isolation error (system crosstalk that bypasses the DUT
          entirely -- present even with S21_actual = 0)
    E_T = transmission tracking error (the system's own frequency
          response when the DUT is an ideal, lossless thru: S21_actual = 1+0j)

Two-standard solve
-------------------
    Isolation standard (S21_actual assumed 0 -- e.g. nothing connected, or
    matched terminations on both ports so nothing gets through but the
    setup's own leakage):
        S21_m,isolation = E_L

    Thru standard (S21_actual assumed 1+0j -- a direct connection):
        S21_m,thru = E_L + E_T   =>   E_T = S21_m,thru - E_L

Correction (inverting the error model for S21_actual):
    S21_actual = (S21_measured - E_L) / E_T

If no isolation measurement is supplied, E_L defaults to 0 -- plain
"response-only" normalization, which is fine whenever system crosstalk is
small compared to the DUT's own signal level (true for most filters
outside their deepest stopband; less true right in a >40 dB notch, where
isolation calibration actually matters).

Verified with a random self-check at the bottom of this file -- run
`python thru_calibration.py` directly. (osl_calibration.py's first
derivation had a sign error caught by exactly this kind of check; same
discipline applied here before trusting the math.)
"""
from __future__ import annotations

import math
from bisect import bisect_right
from dataclasses import dataclass
from typing import Optional, Sequence

S21 = complex


def _interp_complex(x: float, x0: float, y0: S21, x1: float, y1: S21) -> S21:
    """Linear interpolation of a complex value between two frequency
    points, real and imaginary parts independently -- same technique and
    rationale as osl_calibration.py's _interp_complex (error terms are
    smooth functions of frequency, not resonant, so linear interpolation
    between calibrated points is the standard, well-behaved approach)."""
    if x1 == x0:
        return y0
    t = (x - x0) / (x1 - x0)
    return complex(y0.real + t * (y1.real - y0.real), y0.imag + t * (y1.imag - y0.imag))


@dataclass
class TransmissionErrorTerms:
    """The 2-term transmission error model at a single frequency."""
    leakage: S21    # E_L
    tracking: S21   # E_T

    def correct(self, s21_measured: S21) -> S21:
        if self.tracking == 0:
            return complex(float("nan"), float("nan"))
        return (s21_measured - self.leakage) / self.tracking


def solve_transmission_error_terms(
    s21_measured_thru: S21,
    s21_measured_isolation: S21 = 0 + 0j,
) -> TransmissionErrorTerms:
    """Solve the 2-term transmission error model from a Thru measurement
    (and, optionally, an Isolation measurement) at a single frequency."""
    e_l = s21_measured_isolation
    e_t = s21_measured_thru - e_l
    return TransmissionErrorTerms(leakage=e_l, tracking=e_t)


@dataclass
class ThruCalibration:
    """A full-sweep thru(+isolation) calibration: one error-term set per
    frequency point."""
    frequencies_hz: list[float]
    error_terms: list[TransmissionErrorTerms]

    @classmethod
    def from_measurements(
        cls,
        frequencies_hz: Sequence[float],
        s21_thru: Sequence[S21],
        s21_isolation: Sequence[S21] | None = None,
    ) -> "ThruCalibration":
        n = len(frequencies_hz)
        if len(s21_thru) != n:
            raise ValueError("frequencies_hz and s21_thru must be the same length")
        if s21_isolation is None:
            s21_isolation = [0 + 0j] * n
        elif len(s21_isolation) != n:
            raise ValueError("s21_isolation must be the same length as frequencies_hz")

        terms = [
            solve_transmission_error_terms(s21_thru[i], s21_isolation[i])
            for i in range(n)
        ]
        return cls(frequencies_hz=list(frequencies_hz), error_terms=terms)

    def correct(self, index: int, s21_measured: S21) -> S21:
        """Correct one raw measured S21 using the error terms at `index`
        (position into this calibration's own frequency list)."""
        return self.error_terms[index].correct(s21_measured)

    def error_terms_at(self, frequency_hz: float) -> TransmissionErrorTerms:
        """Error terms at an arbitrary frequency, linearly interpolated
        between the two nearest calibrated points, or clamped to the
        nearest endpoint outside the calibrated span -- same approach as
        OSLCalibration.error_terms_at, see its docstring for the
        reasoning. Assumes self.frequencies_hz is sorted ascending."""
        freqs = self.frequencies_hz
        if frequency_hz <= freqs[0]:
            return self.error_terms[0]
        if frequency_hz >= freqs[-1]:
            return self.error_terms[-1]
        hi = bisect_right(freqs, frequency_hz)
        lo = hi - 1
        f0, f1 = freqs[lo], freqs[hi]
        e0, e1 = self.error_terms[lo], self.error_terms[hi]
        return TransmissionErrorTerms(
            leakage=_interp_complex(frequency_hz, f0, e0.leakage, f1, e1.leakage),
            tracking=_interp_complex(frequency_hz, f0, e0.tracking, f1, e1.tracking),
        )

    def correct_sweep(
        self, s21_measured: Sequence[S21], frequencies_hz: Optional[Sequence[float]] = None
    ) -> list[S21]:
        """Correct a full sweep. If `frequencies_hz` is omitted (or
        exactly matches this calibration's own grid), corrects point-for-
        point directly, same as always. If given and the grid differs, the
        error terms are interpolated (see error_terms_at) onto
        `frequencies_hz` first -- see OSLCalibration.correct_sweep's
        docstring, same idea applied to the transmission model."""
        if frequencies_hz is None or list(frequencies_hz) == self.frequencies_hz:
            if len(s21_measured) != len(self.error_terms):
                raise ValueError(
                    f"sweep has {len(s21_measured)} points, calibration has "
                    f"{len(self.error_terms)} -- must use the same start/stop/num_points, "
                    f"or pass frequencies_hz to interpolate onto a different grid"
                )
            return [self.correct(i, s) for i, s in enumerate(s21_measured)]
        if len(s21_measured) != len(frequencies_hz):
            raise ValueError("s21_measured and frequencies_hz must be the same length")
        return [self.error_terms_at(f).correct(s) for f, s in zip(frequencies_hz, s21_measured)]


# -- convenience conversions from a (calibrated) S21 --------------------

def s21_to_db(s21: S21) -> float:
    """Signed dB: 0 = unity/thru, negative = loss, positive = gain. This
    is the usual VNA-trace convention (matches filter_scan.py's plot)."""
    mag = abs(s21)
    if mag <= 0:
        return float("-inf")
    return 20.0 * math.log10(mag)


def s21_to_insertion_loss_db(s21: S21) -> float:
    """Positive-for-loss convention, e.g. a datasheet's "insertion loss:
    3 dB". Just -s21_to_db(s21)."""
    return -s21_to_db(s21)


if __name__ == "__main__":
    import random

    random.seed(0)

    def forward(s21_actual: S21, e: TransmissionErrorTerms) -> S21:
        return e.leakage + e.tracking * s21_actual

    max_err = 0.0
    for trial in range(200):
        e_true = TransmissionErrorTerms(
            leakage=complex(random.uniform(-0.05, 0.05), random.uniform(-0.05, 0.05)),
            tracking=complex(random.uniform(0.5, 1.2), random.uniform(-0.2, 0.2)),
        )
        s21_m_thru = forward(1 + 0j, e_true)
        s21_m_iso = forward(0 + 0j, e_true)
        solved = solve_transmission_error_terms(s21_m_thru, s21_m_iso)

        s21_dut = complex(random.uniform(-1.0, 1.0), random.uniform(-1.0, 1.0))
        s21_m_dut = forward(s21_dut, e_true)
        recovered = solved.correct(s21_m_dut)
        err = abs(recovered - s21_dut)
        max_err = max(max_err, err)

    print(f"Self-check over 200 random trials: max recovery error = {max_err:.2e}")
    assert max_err < 1e-9, "thru calibration math self-check FAILED"
    print("Thru calibration math self-check PASSED")

    # Self-check for error-term interpolation, same idea as
    # osl_calibration.py's -- a smoothly frequency-varying error network
    # (not the constant network above, which would make interpolation
    # trivially exact) so linear interpolation has real, boundable error.
    def e_true_at(f, f_lo, f_hi):
        t = (f - f_lo) / (f_hi - f_lo)
        return TransmissionErrorTerms(
            leakage=complex(0.03 * math.sin(2 * math.pi * t), 0.02 * t),
            tracking=complex(0.9 - 0.15 * t, 0.1 * math.cos(math.pi * t)),
        )

    f_lo, f_hi = 1.0e6, 30.0e6
    cal_freqs = [f_lo + i * (f_hi - f_lo) / 20 for i in range(21)]
    cal_terms = [e_true_at(f, f_lo, f_hi) for f in cal_freqs]
    s21_thru = [forward(1 + 0j, e) for e in cal_terms]
    s21_iso = [forward(0 + 0j, e) for e in cal_terms]
    cal = ThruCalibration.from_measurements(cal_freqs, s21_thru, s21_iso)

    test_freqs = [f_lo - 2.0e6, f_lo + 0.3e6] + \
        [f_lo + i * (f_hi - f_lo) / 47 + 0.11e6 for i in range(48)] + [f_hi + 2.0e6]
    max_interp_err = 0.0
    for f in test_freqs:
        f_clamped = min(max(f, f_lo), f_hi)
        e_true = e_true_at(f_clamped, f_lo, f_hi)
        s21_dut = complex(0.5, -0.2)
        s21_m_dut = forward(s21_dut, e_true)
        recovered = cal.correct_sweep([s21_m_dut], [f])[0]
        err = abs(recovered - s21_dut)
        if f_lo <= f <= f_hi:
            max_interp_err = max(max_interp_err, err)
        assert math.isfinite(recovered.real) and math.isfinite(recovered.imag), \
            f"interpolated/extrapolated correction produced a non-finite result at {f/1e6:.3f} MHz"

    print(f"Interpolation self-check: max in-span recovery error over {len(test_freqs)} "
          f"off-grid points (21-pt coarse calibration) = {max_interp_err:.2e}")
    assert max_interp_err < 0.01, "thru error-term interpolation self-check FAILED (error too large)"

    s21_dut_grid = [forward(complex(0.5, -0.2), e) for e in cal_terms]
    via_default = cal.correct_sweep(s21_dut_grid)
    via_explicit_same_grid = cal.correct_sweep(s21_dut_grid, cal_freqs)
    assert via_default == via_explicit_same_grid, \
        "passing the calibration's own grid explicitly must match the omitted-argument path exactly"
    print("Exact-grid correct_sweep() path unaffected by the new interpolation code PASSED")
