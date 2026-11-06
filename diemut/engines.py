"""Speech-to-text engines: the programs that run Whisper.

The model (tiny, small… or later a custom one) is *which* Whisper runs; the engine is *how*,
and on which chip. Each engine loads a model and turns 16 kHz audio into segments with
per-word times, so the rest of diemut doesn't care which one is running. An engine has:

    name                         shown in the header tag, e.g. "faster-whisper"
    device                       where the loaded model runs: "cuda", "gpu", "cpu"…
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


class FasterWhisper:
    """faster-whisper (CTranslate2): CPUs everywhere, and NVIDIA GPUs through CUDA."""

    name = "faster-whisper"

    def __init__(self):
        self.model = None
        self.key = None
        self.device = None
        self.force_cpu = False

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
            audio, language=language, multilingual=multilingual,
            beam_size=1 if quick else 5, vad_filter=True, word_timestamps=True,
            condition_on_previous_text=not quick, initial_prompt=prompt)
        return segments, info.language


ENGINES = {"faster-whisper": FasterWhisper}
