"""diemut's look: colours, fonts, ttk styles and its own mouse pointers."""

import sys
import tkinter.font as tkfont
from tkinter import ttk

# Simple symbols for the two modes, the same on every platform. Newer emoji only draw
# reliably in Tk on Windows, so the lock is left out elsewhere.
WINDOWS = sys.platform == "win32"
MIC_ICON = "◉"
FILE_ICON = "▤"
SETTINGS_ICON = "⚙"     # an old symbol (not an emoji), so it renders everywhere
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
PLAYING = "#ffff99"   # behind the word being played: a faint #FFFF00

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

    def font(size, weight="normal", fam=family):
        return tkfont.Font(family=fam, size=size, weight=weight)

    FONTS.update(
        body=font(10), label=font(10), small=font(9), small_bold=font(9, "bold"),
        dropdown=font(9), title=font(20, "bold"), card_title=font(12, "bold"),
        icon=font(22), mono=font(9, fam=mono), tag=font(9, fam=mono),
        card_title_mono=font(12, "bold", fam=mono),
        text=font(11), speaker=font(11, "bold"), header=font(10, "bold"), symbol=font(12),
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
    style.map("Toggle.TButton",
              background=[("disabled", BG), ("pressed", BORDER), ("selected", BORDER),
                          ("active", HOVER)],
              foreground=[("disabled", MUTED), ("selected", TEXT)],
              bordercolor=[("disabled", BORDER), ("selected", ACCENT)],
              lightcolor=[("selected", BORDER), ("active", HOVER)],
              darkcolor=[("selected", BORDER), ("active", HOVER)])
    # A button that is just a symbol, in pink so it stands out (⚙ Settings)
    style.configure("Icon.TButton", foreground=DANGER, font=FONTS["symbol"], padding=(6, 0))
    style.map("Icon.TButton", foreground=[("disabled", MUTED)])
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


def file_cursor(root, name, fallback):
    """A pointer drawn for diemut (assets/<name>.cur on Windows, .xbm bitmaps on X11).

    Used for Merge mode (a needle and thread); other systems, or a missing file, get
    fallback.
    """
    try:
        from importlib.resources import files
        assets = files("diemut") / "assets"
        system = root.tk.call("tk", "windowingsystem")
        if system == "win32":
            spec = "{@%s}" % str(assets / f"{name}.cur").replace("\\", "/")
        elif system == "x11":
            spec = "{@%s} {%s} %s white" % (assets / f"{name}.xbm",
                                            assets / f"{name}_mask.xbm", TEXT)
        else:
            return fallback
        root.configure(cursor=spec)    # check that Tk can load it
        root.configure(cursor="")
        return spec
    except Exception:
        return fallback
