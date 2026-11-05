"""Settings diemut remembers between launches.

They're kept in settings.json in diemut's own folder: ~/.cache/diemut, or $DIEMUT_HOME (the
speaker models live there too). A missing or damaged file just means the defaults.
"""

import json
import os
from pathlib import Path

HOME = Path(os.environ.get("DIEMUT_HOME", Path.home() / ".cache" / "diemut"))
PATH = HOME / "settings.json"

DEFAULTS = {
    "hover_hints": True,          # tooltips over buttons and boxes
    "microphone": None,           # live mode's microphone, by name; None: the system default
    "leave_out_fillers": False,   # whether Export starts with "Leave out fillers…" ticked
    "panel_height": None,         # the correction panel's height (at 100% scaling); None: default
}
_TYPES = {"hover_hints": bool, "microphone": str, "leave_out_fillers": bool, "panel_height": int}


def _valid(key, value):
    """The setting's type; None too for the ones whose default is None."""
    if value is None:
        return DEFAULTS[key] is None
    return isinstance(value, _TYPES[key]) and not (_TYPES[key] is int and isinstance(value, bool))


def load(path=PATH):
    """The saved settings, with the default for anything missing, unknown or damaged."""
    values = dict(DEFAULTS)
    try:
        saved = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return values
    if isinstance(saved, dict):
        values.update({k: v for k, v in saved.items() if k in DEFAULTS and _valid(k, v)})
    return values


def save(values, path=PATH):
    """Write the settings. Raises OSError if the folder can't be written to."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps({k: values[k] for k in DEFAULTS}, indent=2), encoding="utf-8")
    os.replace(tmp, path)       # all at once, so a crash never leaves half a file
