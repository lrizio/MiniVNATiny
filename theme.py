"""Shared visual theme for this app.

Ported verbatim (2026-08-31) from Yaesu Radio Controller's
yaesu_controller/theme.py (itself shared with FT710 Remote Desktop) for
"similar look and feel" consistency across this ham-projects family of
Tkinter apps -- same palette, fonts, and rounded-panel/rounded-button
conventions, no changes here. If you touch this file, consider whether
the change belongs upstream in the shared original instead so the family
doesn't drift apart.

2026-08-20 restyle (from the original project's history, carried over
here): moved from the earlier brushed-metal/raised-bevel look to a
flatter, rounded-corner look per direct feedback that the raised boxes
and bevels "look tacky" -- sections are now a thin rounded-corner outline
with the title inline on the border (like an HTML fieldset/legend)
instead of a filled metal box with its own titlebar bar, and buttons are
flat rounded rectangles instead of tk.Button's raised/sunken bevel.
Tkinter has neither of those natively (no border-radius, no fieldset),
so both are hand-drawn on a Canvas -- see rounded_rect()/RoundedButton/
rounded_panel() below.
"""
from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

METAL_HI = "#3a414c"
METAL_LO = "#232830"
METAL_EDGE = "#12161b"
SCREEN = "#080d10"
SCREEN2 = "#0e161a"
TXT = "#eef3f8"
MUTED = "#a7b3c0"
LED_GREEN = "#57e39a"
LED_AMBER = "#ffbf5f"
LED_BLUE = "#6cb6ff"
LED_RED = "#ff7a6b"
PINK = "#d9a8ff"
GRN = "#7ee787"
BTN_RED = "#c2402f"
BTN_RED_ACTIVE = "#7c221a"

BG = "#14181d"           # page background -- panels/buttons all sit directly on this now
BG_PANEL = SCREEN2        # kept for any leftover reference; new panels don't fill a background
BG_HEADER = METAL_LO      # kept for any leftover reference
LINE = METAL_HI           # rounded-panel outline / thin separator color
FG = TXT
FG_DIM = MUTED
ACCENT = LED_BLUE
GOOD = LED_GREEN
WARN = LED_AMBER
BAD = BTN_RED

FONT_UI = ("Segoe UI", 10)
FONT_UI_BOLD = ("Segoe UI", 10, "bold")
FONT_LABEL = ("Segoe UI", 11)
FONT_SECTION = ("Segoe UI", 10, "bold")
FONT_READOUT = ("Consolas", 30, "bold")
FONT_READOUT_SMALL = ("Consolas", 20, "bold")
FONT_READOUT_TINY = ("Consolas", 14, "bold")
FONT_METER_VALUE = ("Consolas", 11)


def configure(root: tk.Tk) -> None:
    root.configure(bg=BG)
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    style.configure("TFrame", background=BG)
    style.configure("TLabel", background=BG, foreground=FG, font=FONT_UI)
    style.configure("Dim.TLabel", background=BG, foreground=FG_DIM, font=FONT_UI)
    style.configure("Section.TLabel", background=BG, foreground=FG_DIM, font=FONT_SECTION)

    style.configure("TCombobox", fieldbackground=SCREEN, background=METAL_HI, foreground=FG,
                     arrowcolor=FG)
    style.map("TCombobox", fieldbackground=[("readonly", SCREEN)])
    root.option_add("*TCombobox*Listbox.font", FONT_UI)
    root.option_add("*TCombobox*Listbox.background", SCREEN)
    root.option_add("*TCombobox*Listbox.foreground", FG)
    root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
    root.option_add("*TCombobox*Listbox.selectForeground", METAL_EDGE)

    style.configure("Level.Horizontal.TScale", background=BG, troughcolor=SCREEN)

    style.configure("TEntry", fieldbackground=SCREEN, foreground=LED_BLUE, insertcolor=LED_BLUE)


