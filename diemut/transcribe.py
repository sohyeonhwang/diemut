"""Speech-to-text with faster-whisper: files and live microphone.

The workers here run in background threads and report back through
emit(kind, payload). Kinds: "status", "progress", "stage", "language",
"duration", "piece", "pending", "started", "reset", "speakers", "done", "error".
"""

import queue
import threading

import numpy as np

from . import speakers
from .languages import AUTO, MULTI

SAMPLE_RATE = 16000

# (model name, size label, note shown in the dropdown)
MODELS = [
    ("tiny", "~75 MB", "fastest"),
    ("base", "~145 MB", ""),
    ("small", "~480 MB", "recommended"),
    ("medium", "~1.5 GB", ""),
    ("large-v3-turbo", "~1.6 GB", ""),
    ("large-v3", "~3 GB", "most accurate"),
]
DEFAULT_MODEL = "small"
SLOW_MODELS = {"medium", "large-v3-turbo", "large-v3"}

MODEL_DESCRIPTIONS = {
    "tiny": "Fastest and lightest, but makes the most mistakes.",
    "base": "Fast; fine for clear speech in quiet rooms.",
    "small": "Good balance of speed and accuracy; works for live mode on most laptops.",
    "medium": "More accurate, noticeably slower.",
    "large-v3-turbo": "Close to large-v3 accuracy at about twice the speed.",
    "large-v3": "The most accurate Whisper model; slow without a GPU.",
}

# Live mode: audio is collected until there's a short pause after at least
# LIVE_MIN_SECONDS, or until LIVE_MAX_SECONDS is reached.
LIVE_MIN_SECONDS = 3.0
LIVE_MAX_SECONDS = 15.0
LIVE_PAUSE_SECONDS = 0.6
SILENCE_FLOOR = 0.002


class AudioClip:
    """The session's audio, kept in memory (16-bit) so paragraphs can be played back.

    Stored as runs of contiguous audio, because live mode skips paused time.
    """

    def __init__(self):
        self.parts = []   # [start_seconds, [int16 arrays], n_samples, cached_concat]

    def add(self, offset, samples):
        data = (np.clip(samples, -1.0, 1.0) * 32767).astype(np.int16)
        last = self.parts[-1] if self.parts else None
        if last and abs(last[0] + last[2] / SAMPLE_RATE - offset) < 0.05:
            last[1].append(data)
            last[2] += len(data)
            last[3] = None
        else:
            self.parts.append([offset, [data], len(data), None])

    @property
    def end(self):
        """Seconds from the start of the session to the end of the last audio kept."""
        return max((p[0] + p[2] / SAMPLE_RATE for p in self.parts), default=0.0)

    def slice(self, start, end):
        """Audio between start and end seconds as float32 (silence where nothing was kept)."""
        start = max(0.0, start)
        out = np.zeros(max(0, int((end - start) * SAMPLE_RATE)), dtype=np.float32)
        for part in self.parts:
            p_start, p_end = part[0], part[0] + part[2] / SAMPLE_RATE
            if p_end <= start or p_start >= end:
                continue
            if part[3] is None:
                part[3] = np.concatenate(part[1])
            a = max(start, p_start)
            b = min(end, p_end)
            src = part[3][int((a - p_start) * SAMPLE_RATE):int((b - p_start) * SAMPLE_RATE)]
            dst = int((a - start) * SAMPLE_RATE)
            n = min(len(src), len(out) - dst)
            out[dst:dst + n] = src[:n].astype(np.float32) / 32767
        return out


def model_size(name):
    return next((size for n, size, _ in MODELS if n == name), "")


def _rms(x):
    return float(np.sqrt(np.mean(np.square(x)))) if len(x) else 0.0


def _resample(x, src_rate, dst_rate):
    if src_rate == dst_rate or len(x) == 0:
        return x
    n = int(round(len(x) * dst_rate / src_rate))
    return np.interp(np.linspace(0, len(x), n, endpoint=False),
                     np.arange(len(x)), x).astype(np.float32)


def _is_gpu_error(exc):
    text = str(exc).lower()
    return any(word in text for word in ("cuda", "cudnn", "cublas", "gpu"))


class SpeechToText:
    """Loads and caches one Whisper model. GPU if it works, otherwise CPU."""

    def __init__(self):
        self.model = None
        self.key = None
        self.device = None
        self.force_cpu = False
        self.lock = threading.Lock()

    def load(self, name, status):
        from faster_whisper import WhisperModel
        with self.lock:
            key = (name, self.force_cpu)
            if self.model is not None and self.key == key:
                return self.model
            self.model = None
            status(f"Loading whisper-{name}… (the first use downloads {model_size(name)})")
            attempts = [("cpu", "int8")]
            if not self.force_cpu:
                try:
                    import ctranslate2
                    if ctranslate2.get_cuda_device_count() > 0:
                        attempts.insert(0, ("cuda", "float16"))
                except Exception:
                    pass
            last_error = None
            for device, compute_type in attempts:
                try:
                    self.model = WhisperModel(name, device=device, compute_type=compute_type)
                    self.key, self.device = key, device
                    return self.model
                except Exception as exc:
                    last_error = exc
            raise last_error

    def fall_back_to_cpu(self):
        self.force_cpu = True
        self.model = None


