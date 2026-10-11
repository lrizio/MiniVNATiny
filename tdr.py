"""Time-Domain Reflectometry (TDR) from a frequency-domain S11 sweep.

Turns a calibrated (or raw) reflection sweep into a distance-domain
response, useful for locating cable faults/discontinuities and measuring
electrical length. Pure computation, no GUI/hardware dependency.

Method: "bandpass" TDR (IFFT of the windowed, zero-padded complex Gamma(f)
array, taken as-is over the swept band). This deliberately does NOT
assume a DC (0 Hz) point, since this device's minimum frequency is 1 MHz
-- the alternative "lowpass" TDR method extrapolates a fake DC value
(usually by holding the first sweep point constant down to 0 Hz) to get a
real-valued step response; bandpass mode skips that assumption and is the
simpler, more honest option when you don't have real low-frequency data.
Peak *positions* in time/distance are correct either way; only relative
peak shape/sign interpretation differs slightly between the two modes.

Uses numpy for the FFT -- matplotlib (already a hard dependency of this
project's GUI) pulls in numpy anyway, so this adds no new packaging
weight.

Verified with a synthetic self-check at the bottom of this file -- run
`python tdr.py` directly. Same discipline as osl_calibration.py's and
thru_calibration.py's self-checks: don't trust the math until it recovers
a known answer.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

import numpy as np

SPEED_OF_LIGHT_M_S = 299_792_458.0

WINDOWS = {
    "none": np.ones,
    "hamming": np.hamming,
    "hann": np.hanning,
    "blackman": np.blackman,
}


@dataclass
class TDRResult:
    time_s: "np.ndarray"
    distance_m: "np.ndarray"
    magnitude: "np.ndarray"  # |impulse response|, arbitrary units, peak-normalized to 1.0

    def peak_distance_m(self) -> float:
        """Distance of the LARGEST reflection (skipping the near-zero
        artifact right at t=0, which is normal for bandpass mode). This
        can be a farther, bigger discontinuity that masks a nearer,
        smaller one -- see first_significant_distance_m() for "distance to
        the first fault" instead."""
        # skip the first few samples to avoid the DC/near-zero-time artifact
        skip = max(1, len(self.magnitude) // 200)
        idx = skip + int(np.argmax(self.magnitude[skip:]))
        return float(self.distance_m[idx])

    def first_significant_distance_m(self, threshold: float = 0.2) -> float:
        """Distance of the FIRST (nearest) reflection whose normalized
        magnitude reaches at least `threshold` (fraction of the largest
        peak, which is always 1.0) -- closer to how dedicated cable-fault
        TDR instruments report "distance to first fault" than
        peak_distance_m()'s "distance to the single largest reflection".
        With one discontinuity the two agree; with several (e.g. a lossy
        connector before the true open/short at the cable end), this finds
        the nearest one that's still a real reflection rather than noise,
        while peak_distance_m() would report whichever is biggest.

        Scans for the first local maximum at/above threshold; if the
        threshold crossing runs off the end of the array without ever
        dipping back down (found a rising edge but not the top of it),
        falls back to the first sample that crosses the threshold at all.
        If nothing reaches the threshold, falls back to peak_distance_m().
        """
        skip = max(1, len(self.magnitude) // 200)
        mag = self.magnitude[skip:]
        if len(mag) < 3:
            return self.peak_distance_m()

        above = mag >= threshold
        rising = np.empty(len(mag), dtype=bool)
        rising[0] = True
        rising[1:] = mag[1:] >= mag[:-1]
        falling_or_flat = np.empty(len(mag), dtype=bool)
        falling_or_flat[-1] = True
        falling_or_flat[:-1] = mag[:-1] >= mag[1:]
        local_max = above & rising & falling_or_flat

        idxs = np.nonzero(local_max)[0]
        if len(idxs):
            return float(self.distance_m[skip + idxs[0]])

        idxs = np.nonzero(above)[0]
        if len(idxs):
            return float(self.distance_m[skip + idxs[0]])

        return self.peak_distance_m()


def compute_tdr(
    frequencies_hz: Sequence[float],
    gamma: Sequence[complex],
    velocity_factor: float = 0.66,
    window: str = "hamming",
    zero_pad_factor: int = 8,
) -> TDRResult:
    if len(frequencies_hz) != len(gamma):
        raise ValueError("frequencies_hz and gamma must be the same length")
    if len(frequencies_hz) < 4:
        raise ValueError("need at least 4 points for a meaningful TDR")
    if window not in WINDOWS:
        raise ValueError(f"unknown window {window!r}, choose from {list(WINDOWS)}")

    freqs = np.asarray(frequencies_hz, dtype=float)
    g = np.asarray(gamma, dtype=complex)
    n = len(freqs)

    w = WINDOWS[window](n)
    g_windowed = g * w

    n_fft = max(n * zero_pad_factor, n)
    padded = np.zeros(n_fft, dtype=complex)
    padded[:n] = g_windowed

    response = np.fft.ifft(padded)
    magnitude = np.abs(response)
    peak = magnitude.max()
    if peak > 0:
        magnitude = magnitude / peak

    df = (freqs[-1] - freqs[0]) / (n - 1)
    dt = 1.0 / (n_fft * df)
    time_s = np.arange(n_fft) * dt
    distance_m = time_s * SPEED_OF_LIGHT_M_S * velocity_factor / 2.0  # round-trip -> one-way distance

    return TDRResult(time_s=time_s, distance_m=distance_m, magnitude=magnitude)


if __name__ == "__main__":
    # Self-check: simulate an ideal lossless line of a KNOWN physical
    # length, terminated in an open (Gamma = +1 at the open, so the
    # measured Gamma(f) is a pure linear phase rotor from the round-trip
    # delay). Verify compute_tdr's peak lands back at that known length.
    vf = 0.66
    true_length_m = 12.3
    one_way_delay_s = true_length_m / (SPEED_OF_LIGHT_M_S * vf)
    round_trip_delay_s = 2 * one_way_delay_s

    freqs = np.linspace(1e6, 500e6, 2001)
    gamma = np.exp(-1j * 2 * math.pi * freqs * round_trip_delay_s)

    result = compute_tdr(freqs, gamma, velocity_factor=vf)
    peak_m = result.peak_distance_m()
    err_m = abs(peak_m - true_length_m)
    # zero-padded IFFT distance resolution -- allow a few resolution-cells
    # of slack rather than demanding exact-bin precision
    resolution_m = result.distance_m[1] - result.distance_m[0]
    print(f"True length: {true_length_m:.3f} m, TDR peak: {peak_m:.3f} m, "
          f"error: {err_m:.3f} m (grid resolution: {resolution_m:.3f} m)")
    assert err_m < 3 * resolution_m, "TDR self-check FAILED -- peak not where expected"
    print("TDR self-check PASSED")

    # Second self-check: two discontinuities -- a SMALLER one nearer, a
    # LARGER one farther out (e.g. a lossy connector before the true open
    # cable end). peak_distance_m() should find the bigger/farther one;
    # first_significant_distance_m() should find the nearer one instead.
    near_m, far_m = 3.0, 12.3
    near_delay_s = 2 * near_m / (SPEED_OF_LIGHT_M_S * vf)
    far_delay_s = 2 * far_m / (SPEED_OF_LIGHT_M_S * vf)
    gamma2 = (0.3 * np.exp(-1j * 2 * math.pi * freqs * near_delay_s)
              + 1.0 * np.exp(-1j * 2 * math.pi * freqs * far_delay_s))

    result2 = compute_tdr(freqs, gamma2, velocity_factor=vf)
    peak2_m = result2.peak_distance_m()
    first2_m = result2.first_significant_distance_m(threshold=0.2)
    resolution2_m = result2.distance_m[1] - result2.distance_m[0]
    print(f"Two-reflection check: near={near_m:.3f} m (small), far={far_m:.3f} m (large) -- "
          f"peak_distance_m()={peak2_m:.3f} m, first_significant_distance_m()={first2_m:.3f} m")
    assert abs(peak2_m - far_m) < 3 * resolution2_m, \
        "TDR self-check FAILED -- peak_distance_m() should find the LARGER far reflection"
    assert abs(first2_m - near_m) < 3 * resolution2_m, \
        "TDR self-check FAILED -- first_significant_distance_m() should find the NEARER reflection"
    print("Two-reflection self-check PASSED")