# --- rounded-rectangle drawing -------------------------------------------
# Tkinter has no native rounded-rect primitive. The standard trick:
# create_polygon(..., smooth=True) runs a quadratic B-spline through the
# given points, which rounds any corner where the path is given as two
# points short of the actual corner (one on each adjacent edge) instead
# of the corner point itself.

def _rounded_points(x1: float, y1: float, x2: float, y2: float, r: float) -> list[float]:
    r = max(0.0, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
    return [
        x1 + r, y1,  x2 - r, y1,  x2, y1,
        x2, y1 + r,  x2, y2 - r,  x2, y2,
        x2 - r, y2,  x1 + r, y2,  x1, y2,
        x1, y2 - r,  x1, y1 + r,  x1, y1,
        x1 + r, y1,
    ]


def rounded_rect(canvas: tk.Canvas, x1: float, y1: float, x2: float, y2: float,
                  radius: float = 12, **kwargs) -> int:
    """Draws a rounded rectangle on `canvas` and returns its item id."""
    return canvas.create_polygon(_rounded_points(x1, y1, x2, y2, radius), smooth=True, **kwargs)


def _clamp8(v: float) -> int:
    return max(0, min(255, int(round(v))))


def _lighten(hex_color: str, amount: int) -> str:
    """Shifts `hex_color` toward white (amount>0) or black (amount<0) by
    `amount` per channel -- the shading math behind RoundedButton's
    highlight-cap/shadow/pressed-state layers below."""
    r = int(hex_color[1:3], 16)
    g = int(hex_color[3:5], 16)
    b = int(hex_color[5:7], 16)
    return f"#{_clamp8(r + amount):02x}{_clamp8(g + amount):02x}{_clamp8(b + amount):02x}"


def _darken(hex_color: str, amount: int) -> str:
    return _lighten(hex_color, -amount)


# --- buttons ---------------------------------------------------------------

# Drop-shadow offset -- the Canvas itself is padded this much larger than
# the button's own logical width/height so the shadow has somewhere to be
# drawn beyond the button shape's own edge (see RoundedButton.__init__).
_BTN_SHADOW_DX = 5
_BTN_SHADOW_DY = 6


class RoundedButton(tk.Canvas):
    """A rounded-corner button with real depth -- tk.Button has no
    border-radius, so this draws its own on a small Canvas: a drop shadow,
    a glossy top-half highlight cap, and a darker sunk-in look while held
    (shape nudges into where the shadow was, highlight swaps for an inner
    shadow), rather than a flat single-color fill. Supports the subset of
    tk.Button's configure() options this app actually uses (text, bg,
    activebackground, fg, state, command), so existing call sites that
    reconfigure a button after creation (PTT's press/release recoloring,
    ATU/TUNE's enable-on-condition, etc.) don't need to change."""

    def __init__(self, parent, text: str, command=None, *, width: int | None = None,
                 height: int = 40, radius: int = 10, bg: str = METAL_HI, active_bg: str = ACCENT,
                 fg: str = FG, font=FONT_UI_BOLD, page_bg: str = BG, padx: int = 18,
                 state: str = "normal") -> None:
        self._font = font
        self._text = text
        self._bg = bg
        self._active_bg = active_bg
        self._fg = fg
        self._radius = radius
        self._state = state
        self._pressed = False
        self._command = command
        if width is None:
            width = tkfont.Font(font=font).measure(text) + padx * 2
        # NOT self._w/self._h -- tkinter.Misc already uses self._w
        # internally for the widget's Tk pathname; clobbering it breaks
        # every child widget creation with a cryptic "unsupported operand
        # type(s) for -: 'str' and 'int'" (hit this exact bug once already
        # in widgets.py's SMeterBar/VerticalBar -- reintroduced it here by
        # not checking against that lesson, now fixed the same way).
        self._btn_w = width
        self._btn_h = height
        # The widget itself is bigger than the logical button size -- the
        # extra margin at bottom-right is where the drop shadow lives.
        # page_bg has to be right for the shadow's dithered stipple fill
        # to blend correctly (see _draw()).
        super().__init__(parent, width=width + _BTN_SHADOW_DX, height=height + _BTN_SHADOW_DY,
                          bg=page_bg, highlightthickness=0, bd=0)
        self._draw()
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.configure(cursor="hand2")

    def _draw(self) -> None:
        self.delete("all")
        w, h, r = self._btn_w, self._btn_h, self._radius
        # Real bug fixed here (reported directly: button text unreadable
        # while disabled): disabled state only ever swapped the TEXT color
        # to MUTED, never the background -- fine for a button using the
        # default METAL_HI bg (dark enough for MUTED text to read against),
        # but Connect/Sweep use bg=ACCENT (bright LED_BLUE), and MUTED text
        # on a still-bright ACCENT background is illegible (both light).
        # Disabled buttons now always use a fixed dim background regardless
        # of their configured bg/active_bg, so this can't recur for any
        # button color combination.
        if self._state == "disabled":
            base = METAL_LO
            text_fill = MUTED
        else:
            base = self._active_bg if self._pressed else self._bg
            text_fill = self._fg

        if self._pressed:
            # Sunk into the page: no shadow, shape nudged down-right into
            # the space the shadow normally occupies, dark inner-top
            # shading instead of a bright highlight -- reads as physically
            # pressed in, not just recolored.
            dx, dy = _BTN_SHADOW_DX * 0.6, _BTN_SHADOW_DY * 0.6
            rounded_rect(self, dx, dy, dx + w - 2, dy + h - 2, r,
                         fill=_darken(base, 18), outline=_darken(base, 55), width=2)
            cap_h = max(6, (h - 6) * 0.4)
            rounded_rect(self, dx + 3, dy + 3, dx + w - 5, dy + 3 + cap_h, max(2, r - 3),
                         fill=_darken(base, 32), outline="")
            tx, ty = dx + w / 2, dy + h / 2
        else:
            # Raised off the page: dithered drop shadow behind (stipple
            # fakes the transparency a real shadow needs -- Tkinter canvas
            # fills are opaque otherwise), a clearly darker edge, and a
            # glossy lighter cap across the top ~40% to catch the light.
            rounded_rect(self, _BTN_SHADOW_DX, _BTN_SHADOW_DY, _BTN_SHADOW_DX + w - 2, _BTN_SHADOW_DY + h - 2,
                         r, fill="#000000", outline="", stipple="gray50")
            rounded_rect(self, 0, 0, w - 2, h - 2, r, fill=base, outline=_darken(base, 45), width=2)
            cap_h = max(8, (h - 6) * 0.42)
            rounded_rect(self, 3, 3, w - 5, 3 + cap_h, max(2, r - 3), fill=_lighten(base, 40), outline="")
            tx, ty = w / 2 - 1, h / 2 - 1

        self.create_text(tx, ty, text=self._text, fill=text_fill, font=self._font)

    def _on_press(self, _event) -> None:
        if self._state == "disabled":
            return
        self._pressed = True
        self._draw()

    def _on_release(self, event) -> None:
        if self._state == "disabled":
            return
        was_pressed = self._pressed
        self._pressed = False
        self._draw()
        inside = 0 <= event.x <= self._btn_w and 0 <= event.y <= self._btn_h
        if was_pressed and inside and self._command is not None:
            self._command()

    def configure(self, **kwargs):  # noqa: A003 -- intentional tk.Widget-style override
        text = kwargs.pop("text", None)
        bg = kwargs.pop("bg", None)
        activebackground = kwargs.pop("activebackground", None)
        fg = kwargs.pop("fg", None)
        state = kwargs.pop("state", None)
        command = kwargs.pop("command", None)
        # Accepted-and-ignored for call-site compatibility with the old
        # tk.Button-based metal_button() -- rounded buttons have no
        # raised/sunken relief, and none of these need touching after
        # construction anywhere in this app.
        kwargs.pop("relief", None)
        kwargs.pop("width", None)
        kwargs.pop("height", None)
        changed = False
        if text is not None:
            self._text = text
            changed = True
        if bg is not None:
            self._bg = bg
            changed = True
        if activebackground is not None:
            self._active_bg = activebackground
            changed = True
        if fg is not None:
            self._fg = fg
            changed = True
        if state is not None:
            self._state = state
            changed = True
        if command is not None:
            self._command = command
        if changed:
            self._draw()
        if kwargs:
            super().configure(**kwargs)

    config = configure

    def __setitem__(self, key, value) -> None:
        self.configure(**{key: value})

    def __getitem__(self, key):
        if key == "text":
            return self._text
        if key == "bg":
            return self._bg
        if key == "state":
            return self._state
        return super().__getitem__(key)


def metal_button(parent, text: str, command=None, *, width: int | None = None, font=FONT_UI_BOLD,
                  bg: str = METAL_HI, active_bg: str = ACCENT, fg: str = FG, height: int = 40,
                  page_bg: str = BG, state: str = "normal") -> RoundedButton:
    """A flat rounded-corner button. `width`, if given, is a PIXEL width
    (unlike tk.Button's character-count `width`) -- pass it to force
    several buttons in a row to the same width for alignment (measure the
    longest label with button_text_width() first); omit it to auto-size
    from the text. Name kept from the earlier raised-metal-button theme so
    call sites didn't all need renaming when the look changed underneath."""
    return RoundedButton(parent, text, command, width=width, height=height, bg=bg,
                          active_bg=active_bg, fg=fg, font=font, page_bg=page_bg, state=state)


def button_text_width(text: str, font=FONT_UI_BOLD, padx: int = 18) -> int:
    """Pixel width metal_button() would auto-size `text` to -- use this to
    compute a shared width across a row of buttons with different-length
    labels (e.g. band presets) so they all line up instead of hugging
    their own text tightly."""
    return tkfont.Font(font=font).measure(text) + padx * 2


# Back-compat alias -- earlier flat-theme name, kept so any straggling
# caller (or a future diff) doesn't silently break.
flat_button = metal_button


def metal_bar(parent, **kwargs) -> tk.Frame:
    """A plain flat bar sitting on the page background -- used for the
    connection toolbar (app-level chrome, not an in-page section, so it
    doesn't get the rounded-outline treatment sections use)."""
    kw = dict(bg=BG)
    kw.update(kwargs)
    return tk.Frame(parent, **kw)


def rounded_panel(parent, title: str | None = None, *, dot_color: str | None = None,
                   bg: str = BG, border_color: str = LINE, radius: int = 14,
                   pad: int = 16) -> tuple[tk.Canvas, tk.Frame]:
    """A thin rounded-corner outline with the section title inline on the
    top border (like an HTML fieldset/legend) -- replaces the earlier
    filled-metal-box-plus-titlebar convention. Pack content into the
    returned `body` frame as normal, then call finalize_rounded_panel()
    once, after everything is built, to measure body's real size and
    actually draw the border/title to fit it (can't be drawn up front --
    the size isn't known until the content exists).
    """
    canvas = tk.Canvas(parent, bg=bg, highlightthickness=0, bd=0)
    body = tk.Frame(canvas, bg=bg)
    top_pad = pad + 10 if title else pad
    body_id = canvas.create_window(pad, top_pad, window=body, anchor="nw")
    canvas._rp = {  # noqa: SLF001 -- private stash read back by finalize_rounded_panel()
        "body": body, "body_id": body_id, "title": title, "dot_color": dot_color,
        "bg": bg, "border_color": border_color, "radius": radius, "pad": pad, "top_pad": top_pad,
    }
    return canvas, body


def measure_rounded_panel_width(canvas: tk.Canvas) -> int:
    """The width finalize_rounded_panel() would use for this panel if
    called with no min_width -- i.e. its own content's natural width.
    Call this on a group of panels that should all line up (e.g. every
    section stacked in one column) to find the widest one, then pass that
    as min_width to finalize_rounded_panel() for all of them so their
    borders share one common width instead of each hugging its own
    content -- this is what makes "edges line up" mean something more
    than "all left-aligned"."""
    rp = canvas._rp  # noqa: SLF001
    rp["body"].update_idletasks()
    return rp["body"].winfo_reqwidth() + rp["pad"] * 2


def finalize_rounded_panel(canvas: tk.Canvas, min_width: int | None = None) -> tk.Canvas | None:
    """Call once after a rounded_panel()'s body is fully built. Returns the
    title dot's Canvas (for later recolor_glow_dot() calls) if the panel
    was given a dot_color, else None. `min_width`, if given, floors the
    panel's width below its own natural content width -- see
    measure_rounded_panel_width() for lining up several panels' borders."""
    rp = canvas._rp  # noqa: SLF001
    body, pad, top_pad = rp["body"], rp["pad"], rp["top_pad"]
    body.update_idletasks()
    w = body.winfo_reqwidth()
    h = body.winfo_reqheight()
    total_w = max(w + pad * 2, min_width or 0)
    total_h = h + top_pad + pad
    canvas.configure(width=total_w, height=total_h)
    canvas.coords(rp["body_id"], pad, top_pad)

    border_top = (top_pad - pad) if rp["title"] else 2
    rounded_rect(canvas, 2, border_top, total_w - 2, total_h - 2, rp["radius"],
                 fill="", outline=rp["border_color"], width=2)

    dot = None
    if rp["title"]:
        title_frame = tk.Frame(canvas, bg=rp["bg"])
        if rp["dot_color"] is not None:
            dot = glow_dot(title_frame, 10, rp["dot_color"], bg=rp["bg"])
            dot.pack(side=tk.LEFT, padx=(0, 6))
        title_label = tk.Label(title_frame, text=rp["title"], bg=rp["bg"], fg=FG, font=FONT_SECTION)
        title_label.pack(side=tk.LEFT)
        rp["title_label"] = title_label
        canvas.create_window(18, border_top, window=title_frame, anchor="w")
    return dot


def set_panel_title(canvas: tk.Canvas, text: str) -> None:
    """Changes an already-finalized rounded_panel()'s inline title text --
    e.g. Control's STEP/CHANNEL panel relabeling itself when VFO/Memory
    mode toggles."""
    label = canvas._rp.get("title_label")  # noqa: SLF001
    if label is not None:
        label.configure(text=text)


def _paint_glow_dot(c: tk.Canvas, size: int, color: str) -> None:
    cx = cy = size / 2
    c.create_oval(1, 1, size - 1, size - 1, fill=color, outline="", stipple="gray50")
    r = size * 0.32
    c.create_oval(cx - r, cy - r, cx + r, cy + r, fill=color, outline="")
    hr = r * 0.4
    c.create_oval(cx - r * 0.3 - hr, cy - r * 0.3 - hr, cx - r * 0.3 + hr, cy - r * 0.3 + hr,
                   fill="#ffffff", outline="", stipple="gray25")


def glow_dot(parent, size: int, color: str, bg: str = BG) -> tk.Canvas:
    """A small glowing LED dot -- concentric circles faking a soft glow
    (Tkinter has no real blur/glow filter)."""
    c = tk.Canvas(parent, width=size, height=size, bg=bg, highlightthickness=0, bd=0)
    _paint_glow_dot(c, size, color)
    return c


def recolor_glow_dot(canvas: tk.Canvas, color: str) -> None:
    """Repaints an existing glow_dot() canvas in place with a new color --
    cheaper than destroying/recreating it on every periodic refresh tick,
    and keeps its position stable in its parent."""
    canvas.delete("all")
    _paint_glow_dot(canvas, int(canvas["width"]), color)
