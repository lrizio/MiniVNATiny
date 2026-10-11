"""
Standalone GUI for the miniVNA Tiny, in the spirit of NanoVNA-Saver.

Tkinter + matplotlib, no Qt -- keeps the packaged executable small and
dependency-light for running off a USB stick on an arbitrary PC. Reuses
minivna_tiny.py / osl_calibration.py / thru_calibration.py / export.py /
bands.py / tdr.py from this project unchanged.

Visual theme (2026-08-31) ported from theme.py, shared with this same
ham-projects family's other Tkinter apps (FT710 Remote Desktop, Yaesu
Radio Controller) for a consistent look and feel: dark instrument-panel
palette, rounded-corner fieldset-style panels, custom rounded buttons,
Segoe UI/Consolas fonts. matplotlib figures are styled to match via
rcParams so the plots don't look like a pasted-in white rectangle.

Run: python gui_app.py

Note on TRL calibration: NOT implemented, on purpose. TRL solves a full
2-port 8/10-term error model and needs S11, S21, S12, *and* S22. This
device has a fixed TX (source) and RX (receiver) -- it can measure S11
and S21, but never S12/S22 without physically reversing the DUT, which
isn't real port-switching and would introduce its own unknown errors.
The OSL (1-port reflection) and thru+isolation (unidirectional S21)
calibrations here are the correct and complete techniques this hardware
actually supports.

Note on sweep segmentation: also not implemented, on purpose -- tested
empirically against real hardware up to 5000+ points in one native sweep
call with no failures (~4.5ms/point, linear). Unlike NanoVNA's ~101-point
native cap, there's nothing here to stitch around. What large sweeps
actually need is progress feedback, which IS implemented (see the
progress bar under Sweep).
"""
import json
import math
import os
import queue
import re
import sys
import threading
import tkinter as tk
from dataclasses import dataclass, field
from tkinter import ttk, filedialog, messagebox
from typing import Optional

import serial.tools.list_ports
import matplotlib
matplotlib.use("TkAgg")
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.patches import Circle

import theme
from minivna_tiny import MiniVNATiny, MIN_FREQ_HZ, MAX_FREQ_HZ
from osl_calibration import (
    OSLCalibration, gamma_to_return_loss_db, gamma_to_swr, gamma_to_impedance,
    gamma_to_phase_deg, gamma_to_admittance, gamma_to_q_factor, gamma_to_mismatch_loss_db,
    ErrorTerms as OSLErrorTerms,
)
from thru_calibration import ThruCalibration, s21_to_db, TransmissionErrorTerms as ThruErrorTerms
from group_delay import compute_group_delay_ns
from export import export_touchstone_s1p, export_csv
from bands import bands_overlapping
from tdr import compute_tdr
import updatecheck

# Style every matplotlib figure created after this point to match theme.py's
# dark instrument-panel palette, instead of matplotlib's default white/black.
matplotlib.rcParams.update({
    "figure.facecolor": theme.BG,
    "axes.facecolor": theme.SCREEN,
    "axes.edgecolor": theme.LINE,
    "axes.labelcolor": theme.FG,
    "text.color": theme.FG,
    "xtick.color": theme.FG_DIM,
    "ytick.color": theme.FG_DIM,
    "grid.color": theme.LINE,
    "axes.titlecolor": theme.FG,
    "legend.facecolor": theme.SCREEN2,
    "legend.edgecolor": theme.LINE,
    "legend.labelcolor": theme.FG,
    "font.family": "Segoe UI",
})

SIDEBAR_WIDTH = 340  # initial pane width (px) -- draggable via the sash, this is just the starting point


__version__ = "1.1"


