"""Several files at once: the list of files, where each one's session goes, and what's
happening to each. No Tk here: the app's side is batchview.BatchMixin.

Files are transcribed one after another. Each one that finishes is saved straight away as
its own session (.diemut) in the folder chosen, so a batch never holds more than the file
being transcribed and the one on screen. A batch of saved sessions opened together uses the
same list, with every file already done.
"""

import os
from pathlib import Path

QUEUED, RUNNING, DONE, ERROR, CANCELLED = "queued", "running", "done", "error", "cancelled"


def session_path(folder, source, taken=()):
    """Where the session for source goes in folder: its name with .diemut, plus " (2)",
    " (3)"… if that file exists already or is taken by another file in the batch."""
    from .sessions import SUFFIX        # (sessions needs numpy; this module doesn't)
    stem = Path(source).stem
    n = 1
    while True:
        path = os.path.join(folder, f"{stem}{'' if n == 1 else f' ({n})'}{SUFFIX}")
        if not os.path.exists(path) and path not in taken:
            return path
        n += 1


class Item:
    """One file in a batch."""

    def __init__(self, source, out, status=QUEUED):
        self.source = source        # the audio or video file (None for an opened session)
        self.out = out              # its session file
        self.status = status
        self.stage = None           # "transcribing" or "speakers", while running
        self.progress = 0.0         # percent of the stage
        self.error = None           # why it failed, for the person
        self.note = None            # the worker's last word, e.g. speaker labels didn't work
        self.cancel = False         # Cancel this file was chosen while it ran
        self.stop = None            # its worker's stop event, while it runs

    @property
    def name(self):
        return os.path.basename(self.source or self.out)

    def menu_status(self):
        """The few words the file list shows after the name."""
        if self.status == RUNNING:
            return {"transcribing": f"{self.progress:.0f}%",
                    "speakers": f"speakers {self.progress:.0f}%",
                    "saving": "saving"}.get(self.stage, "starting")
        return self.status


class Batch:
    """The files, in order, and which one is on screen."""

    def __init__(self, items, folder=None):
        self.items = items
        self.folder = folder
        self.shown = 0              # the file on screen
        self.sid = None             # the session id the running file's worker reports with

    @classmethod
    def from_files(cls, paths, folder):
        items, taken = [], set()
        for p in paths:
            out = session_path(folder, p, taken)
            taken.add(out)
            items.append(Item(p, out))
        return cls(items, folder)

    @classmethod
    def from_sessions(cls, paths):
        return cls([Item(None, p, DONE) for p in paths])

    def running(self):
        """The file being transcribed, or None."""
        return next((it for it in self.items if it.status == RUNNING), None)

    def next_queued(self):
        return next((it for it in self.items if it.status == QUEUED), None)

    def active(self):
        """True while a file is being transcribed or waiting to be."""
        return any(it.status in (RUNNING, QUEUED) for it in self.items)

    def left(self):
        """How many files are being transcribed or waiting."""
        return sum(it.status in (RUNNING, QUEUED) for it in self.items)

    def cancel_all(self):
        """Cancel every queued file, and the running one; returns the running one."""
        for it in self.items:
            if it.status == QUEUED:
                it.status = CANCELLED
        running = self.running()
        if running is not None:
            running.cancel = True
        return running

    def summary(self):
        """The line beside the file list: which file is shown, and while files are left, how
        far the whole batch has got. Every file counts the same; the one running counts by
        its transcription (finding speakers and saving count as nearly done)."""
        n = len(self.items)
        text = f"{self.shown + 1} of {n}"
        if self.active():
            done = 0.0
            for it in self.items:
                if it.status == RUNNING:
                    done += it.progress / 100 if it.stage in (None, "transcribing") else 0.95
                elif it.status != QUEUED:
                    done += 1
            text += f"  •  {done / n * 100:.0f}%"
        return text

    def queue_place(self, item):
        """1 for the next file to be transcribed, 2 for the one after…"""
        queued = [it for it in self.items if it.status == QUEUED]
        return queued.index(item) + 1
