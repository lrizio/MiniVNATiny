"""
Open-Short-Load (OSL) one-port vector error correction.

This is standard VNA calibration math, independent of the miniVNA Tiny --
the same algorithm applies to any reflectometer. It corrects for the three
systematic error terms in a one-port measurement (directivity, source
match, reflection tracking) using measurements of three known calibration
standards.

Error model
-----------
    Gamma_measured = E_D + E_R * Gamma_actual / (1 - E_S * Gamma_actual)

    E_D = directivity error
    E_S = source match error
    E_R = reflection tracking error (= e10*e01 in some texts' notation)

Solving for the 3 unknowns
---------------------------
Rearranging the error-model equation for one standard `i`:

    Gamma_m,i * (1 - E_S * Gamma_a,i) = E_D * (1 - E_S * Gamma_a,i) + E_R * Gamma_a,i
    Gamma_m,i = E_D + Gamma_a,i * (E_R - E_D * E_S) + E_S * Gamma_a,i * Gamma_m,i

Substituting a = E_D, b = E_S, d = E_R - E_D*E_S, this is now LINEAR in
(a, b, d):

    Gamma_m,i = a + d * Gamma_a,i + b * (Gamma_a,i * Gamma_m,i)

Three standards (Open, Short, Load) give three linear equations, solved
here with a plain Cramer's-rule 3x3 complex solve (no numpy dependency).
Then E_R = d + a*b. (Verified against a 200-trial random self-check at
the bottom of this file -- run `python osl_calibration.py` directly.)

This form handles non-ideal standards too (e.g. an Open's fringing
capacitance or a Short's series inductance, if your calibration kit's
real coefficients are known) -- see `open_with_fringing_capacitance()`
and `short_with_series_inductance()`. Default is the common assumption
of ideal standards: Gamma_open=+1, Gamma_short=-1, Gamma_load=0.

Sources: standard 3-term one-port error-correction theory (as used by
every commercial VNA); the ideal-standard convention and the overall
raw-standards -> corrected-calibration-block -> corrected-scan-data flow
is also described for this exact instrument in vna/J's driver guide PDF
("Measurement basics" section, see reference/DriverGuide_miniVNAtiny.pdf).
"""
from __future__ import annotations

import cmath
import math
from bisect import bisect_right
from dataclasses import dataclass
from typing import Callable, Optional, Sequence

Gamma = complex
StandardFn = Callable[[float], Gamma]  # frequency_hz -> ideal Gamma at that frequency


def ideal_open(frequency_hz: float) -> Gamma:
    return 1.0 + 0j


def ideal_short(frequency_hz: float) -> Gamma:
    return -1.0 + 0j


def ideal_load(frequency_hz: float) -> Gamma:
    return 0.0 + 0j


def open_with_fringing_capacitance(
    c0: float, c1: float = 0.0, c2: float = 0.0, c3: float = 0.0, z0: float = 50.0
) -> StandardFn:
    """Frequency-dependent Open standard model, as used in real cal-kit
    datasheets:  C(f) = c0 + c1*f + c2*f^2 + c3*f^3  (f in Hz, C in farads)
    Gamma_open(f) = (1 - j*w*C*Z0) / (1 + j*w*C*Z0)
    """
    def fn(frequency_hz: float) -> Gamma:
        w = 2 * math.pi * frequency_hz
        c = c0 + c1 * frequency_hz + c2 * frequency_hz**2 + c3 * frequency_hz**3
        jwcz = 1j * w * c * z0
        return (1 - jwcz) / (1 + jwcz)
    return fn


def short_with_series_inductance(
    l0: float, l1: float = 0.0, l2: float = 0.0, l3: float = 0.0, z0: float = 50.0
) -> StandardFn:
    """Frequency-dependent Short standard model:
    L(f) = l0 + l1*f + l2*f^2 + l3*f^3  (f in Hz, L in henries)
    Gamma_short(f) = (j*w*L - Z0) / (j*w*L + Z0)
    """
    def fn(frequency_hz: float) -> Gamma:
        w = 2 * math.pi * frequency_hz
        l = l0 + l1 * frequency_hz + l2 * frequency_hz**2 + l3 * frequency_hz**3
        jwl = 1j * w * l
        return (jwl - z0) / (jwl + z0)
    return fn


