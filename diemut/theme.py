"""diemut's look: colours, fonts, ttk styles, icons and its own mouse pointers."""

import base64
import sys
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

from .images import faded_png, tinted_png

# Simple symbols for the two modes, the same on every platform. Newer emoji only draw
# reliably in Tk on Windows, so the lock is left out elsewhere.
WINDOWS = sys.platform == "win32"
MIC_ICON = "◉"
FILE_ICON = "▤"
SETTINGS_ICON = "⚙"     # an old symbol (not an emoji), so it renders everywhere; and the
                        # fallback if assets/icons/settings_*.png is missing
LOCK = "🔒 " if WINDOWS else ""

BG = "#ffffff"
SURFACE = "#f4f4f2"
HOVER = "#eef3fb"
BORDER = "#dcdbd5"
BORDER_STRONG = "#b9b8b1"
TEXT = "#1f1f1d"
TEXT2 = "#5f5e5a"
MUTED = "#8f8e88"
ACCENT = "#51b5d1"
DANGER = "#c90065"
# Icon ink under the mouse and while pressed: the accent, darkened so thin lines read
INK = {"hover": "#4191a7", "pressed": "#357688"}     # ACCENT × 0.8 and × 0.65
PLAYING = "#ffff99"   # behind the word being played: a faint #FFFF00
NOTE_BG = "#9dd459"   # behind a note ([laughs]), with TEXT on it

FONTS = {}


def display_scale(widget):
    """The display scaling: 1.0 at 100%, 1.5 at 150% (never below 1)."""
    return max(1.0, widget.winfo_fpixels("1i") / 96.0)


def mix(a, b, f):
    """The colour f of the way from a to b ("#rrggbb"; f from 0 to 1)."""
    ca, cb = ([int(c[i:i + 2], 16) for i in (1, 3, 5)] for c in (a, b))
    return "#%02x%02x%02x" % tuple(round(x + (y - x) * f) for x, y in zip(ca, cb))


def shade(color, factor):
    """color ("#rrggbb") darkened by factor (0.9 = 10% darker)."""
    r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    return "#%02x%02x%02x" % tuple(max(0, min(255, round(c * factor))) for c in (r, g, b))


# ------------------------------------------------------------ style --
def setup_style(root):
    """The fonts in FONTS and the ttk styles diemut uses."""
    family = tkfont.nametofont("TkDefaultFont").actual("family")
    if WINDOWS:
        mono = "Consolas"
    elif sys.platform == "darwin":
        mono = "Menlo"
    else:
        mono = tkfont.nametofont("TkFixedFont").actual("family")

    def font(size, weight="normal", fam=family, slant="roman"):
        return tkfont.Font(family=fam, size=size, weight=weight, slant=slant)

    FONTS.update(
        body=font(10), label=font(10), small=font(9), small_bold=font(9, "bold"),
        dropdown=font(9), title=font(20, "bold"), card_title=font(12, "bold"),
        icon=font(22), mono=font(9, fam=mono), tag=font(9, fam=mono),
        card_title_mono=font(12, "bold", fam=mono),
        text=font(11), text_italic=font(11, slant="italic"), speaker=font(11, "bold"), header=font(10, "bold"), symbol=font(12),
    )

    style = ttk.Style()
    style.theme_use("clam")
    style.configure(".", background=BG, foreground=TEXT, font=FONTS["body"])
    style.configure("TButton", background=SURFACE, foreground=TEXT, padding=(10, 3), width=0,
                    bordercolor=BORDER_STRONG, lightcolor=SURFACE, darkcolor=SURFACE,
                    focuscolor=SURFACE)
    style.map("TButton",
              background=[("disabled", BG), ("pressed", BORDER), ("active", HOVER)],
              foreground=[("disabled", MUTED)],
              bordercolor=[("disabled", BORDER)],
              lightcolor=[("active", HOVER)], darkcolor=[("active", HOVER)])
    # A button that stays pressed while it's on (Loop in the correction panel)
    # On (Loop): the accent fill, like an action button, so it's plain to see
    style.map("Toggle.TButton",
              background=[("disabled", BG), ("selected", "active", shade(ACCENT, 0.9)),
                          ("selected", ACCENT), ("pressed", BORDER), ("active", HOVER)],
              foreground=[("disabled", MUTED), ("selected", TEXT)],
              bordercolor=[("disabled", BORDER), ("selected", shade(ACCENT, 0.75))],
              lightcolor=[("selected", ACCENT), ("active", HOVER)],
              darkcolor=[("selected", ACCENT), ("active", HOVER)])
    # Buttons that are just an icon: no box; under the mouse the icon (or symbol) turns blue
    # (INK). Icon.TButton on white (Settings, whose ⚙ fallback is pink, and the Find window),
    # PanelIcon.TButton on the correction panel's grey. A toggle that's on (Loop) gets the
    # accent fill.
    for name, bg, fg in (("Icon.TButton", BG, DANGER), ("PanelIcon.TButton", SURFACE, TEXT)):
        style.configure(name, background=bg, foreground=fg, font=FONTS["symbol"],
                        padding=(4, 2), relief="flat", borderwidth=0, bordercolor=bg,
                        lightcolor=bg, darkcolor=bg, focuscolor=bg)
        fill = [("disabled", bg), ("selected", "active", shade(ACCENT, 0.9)),
                ("selected", ACCENT), ("pressed", bg), ("active", bg)]
        style.map(name, background=fill, bordercolor=fill, lightcolor=fill, darkcolor=fill,
                  foreground=[("disabled", MUTED), ("selected", TEXT),
                              ("pressed", INK["pressed"]), ("active", INK["hover"])])
    # Accent buttons, for actions (Start, Export…, Merge…): accent fill, darker on hover
    fill = [("disabled", BG), ("pressed", shade(ACCENT, 0.8)), ("active", shade(ACCENT, 0.9))]
    style.configure("Accent.TButton", background=ACCENT, foreground=TEXT, focuscolor=ACCENT,
                    bordercolor=shade(ACCENT, 0.75), lightcolor=ACCENT, darkcolor=ACCENT)
    style.map("Accent.TButton", background=fill, lightcolor=fill, darkcolor=fill,
              foreground=[("disabled", MUTED)], bordercolor=[("disabled", BORDER)])
    style.configure("TRadiobutton", background=BG, foreground=TEXT, focuscolor=BG,
                    indicatorbackground=BG, indicatorforeground=ACCENT)
    style.map("TRadiobutton", background=[("active", BG)],
              foreground=[("disabled", MUTED)])
    style.configure("TCheckbutton", background=BG, foreground=TEXT, focuscolor=BG,
                    indicatorbackground=BG, indicatorforeground=ACCENT)
    style.map("TCheckbutton", background=[("active", BG)], foreground=[("disabled", MUTED)])
    style.configure("Subtle.TCombobox", fieldbackground=BG, background=BG,
                    foreground=MUTED, bordercolor=BORDER, lightcolor=BG, darkcolor=BG,
                    arrowcolor=MUTED, padding=(6, 2))
    style.map("Subtle.TCombobox",
              fieldbackground=[("readonly", "focus", BG), ("readonly", BG)],
              foreground=[("readonly", "focus", MUTED), ("readonly", MUTED)],
              selectbackground=[("readonly", "focus", BG), ("readonly", BG)],
              selectforeground=[("readonly", "focus", MUTED), ("readonly", MUTED)],
              background=[("readonly", "focus", BG), ("readonly", BG), ("active", BG)],
              bordercolor=[("focus", BORDER_STRONG), ("hover", BORDER_STRONG)])
    style.configure("Horizontal.TProgressbar", background=ACCENT, troughcolor=SURFACE,
                    bordercolor=BORDER, lightcolor=ACCENT, darkcolor=ACCENT)
    style.configure("Vertical.TScrollbar", background=SURFACE, troughcolor=BG,
                    bordercolor=BG, arrowcolor=MUTED, lightcolor=SURFACE, darkcolor=SURFACE)
    # The list that opens under a dropdown
    root.option_add("*TCombobox*Listbox.background", BG)
    root.option_add("*TCombobox*Listbox.foreground", TEXT)
    root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
    root.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")
    root.option_add("*TCombobox*Listbox.font", FONTS["dropdown"])
    # Selected text: white on near-black, and just as strong when the box isn't focused
    for widget in ("Text", "Entry"):
        root.option_add(f"*{widget}.selectBackground", TEXT)
        root.option_add(f"*{widget}.selectForeground", BG)
        root.option_add(f"*{widget}.inactiveSelectBackground", TEXT)