# --------------------------------------------------------------------------- #
# File transcription
# --------------------------------------------------------------------------- #
def file_worker(stt, path, model_name, language, emit, stop_event, num_speakers=None):
    """num_speakers: how many people speak in the file, if the person said; or None."""
    try:
        from faster_whisper import decode_audio
        model = stt.load(model_name, lambda m: emit("status", m))
        emit("status", "Reading the file…")
        audio = decode_audio(path, sampling_rate=SAMPLE_RATE)
        duration = len(audio) / SAMPLE_RATE
        emit("duration", duration)
        clip = AudioClip()
        clip.add(0.0, audio)
        emit("audio", clip)

        words = None
        for attempt in range(2):
            try:
                words = _transcribe_words(model, audio, duration, language, emit, stop_event)
                break
            except Exception as exc:
                if attempt == 0 and stt.device == "cuda" and _is_gpu_error(exc):
                    emit("reset", None)
                    emit("status", "The GPU didn't work, so diemut is switching to the CPU…")
                    stt.fall_back_to_cpu()
                    model = stt.load(model_name, lambda m: emit("status", m))
                else:
                    raise
        if stop_event.is_set():
            emit("done", "Cancelled. The partial transcript is kept.")
            return
        if not words:
            emit("done", "Done. No speech was found in this file.")
            return

        # Speaker labels
        if num_speakers == 1:
            emit("speakers", ([0] * len(words), 1))
            emit("done", _DONE)
            return
        emit("stage", "speakers")
        try:
            speakers.ensure_models(lambda m: emit("status", m))
            audio_path = speakers.save_audio(audio)
        except Exception as exc:
            emit("done", f"Done, but speaker labels didn't work: {exc}")
            return
        del audio   # free it while speakers are found; playback has its own copy in clip
        emit("done", _label_speakers(audio_path, words, num_speakers, emit, stop_event))
    except Exception as exc:
        emit("error", str(exc))


def speaker_worker(clip, duration, pieces, num_speakers, emit, stop_event):
    """Find speakers again for a finished file transcript, keeping its text."""
    try:
        if num_speakers == 1:
            emit("speakers", ([0] * len(pieces), 1))
            emit("done", _DONE)
            return
        emit("stage", "speakers")
        speakers.ensure_models(lambda m: emit("status", m))
        audio_path = speakers.save_audio(clip.slice(0.0, duration))
        emit("done", _label_speakers(audio_path, pieces, num_speakers, emit, stop_event))
    except Exception as exc:
        emit("error", str(exc))


_DONE = "Done. Click a paragraph to listen and correct it."


def _label_speakers(audio_path, pieces, num_speakers, emit, stop_event):
    """Find speakers in the saved audio and send their ids. Returns the "done" message."""
    try:
        emit("status", "Finding speakers…")
        turns = speakers.diarize(audio_path, lambda pct: emit("progress", pct),
                                 stop_event.is_set, num_speakers)
    except Exception as exc:
        return f"Done, but speaker labels didn't work: {exc}"
    if turns is None:
        return "Cancelled during speaker labelling. The transcript is kept."
    ids, count = speakers.assign_speakers(pieces, turns)
    emit("speakers", (ids, count))
    return _DONE + (" Click a name to rename or merge speakers." if count > 1 else "")


def _transcribe_words(model, audio, duration, language, emit, stop_event):
    emit("stage", "transcribing")
    emit("status", "Transcribing…")
    segments, info = model.transcribe(
        audio,
        language=None if language in (AUTO, MULTI) else language,
        multilingual=(language == MULTI),
        beam_size=5,
        vad_filter=True,
        word_timestamps=True,
    )
    if language == AUTO:
        emit("language", info.language)
    words = []
    for seg in segments:
        if stop_event.is_set():
            break
        items = seg.words or [type("W", (), {"start": seg.start, "end": seg.end, "word": seg.text})]
        for w in items:
            if not w.word.strip():
                continue
            piece = {"start": float(w.start), "end": float(w.end), "text": w.word, "speaker": None}
            words.append(piece)
            emit("piece", piece)
        if duration:
            emit("progress", min(100.0, seg.end / duration * 100))
    return words


