#!/usr/bin/env python3
"""Generates the PDF user guide for the miniVNA Tiny GUI.

Usage (run from this project's root):
    python scripts/make_user_guide.py

Requires: pip install reportlab (build-time only, not a runtime dependency).
"""
import os

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, ListFlowable,
    ListItem, PageBreak, HRFlowable,
)

OUT_PATH = os.path.join(os.path.dirname(__file__), "..", "build", "miniVNA Tiny User Guide.pdf")

ACCENT = colors.HexColor("#2a78d6")
DIM = colors.HexColor("#52514e")
LINE = colors.HexColor("#c3c2b7")

styles = getSampleStyleSheet()
styles.add(ParagraphStyle("Cover", parent=styles["Title"], fontSize=26, spaceAfter=6, textColor=ACCENT))
styles.add(ParagraphStyle("CoverSub", parent=styles["Normal"], fontSize=13, alignment=TA_CENTER, textColor=DIM))
styles.add(ParagraphStyle("H1", parent=styles["Heading1"], textColor=ACCENT, spaceBefore=18, spaceAfter=8))
styles.add(ParagraphStyle("H2", parent=styles["Heading2"], textColor=colors.HexColor("#1a1a19"), spaceBefore=10, spaceAfter=4))
styles.add(ParagraphStyle("Body", parent=styles["Normal"], fontSize=10, leading=14, spaceAfter=6))
styles.add(ParagraphStyle("Note", parent=styles["Normal"], fontSize=9.5, leading=13, textColor=DIM,
                           leftIndent=12, borderColor=LINE, borderWidth=0.75, borderPadding=8,
                           backColor=colors.HexColor("#f4f4f2"), spaceBefore=6, spaceAfter=10))
story = []

# -- cover ---------------------------------------------------------------
story.append(Spacer(1, 1.6 * inch))
story.append(Paragraph("miniVNA Tiny", styles["Cover"]))
story.append(Paragraph("Standalone Control &amp; Analysis Software", styles["CoverSub"]))
story.append(Spacer(1, 0.3 * inch))
story.append(Paragraph("User Guide - v1.1", ParagraphStyle("CoverGuide", parent=styles["Normal"], fontSize=15,
                                                      alignment=TA_CENTER, textColor=DIM)))
story.append(Spacer(1, 1.2 * inch))
story.append(Paragraph(
    "A replacement driver and GUI for the mRS miniVNA Tiny, built after the "
    "manufacturer discontinued support. This guide covers day-to-day use of "
    "the application; see the project README for the technical/protocol "
    "background.",
    ParagraphStyle("CoverNote", parent=styles["Normal"], fontSize=10, alignment=TA_CENTER,
                   textColor=DIM, leftIndent=60, rightIndent=60)))
story.append(PageBreak())

# -- getting started -------------------------------------------------------
story.append(Paragraph("1. Getting Started", styles["H1"]))
story.append(Paragraph(
    "This program is fully self-contained: the .exe, its settings, and your "
    "calibration data all live together in one folder. Copy that whole "
    "folder anywhere - including a USB stick - and it works the same way on "
    "any Windows PC, with no installer.", styles["Body"]))

story.append(Paragraph("Connecting", styles["H2"]))
story.append(ListFlowable([
    ListItem(Paragraph("Plug in the miniVNA Tiny via USB. Windows will assign it a COM port.", styles["Body"])),
    ListItem(Paragraph('Select that port from the <b>PORT</b> dropdown at top (click <b>Refresh</b> if it doesn\'t appear yet).', styles["Body"])),
    ListItem(Paragraph("Click <b>Connect</b>. The status readout (top right) shows the firmware version once connected.", styles["Body"])),
], bulletType="bullet"))

story.append(Paragraph(
    "The port you last used is remembered automatically (see Section 8, "
    "Settings &amp; Portability) - on the same PC you usually won't need to "
    "pick it again.", styles["Note"]))

# -- sweep -----------------------------------------------------------------
story.append(Paragraph("2. Running a Sweep", styles["H1"]))
story.append(Paragraph(
    "The <b>Sweep</b> panel controls what gets measured:", styles["Body"]))
