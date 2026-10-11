"""US amateur radio band edges, for overlaying on frequency-axis plots.

General-class US allocations (ARRL band plan), HF + low VHF only, since
that's this device's useful range for typical antenna/filter work (1MHz-
3GHz covers far more than HF, but band overlays past 2m clutter more than
they help for this device's typical bench use -- easy to extend if wanted).
"""

# (name, start_hz, stop_hz)
US_AMATEUR_BANDS = [
    ("2200m", 135_700, 137_800),
    ("630m", 472_000, 479_000),
    ("160m", 1_800_000, 2_000_000),
    ("80m", 3_500_000, 4_000_000),
    ("60m", 5_330_500, 5_406_400),
    ("40m", 7_000_000, 7_300_000),
    ("30m", 10_100_000, 10_150_000),
    ("20m", 14_000_000, 14_350_000),
    ("17m", 18_068_000, 18_168_000),
    ("15m", 21_000_000, 21_450_000),
    ("12m", 24_890_000, 24_990_000),
    ("10m", 28_000_000, 29_700_000),
    ("6m", 50_000_000, 54_000_000),
    ("2m", 144_000_000, 148_000_000),
    ("1.25m", 219_000_000, 220_000_000),
    ("70cm", 420_000_000, 450_000_000),
]


def bands_overlapping(start_hz: float, stop_hz: float):
    """Bands (name, start_hz, stop_hz) that overlap the given sweep range,
    clipped to that range."""
    lo, hi = min(start_hz, stop_hz), max(start_hz, stop_hz)
    result = []
    for name, b_start, b_stop in US_AMATEUR_BANDS:
        overlap_start = max(lo, b_start)
        overlap_stop = min(hi, b_stop)
        if overlap_start < overlap_stop:
            result.append((name, overlap_start, overlap_stop))
    return result
