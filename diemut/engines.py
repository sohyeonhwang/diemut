"""Speech-to-text engines: the programs that run Whisper.

The model (tiny, small… or later a custom one) is *which* Whisper runs; the engine is *how*,
and on which chip. Each engine loads a model and turns 16 kHz audio into segments with
per-word times, so the rest of diemut doesn't care which one is running. An engine has:

    name                         shown in the header tag, e.g. "faster-whisper"
    device                       where the loaded model runs: "cuda", "gpu", "cpu"…
    label()                      both, for the header tag: "OpenVINO · GPU"
    loaded(model) -> bool        is that model ready?
    load(model)                  load it (downloading it the first time)
    fall_back_to_cpu()           after a GPU error
    is_gpu_error(exc) -> bool
    detect_language(audio) -> language code
    transcribe(audio, language, prompt, multilingual=False, quick=False)
        -> (segments, detected language); segments have .start, .end, .text and .words,
        each word .start, .end and .word (with its leading space)

quick is for live mode's short chunks: the fastest settings, without carrying text over.
"""

from pathlib import Path

SAMPLE_RATE = 16000
WINDOW = 28.0           # seconds: OpenVINO is given stretches shorter than Whisper's 30 s
SPACELESS = {"zh", "ja", "th", "lo", "km", "my", "yue", "bo"}   # written without spaces


def _label(name, device):
    return f"{name} · {'GPU' if device in ('cuda', 'gpu') else 'CPU'}"


class _Segment:
    def __init__(self, start, end, text, words):
        self.start, self.end, self.text, self.words = start, end, text, words


class _Word:
    def __init__(self, start, end, word):
        self.start, self.end, self.word = start, end, word


