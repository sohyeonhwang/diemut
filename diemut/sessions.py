"""Saved sessions (.diemut files), and the recovery copy kept while work is unsaved.

A .diemut file is a zip holding:
- session.json: the transcript (pieces), speaker names, where it came from, and the layout
  of the audio (live mode skips paused time, so the audio is a list of runs);
- audio.flac: the runs one after another, lossless, 16 kHz mono.

While a session has unsaved changes, App also keeps a recovery copy in RECOVERY_DIR, in a
folder of its own: the same session.json, with the audio as raw 16-bit samples that only
ever grow at the end (so a long live recording isn't rewritten every time). The app rewrites
session.json every 30 seconds while there are unsaved changes; a folder that hasn't been
written for a while was left by a diemut that closed without saving, and is offered back at
the next start.
"""

import io
import json
import os
import shutil
import time
import uuid
import zipfile
from pathlib import Path

import numpy as np

from . import settings

FORMAT = 1
SUFFIX = ".diemut"
RECOVERY_DIR = settings.HOME / "recovery"
STALE = 120            # seconds: a recovery folder untouched this long isn't in use
_JSON, _FLAC, _PCM = "session.json", "audio.flac", "audio.pcm"


def make_state(mode, source, started, model, language, detected_language, duration,
               speaker_count, names, pieces, engine=None):
    """Everything about a transcript that a session file keeps, except the audio. source: the
    file's name (or None for live); started: a datetime; engine: what made it, e.g.
    "OpenVINO · GPU" (older files don't have it)."""
    return {
        "mode": mode,
        "source": os.path.basename(source) if source else None,
        "started": started.isoformat(timespec="seconds"),
        "model": model,
        "language": language,
        "detected_language": detected_language,
        "duration": duration,
        "speaker_count": speaker_count,
        "names": {str(k): v for k, v in names.items()},
        "pieces": pieces,
        "engine": engine,
    }


class SessionError(Exception):
    """A session file that can't be opened; the message says why, for the person."""


# -------------------------------------------------------------------- audio --
def _runs(clip):
    """[(start seconds, [int16 arrays])] for the clip. Safe while a worker is still adding:
    it's a snapshot, and runs only ever grow at the end."""
    if clip is None:
        return []
    return [(part[0], list(part[1])) for part in list(clip.parts)]


def _layout(runs):
    return [[start, sum(len(a) for a in arrays)] for start, arrays in runs]


def _clip(layout, samples):
    from .transcribe import AudioClip
    clip, at = AudioClip(), 0
    for start, n in layout:
        clip.parts.append([float(start), [samples[at:at + n]], int(n), None])
        at += n
    return clip


def _encode_flac(runs):
    import av

    from .transcribe import SAMPLE_RATE
    buf, pts = io.BytesIO(), 0
    with av.open(buf, "w", format="flac") as out:
        stream = out.add_stream("flac", rate=SAMPLE_RATE, layout="mono")
        stream.format = "s16"
        for _, arrays in runs:
            for data in arrays:
                frame = av.AudioFrame.from_ndarray(data.reshape(1, -1), format="s16",
                                                   layout="mono")
                frame.rate, frame.pts = SAMPLE_RATE, pts
                pts += len(data)
                for packet in stream.encode(frame):
                    out.mux(packet)
        for packet in stream.encode(None):
            out.mux(packet)
    return buf.getvalue()


def _decode_flac(data):
    import av
    with av.open(io.BytesIO(data)) as inp:
        parts = [frame.to_ndarray().reshape(-1) for frame in inp.decode(audio=0)]
    return np.concatenate(parts).astype(np.int16) if parts else np.zeros(0, np.int16)


# ------------------------------------------------------------- the session --
def _json(state, runs):
    """session.json's text. Speaker ids can be numpy integers, so those become ints."""
    return json.dumps(dict(state, format=FORMAT, audio=_layout(runs)), ensure_ascii=False,
                      default=lambda o: o.item() if hasattr(o, "item") else str(o))


