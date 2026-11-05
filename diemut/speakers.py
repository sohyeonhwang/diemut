"""Speaker labels (sherpa-onnx) and pause-based paragraph grouping.

A transcript is a list of "pieces": dicts with start, end, text and speaker.
Both file and live transcripts use one piece per word. A piece may also have
"par": "start" (always begin a paragraph here) or "join" (never begin one here),
set when someone splits or merges paragraphs by hand.
"""

import bisect
import multiprocessing
import os
import shutil
import tarfile
import tempfile
import time
import urllib.request
from pathlib import Path

# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #
PARAGRAPH_PAUSE = 1.5        # seconds of silence that start a new paragraph
PARAGRAPH_SOFT_MAX = 600     # characters; after this, break at the next sentence end
SENTENCE_ENDS = (".", "?", "!", "。", "？", "！")

# Speaker clustering threshold. Higher = fewer speakers, lower = more.
SPEAKER_THRESHOLD = 0.85

MODEL_DIR = Path(os.environ.get("DIEMUT_HOME", Path.home() / ".cache" / "diemut")) / "models"
_RELEASES = "https://github.com/k2-fsa/sherpa-onnx/releases/download"
SEGMENTATION_URL = f"{_RELEASES}/speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2"
SEGMENTATION_FILE = MODEL_DIR / "sherpa-onnx-pyannote-segmentation-3-0" / "model.onnx"
EMBEDDING_URL = f"{_RELEASES}/speaker-recongition-models/nemo_en_titanet_small.onnx"
EMBEDDING_FILE = MODEL_DIR / "nemo_en_titanet_small.onnx"

# --------------------------------------------------------------------------- #
# Model download
# --------------------------------------------------------------------------- #
def _download(url, dest, label, status):
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    with urllib.request.urlopen(url, timeout=60) as resp, open(tmp, "wb") as out:
        total = int(resp.headers.get("Content-Length") or 0)
        done, shown = 0, -1
        while True:
            chunk = resp.read(1 << 16)
            if not chunk:
                break
            out.write(chunk)
            done += len(chunk)
            if total and done // 1_000_000 != shown:
                shown = done // 1_000_000
                status(f"Downloading {label}… {shown} of {total // 1_000_000} MB")
    tmp.replace(dest)


def ensure_models(status):
    """Download the two small speaker models on first use (~47 MB in total)."""
    if not SEGMENTATION_FILE.exists():
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "segmentation.tar.bz2"
            _download(SEGMENTATION_URL, archive, "speaker segmentation model", status)
            with tarfile.open(archive) as tar:
                root = Path(tmp).resolve()
                for member in tar.getmembers():
                    target = (root / member.name).resolve()
                    if member.issym() or member.islnk() or root not in target.parents:
                        raise RuntimeError("Unexpected file in the speaker model download.")
                tar.extractall(tmp)
            src = Path(tmp) / "sherpa-onnx-pyannote-segmentation-3-0"
            shutil.rmtree(SEGMENTATION_FILE.parent, ignore_errors=True)
            shutil.move(str(src), str(SEGMENTATION_FILE.parent))
    if not EMBEDDING_FILE.exists():
        _download(EMBEDDING_URL, EMBEDDING_FILE, "speaker voice model", status)


# --------------------------------------------------------------------------- #
# Diarization
# --------------------------------------------------------------------------- #
def _get_diarizer(num_speakers=None):
    """num_speakers: how many people speak, if known; otherwise they're counted."""
    import sherpa_onnx
    threads = max(1, min(4, (os.cpu_count() or 2) // 2))
    config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
        segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
            pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(
                model=str(SEGMENTATION_FILE)),
            num_threads=threads),
        embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(
            model=str(EMBEDDING_FILE), num_threads=threads),
        clustering=sherpa_onnx.FastClusteringConfig(
            num_clusters=num_speakers or -1, threshold=SPEAKER_THRESHOLD),
    )
    if not config.validate():
        raise RuntimeError("The speaker models are missing or damaged. "
                           f"Delete {MODEL_DIR} and try again.")
    return sherpa_onnx.OfflineSpeakerDiarization(config)


def save_audio(audio):
    """Write audio to a temporary file for diarize(), which deletes it."""
    import numpy as np
    # Clear out files left behind if diemut was ever closed by force mid-way.
    for old in Path(tempfile.gettempdir()).glob("diemut-*.npy"):
        try:
            if time.time() - old.stat().st_mtime > 24 * 3600:
                old.unlink()
        except OSError:
            pass
    fd, path = tempfile.mkstemp(prefix="diemut-", suffix=".npy")
    with os.fdopen(fd, "wb") as f:
        np.save(f, np.asarray(audio, dtype=np.float32))
    return path