def windows_from_speech(regions, cuts=(), longest=WINDOW):
    """Windows no longer than longest seconds for OpenVINO. regions: the speech, as (start,
    end) seconds in order (long silences are left out, as faster-whisper does); cuts: good
    places to cut inside them (the middles of short pauses). A region too long for one window
    is cut at the last pause that fits (at least halfway in), or evenly if there's none;
    regions are then put together while they fit. Returns [(start, end)]."""
    cuts = sorted(cuts)
    pieces = []
    for a, b in regions:
        while b - a > longest:
            fits = [c for c in cuts if a + longest / 2 <= c <= a + longest]
            c = fits[-1] if fits else a + (b - a) / -(-(b - a) // longest)
            pieces.append((a, c))
            a = c
        pieces.append((a, b))
    out = []
    for a, b in pieces:
        if out and b - out[-1][0] <= longest:
            out[-1] = (out[-1][0], b)
        else:
            out.append((a, b))
    return out


def spaced_words(text, words, spaced=True):
    """Each word with the space it has in text (Whisper's own writing of the window) in
    front of it, or none: "40%" stays "40%", not "40 %". words: the words as OpenVINO gives
    them, without their spacing. The first word always gets one (a new stretch)."""
    out, pos = [], 0
    for i, w in enumerate(words):
        w = w.strip()
        at = text.find(w, pos)
        if at >= 0:
            space = i == 0 or (at > 0 and text[at - 1].isspace())
            pos = at + len(w)
        else:
            space = True
        out.append((" " if spaced and space else "") + w)
    return out


class FasterWhisper:
    """faster-whisper (CTranslate2): CPUs everywhere, and NVIDIA GPUs through CUDA."""

    name = "faster-whisper"

    def __init__(self):
        self.model = None
        self.key = None
        self.device = None
        self.force_cpu = False

    def label(self):
        return _label("faster-whisper", self.device)

    def loaded(self, model):
        return self.model is not None and self.key == (model, self.force_cpu)

    def load(self, model):
        from faster_whisper import WhisperModel
        self.model = None
        attempts = [("cpu", "int8")]
        if not self.force_cpu:
            try:
                import ctranslate2
                if ctranslate2.get_cuda_device_count() > 0:
                    attempts.insert(0, ("cuda", "float16"))
            except Exception:
                pass
        last_error = None
        for device, compute_type in attempts:      # the GPU if it works, otherwise the CPU
            try:
                self.model = WhisperModel(model, device=device, compute_type=compute_type)
                self.key, self.device = (model, self.force_cpu), device
                return
            except Exception as exc:
                last_error = exc
        raise last_error

    def fall_back_to_cpu(self):
        self.force_cpu = True
        self.model = None

    def is_gpu_error(self, exc):
        text = str(exc).lower()
        return self.device == "cuda" and any(w in text for w in ("cuda", "cudnn", "cublas", "gpu"))

    def detect_language(self, audio):
        language, _, _ = self.model.detect_language(audio)
        return language

    def transcribe(self, audio, language, prompt, multilingual=False, quick=False):
        segments, info = self.model.transcribe(
            audio, language=language, multilingual=multilingual and not quick,
            beam_size=1 if quick else 5, vad_filter=True, word_timestamps=True,
            condition_on_previous_text=not quick, initial_prompt=prompt)
        return segments, info.language



def openvino_installed():
    try:
        import openvino_genai  # noqa: F401
        return True
    except Exception:
        return False


class OpenVINO:
    """OpenVINO (Intel): Intel graphics cards, and a faster CPU path on Intel processors.

    Two things it does differently from faster-whisper, measured on an Intel Arc 140V:
    - it only uses the starting prompt for the first 30 s of a recording, so the verbatim
      prompt stopped keeping "um"s after that. diemut cuts the audio itself, at pauses
      (faster-whisper's silence detector), into windows under 30 s and gives each the prompt.
    - it has no beam search, and word times must be asked for when the model is loaded.
    The NPU isn't used: compiling Whisper for it took about 2 minutes, and it was slower than
    the GPU once running.
    """

    name = "OpenVINO"
    REPO = "OpenVINO/whisper-{}-int8-ov"     # Intel's 8-bit conversions on Hugging Face

    def __init__(self, device=None):
        self.prefer = device            # "GPU", "CPU", or None for the GPU if there is one
        self.pipe = None
        self.key = None
        self.device = None
        self.force_cpu = False
        self._detector = None           # a small faster-whisper model, to tell languages apart

    def label(self):
        return _label("OpenVINO", self.device)

    def loaded(self, model):
        return self.pipe is not None and self.key == (model, self.force_cpu)

    def load(self, model):
        import openvino as ov
        import openvino_genai as ov_genai
        from huggingface_hub import snapshot_download

        from .settings import HOME
        self.pipe = None
        repo = self.REPO.format(model)
        try:
            path = snapshot_download(repo)
        except Exception as exc:
            if type(exc).__name__ in ("RepositoryNotFoundError", "GatedRepoError"):
                raise RuntimeError(f"there's no OpenVINO copy of whisper-{model}") from exc
            raise RuntimeError(f"couldn't download {repo} ({type(exc).__name__}: {exc}). "
                               "Check the internet connection and try again") from exc
        device = "CPU" if self.force_cpu else self.prefer or (
            "GPU" if "GPU" in ov.Core().available_devices else "CPU")
        cache = Path(HOME) / "openvino_cache"       # compiled models: later loads are faster
        cache.mkdir(parents=True, exist_ok=True)
        self.pipe = ov_genai.WhisperPipeline(path, device, word_timestamps=True,
                                             CACHE_DIR=str(cache))
        self.key, self.device = (model, self.force_cpu), device.lower()

    def fall_back_to_cpu(self):
        self.force_cpu = True
        self.pipe = None

    def is_gpu_error(self, exc):
        return self.device == "gpu"

    def detect_language(self, audio):
        if self._detector is None:
            self._detector = FasterWhisper()
            self._detector.force_cpu = True
            self._detector.load("tiny")
        return self._detector.detect_language(audio[:int(30 * SAMPLE_RATE)])

    def _windows(self, audio):
        """The windows to transcribe: faster-whisper's silence detector with its own settings
        decides what's speech (so a quiet "um" isn't trimmed off), and a finer pass finds
        short pauses to cut at."""
        from faster_whisper.vad import VadOptions, get_speech_timestamps

        def spans(options):
            return [(s["start"] / SAMPLE_RATE, s["end"] / SAMPLE_RATE)
                    for s in get_speech_timestamps(audio, options)]
        fine = spans(VadOptions(min_silence_duration_ms=300, speech_pad_ms=100))
        cuts = [(fine[i][1] + fine[i + 1][0]) / 2 for i in range(len(fine) - 1)]
        return windows_from_speech(spans(VadOptions()), cuts)

    def transcribe(self, audio, language, prompt, multilingual=False, quick=False):
        detected = language or (None if multilingual else self.detect_language(audio))
        if quick:                        # a live chunk: already shorter than 30 s
            parts = [(0.0, len(audio) / SAMPLE_RATE)]
        else:
            parts = self._windows(audio)
        fixed = detected if not multilingual else None

        def segments():
            before = ""             # the end of what was written so far (faster-whisper
            for a, b in parts:      # carries its earlier text over the same way)
                chunk = audio[int(a * SAMPLE_RATE):int(b * SAMPLE_RATE)]
                if len(chunk) < SAMPLE_RATE // 10:
                    continue
                options = {"task": "transcribe", "return_timestamps": True,
                           "word_timestamps": True}
                if fixed:
                    options["language"] = f"<|{fixed}|>"
                start = " ".join(p for p in (prompt, before if not quick else "") if p)
                if start:
                    options["initial_prompt"] = start
                result = self.pipe.generate(chunk.tolist(), **options)
                found = [w for w in getattr(result, "words", None) or []
                         if (getattr(w, "word", None) or getattr(w, "text", "")).strip()]
                if not found:
                    continue
                text = result.texts[0] if getattr(result, "texts", None) else ""
                spelled = spaced_words(
                    text, [getattr(w, "word", None) or getattr(w, "text", "") for w in found],
                    spaced=(fixed or detected) not in SPACELESS)
                words = [_Word(a + w.start_ts, a + w.end_ts, t) for w, t in zip(found, spelled)]
                before = (before + "".join(spelled))[-200:].strip()
                yield _Segment(words[0].start, words[-1].end,
                               "".join(w.word for w in words), words)
        return segments(), detected


# Settings' "engine" choice -> how to make it (None: Automatic)
CHOICES = {
    "auto": "Automatic",
    "faster-whisper": "faster-whisper",
    "openvino-gpu": "OpenVINO (Intel) on the GPU",
    "openvino-cpu": "OpenVINO (Intel) on the CPU",
}


def make(choice):
    """The engine for a Settings choice. Automatic: faster-whisper on an NVIDIA GPU; else
    OpenVINO if it's installed (an Intel graphics card if there is one); else faster-whisper."""
    if choice == "faster-whisper":
        return FasterWhisper()
    if choice in ("openvino-gpu", "openvino-cpu") and openvino_installed():
        return OpenVINO("GPU" if choice == "openvino-gpu" else "CPU")
    if choice == "auto" and openvino_installed() and not _cuda():
        return OpenVINO()
    return FasterWhisper()


def _cuda():
    try:
        import ctranslate2
        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False
