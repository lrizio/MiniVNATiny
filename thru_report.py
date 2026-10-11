"""Build a ThruCalibration from cal_data/thru.json (+ optional
cal_data/isolation.json) and, if cal_data/dut.json exists (captured in
transmission mode), print calibrated insertion loss/phase for it.
Otherwise, sanity-checks by re-correcting the thru standard's own raw
data (should come back at ~0 dB, ~0 deg -- unity).

Companion to cal_report.py, which does the same thing for OSL/reflection.
"""
import json
from pathlib import Path

from thru_calibration import ThruCalibration, s21_to_db, s21_to_insertion_loss_db

DATA_DIR = Path(__file__).parent / "cal_data"


def load(label):
    d = json.loads((DATA_DIR / f"{label}.json").read_text())
    if d.get("mode", "transmission") != "transmission":
        raise ValueError(f"{label}.json was captured in '{d.get('mode')}' mode, expected 'transmission'")
    freqs = d["frequencies_hz"]
    s21 = [complex(re, im) for re, im in zip(d["gamma_re"], d["gamma_im"])]
    return freqs, s21


def print_table(title, freqs, s21_list):
    print(f"\n{title}")
    print(f"{'Freq (MHz)':>12} {'|S21|':>10} {'dB (signed)':>12} {'IL (dB)':>10} {'phase (deg)':>12}")
    for f, s in zip(freqs, s21_list):
        import cmath
        phase = cmath.phase(s) * 180 / 3.141592653589793
        print(f"{f/1e6:12.4f} {abs(s):10.4f} {s21_to_db(s):12.2f} "
              f"{s21_to_insertion_loss_db(s):10.2f} {phase:12.2f}")


def main():
    freqs, s21_thru = load("thru")
    s21_isolation = None
    iso_path = DATA_DIR / "isolation.json"
    if iso_path.exists():
        _, s21_isolation = load("isolation")
        print("Using isolation.json for leakage correction.")
    else:
        print("No cal_data/isolation.json -- assuming zero leakage (response-only calibration).")

    cal = ThruCalibration.from_measurements(freqs, s21_thru, s21_isolation)
    print("Thru calibration built from cal_data/thru.json"
          + (" + isolation.json" if s21_isolation else ""))

    dut_path = DATA_DIR / "dut.json"
    if dut_path.exists():
        _, s21_dut_raw = load("dut")
        s21_dut = cal.correct_sweep(s21_dut_raw)
        print_table("DUT (calibrated):", freqs, s21_dut)
    else:
        s21_thru_corrected = cal.correct_sweep(s21_thru)
        print_table(
            "No cal_data/dut.json yet -- sanity check: re-correcting the "
            "THRU standard's own raw data (should read ~0.00 dB, ~0 deg):",
            freqs, s21_thru_corrected,
        )


if __name__ == "__main__":
    main()