def diarize(audio_path, progress, should_stop, num_speakers=None):
    """Find who speaks when, in the audio saved by save_audio (16 kHz mono float32).

    sherpa-onnx holds Python's GIL for the whole run, which would freeze the
    window, so the work happens in a separate process. The file is deleted
    afterwards, and cancelling ends the process straight away.

    num_speakers: how many people speak, if known.
    Returns a list of (start, end, speaker_id), or None if cancelled.
    """
    proc = None
    try:
        ctx = multiprocessing.get_context("spawn")
        receive, send = ctx.Pipe(duplex=False)
        proc = ctx.Process(target=_diarize_child, args=(audio_path, send, num_speakers),
                           daemon=True)
        proc.start()
        send.close()
        while True:
            if should_stop():
                return None
            if not receive.poll(0.1):
                if not proc.is_alive() and not receive.poll():
                    raise RuntimeError("Speaker labelling stopped unexpectedly.")
                continue
            try:
                kind, payload = receive.recv()
            except EOFError:
                raise RuntimeError("Speaker labelling stopped unexpectedly.") from None
            if kind == "progress":
                progress(payload)
            elif kind == "done":
                return payload
            else:
                raise RuntimeError(payload)
    finally:
        if proc is not None:
            if proc.is_alive():
                proc.terminate()
            proc.join(5)
        for _ in range(20):   # on Windows the file stays locked until the process has gone
            try:
                os.remove(audio_path)
                break
            except FileNotFoundError:
                break
            except OSError:
                time.sleep(0.1)


def _diarize_child(audio_path, send, num_speakers):
    """Runs in the separate process started by diarize()."""
    try:
        import numpy as np
        audio = np.load(audio_path, mmap_mode="r")
        diarizer = _get_diarizer(num_speakers)

        def callback(done, total):
            if total:
                send.send(("progress", done / total * 100))
            return 0

        result = diarizer.process(audio, callback=callback).sort_by_start_time()
        send.send(("done", [(r.start, r.end, r.speaker) for r in result]))
    except Exception as exc:
        send.send(("error", str(exc)))
    finally:
        send.close()


def assign_speakers(pieces, turns):
    """Give each piece the speaker whose turn it falls in (or is nearest to).

    Returns (speaker ids in the same order as pieces, number of speakers).
    Speakers are numbered 0, 1, 2… in order of first appearance.
    """
    if not pieces or not turns:
        return [None] * len(pieces), 0
    starts = [t[0] for t in turns]
    raw = []
    for p in pieces:
        mid = (p["start"] + p["end"]) / 2
        i = bisect.bisect_right(starts, mid) - 1
        best, best_dist = None, float("inf")
        for j in (i - 1, i, i + 1):
            if 0 <= j < len(turns):
                s, e, spk = turns[j]
                dist = 0.0 if s <= mid <= e else min(abs(mid - s), abs(mid - e))
                if dist < best_dist:
                    best, best_dist = spk, dist
        raw.append(best)

    # Smooth out one- or two-word blips sandwiched inside another speaker's turn.
    runs = []
    for spk in raw:
        if runs and runs[-1][0] == spk:
            runs[-1][1] += 1
        else:
            runs.append([spk, 1])
    for k in range(1, len(runs) - 1):
        if runs[k][1] <= 2 and runs[k - 1][0] == runs[k + 1][0]:
            runs[k][0] = runs[k - 1][0]
    smoothed = [spk for spk, n in runs for _ in range(n)]

    order = {}
    for spk in smoothed:
        order.setdefault(spk, len(order))
    return [order[s] for s in smoothed], len(order)


# --------------------------------------------------------------------------- #
# Paragraph grouping
# --------------------------------------------------------------------------- #
def starts_new_paragraph(prev, cur, paragraph_len):
    if prev is None:
        return True
    # A manual split or merge (from the correction panel) overrides the pause rule.
    mark = cur.get("par")
    if mark in ("start", "join"):
        return mark == "start"
    if cur["start"] - prev["end"] > PARAGRAPH_PAUSE:
        return True
    return paragraph_len > PARAGRAPH_SOFT_MAX and prev["text"].rstrip().endswith(SENTENCE_ENDS)


def group_blocks(pieces, use_speakers):
    """Group pieces into speaker blocks, each split into paragraphs.

    Returns [{"speaker": id or None, "start": t,
              "paragraphs": [{"start", "end", "text"}, …]}, …]
    """
    blocks, prev = [], None
    for i, p in enumerate(pieces):
        spk = p.get("speaker") if use_speakers else None
        if not blocks or blocks[-1]["speaker"] != spk:
            blocks.append({"speaker": spk, "start": p["start"], "paragraphs": []})
            prev = None
        paragraphs = blocks[-1]["paragraphs"]
        current_len = len(paragraphs[-1]["text"]) if paragraphs else 0
        if starts_new_paragraph(prev, p, current_len):
            # i0/i1: the range of pieces this paragraph was built from
            paragraphs.append({"start": p["start"], "end": p["end"], "text": "", "i0": i})
        paragraphs[-1]["text"] += p["text"]
        paragraphs[-1]["end"] = p["end"]
        paragraphs[-1]["i1"] = i + 1
        prev = p
    for block in blocks:
        for par in block["paragraphs"]:
            par["text"] = " ".join(par["text"].split()) if " " in par["text"] else par["text"].strip()
    return blocks
