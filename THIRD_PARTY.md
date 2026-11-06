# Third-party software and models

diemut's own code is MIT-licensed (see `LICENSE`). It relies on the projects below, which keep their own licenses. The models are downloaded the first time you use them; they are not stored in this repository.

Please check each project's current license before redistributing diemut in a bundled form (for example, as a packaged .exe that includes the models).

## Models

| Model | Used for | Source | License |
|---|---|---|---|
| OpenAI Whisper (tiny → large-v3), CTranslate2 conversions | Speech-to-text | huggingface.co/Systran (and mobiuslabsgmbh for large-v3-turbo) | MIT |
| pyannote segmentation-3.0 (ONNX conversion by sherpa-onnx) | Finding where each speaker turn starts and ends | github.com/k2-fsa/sherpa-onnx releases | MIT (CNRS) |
| NVIDIA NeMo TitaNet-small (ONNX conversion by sherpa-onnx) | Telling voices apart | github.com/k2-fsa/sherpa-onnx releases | CC BY 4.0 (per NVIDIA's model card) |
| Silero VAD (bundled with faster-whisper) | Skipping silence | github.com/snakers4/silero-vad | MIT |
| OpenAI Whisper, Intel's OpenVINO conversions (optional) | Speech-to-text with the OpenVINO engine | huggingface.co/OpenVINO | MIT |

## App icon

diemut's icon (`diemut/assets/`) is drawing of a little snail I drew, inspired by the weird medieval snail marginalia. 

## Libraries

| Library | License |
|---|---|
| faster-whisper | MIT |
| CTranslate2 | MIT |
| sherpa-onnx | Apache 2.0 |
| ONNX Runtime | MIT |
| PyAV (and the FFmpeg libraries it bundles) | BSD-3-Clause (FFmpeg: LGPL) |
| sounddevice / PortAudio | MIT |
| python-docx | MIT |
| OpenVINO, OpenVINO GenAI, OpenVINO Tokenizers (optional: `pip install ".[openvino]"`) | Apache 2.0 |
| NumPy | BSD-3-Clause |