def _solve3(m: list[list[Gamma]], rhs: list[Gamma]) -> list[Gamma]:
    """Cramer's-rule solve of a 3x3 complex linear system m @ x = rhs."""
    def det3(a):
        return (a[0][0] * (a[1][1] * a[2][2] - a[1][2] * a[2][1])
                - a[0][1] * (a[1][0] * a[2][2] - a[1][2] * a[2][0])
                + a[0][2] * (a[1][0] * a[2][1] - a[1][1] * a[2][0]))

    d = det3(m)
    if abs(d) < 1e-18:
        raise ValueError(
            "singular calibration system -- standards aren't independent "
            "enough at this frequency (duplicate/degenerate measurements?)"
        )
    result = []
    for col in range(3):
        mi = [row[:] for row in m]
        for r in range(3):
            mi[r][col] = rhs[r]
        result.append(det3(mi) / d)
    return result


def _interp_complex(x: float, x0: float, y0: Gamma, x1: float, y1: Gamma) -> Gamma:
    """Linear interpolation of a complex value between two frequency
    points, real and imaginary parts independently. Error terms are
    smooth, slowly-varying functions of frequency (they describe fixed
    hardware imperfections -- directivity, source match, tracking -- not
    anything resonant), so this is the standard technique real VNA
    calibration-interpolation uses to apply a calibration measured at one
    set of frequency points to a sweep at a different one."""
    if x1 == x0:
        return y0
    t = (x - x0) / (x1 - x0)
    return complex(y0.real + t * (y1.real - y0.real), y0.imag + t * (y1.imag - y0.imag))


@dataclass
class ErrorTerms:
    """The 3-term one-port error model, solved at a single frequency."""
    directivity: Gamma           # E_D
    source_match: Gamma          # E_S
    reflection_tracking: Gamma   # E_R

    def correct(self, gamma_measured: Gamma) -> Gamma:
        num = gamma_measured - self.directivity
        denom = self.reflection_tracking + self.source_match * num
        if denom == 0:
            return complex(float("nan"), float("nan"))
        return num / denom


def solve_error_terms(
    gamma_measured_open: Gamma,
    gamma_measured_short: Gamma,
    gamma_measured_load: Gamma,
    gamma_ideal_open: Gamma = 1.0,
    gamma_ideal_short: Gamma = -1.0,
    gamma_ideal_load: Gamma = 0.0,
) -> ErrorTerms:
    """Solve the 3-term error model from one Open/Short/Load measurement
    triple at a single frequency, given the standards' true (possibly
    non-ideal) reflection coefficients."""
    rows: list[list[Gamma]] = []
    rhs: list[Gamma] = []
    for gm, ga in (
        (gamma_measured_open, gamma_ideal_open),
        (gamma_measured_short, gamma_ideal_short),
        (gamma_measured_load, gamma_ideal_load),
    ):
        # gm = a + d*ga + b*(ga*gm)  -- see module docstring derivation
        rows.append([1.0 + 0j, (ga * gm), ga])
        rhs.append(gm)
    a, b, d = _solve3(rows, rhs)
    return ErrorTerms(directivity=a, source_match=b, reflection_tracking=d + a * b)