_icons = {}             # (name, size, look) -> PhotoImage, kept so Tk doesn't lose them


def icon_size(scale):
    """The icon size drawn at a display scaling: each PNG is used at its own size (never
    stretched, so the ink lines stay sharp), the nearest one at or above the ideal size."""
    return 20 if scale < 1.2 else 24 if scale < 1.45 else 32 if scale < 1.95 else 48


def icon(root, name, scale, look=None):
    """assets/icons/<name>_<size>.png as a PhotoImage, or None if it's missing, so the caller
    keeps its text or symbol. look: None, "faded" (35% opacity, for disabled buttons), or
    "hover" / "pressed" (in blue ink)."""
    key = (name, icon_size(scale), look)
    if key not in _icons:
        try:
            from importlib.resources import files
            data = (files("diemut") / "assets" / "icons" / f"{name}_{key[1]}.png").read_bytes()
            if look == "faded":
                data = faded_png(data, 0.35)
            elif look in INK:
                data = tinted_png(data, INK[look])
            _icons[key] = tk.PhotoImage(master=root, format="png",
                                        data=base64.b64encode(data).decode("ascii"))
        except Exception:
            _icons[key] = None
    return _icons[key]


def icon_images(root, name, scale, toggle=False):
    """The image option for a ttk button showing icon name: faded when disabled, blue under
    the mouse. A toggle (Loop) keeps its own ink while on, over the accent fill. None if the
    icon is missing."""
    img = icon(root, name, scale)
    if img is None:
        return None
    spec = [img]
    for state, look in (("disabled", "faded"), ("selected", None), ("pressed", "pressed"),
                        ("active", "hover")):
        if state == "selected" and not toggle:
            continue
        other = icon(root, name, scale, look) if look else img
        if other is not None:
            spec += [state, other]
    return tuple(spec)


def file_cursor(root, name, fallback):
    """A pointer drawn for diemut (assets/<name>.cur on Windows, .xbm bitmaps on X11).

    Used for Merge mode (a needle and thread). Tk on macOS can't load pointer files, so it,
    and a missing file, get fallback.
    """
    try:
        from importlib.resources import files
        assets = files("diemut") / "assets"
        system = root.tk.call("tk", "windowingsystem")
        if system == "win32":
            spec = "{@%s}" % str(assets / f"{name}.cur").replace("\\", "/")
        elif system == "x11":
            spec = "{@%s} {%s} #2b1b10 #ffffff" % (assets / f"{name}.xbm",
                                                   assets / f"{name}_mask.xbm")
        else:
            return fallback
        root.configure(cursor=spec)    # check that Tk can load it
        root.configure(cursor="")
        return spec
    except Exception:
        return fallback
