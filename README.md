# diemut

diemut turns speech into text on your own computer. It can transcribe your microphone live or an audio/video file, label who is speaking, and export the result as `.txt`, `.vtt` (subtitles) or `.docx` (Word).

Everything runs locally. Your audio never leaves your computer; the only internet use is downloading the models the first time you need them.

| Start screen | Transcribing a file | Live from microphone |
|---|---|---|
| ![Start screen](docs/start.png) | ![A finished file transcript with speaker labels](docs/file.png) | ![Live transcription while recording](docs/live.png) |

*(Screenshots were taken on Linux. On Windows and macOS, diemut looks much the same but uses your system's fonts.)*

## What it does

- **Live from microphone:** Start, Pause/Resume and Stop. Text appears a few seconds after you speak, grouped into paragraphs at natural pauses.
- **Audio or video files:** mp3, wav, m4a, mp4, mkv, mov and more. You get timestamps, speaker labels (Speaker 1, Speaker 2…, which you can rename by clicking them) and paragraphs.
- **Listen and correct:** click any paragraph to play its audio, fix the text, or move it to a different speaker.
- **Export** to `.txt`, `.vtt` and `.docx`. Speaker names and paragraphs carry over to every format.
- **100 languages**, plus a *Multiple languages* option for recordings that switch between languages.

Under the hood, diemut uses three models:

- **`whisper-small`** for speech-to-text: OpenAI's Whisper, run with faster-whisper. You can pick a larger or smaller model on the start screen.
- **`sherpa-onnx`** for speaker labels.
- **pause grouping** for paragraph breaks.

---

## Setup on Windows

> **Using WSL?** Run diemut with **Windows** Python, not inside WSL. diemut needs a window and your microphone, and both are unreliable through WSL. You can keep using WSL for everything else.

### 1. Install Python

1. Go to [python.org/downloads](https://www.python.org/downloads/) and download **Python 3.12** for Windows.
2. Run the installer. On the first screen, **tick "Add python.exe to PATH"**, then click *Install Now*.
3. Open **PowerShell**: press the Windows key, type `powershell`, and press Enter. Check that Python works:
   ```powershell
   py --version
   ```
   You should see something like `Python 3.12.x`.

### 2. Get diemut

Put diemut in a folder that **isn't synced by OneDrive**. Documents and Desktop often are, and syncing causes slowdowns and odd errors. `C:\Users\<your name>\code\diemut` is a good choice.

**If you have Git:**
```powershell
mkdir $HOME\code
cd $HOME\code
git clone https://github.com/YOUR-LAB/diemut.git
cd diemut
```

**If you don't:** on the GitHub page, click the green **Code** button, then **Download ZIP**. Unzip it into `C:\Users\<your name>\code\`, and rename the folder to `diemut` if it's called something like `diemut-main`. Then:
```powershell
cd $HOME\code\diemut
```

### 3. Create a virtual environment

A virtual environment is a private set of Python packages just for diemut, so it won't interfere with anything else on your computer.

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
```

Your prompt should now start with `(.venv)`.

If PowerShell says *"running scripts is disabled on this system"*, run this once, then try the `Activate.ps1` line again:
```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

### 4. Install diemut

```powershell
pip install .
```

This takes a few minutes the first time.

### 5. Run it

```powershell
diemut
```

### 6. Allow microphone access (for live mode)

Open **Settings → Privacy & security → Microphone**, and turn on both **"Microphone access"** and **"Let desktop apps access your microphone"**.

### Running diemut again later

Open PowerShell and run:
```powershell
cd $HOME\code\diemut
.venv\Scripts\Activate.ps1
diemut
```

---

## Setup on macOS

1. Install **Python 3.12** from [python.org/downloads](https://www.python.org/downloads/). Use the python.org installer: it includes the window toolkit diemut needs, which Homebrew's Python sometimes doesn't.
2. Get diemut (with `git clone` or the ZIP, as in the Windows steps above), then open **Terminal** in the diemut folder.
3. Install and run:
   ```bash
   python3.12 -m venv .venv
   source .venv/bin/activate
   pip install .
   diemut
   ```
4. The first time you use live mode, macOS asks whether Terminal may use the microphone. Click **Allow**. If you missed it, go to **System Settings → Privacy & Security → Microphone** and turn on Terminal.

To run it again later: `cd` into the folder, run `source .venv/bin/activate`, then `diemut`.

## Setup on Linux (Ubuntu/Debian)

```bash
sudo apt install python3-venv python3-tk libportaudio2
git clone https://github.com/YOUR-LAB/diemut.git
cd diemut
python3 -m venv .venv
source .venv/bin/activate
pip install .
diemut
```

---

## The first run

The first time you use each model, diemut downloads it. The status bar shows the progress.

| What | Size | Saved in |
|---|---|---|
| Whisper model (default: small) | ~480 MB | `~/.cache/huggingface/hub` |
| Speaker models (first file you transcribe) | ~47 MB | `~/.cache/diemut/models` |

On Windows, `~` means `C:\Users\<your name>`. After these downloads, diemut works offline. To keep the speaker models somewhere else, such as a shared lab drive, set the environment variable `DIEMUT_HOME` to that folder.

## Using diemut

**Start screen.** Choose the **STT model** and **Language**, then pick a mode. Hover over the model names under the title to see what each one does.

**Live from microphone**
1. Click **Start** and speak. Text appears a few seconds behind you, after each natural pause.
2. **Pause** stops listening without ending the session; **Resume** carries on. Timestamps keep counting in real time while paused.
3. **Stop** ends the session and transcribes your last few words.
4. Tick **Always on top** to keep the window in front of other apps, for example as captions during a video call.

**Audio or video file**
1. Choose a file. diemut transcribes it first, then finds the speakers. The progress bar shows each stage, and **Cancel** stops early but keeps what's done so far.
2. Click a speaker's name to rename it, for example from "Speaker 2" to "Maya".
3. **New file…** starts another file without going back to the start screen.

**Listening and correcting.** Once a transcription has finished (or you've pressed Stop in live mode), click any paragraph. It's highlighted, and a panel opens below the transcript:
- **▶ Play** plays that paragraph's audio. Ctrl+Space does the same while you're typing.
- Type your correction in the text box. **Ctrl+Enter** saves it, and **Esc** discards your changes.
- **Speaker** (files only) moves the paragraph to another speaker, or to a *New speaker* if two people were merged into one.
- **◀ Prev / Next ▶** step through the transcript paragraph by paragraph and save as you go. **Done** saves and closes the panel.

Corrections carry over to every export format. Corrected words keep their paragraph's start and end times, spread evenly in between, so subtitle timing in a corrected paragraph is approximate. Deleting all the text in a paragraph removes it.

**Exporting.** Use the **.txt**, **.vtt** or **.docx** buttons. The **Timestamps** checkbox controls whether `.txt` and `.docx` include times; `.vtt` subtitles always have them.

**Clear** removes the transcript from the window. diemut warns you before discarding a transcript you haven't exported.

## Choosing a model

| STT model | Download | Good for |
|---|---|---|
| tiny | ~75 MB | Quick tests; slower laptops |
| base | ~145 MB | Clear speech in quiet rooms |
| **small** (default) | ~480 MB | Most uses, including live mode |
| medium | ~1.5 GB | Better accuracy for files; may lag in live mode |
| large-v3-turbo | ~1.6 GB | Near-best accuracy for files on a strong computer |
| large-v3 | ~3 GB | Best accuracy; slow without a GPU |

For languages other than English, the larger models are noticeably more accurate.

**Rough speed on a typical laptop CPU** for one hour of audio: small takes about 15–25 minutes, and large-v3 can take over an hour. Speaker labelling adds a few minutes. This varies a lot between computers.

**GPU (optional):** if you have an NVIDIA graphics card with CUDA 12 and cuDNN 9 installed, diemut uses it automatically. If the GPU doesn't work, diemut quietly switches to the CPU.

## Languages

Whisper supports 100 languages. The dropdown lists the **best supported** ones first, followed by every language from A to Z.

- **Auto-detect** guesses the language from the first 30 seconds of audio and uses it for the whole recording.
- **Multiple languages** re-detects the language every ~30 seconds. This works well when speakers switch between languages from sentence to sentence. Switching *within* a sentence isn't handled well.

## Troubleshooting

**`py` or `python` isn't recognised, or opens the Microsoft Store.** Python wasn't added to PATH. Rerun the installer, choose *Modify*, and tick *Add Python to environment variables*.

**"Microphone unavailable", or live mode hears nothing.** Check the Windows microphone settings in step 6 of the Windows setup. Also check which microphone is selected as the default under Settings → System → Sound → Input.

**A model download fails.** Check your internet connection. Some university networks block Hugging Face or GitHub downloads; try another network, or ask IT to allow `huggingface.co` and `github.com`.

**Speaker labels split one person into two, or merge two people.** Speaker detection works best with clear audio and people taking turns. You can adjust `SPEAKER_THRESHOLD` in `diemut/speakers.py`: higher means fewer speakers, lower means more. After editing the code, run `pip install .` again.

**Live mode falls behind.** Pick a smaller STT model, such as small or base.

**Updating diemut.** In the diemut folder, with the virtual environment active, run:
```powershell
git pull
pip install .
```
If you downloaded the ZIP, download the new ZIP and replace the old folder instead of running `git pull`.

## Project layout

```
diemut/
├── __main__.py     starts the app
├── ui.py           start screen and transcript screen
├── transcribe.py   Whisper (files and live), model choices, live-mode timing
├── speakers.py     speaker labels and paragraph grouping
├── export.py       .txt, .vtt and .docx
└── languages.py    the language list
```

Settings you might want to adjust are constants near the top of each file. For example, `PARAGRAPH_PAUSE` in `speakers.py` sets how long a pause must be before a new paragraph starts.

## License

diemut is released under the MIT License (see `LICENSE`). The models and libraries it uses have their own licenses, listed in `THIRD_PARTY.md`.
