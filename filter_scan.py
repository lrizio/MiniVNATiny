"""Uncalibrated transmission (S21) sweep -- for looking at a filter's raw
passband shape. No thru-normalization applied yet (see README/session notes
on why the reflection OSL cal doesn't apply to a transmission measurement).
"""
from minivna_tiny import MiniVNATiny
from osl_calibration import gamma_to_return_loss_db

PORT = "COM3"
START_HZ = 10_000_000
STOP_HZ = 11_400_000
NUM_POINTS = 141  # 10 kHz steps

with MiniVNATiny(PORT) as vna:
    samples = vna.transmission_sweep(START_HZ, STOP_HZ, NUM_POINTS)

print(f"{'Freq (MHz)':>12} {'|S21| raw':>10} {'rel dB':>10}  bar")
peak = max(abs(s.gamma) for s in samples) or 1e-12
for s in samples:
    mag = abs(s.gamma)
    rel_db = gamma_to_return_loss_db(mag / peak) * -1  # 0 dB at peak, negative going down
    bar_len = max(0, int((mag / peak) * 50))
    print(f"{s.frequency_hz/1e6:12.4f} {mag:10.4f} {rel_db:10.2f}  {'#' * bar_len}")
