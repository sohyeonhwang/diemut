"""Save transcripts as .txt, .vtt, .srt or .docx, and live audio as .mp3."""

from . import notes
from .speakers import SENTENCE_ENDS, group_blocks


def fmt_time(seconds, with_ms=False):
    total_ms = int(round(max(0.0, seconds) * 1000))
    h, rem = divmod(total_ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}" if with_ms else f"{h:02d}:{m:02d}:{s:02d}"


# Timestamps in .txt and .docx: none, one per speaker turn, or one per paragraph.
# Without speaker labels, each paragraph counts as a turn.
TIMESTAMPS = ("turns", "paragraphs", "none")


def speaker_name(names, speaker):
    """A speaker's name: the one given, or Speaker 1, Speaker 2…"""
    return names.get(speaker, f"Speaker {speaker + 1}")


def export_txt(path, pieces, names, use_speakers, timestamps):
    """timestamps: one of TIMESTAMPS."""
    lines = []
    for block in group_blocks(pieces, use_speakers):
        if block["speaker"] is not None:
            header = speaker_name(names, block["speaker"])
            if timestamps == "turns":
                header += f"  [{fmt_time(block['start'])}]"
            lines += [header]
            for par in block["paragraphs"]:
                prefix = f"[{fmt_time(par['start'])}] " if timestamps == "paragraphs" else ""
                lines += [prefix + par["text"], ""]
        else:
            for par in block["paragraphs"]:
                prefix = f"[{fmt_time(par['start'])}] " if timestamps != "none" else ""
                lines += [prefix + par["text"], ""]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines).rstrip() + "\n")


def _make_cues(pieces, use_speakers):
    """Merge pieces into subtitle-sized cues (about one line, a few seconds)."""
    cues = []
    for p in pieces:
        spk = p.get("speaker") if use_speakers else None
        cur = cues[-1] if cues else None
        if (cur and cur["speaker"] == spk and p["start"] - cur["end"] <= 1.0
                and cur["end"] - cur["start"] < 6.0 and len(cur["text"]) < 80
                and not (cur["text"].rstrip().endswith(SENTENCE_ENDS)
                         and cur["end"] - cur["start"] > 2.0)):
            cur["text"] += p["text"]
            cur["end"] = p["end"]
        else:
            cues.append({"start": p["start"], "end": p["end"], "text": p["text"], "speaker": spk})
    # Fold one- or two-word leftovers back into the cue before them.
    merged = []
    for cue in cues:
        prev = merged[-1] if merged else None
        if (prev and prev["speaker"] == cue["speaker"] and len(cue["text"].split()) <= 2
                and cue["start"] - prev["end"] <= 1.0 and cue["end"] - prev["start"] < 9.0):
            prev["text"] += cue["text"]
            prev["end"] = cue["end"]
        else:
            merged.append(cue)
    return merged


def _subtitles(pieces, names, use_speakers):
    """(start, end, text, speaker name or None) for each subtitle."""
    for cue in _make_cues(pieces, use_speakers):
        text = " ".join(cue["text"].split()) if " " in cue["text"] else cue["text"].strip()
        name = speaker_name(names, cue["speaker"]) if cue["speaker"] is not None else None
        yield cue["start"], max(cue["end"], cue["start"] + 0.01), text, name


def export_vtt(path, pieces, names, use_speakers):
    out = ["WEBVTT", ""]
    for i, (start, end, text, name) in enumerate(_subtitles(pieces, names, use_speakers), 1):
        text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        if name:
            text = f"<v {name}>{text}"
        out += [str(i), f"{fmt_time(start, True)} --> {fmt_time(end, True)}", text, ""]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out))


def export_srt(path, pieces, names, use_speakers):
    """SubRip subtitles. They have no voice tags, so a speaker is a "Name: " prefix."""
    out = []
    for i, (start, end, text, name) in enumerate(_subtitles(pieces, names, use_speakers), 1):
        times = f"{fmt_time(start, True)} --> {fmt_time(end, True)}".replace(".", ",")
        out += [str(i), times, f"{name}: {text}" if name else text, ""]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out))


def export_mp3(path, clip):
    """Save a live session's audio as a mono MP3 (64 kbit/s, about 28 MB an hour).

    Paused stretches are silence, so times in the file match the transcript's.
    Uses the FFmpeg that PyAV (installed with faster-whisper) bundles.
    """
    import av
    import numpy as np

    from .transcribe import SAMPLE_RATE
    with av.open(path, "w", format="mp3") as out:
        stream = out.add_stream("libmp3lame", rate=SAMPLE_RATE)
        stream.bit_rate = 64000
        stream.layout = "mono"
        t, end, pts = 0.0, clip.end, 0
        while t < end:   # a minute at a time, to keep memory low on long sessions
            chunk = np.clip(clip.slice(t, min(end, t + 60.0)), -1.0, 1.0).astype(np.float32)
            frame = av.AudioFrame.from_ndarray(chunk.reshape(1, -1), format="fltp", layout="mono")
            frame.rate, frame.pts = SAMPLE_RATE, pts
            pts += len(chunk)
            for packet in stream.encode(frame):
                out.mux(packet)
            t += 60.0
        for packet in stream.encode(None):
            out.mux(packet)


def export_docx(path, pieces, names, use_speakers, timestamps, title, meta_lines):
    """timestamps: one of TIMESTAMPS."""
    import docx
    from docx.shared import Pt, RGBColor

    grey = RGBColor(0x76, 0x75, 0x70)
    document = docx.Document()
    document.add_heading(title, level=1)
    meta = document.add_paragraph()
    for i, line in enumerate(meta_lines):
        run = meta.add_run(line)
        run.font.size = Pt(9)
        run.font.color.rgb = grey
        if i < len(meta_lines) - 1:
            run.add_break()

    for block in group_blocks(pieces, use_speakers):
        if block["speaker"] is not None:
            head = document.add_paragraph()
            head.paragraph_format.space_before = Pt(10)
            head.paragraph_format.keep_with_next = True
            head.add_run(speaker_name(names, block["speaker"])).bold = True
            if timestamps == "turns":
                ts = head.add_run(f"   {fmt_time(block['start'])}")
                ts.font.size = Pt(9)
                ts.font.color.rgb = grey
            for par in block["paragraphs"]:
                p = document.add_paragraph()
                if timestamps == "paragraphs":
                    ts = p.add_run(f"{fmt_time(par['start'])}   ")
                    ts.font.size = Pt(9)
                    ts.font.color.rgb = grey
                _add_text(p, par["text"])
        else:
            for par in block["paragraphs"]:
                p = document.add_paragraph()
                if timestamps != "none":
                    ts = p.add_run(f"{fmt_time(par['start'])}   ")
                    ts.font.size = Pt(9)
                    ts.font.color.rgb = grey
                _add_text(p, par["text"])
    document.save(path)


def _add_text(paragraph, text):
    """A paragraph's text in .docx, with the words of each note ([…]) in italics."""
    for part, is_note in notes.runs(text):
        paragraph.add_run(part).italic = is_note or None