@dataclass
class OSLCalibration:
    """A full-sweep OSL calibration: one ErrorTerms set per frequency point."""
    frequencies_hz: list[float]
    error_terms: list[ErrorTerms]

    @classmethod
    def from_measurements(
        cls,
        frequencies_hz: Sequence[float],
        gamma_open: Sequence[Gamma],
        gamma_short: Sequence[Gamma],
        gamma_load: Sequence[Gamma],
        standard_open: StandardFn = ideal_open,
        standard_short: StandardFn = ideal_short,
        standard_load: StandardFn = ideal_load,
    ) -> "OSLCalibration":
        n = len(frequencies_hz)
        if not (len(gamma_open) == len(gamma_short) == len(gamma_load) == n):
            raise ValueError("frequency/open/short/load arrays must be the same length")
        terms = [
            solve_error_terms(
                gamma_open[i], gamma_short[i], gamma_load[i],
                standard_open(frequencies_hz[i]),
                standard_short(frequencies_hz[i]),
                standard_load(frequencies_hz[i]),
            )
            for i in range(n)
        ]
        return cls(frequencies_hz=list(frequencies_hz), error_terms=terms)

    def correct(self, index: int, gamma_measured: Gamma) -> Gamma:
        """Correct one raw measured Gamma using the error terms at `index`
        (position into this calibration's own frequency list)."""
        return self.error_terms[index].correct(gamma_measured)

    def error_terms_at(self, frequency_hz: float) -> ErrorTerms:
        """Error terms at an arbitrary frequency, linearly interpolated
        between the two nearest calibrated points. Outside the calibrated
        span, holds the nearest endpoint's terms constant (clamped
        extrapolation) rather than extrapolating the line -- a held-flat
        guess degrades gracefully, where linear extrapolation can swing
        arbitrarily far from reality past the last measured point.
        Assumes self.frequencies_hz is sorted ascending, true for any
        calibration built from a normal start<stop sweep."""
        freqs = self.frequencies_hz
        if frequency_hz <= freqs[0]:
            return self.error_terms[0]
        if frequency_hz >= freqs[-1]:
            return self.error_terms[-1]
        hi = bisect_right(freqs, frequency_hz)
        lo = hi - 1
        f0, f1 = freqs[lo], freqs[hi]
        e0, e1 = self.error_terms[lo], self.error_terms[hi]
        return ErrorTerms(
            directivity=_interp_complex(frequency_hz, f0, e0.directivity, f1, e1.directivity),
            source_match=_interp_complex(frequency_hz, f0, e0.source_match, f1, e1.source_match),
            reflection_tracking=_interp_complex(
                frequency_hz, f0, e0.reflection_tracking, f1, e1.reflection_tracking),
        )

    def correct_sweep(
        self, gamma_measured: Sequence[Gamma], frequencies_hz: Optional[Sequence[float]] = None
    ) -> list[Gamma]:
        """Correct a full sweep. If `frequencies_hz` is omitted (or
        exactly matches this calibration's own grid), corrects point-for-
        point directly, same as always. If given and the grid differs --
        different Start/Stop/Points than the calibration -- the error
        terms are interpolated (see error_terms_at) onto `frequencies_hz`
        first, so a calibration remains usable across a changed sweep
        range/resolution instead of requiring an exact re-match."""
        if frequencies_hz is None or list(frequencies_hz) == self.frequencies_hz:
            if len(gamma_measured) != len(self.error_terms):
                raise ValueError(
                    f"sweep has {len(gamma_measured)} points, calibration has "
                    f"{len(self.error_terms)} -- must use the same start/stop/num_points, "
                    f"or pass frequencies_hz to interpolate onto a different grid"
                )
            return [self.correct(i, g) for i, g in enumerate(gamma_measured)]
        if len(gamma_measured) != len(frequencies_hz):
            raise ValueError("gamma_measured and frequencies_hz must be the same length")
        return [self.error_terms_at(f).correct(g) for f, g in zip(frequencies_hz, gamma_measured)]


# -- convenience conversions from a (calibrated) Gamma -----------------

def gamma_to_return_loss_db(gamma: Gamma) -> float:
    """Positive dB, e.g. 20 dB return loss = well matched."""
    mag = abs(gamma)
    if mag <= 0:
        return float("inf")
    return -20.0 * math.log10(mag)


def gamma_to_swr(gamma: Gamma) -> float:
    mag = abs(gamma)
    if mag >= 1.0 - 1e-9:  # treat as a perfect short/open; avoids float-noise blowups
        return float("inf")
    return (1 + mag) / (1 - mag)


