"""
Driver for the mRS (mini Radio Solutions) miniVNA Tiny.

The manufacturer no longer supports this device, so this protocol was
reverse-engineered (not vendor-documented) by:
  1. Decompiling the GPL vna/J driver (krause/vna/device/serial/tiny/*)
     from https://github.com/ohleck/vna-J
  2. Cross-checking against an independent working implementation,
     https://github.com/zzzzhuzhuo/pyvna (device_tiny.py)
  3. Live-verifying the firmware/supply/temperature queries against the
     real device on COM3

See the README's Protocol section for the summary. This file is a clean-room
reimplementation from the recovered protocol facts, not a copy of either reference.
Wire protocol summary
----------------------
- 921600 baud, 8 data bits, 1 stop bit, no parity.
- Every command is ASCII text followed by a single '\\r' (no '\\n').
- Text responses come back '\\r\\n' terminated.
- Binary responses (voltage, temperature, sweep samples) are fixed-length
  raw byte reads with no framing.

Known commands:
    '9'   -> firmware version, ASCII line back (e.g. "FW Tiny V1.0 - 22 June 2014")
    '8'   -> supply voltage, 2 raw bytes back (uint16 LE, /1024.0 scaled)
    '10'  -> device temperature, 2 raw bytes back (uint16 LE, /10.0 -> deg C)
    '6'   -> start a sweep in TRANSMISSION mode
    '7'   -> start a sweep in REFLECTION mode

Sweep sequence:
    send mode command ('6' or '7')
    send str(start_hz // PRESCALER)
    send str(stop_hz // PRESCALER)
    send str(num_points)
    send ''                                  # blank line = trigger
    for each of num_points:
        read 12 raw bytes -> four little-endian 3-byte (24-bit) unsigned
        ints x1,x2,x3,x4; complex sample = ((x1-x3) + j*(x2-x4)) / 2**24
    send '7', '0', '0', '1', ''               # end-of-sweep reset
    read and discard one more 12-byte sample
"""
from __future__ import annotations

import struct
import time
from dataclasses import dataclass

import serial

BAUDRATE = 921600
PRESCALER = 10
NUM_BYTES_PER_SAMPLE = 12
MIN_FREQ_HZ = 1_000_000
MAX_FREQ_HZ = 3_000_000_000
DEFAULT_TIMEOUT_S = 5.0

CMD_FIRMWARE = "9"
CMD_SUPPLY = "8"
CMD_TEMPERATURE = "10"
CMD_TRANSMISSION = "6"
CMD_REFLECTION = "7"


def _write_line(port: serial.Serial, value=""):
    """Send `value` as ASCII text terminated by a single CR (no LF)."""
    port.write(str(value).encode("ascii") + b"\r")


def _read_line(port: serial.Serial) -> str:
    """Read one CR/LF-terminated ASCII line (blocking, per port timeout)."""
    raw = port.readline()
    return raw.decode("ascii", errors="replace").strip()


def _read_uint16_le(port: serial.Serial) -> int:
    raw = port.read(2)
    if len(raw) != 2:
        raise TimeoutError(f"expected 2 bytes, got {len(raw)!r}")
    return struct.unpack("<H", raw)[0]


def _read_uint24_le(port: serial.Serial) -> int:
    raw = port.read(3)
    if len(raw) != 3:
        raise TimeoutError(f"expected 3 bytes, got {len(raw)!r}")
    return struct.unpack("<I", raw + b"\x00")[0]


@dataclass
class Sample:
    frequency_hz: int
    gamma: complex  # normalized complex ratio, |gamma| <= ~1 for a good match

    @property
    def loss_db(self) -> float:
        mag = abs(self.gamma)
        if mag <= 0:
            return float("-inf")
        import math
        return 20.0 * math.log10(mag)

    @property
    def phase_deg(self) -> float:
        import math
        return math.degrees(math.atan2(self.gamma.imag, self.gamma.real))


