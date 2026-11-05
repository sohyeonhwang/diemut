"""Word timings for corrected text.

When someone corrects a paragraph, the words they kept should keep the times
Whisper gave them. This module lines the edited text up against the original
pieces (difflib), keeps the times of matching words, and spreads new words
evenly across the gap between the kept words around them.

Text without spaces (Chinese, Japanese…) is aligned character by character.
"""

import difflib
import re

_NON_WORD = re.compile(r"[^\w]+")
_TOKEN = re.compile(r"\S+")


def _norm(token):
    """Compare words without case or punctuation ("Hello," matches "hello")."""
    return _NON_WORD.sub("", token.lower()) or token


def is_spaceless(pieces, text):
    """True when neither the original pieces nor the edited text use spaces."""
    original = "".join(p["text"] for p in pieces).strip()
    return not any(c.isspace() for c in original) and not any(c.isspace() for c in text.strip())


def tokenize(text, spaceless):
    """Words in text as (word, char_start, char_end); characters when spaceless."""
    if spaceless:
        return [(c, i, i + 1) for i, c in enumerate(text) if not c.isspace()]
    return [(m.group(), m.start(), m.end()) for m in _TOKEN.finditer(text)]


def original_words(pieces, spaceless):
    """The pieces' words as (word, start, end, piece index).

    A piece can hold several words (live mode makes one piece per segment);
    its time is then shared out in proportion to each word's length.
    """
    words = []
    for k, p in enumerate(pieces):
        parts = [t for t, _, _ in tokenize(p["text"], spaceless)]
        if not parts:
            continue
        span = p["end"] - p["start"]
        weights = [len(t) + (0 if spaceless else 1) for t in parts]
        total, acc = sum(weights), 0
        for t, w in zip(parts, weights):
            a = p["start"] + span * acc / total
            acc += w
            words.append((t, a, p["start"] + span * acc / total, k))
    return words


def align(old, new_words, start, end):
    """Times for new_words, given the original words.

    old: [(word, start, end, …)]. new_words: [word]. start/end: the paragraph's span.
    Returns [(start, end, kept)] for each new word, where kept is the index into old
    of a word that matched, or None for a new or changed word.
    """
    matcher = difflib.SequenceMatcher(None, [_norm(w[0]) for w in old],
                                      [_norm(w) for w in new_words], autojunk=False)
    times = [None] * len(new_words)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for i, j in zip(range(i1, i2), range(j1, j2)):
                times[j] = (old[i][1], old[i][2], i)
        elif tag in ("replace", "insert"):
            # Spread evenly between the kept words on either side.
            gap_a = old[i1 - 1][2] if i1 > 0 else start
            gap_b = old[i2][1] if i2 < len(old) else end
            gap_b = max(gap_a, gap_b)
            step = (gap_b - gap_a) / (j2 - j1)
            for n, j in enumerate(range(j1, j2)):
                times[j] = (gap_a + n * step, gap_a + (n + 1) * step, None)
    return times


def timed_spans(pieces, text):
    """Map each word of text (as typed in the edit box) to a time.

    Returns [(char_start, char_end, start, end)], in text order.
    """
    if not pieces:
        return []
    spaceless = is_spaceless(pieces, text)
    old = original_words(pieces, spaceless)
    tokens = tokenize(text, spaceless)
    if not old or not tokens:
        return []
    times = align(old, [t for t, _, _ in tokens], pieces[0]["start"], pieces[-1]["end"])
    return [(a, b, s, e) for (_, a, b), (s, e, _) in zip(tokens, times)]


def retime_spans(pieces, text):
    """New pieces for a corrected paragraph, keeping the times of unchanged words.

    pieces: the paragraph's original pieces. text: the corrected text, where a
    line break starts a new paragraph. Every new piece takes the first original
    piece's speaker, and the first one keeps the paragraph's manual "par" mark.

    Returns [(piece, char_start, char_end)]: where each piece's text is in text.
    """
    if not pieces:
        return []
    speaker = pieces[0].get("speaker")
    spaceless = is_spaceless(pieces, text)
    old = original_words(pieces, spaceless)
    tokens = tokenize(text, spaceless)
    if not tokens:
        return []
    times = align(old, [t for t, _, _ in tokens], pieces[0]["start"], pieces[-1]["end"])
    first_word = {k: i for i, (_, _, _, k) in reversed(list(enumerate(old)))}

    out, prev_end, last = [], 0, None
    for (tok, a, b), (start, end, kept) in zip(tokens, times):
        if not out:
            mark = pieces[0].get("par")
        elif "\n" in text[prev_end:a]:
            mark = "start"
        elif kept is not None and first_word.get(old[kept][3]) == kept:
            mark = pieces[old[kept][3]].get("par")   # e.g. a "join" from an earlier merge
        else:
            mark = None
        prev_end = b
        # Without spaces, join characters back up: kept characters from the same
        # original piece, and runs of new characters, each become one piece.
        key = ("kept", old[kept][3]) if kept is not None else ("new", None)
        if (spaceless and out and mark is None and key == last[0]
                and (key[0] == "new" or kept == last[1] + 1)):
            piece, a0, _ = out[-1]
            piece["text"] += tok
            piece["end"] = end
            out[-1] = (piece, a0, b)
        else:
            piece = {"start": start, "end": end, "text": tok if spaceless else " " + tok,
                     "speaker": speaker}
            if mark:
                piece["par"] = mark
            out.append((piece, a, b))
        last = (key, kept)
    return out


def retime(pieces, text):
    """retime_spans without the character positions: just the new pieces."""
    return [piece for piece, _, _ in retime_spans(pieces, text)]
