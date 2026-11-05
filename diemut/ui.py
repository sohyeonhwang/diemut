"""diemut's window: the start screen and the transcript screen."""

import datetime as dt
import os
import queue
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, simpledialog, ttk

from . import export, speakers, transcribe
from .export import fmt_time
from .languages import AUTO, BEST_SUPPORTED, LANGUAGES, MULTI, language_name

# Newer emoji only draw reliably in Tk on Windows; elsewhere use simple symbols.
WINDOWS = sys.platform == "win32"
PEN = "🖊" if WINDOWS else "✎"
MIC_ICON = "🎙" if WINDOWS else "◉"
FILE_ICON = "🎞" if WINDOWS else "▤"
LOCK = "🔒 " if WINDOWS else ""

BG = "#ffffff"
SURFACE = "#f4f4f2"
HOVER = "#eef3fb"
BORDER = "#dcdbd5"
BORDER_STRONG = "#b9b8b1"
TEXT = "#1f1f1d"
TEXT2 = "#5f5e5a"
MUTED = "#8f8e88"
ACCENT = "#2563c9"
SUCCESS = "#2e7d4f"
DANGER = "#d64545"

MEDIA_TYPES = ("*.mp3 *.wav *.m4a *.flac *.ogg *.opus *.aac *.wma "
               "*.mp4 *.mkv *.mov *.avi *.webm *.m4v *.wmv")
SEPARATOR = "────"

FONTS = {}


class Tooltip:
    """A small pop-up that appears while the mouse is over a widget."""

    def __init__(self, widget, title, text):
        self.widget, self.title, self.text, self.tip = widget, title, text, None
        widget.bind("<Enter>", self.show, add="+")
        widget.bind("<Leave>", self.hide, add="+")

    def show(self, _event=None):
        if self.tip is not None:
            return
        text = self.text() if callable(self.text) else self.text
        tip = tk.Toplevel(self.widget)
        tip.wm_overrideredirect(True)
        try:
            tip.attributes("-topmost", True)
        except tk.TclError:
            pass
        frame = tk.Frame(tip, bg=BG, highlightthickness=1,
                         highlightbackground=BORDER_STRONG, padx=10, pady=7)
        tk.Label(frame, text=self.title, font=FONTS["small_bold"], fg=TEXT2, bg=BG,
                 anchor="w").pack(fill="x")
        tk.Label(frame, text=text, font=FONTS["small"], fg=TEXT, bg=BG, wraplength=260,
                 justify="left", anchor="w").pack(fill="x")
        frame.pack()
        tip.update_idletasks()
        x = self.widget.winfo_rootx() + self.widget.winfo_width() // 2 - tip.winfo_reqwidth() // 2
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        tip.geometry(f"+{max(0, x)}+{y}")
        self.tip = tip

    def hide(self, _event=None):
        if self.tip is not None:
            self.tip.destroy()
            self.tip = None