class MiniVNATiny:
    """Serial driver for the miniVNA Tiny."""

    def __init__(self, port: str, timeout: float = DEFAULT_TIMEOUT_S):
        self.port_name = port
        self.ser = serial.Serial(
            port,
            BAUDRATE,
            bytesize=8,
            parity="N",
            stopbits=1,
            timeout=timeout,
        )
        time.sleep(0.2)
        self.ser.reset_input_buffer()

    def close(self):
        self.ser.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # -- simple queries ----------------------------------------------

    def firmware_version(self) -> str:
        self.ser.reset_input_buffer()
        _write_line(self.ser, CMD_FIRMWARE)
        return _read_line(self.ser)

    def supply_voltage_raw(self) -> int:
        """Raw 2-byte ADC-style reading from the '8' command, unscaled.

        NOTE: the true volts-per-count scale factor is NOT confirmed. The
        decompiled vna/J bytecode divides by 1024.0 (consistent with a
        10-bit ADC), but my bytecode tracer only sees literal constants
        immediately preceding a call and cannot see arithmetic done in
        between -- there is very likely a missing multiplier (e.g. a
        voltage-reference scale) that got flattened away. A live read on
        this device returned raw=1024 -> raw/1024.0 = 1.0 V, which is not
        plausible for a USB-powered (~5V) board, so do not trust a bare
        /1024.0 scaling. Live-compare against a multimeter on the USB 5V
        rail to pin down the real formula before relying on this value.
        """
        self.ser.reset_input_buffer()
        _write_line(self.ser, CMD_SUPPLY)
        return _read_uint16_le(self.ser)

    def temperature_c(self) -> float:
        self.ser.reset_input_buffer()
        _write_line(self.ser, CMD_TEMPERATURE)
        raw = _read_uint16_le(self.ser)
        return raw / 10.0

    def check_presence(self) -> bool:
        try:
            return self.firmware_version().startswith("FW Tiny ")
        except (TimeoutError, serial.SerialException):
            return False

    # -- sweeps --------------------------------------------------------

    def _read_sample(self, frequency_hz: int) -> Sample:
        x1 = _read_uint24_le(self.ser)
        x2 = _read_uint24_le(self.ser)
        x3 = _read_uint24_le(self.ser)
        x4 = _read_uint24_le(self.ser)
        re = x1 - x3
        im = x2 - x4
        gamma = complex(re, im) / float(1 << 24)
        return Sample(frequency_hz=frequency_hz, gamma=gamma)

    def sweep(
        self,
        start_hz: int,
        stop_hz: int,
        num_points: int,
        reflection: bool = True,
        progress_callback=None,
    ) -> list[Sample]:
        """`progress_callback`, if given, is called as progress_callback(i,
        num_points) after each point is read (i is 1-based). Large sweeps
        run natively on-device at ~4.5ms/point (empirically measured, no
        per-sweep point-count cap found up to 5000+ points) -- there's no
        need to split a big sweep into several smaller ones the way
        NanoVNA's ~101-point-per-sweep hardware limit requires; this
        callback exists purely so a caller (e.g. a GUI) can show progress
        during what can still be a multi-second-to-multi-minute single
        call for very large point counts."""
        if not (MIN_FREQ_HZ <= start_hz <= MAX_FREQ_HZ):
            raise ValueError(f"start_hz {start_hz} out of range")
        if not (MIN_FREQ_HZ <= stop_hz <= MAX_FREQ_HZ):
            raise ValueError(f"stop_hz {stop_hz} out of range")
        if num_points < 1:
            raise ValueError("num_points must be >= 1")

        self.ser.reset_input_buffer()
        _write_line(self.ser, CMD_REFLECTION if reflection else CMD_TRANSMISSION)
        _write_line(self.ser, start_hz // PRESCALER)
        _write_line(self.ser, stop_hz // PRESCALER)
        _write_line(self.ser, num_points)
        _write_line(self.ser)  # blank line triggers the sweep

        step = (stop_hz - start_hz) / max(1, num_points - 1)
        samples = []
        for i in range(num_points):
            freq = int(round(start_hz + step * i))
            samples.append(self._read_sample(freq))
            if progress_callback is not None:
                progress_callback(i + 1, num_points)

        # end-of-sweep reset sequence
        _write_line(self.ser, CMD_REFLECTION)
        _write_line(self.ser, 0)
        _write_line(self.ser, 0)
        _write_line(self.ser, 1)
        _write_line(self.ser)
        self._read_sample(0)  # discard

        return samples

    def reflection_sweep(self, start_hz: int, stop_hz: int, num_points: int, progress_callback=None) -> list[Sample]:
        return self.sweep(start_hz, stop_hz, num_points, reflection=True, progress_callback=progress_callback)

    def transmission_sweep(self, start_hz: int, stop_hz: int, num_points: int, progress_callback=None) -> list[Sample]:
        return self.sweep(start_hz, stop_hz, num_points, reflection=False, progress_callback=progress_callback)
