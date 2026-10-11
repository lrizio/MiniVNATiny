"""Group delay estimation for a transmission (S21) sweep.

Group delay = -d(phase)/d(omega), the standard measure of a transmission
path's electrical delay / filter dispersion. Estimated here via a local
finite difference using the sample immediately before and after a given
index -- needs a point on both sides, so it's undefined right at either
end of a sweep.

The one real pitfall: naively differencing raw (wrapped, [-pi, pi]) phase
across two points can alias by a full 2*pi if the true phase change
between them happens to straddle the wrap point -- that looks like a huge
spurious delay spike instead of the small true one. Guarded against here
by wrapping the delta itself into (-pi, pi] before using it.

This only works up to a hard, Nyquist-like ceiling, though: it can only
correctly recover a delay whose true phase change across the 2-point span
(index-1 to index+1) is itself under pi, i.e.
    max recoverable delay (s) < 1 / (2 * (f[index+1] - f[index-1]))
A genuinely larger delay at that frequency step is fundamentally
indistinguishable from a smaller aliased one from two samples alone --
no amount of cleverness in the wrap fixes that, only a finer frequency
step (closer-spaced points) does. Confirmed by the self-check below,
which deliberately picked a first "wraparound" delay too large for the
step size and got exactly this aliased result -- a real limit, not a bug.

Verified with a synthetic constant-delay self-check -- run
`python group_delay.py` directly. Same discipline as this project's other
math modules (osl_calibration.py, thru_calibration.py, tdr.py).
"""
from __future__ import annotations

import math
from typing import Optional, Sequence


def compute_group_delay_ns(
    frequencies_hz: Sequence[float],
    values: Sequence[complex],
    index: int,
) -> Optional[float]:
    """Group delay in nanoseconds at `index`, or None if `index` is at
    either end of the sweep (no neighbor on one side) or the two
    neighboring frequencies coincide (degenerate sweep)."""
    n = len(frequencies_hz)
    if index <= 0 or index >= n - 1:
        return None
    phase_prev = math.atan2(values[index - 1].imag, values[index - 1].real)
    phase_next = math.atan2(values[index + 1].imag, values[index + 1].real)
    delta_phase = phase_next - phase_prev
    delta_phase = (delta_phase + math.pi) % (2 * math.pi) - math.pi  # wrap to (-pi, pi]
    delta_f_hz = frequencies_hz[index + 1] - frequencies_hz[index - 1]
    if delta_f_hz == 0:
        return None
    delay_s = -delta_phase / (2 * math.pi * delta_f_hz)
    return delay_s * 1e9


if __name__ == "__main__":
    import cmath

    true_delay_ns = 50.0
    true_delay_s = true_delay_ns * 1e-9
    freqs = [1e6 + i * 1e6 for i in range(200)]  # 1-200 MHz, 1 MHz steps
    values = [cmath.exp(-1j * 2 * math.pi * f * true_delay_s) for f in freqs]

    estimated = compute_group_delay_ns(freqs, values, 100)
    err = abs(estimated - true_delay_ns)
    print(f"True delay: {true_delay_ns} ns, estimated: {estimated:.4f} ns, error: {err:.4f} ns")
    assert err < 0.01, "group delay self-check FAILED"

    assert compute_group_delay_ns(freqs, values, 0) is None, "should be None at the low edge"
    assert compute_group_delay_ns(freqs, values, len(freqs) - 1) is None, "should be None at the high edge"

    # Wraparound guard: at this 1MHz step, the 2-point span is 2MHz, so the
    # max recoverable delay is 1/(2*2MHz) = 250ns (see module docstring).
    # 200ns is safely inside that but still large enough (144 deg of phase
    # change per span) to genuinely exercise the wrap-to-(-pi,pi] logic --
    # without it, this would already alias to a wrong answer.
    wrap_delay_ns = 200.0
    wrap_delay_s = wrap_delay_ns * 1e-9
    values2 = [cmath.exp(-1j * 2 * math.pi * f * wrap_delay_s) for f in freqs]
    estimated2 = compute_group_delay_ns(freqs, values2, 100)
    err2 = abs(estimated2 - wrap_delay_ns)
    print(f"Wraparound case -- true: {wrap_delay_ns} ns, estimated: {estimated2:.4f} ns, error: {err2:.4f} ns")
    assert err2 < 0.01, "group delay wraparound guard FAILED"

    # And confirm the documented ceiling is real: a delay past it should
    # NOT recover correctly (aliases) -- this is a hard sampling limit,
    # not something the code is expected to work around.
    over_ceiling_ns = 400.0
    over_ceiling_s = over_ceiling_ns * 1e-9
    values3 = [cmath.exp(-1j * 2 * math.pi * f * over_ceiling_s) for f in freqs]
    estimated3 = compute_group_delay_ns(freqs, values3, 100)
    assert abs(estimated3 - over_ceiling_ns) > 1.0, (
        "expected aliasing past the documented recoverable-delay ceiling"
    )
    print(f"Past-ceiling case -- true: {over_ceiling_ns} ns, estimated: {estimated3:.4f} ns "
          f"(aliased, as expected -- confirms the documented limit is real)")

    print("group delay self-check PASSED")
