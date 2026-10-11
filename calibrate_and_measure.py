"""
Interactive OSL calibration + calibrated DUT measurement against the real
miniVNA Tiny.

Walks through: connect Open -> connect Short -> connect Load -> connect
your device under test (antenna, filter, whatever's on the TX port).
Each stage does one raw reflection sweep over the same frequency range,
so the calibration and the DUT sweep are always point-for-point aligned.
"""
from minivna_tiny import MiniVNATiny
from osl_calibration import (
    OSLCalibration,
    gamma_to_return_loss_db,
    gamma_to_swr,
    gamma_to_impedance,
)

PORT = "COM3"
START_HZ = 14_000_000
STOP_HZ = 14_350_000
NUM_POINTS = 11


def sweep_gammas(vna: MiniVNATiny):
    samples = vna.reflection_sweep(START_HZ, STOP_HZ, NUM_POINTS)
    freqs = [s.frequency_hz for s in samples]
    gammas = [s.gamma for s in samples]
    return freqs, gammas


def main():
    with MiniVNATiny(PORT) as vna:
        print("Firmware:", vna.firmware_version())

        input("Connect OPEN to the TX port, then press Enter...")
        freqs, gamma_open = sweep_gammas(vna)

        input("Connect SHORT to the TX port, then press Enter...")
        _, gamma_short = sweep_gammas(vna)

        input("Connect LOAD (50 ohm) to the TX port, then press Enter...")
        _, gamma_load = sweep_gammas(vna)

        cal = OSLCalibration.from_measurements(freqs, gamma_open, gamma_short, gamma_load)
        print("\nCalibration complete.\n")

        input("Connect your DUT to the TX port, then press Enter...")
        _, gamma_dut_raw = sweep_gammas(vna)
        gamma_dut = cal.correct_sweep(gamma_dut_raw)

        print(f"\n{'Freq (MHz)':>12} {'|Gamma|':>10} {'RL (dB)':>10} {'SWR':>8} {'Z (ohm)':>22}")
        for f, g in zip(freqs, gamma_dut):
            z = gamma_to_impedance(g)
            print(f"{f/1e6:12.4f} {abs(g):10.4f} {gamma_to_return_loss_db(g):10.2f} "
                  f"{gamma_to_swr(g):8.2f} {z.real:8.2f}{z.imag:+7.2f}j")


if __name__ == "__main__":
    main()
