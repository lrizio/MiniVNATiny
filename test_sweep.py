"""Quick smoke test against the real hardware on COM3."""
from minivna_tiny import MiniVNATiny

PORT = "COM3"

with MiniVNATiny(PORT) as vna:
    print("Firmware:", vna.firmware_version())
    print("Supply raw (scale factor unconfirmed, see docstring):", vna.supply_voltage_raw())
    print("Temperature (C):", vna.temperature_c())

    print("\nReflection sweep 14.000-14.350 MHz, 11 points:")
    samples = vna.reflection_sweep(14_000_000, 14_350_000, 11)
    for s in samples:
        print(f"  {s.frequency_hz/1e6:8.4f} MHz  |gamma|={abs(s.gamma):.4f}  "
              f"loss={s.loss_db:7.2f} dB  phase={s.phase_deg:7.2f} deg")
