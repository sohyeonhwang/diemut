"""Find and replace in the transcript's text: no Tk here (the window is dialogs.FindWindow).

Matches are found in each paragraph's text on its own, so one never runs across two
paragraphs. A replacement goes in exactly as typed: with a regular expression, \\1 and the
like are not filled in.
"""

import re


def compile_query(query, match_case=False, regex=False):
    """The compiled pattern for query, or None if it's empty. Raises re.error if regex is
    on and the pattern isn't valid."""
    if not query:
        return None
    return re.compile(query if regex else re.escape(query), 0 if match_case else re.IGNORECASE)


def matches(texts, pattern):
    """[(paragraph, start, end)] for every match in texts (one string per paragraph).
    Empty matches (from patterns like "a*") are skipped."""
    if pattern is None:
        return []
    return [(k, m.start(), m.end()) for k, text in enumerate(texts)
            for m in pattern.finditer(text) if m.end() > m.start()]


def replace_spans(text, spans, new):
    """text with each (start, end) in spans replaced by new, spaces tidied as in the edit box."""
    for s, e in sorted(spans, reverse=True):
        text = text[:s] + new + text[e:]
    return " ".join(text.split())
