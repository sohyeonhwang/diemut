"""Filler words (um, uh…) and false starts ("it was- it was"): finding them, and leaving them
out of an export.

Only non-word fillers count: words like "like" or "you know" are often meant, so they stay.
A false start is the first try of something said again straight after:
- one word, said again after a dash or a filler ("It's- it's", "I, um, I");
- or two or three words, said again after a comma, a dash or a filler ("it was- it was",
  "I was, uh, I was").
A single word repeated after just a comma ("very, very good") is left alone: that's usually
emphasis. Whisper writes cut-off words with a dash when it's given the verbatim prompt
(transcribe.VERBATIM_PROMPT), usually as a separate "-" piece.

Nothing is stored on the pieces: like paragraphs, this is worked out when it's needed.
"""

import re

from . import speakers
from .notes import is_note

_FILLER = re.compile(r"u+[mh]+|e+r+m*|a+h+|h+m+|m{2,}|m+h+m+")
_NOT_LETTER = re.compile(r"[^a-z]")
_NOT_WORD = re.compile(r"[^\w']")
_DASHES = ("-", "–", "—")
_SENTENCE_ENDS = (".", "?", "!")


def _letters(text):
    return _NOT_LETTER.sub("", text.lower())


def _norm(text):
    return _NOT_WORD.sub("", text.lower())


def is_filler(text):
    """True for a non-word filler: um, uh, er, erm, ah, hmm, mm, mhm (with any punctuation).
    A note ([um]) isn't one: it's written in, not said."""
    if is_note(text):
        return False
    word = _letters(text)
    return bool(word) and _FILLER.fullmatch(word) is not None


def _is_dash(text):
    return text.strip() in _DASHES


def _false_starts(pieces):
    """Indices of the pieces that are false starts, with any dash or filler after them."""
    words, said = [], []        # the real words, and each one as compared (once per word)
    for i, p in enumerate(pieces):
        norm = _norm(p["text"])
        if norm and not is_filler(p["text"]) and not _is_dash(p["text"]) \
                and not is_note(p["text"]):
            words.append(i)
            said.append(norm)
    out = set()
    for pos in range(1, len(words)):
        for n in (3, 2, 1):
            if pos - n < 0 or pos + n > len(words):
                continue
            if said[pos - n:pos] != said[pos:pos + n]:
                continue
            first, again = words[pos - n:pos], words[pos:pos + n]
            if len({pieces[i].get("speaker") for i in first + again}) > 1:
                continue
            between = [pieces[i]["text"] for i in range(first[-1] + 1, again[0])]
            cut = pieces[first[-1]]["text"].rstrip().endswith(_DASHES) or \
                any(_is_dash(t) or is_filler(t) for t in between)
            comma = pieces[first[-1]]["text"].rstrip().endswith(",")
            if cut or (comma and n > 1):
                out.update(range(first[0], again[0]))
                break
    return out


def removable(pieces):
    """Indices of the pieces "Leave out fillers and false starts" takes out. (A false start
    includes the dash or filler between it and the retry.)"""
    return {i for i, p in enumerate(pieces) if is_filler(p["text"])} | _false_starts(pieces)


def leave_out(pieces, use_speakers):
    """A copy of pieces without fillers and false starts, tidied so the text still reads:
    a sentence's full stop moves to the word before, and if a removed word started a
    sentence, the next word kept gets a capital.

    The paragraphs stay as they are in the transcript (use_speakers as for group_blocks):
    every word kept is marked to start or join a paragraph, because the gap a filler leaves
    would otherwise look like a pause and start a new one.
    """
    drop = removable(pieces)
    starts = {par["i0"] for block in speakers.group_blocks(pieces, use_speakers)
              for par in block["paragraphs"]}
    out, carry_par, capital = [], False, False
    for i, p in enumerate(pieces):
        text = p["text"].strip()
        if i in drop:
            prev = out[-1] if out and out[-1].get("speaker") == p.get("speaker") else None
            if text.endswith(_SENTENCE_ENDS) and prev is not None and \
                    not prev["text"].rstrip().endswith(_SENTENCE_ENDS):
                prev["text"] = prev["text"].rstrip().rstrip(",;:") + text[-1]
            if prev is None or prev["text"].rstrip().endswith(_SENTENCE_ENDS):
                capital = True          # the next word kept starts the sentence now
            carry_par = carry_par or i in starts
            continue
        q = dict(p, par="start" if carry_par or i in starts else "join")
        if capital:
            body = q["text"].lstrip()
            q["text"] = q["text"][:len(q["text"]) - len(body)] + body[:1].upper() + body[1:]
        carry_par, capital = False, False
        out.append(q)
    return out
