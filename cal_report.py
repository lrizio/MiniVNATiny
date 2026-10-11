"""Build an OSLCalibration from cal_data/{open,short,load}.json and, if
cal_data/dut.json exists, print calibrated return loss/SWR/impedance for it.
Otherwise, sanity-checks the calibration by re-correcting the short
standard's own raw measurement (should come back very close to -1+0j).
"""
import json
from pathlib import Path

from osl_calibration import (
    OSLCalibration,
    gamma_to_return_loss_db,
    gamma_to_swr,
    gamma_to_impedance,
)

DATA_DIR = Path(__file__).parent / "cal_data"


def load(label):
    d = json.loads((DATA_DIR / f"{label}.json").read_text())
    freqs = d["frequencies_hz"]
    gammas = [complex(re, im) for re, im in zip(d["gamma_re"], d["gamma_im"])]
    return freqs, gammas


def print_table(title, freqs, gammas):
    print(f"\n{title}")
    print(f"{'Freq (MHz)':>12} {'|Gamma|':>10} {'RL (dB)':>10} {'SWR':>8} {'Z (ohm)':>22}")
    for f, g in zip(freqs, gammas):
        z = gamma_to_impedance(g)
        print(f"{f/1e6:12.4f} {abs(g):10.4f} {gamma_to_return_loss_db(g):10.2f} "
              f"{gamma_to_swr(g):8.2f} {z.real:8.2f}{z.imag:+7.2f}j")


def main():
    freqs, gamma_open = load("open")
    _, gamma_short = load("short")
    _, gamma_load = load("load")

    cal = OSLCalibration.from_measurements(freqs, gamma_open, gamma_short, gamma_load)
    print("Calibration built from cal_data/{open,short,load}.json")

    dut_path = DATA_DIR / "dut.json"
    if dut_path.exists():
        _, gamma_dut_raw = load("dut")
        gamma_dut = cal.correct_sweep(gamma_dut_raw)
        print_table("DUT (calibrated):", freqs, gamma_dut)
    else:
        # No DUT yet -- sanity check by re-correcting the short standard's
        # own raw measurement. A perfect calibration recovers Gamma=-1+0j
        # exactly (since Short was one of the calibration inputs); this
        # mainly confirms the pipeline end-to-end, not independent accuracy.
        gamma_short_corrected = cal.correct_sweep(gamma_short)
        print_table(
            "No cal_data/dut.json yet -- sanity check: re-correcting the "
            "SHORT standard's own raw data (should read ~ -1.0000 +0.0000j):",
            freqs, gamma_short_corrected,
        )


if __name__ == "__main__":
    main()