def _app_dir() -> str:
    """Directory the running program lives in -- the script's own folder
    in dev mode, or the .exe's own folder once packaged with PyInstaller
    (sys.frozen/sys.executable, the standard way to detect this). Settings
    live here, next to the program itself, specifically so the whole
    thing -- program + settings + calibration -- stays together and works
    the same way when the folder is copied to a different PC (e.g. off a
    USB stick), instead of settings silently living in *this* PC's user
    profile where a fresh machine would never see them."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


SETTINGS_PATH = os.path.join(_app_dir(), "settings.json")


# ---------------------------------------------------------------- Smith chart

R_CIRCLES = [0.2, 0.5, 1.0, 2.0, 5.0]
X_ARCS = [0.2, 0.5, 1.0, 2.0, 5.0]


def draw_smith_grid(ax):
    """Draw a Smith-chart impedance grid. Constant-R and constant-X circles
    are drawn as full circles then clipped to the unit disk via
    set_clip_path -- avoids doing the arc-angle trig by hand."""
    ax.set_aspect("equal")
    ax.axis("off")
    boundary = Circle((0, 0), 1.0, fill=False, edgecolor=theme.FG_DIM, linewidth=1.0)
    ax.add_patch(boundary)
    for r in R_CIRCLES:
        c = Circle((r / (1 + r), 0), 1 / (1 + r), fill=False, edgecolor=theme.LINE, linewidth=0.7)
        c.set_clip_path(boundary)
        ax.add_patch(c)
    for x in X_ARCS:
        for sign in (1, -1):
            xv = sign * x
            c = Circle((1, 1 / xv), abs(1 / xv), fill=False, edgecolor=theme.LINE, linewidth=0.7)
            c.set_clip_path(boundary)
            ax.add_patch(c)
    ax.plot([-1, 1], [0, 0], color=theme.LINE, linewidth=0.7)  # x=0 diameter
    ax.set_xlim(-1.15, 1.15)
    ax.set_ylim(-1.15, 1.15)


# ---------------------------------------------------------------- background worker

@dataclass
class SweepResult:
    kind: str  # "sweep" | "capture" | "connected" | "error" | "progress"
    mode: Optional[str] = None
    label: Optional[str] = None
    frequencies_hz: list = field(default_factory=list)
    values: list = field(default_factory=list)
    message: str = ""
    current: int = 0
    total: int = 0


class Worker(threading.Thread):
    """Runs one blocking hardware operation off the GUI thread, reports
    back via a queue (Tkinter is not thread-safe -- never touch widgets
    from here, only from the queue-polling loop on the main thread)."""

    def __init__(self, fn, result_queue: "queue.Queue"):
        super().__init__(daemon=True)
        self.fn = fn
        self.result_queue = result_queue

    def run(self):
        try:
            self.fn()
        except Exception as e:  # noqa: BLE001 -- surface any failure to the GUI
            self.result_queue.put(SweepResult(kind="error", message=str(e)))


def _make_progress_cb(result_queue: "queue.Queue", mode: str):
    """Throttled progress callback -- reports at most ~100 updates
    regardless of point count, so a 5000+ point sweep doesn't flood the
    queue with one message per point."""
    last_pct = {"value": -1}

    def cb(i, n):
        pct = int(i * 100 / n)
        if pct != last_pct["value"]:
            last_pct["value"] = pct
            result_queue.put(SweepResult(kind="progress", mode=mode, current=i, total=n))

    return cb


# TDR needs bandwidth, not points: real spatial resolution is ~c*VF/(2*BW),
# and the app's default sweep (1-30 MHz) only has 29 MHz of BW -- at
# VF=0.66 that's a ~3.4 m resolution cell and a ~3.4 km unambiguous range,
# nowhere near enough to see a short jumper/patch cable (the IFFT just
# reports noise/sidelobe artifacts at some large "distance" in that case).
# This preset trades up to a wide span for a ~6-7 cm resolution cell
# instead -- see tdr.py's docstring and gui_app._quick_tdr_sweep.
TDR_QUICK_SWEEP_START_MHZ = 1.0
TDR_QUICK_SWEEP_STOP_MHZ = 1500.0
TDR_QUICK_SWEEP_POINTS = 2000

DEFAULT_MODE = "reflection"
PLOT_KEYS = ("smith", "rl", "swr", "s21_mag", "s21_phase")
PLOT_TITLES = {
    "smith": "Smith Chart", "rl": "Return Loss", "swr": "SWR",
    "s21_mag": "|S21|", "s21_phase": "S21 Phase",
}
# Default startup view: just RL + SWR, since that's the everyday
# antenna/SWR-check use case -- Smith chart and the S21 panels are still
# one click away via the Display checkboxes.
PLOT_DEFAULT_VISIBLE = {"smith": False, "rl": True, "swr": True, "s21_mag": False, "s21_phase": False}

MAX_MARKERS = 4
# Reuses theme.py's existing accent colors -- already 4 distinct,
# CVD-considered hues (red/amber/pink/green) rather than inventing new ones.
MARKER_COLORS = [theme.BAD, theme.LED_AMBER, theme.PINK, theme.LED_GREEN]


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title(f"miniVNA Tiny v{__version__}")
        root.geometry("1200x800")
        theme.configure(root)
        try:
            root.iconbitmap(os.path.join(_app_dir(), "icon.ico"))
        except tk.TclError:
            pass  # e.g. running somewhere .ico isn't supported -- not fatal

        self.vna: Optional[MiniVNATiny] = None
        self._connected_vna: Optional[MiniVNATiny] = None
        self.result_queue: "queue.Queue[SweepResult]" = queue.Queue()

        self.cal_captures: dict[str, tuple[list, list]] = {}
        self.osl_cal: Optional[OSLCalibration] = None
        self.thru_cal: Optional[ThruCalibration] = None
        # Which external file (if any) the CURRENT calibration state
        # matches -- set on Load/Save Calibration..., cleared on any live
        # build (Build OSL/Thru Calibration) or Clear Calibration, since
        # at that point the in-memory state no longer matches what's on
        # disk. One shared path rather than one per OSL/Thru: matches the
        # existing combined "OSL: ready | Thru: ready" status convention,
        # and a single Load/Save Calibration... file already holds both.
        self.cal_file_path: Optional[str] = None

        # freqs, raw_values, calibrated_values_or_None -- per mode. raw_values
        # here is already sweep-averaged (see _apply_sweep_averaging) when
        # Averages > 1 -- "raw" means "not yet calibration-corrected", not
        # "straight off the wire".
        self.last: dict[str, Optional[tuple]] = {"reflection": None, "transmission": None}
        # Rolling buffers of the last N raw (pre-calibration) sweeps per
        # mode, for sweep-to-sweep averaging -- genuine noise reduction
        # (real receiver/thermal noise varies sweep to sweep), distinct
        # from the display-only frequency-smoothing below. Keyed
        # separately from cal_captures so calibration-standard captures
        # (Open/Short/Load/Thru/Isolation) never get mixed into a DUT
        # sweep's average or vice versa -- captures go through a wholly
        # separate code path (SweepResult.kind == "capture") that never
        # touches these buffers.
        self.sweep_avg_buffers: dict[str, list] = {"reflection": [], "transmission": []}
        self.sweep_avg_freqs: dict[str, Optional[list]] = {"reflection": None, "transmission": None}
        # How many more automatic laps to chain after the in-flight sweep
        # completes -- used only to fill the averaging buffer for a single
        # "Sweep" click (see _on_sweep); Continuous sweep loops via
        # continuous_var instead and ignores this.
        self._sweep_chain_remaining = 0
        # Up to MAX_MARKERS independent markers. Each can hold a reflection
        # index, a transmission index, or both (nothing stops placing the
        # same marker on both a reflection and a transmission plot -- it's
        # still "one marker", just showing readings from both sweeps).
        self.markers: list[dict] = [
            {"active": False, "refl_idx": None, "trans_idx": None} for _ in range(MAX_MARKERS)
        ]
        self.selected_marker_var = tk.IntVar(value=0)  # which marker slot the next plot click affects
        self.marker_data_labels: dict[int, dict] = {}  # marker index -> {"table": Frame}

        self.tdr_window: Optional[tk.Toplevel] = None
        self.tdr_ax = None
        self.tdr_canvas = None
        self.tdr_vf_var = tk.StringVar(value="0.66")
        self.tdr_window_var = tk.StringVar(value="hamming")
        self.tdr_threshold_var = tk.StringVar(value="20")  # % of peak, for first_significant_distance_m
        self._tdr_quick_sweep_pending = False  # set by _quick_tdr_sweep, consumed in _handle_result

        self.axes: dict[str, "matplotlib.axes.Axes"] = {}
        self.show_plot_var = {k: tk.BooleanVar(value=PLOT_DEFAULT_VISIBLE[k]) for k in PLOT_KEYS}
        self.scale_auto_var = {k: tk.BooleanVar(value=True) for k in ("rl", "swr", "s21_mag", "s21_phase")}
        self.scale_min_var = {
            "rl": tk.StringVar(value="-50"), "swr": tk.StringVar(value="1"),
            "s21_mag": tk.StringVar(value="-100"), "s21_phase": tk.StringVar(value="-180"),
        }
        self.scale_max_var = {
            "rl": tk.StringVar(value="0"), "swr": tk.StringVar(value="10"),
            "s21_mag": tk.StringVar(value="0"), "s21_phase": tk.StringVar(value="180"),
        }

        self._build_ui()
        self._load_settings()  # before _refresh_ports(), which only fills
        # port_var if it's still empty -- so a restored port isn't clobbered
        self._refresh_ports()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(100, self._poll_queue)
        self.root.after(4000, self._start_update_check)

    # -- themed helpers ------------------------------------------------------

    def _checkbutton(self, parent, text, variable, command=None):
        return tk.Checkbutton(
            parent, text=text, variable=variable, command=command,
            bg=theme.BG, fg=theme.FG_DIM, selectcolor=theme.SCREEN,
            activebackground=theme.BG, activeforeground=theme.FG, font=theme.FONT_UI,
        )

    def _radiobutton(self, parent, text, variable, value, command=None):
        return tk.Radiobutton(
            parent, text=text, variable=variable, value=value, command=command,
            bg=theme.BG, fg=theme.FG_DIM, selectcolor=theme.SCREEN,
            activebackground=theme.BG, activeforeground=theme.FG, font=theme.FONT_UI,
        )

    def _label(self, parent, text="", dim=False, font=None):
        return tk.Label(parent, text=text, bg=theme.BG, fg=(theme.FG_DIM if dim else theme.FG),
                         font=font or theme.FONT_UI)

    def _collapsible(self, parent, title, panel_canvas, start_open=False):
        """A clickable header ("<arrow> Title") that shows/hides a content
        frame below it -- an in-panel accordion section, for the same
        "hidden until you need it" goal as the Calibration menu button, but
        implemented as a toggle rather than a popup menu since this holds
        live widgets (checkboxes, entry fields) that tk.Menu can't host at
        all (menus only support text-based command/checkbutton/radiobutton
        entries, no arbitrary child widgets).

        Returns the body frame -- pack content into it as normal. Reopening
        after a close re-inserts the body with pack(after=header), so it
        always lands in the right place regardless of what else was packed
        into `parent` in between (plain re-pack() without `after` would
        instead append it below every sibling, out of order).

        `panel_canvas` is the enclosing theme.rounded_panel()'s canvas
        (whatever `parent` -- or an ancestor of it -- was built inside).
        Real bug this fixes, reported directly ("cannot see the items
        below the first one"): theme.finalize_rounded_panel() sizes that
        canvas ONCE, to fit the body's height *at that moment* -- it was
        written for this app's (and the sibling apps') other panels, none
        of which change size after being built. A collapsible section
        growing later doesn't grow the canvas with it, so the panel's own
        fixed-size viewport clips everything past whatever fit when it was
        still collapsed. Fixed by calling self._refinalize_panel() after
        every toggle, which re-measures and re-draws that canvas."""
        header = tk.Frame(parent, bg=theme.BG)
        header.pack(fill="x", pady=(4, 2))

        state = {"open": start_open}
        arrow_var = tk.StringVar(value="▾" if start_open else "▸")
        arrow = tk.Label(header, textvariable=arrow_var, bg=theme.BG, fg=theme.FG_DIM,
                          font=theme.FONT_UI_BOLD, cursor="hand2")
        arrow.pack(side="left")
        title_label = tk.Label(header, text=title, bg=theme.BG, fg=theme.FG,
                                font=theme.FONT_UI_BOLD, cursor="hand2")
        title_label.pack(side="left", padx=(4, 0))

        body = tk.Frame(parent, bg=theme.BG)
        if start_open:
            body.pack(fill="x", anchor="w", after=header)

        def toggle(_event=None):
            if state["open"]:
                body.pack_forget()
                arrow_var.set("▸")
            else:
                body.pack(fill="x", anchor="w", after=header)
                arrow_var.set("▾")
            state["open"] = not state["open"]
            self._refinalize_panel(panel_canvas)

        header.bind("<Button-1>", toggle)
        arrow.bind("<Button-1>", toggle)
        title_label.bind("<Button-1>", toggle)
        header.configure(cursor="hand2")

        return body

    def _panel(self, parent, title):
        """A rounded-corner fieldset panel. Caller packs widgets into the
        returned body frame, then MUST call self._finish_panel(canvas)
        once done (measures content to size/draw the border, per
        theme.rounded_panel's documented two-step pattern)."""
        canvas, body = theme.rounded_panel(parent, title=title, bg=theme.BG)
        return canvas, body

    def _finish_panel(self, canvas):
        theme.finalize_rounded_panel(canvas, min_width=SIDEBAR_WIDTH)
        canvas.pack(fill=tk.X, pady=(0, 10))

    def _refinalize_panel(self, canvas):
        """Re-run theme.finalize_rounded_panel() on a panel whose body
        content changed size after the initial _finish_panel() call (a
        collapsible section inside it toggled). Not a plain re-call --
        finalize_rounded_panel() was written to run exactly once: it
        draws a new border polygon and builds a whole new title-frame
        widget tree on every call without deleting the previous ones, so
        calling it twice as-is leaks a duplicate border outline and an
        orphaned title label/glow-dot each time. Clears the canvas and
        re-embeds the body window first so finalize starts from a clean
        slate, same net visual result as the first call, not a stacked
        duplicate of it."""
        rp = canvas._rp
        canvas.delete("all")
        rp["body_id"] = canvas.create_window(rp["pad"], rp["top_pad"], window=rp["body"], anchor="nw")
        theme.finalize_rounded_panel(canvas, min_width=SIDEBAR_WIDTH)

    # -- UI construction ---------------------------------------------------

    def _build_ui(self):
        self._build_top_bar()
        tk.Frame(self.root, bg=theme.LINE, height=2).pack(fill=tk.X)

        # PanedWindow instead of plain packed frames: the whole point is a
        # draggable sash, since a fixed-width sidebar was clipping longer
        # labels/controls with no way for the user to give it more room.
        # bg=theme.LINE (not theme.BG) is deliberate: each pane's own
        # background covers its own area, so the PanedWindow's background
        # only ever shows through in the thin sash gaps between panes --
        # giving a visible divider line there, matching the same LINE-
        # colored separator convention as the bar under the top toolbar.
        paned = tk.PanedWindow(self.root, orient=tk.HORIZONTAL, bg=theme.LINE,
                                sashwidth=4, sashpad=0, bd=0, relief=tk.FLAT,
                                showhandle=False)
        paned.pack(fill=tk.BOTH, expand=True)

        sidebar = self._build_sidebar(paned)
        marker_data_panel = self._build_marker_data_panel(paned)
        plot_area = self._build_plot_area(paned)
        # stretch="never" on the two left panes keeps them at whatever width
        # the user last dragged them to when the window itself is resized;
        # the plot area (stretch="always") absorbs all the extra/lost space.
        paned.add(sidebar, minsize=240, width=SIDEBAR_WIDTH, stretch="never")
        paned.add(marker_data_panel, minsize=200, width=280, stretch="never")
        paned.add(plot_area, minsize=300, stretch="always")

        self._redraw_plots()

    def _build_top_bar(self):
        bar = theme.metal_bar(self.root, height=70)
        bar.pack(fill=tk.X)
        bar.pack_propagate(False)

        brand = tk.Frame(bar, bg=theme.BG)
        brand.pack(side=tk.LEFT, padx=16)
        theme.glow_dot(brand, 18, theme.GOOD).pack(side=tk.LEFT, padx=(0, 10))
        tk.Label(brand, text="miniVNA TINY", bg=theme.BG, fg=theme.FG,
                 font=("Segoe UI", 14, "bold")).pack(side=tk.LEFT)

        controls = tk.Frame(bar, bg=theme.BG)
        controls.pack(side=tk.LEFT, padx=(28, 0))

        self._label(controls, "PORT", dim=True, font=theme.FONT_UI_BOLD).grid(row=0, column=0, sticky="w")
        self.port_var = tk.StringVar()
        self.port_combo = ttk.Combobox(controls, textvariable=self.port_var, width=12, state="readonly")
        self.port_combo.grid(row=1, column=0, padx=(0, 8))

        theme.metal_button(controls, "Refresh", self._refresh_ports, font=theme.FONT_UI, height=32).grid(
            row=1, column=1, padx=(0, 16))

        self.connect_btn = theme.metal_button(
            controls, "Connect", self._on_connect, bg=theme.ACCENT,
            active_bg=theme.LED_BLUE, fg=theme.METAL_EDGE, height=32,
        )
        self.connect_btn.grid(row=1, column=2, padx=(0, 16))

        self.status_label = self._label(bar, "Not connected", dim=True)
        self.status_label.pack(side=tk.RIGHT, padx=20)

        # Optional start-up check for a newer public release (one request to
        # GitHub, see updatecheck.py). The amber button stays hidden unless
        # a newer release exists; the tick box opts out entirely.
        self.check_updates_var = tk.BooleanVar(value=True)
        tk.Checkbutton(bar, text="Check for updates", variable=self.check_updates_var,
                       bg=theme.BG, fg=theme.FG_DIM, selectcolor=theme.BG, activebackground=theme.BG,
                       activeforeground=theme.FG, font=theme.FONT_UI, bd=0, highlightthickness=0,
                       command=self._save_settings).pack(side=tk.RIGHT, padx=(0, 8))
        self._top_bar = bar
        self._update_url = updatecheck.PAGE_URL

    def _build_sidebar(self, parent):
        # Scrollable sidebar -- the same Canvas+Frame+Scrollbar pattern used
        # in this family's other apps (app.py's _build_content). Width comes
        # from the PanedWindow pane (draggable sash), NOT a hardcoded pixel
        # width here -- an earlier fixed width was clipping longer labels
        # with no way for the user to see the rest. The canvas and its
        # inner content frame are kept width-matched to whatever the pane
        # actually is, so dragging the sash wider actually gives the panels
        # more room instead of leaving them stuck at their original size.
        outer = tk.Frame(parent, bg=theme.BG)

        canvas = tk.Canvas(outer, bg=theme.BG, highlightthickness=0)
        vscroll = ttk.Scrollbar(outer, orient=tk.VERTICAL, command=canvas.yview)
        canvas.configure(yscrollcommand=vscroll.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vscroll.pack(side=tk.RIGHT, fill=tk.Y)

        content = tk.Frame(canvas, bg=theme.BG)
        content_id = canvas.create_window((0, 0), window=content, anchor="nw")
        content.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(content_id, width=e.width))

        def _on_wheel(event):
            canvas.yview_scroll(-1 * (event.delta // 120), "units")
        canvas.bind_all("<MouseWheel>", _on_wheel)

        pad = tk.Frame(content, bg=theme.BG, width=10)
        pad.pack(side=tk.LEFT)
        col = tk.Frame(content, bg=theme.BG)
        col.pack(side=tk.LEFT, fill=tk.Y, pady=10)

        self._build_sweep_panel(col)
        self._build_calibration_panel(col)
        self._build_display_panel(col)
        self._build_tools_panel(col)
        self._build_marker_panel(col)
        self._build_export_panel(col)

        return outer

    def _build_sweep_panel(self, parent):
        canvas, body = self._panel(parent, "Sweep")

        self.mode_var = tk.StringVar(value=DEFAULT_MODE)
        self._radiobutton(body, "Reflection (S11)", self.mode_var, "reflection").pack(anchor="w")
        self._radiobutton(body, "Transmission (S21)", self.mode_var, "transmission").pack(anchor="w")

        self.start_var = tk.StringVar(value="1.000")
        self.stop_var = tk.StringVar(value="30.000")
        self.points_var = tk.StringVar(value="500")

        grid = tk.Frame(body, bg=theme.BG)
        grid.pack(fill="x", pady=(6, 0))
        self._label(grid, "Start (MHz)").grid(row=0, column=0, sticky="w")
        ttk.Entry(grid, textvariable=self.start_var, width=10).grid(row=0, column=1)
        self._label(grid, "Stop (MHz)").grid(row=1, column=0, sticky="w")
        ttk.Entry(grid, textvariable=self.stop_var, width=10).grid(row=1, column=1)
        self._label(grid, "Points").grid(row=2, column=0, sticky="w")
        ttk.Entry(grid, textvariable=self.points_var, width=10).grid(row=2, column=1)

        self.sweep_btn = theme.metal_button(body, "Sweep", self._on_sweep, bg=theme.ACCENT,
                                             active_bg=theme.LED_BLUE, fg=theme.METAL_EDGE, height=34)
        self.sweep_btn.pack(fill="x", pady=(8, 0))

        self.continuous_var = tk.BooleanVar(value=False)
        self._checkbutton(body, "Continuous sweep", self.continuous_var,
                           command=self._on_continuous_toggle).pack(anchor="w", pady=(4, 0))

        avg_row = tk.Frame(body, bg=theme.BG)
        avg_row.pack(fill="x", pady=(6, 0))
        self._label(avg_row, "Averages").pack(side="left")
        self.avg_var = tk.StringVar(value="1")
        ttk.Spinbox(avg_row, from_=1, to=50, textvariable=self.avg_var, width=4).pack(side="left", padx=(6, 0))
        self._label(avg_row, "(sweep-to-sweep)", dim=True, font=("Segoe UI", 8)).pack(side="left", padx=(6, 0))

        style = ttk.Style(self.root)
        style.configure("Themed.Horizontal.TProgressbar", troughcolor=theme.SCREEN,
                         background=theme.ACCENT, bordercolor=theme.LINE, lightcolor=theme.ACCENT,
                         darkcolor=theme.ACCENT)
        self.progress = ttk.Progressbar(body, orient="horizontal", mode="determinate", maximum=100,
                                         style="Themed.Horizontal.TProgressbar")
        self.progress.pack(fill="x", pady=(6, 0))
        self.progress_label = self._label(body, "", dim=True, font=("Segoe UI", 8))
        self.progress_label.pack(fill="x")

        self.show_bands_var = tk.BooleanVar(value=True)
        self._checkbutton(body, "Show amateur band overlays", self.show_bands_var,
                           command=self._redraw_plots).pack(anchor="w", pady=(6, 0))

        self._finish_panel(canvas)

    def _build_calibration_panel(self, parent):
        canvas, body = self._panel(parent, "Calibration")

        self.cal_status = self._label(body, "No calibration standards captured", dim=True)
        self.cal_status.configure(wraplength=SIDEBAR_WIDTH - 10, justify="left")
        self.cal_status.pack(fill="x", pady=(0, 2))

        self.cal_file_label = self._label(body, "", dim=True, font=("Segoe UI", 8))
        self.cal_file_label.configure(wraplength=SIDEBAR_WIDTH - 10, justify="left")
        self.cal_file_label.pack(fill="x", pady=(0, 2))

        # Passive status line, not a popup -- a sweep at a different
        # Start/Stop/Points than the calibration now gets corrected via
        # interpolated error terms automatically (see osl_calibration.py/
        # thru_calibration.py's correct_sweep), so this just informs
        # rather than blocking. A modal messagebox here (the old behavior)
        # would fire every single lap during Continuous sweep -- this
        # updates quietly instead, in-line with the rest of the sidebar.
        self.cal_range_note = self._label(body, "", dim=True)
        self.cal_range_note.configure(wraplength=SIDEBAR_WIDTH - 10, justify="left",
                                       fg=theme.LED_AMBER, font=("Segoe UI", 8))
        self.cal_range_note.pack(fill="x", pady=(0, 6))

        cal_menu = tk.Menu(self.root, tearoff=0, bg=theme.SCREEN, fg=theme.FG,
                            activebackground=theme.ACCENT, activeforeground=theme.METAL_EDGE)

        osl_menu = tk.Menu(cal_menu, tearoff=0, bg=theme.SCREEN, fg=theme.FG,
                            activebackground=theme.ACCENT, activeforeground=theme.METAL_EDGE)
        osl_menu.add_command(label="Measure Open", command=lambda: self._capture_standard("open", "reflection"))
        osl_menu.add_command(label="Measure Short", command=lambda: self._capture_standard("short", "reflection"))
        osl_menu.add_command(label="Measure Load", command=lambda: self._capture_standard("load", "reflection"))
        osl_menu.add_separator()
        osl_menu.add_command(label="Build OSL Calibration", command=self._build_osl)
        cal_menu.add_cascade(label="Reflection (OSL)", menu=osl_menu)

        thru_menu = tk.Menu(cal_menu, tearoff=0, bg=theme.SCREEN, fg=theme.FG,
                             activebackground=theme.ACCENT, activeforeground=theme.METAL_EDGE)
        thru_menu.add_command(label="Measure Thru", command=lambda: self._capture_standard("thru", "transmission"))
        thru_menu.add_command(label="Measure Isolation", command=lambda: self._capture_standard("isolation", "transmission"))
        thru_menu.add_separator()
        thru_menu.add_command(label="Build Thru Calibration", command=self._build_thru)
        cal_menu.add_cascade(label="Transmission (Thru)", menu=thru_menu)

        cal_menu.add_separator()
        cal_menu.add_command(label="Save Calibration...", command=self._save_cal)
        cal_menu.add_command(label="Load Calibration...", command=self._load_cal)
        cal_menu.add_separator()
        cal_menu.add_command(label="Clear Calibration", command=self._clear_cal)

        def _post_cal_menu():
            x = self.cal_btn.winfo_rootx()
            y = self.cal_btn.winfo_rooty() + self.cal_btn.winfo_height()
            cal_menu.post(x, y)

        self.cal_btn = theme.metal_button(body, "Calibration ▾", _post_cal_menu, height=34)
        self.cal_btn.pack(fill="x")

        self._finish_panel(canvas)

    def _build_display_panel(self, parent):
        # Both sections collapsed by default -- same "hidden until you need
        # it" goal as the Calibration menu button, just implemented as an
        # in-panel accordion (see _collapsible's docstring for why) rather
        # than a popup menu, since both sections hold live widgets
        # (checkboxes, entries) a tk.Menu can't contain at all. This is
        # what actually shrinks the panel's default height -- reported
        # directly as too tall.
        canvas, body = self._panel(parent, "Display")

        smooth_row = tk.Frame(body, bg=theme.BG)
        smooth_row.pack(fill="x", pady=(0, 6))
        self._label(smooth_row, "Smoothing (pts)").pack(side="left")
        self.smoothing_var = tk.StringVar(value="1")
        smooth_spin = ttk.Spinbox(smooth_row, from_=1, to=25, textvariable=self.smoothing_var, width=4,
                                   command=self._redraw_plots)
        smooth_spin.pack(side="left", padx=(6, 0))
        # command= covers the up/down arrows; typing a value directly needs
        # its own trigger since Spinbox's command doesn't fire for that.
        smooth_spin.bind("<Return>", lambda e: self._redraw_plots())
        smooth_spin.bind("<FocusOut>", lambda e: self._redraw_plots())

        # Two equally-valid, equally-common sign conventions for the same
        # underlying |Gamma| data: this app's default is the classic "RL"
        # convention (RL = -20log10|Gamma|, always >=0 for a passive DUT,
        # bigger = better match) -- but plenty of other VNA software (and
        # people used to reading raw S11 in dB) expect the opposite sign
        # (S11_dB = +20log10|Gamma|, typically negative for a passive DUT,
        # more negative = better match). Purely a display choice; neither
        # is "more correct" and no measurement/calibration math changes.
        self.rl_negative_var = tk.BooleanVar(value=False)
        self._checkbutton(body, "Negative dB (S11 convention)", self.rl_negative_var,
                           command=self._on_rl_sign_toggle).pack(anchor="w", pady=(0, 6))

        windows_body = self._collapsible(body, "Show windows", canvas, start_open=False)
        for key in PLOT_KEYS:
            self._checkbutton(windows_body, PLOT_TITLES[key], self.show_plot_var[key],
                               command=self._redraw_plots).pack(anchor="w")

        scale_body = self._collapsible(body, "Axis scale", canvas, start_open=False)
        scale_grid = tk.Frame(scale_body, bg=theme.BG)
        scale_grid.pack(fill="x", pady=(2, 4))
        scale_rows = [
            ("rl", "RL (dB)"), ("swr", "SWR"),
            ("s21_mag", "S21 (dB)"), ("s21_phase", "S21 (deg)"),
        ]
        for row, (key, label) in enumerate(scale_rows):
            self._label(scale_grid, label).grid(row=row, column=0, sticky="w")
            self._checkbutton(scale_grid, "Auto", self.scale_auto_var[key]).grid(row=row, column=1)
            ttk.Entry(scale_grid, textvariable=self.scale_min_var[key], width=6).grid(row=row, column=2, padx=2)
            ttk.Entry(scale_grid, textvariable=self.scale_max_var[key], width=6).grid(row=row, column=3, padx=2)
        theme.metal_button(scale_body, "Apply Scale", self._redraw_plots, height=30).pack(fill="x")

        self._finish_panel(canvas)

    def _build_tools_panel(self, parent):
        canvas, body = self._panel(parent, "Tools")
        theme.metal_button(body, "TDR (from Reflection sweep)", self._open_tdr_window, height=34).pack(fill="x")
        self._finish_panel(canvas)

    def _build_marker_panel(self, parent):
        # Compact controls only -- the actual per-marker readout lives in
        # the dedicated Marker Data pane (see _build_marker_data_panel),
        # not crammed in here.
        canvas, body = self._panel(parent, "Marker")
        self._label(body, "Click a plot to place:", dim=True).pack(anchor="w")

        sel_row = tk.Frame(body, bg=theme.BG)
        sel_row.pack(fill="x", pady=(2, 6))
        for i in range(MAX_MARKERS):
            tk.Radiobutton(
                sel_row, text=f"M{i + 1}", variable=self.selected_marker_var, value=i,
                bg=theme.BG, fg=MARKER_COLORS[i], selectcolor=theme.SCREEN,
                activebackground=theme.BG, activeforeground=MARKER_COLORS[i],
                font=theme.FONT_UI_BOLD,
            ).pack(side="left", padx=(0, 8))

        theme.metal_button(body, "Clear Selected", self._clear_selected_marker, height=28).pack(fill="x")
        theme.metal_button(body, "Clear All", self._clear_all_markers, height=28).pack(fill="x", pady=(4, 0))
        self._finish_panel(canvas)

    def _build_marker_data_panel(self, parent):
        """The second LHS panel: full measured parameters for every
        enabled marker, kept separate from the compact Marker controls
        panel in the main sidebar."""
        outer = tk.Frame(parent, bg=theme.BG, padx=10, pady=10)
        canvas, body = self._panel(outer, "Marker Data")

        for i in range(MAX_MARKERS):
            if i > 0:
                tk.Frame(body, bg=theme.LINE, height=1).pack(fill="x", pady=8)
            header_row = tk.Frame(body, bg=theme.BG)
            header_row.pack(fill="x", anchor="w")
            swatch = tk.Canvas(header_row, width=10, height=10, bg=theme.BG, highlightthickness=0)
            swatch.create_oval(1, 1, 9, 9, fill=MARKER_COLORS[i], outline="")
            swatch.pack(side="left", padx=(0, 6))
            tk.Label(header_row, text=f"M{i + 1}", bg=theme.BG, fg=MARKER_COLORS[i],
                     font=theme.FONT_UI_BOLD).pack(side="left")
            table = tk.Frame(body, bg=theme.BG)
            table.pack(fill="x", anchor="w", padx=(16, 0), pady=(2, 0))
            self.marker_data_labels[i] = {"table": table}

        theme.finalize_rounded_panel(canvas, min_width=240)
        canvas.pack(fill=tk.BOTH, expand=True)
        return outer

    def _build_export_panel(self, parent):
        canvas, body = self._panel(parent, "Export")
        theme.metal_button(body, "Export .s1p (reflection)", self._export_s1p, height=32).pack(fill="x")
        theme.metal_button(body, "Export .csv (current mode)", self._export_csv, height=32).pack(fill="x", pady=(4, 0))
        self._finish_panel(canvas)

    def _build_plot_area(self, parent):
        right = tk.Frame(parent, bg=theme.BG, padx=10, pady=10)

        # constrained_layout (not tight_layout) is the key fix for live
        # resizing: tight_layout computes fixed fractional axes positions
        # ONCE, for whatever the canvas size was *at that call* -- it is
        # never redone automatically, so after any later resize the old
        # fractions are wrong until something explicitly recomputes them.
        # That's exactly what broke the grid after a window maximize
        # (confirmed via actual live-window screenshots, not just headless
        # renders).
        # constrained_layout instead recalculates on every draw, which is
        # what a resizable embedded figure actually needs.
        self.fig = Figure(figsize=(7.5, 6.5), dpi=100, constrained_layout=True)
        self.canvas = FigureCanvasTkAgg(self.fig, master=right)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)
        self.canvas.mpl_connect("button_press_event", self._on_plot_click)

        # Belt-and-suspenders on top of constrained_layout: also explicitly
        # rebuild (not just re-layout) on any real widget size change, so
        # data/marker overlays stay correctly positioned too, not just
        # spacing. Debounced (150ms) since <Configure> fires continuously
        # during a drag-resize.
        #
        # ROOT CAUSE of the "plots don't resize at all" bug (confirmed via
        # a live screenshot on real hardware showing the plot bitmap frozen
        # at its original size with a huge blank area around it): Tkinter's
        # widget.bind(seq, func) REPLACES any existing binding for that
        # sequence unless add="+" is passed. FigureCanvasTkAgg's own
        # __init__ already does `self._tkcanvas.bind("<Configure>",
        # self.resize)` -- and get_tk_widget() returns that exact
        # _tkcanvas object (matplotlib/backends/_backend_tk.py, verified by
        # reading the source directly). The earlier version of this handler
        # called .bind(...) without add="+", silently overwriting
        # matplotlib's own resize-the-figure-to-the-widget callback --
        # the widget itself still grew (that's plain Tk geometry
        # management, unaffected), but the rendered figure bitmap never
        # got told to grow with it. add="+" here is what makes both
        # callbacks coexist instead of the second clobbering the first.
        self._resize_after_id = None

        def _on_canvas_resize(event):
            if self._resize_after_id is not None:
                self.root.after_cancel(self._resize_after_id)
            self._resize_after_id = self.root.after(150, self._redraw_plots)

        self.canvas.get_tk_widget().bind("<Configure>", _on_canvas_resize, add="+")

        return right

    # -- ports / connection -------------------------------------------------

    def _refresh_ports(self):
        ports = [p.device for p in serial.tools.list_ports.comports()]
        self.port_combo["values"] = ports
        if ports and not self.port_var.get():
            self.port_var.set(ports[0])

    def _on_connect(self):
        if self.vna is not None:
            try:
                self.vna.close()
            except Exception:
                pass
            self.vna = None
            self.connect_btn.configure(text="Connect")
            self.status_label.configure(text="Not connected", fg=theme.FG_DIM)
            return

        port = self.port_var.get()
        if not port:
            messagebox.showerror("No port", "Select a COM port first.")
            return

        def work():
            vna = MiniVNATiny(port)
            fw = vna.firmware_version()
            self._connected_vna = vna
            self.result_queue.put(SweepResult(kind="connected", message=fw))

        self.status_label.configure(text="Connecting...", fg=theme.FG_DIM)
        Worker(work, self.result_queue).start()

    # -- sweeping -------------------------------------------------------

    def _parse_sweep_params(self):
        try:
            start_hz = int(round(float(self.start_var.get()) * 1e6))
            stop_hz = int(round(float(self.stop_var.get()) * 1e6))
            points = int(self.points_var.get())
        except ValueError:
            messagebox.showerror("Invalid input", "Start/Stop must be numbers (MHz), Points must be an integer.")
            return None
        if not (MIN_FREQ_HZ <= start_hz <= MAX_FREQ_HZ) or not (MIN_FREQ_HZ <= stop_hz <= MAX_FREQ_HZ):
            messagebox.showerror("Out of range", f"Frequency must be within {MIN_FREQ_HZ/1e6:.0f}-{MAX_FREQ_HZ/1e6:.0f} MHz.")
            return None
        if points < 2:
            messagebox.showerror("Invalid input", "Points must be >= 2.")
            return None
        return start_hz, stop_hz, points

    def _get_avg_count(self) -> int:
        try:
            n = int(self.avg_var.get())
        except ValueError:
            return 1
        return max(1, min(n, 50))

    def _apply_sweep_averaging(self, mode, freqs, values):
        """Point-by-point complex mean of the last N raw sweeps for `mode`
        (a rolling buffer -- grows toward N as sweeps come in, then slides).
        Averaging is done on RAW (pre-calibration) data, not on already-
        calibrated values: OSL correction is a bilinear (Mobius) transform
        of Gamma, not a linear one, so averaging raw noise first and
        correcting the averaged result is the metrologically correct order
        -- matches how real VNAs do sweep averaging.

        The buffer is discarded and restarted whenever the frequency grid
        changes (different Start/Stop/Points since the last sweep) --
        averaging sweeps taken at different frequency points together
        would silently produce nonsense."""
        n = self._get_avg_count()
        if n <= 1:
            self.sweep_avg_buffers[mode] = []
            self.sweep_avg_freqs[mode] = None
            return values
        if self.sweep_avg_freqs[mode] != freqs:
            self.sweep_avg_buffers[mode] = []
            self.sweep_avg_freqs[mode] = freqs
        buf = self.sweep_avg_buffers[mode]
        buf.append(values)
        if len(buf) > n:
            del buf[0]
        m = len(buf)
        return [sum(pts) / m for pts in zip(*buf)]

    def _set_busy_controls(self, disabled: bool):
        """Disable/enable the controls that would conflict with an
        in-flight sweep if clicked -- the serial port only ever tolerates
        one command stream at a time, so Connect/Disconnect and the
        Calibration menu need to be blocked for the duration of ANY sweep
        (one-shot or continuous), not just while Continuous is running."""
        state = "disabled" if disabled else "normal"
        self.sweep_btn.configure(state=state)
        self.connect_btn.configure(state=state)
        self.cal_btn.configure(state=state)

    def _trigger_sweep(self, mode=None):
        """Starts one sweep using the CURRENT Start/Stop/Points (and, if
        `mode` isn't given, the current mode radio) field values -- shared
        by a one-shot Sweep click and every lap of Continuous sweep, which
        re-reads these live each time, so adjusting the sweep range or
        mode mid-run takes effect on the next lap rather than needing you
        to stop and restart."""
        if self.vna is None:
            messagebox.showerror("Not connected", "Connect to the device first.")
            self.continuous_var.set(False)
            self._set_busy_controls(False)
            return
        params = self._parse_sweep_params()
        if params is None:
            self.continuous_var.set(False)
            self._set_busy_controls(False)
            return
        start_hz, stop_hz, points = params
        if mode is None:
            mode = self.mode_var.get()

        def work():
            cb = _make_progress_cb(self.result_queue, mode)
            samples = self.vna.sweep(start_hz, stop_hz, points, reflection=(mode == "reflection"),
                                      progress_callback=cb)
            self.result_queue.put(SweepResult(
                kind="sweep", mode=mode,
                frequencies_hz=[s.frequency_hz for s in samples],
                values=[s.gamma for s in samples],
            ))

        self.progress.config(value=0)
        Worker(work, self.result_queue).start()

    def _on_sweep(self):
        if self.vna is None:
            messagebox.showerror("Not connected", "Connect to the device first.")
            return
        # With Averages > 1, a single click chains N automatic laps to fill
        # the averaging buffer, instead of requiring N separate clicks --
        # _sweep_chain_remaining counts the laps still owed after this one.
        avg_n = self._get_avg_count()
        self._sweep_chain_remaining = avg_n - 1
        self._set_busy_controls(True)
        self.sweep_btn.configure(text="Sweeping..." if avg_n <= 1 else f"Averaging (1/{avg_n})...")
        self._trigger_sweep()

    def _on_continuous_toggle(self):
        if self.continuous_var.get():
            if self.vna is None:
                messagebox.showerror("Not connected", "Connect to the device first.")
                self.continuous_var.set(False)
                return
            self._sweep_chain_remaining = 0  # continuous loops via continuous_var, not this counter
            self._set_busy_controls(True)
            self.sweep_btn.configure(text="Continuous...")
            self._trigger_sweep()
        # Unchecking doesn't need to do anything here -- any in-flight
        # sweep just finishes normally, and its completion handler
        # (_handle_result) sees continuous_var is now False and stops
        # chaining instead of starting another lap.

    def _capture_standard(self, label, mode):
        if self.vna is None:
            messagebox.showerror("Not connected", "Connect to the device first.")
            return
        if self.continuous_var.get():
            messagebox.showerror("Continuous sweep running", "Stop continuous sweep before calibrating.")
            return
        params = self._parse_sweep_params()
        if params is None:
            return
        start_hz, stop_hz, points = params
        self._set_busy_controls(True)

        def work():
            cb = _make_progress_cb(self.result_queue, mode)
            samples = self.vna.sweep(start_hz, stop_hz, points, reflection=(mode == "reflection"),
                                      progress_callback=cb)
            self.result_queue.put(SweepResult(
                kind="capture", mode=mode, label=label,
                frequencies_hz=[s.frequency_hz for s in samples],
                values=[s.gamma for s in samples],
            ))

        self.progress.config(value=0)
        Worker(work, self.result_queue).start()

    # -- calibration build/save/load -------------------------------------

    def _build_osl(self):
        needed = ["open", "short", "load"]
        missing = [n for n in needed if n not in self.cal_captures]
        if missing:
            messagebox.showerror("Missing standards", f"Still need: {', '.join(missing)}")
            return
        freqs, gamma_open = self.cal_captures["open"]
        _, gamma_short = self.cal_captures["short"]
        _, gamma_load = self.cal_captures["load"]
        self.osl_cal = OSLCalibration.from_measurements(freqs, gamma_open, gamma_short, gamma_load)
        self.cal_file_path = None  # no longer matches whatever file (if any) was last loaded/saved
        self._update_cal_status()
        self._save_settings()  # calibration is expensive to redo -- don't rely on a clean app close to keep it
        messagebox.showinfo("Calibration", "OSL (reflection) calibration built.")

    def _build_thru(self):
        if "thru" not in self.cal_captures:
            messagebox.showerror("Missing standard", "Measure Thru first.")
            return
        freqs, s21_thru = self.cal_captures["thru"]
        s21_iso = None
        if "isolation" in self.cal_captures:
            _, s21_iso = self.cal_captures["isolation"]
        self.thru_cal = ThruCalibration.from_measurements(freqs, s21_thru, s21_iso)
        self.cal_file_path = None  # no longer matches whatever file (if any) was last loaded/saved
        self._update_cal_status()
        self._save_settings()  # calibration is expensive to redo -- don't rely on a clean app close to keep it
        messagebox.showinfo("Calibration", "Thru" + (" + isolation" if s21_iso else "") + " calibration built.")

    def _clear_cal(self):
        self.cal_captures.clear()
        self.osl_cal = None
        self.thru_cal = None
        self.cal_file_path = None
        self._update_cal_status()
        self.cal_range_note.configure(text="")

    def _update_cal_status(self):
        osl_state = "ready" if self.osl_cal else (
            ", ".join(k for k in ("open", "short", "load") if k in self.cal_captures) or "none")
        thru_state = "ready" if self.thru_cal else (
            ", ".join(k for k in ("thru", "isolation") if k in self.cal_captures) or "none")
        self.cal_status.configure(text=f"OSL: {osl_state}  |  Thru: {thru_state}")
        if self.cal_file_path:
            self.cal_file_label.configure(text=f"File: {os.path.basename(self.cal_file_path)}")
        elif self.osl_cal or self.thru_cal:
            self.cal_file_label.configure(text="(built live, not from a file)")
        else:
            self.cal_file_label.configure(text="")

    def _serialize_cal(self) -> dict:
        """Plain-dict form of the current calibration -- shared by the
        manual Save/Load-to-file dialogs and the automatic settings.json
        persistence, so there's exactly one place that knows this format."""
        data = {}
        if self.osl_cal:
            data["osl"] = {
                "frequencies_hz": self.osl_cal.frequencies_hz,
                "terms": [[e.directivity.real, e.directivity.imag,
                           e.source_match.real, e.source_match.imag,
                           e.reflection_tracking.real, e.reflection_tracking.imag]
                          for e in self.osl_cal.error_terms],
            }
        if self.thru_cal:
            data["thru"] = {
                "frequencies_hz": self.thru_cal.frequencies_hz,
                "terms": [[e.leakage.real, e.leakage.imag, e.tracking.real, e.tracking.imag]
                          for e in self.thru_cal.error_terms],
            }
        return data

    def _deserialize_cal(self, data: dict) -> None:
        """Inverse of _serialize_cal() -- sets self.osl_cal/self.thru_cal
        from a dict in that format. Missing keys are left alone (doesn't
        clear an existing calibration just because the loaded data only
        has one of the two)."""
        if "osl" in data:
            d = data["osl"]
            terms = [OSLErrorTerms(complex(t[0], t[1]), complex(t[2], t[3]), complex(t[4], t[5]))
                     for t in d["terms"]]
            self.osl_cal = OSLCalibration(frequencies_hz=d["frequencies_hz"], error_terms=terms)
        if "thru" in data:
            d = data["thru"]
            terms = [ThruErrorTerms(complex(t[0], t[1]), complex(t[2], t[3])) for t in d["terms"]]
            self.thru_cal = ThruCalibration(frequencies_hz=d["frequencies_hz"], error_terms=terms)
        self._update_cal_status()

    # -- settings.json persistence -----------------------------------------
    # Lives next to the program itself (script or .exe, see _app_dir()),
    # not in this PC's user profile -- the whole point is that copying the
    # program's folder to a different PC (e.g. off a USB stick) brings
    # sweep defaults, display preferences, and calibration along with it.

    def _save_settings(self):
        data = {
            "port": self.port_var.get(),
            "mode": self.mode_var.get(),
            "start_mhz": self.start_var.get(),
            "stop_mhz": self.stop_var.get(),
            "points": self.points_var.get(),
            "show_bands": self.show_bands_var.get(),
            "averages": self.avg_var.get(),
            "smoothing": self.smoothing_var.get(),
            "rl_negative": self.rl_negative_var.get(),
            "show_plot": {k: v.get() for k, v in self.show_plot_var.items()},
            "scale_auto": {k: v.get() for k, v in self.scale_auto_var.items()},
            "scale_min": {k: v.get() for k, v in self.scale_min_var.items()},
            "scale_max": {k: v.get() for k, v in self.scale_max_var.items()},
            "calibration": self._serialize_cal(),
            "cal_file_path": self.cal_file_path,
            "window": self._window_state(),
            "check_updates": self.check_updates_var.get(),
        }
        try:
            with open(SETTINGS_PATH, "w") as f:
                json.dump(data, f, indent=2)
        except OSError as e:
            # Not fatal -- e.g. a read-only USB stick. Surface it once
            # rather than fail silently, but don't block closing the app.
            messagebox.showwarning("Settings not saved", f"Could not write {SETTINGS_PATH}:\n{e}")

    def _start_update_check(self):
        if not self.check_updates_var.get():
            return
        # The check runs on a worker thread; hop back to the Tk thread via
        # the result queue's sibling mechanism (root.after is not thread-safe
        # from another thread, so just stash the answer and let a poll pick it up).
        def found(version, url):
            self._update_found = (version, url)
        updatecheck.check_async(__version__, found)
        self.root.after(1000, self._poll_update)

    def _poll_update(self):
        found = getattr(self, "_update_found", None)
        if found:
            version, self._update_url = found
            self.update_btn = theme.metal_button(
                self._top_bar, f"UPDATE v{version} AVAILABLE", self._open_release_page,
                bg=theme.LED_AMBER, active_bg=theme.LED_AMBER, fg=theme.METAL_EDGE, height=32)
            self.update_btn.pack(side=tk.RIGHT, padx=(0, 12))
        elif self.check_updates_var.get():
            self._update_polls = getattr(self, "_update_polls", 0) + 1
            if self._update_polls < 30:
                self.root.after(1000, self._poll_update)

    def _open_release_page(self):
        import webbrowser
        webbrowser.open(self._update_url)

    def _window_state(self):
        """Window size/position, plus whether it was maximized. A maximized
        window reports its maximized geometry, so briefly drop back to the
        normal state to read the size it should restore to."""
        try:
            zoomed = self.root.state() == "zoomed"
            if zoomed:
                self.root.state("normal")
                self.root.update_idletasks()
            geom = self.root.geometry()
            if zoomed:
                self.root.state("zoomed")
            return {"geometry": geom, "zoomed": zoomed}
        except tk.TclError:
            return {}

    def _restore_window(self, win):
        m = re.fullmatch(r"(\d+)x(\d+)([+-]-?\d+)([+-]-?\d+)", str(win.get("geometry", "")))
        if not m:
            return
        w, h, x, y = int(m[1]), int(m[2]), int(m[3]), int(m[4])
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        w, h = min(w, sw), min(h, sh)
        # A monitor that's no longer attached would leave the window off
        # screen -- pull it back so at least the title bar is reachable.
        x = max(0, min(x, sw - w))
        y = max(0, min(y, sh - h))
        self.root.geometry(f"{w}x{h}+{x}+{y}")
        if win.get("zoomed"):
            self.root.state("zoomed")

    def _load_settings(self):
        if not os.path.exists(SETTINGS_PATH):
            return
        try:
            with open(SETTINGS_PATH) as f:
                data = json.load(f)
        except (OSError, ValueError) as e:
            messagebox.showwarning("Settings not loaded", f"Could not read {SETTINGS_PATH}:\n{e}")
            return

        if "check_updates" in data:
            self.check_updates_var.set(bool(data["check_updates"]))
        if isinstance(data.get("window"), dict):
            self._restore_window(data["window"])

        if "port" in data:
            self.port_var.set(data["port"])
        if "mode" in data:
            self.mode_var.set(data["mode"])
        if "start_mhz" in data:
            self.start_var.set(data["start_mhz"])
        if "stop_mhz" in data:
            self.stop_var.set(data["stop_mhz"])
        if "points" in data:
            self.points_var.set(data["points"])
        if "show_bands" in data:
            self.show_bands_var.set(data["show_bands"])
        if "averages" in data:
            self.avg_var.set(data["averages"])
        if "smoothing" in data:
            self.smoothing_var.set(data["smoothing"])
        if "rl_negative" in data:
            self.rl_negative_var.set(data["rl_negative"])
        for key, val in data.get("show_plot", {}).items():
            if key in self.show_plot_var:
                self.show_plot_var[key].set(val)
        for key, val in data.get("scale_auto", {}).items():
            if key in self.scale_auto_var:
                self.scale_auto_var[key].set(val)
        for key, val in data.get("scale_min", {}).items():
            if key in self.scale_min_var:
                self.scale_min_var[key].set(val)
        for key, val in data.get("scale_max", {}).items():
            if key in self.scale_max_var:
                self.scale_max_var[key].set(val)
        if "calibration" in data:
            try:
                self._deserialize_cal(data["calibration"])
            except (KeyError, TypeError, ValueError):
                pass  # corrupt/old-format calibration block -- ignore, don't crash startup over it
        # Restored after _deserialize_cal (which calls _update_cal_status
        # itself, with cal_file_path still unset at that point) so the
        # filename actually sticks instead of being immediately redrawn
        # away -- _update_cal_status() below re-syncs the label either way.
        self.cal_file_path = data.get("cal_file_path")
        self._update_cal_status()

    def _on_close(self):
        self._save_settings()
        if self.vna is not None:
            try:
                self.vna.close()
            except Exception:
                pass
        self.root.destroy()

    def _save_cal(self):
        if not self.osl_cal and not self.thru_cal:
            messagebox.showerror("Nothing to save", "Build a calibration first.")
            return
        path = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("Calibration", "*.json")])
        if not path:
            return
        with open(path, "w") as f:
            json.dump(self._serialize_cal(), f)
        self.cal_file_path = path  # now matches this file
        self._update_cal_status()
        messagebox.showinfo("Saved", f"Calibration saved to {path}")

    def _load_cal(self):
        path = filedialog.askopenfilename(filetypes=[("Calibration", "*.json")])
        if not path:
            return
        with open(path) as f:
            data = json.load(f)
        self._deserialize_cal(data)
        self.cal_file_path = path
        self._update_cal_status()
        messagebox.showinfo("Loaded", f"Calibration loaded from {path}")

    # -- export -----------------------------------------------------------

    def _current_calibrated(self, mode):
        entry = self.last.get(mode)
        if entry is None:
            return None
        freqs, raw, calibrated = entry
        return freqs, (calibrated if calibrated is not None else raw)

    def _export_s1p(self):
        result = self._current_calibrated("reflection")
        if result is None:
            messagebox.showerror("No data", "Run a reflection sweep first.")
            return
        freqs, values = result
        path = filedialog.asksaveasfilename(defaultextension=".s1p", filetypes=[("Touchstone S1P", "*.s1p")])
        if not path:
            return
        export_touchstone_s1p(path, freqs, values,
                               comment="miniVNA Tiny" + (" (calibrated)" if self.osl_cal else " (uncalibrated raw)"))
        messagebox.showinfo("Exported", f"Saved {path}")

    def _export_csv(self):
        mode = self.mode_var.get()
        result = self._current_calibrated(mode)
        if result is None:
            messagebox.showerror("No data", f"Run a {mode} sweep first.")
            return
        freqs, values = result
        path = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV", "*.csv")])
        if not path:
            return
        export_csv(path, freqs, values, label="S11" if mode == "reflection" else "S21")
        messagebox.showinfo("Exported", f"Saved {path}")

    # -- TDR ---------------------------------------------------------------

    def _open_tdr_window(self):
        result = self._current_calibrated("reflection")
        if result is None:
            messagebox.showerror("No data", "Run a reflection sweep first (calibrated recommended, but raw works too).")
            return

        if self.tdr_window is None or not self.tdr_window.winfo_exists():
            win = tk.Toplevel(self.root)
            win.title("TDR")
            win.geometry("820x520")
            win.configure(bg=theme.BG)
            self.tdr_window = win

            ctrl = tk.Frame(win, bg=theme.BG, padx=8, pady=8)
            ctrl.pack(fill="x")
            self._label(ctrl, "Velocity factor").pack(side="left")
            ttk.Entry(ctrl, textvariable=self.tdr_vf_var, width=6).pack(side="left", padx=(4, 12))
            self._label(ctrl, "Window").pack(side="left")
            ttk.Combobox(ctrl, textvariable=self.tdr_window_var, width=10, state="readonly",
                         values=["hamming", "hann", "blackman", "none"]).pack(side="left", padx=(4, 12))
            self._label(ctrl, "1st-reflection threshold (%)").pack(side="left")
            ttk.Entry(ctrl, textvariable=self.tdr_threshold_var, width=5).pack(side="left", padx=(4, 12))
            theme.metal_button(ctrl, "Recompute", self._recompute_tdr, height=30).pack(side="left")

            ctrl2 = tk.Frame(win, bg=theme.BG, padx=8)
            ctrl2.pack(fill="x")
            theme.metal_button(
                ctrl2,
                f"Quick TDR Sweep ({TDR_QUICK_SWEEP_START_MHZ:g}-{TDR_QUICK_SWEEP_STOP_MHZ:g} MHz)",
                self._quick_tdr_sweep, height=30,
            ).pack(side="left")

            fig = Figure(figsize=(6.5, 4.3), dpi=100, constrained_layout=True)
            self.tdr_ax = fig.add_subplot(1, 1, 1)
            self.tdr_canvas = FigureCanvasTkAgg(fig, master=win)
            self.tdr_canvas.get_tk_widget().pack(fill="both", expand=True)

            self.tdr_info = self._label(win, "", dim=True)
            self.tdr_info.configure(wraplength=660, justify="left", padx=8, pady=8)
            self.tdr_info.pack(fill="x")
        else:
            self.tdr_window.lift()

        self._recompute_tdr()

    def _recompute_tdr(self):
        result = self._current_calibrated("reflection")
        if result is None or self.tdr_ax is None:
            return
        freqs, values = result
        try:
            vf = float(self.tdr_vf_var.get())
        except ValueError:
            messagebox.showerror("Invalid input", "Velocity factor must be a number (e.g. 0.66).")
            return
        window = self.tdr_window_var.get()
        try:
            threshold_pct = float(self.tdr_threshold_var.get())
            if not (0 < threshold_pct <= 100):
                raise ValueError
        except ValueError:
            messagebox.showerror("Invalid input", "1st-reflection threshold must be a number between 0 and 100.")
            return

        try:
            tdr_result = compute_tdr(freqs, values, velocity_factor=vf, window=window)
        except ValueError as e:
            messagebox.showerror("TDR error", str(e))
            return

        ax = self.tdr_ax
        ax.clear()
        ax.plot(tdr_result.distance_m, tdr_result.magnitude, color=theme.LED_BLUE, linewidth=1.3)
        ax.set_xlabel("Distance (m), one-way")
        ax.set_ylabel("Response (normalized)")
        ax.grid(True, linewidth=0.4, alpha=0.5)

        peak_m = tdr_result.peak_distance_m()
        first_m = tdr_result.first_significant_distance_m(threshold=threshold_pct / 100.0)
        # Same physical reflection found by both metrics (the common case,
        # a single discontinuity) -- draw one line instead of two
        # overlapping ones with overlapping labels.
        same_reflection = abs(peak_m - first_m) < 1e-9
        if peak_m > 0:
            ax.axvline(peak_m, color=theme.LED_AMBER, linewidth=1.0, linestyle="--")
            ax.annotate(f"largest: {peak_m:.2f} m ({peak_m*3.28084:.2f} ft)",
                        xy=(peak_m, 1.0), xytext=(peak_m, 1.05),
                        fontsize=8, color=theme.LED_AMBER, ha="center")
        if first_m > 0 and not same_reflection:
            ax.axvline(first_m, color=theme.LED_GREEN, linewidth=1.0, linestyle=":")
            ax.annotate(f"1st: {first_m:.2f} m ({first_m*3.28084:.2f} ft)",
                        xy=(first_m, 1.0), xytext=(first_m, 0.92),
                        fontsize=8, color=theme.LED_GREEN, ha="center")
        # first_m is always <= peak_m by construction (the largest
        # reflection's own magnitude is always >= any <=100% threshold, so
        # it's always itself a candidate -- first_significant_distance_m
        # can only return something nearer, never farther), so framing
        # off peak_m alone always fits both lines.
        ax.set_xlim(0, max(peak_m * 2.2, 1.0))
        ax.set_ylim(0, 1.15)

        cal_note = "calibrated" if self.osl_cal else "UNCALIBRATED raw -- results are approximate"
        range_note = self.cal_range_note.cget("text")
        range_suffix = f" {range_note}" if range_note else ""
        first_desc = (
            "same as largest (single discontinuity)" if same_reflection
            else f"{first_m:.2f} m ({first_m*3.28084:.2f} ft)"
        )
        self.tdr_info.configure(text=(
            f"Largest reflection at {peak_m:.2f} m ({peak_m*3.28084:.2f} ft). "
            f"First reflection >={threshold_pct:g}% of peak at {first_desc}. "
            f"VF={vf:g}, window={window}. Data: {cal_note}.{range_suffix} Bandpass TDR "
            f"(no DC point assumed) -- see tdr.py docstring for method details."
        ))

        self.tdr_canvas.draw_idle()

    def _quick_tdr_sweep(self):
        """One-click fix for the classic 'TDR reports a wildly wrong
        distance' symptom: that happens when the loaded reflection sweep
        doesn't span enough bandwidth (e.g. the default 1-30 MHz sweep
        panel range) for the IFFT to resolve a short cable -- see the
        TDR_QUICK_SWEEP_* comment near the top of this file. Runs a real
        reflection sweep at a wide preset range/point count and refreshes
        the TDR plot once it lands, instead of just changing the mode/
        range/points fields and leaving the user to click Sweep."""
        if self.vna is None:
            messagebox.showerror("Not connected", "Connect to the device first.")
            return
        if self.continuous_var.get():
            messagebox.showerror("Continuous sweep running", "Stop continuous sweep first.")
            return
        self.mode_var.set("reflection")
        self.start_var.set(f"{TDR_QUICK_SWEEP_START_MHZ:.3f}")
        self.stop_var.set(f"{TDR_QUICK_SWEEP_STOP_MHZ:.3f}")
        self.points_var.set(str(TDR_QUICK_SWEEP_POINTS))
        # If the current OSL cal doesn't cover this wider range, the sweep
        # completion handler's _update_cal_range_note will already flag
        # that on self.cal_range_note (same as any other out-of-cal-range
        # sweep) -- _recompute_tdr folds that note into tdr_info below so
        # it's visible without checking the main window.
        self._tdr_quick_sweep_pending = True
        self._on_sweep()

    # -- markers ------------------------------------------------------------

    def _clear_selected_marker(self):
        i = self.selected_marker_var.get()
        self.markers[i] = {"active": False, "refl_idx": None, "trans_idx": None}
        self._update_marker_data_panel()
        self._redraw_plots()

    def _clear_all_markers(self):
        self.markers = [{"active": False, "refl_idx": None, "trans_idx": None} for _ in range(MAX_MARKERS)]
        self._update_marker_data_panel()
        self._redraw_plots()

    def _on_plot_click(self, event):
        if event.inaxes is None or event.xdata is None:
            return
        m = self.selected_marker_var.get()

        if event.inaxes is self.axes.get("smith"):
            refl = self.last.get("reflection")
            if refl is None:
                return
            freqs, raw, calibrated = refl
            values = calibrated if calibrated is not None else raw
            idx = min(range(len(values)),
                      key=lambda i: (values[i].real - event.xdata) ** 2 + (values[i].imag - event.ydata) ** 2)
            self.markers[m]["active"] = True
            self.markers[m]["refl_idx"] = idx
            self._update_marker_data_panel()
            self._redraw_plots()
            return

        if event.inaxes in (self.axes.get("rl"), self.axes.get("swr")):
            refl = self.last.get("reflection")
            if refl is None:
                return
            freqs, raw, calibrated = refl
            idx = min(range(len(freqs)), key=lambda i: abs(freqs[i] / 1e6 - event.xdata))
            self.markers[m]["active"] = True
            self.markers[m]["refl_idx"] = idx
            self._update_marker_data_panel()
            self._redraw_plots()
            return

        if event.inaxes in (self.axes.get("s21_mag"), self.axes.get("s21_phase")):
            trans = self.last.get("transmission")
            if trans is None:
                return
            freqs, raw, calibrated = trans
            idx = min(range(len(freqs)), key=lambda i: abs(freqs[i] / 1e6 - event.xdata))
            self.markers[m]["active"] = True
            self.markers[m]["trans_idx"] = idx
            self._update_marker_data_panel()
            self._redraw_plots()
            return

    def _update_marker_data_panel(self):
        """Rebuilds each marker's little parameter table from scratch on
        every update (cheap -- this only runs on marker clicks/new sweep
        data, not per-frame) rather than trying to reuse/reposition a fixed
        set of row widgets, since which sections (S11/S21) are present
        varies per marker. One row per parameter, name/value in their own
        grid column, so values line up instead of running together in a
        single wrapped sentence."""
        for i, marker in enumerate(self.markers):
            table = self.marker_data_labels.get(i, {}).get("table")
            if table is None:
                continue
            for child in table.winfo_children():
                child.destroy()

            row = 0

            def add_header(text):
                nonlocal row
                self._label(table, text, font=theme.FONT_UI_BOLD).grid(
                    row=row, column=0, columnspan=2, sticky="w", pady=(6 if row else 0, 1))
                row += 1

            def add_row(name, value):
                nonlocal row
                self._label(table, name, dim=True).grid(
                    row=row, column=0, sticky="w", padx=(0, 10))
                self._label(table, value, font=theme.FONT_METER_VALUE).grid(
                    row=row, column=1, sticky="w")
                row += 1

            if not marker["active"]:
                self._label(table, "not set", dim=True).grid(row=0, column=0, sticky="w")
                continue

            had_data = False
            refl = self.last.get("reflection")
            if marker["refl_idx"] is not None and refl is not None:
                freqs, raw, calibrated = refl
                idx = marker["refl_idx"]
                if idx < len(freqs):
                    had_data = True
                    values = calibrated if calibrated is not None else raw
                    f, v = freqs[idx], values[idx]
                    z = gamma_to_impedance(v)
                    y = gamma_to_admittance(v)
                    q = gamma_to_q_factor(v)
                    ml = gamma_to_mismatch_loss_db(v)
                    cal_note = "" if calibrated is not None else " (uncal)"
                    add_header(f"S11 @ {f/1e6:.4f} MHz{cal_note}")
                    add_row("Gamma", f"{v.real:+.3f}{v.imag:+.3f}j")
                    add_row("Angle", f"{gamma_to_phase_deg(v):.1f} deg")
                    add_row("|Gamma|", f"{abs(v):.3f}")
                    add_row(self._rl_label(), f"{self._rl_db(v):.2f} dB")
                    add_row("SWR", f"{min(gamma_to_swr(v), 99):.2f}")
                    add_row("Mismatch loss", f"{ml:.2f} dB")
                    add_row("Z", f"{z.real:.1f}{z.imag:+.1f}j ohm")
                    add_row("Q", f"{q:.2f}")
                    add_row("Y", f"{y.real*1000:.2f}{y.imag*1000:+.2f}j mS")

            trans = self.last.get("transmission")
            if marker["trans_idx"] is not None and trans is not None:
                freqs, raw, calibrated = trans
                idx = marker["trans_idx"]
                if idx < len(freqs):
                    had_data = True
                    values = calibrated if calibrated is not None else raw
                    f, v = freqs[idx], values[idx]
                    phase = math.degrees(math.atan2(v.imag, v.real))
                    cal_note = "" if calibrated is not None else " (uncal)"
                    gd = compute_group_delay_ns(freqs, values, idx)
                    gd_text = f"{gd:.2f} ns" if gd is not None else "n/a (edge point)"
                    add_header(f"S21 @ {f/1e6:.4f} MHz{cal_note}")
                    add_row("|S21|", f"{s21_to_db(v):.2f} dB")
                    add_row("Phase", f"{phase:.1f} deg")
                    add_row("Group delay", gd_text)

            if not had_data:
                self._label(table, "not set", dim=True).grid(row=0, column=0, sticky="w")

    # -- queue polling / plotting -----------------------------------------

    def _poll_queue(self):
        try:
            while True:
                r: SweepResult = self.result_queue.get_nowait()
                try:
                    self._handle_result(r)
                except Exception as e:  # noqa: BLE001 -- see comment below
                    # Without this, an unexpected exception anywhere in
                    # _handle_result (a new bug, a data edge case not
                    # anticipated) would propagate straight past the
                    # self.root.after(100, self._poll_queue) call at the
                    # bottom of this method -- silently killing the
                    # recurring poll FOREVER. Every later sweep result
                    # would just pile up in the queue, never drawn: the
                    # screen freezes on whatever was last plotted, with no
                    # error shown (especially in the windowed .exe build,
                    # which has no console to even print a traceback to).
                    # That's indistinguishable from "the app just stopped
                    # updating" -- exactly the failure mode to guard
                    # against. Catching it here, per-result, means one bad
                    # result gets discarded (it's already been pulled off
                    # the queue) and everything else keeps working,
                    # instead of the whole app going quietly inert.
                    self.continuous_var.set(False)
                    self._set_busy_controls(False)
                    self.sweep_btn.configure(text="Sweep")
                    self.status_label.configure(text="Error", fg=theme.BAD)
                    messagebox.showerror("Unexpected error", f"{type(e).__name__}: {e}")
        except queue.Empty:
            pass
        self.root.after(100, self._poll_queue)

    def _handle_result(self, r: SweepResult):
        if r.kind == "error":
            # Stop Continuous on any error rather than hammering a broken
            # connection with an unattended retry loop.
            self.continuous_var.set(False)
            self._set_busy_controls(False)
            self.sweep_btn.configure(text="Sweep")
            self.status_label.configure(text="Error", fg=theme.BAD)
            messagebox.showerror("Error", r.message)
            return

        if r.kind == "connected":
            self.vna = self._connected_vna
            self.connect_btn.configure(text="Disconnect")
            self.status_label.configure(text=f"Connected: {r.message}", fg=theme.GOOD)
            return

        if r.kind == "progress":
            pct = (r.current / r.total * 100) if r.total else 0
            self.progress.config(value=pct)
            self.progress_label.configure(text=f"{r.current}/{r.total} ({pct:.0f}%) -- {r.mode}")
            return

        if r.kind == "sweep":
            self.progress.config(value=100)
            self.progress_label.configure(text="Done")
            avg_values = self._apply_sweep_averaging(r.mode, r.frequencies_hz, r.values)
            calibrated = None
            cal = self.osl_cal if r.mode == "reflection" else self.thru_cal
            if cal is not None:
                calibrated = cal.correct_sweep(avg_values, r.frequencies_hz)
                self._update_cal_range_note(r.mode, r.frequencies_hz, cal)
            else:
                self.cal_range_note.configure(text="")
            self.last[r.mode] = (r.frequencies_hz, avg_values, calibrated)
            # Invalidate just this mode's index on every marker -- new
            # sweep data means an old index could point at the wrong
            # frequency now. A marker with nothing left on either mode
            # (e.g. it was only ever placed on this mode) goes inactive;
            # one also placed on the other mode keeps that half.
            idx_key = "refl_idx" if r.mode == "reflection" else "trans_idx"
            for marker in self.markers:
                marker[idx_key] = None
                if marker["refl_idx"] is None and marker["trans_idx"] is None:
                    marker["active"] = False
            self._update_marker_data_panel()
            self._redraw_plots()
            if self.continuous_var.get():
                self._trigger_sweep()  # next lap -- stays busy/disabled, no re-enable yet
            elif self._sweep_chain_remaining > 0:
                # Chaining automatic laps to fill the averaging buffer for
                # a single "Sweep" click (see _on_sweep) -- not Continuous.
                self._sweep_chain_remaining -= 1
                avg_n = self._get_avg_count()
                done = avg_n - self._sweep_chain_remaining
                self.sweep_btn.configure(text=f"Averaging ({done}/{avg_n})...")
                self._trigger_sweep()
            else:
                self._set_busy_controls(False)
                self.sweep_btn.configure(text="Sweep")
                if self._tdr_quick_sweep_pending and r.mode == "reflection":
                    self._tdr_quick_sweep_pending = False
                    if self.tdr_window is not None and self.tdr_window.winfo_exists():
                        self._recompute_tdr()
            return

        if r.kind == "capture":
            self.progress.config(value=100)
            self.progress_label.configure(text="Done")
            self.cal_captures[r.label] = (r.frequencies_hz, r.values)
            self._update_cal_status()
            self._set_busy_controls(False)
            return

    def _update_cal_range_note(self, mode, freqs, cal):
        """Updates the passive (non-popup) note under the Calibration
        status line. Silent (empty) when the sweep matches the
        calibration's own grid exactly -- the normal case. Otherwise notes
        that interpolation was used, or -- only when the sweep's range
        genuinely extends past what was ever measured -- warns that part
        of the trace is extrapolated (held-flat past the last calibrated
        point, see error_terms_at docstrings) and less trustworthy there."""
        cal_freqs = cal.frequencies_hz
        if list(freqs) == cal_freqs:
            self.cal_range_note.configure(text="")
            return
        label = "Reflection" if mode == "reflection" else "Transmission"
        lo_cal, hi_cal = cal_freqs[0] / 1e6, cal_freqs[-1] / 1e6
        if freqs[0] < cal_freqs[0] - 1.0 or freqs[-1] > cal_freqs[-1] + 1.0:
            self.cal_range_note.configure(text=(
                f"{label}: sweep extends beyond the calibrated {lo_cal:.3f}-{hi_cal:.3f} MHz "
                f"range -- extrapolated there, less accurate outside it."
            ))
        else:
            self.cal_range_note.configure(text=(
                f"{label}: calibration interpolated onto this sweep "
                f"(calibrated at {lo_cal:.3f}-{hi_cal:.3f} MHz)."
            ))

    def _add_band_overlays(self, ax, freq_lo_hz, freq_hi_hz):
        if not self.show_bands_var.get():
            return
        for name, b_start, b_stop in bands_overlapping(freq_lo_hz, freq_hi_hz):
            ax.axvspan(b_start / 1e6, b_stop / 1e6, color=theme.FG_DIM, alpha=0.12, zorder=0)
            mid = (b_start + b_stop) / 2 / 1e6
            ax.text(mid, 0.97, name, transform=ax.get_xaxis_transform(),
                    fontsize=6.5, color=theme.FG_DIM, ha="center", va="top", zorder=0)

    def _apply_axis_scale(self, ax, key):
        """Apply the manual min/max for `key` if its Auto box is unchecked;
        otherwise leave matplotlib's autoscale alone."""
        if self.scale_auto_var[key].get():
            return
        try:
            lo = float(self.scale_min_var[key].get())
            hi = float(self.scale_max_var[key].get())
        except ValueError:
            return  # bad input -- silently fall back to auto rather than crash the redraw
        if lo >= hi:
            return
        ax.set_ylim(lo, hi)

    def _rl_db(self, gamma) -> float:
        """Return Loss in dB, in whichever sign convention the user picked
        (see the "Negative dB (S11 convention)" checkbox docstring/comment
        in _build_display_panel) -- same underlying |Gamma|, just negated
        for display. gamma_to_return_loss_db can return +inf at a perfect
        match; negating that is -inf, which matplotlib/f-string formatting
        both handle fine, no special-casing needed here."""
        v = gamma_to_return_loss_db(gamma)
        return -v if self.rl_negative_var.get() else v

    def _rl_label(self) -> str:
        return "S11" if self.rl_negative_var.get() else "Return Loss"

    def _on_rl_sign_toggle(self):
        # If a manual (Auto off) RL axis range is set, keep it sensible
        # across the flip by swapping and negating min/max -- otherwise a
        # range set up for one convention (e.g. 0..50) would silently
        # become nonsense in the other (0..50 in S11-negative-dB terms
        # excludes almost all real passive data) instead of just following
        # the flip the same way the plotted data does.
        try:
            lo = float(self.scale_min_var["rl"].get())
            hi = float(self.scale_max_var["rl"].get())
            # + 0.0 normalizes a -0.0 result (e.g. negating hi=0.0) back to
            # a plain 0.0 -- correct either way, just avoids a confusing
            # "-0.0" appearing in the entry field.
            self.scale_min_var["rl"].set(str(-hi + 0.0))
            self.scale_max_var["rl"].set(str(-lo + 0.0))
        except ValueError:
            pass  # non-numeric manual range -- leave it alone, _apply_axis_scale falls back to auto anyway
        self._redraw_plots()

    def _get_smoothing_window(self) -> int:
        try:
            n = int(self.smoothing_var.get())
        except ValueError:
            return 1
        return max(1, min(n, 51))

    @staticmethod
    def _smooth(values, window: int):
        """Centered moving-average smoothing of a plotted trace -- display
        only, applied to whatever's actually drawn (RL/SWR/dB/phase/Smith
        re-im), never to self.last (the calibrated-or-raw data markers,
        export, and TDR all read from) -- so smoothing a jagged-looking
        curve never quietly alters an exported .s1p/.csv or a marker
        readout. window=1 is a no-op. The window shrinks near the edges
        (averages over whatever neighbors exist) rather than leaving the
        ends unsmoothed or shortening the array -- every point stays
        plotted, so the x-axis/marker-index alignment is unaffected."""
        n = len(values)
        if window <= 1 or n == 0:
            return list(values)
        half = window // 2
        out = []
        for i in range(n):
            lo = max(0, i - half)
            hi = min(n, i + half + 1)
            chunk = values[lo:hi]
            out.append(sum(chunk) / len(chunk))
        return out

    @classmethod
    def _smooth_phase_deg(cls, phase_deg, window):
        """Same idea as _smooth(), but for a wrapped [-180, 180] phase
        trace -- averaging raw wrapped values straight across a wrap point
        (e.g. neighbors +179 deg and -179 deg) would produce a nonsense
        ~0 deg average instead of ~180 deg. Same wrap pitfall documented
        in group_delay.py for phase differencing; fixed here the standard
        way -- cumulatively unwrap to a phase-continuous sequence, smooth
        that, then wrap the smoothed result back to [-180, 180] for
        display."""
        if window <= 1 or not phase_deg:
            return list(phase_deg)
        unwrapped = [phase_deg[0]]
        for i in range(1, len(phase_deg)):
            delta = phase_deg[i] - phase_deg[i - 1]
            delta = (delta + 180) % 360 - 180  # shortest-path delta, handles the wrap
            unwrapped.append(unwrapped[-1] + delta)
        smoothed = cls._smooth(unwrapped, window)
        return [((v + 180) % 360) - 180 for v in smoothed]

    @staticmethod
    def _draw_marker_point(ax, x, y, color, label):
        ax.plot(x, y, "x", color=color, markersize=9, markeredgewidth=2, zorder=6)
        ax.annotate(label, xy=(x, y), xytext=(4, 4), textcoords="offset points",
                    fontsize=7, color=color, zorder=6)

    def _redraw_plots(self):
        self.fig.clear()
        self.axes = {}

        visible = [k for k in PLOT_KEYS if self.show_plot_var[k].get()]
        n = len(visible)
        if n == 0:
            self.canvas.draw_idle()
            return

        if "smith" in visible and n > 1:
            # Smith chart gets a dedicated large cell (full height, wider
            # column) instead of one equal share of an N-way grid -- at
            # equal shares it was too small to read (reported directly:
            # "way too small to be useful"). Other panels stack in the
            # remaining column.
            others = [k for k in visible if k != "smith"]
            gs = self.fig.add_gridspec(nrows=len(others), ncols=2, width_ratios=[3, 2])
            grid_axes = {"smith": self.fig.add_subplot(gs[:, 0])}
            for i, key in enumerate(others):
                grid_axes[key] = self.fig.add_subplot(gs[i, 1])
        else:
            # Stacked (one column), not side-by-side -- e.g. the default
            # Return Loss + SWR view reads better as two full-width plots
            # stacked vertically than two half-width ones side by side.
            grid_axes = {key: self.fig.add_subplot(n, 1, i + 1) for i, key in enumerate(visible)}

        for key, ax in grid_axes.items():
            self.axes[key] = ax
            if key == "smith":
                draw_smith_grid(ax)
                ax.set_title("Smith Chart (S11)", fontsize=11)
            elif key == "rl":
                ax.set_title(self._rl_label(), fontsize=9)
                ax.set_xlabel("MHz", fontsize=8)
                ax.set_ylabel(f"{self._rl_label()} (dB)", fontsize=8)
                ax.grid(True, linewidth=0.4, alpha=0.5)
            elif key == "swr":
                ax.set_title("SWR", fontsize=9)
                ax.set_xlabel("MHz", fontsize=8)
                ax.set_ylabel("SWR", fontsize=8)
                ax.grid(True, linewidth=0.4, alpha=0.5)
            elif key == "s21_mag":
                ax.set_title("|S21| (dB)", fontsize=9)
                ax.set_xlabel("MHz", fontsize=8)
                ax.grid(True, linewidth=0.4, alpha=0.5)
            elif key == "s21_phase":
                ax.set_title("S21 phase (deg)", fontsize=9)
                ax.set_xlabel("MHz", fontsize=8)
                ax.grid(True, linewidth=0.4, alpha=0.5)

        smooth_n = self._get_smoothing_window()

        refl = self.last.get("reflection")
        if refl is not None:
            freqs, raw, calibrated = refl
            values = calibrated if calibrated is not None else raw
            re = [v.real for v in values]
            im = [v.imag for v in values]
            mhz = [f / 1e6 for f in freqs]
            rl = [self._rl_db(v) for v in values]
            swr = [min(gamma_to_swr(v), 20) for v in values]  # clip for display
            if smooth_n > 1:
                re, im, rl, swr = (self._smooth(a, smooth_n) for a in (re, im, rl, swr))

            if "smith" in self.axes:
                ax = self.axes["smith"]
                ax.plot(re, im, color=theme.LED_BLUE, linewidth=1.5)
                ax.plot(re[-1], im[-1], "o", color=theme.LED_BLUE, markersize=4)

            if "rl" in self.axes:
                ax = self.axes["rl"]
                self._add_band_overlays(ax, freqs[0], freqs[-1])
                ax.plot(mhz, rl, color=theme.LED_BLUE, linewidth=1.3)
                self._apply_axis_scale(ax, "rl")

            if "swr" in self.axes:
                ax = self.axes["swr"]
                self._add_band_overlays(ax, freqs[0], freqs[-1])
                ax.plot(mhz, swr, color=theme.LED_AMBER, linewidth=1.3)
                self._apply_axis_scale(ax, "swr")

            for m, marker in enumerate(self.markers):
                idx = marker["refl_idx"]
                if not marker["active"] or idx is None or idx >= len(freqs):
                    continue
                color, label = MARKER_COLORS[m], f"M{m + 1}"
                if "smith" in self.axes:
                    self._draw_marker_point(self.axes["smith"], re[idx], im[idx], color, label)
                if "rl" in self.axes:
                    ax = self.axes["rl"]
                    ax.axvline(mhz[idx], color=color, linewidth=0.8, linestyle=":", zorder=4)
                    self._draw_marker_point(ax, mhz[idx], rl[idx], color, label)
                if "swr" in self.axes:
                    ax = self.axes["swr"]
                    ax.axvline(mhz[idx], color=color, linewidth=0.8, linestyle=":", zorder=4)
                    self._draw_marker_point(ax, mhz[idx], swr[idx], color, label)

        trans = self.last.get("transmission")
        if trans is not None:
            freqs, raw, calibrated = trans
            values = calibrated if calibrated is not None else raw
            mhz = [f / 1e6 for f in freqs]
            db = [s21_to_db(v) for v in values]
            phase = [math.degrees(math.atan2(v.imag, v.real)) for v in values]
            if smooth_n > 1:
                db = self._smooth(db, smooth_n)
                phase = self._smooth_phase_deg(phase, smooth_n)

            if "s21_mag" in self.axes:
                ax = self.axes["s21_mag"]
                self._add_band_overlays(ax, freqs[0], freqs[-1])
                ax.plot(mhz, db, color=theme.LED_GREEN, linewidth=1.3)
                self._apply_axis_scale(ax, "s21_mag")
            if "s21_phase" in self.axes:
                ax = self.axes["s21_phase"]
                self._add_band_overlays(ax, freqs[0], freqs[-1])
                ax.plot(mhz, phase, color=theme.LED_GREEN, linewidth=1.3)
                self._apply_axis_scale(ax, "s21_phase")

            for m, marker in enumerate(self.markers):
                idx = marker["trans_idx"]
                if not marker["active"] or idx is None or idx >= len(freqs):
                    continue
                color, label = MARKER_COLORS[m], f"M{m + 1}"
                if "s21_mag" in self.axes:
                    ax = self.axes["s21_mag"]
                    ax.axvline(mhz[idx], color=color, linewidth=0.8, linestyle=":", zorder=4)
                    self._draw_marker_point(ax, mhz[idx], db[idx], color, label)
                if "s21_phase" in self.axes:
                    ax = self.axes["s21_phase"]
                    ax.axvline(mhz[idx], color=color, linewidth=0.8, linestyle=":", zorder=4)
                    self._draw_marker_point(ax, mhz[idx], phase[idx], color, label)

        self.canvas.draw_idle()


def _enable_windows_dpi_awareness():
    """Without this, Windows applies legacy DPI virtualization to a
    non-DPI-aware process on any display scaled above 100% (125%, 150%,
    etc. -- common on most laptops): it silently renders the whole window
    to a smaller off-screen buffer and bitmap-stretches it to fit, and
    newly-exposed regions after a resize can show up unpainted (observed:
    a persistent gap in the bottom-right corner after maximizing, sized
    suspiciously close to a 1/1.25 ratio -- consistent with 125% scaling).
    Marking the process DPI-aware tells Windows to skip that virtualization
    and let Tk render at native resolution instead."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # PROCESS_SYSTEM_DPI_AWARE
    except Exception:
        try:
            import ctypes
            ctypes.windll.user32.SetProcessDPIAware()  # older Windows fallback
        except Exception:
            pass  # not fatal -- worst case, back to the pre-fix behavior


def main():
    _enable_windows_dpi_awareness()
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