def gamma_to_impedance(gamma: Gamma, z0: float = 50.0) -> complex:
    if gamma == 1:
        return complex(float("inf"), 0.0)
    return z0 * (1 + gamma) / (1 - gamma)


def gamma_to_phase_deg(gamma: Gamma) -> float:
    return math.degrees(math.atan2(gamma.imag, gamma.real))


def gamma_to_admittance(gamma: Gamma, z0: float = 50.0) -> complex:
    """Y = 1/Z = G + jB (siemens) -- the Smith chart's dual, useful for
    matching-network work (shunt elements add directly in admittance)."""
    z = gamma_to_impedance(gamma, z0)
    if z == 0:
        return complex(float("inf"), 0.0)
    return 1.0 / z


def gamma_to_q_factor(gamma: Gamma, z0: float = 50.0) -> float:
    """Single-point Q = |X|/R, the same convention most antenna analyzers
    display (not the bandwidth-derived loaded/unloaded Q of a resonator --
    that needs the whole trace, not one point)."""
    z = gamma_to_impedance(gamma, z0)
    if z.real == 0:
        return float("inf")
    return abs(z.imag) / z.real


def gamma_to_mismatch_loss_db(gamma: Gamma) -> float:
    """Power actually lost to reflection, in dB -- distinct from return
    loss (which measures the reflected/incident ratio, not delivered
    power): -10*log10(1 - |Gamma|^2). 0 dB at a perfect match, -> infinity
    as |Gamma| -> 1 (nothing delivered)."""
    mag2 = abs(gamma) ** 2
    if mag2 >= 1.0 - 1e-9:
        return float("inf")
    return -10.0 * math.log10(1.0 - mag2)