story.append(ListFlowable([
    ListItem(Paragraph("<b>Reflection (S11)</b> - one-port measurement off the TX connector: return loss, SWR, impedance.", styles["Body"])),
    ListItem(Paragraph("<b>Transmission (S21)</b> - two-port measurement from TX through to RX: insertion loss, phase, group delay.", styles["Body"])),
    ListItem(Paragraph("<b>Start / Stop (MHz)</b> and <b>Points</b> set the sweep range and resolution (1 MHz - 3 GHz, up to several thousand points).", styles["Body"])),
], bulletType="bullet"))
story.append(Paragraph("Click <b>Sweep</b> for a single sweep, or check <b>Continuous sweep</b> to keep re-sweeping "
                        "automatically - useful while tuning an antenna or adjusting a device under test (DUT) and "
                        "watching the result update live. Uncheck it to stop. While a sweep (single or continuous) is "
                        "running, Connect/Disconnect and Calibration are disabled, since the device can only "
                        "handle one command stream at a time.", styles["Body"]))
story.append(Paragraph("Start/Stop/Points can be changed while Continuous sweep is running - the next lap picks up "
                        "the new values immediately, no need to stop first.", styles["Note"]))

story.append(Paragraph(
    "<b>Averages</b> (next to Continuous sweep) does genuine sweep-to-sweep noise reduction - it rolls a "
    "point-by-point average of the last N raw sweeps together (N=1 is off). A single Sweep click with N&gt;1 "
    "automatically chains N laps to fill the average; Continuous sweep keeps it as a sliding window across laps "
    "instead. Higher N means a cleaner trace but a slower response to a real change on the DUT - if you adjust "
    "something and the trace seems slow to update, that's the average catching up, not a stuck display.",
    styles["Body"]))

# -- calibration -------------------------------------------------------------
story.append(Paragraph("3. Calibration", styles["H1"]))
story.append(Paragraph(
    "Raw measurements include the instrument's own systematic errors. Calibration removes them, using standard "
    "known references. Click the <b>Calibration</b> button for the menu:", styles["Body"]))

story.append(Paragraph("Reflection (OSL)", styles["H2"]))
story.append(Paragraph(
    "For Reflection (S11) accuracy. Connect each standard to the TX port in turn and click its menu entry: "
    "<b>Measure Open</b>, <b>Measure Short</b>, <b>Measure Load</b> (50&#8486;). Once all three are captured, click "
    "<b>Build OSL Calibration</b>. All three must be swept at the same Start/Stop/Points - that's exactly what "
    "already happens automatically since they all use whatever the Sweep panel is currently set to.", styles["Body"]))

story.append(Paragraph("Transmission (Thru)", styles["H2"]))
story.append(Paragraph(
    "For Transmission (S21) accuracy. Connect TX directly to RX (no DUT) and click <b>Measure Thru</b>. "
    "Optionally, disconnect both ports entirely and click <b>Measure Isolation</b> - this corrects for "
    "system crosstalk, which matters most when measuring a filter's deep stopband. Click "
    "<b>Build Thru Calibration</b> when ready.", styles["Body"]))

story.append(Paragraph(
    "Calibration is saved automatically as soon as you build it (see Section 8) - you do not need to "
    "recalibrate every time you open the program.", styles["Note"]))
story.append(Paragraph(
    "A sweep at a <b>different</b> Start/Stop/Points than the calibration still gets corrected - the error "
    "terms are interpolated onto your sweep's frequencies automatically, rather than refusing and falling back "
    "to uncalibrated data. A note appears under the Calibration status line whenever this happens: it says "
    "\"interpolated\" for a sweep that stays within the calibrated range, or \"extrapolated\" (less accurate) if "
    "part of the sweep goes beyond it. No note at all means an exact match, the normal case if you keep the same "
    "sweep settings you calibrated at.", styles["Body"]))