# --------------------------------------------------------------------------- #
# Live transcription
# --------------------------------------------------------------------------- #
def live_worker(stt, model_name, language, emit, stop_event, pause_event):
    import sounddevice as sd
    stream = None
    try:
        model = stt.load(model_name, lambda m: emit("status", m))
        audio_q = queue.Queue()

        def callback(indata, frames, time_info, status):
            audio_q.put(indata[:, 0].copy())

        rate = SAMPLE_RATE
        try:
            stream = sd.InputStream(samplerate=rate, channels=1, dtype="float32", callback=callback)
        except Exception:
            rate = int(sd.query_devices(kind="input")["default_samplerate"])
            stream = sd.InputStream(samplerate=rate, channels=1, dtype="float32", callback=callback)
        clip = AudioClip()
        emit("audio", clip)
        stream.start()
        emit("started", None)
        emit("status", "Listening… speak into your microphone.")

        state = {"language": None if language in (AUTO, MULTI) else language,
                 "prompt": "", "model": model}
        min_len = int(LIVE_MIN_SECONDS * SAMPLE_RATE)
        max_len = int(LIVE_MAX_SECONDS * SAMPLE_RATE)
        pause_len = int(LIVE_PAUSE_SECONDS * SAMPLE_RATE)
        buf = np.zeros(0, dtype=np.float32)
        offset = 0.0   # seconds since Start, including paused time
        clock = 0.0    # time of the newest block received

        def flush(chunk, start):
            if len(chunk) > 0.3 * SAMPLE_RATE and _rms(chunk) >= SILENCE_FLOOR:
                _transcribe_chunk(stt, model_name, chunk, start, language, state, emit)

        while not stop_event.is_set():
            paused = pause_event.is_set()
            if paused and len(buf):
                flush(buf, offset)
                offset += len(buf) / SAMPLE_RATE
                buf = np.zeros(0, dtype=np.float32)
            try:
                block = _resample(audio_q.get(timeout=0.1), rate, SAMPLE_RATE)
            except queue.Empty:
                continue
            if not paused:
                clip.add(clock, block)
            clock += len(block) / SAMPLE_RATE
            if paused:
                offset += len(block) / SAMPLE_RATE   # keep the clock running
                continue
            buf = np.concatenate((buf, block))
            if len(buf) < min_len:
                continue
            level = _rms(buf)
            if level < SILENCE_FLOOR:
                offset += len(buf) / SAMPLE_RATE
                buf = np.zeros(0, dtype=np.float32)
                continue
            if len(buf) >= max_len or _rms(buf[-pause_len:]) < max(SILENCE_FLOOR, 0.3 * level):
                flush(buf, offset)
                offset += len(buf) / SAMPLE_RATE
                buf = np.zeros(0, dtype=np.float32)

        stream.stop()
        stream.close()
        stream = None
        while not audio_q.empty():
            block = _resample(audio_q.get(), rate, SAMPLE_RATE)
            clip.add(clock, block)
            clock += len(block) / SAMPLE_RATE
            buf = np.concatenate((buf, block))
        flush(buf, offset)
        emit("done", "Stopped. Click a paragraph to listen and correct it, or press Start "
                     "to begin a new recording.")
    except Exception as exc:
        emit("error", str(exc))
    finally:
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass


def _transcribe_chunk(stt, model_name, audio, offset, language, state, emit):
    emit("pending", True)
    try:
        try:
            segments, info = state["model"].transcribe(
                audio, language=state["language"], beam_size=1, vad_filter=True,
                condition_on_previous_text=False, initial_prompt=state["prompt"] or None,
                word_timestamps=True)
            segments = list(segments)
        except Exception as exc:
            if stt.device != "cuda" or not _is_gpu_error(exc):
                raise
            emit("status", "The GPU didn't work, so diemut is switching to the CPU…")
            stt.fall_back_to_cpu()
            state["model"] = stt.load(model_name, lambda m: emit("status", m))
            segments, info = state["model"].transcribe(
                audio, language=state["language"], beam_size=1, vad_filter=True,
                condition_on_previous_text=False, initial_prompt=state["prompt"] or None,
                word_timestamps=True)
            segments = list(segments)
            emit("status", "Listening… speak into your microphone.")
    finally:
        emit("pending", False)

    texts = []
    for seg in segments:
        if not seg.text.strip():
            continue
        texts.append(seg.text.strip())
        items = seg.words or [type("W", (), {"start": seg.start, "end": seg.end, "word": seg.text})]
        for w in items:
            if w.word.strip():
                emit("piece", {"start": offset + float(w.start), "end": offset + float(w.end),
                               "text": w.word, "speaker": None})
    if texts:
        # Auto-detect locks onto the first language heard; Multiple languages never locks.
        if language == AUTO and state["language"] is None:
            state["language"] = info.language
            emit("language", info.language)
        state["prompt"] = (state["prompt"] + " " + " ".join(texts)).strip()[-200:]