if __name__ == "__main__":
    # Self-check: forward-model a sweep through an arbitrary (nontrivial)
    # error network, solve for the error terms from O/S/L, then verify we
    # recover the original actual Gamma of a synthetic "DUT" measurement.
    import random

    random.seed(0)

    def forward(gamma_actual: Gamma, e: ErrorTerms) -> Gamma:
        return e.directivity + e.reflection_tracking * gamma_actual / (
            1 - e.source_match * gamma_actual
        )

    max_err = 0.0
    for trial in range(200):
        e_true = ErrorTerms(
            directivity=complex(random.uniform(-0.1, 0.1), random.uniform(-0.1, 0.1)),
            source_match=complex(random.uniform(-0.3, 0.3), random.uniform(-0.3, 0.3)),
            reflection_tracking=complex(random.uniform(0.7, 1.0), random.uniform(-0.1, 0.1)),
        )
        gm_open = forward(ideal_open(0), e_true)
        gm_short = forward(ideal_short(0), e_true)
        gm_load = forward(ideal_load(0), e_true)
        solved = solve_error_terms(gm_open, gm_short, gm_load)

        gamma_dut = complex(random.uniform(-0.9, 0.9), random.uniform(-0.9, 0.9))
        gm_dut = forward(gamma_dut, e_true)
        recovered = solved.correct(gm_dut)
        err = abs(recovered - gamma_dut)
        max_err = max(max_err, err)

    print(f"Self-check over 200 random trials: max recovery error = {max_err:.2e}")
    assert max_err < 1e-9, "OSL math self-check FAILED"
    print("OSL calibration math self-check PASSED")

    # Self-check for error-term interpolation (correct_sweep with a
    # frequencies_hz different from the calibration's own grid). Uses a
    # SMOOTHLY FREQUENCY-VARYING error network (not the constant-error
    # network above -- interpolating a constant would trivially be exact
    # and wouldn't actually test the interpolation math) so linear
    # interpolation between calibrated points has genuine, boundable error
    # to check against.
    def e_true_at(f, f_lo, f_hi):
        t = (f - f_lo) / (f_hi - f_lo)
        return ErrorTerms(
            directivity=complex(0.05 * math.sin(2 * math.pi * t), 0.03 * t),
            source_match=complex(0.2 * t, 0.1 * math.cos(math.pi * t)),
            reflection_tracking=complex(0.9 - 0.1 * t, 0.05 * math.sin(math.pi * t)),
        )

    f_lo, f_hi = 1.0e6, 30.0e6
    cal_freqs = [f_lo + i * (f_hi - f_lo) / 20 for i in range(21)]  # 21 pts, coarse on purpose
    cal_terms = [e_true_at(f, f_lo, f_hi) for f in cal_freqs]
    gm_open = [forward(ideal_open(f), e) for f, e in zip(cal_freqs, cal_terms)]
    gm_short = [forward(ideal_short(f), e) for f, e in zip(cal_freqs, cal_terms)]
    gm_load = [forward(ideal_load(f), e) for f, e in zip(cal_freqs, cal_terms)]
    cal = OSLCalibration.from_measurements(cal_freqs, gm_open, gm_short, gm_load)

    # Test at a DIFFERENT, finer grid, offset from the calibration's own
    # points -- exercises the actual interpolation path, plus two points
    # beyond the calibrated span to exercise clamped extrapolation.
    test_freqs = [f_lo - 2.0e6, f_lo + 0.3e6] + \
        [f_lo + i * (f_hi - f_lo) / 47 + 0.11e6 for i in range(48)] + [f_hi + 2.0e6]
    max_interp_err = 0.0
    for f in test_freqs:
        f_clamped = min(max(f, f_lo), f_hi)  # true model isn't defined past the span either
        e_true = e_true_at(f_clamped, f_lo, f_hi)
        gamma_dut = complex(0.3, -0.4)
        gm_dut = forward(gamma_dut, e_true)
        recovered = cal.correct_sweep([gm_dut], [f])[0]
        err = abs(recovered - gamma_dut)
        if f_lo <= f <= f_hi:  # only bound the error where interpolation (not extrapolation) applies
            max_interp_err = max(max_interp_err, err)
        assert math.isfinite(recovered.real) and math.isfinite(recovered.imag), \
            f"interpolated/extrapolated correction produced a non-finite result at {f/1e6:.3f} MHz"

    print(f"Interpolation self-check: max in-span recovery error over {len(test_freqs)} "
          f"off-grid points (21-pt coarse calibration) = {max_interp_err:.2e}")
    assert max_interp_err < 0.01, "OSL error-term interpolation self-check FAILED (error too large)"

    # Exact-grid path must still be untouched (frequencies_hz omitted, or
    # passed but identical to the calibration's own grid) -- both should
    # give the same bit-for-bit result as the original point-for-point path.
    gm_dut_grid = [forward(complex(0.3, -0.4), e) for e in cal_terms]
    via_default = cal.correct_sweep(gm_dut_grid)
    via_explicit_same_grid = cal.correct_sweep(gm_dut_grid, cal_freqs)
    assert via_default == via_explicit_same_grid, \
        "passing the calibration's own grid explicitly must match the omitted-argument path exactly"
    print("Exact-grid correct_sweep() path unaffected by the new interpolation code PASSED")

    # Sanity check for the newer derived-parameter helpers, against known
    # closed-form values at gamma=0 (perfect 50-ohm match).
    z = gamma_to_impedance(0j)
    assert abs(z - 50) < 1e-9, f"Z at gamma=0 should be 50+0j, got {z}"
    y = gamma_to_admittance(0j)
    assert abs(y - 0.02) < 1e-9, f"Y at gamma=0 should be 0.02+0j S, got {y}"
    assert gamma_to_q_factor(0j) == 0.0, "Q at gamma=0 (X=0) should be 0"
    assert abs(gamma_to_mismatch_loss_db(0j) - 0.0) < 1e-9, "mismatch loss at gamma=0 should be 0 dB"
    assert abs(gamma_to_phase_deg(1j) - 90.0) < 1e-9, "phase of +j should be 90 deg"
    print("Derived-parameter helper sanity checks PASSED")