story.append(Paragraph(
    "<b>Save Calibration...</b> / <b>Load Calibration...</b> in the same menu export/import calibration to a "
    "chosen file, separate from the automatic settings.json - handy for keeping a calibration for a specific "
    "cable or test fixture. A line under the Calibration status shows which file (if any) the active calibration "
    "matches - \"File: &lt;name&gt;\" after a Save/Load, or \"(built live, not from a file)\" if it came straight "
    "from Measure Open/Short/Load/Thru instead.", styles["Body"]))

# -- display -----------------------------------------------------------------
story.append(Paragraph("4. Display", styles["H1"]))
story.append(Paragraph(
    "<b>Smoothing (pts)</b>, at the top of the panel, is a display-only moving-average filter across neighboring "
    "frequency points - it's for visually flattening a jagged/noisy-looking trace, and does not touch the "
    "underlying data: markers, TDR, and Export all still see the true, unsmoothed values. 1 is off; higher values "
    "smooth more (at the cost of averaging away real fine detail if set too high).", styles["Body"]))
story.append(Paragraph(
    "<b>Negative dB (S11 convention)</b> switches the Return Loss plot and Marker Data's reflection readout "
    "between two equally valid sign conventions for the same |&#915;| data: the default Return Loss convention "
    "(always &ge;0 dB for a passive DUT, bigger = better match), or the S11-style convention some other VNA "
    "software uses instead (typically negative for a passive DUT, more negative = better match). This is purely "
    "a display choice - calibration, export, and TDR are unaffected either way.", styles["Body"]))
story.append(Paragraph(
    "The <b>Show windows</b> and <b>Axis scale</b> sections below are collapsible (click the arrow to expand):", styles["Body"]))
story.append(ListFlowable([
    ListItem(Paragraph("<b>Show windows</b> - choose which of Smith Chart, Return Loss, SWR, |S21|, and S21 Phase are visible. "
                        "The layout reflows automatically; the Smith Chart gets extra room when shown alongside others. Note "
                        "that Return Loss/SWR only ever show Reflection (S11) data and |S21|/S21 Phase only ever show "
                        "Transmission (S21) data - if you're sweeping in Transmission mode, check |S21| (or S21 Phase) to "
                        "actually see it; Return Loss/SWR staying on screen unchanged while you sweep in Transmission mode "
                        "is expected, not a stuck display.", styles["Body"])),
    ListItem(Paragraph("<b>Axis scale</b> - each plot's Y-axis can be left on Auto or given a fixed min/max (click "
                        "<b>Apply Scale</b> after editing). Toggling Negative dB automatically flips a manual Return Loss "
                        "range to match (e.g. 0..50 becomes -50..0).", styles["Body"])),
], bulletType="bullet"))
story.append(Paragraph(
    "<b>Show amateur band overlays</b> (in the Sweep panel) shades the US amateur bands on the frequency-axis "
    "plots for quick reference.", styles["Body"]))

# -- markers -----------------------------------------------------------------
story.append(Paragraph("5. Markers", styles["H1"]))
story.append(Paragraph(
    "Up to four independent markers (M1-M4), each with its own color. In the <b>Marker</b> panel, select which "
    "marker (M1-M4) the next click applies to, then click anywhere on a plot to place it there. The "
    "<b>Marker Data</b> panel (next to the main sidebar) shows full readings for every placed marker:",
    styles["Body"]))
story.append(ListFlowable([
    ListItem(Paragraph("<b>Reflection:</b> &#915; (magnitude/angle), return loss, SWR, mismatch loss, impedance "
                        "(R+jX), Q factor, admittance (G+jB).", styles["Body"])),
    ListItem(Paragraph("<b>Transmission:</b> |S21| in dB, phase, and group delay (needs a neighboring point on "
                        "each side, so it's blank right at the sweep's edges).", styles["Body"])),
], bulletType="bullet"))
story.append(Paragraph(
    "<b>Clear Selected</b> removes just the active marker; <b>Clear All</b> removes all four. A marker survives "
    "a re-sweep of the *other* mode, but a re-sweep of its *own* mode clears that half (the old point could be at "
    "the wrong frequency now).", styles["Body"]))

