"""Notes: things written into the transcript that nobody said, in square brackets, like
[laughs], [long pause] or [she means the 2019 study].

A note is a piece of its own whose text is the bracketed note, taking no time: it sits at the
end of the word before it (or the start of the word after, at the start of a paragraph). So
nothing is stored besides the text, and the words around a note keep their times. Typing
[…] in the correction box makes one (timing.retime_spans); the transcript shows notes with
a lime highlight.
"""

import re

NOTE = re.compile(r"\[[^\[\]\n]*\]")


def is_note(text):
    """True if a piece's text is a note."""
    return NOTE.fullmatch(text.strip()) is not None


def leave_out(pieces):
    """A copy of pieces without notes, for exports. A note's paragraph mark goes to the piece
    after it, if that has none, so the paragraphs stay as they were."""
    out, carry = [], None
    for p in pieces:
        if is_note(p["text"]):
            carry = carry or p.get("par")
            continue
        if carry and "par" not in p:
            p = dict(p, par=carry)
        carry = None
        out.append(p)
    return out


def runs(text):
    """text split into (part, is note text) runs, the brackets kept with the text around
    them: "a [laughs] b" → ("a [", False), ("laughs", True), ("] b", False)."""
    out, pos = [], 0
    for m in NOTE.finditer(text):
        out += [(text[pos:m.start() + 1], False), (m.group()[1:-1], True)]
        pos = m.end() - 1
    out.append((text[pos:], False))
    return [(part, note) for part, note in out if part]
