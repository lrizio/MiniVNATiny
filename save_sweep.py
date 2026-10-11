"""One-shot: sweep the VNA and save the raw complex data.

Usage:
    python save_sweep.py <label> [--mode reflection|transmission]
                          [--start HZ] [--stop HZ] [--points N]

Saves cal_data/<label>.json = {"frequencies_hz": [...], "gamma_re": [...], "gamma_im": [...]}
(the "gamma" key name is reused for both S11 and S21 raw complex data --
it's just "the raw complex sample," interpretation depends on the mode
used when it was captured.)

Non-interactive by design -- run it once per calibration standard (open,
short, load for reflection; thru, isolation for transmission) or DUT,
swapping the physical connection between invocations.
"""
import argparse
import json
from pathlib import Path

from minivna_tiny import MiniVNATiny

PORT = "COM3"
OUT_DIR = Path(__file__).parent / "cal_data"

DEFAULTS = {
    "reflection": dict(start=14_000_000, stop=14_350_000, points=11),
    "transmission": dict(start=10_000_000, stop=11_400_000, points=141),
}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("label", help="e.g. open / short / load / thru / isolation / dut")
    p.add_argument("--mode", choices=["reflection", "transmission"], default="reflection")
    p.add_argument("--start", type=int, default=None, help="start freq in Hz")
    p.add_argument("--stop", type=int, default=None, help="stop freq in Hz")
    p.add_argument("--points", type=int, default=None)
    args = p.parse_args()

    d = DEFAULTS[args.mode]
    start = args.start if args.start is not None else d["start"]
    stop = args.stop if args.stop is not None else d["stop"]
    points = args.points if args.points is not None else d["points"]

    with MiniVNATiny(PORT) as vna:
        if args.mode == "reflection":
            samples = vna.reflection_sweep(start, stop, points)
        else:
            samples = vna.transmission_sweep(start, stop, points)

    OUT_DIR.mkdir(exist_ok=True)
    out_path = OUT_DIR / f"{args.label}.json"
    data = {
        "mode": args.mode,
        "frequencies_hz": [s.frequency_hz for s in samples],
        "gamma_re": [s.gamma.real for s in samples],
        "gamma_im": [s.gamma.imag for s in samples],
    }
    out_path.write_text(json.dumps(data, indent=2))

    print(f"Saved {len(samples)} {args.mode} points to {out_path}")
    for s in samples:
        print(f"  {s.frequency_hz/1e6:8.4f} MHz  raw={s.gamma:.4f}  |raw|={abs(s.gamma):.4f}")


if __name__ == "__main__":
    main()