# -- tdr -----------------------------------------------------------------
story.append(Paragraph("6. TDR (Time-Domain Reflectometry)", styles["H1"]))
story.append(Paragraph(
    "The <b>TDR</b> button (Tools panel) turns the current Reflection sweep into a distance-domain plot - useful "
    "for locating a cable fault or measuring electrical length. Set the cable's <b>velocity factor</b> "
    "(printed on most coax, e.g. 0.66 for RG-58) and pick a window function, then click <b>Recompute</b>. "
    "The largest reflection is marked with its distance in both meters and feet.", styles["Body"]))
story.append(Paragraph(
    "This uses a calibrated Reflection sweep if you have one; an uncalibrated sweep still works but the result "
    "is only approximate.", styles["Note"]))

# -- export -----------------------------------------------------------------
story.append(Paragraph("7. Export", styles["H1"]))
story.append(Paragraph(
    "<b>Export .s1p (reflection)</b> writes a standard Touchstone 1-port file from the current (calibrated, if "
    "available) Reflection sweep - readable by most RF/antenna modeling software. <b>Export .csv (current mode)</b> "
    "writes a spreadsheet-friendly CSV (frequency, real, imaginary, magnitude, dB, phase) for whichever mode is "
    "currently selected.", styles["Body"]))

# -- settings -----------------------------------------------------------------
story.append(Paragraph("8. Settings &amp; Portability", styles["H1"]))
story.append(Paragraph(
    "Every time the program closes normally, it writes <b>settings.json</b> into the same folder as the .exe - "
    "not into this PC's user profile. That file remembers: last COM port, sweep mode/range/points, Averages, "
    "Smoothing, the Return Loss sign convention, the window's size, position and maximized state, which display windows and axis scales you had set, any "
    "calibration you'd built (plus which file it matches, if any), and it's all reloaded automatically the next "
    "time you launch.", styles["Body"]))
story.append(Paragraph(
    "Because everything lives in one folder together, copying that folder - including onto a USB stick - and "
    "running the .exe on a different PC picks up right where you left off, calibration included. Calibration is "
    "also saved immediately when built (not just on close), so it survives even if the program is closed "
    "abnormally afterward.", styles["Body"]))
story.append(Paragraph(
    "<b>Update check.</b> A few seconds after start-up the program makes one request to GitHub's public "
    "\"latest release\" record for this project. If a newer version exists, an amber <b>UPDATE vX AVAILABLE</b> "
    "button appears in the top bar and opens the release page. Nothing is downloaded or installed automatically, "
    "and nothing is sent except the request itself (GitHub sees your IP address, as for any web request). Untick "
    "<b>Check for updates</b> in the top bar to turn it off; the choice is saved in settings.json.", styles["Body"]))
story.append(Paragraph(
    "settings.json is plain text (JSON) if you ever want to inspect or hand-edit it, though normally there's no "
    "need to.", styles["Note"]))

# -- limitations -----------------------------------------------------------------
story.append(Paragraph("9. Known Limitations", styles["H1"]))
story.append(ListFlowable([
    ListItem(Paragraph("<b>TRL calibration is not offered.</b> It's a full 2-port technique needing S11, S21, S12, "
                        "and S22 - this device has a fixed TX/RX and can only ever measure S11 and S21. The OSL "
                        "and Thru calibrations here are the correct, complete techniques for what this hardware "
                        "can actually do.", styles["Body"])),
    ListItem(Paragraph("Supply-voltage reading is not currently trustworthy - the underlying scale factor could "
                        "not be independently confirmed. Not used anywhere in normal operation.", styles["Body"])),
    ListItem(Paragraph("First launch of the .exe can take several seconds longer than later launches (self-"
                        "extracting to a temp folder) - this is normal for a single-file packaged program.", styles["Body"])),
], bulletType="bullet"))

doc = SimpleDocTemplate(
    OUT_PATH, pagesize=letter,
    topMargin=0.85 * inch, bottomMargin=0.85 * inch,
    leftMargin=0.9 * inch, rightMargin=0.9 * inch,
    title="miniVNA Tiny - User Guide",
)
doc.build(story)
print(f"Wrote {os.path.abspath(OUT_PATH)}")