class App:
    def __init__(self, root):
        self.root = root
        root.title("diemut")
        root.configure(bg=BG)
        self._set_icon()
        self._setup_style()

        self.ui_q = queue.Queue()
        self.session_id = 0
        self.stt = transcribe.SpeechToText()
        self.stop_event = threading.Event()
        self.pause_event = threading.Event()

        self.model_name = transcribe.DEFAULT_MODEL
        self.language = AUTO
        self.mode = None            # "live" or "file"
        self.live_state = "idle"    # idle, loading, recording, paused, stopping, stopped
        self.busy = False
        self.stage = None
        self._playing = False
        self._play_job = None
        self._spk_map = {}
        self.reset_transcript()

        self.container = tk.Frame(root, bg=BG)
        self.container.pack(fill="both", expand=True)
        self.start_screen = self._build_start_screen()
        self.transcript_screen = self._build_transcript_screen()
        self.show_start()
        self._fit_window()

        root.after(100, self._poll)
        root.after(500, self._tick)
        root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _set_icon(self):
        """Window and taskbar icon: the scribe in diemut/assets."""
        try:
            from importlib.resources import files
            assets = files("diemut") / "assets"
            if WINDOWS:
                self.root.iconbitmap(default=str(assets / "icon.ico"))
            self._icons = [tk.PhotoImage(file=str(assets / f"icon_{n}.png"))
                           for n in (256, 48, 32, 16)]
            self.root.iconphoto(True, *self._icons)
        except Exception:
            pass   # a missing icon should never stop diemut from starting

    # ------------------------------------------------------------ style --
    def _setup_style(self):
        family = tkfont.nametofont("TkDefaultFont").actual("family")
        if WINDOWS:
            mono = "Consolas"
        elif sys.platform == "darwin":
            mono = "Menlo"
        else:
            mono = tkfont.nametofont("TkFixedFont").actual("family")

        def font(size, weight="normal", fam=family):
            return tkfont.Font(family=fam, size=size, weight=weight)

        FONTS.update(
            body=font(10), label=font(10), small=font(9), small_bold=font(9, "bold"),
            dropdown=font(9), title=font(20, "bold"), card_title=font(12, "bold"),
            icon=font(22), mono=font(9, fam=mono), tag=font(9, fam=mono),
            text=font(11), speaker=font(11, "bold"), header=font(10, "bold"),
        )

        style = ttk.Style()
        style.theme_use("clam")
        style.configure(".", background=BG, foreground=TEXT, font=FONTS["body"])
        style.configure("TButton", background=SURFACE, foreground=TEXT, padding=(10, 3), width=0,
                        bordercolor=BORDER_STRONG, lightcolor=SURFACE, darkcolor=SURFACE,
                        focuscolor=SURFACE)
        style.map("TButton",
                  background=[("disabled", BG), ("pressed", BORDER), ("active", HOVER)],
                  foreground=[("disabled", MUTED)],
                  bordercolor=[("disabled", BORDER)],
                  lightcolor=[("active", HOVER)], darkcolor=[("active", HOVER)])
        style.configure("TCheckbutton", background=BG, foreground=TEXT2, focuscolor=BG,
                        indicatorbackground=BG, indicatorforeground=ACCENT)
        style.map("TCheckbutton", background=[("active", BG)])
        style.configure("Subtle.TCombobox", fieldbackground=BG, background=BG,
                        foreground=MUTED, bordercolor=BORDER, lightcolor=BG, darkcolor=BG,
                        arrowcolor=MUTED, padding=(6, 2))
        style.map("Subtle.TCombobox",
                  fieldbackground=[("readonly", "focus", BG), ("readonly", BG)],
                  foreground=[("readonly", "focus", MUTED), ("readonly", MUTED)],
                  selectbackground=[("readonly", "focus", BG), ("readonly", BG)],
                  selectforeground=[("readonly", "focus", MUTED), ("readonly", MUTED)],
                  background=[("readonly", "focus", BG), ("readonly", BG), ("active", BG)],
                  bordercolor=[("focus", BORDER_STRONG), ("hover", BORDER_STRONG)])
        style.configure("Horizontal.TProgressbar", background=ACCENT, troughcolor=SURFACE,
                        bordercolor=BORDER, lightcolor=ACCENT, darkcolor=ACCENT)
        style.configure("Vertical.TScrollbar", background=SURFACE, troughcolor=BG,
                        bordercolor=BG, arrowcolor=MUTED, lightcolor=SURFACE, darkcolor=SURFACE)
        # The list that opens under a dropdown
        self.root.option_add("*TCombobox*Listbox.background", BG)
        self.root.option_add("*TCombobox*Listbox.foreground", TEXT)
        self.root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
        self.root.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")
        self.root.option_add("*TCombobox*Listbox.font", FONTS["dropdown"])

    def _tag(self, parent, text, font=None):
        return tk.Label(parent, text=text, font=font or FONTS["tag"], bg=SURFACE, fg=TEXT,
                        padx=6, pady=1, highlightthickness=1, highlightbackground=BORDER_STRONG)

    def _fit_window(self):
        """Size the window for the screen's display scaling (e.g. 150% on Windows)."""
        self.root.update_idletasks()
        scale = max(1.0, self.root.winfo_fpixels("1i") / 96.0)
        pad = int(90 * scale)
        width = max(int(780 * scale), self._start_inner.winfo_reqwidth() + pad)
        height = max(int(580 * scale), self._start_inner.winfo_reqheight() + pad)
        screen_w, screen_h = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        width, height = min(width, screen_w - 40), min(height, screen_h - 80)
        x, y = (screen_w - width) // 2, max(0, (screen_h - height) // 3)
        self.root.geometry(f"{width}x{height}+{x}+{y}")
        self.root.minsize(min(int(640 * scale), width), min(int(480 * scale), height))

    # ------------------------------------------------------ start screen --
    def _build_start_screen(self):
        screen = tk.Frame(self.container, bg=BG)
        inner = tk.Frame(screen, bg=BG)
        inner.place(relx=0.5, rely=0.46, anchor="center")
        self._start_inner = inner

        tk.Label(inner, text=f"Transcribe {PEN}", font=FONTS["title"], bg=BG, fg=TEXT).pack(pady=(0, 8))

        tags = tk.Frame(inner, bg=BG)
        tags.pack(pady=(0, 18))
        self.model_tag = self._tag(tags, f"whisper-{self.model_name}")
        self.model_tag.pack(side="left")
        Tooltip(self.model_tag, "Speech-to-text",
                lambda: f"OpenAI Whisper {self.model_name}, run locally with faster-whisper. "
                        f"{transcribe.MODEL_DESCRIPTIONS[self.model_name]}")
        tk.Label(tags, text="+", fg=MUTED, bg=BG, font=FONTS["body"]).pack(side="left", padx=5)
        tag = self._tag(tags, "sherpa-onnx")
        tag.pack(side="left")
        Tooltip(tag, "Speaker diarization",
                "Labels who is speaking (Speaker 1, Speaker 2…). Files only.")
        tk.Label(tags, text="+", fg=MUTED, bg=BG, font=FONTS["body"]).pack(side="left", padx=5)
        tag = self._tag(tags, "pause grouping")
        tag.pack(side="left")
        Tooltip(tag, "Paragraph breaks",
                f"Starts a new paragraph after a pause longer than {speakers.PARAGRAPH_PAUSE:g} s.")
        tk.Label(tags, text="•", fg=MUTED, bg=BG, font=FONTS["body"]).pack(side="left", padx=8)
        tk.Label(tags, text=f"{LOCK}Fully local", fg=SUCCESS, bg=BG,
                 font=FONTS["body"]).pack(side="left")

        cards = tk.Frame(inner, bg=BG)
        cards.pack(fill="x")
        cards.columnconfigure((0, 1), weight=1, uniform="cards")
        self._card(cards, MIC_ICON, "Live from microphone", "Start, pause, and stop as you go",
                   self.open_live).grid(row=0, column=0, padx=(0, 6), sticky="nsew")
        self._card(cards, FILE_ICON, "Audio or video file", "Timestamps and speaker labels",
                   self.open_file).grid(row=0, column=1, padx=(6, 0), sticky="nsew")

        form = tk.Frame(inner, bg=BG)
        form.pack(pady=(14, 0))
        tk.Label(form, text="STT model", font=FONTS["label"], fg=TEXT2, bg=BG).grid(
            row=0, column=0, sticky="e", padx=(0, 10), pady=3)
        self.model_labels = {self._model_label(n): n for n, _, _ in transcribe.MODELS}
        self.model_box = ttk.Combobox(form, style="Subtle.TCombobox", state="readonly",
                                      font=FONTS["dropdown"], width=40,
                                      values=list(self.model_labels))
        self.model_box.set(self._model_label(self.model_name))
        self.model_box.grid(row=0, column=1, sticky="w", pady=3)
        self.model_box.bind("<<ComboboxSelected>>", self._on_model)

        tk.Label(form, text="Language", font=FONTS["label"], fg=TEXT2, bg=BG).grid(
            row=1, column=0, sticky="e", padx=(0, 10), pady=3)
        values = ["Auto-detect", "Multiple languages", f"{SEPARATOR} Best supported {SEPARATOR}"]
        values += [f"{LANGUAGES[c]} ({c})" for c in BEST_SUPPORTED]
        values += [f"{SEPARATOR} All languages ({len(LANGUAGES)}) {SEPARATOR}"]
        values += [f"{name} ({code})" for code, name in
                   sorted(LANGUAGES.items(), key=lambda kv: kv[1])]
        self.lang_labels = {v: v[v.rfind("(") + 1:-1] for v in values if v.endswith(")")}
        self.lang_labels.update({"Auto-detect": AUTO, "Multiple languages": MULTI})
        self.lang_box = ttk.Combobox(form, style="Subtle.TCombobox", state="readonly",
                                     font=FONTS["dropdown"], width=40, height=18, values=values)
        self.lang_box.set("Auto-detect")
        self._lang_label = "Auto-detect"
        self.lang_box.grid(row=1, column=1, sticky="w", pady=3)
        self.lang_box.bind("<<ComboboxSelected>>", self._on_language)

        self.note = tk.Label(inner, text="", font=FONTS["small"], fg=MUTED, bg=BG,
                             wraplength=460, justify="center")
        return screen

    def _model_label(self, name):
        size = transcribe.model_size(name)
        note = next(n for m, _, n in transcribe.MODELS if m == name)
        return f"faster-whisper · {name} ({size}{', ' + note if note else ''})"

    def _card(self, parent, icon, title, subtitle, command):
        card = tk.Frame(parent, bg=BG, highlightthickness=1, highlightbackground=BORDER_STRONG,
                        padx=24, pady=16, cursor="hand2")
        widgets = [card]
        for text, font, color in ((icon, FONTS["icon"], ACCENT), (title, FONTS["card_title"], TEXT),
                                  (subtitle, FONTS["body"], TEXT2)):
            label = tk.Label(card, text=text, font=font, fg=color, bg=BG, cursor="hand2")
            label.pack(pady=1)
            widgets.append(label)

        def paint(color):
            for w in widgets:
                w.configure(bg=color)

        def leave(_event):
            x, y = card.winfo_pointerxy()
            if card.winfo_containing(x, y) not in widgets:
                paint(BG)

        for w in widgets:
            w.bind("<Enter>", lambda e: paint(SURFACE))
            w.bind("<Leave>", leave)
            w.bind("<Button-1>", lambda e: (paint(BG), command()))
        return card

    def _on_model(self, _event=None):
        self.model_name = self.model_labels[self.model_box.get()]
        self.model_tag.configure(text=f"whisper-{self.model_name}")
        self.model_box.selection_clear()
        self._update_note()

    def _on_language(self, _event=None):
        label = self.lang_box.get()
        if label.startswith(SEPARATOR):
            self.lang_box.set(self._lang_label)
        else:
            self._lang_label = label
            self.language = self.lang_labels[label]
        self.lang_box.selection_clear()
        self._update_note()

    def _update_note(self):
        notes = []
        if self.model_name in transcribe.SLOW_MODELS:
            notes.append("Slower models may fall behind in live mode."
                         + (" Best on a strong CPU or a GPU." if "large" in self.model_name else ""))
        if self.language == MULTI:
            notes.append("Multiple languages: re-detects the language every ~30 s of audio. "
                         "Switches mid-sentence aren't handled well.")
        if notes:
            self.note.configure(text=" ".join(notes))
            self.note.pack(pady=(8, 0))
        else:
            self.note.pack_forget()

    # ------------------------------------------------- transcript screen --
    def _build_transcript_screen(self):
        screen = tk.Frame(self.container, bg=BG)

        top = tk.Frame(screen, bg=BG)
        top.pack(fill="x", padx=14, pady=(12, 4))
        ttk.Button(top, text="← Home", command=self.go_home).pack(side="left")
        self.dot = tk.Canvas(top, width=10, height=10, bg=BG, highlightthickness=0)
        self.dot_id = self.dot.create_oval(1, 1, 9, 9, fill=DANGER, outline="")
        self.title_lbl = tk.Label(top, text="", font=FONTS["header"], fg=TEXT, bg=BG)
        self.sub_lbl = tk.Label(top, text="", font=FONTS["mono"], fg=TEXT2, bg=BG)
        self.header_tags = tk.Frame(top, bg=BG)
        self.header_tags.pack(side="right")

        controls = tk.Frame(screen, bg=BG)
        controls.pack(fill="x", padx=14, pady=4)
        self.controls_left = tk.Frame(controls, bg=BG)
        self.controls_left.pack(side="left")
        self.start_btn = ttk.Button(self.controls_left, text="Start", command=self.start_live)
        self.pause_btn = ttk.Button(self.controls_left, text="Pause", command=self.toggle_pause)
        self.stop_btn = ttk.Button(self.controls_left, text="Stop", command=self.stop_live)
        self.ontop_var = tk.BooleanVar(value=False)
        self.ontop_chk = ttk.Checkbutton(self.controls_left, text="Always on top",
                                         variable=self.ontop_var, command=self._toggle_on_top)
        self.cancel_btn = ttk.Button(self.controls_left, text="Cancel", command=self.cancel_file)
        self.newfile_btn = ttk.Button(self.controls_left, text="New file…", command=self.open_file)

        for ext in ("docx", "vtt", "txt"):
            ttk.Button(controls, text=f".{ext}",
                       command=lambda e=ext: self.export(e)).pack(side="right", padx=(4, 0))
        tk.Label(controls, text="Export:", fg=TEXT2, bg=BG, font=FONTS["body"]).pack(
            side="right", padx=(10, 2))
        self.ts_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(controls, text="Timestamps", variable=self.ts_var).pack(side="right")

        bottom = tk.Frame(screen, bg=BG)
        bottom.pack(side="bottom", fill="x", padx=14, pady=(4, 12))
        body = tk.Frame(screen, bg=BG, highlightthickness=1, highlightbackground=BORDER_STRONG)
        body.pack(fill="both", expand=True, padx=14, pady=4)
        self.body = body
        self.text = tk.Text(body, wrap="word", font=FONTS["text"], bg=BG, fg=TEXT, relief="flat",
                            padx=14, pady=10, highlightthickness=0, cursor="arrow",
                            state="disabled", insertwidth=0, spacing1=1, spacing3=1, height=8)
        scroll = ttk.Scrollbar(body, command=self.text.yview)
        self.text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.text.pack(side="left", fill="both", expand=True)
        self.text.tag_configure("ts", font=FONTS["mono"], foreground=MUTED)
        self.text.tag_configure("speaker", font=FONTS["speaker"], foreground=TEXT)
        self.text.tag_configure("pencil", font=FONTS["small"], foreground=MUTED)
        self.text.tag_configure("para", spacing3=8)
        self.text.tag_configure("head", spacing1=10)
        self.text.tag_configure("pending", foreground=MUTED)
        self.text.tag_configure("placeholder", foreground=MUTED)
        self.text.tag_configure("selected", background=HOVER)
        self.text.bind("<Button-1>", self._on_text_click)
        self.editor = self._build_editor(screen)

        self.clear_btn = ttk.Button(bottom, text="Clear", command=self.clear)
        self.clear_btn.pack(side="right")
        self.progress = ttk.Progressbar(bottom, length=160, mode="determinate")
        self.status_lbl = tk.Label(bottom, text="", fg=TEXT2, bg=BG, font=FONTS["body"],
                                   anchor="w")
        self.status_lbl.pack(side="left", fill="x", expand=True)
        return screen

    # ------------------------------------------------------- navigation --
    def show_start(self):
        self.transcript_screen.pack_forget()
        self.start_screen.pack(fill="both", expand=True)

    def _show_transcript(self, mode):
        self.mode = mode
        self.start_screen.pack_forget()
        self.transcript_screen.pack(fill="both", expand=True)
        for w in (self.dot, self.title_lbl, self.sub_lbl):
            w.pack_forget()
        if mode == "live":
            self.dot.pack(side="left", padx=(12, 6))
        self.title_lbl.pack(side="left", padx=(0 if mode == "live" else 12, 0))
        self.sub_lbl.pack(side="left", padx=8)
        self._update_header_tags()

    def _confirm_discard(self, action="continue"):
        if not self.dirty or not self.pieces:
            return True
        return messagebox.askyesno(
            "Transcript not exported",
            f"You haven't exported this transcript yet. If you {action}, it will be lost.\n\n"
            f"{action.capitalize()} anyway?")

    def go_home(self):
        if self.sel is not None:
            self._commit_open_edit()
        if self.busy:
            if not messagebox.askyesno("Stop and go back?",
                                       "This will stop the current transcription. Go back anyway?"):
                return
            self._abandon_session()
        elif not self._confirm_discard("go back"):
            return
        self.close_editor(save=False)
        self.root.attributes("-topmost", False)
        self.ontop_var.set(False)
        self.show_start()

    def _abandon_session(self):
        self.stop_event.set()
        self.session_id += 1      # ignore anything the old worker still sends
        self.busy = False
        self.progress.pack_forget()

    def _on_close(self):
        self._commit_open_edit()
        if self.busy and not messagebox.askyesno("Quit diemut?",
                                                 "A transcription is still running. Quit anyway?"):
            return
        if not self.busy and not self._confirm_discard("quit"):
            return
        self.stop_event.set()
        self.root.destroy()

    def _new_session(self):
        self.session_id += 1
        self.stop_event = threading.Event()
        self.pause_event = threading.Event()
        sid = self.session_id
        return lambda kind, payload: self.ui_q.put((sid, kind, payload))

    def reset_transcript(self):
        self.pieces = []
        self.names = {}
        self.speaker_count = 0
        self.dirty = False
        self.detected_language = None
        self.duration = None
        self.source_path = None
        self.session_start = dt.datetime.now()
        self.live_t0 = None
        self.live_elapsed = 0.0
        self._r = {"prev": None, "par_len": 0}
        self.audio = None
        self._pars = []
        if hasattr(self, "editor"):
            self.close_editor(save=False)
        self.sel = None

    # ------------------------------------------------------------- live --
    def open_live(self):
        self.reset_transcript()
        self.live_state = "idle"
        self._show_transcript("live")
        self.title_lbl.configure(text="Ready")
        self.sub_lbl.configure(text="00:00:00")
        self._render_all("Press Start, then speak. Your words will appear here.")
        self.status_lbl.configure(text="Press Start when you're ready.")
        self._update_controls()

    def start_live(self):
        try:
            import sounddevice  # noqa: F401
        except Exception:
            messagebox.showerror(
                "Microphone unavailable",
                "diemut couldn't load its microphone library (sounddevice).\n\n"
                "Try reinstalling diemut with:  pip install --force-reinstall .")
            return
        if not self._confirm_discard("start a new recording"):
            return
        self.reset_transcript()
        self._render_all("")
        emit = self._new_session()
        self.busy = True
        self.live_state = "loading"
        self.title_lbl.configure(text="Starting…")
        self.sub_lbl.configure(text="00:00:00")
        self._update_controls()
        self._update_header_tags()
        threading.Thread(target=transcribe.live_worker, daemon=True,
                         args=(self.stt, self.model_name, self.language, emit,
                               self.stop_event, self.pause_event)).start()

    def toggle_pause(self):
        if self.live_state == "recording":
            self.pause_event.set()
            self.live_state = "paused"
            self.status_lbl.configure(text="Paused. Timestamps keep counting until you resume.")
        elif self.live_state == "paused":
            self.pause_event.clear()
            self.live_state = "recording"
            self.status_lbl.configure(text="Listening… speak into your microphone.")
        self._update_controls()

    def stop_live(self):
        self.pause_event.clear()
        self.stop_event.set()
        self.live_state = "stopping"
        self.status_lbl.configure(text="Finishing the last few words…")
        self._update_controls()

    def _toggle_on_top(self):
        self.root.attributes("-topmost", self.ontop_var.get())

    # ------------------------------------------------------------- file --
    def open_file(self):
        path = filedialog.askopenfilename(
            title="Choose an audio or video file",
            filetypes=[("Audio or video", MEDIA_TYPES), ("All files", "*.*")])
        if not path:
            return
        if self.mode == "file" and self.transcript_screen.winfo_ismapped() \
                and not self._confirm_discard("open a new file"):
            return
        self.reset_transcript()
        self.source_path = path
        self._show_transcript("file")
        self.title_lbl.configure(text=f"{FILE_ICON} {os.path.basename(path)}")
        self.sub_lbl.configure(text="")
        self._render_all("Your transcript will appear here as diemut works through the file.")
        emit = self._new_session()
        self.busy = True
        self.stage = None
        self.progress.configure(value=0)
        self.progress.pack(side="right", padx=(0, 10))
        self._update_controls()
        threading.Thread(target=transcribe.file_worker, daemon=True,
                         args=(self.stt, path, self.model_name, self.language, emit,
                               self.stop_event)).start()

    def cancel_file(self):
        self.stop_event.set()
        self.cancel_btn.configure(state="disabled")
        self.status_lbl.configure(text="Cancelling…")

    # --------------------------------------------------------- controls --
    def _update_controls(self):
        for w in self.controls_left.winfo_children():
            w.pack_forget()
        if self.mode == "live":
            s = self.live_state
            if s in ("idle", "stopped", "loading"):
                self.start_btn.pack(side="left")
                self.start_btn.configure(state="disabled" if s == "loading" else "normal")
            else:
                self.pause_btn.configure(text="Resume" if s == "paused" else "Pause",
                                         state="disabled" if s == "stopping" else "normal")
                self.stop_btn.configure(state="disabled" if s == "stopping" else "normal")
                self.pause_btn.pack(side="left")
                self.stop_btn.pack(side="left", padx=(6, 0))
            self.ontop_chk.pack(side="left", padx=(14, 0))
            colors = {"recording": DANGER, "paused": MUTED}
            self.dot.itemconfigure(self.dot_id, fill=colors.get(s, BORDER_STRONG))
            titles = {"idle": "Ready", "loading": "Starting…", "recording": "Recording",
                      "paused": "Paused", "stopping": "Stopping…", "stopped": "Stopped"}
            self.title_lbl.configure(text=titles[s])
        else:
            if self.busy:
                self.cancel_btn.configure(state="normal")
                self.cancel_btn.pack(side="left")
            else:
                self.newfile_btn.pack(side="left")
        self.clear_btn.configure(state="disabled" if self.busy else "normal")

    def _update_header_tags(self):
        for w in self.header_tags.winfo_children():
            w.destroy()
        if self.language == AUTO:
            lang = (f"{language_name(self.detected_language)} (auto)"
                    if self.detected_language else "Auto-detect")
        else:
            lang = language_name(self.language)
        tags = [f"whisper-{self.model_name}", lang]
        if self.mode == "file" and self.speaker_count:
            tags.append(f"{self.speaker_count} speaker{'s' if self.speaker_count > 1 else ''}")
        for t in tags:
            self._tag(self.header_tags, t).pack(side="left", padx=(6, 0))

    def _tick(self):
        if self.mode == "live" and self.live_t0 is not None and self.live_state in (
                "recording", "paused", "stopping"):
            self.live_elapsed = time.monotonic() - self.live_t0
            self.sub_lbl.configure(text=fmt_time(self.live_elapsed))
        self.root.after(500, self._tick)

    # -------------------------------------------------------- rendering --
    def _render_all(self, placeholder=None):
        t = self.text
        t.configure(state="normal")
        t.delete("1.0", "end")
        self._r = {"prev": None, "par_len": 0}
        self._pars = []
        if not self.pieces:
            if placeholder:
                t.insert("end", placeholder, "placeholder")
            t.configure(state="disabled", cursor=self._text_cursor())
            return
        first_line = True
        use_speakers = self.speaker_count > 1
        for block in speakers.group_blocks(self.pieces, use_speakers):
            if block["speaker"] is not None:
                spk = block["speaker"]
                if not first_line:
                    t.insert("end-1c", "\n")
                link = f"spk:{spk}"
                t.insert("end-1c", self._speaker_name(spk), ("speaker", link, "head"))
                t.insert("end-1c", " ✎", ("pencil", link, "head"))
                t.insert("end-1c", f"   {fmt_time(block['start'])}", ("ts", "head"))
                self._bind_speaker(link, spk)
                first_line = False
                for par in block["paragraphs"]:
                    k = len(self._pars)
                    self._pars.append(dict(par, speaker=spk))
                    t.insert("end-1c", "\n", "para")
                    t.insert("end-1c", par["text"], ("para", f"par:{k}"))
            else:
                for par in block["paragraphs"]:
                    k = len(self._pars)
                    self._pars.append(dict(par, speaker=None))
                    if not first_line:
                        t.insert("end-1c", "\n")
                    t.insert("end-1c", fmt_time(par["start"]), ("ts", "para", f"par:{k}"))
                    t.insert("end-1c", "  " + par["text"], ("para", f"par:{k}"))
                    first_line = False
        if not use_speakers and self.pieces:
            self._r = {"prev": self.pieces[-1],
                       "par_len": len(t.get("end-1c linestart", "end-1c"))}
        t.configure(state="disabled", cursor=self._text_cursor())

    def _append_piece(self, piece):
        first = not self.pieces
        self.pieces.append(piece)
        self.dirty = True
        t = self.text
        at_bottom = t.yview()[1] > 0.98
        t.configure(state="normal")
        if first:
            t.delete("1.0", "end")
        self._clear_pending()
        r = self._r
        if speakers.starts_new_paragraph(r["prev"], piece, r["par_len"]):
            if r["prev"] is not None:
                t.insert("end-1c", "\n")
            t.insert("end-1c", fmt_time(piece["start"]), ("ts", "para"))
            text = "  " + piece["text"].lstrip()
            r["par_len"] = 0
        else:
            text = piece["text"]
        t.insert("end-1c", text, "para")
        r["par_len"] += len(text)
        r["prev"] = piece
        t.configure(state="disabled")
        if at_bottom:
            t.see("end")

    def _set_pending(self, on):
        t = self.text
        t.configure(state="normal")
        self._clear_pending()
        if on and self.pieces:
            t.insert("end-1c", " …", "pending")
        t.configure(state="disabled")

    def _clear_pending(self):
        ranges = self.text.tag_ranges("pending")
        for i in range(len(ranges) - 2, -1, -2):
            self.text.delete(ranges[i], ranges[i + 1])

    def _speaker_name(self, spk):
        return self.names.get(spk, f"Speaker {spk + 1}")

    def _bind_speaker(self, tag, spk):
        self.text.tag_bind(tag, "<Button-1>", lambda e: self._rename_speaker(spk))
        self.text.tag_bind(tag, "<Enter>", lambda e: self.text.configure(cursor="hand2"))
        self.text.tag_bind(tag, "<Leave>", lambda e: self.text.configure(cursor=self._text_cursor()))

    def _rename_speaker(self, spk):
        name = simpledialog.askstring("Rename speaker",
                                      f"New name for {self._speaker_name(spk)}:",
                                      initialvalue=self._speaker_name(spk), parent=self.root)
        if name and name.strip():
            self.names[spk] = name.strip()
            self.dirty = True
            self._render_all()

    # ------------------------------------------------- worker messages --
    def _poll(self):
        try:
            while True:
                sid, kind, payload = self.ui_q.get_nowait()
                if sid == self.session_id:
                    self._handle(kind, payload)
        except queue.Empty:
            pass
        self.root.after(80, self._poll)

    def _handle(self, kind, payload):
        if kind == "status":
            self.status_lbl.configure(text=payload)
        elif kind == "stage":
            self.stage = payload
            self.progress.configure(value=0)
        elif kind == "progress":
            self.progress.configure(value=payload)
            label = "Transcribing…" if self.stage == "transcribing" else "Finding speakers…"
            self.status_lbl.configure(text=f"{label} {payload:.0f}%")
        elif kind == "duration":
            self.duration = payload
            self.sub_lbl.configure(text=fmt_time(payload))
        elif kind == "language":
            self.detected_language = payload
            self._update_header_tags()
        elif kind == "piece":
            self._append_piece(payload)
        elif kind == "audio":
            self.audio = payload
        elif kind == "pending":
            self._set_pending(payload)
        elif kind == "started":
            self.live_t0 = time.monotonic()
            self.live_state = "recording"
            self._update_controls()
        elif kind == "reset":
            self.pieces = []
            self._render_all("")
        elif kind == "speakers":
            ids, count = payload
            for piece, spk in zip(self.pieces, ids):
                piece["speaker"] = spk
            self.speaker_count = count
            self.names = {}
            self._render_all()
            self._update_header_tags()
        elif kind in ("done", "error"):
            self.busy = False
            self.progress.pack_forget()
            self._set_pending(False)
            if self.mode == "live":
                self.live_state = "stopped" if self.live_t0 else "idle"
            self._update_controls()
            if self.pieces:
                self._render_all()
            if kind == "done":
                self.status_lbl.configure(text=payload)
            else:
                self.status_lbl.configure(text="Something went wrong.")
                messagebox.showerror("diemut", self._friendly_error(payload))

    def _friendly_error(self, message):
        low = message.lower()
        if "portaudio" in low or "input device" in low or "-9996" in low or "-9999" in low:
            hint = ("diemut couldn't open a microphone. Check that one is plugged in, and on "
                    "Windows turn on Settings → Privacy & security → Microphone → "
                    "“Let desktop apps access your microphone”.")
        elif "connection" in low or "resolve" in low or "timed out" in low or "urlopen" in low:
            hint = ("diemut couldn't download a model. Models download the first time you use "
                    "them, so check your internet connection and try again.")
        elif "invalid data" in low or "no such file" in low or "could not" in low:
            hint = "diemut couldn't read this file. Check it plays in a media player."
        else:
            hint = "Something went wrong."
        return f"{hint}\n\nDetails: {message}"

    # ----------------------------------------------- listen and correct --
    def _build_editor(self, screen):
        ed = tk.Frame(screen, bg=SURFACE, highlightthickness=1,
                      highlightbackground=BORDER_STRONG, padx=10, pady=8)
        row = tk.Frame(ed, bg=SURFACE)
        row.pack(fill="x")
        self.play_btn = ttk.Button(row, text="▶ Play", command=self.toggle_play)
        self.play_btn.pack(side="left")
        self.range_lbl = tk.Label(row, text="", font=FONTS["mono"], fg=TEXT2, bg=SURFACE)
        self.range_lbl.pack(side="left", padx=(10, 0))
        self.spk_frame = tk.Frame(row, bg=SURFACE)
        tk.Label(self.spk_frame, text="Speaker", fg=TEXT2, bg=SURFACE,
                 font=FONTS["body"]).pack(side="left", padx=(0, 6))
        self.spk_box = ttk.Combobox(self.spk_frame, style="Subtle.TCombobox", state="readonly",
                                    font=FONTS["dropdown"], width=18)
        self.spk_box.pack(side="left")
        ttk.Button(row, text="Done", command=lambda: self.close_editor(save=True)).pack(side="right")
        ttk.Button(row, text="Next ▶", command=lambda: self._step(1)).pack(side="right", padx=(0, 6))
        ttk.Button(row, text="◀ Prev", command=lambda: self._step(-1)).pack(side="right", padx=(0, 6))
        self.edit_box = tk.Text(ed, height=4, wrap="word", font=FONTS["text"], bg=BG, fg=TEXT,
                                relief="flat", highlightthickness=1, highlightbackground=BORDER,
                                highlightcolor=ACCENT, padx=8, pady=6, undo=True,
                                insertbackground=TEXT)
        self.edit_box.pack(fill="x", pady=(8, 4))
        tk.Label(ed, text="Ctrl+Space plays  ·  Ctrl+Enter saves  ·  Esc discards changes. "
                          "Moving to another paragraph saves automatically.",
                 font=FONTS["small"], fg=MUTED, bg=SURFACE, anchor="w").pack(fill="x")
        self.edit_box.bind("<Control-Return>", lambda e: self._key(self._commit_open_edit))
        self.edit_box.bind("<Escape>", lambda e: self._key(lambda: self.close_editor(save=False)))
        self.edit_box.bind("<Control-space>", lambda e: self._key(self.toggle_play))
        return ed

    @staticmethod
    def _key(action):
        action()
        return "break"

    def _text_cursor(self):
        return "hand2" if (self.pieces and not self.busy) else "arrow"

    def _on_text_click(self, event):
        if self.busy or not self.pieces:
            return
        index = self.text.index(f"@{event.x},{event.y}")
        for tag in self.text.tag_names(index):
            if tag.startswith("par:"):
                self.open_editor(int(tag[4:]))
                return

    def _par_at_piece(self, i):
        for k, par in enumerate(self._pars):
            if par["i0"] <= i < par["i1"]:
                return k
        for k, par in enumerate(self._pars):
            if par["i0"] >= i:
                return k
        return len(self._pars) - 1 if self._pars else None

    def open_editor(self, k):
        if self.sel is not None and k != self.sel and self.editor.winfo_ismapped():
            target = self._pars[k]["i0"]
            result = self._apply_edit()
            if result:
                edited_i0, delta, _ = result
                if target > edited_i0:
                    target += delta
            k = self._par_at_piece(target)
            if k is None:
                self.close_editor(save=False)
                return
        self._stop_playback()
        self.sel = k
        par = self._pars[k]
        self.text.tag_remove("selected", "1.0", "end")
        ranges = self.text.tag_ranges(f"par:{k}")
        if ranges:
            self.text.tag_add("selected", ranges[0], ranges[-1])
            self.text.see(ranges[0])
        self.range_lbl.configure(text=f"{fmt_time(par['start'])} – {fmt_time(par['end'])}")
        self.edit_box.delete("1.0", "end")
        self.edit_box.insert("1.0", par["text"])
        self.edit_box.edit_reset()
        self._fill_speakers(par["speaker"] if par["speaker"] is not None
                            else self.pieces[par["i0"]].get("speaker"))
        self.play_btn.configure(state="normal" if self.audio is not None else "disabled")
        if not self.editor.winfo_ismapped():
            self.editor.pack(side="bottom", fill="x", padx=14, pady=(4, 0), before=self.body)
        self.edit_box.focus_set()

    def _fill_speakers(self, current):
        ids = sorted({p["speaker"] for p in self.pieces if p.get("speaker") is not None})
        if self.mode != "file" or not ids:
            self._spk_map = {}
            self.spk_frame.pack_forget()
            return
        labels = [self._speaker_name(i) for i in ids] + ["New speaker"]
        self._spk_map = dict(zip(labels, ids))
        self._spk_map["New speaker"] = max(ids) + 1
        self.spk_box.configure(values=labels)
        self.spk_box.set(self._speaker_name(current if current is not None else ids[0]))
        self.spk_frame.pack(side="left", padx=(16, 0))

    def _apply_edit(self):
        """Write the editor's text and speaker into the transcript.

        Returns (first piece index, change in piece count, pieces now in the paragraph),
        or None if nothing changed.
        """
        if self.sel is None or self.sel >= len(self._pars):
            return None
        par = self._pars[self.sel]
        i0, i1 = par["i0"], par["i1"]
        old = self.pieces[i0:i1]
        new_text = " ".join(self.edit_box.get("1.0", "end-1c").split())
        chosen = self._spk_map.get(self.spk_box.get()) if self._spk_map else None
        old_spk = old[0].get("speaker")
        text_changed = new_text != par["text"]
        speaker_changed = chosen is not None and chosen != old_spk
        if not text_changed and not speaker_changed:
            return None
        if text_changed:
            # Corrected words are spread evenly over the paragraph's original time span.
            tokens = new_text.split()
            start, end = old[0]["start"], old[-1]["end"]
            step = (end - start) / max(1, len(tokens))
            new = [{"start": start + j * step, "end": start + (j + 1) * step,
                    "text": " " + tok, "speaker": old_spk} for j, tok in enumerate(tokens)]
        else:
            new = [dict(p) for p in old]
        if speaker_changed:
            for p in new:
                p["speaker"] = chosen
        self.pieces[i0:i1] = new
        ids = {p["speaker"] for p in self.pieces if p.get("speaker") is not None}
        if ids:
            self.speaker_count = len(ids)
        self.dirty = True
        self._render_all()
        self._update_header_tags()
        self.status_lbl.configure(text="Correction saved." if new else "Paragraph removed.")
        return i0, len(new) - (i1 - i0), len(new)

    def _commit_open_edit(self):
        """Save the open paragraph and keep editing it."""
        if self.sel is None:
            return
        i0 = self._pars[self.sel]["i0"]
        result = self._apply_edit()
        if result is None:
            return
        self.sel = None
        k = self._par_at_piece(i0)
        if k is None or result[2] == 0:
            self.close_editor(save=False)
        else:
            self.open_editor(k)

    def _step(self, direction):
        if self.sel is None:
            return
        i0 = self._pars[self.sel]["i0"]
        result = self._apply_edit()
        current = self._par_at_piece(i0)
        if current is None:
            self.close_editor(save=False)
            return
        removed = result is not None and result[2] == 0
        if removed:
            target = current if direction > 0 else current - 1
        else:
            target = current + direction
        target = max(0, min(len(self._pars) - 1, target))
        self.sel = None
        self.open_editor(target)

    def close_editor(self, save=True):
        if save:
            self._apply_edit()
        self._stop_playback()
        self.sel = None
        self.text.tag_remove("selected", "1.0", "end")
        self.editor.pack_forget()

    def toggle_play(self):
        if self._playing:
            self._stop_playback()
            return
        if self.sel is None or self.audio is None:
            return
        par = self._pars[self.sel]
        clip = self.audio.slice(par["start"] - 0.25, par["end"] + 0.35)
        if not len(clip):
            return
        try:
            import sounddevice as sd
            sd.play(clip, transcribe.SAMPLE_RATE)
        except Exception as exc:
            messagebox.showerror("diemut", "diemut couldn't play the audio. Check that speakers "
                                           f"or headphones are connected.\n\nDetails: {exc}")
            return
        self._playing = True
        self.play_btn.configure(text="■ Stop")
        duration_ms = int(len(clip) / transcribe.SAMPLE_RATE * 1000) + 150
        self._play_job = self.root.after(duration_ms, self._stop_playback)

    def _stop_playback(self):
        if self._play_job is not None:
            self.root.after_cancel(self._play_job)
            self._play_job = None
        if self._playing:
            try:
                import sounddevice as sd
                sd.stop()
            except Exception:
                pass
        self._playing = False
        if hasattr(self, "play_btn"):
            self.play_btn.configure(text="▶ Play")

    # ----------------------------------------------------------- export --
    def export(self, kind):
        self._commit_open_edit()
        if not self.pieces:
            messagebox.showinfo("Nothing to export", "There's no transcript yet.")
            return
        if self.source_path:
            base = os.path.splitext(os.path.basename(self.source_path))[0]
        else:
            base = f"live_{self.session_start:%Y-%m-%d_%H%M}"
        path = filedialog.asksaveasfilename(
            title=f"Export .{kind}", defaultextension=f".{kind}", initialfile=f"{base}.{kind}",
            filetypes=[(f"{kind.upper()} file", f"*.{kind}"), ("All files", "*.*")])
        if not path:
            return
        use_speakers = self.speaker_count > 1
        try:
            if kind == "txt":
                export.export_txt(path, self.pieces, self.names, use_speakers, self.ts_var.get())
            elif kind == "vtt":
                export.export_vtt(path, self.pieces, self.names, use_speakers)
            else:
                lang = (language_name(self.detected_language) + " (detected)"
                        if self.language == AUTO and self.detected_language
                        else language_name(self.language))
                meta = [f"Source: {os.path.basename(self.source_path) if self.source_path else 'Live microphone'}",
                        f"Date: {self.session_start:%Y-%m-%d %H:%M}",
                        f"STT model: whisper-{self.model_name} (faster-whisper)",
                        f"Language: {lang}"]
                if self.mode == "file" and self.speaker_count:
                    meta.append(f"Speakers: {self.speaker_count}")
                export.export_docx(path, self.pieces, self.names, use_speakers, self.ts_var.get(),
                                   f"Transcript – {base}", meta)
        except Exception as exc:
            messagebox.showerror("Export failed", f"diemut couldn't save the file.\n\n{exc}")
            return
        self.dirty = False
        self.status_lbl.configure(text=f"Saved {os.path.basename(path)}")

    def clear(self):
        if self.busy or not self.pieces:
            return
        if not messagebox.askyesno("Clear transcript?",
                                   "This removes the transcript from the window. "
                                   "Files you've already exported aren't affected."):
            return
        keep_source = self.source_path
        self.close_editor(save=False)
        self.reset_transcript()
        self.source_path = keep_source
        self._render_all("")
        self._update_header_tags()
        self.status_lbl.configure(text="Cleared.")