def _check(state, samples):
    """state as read from session.json, checked; returns (state, clip)."""
    if not isinstance(state, dict) or not isinstance(state.get("pieces"), list):
        raise SessionError("This file isn't a diemut session, or it's damaged.")
    if state.get("format", 0) > FORMAT:
        raise SessionError("This session was saved by a newer version of diemut. Update "
                           "diemut to open it.")
    layout = state.pop("audio", None) or []
    if samples is None or sum(n for _, n in layout) > len(samples):
        layout, clip = [], None      # the transcript without its audio
    else:
        clip = _clip(layout, samples)
    state["names"] = {int(k): v for k, v in state.get("names", {}).items()}
    return state, clip


def save(path, state, clip):
    """Write a .diemut file. state: the session as a dict that json can write (App makes
    it); clip: the AudioClip, or None. Written to a temporary file first, so a failed save
    never damages an earlier one."""
    runs = _runs(clip)
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    try:
        with zipfile.ZipFile(tmp, "w") as z:
            z.writestr(_JSON, _json(state, runs), zipfile.ZIP_DEFLATED)
            if runs:
                z.writestr(_FLAC, _encode_flac(runs), zipfile.ZIP_STORED)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def load(path):
    """(state, clip) from a .diemut file. Raises SessionError with a message to show."""
    try:
        with zipfile.ZipFile(path) as z:
            state = json.loads(z.read(_JSON).decode("utf-8"))
            samples = _decode_flac(z.read(_FLAC)) if _FLAC in z.namelist() else None
    except SessionError:
        raise
    except (OSError, KeyError, ValueError, zipfile.BadZipFile) as exc:
        raise SessionError("This file isn't a diemut session, or it's damaged.") from exc
    except Exception as exc:        # the audio couldn't be decoded
        raise SessionError("The audio in this session is damaged.") from exc
    return _check(state, samples)


# ---------------------------------------------------------------- recovery --
class Recovery:
    """The recovery copy of the open session, in a folder of its own under RECOVERY_DIR."""

    def __init__(self, root=None):
        self.folder = Path(root or RECOVERY_DIR) / f"{time.strftime('%Y%m%d-%H%M%S')}-" \
                                                   f"{uuid.uuid4().hex[:6]}"
        self._written = 0       # samples already in audio.pcm

    def write(self, state, clip):
        """Bring the copy up to date: new audio first, then session.json (which says how
        much of the audio belongs to it)."""
        self.folder.mkdir(parents=True, exist_ok=True)
        runs = _runs(clip)
        with open(self.folder / _PCM, "ab") as f:
            seen = 0
            for _, arrays in runs:
                for data in arrays:
                    if seen + len(data) > self._written:
                        f.write(data[max(0, self._written - seen):].tobytes())
                    seen += len(data)
            self._written = max(self._written, seen)
        tmp = self.folder / (_JSON + ".tmp")
        tmp.write_text(_json(state, runs), encoding="utf-8")
        os.replace(tmp, self.folder / _JSON)

    def delete(self):
        shutil.rmtree(self.folder, ignore_errors=True)


def left_behind(root=None):
    """Recovery folders no running diemut is using, newest first: [(folder, state)]."""
    found = []
    root = Path(root or RECOVERY_DIR)
    for folder in root.glob("*") if root.is_dir() else ():
        try:
            if time.time() - folder.stat().st_mtime < STALE or \
                    time.time() - (folder / _JSON).stat().st_mtime < STALE:
                continue        # in use
            state = json.loads((folder / _JSON).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            if time.time() - folder.stat().st_mtime >= STALE:
                shutil.rmtree(folder, ignore_errors=True)   # an old one with nothing in it
            continue
        found.append((folder, state))
    return sorted(found, key=lambda f: f[0].name, reverse=True)


def load_recovery(folder):
    """(state, clip) from a recovery folder."""
    folder = Path(folder)
    try:
        state = json.loads((folder / _JSON).read_text(encoding="utf-8"))
        pcm = folder / _PCM
        samples = np.fromfile(pcm, dtype=np.int16) if pcm.exists() else None
    except (OSError, ValueError) as exc:
        raise SessionError("The recovered session is damaged.") from exc
    return _check(state, samples)
