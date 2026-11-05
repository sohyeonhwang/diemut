"""Undo and redo for changes to the transcript: corrections, paragraph splits and merges,
speaker changes, renames and merges, and finding speakers again.

Each step keeps a copy of the whole transcript from before the change: pieces as tuples
(small, and they share their text and numbers with the pieces themselves), plus the speaker
names. That's simple and always right, whatever the change was; LIMIT keeps it from growing
without end on long transcripts. Typing in the correction box has Tk's own undo.
"""

LIMIT = 30
_KEYS = ("start", "end", "text", "speaker", "par")


def snapshot(pieces, names):
    return [tuple(p.get(k) for k in _KEYS) for p in pieces], dict(names)


def restore(snap):
    """(pieces, names) from a snapshot: new piece dicts, as they were."""
    rows, names = snap
    pieces = []
    for row in rows:
        piece = dict(zip(_KEYS[:4], row[:4]))
        if row[4] is not None:
            piece["par"] = row[4]
        pieces.append(piece)
    return pieces, dict(names)


class History:
    def __init__(self):
        self.clear()

    def clear(self):
        self._undo, self._redo = [], []     # [(label, snapshot)], newest last

    def push(self, label, snap):
        """Remember snap, the transcript from before a change described by label."""
        self._undo.append((label, snap))
        del self._undo[:-LIMIT]
        self._redo.clear()

    def undo_label(self):
        return self._undo[-1][0] if self._undo else None

    def redo_label(self):
        return self._redo[-1][0] if self._redo else None

    def undo(self, current):
        """(label, snapshot to go back to); current is the transcript as it is now."""
        label, snap = self._undo.pop()
        self._redo.append((label, current))
        return label, snap

    def redo(self, current):
        label, snap = self._redo.pop()
        self._undo.append((label, current))
        return label, snap
