"""diemut's window: the start screen, the transcript screen and the live mini view.

The correction panel is in editor.py and merging paragraphs in paragraphs.py
(both mixed into App); dialogs.py has the small windows and theme.py the look.
"""

import datetime as dt
import os
import queue
import shutil
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import (export, find, fillers, history, notes, playback, sessions, settings, speakers,
               timing, transcribe)
from .dialogs import (REPLACES_LABELS, SPEAKER_COUNTS, ask_choice, ask_speaker_count, count_label,
                      export_dialog, settings_window, FindWindow, SpeakersWindow, Tooltip)
from .editor import SEEK_BINDING, EditorMixin
from .export import fmt_time
from .languages import AUTO, BEST_SUPPORTED, LANGUAGES, MULTI, language_name
from .paragraphs import ParagraphsMixin
from .theme import (ACCENT, BG, BORDER, BORDER_STRONG, DANGER, FILE_ICON, FONTS, HOVER, LOCK, MIC_ICON,
                    MUTED, NOTE_BG, PLAYING, SETTINGS_ICON, SURFACE, TEXT, TEXT2, WINDOWS, display_scale,
                    file_cursor, setup_style)

MEDIA_TYPES = ("*.mp3 *.wav *.m4a *.flac *.ogg *.opus *.aac *.wma "
               "*.mp4 *.mkv *.mov *.avi *.webm *.m4v *.wmv")
SEPARATOR = "────"
AUTOSAVE_MS = 30_000     # how often the recovery copy is brought up to date


class App(EditorMixin, ParagraphsMixin):
    def __init__(self, root):
        self.root = root
        root.title("diemut")
        root.configure(bg=BG)
        self._set_icon()
        setup_style(root)
        self._scale = display_scale(root)

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
        self.player = playback.Player()
        self.num_speakers = None    # how many people speak in the next file, if known
        self.settings = settings.load()    # remembered between launches (settings.py)
        Tooltip.enabled = self.settings["hover_hints"]
        self._settings_win = None
        self._speakers_win = None
        self._shortcuts_win = None
        self._find_win = None
        self.history = history.History()   # undo and redo for the transcript
        self._relabel = False       # finding speakers again (so its result can be undone)
        self.mini_active = False
        self._pending = False
        self._play_job = None
        self._shown_playing = False  # what the Play / Pause button shows
        self._loop = False
        self._mode = None           # None or "merge": changed only by _set_mode
        self._merge_range = None    # (first, last) paragraphs checked in merge mode
        self._merge_anchor = None   # the paragraph clicked last, where Shift+click counts from
        self._press = None          # [paragraph, dragged?, Shift?] while the mouse is down
        self._flash_job = None      # fading a just-merged paragraph back (_flash)
        self._region = None        # (start, end) seconds selected on the audio bar or as words
        self._region_src = None    # "bar" or "text"
        self._drag = None
        self._wave_span = (0.0, 0.0)
        self._wave_cols = None     # the waveform's columns, as last drawn
        self._spans_cache = None   # ((open paragraph, edit text), word positions and times)
        self._playing_word = None  # (char start, char end) highlighted as it plays
        self._spk_map = {}
        self._recovery = None       # sessions.Recovery while there are unsaved changes
        self._clear_transcript()

        self.container = tk.Frame(root, bg=BG)
        self.container.pack(fill="both", expand=True)
        self.start_screen = self._build_start_screen()
        self.transcript_screen = self._build_transcript_screen()
        self.mini = self._build_mini()
        self.show_start()
        self._fit_window()

        root.after(100, self._poll)
        root.after(500, self._tick)
        root.after(AUTOSAVE_MS, self._autosave)
        root.after(300, self._offer_recovery)
        root.bind("<Control-s>", lambda e: self._save_key(False))
        root.bind("<Control-S>", lambda e: self._save_key(True))       # Ctrl+Shift+S
        root.bind("<Control-z>", lambda e: self._undo_key(False))
        root.bind("<Control-y>", lambda e: self._undo_key(True))
        root.bind("<Control-Z>", lambda e: self._undo_key(True))       # Ctrl+Shift+Z
        root.bind("<Control-f>", lambda e: self.open_find())
        root.bind("<Control-F>", lambda e: self.open_find())
        root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _set_icon(self):
        """Window and taskbar icon: the snail in diemut/assets."""
        try:
            from importlib.resources import files
            assets = files("diemut") / "assets"
            if WINDOWS:
                # Only the .ico on Windows (iconphoto() would replace it). Tk itself only
                # ever passes Windows the .ico's 16 and 32 px images, which Windows then
                # stretches at 125–150% scaling, so once the window exists, hand Windows
                # the sizes it actually draws at. Dialogs keep Tk's icon.
                ico = str(assets / "icon.ico")
                self.root.iconbitmap(default=ico)
                self.root.bind("<Map>", lambda e: e.widget is self.root and
                               self._set_windows_icon(ico), add="+")
                self.root.after(50, lambda: self._set_windows_icon(ico))
                return
            self._icons = [tk.PhotoImage(file=str(assets / f"icon_{n}.png"))
                           for n in (256, 48, 32, 16)]
            self.root.iconphoto(True, *self._icons)
        except Exception:
            pass   # a missing icon should never stop diemut from starting

    def _set_windows_icon(self, ico):
        """Give the window title bar and taskbar icons at the display's scaled sizes."""
        try:
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            user32.LoadImageW.restype = wintypes.HANDLE
            user32.LoadImageW.argtypes = (wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
                                          ctypes.c_int, ctypes.c_int, wintypes.UINT)
            user32.SendMessageW.restype = wintypes.LPARAM
            user32.SendMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                                            wintypes.LPARAM)
            hwnd = int(self.root.wm_frame(), 16)
            sm_cxicon, sm_cxsmicon = 11, 49
            try:   # Windows 10 and later: the sizes for this window's monitor
                dpi = user32.GetDpiForWindow(hwnd)
                big = user32.GetSystemMetricsForDpi(sm_cxicon, dpi)
                small = user32.GetSystemMetricsForDpi(sm_cxsmicon, dpi)
            except AttributeError:
                big = user32.GetSystemMetrics(sm_cxicon)
                small = user32.GetSystemMetrics(sm_cxsmicon)
            image_icon, lr_loadfromfile, wm_seticon = 1, 0x10, 0x80
            icons = []
            for which, size in ((0, small), (1, big)):   # ICON_SMALL, ICON_BIG
                hicon = user32.LoadImageW(None, ico, image_icon, size, size, lr_loadfromfile)
                if hicon:
                    user32.SendMessageW(hwnd, wm_seticon, which, hicon)
                    icons.append(hicon)
            self._hicons = icons   # keep the handles alive while the window uses them
        except Exception:
            pass   # Tk's own (blurrier) icon stays in place

    def _tag(self, parent, text, font=None):
        return tk.Label(parent, text=text, font=font or FONTS["tag"], bg=SURFACE, fg=TEXT,
                        padx=6, pady=1, highlightthickness=1, highlightbackground=BORDER_STRONG)

    def _fit_window(self):
        """Size the window for the screen's display scaling (e.g. 150% on Windows)."""
        self.root.update_idletasks()
        scale = self._scale
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

        self._logo = self._load_logo()
        if self._logo is not None:
            tk.Label(inner, image=self._logo, bg=BG).pack()
        # "diemut" in the monospace font of the model names; a Text keeps both on one baseline
        title = tk.Text(inner, height=1, bg=BG, fg=TEXT, relief="flat", highlightthickness=0,
                        borderwidth=0, padx=0, pady=0, cursor="arrow", takefocus=0,
                        font=FONTS["card_title"])
        title.tag_configure("mono", font=FONTS["card_title_mono"])
        title.tag_configure("center", justify="center")
        title.insert("1.0", "Transcribe with ", "center")
        title.insert("end-1c", "diemut", ("mono", "center"))
        title.configure(state="disabled")
        width = FONTS["card_title"].measure("Transcribe with ") + \
            FONTS["card_title_mono"].measure("diemut")
        title.configure(width=max(1, round(width / FONTS["card_title"].measure("0"))) + 1)
        title.pack(pady=(4, 10))

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
                "Labels who is speaking (Speaker 1, Speaker 2…), for files and for live "
                "recordings after Stop.")
        tk.Label(tags, text="•", fg=MUTED, bg=BG, font=FONTS["body"]).pack(side="left", padx=8)
        tk.Label(tags, text=f"{LOCK}Fully local", fg=TEXT2, bg=BG,
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

        lbl = tk.Label(form, text="Speakers", font=FONTS["label"], fg=TEXT2, bg=BG)
        lbl.grid(row=2, column=0, sticky="e", padx=(0, 10), pady=3)
        self.count_box = ttk.Combobox(form, style="Subtle.TCombobox", state="readonly",
                                      font=FONTS["dropdown"], width=40,
                                      values=[count_label(n) for n in SPEAKER_COUNTS])
        self.count_box.set(count_label(None))
        self.count_box.grid(row=2, column=1, sticky="w", pady=3)
        self.count_box.bind("<<ComboboxSelected>>", self._on_count)
        for w in (lbl, self.count_box):
            Tooltip(w, "Speakers (optional)",
                    "If you know how many people speak in the file, choose it here for more "
                    "accurate speaker labels. For live recordings, you can find speakers "
                    "after you press Stop.")

        self.note = tk.Label(inner, text="", font=FONTS["small"], fg=MUTED, bg=BG,
                             wraplength=460, justify="center")
        self.open_session_btn = ttk.Button(inner, text="Open saved session…",
                                           command=self.open_session)
        self.open_session_btn.pack(pady=(16, 0))
        self._settings_button(screen).place(relx=1, rely=1, x=-14, y=-12, anchor="se")
        return screen

    def _load_logo(self):
        """The icon at about desktop-icon size (48 px at 100% display scaling)."""
        size = min((48, 60, 72, 96), key=lambda n: abs(n - 48 * self._scale))
        try:
            from importlib.resources import files
            return tk.PhotoImage(file=str(files("diemut") / "assets" / f"icon_{size}.png"))
        except Exception:
            return None   # a missing icon should never stop diemut from starting

    def _model_label(self, name):
        size = transcribe.model_size(name)
        note = next(n for m, _, n in transcribe.MODELS if m == name)
        return f"faster-whisper • {name} ({size}{', ' + note if note else ''})"

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

    def _settings_button(self, parent):
        """⚙, at the bottom right of each screen."""
        button = ttk.Button(parent, text=SETTINGS_ICON, style="Icon.TButton",
                            command=lambda: settings_window(self))
        Tooltip(button, "Settings", "The microphone, hover hints, and what Export starts with.")
        return button

    @property
    def mic_name(self):
        """Live mode's microphone, by name (None: the system default), from Settings."""
        return self.settings["microphone"]

    def change_setting(self, key, value):
        """Apply a setting from the Settings window, and remember it for next time."""
        self.settings[key] = value
        if key == "hover_hints":
            Tooltip.enabled = value
        elif key == "leave_out_fillers":
            self.leave_out_fillers = value      # what Export starts with
        elif key == "microphone":
            self._update_header_tags()
        try:
            settings.save(self.settings)
        except OSError as exc:
            messagebox.showerror(
                "Settings not saved",
                f"diemut couldn't save your settings to {settings.PATH}. They apply until you "
                f"quit. Check that the folder can be written to.\n\nDetails: {exc}",
                parent=self._settings_win)

    def _on_count(self, _event=None):
        self.num_speakers = next(n for n in SPEAKER_COUNTS
                                 if count_label(n) == self.count_box.get())
        self.count_box.selection_clear()

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
        hints = []
        if self.model_name in transcribe.SLOW_MODELS:
            hints.append("Slower models may fall behind in live mode."
                         + (" Best on a strong CPU or a GPU." if "large" in self.model_name else ""))
        if self.language == MULTI:
            hints.append("Multiple languages: re-detects the language every ~30 s of audio. "
                         "Switches mid-sentence aren't handled well.")
        if hints:
            self.note.configure(text=" ".join(hints))
            self.note.pack(pady=(8, 0), before=self.open_session_btn)   # under the form
        else:
            self.note.pack_forget()

    # ------------------------------------------------- transcript screen --
    def _build_transcript_screen(self):
        screen = tk.Frame(self.container, bg=BG)

        top = tk.Frame(screen, bg=BG)
        top.pack(fill="x", padx=14, pady=(12, 4))
        ttk.Button(top, text="← Home", command=self.go_home).pack(side="left")
        self.mini_btn = ttk.Button(top, text="Mini view", command=self.enter_mini)
        Tooltip(self.mini_btn, "Mini view",
                "A small window that stays in front of other apps, showing the latest words, "
                "for example as captions during a video call.")
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
        self.start_btn = ttk.Button(self.controls_left, text="Start", style="Accent.TButton", 
                                    command=self.start_live)
        self.pause_btn = ttk.Button(self.controls_left, text="Pause", style="Accent.TButton", 
                                    command=self.toggle_pause)
        self.stop_btn = ttk.Button(self.controls_left, text="Stop", style="Accent.TButton", 
                                   command=self.stop_live)
        self.find_spk_btn = ttk.Button(self.controls_left, text="Find speakers…",
                                       style="Accent.TButton", command=self.find_live_speakers)
        self.cancel_btn = ttk.Button(self.controls_left, text="Cancel", style="Accent.TButton",
                                     command=self.cancel_file)
        self.newfile_btn = ttk.Button(self.controls_left, text="New file…", style="Accent.TButton", 
                                      command=self.open_file)
        self.clear_btn = ttk.Button(self.controls_left, text="Clear", command=self.clear)

        self.export_btn = ttk.Button(controls, text="Export…", style="Accent.TButton", command=lambda: export_dialog(self))
        self.export_btn.pack(side="right")
        self.save_btn = ttk.Button(controls, text="Save", style="Accent.TButton",
                                   command=self.save_session)
        self.save_btn.pack(side="right", padx=(0, 6))
        Tooltip(self.save_btn, "Save session",
                "Saves the transcript, your corrections and the audio as a .diemut file you "
                "can open again from the start screen. Ctrl+S; Ctrl+Shift+S saves under a "
                "new name.")
        # Merge, centred in the row whatever is on the left and right
        self.par_tools = tk.Frame(controls, bg=BG)
        self.par_tools.place(relx=0.5, rely=0, anchor="n")   # top-aligned, like the others
        # Merge; in merge mode, Merge N paragraphs and Cancel take its place
        self.merge_mode_btn = ttk.Button(self.par_tools, text="Merge", style="Accent.TButton",
                                         command=self.toggle_merge_mode)
        Tooltip(self.merge_mode_btn, "Merge",
                "Merges paragraphs that are next to each other and from the same speaker. "
                "Click it, then check the paragraphs.")
        self.merge_sel_btn = ttk.Button(self.par_tools, text="Merge", style="Accent.TButton",
                                        command=self.merge_selected)
        self.merge_cancel_btn = ttk.Button(self.par_tools, text="Cancel", style="Accent.TButton",
                                           command=lambda: self._set_mode(None))
        self.ts_labels = {"turns": "Each speaker turn", "paragraphs": "Each paragraph"}
        self.ts_mode = "turns"        # remembered between exports
        self.include_ts = True        # Export's Timestamps box
        self.include_notes = True     # Export's Notes box
        self.leave_out_fillers = self.settings["leave_out_fillers"]   # what Export starts with
        self.export_kind = "txt"

        bottom = tk.Frame(screen, bg=BG)
        bottom.pack(side="bottom", fill="x", padx=14, pady=(4, 12))
        # The transcript above, and the correction panel below it when it's open, with a
        # divider between them that can be dragged
        self.split = tk.PanedWindow(screen, orient="vertical", bg=BORDER, bd=0,
                                    sashwidth=max(5, int(5 * self._scale)), sashrelief="flat",
                                    sashcursor="sb_v_double_arrow", opaqueresize=True)
        self.split.pack(fill="both", expand=True, padx=14, pady=4)
        self.split.bind("<ButtonRelease-1>", self._remember_panel_height, add="+")
        body = tk.Frame(self.split, bg=BG, highlightthickness=1,
                        highlightbackground=BORDER_STRONG)
        self.split.add(body, minsize=int(90 * self._scale), stretch="always")
        self.body = body
        self.text = tk.Text(body, wrap="word", font=FONTS["text"], bg=BG, fg=TEXT, relief="flat",
                            padx=14, pady=10, highlightthickness=0, cursor="arrow",
                            state="disabled", insertwidth=0, spacing1=1, spacing3=1, height=8)
        scroll = ttk.Scrollbar(body, command=self.text.yview)
        self.text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.text.pack(side="left", fill="both", expand=True)
        self.text.tag_configure("ts", font=FONTS["mono"], foreground=MUTED)
        # Fillers and false starts, which Export can leave out (before "selected", so the open
        # paragraph's white-on-black still wins)
        self.text.tag_configure("filler", foreground=MUTED)
        # Just merged or split: shown white on black, then faded (_flash). Before "selected",
        # so the open paragraph's highlight stays put
        self.text.tag_configure("flash")
        self.text.tag_configure("speaker", font=FONTS["speaker"], foreground=TEXT)
        self.text.tag_configure("pencil", font=FONTS["small"], foreground=MUTED)
        self.text.tag_configure("para", spacing3=8)
        self.text.tag_configure("head", spacing1=10)
        self.text.tag_configure("pending", foreground=MUTED)
        self.text.tag_configure("placeholder", foreground=MUTED)
        self.text.tag_configure("selected", background=TEXT, foreground=BG)
        self.text.tag_configure("merging", background=TEXT, foreground=BG)   # checked, merging
        # Notes ([laughs]): lime, with the words in italics. Above the open paragraph's
        # highlight, so a note always looks like one
        self.text.tag_configure("note", background=NOTE_BG, foreground=TEXT)
        self.text.tag_configure("note_i", font=FONTS["text_italic"])
        # Find's matches (FindWindow), above the open paragraph's highlight so they show in it
        self.text.tag_configure("found", background=PLAYING, foreground=TEXT)
        self.text.tag_configure("found_now", background=DANGER, foreground=BG)
        self.text.tag_raise("sel")      # a selection for copying shows over all of these
        self.text.bind("<Button-1>", self._on_text_press)
        self.text.bind("<ButtonRelease-1>", self._on_text_release)
        self.text.bind("<B1-Motion>", self._on_text_drag)
        self.text.bind(SEEK_BINDING, self._on_text_seek_click)
        self.root.bind("<Escape>", lambda e: self._set_mode(None), add="+")
        self._needle = file_cursor(self.root, "needle", "hand2")
        aqua = self.root.tk.call("tk", "windowingsystem") == "aqua"
        for seq in (("<Button-2>", "<Control-Button-1>") if aqua else ("<Button-3>",)):
            self.text.bind(seq, self._on_text_menu)
        self.par_menu = self._menu(self.root)
        self.editor = self._build_editor(self.split)

        self._settings_button(bottom).pack(side="right")
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
        for w in (self.mini_btn, self.dot, self.title_lbl, self.sub_lbl):
            w.pack_forget()
        if mode == "live":
            self.mini_btn.pack(side="left", padx=(6, 0))
            self.dot.pack(side="left", padx=(12, 6))
        self.title_lbl.pack(side="left", padx=(0 if mode == "live" else 12, 0))
        self.sub_lbl.pack(side="left", padx=8)
        self._update_header_tags()

    def _confirm_discard(self, action="continue"):
        """True if the transcript can go: nothing unsaved, saved now, or Don't save."""
        if not self.dirty or not self.pieces:
            return True
        choice = ask_choice(
            self.root, "Save changes?",
            f"Save this session before you {action}? If you don't, the transcript and your "
            "corrections are lost. Files you've exported aren't affected.",
            [("save", "Save"), ("discard", "Don't save"), ("cancel", "Cancel")])
        if choice == "save":
            return self.save_session()
        if choice == "discard":
            self.dirty = False
            self._drop_recovery()
            return True
        return False

    def go_home(self):
        self._save_edit()
        if self.busy:
            if not messagebox.askyesno("Stop and go back?",
                                       "This will stop the current transcription. Go back anyway?"):
                return
            self._abandon_session()
        elif not self._confirm_discard("go back"):
            return
        self.close_editor(save=False)
        self.show_start()

    def _abandon_session(self):
        self.stop_event.set()
        self.session_id += 1      # ignore anything the old worker still sends
        self.busy = False
        self.progress.pack_forget()

    def _on_close(self):
        self._save_edit()
        if self.busy and not messagebox.askyesno("Quit diemut?",
                                                 "A transcription is still running. Quit anyway?"):
            return
        if not self.busy and not self._confirm_discard("quit"):
            return
        self.stop_event.set()
        self.player.close()
        if self._speakers_win is not None:
            self._speakers_win.close()
        self.root.destroy()

    def _new_session(self):
        self.session_id += 1
        self.stop_event = threading.Event()
        self.pause_event = threading.Event()
        sid = self.session_id
        return lambda kind, payload: self.ui_q.put((sid, kind, payload))

    def reset_transcript(self):
        """Close the panel and windows, and forget the transcript."""
        self._set_mode(None)
        self.close_editor(save=False)
        for win in (self._speakers_win, self._find_win):
            if win is not None:
                win.close()
        self._clear_transcript()

    def _clear_transcript(self):
        self._drop_recovery()
        self.session_path = None    # the .diemut file this was opened from or saved to
        self.pieces = []
        self.names = {}
        self.speaker_count = 0
        self.dirty = False
        # The model and language this transcript was made with (the start screen's choice
        # is for the next one)
        self.session_model = self.model_name
        self.session_language = self.language
        self.detected_language = None
        self.duration = None
        self.source_path = None
        self.session_start = dt.datetime.now()
        self.live_t0 = None
        self.audio = None
        self._pars = []
        self.history.clear()
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
        device = transcribe.find_input(self.mic_name) if self.mic_name else None
        if self.mic_name and device is None:
            if not messagebox.askyesno(
                    "Microphone not connected",
                    f"diemut can't find the microphone “{self.mic_name}”. It may have been "
                    "unplugged.\n\nRecord with the system default microphone instead?"):
                return
        self.reset_transcript()
        self._render_all("")
        emit = self._new_session()
        self.busy = True
        self.live_state = "loading"
        self.title_lbl.configure(text="Starting…")
        self.sub_lbl.configure(text="00:00:00")
        self.mini_time.configure(text="00:00:00")
        self._update_controls()
        self._update_header_tags()
        self._update_mini_text()
        threading.Thread(target=transcribe.live_worker, daemon=True,
                         args=(self.stt, self.model_name, self.language, emit,
                               self.stop_event, self.pause_event, device)).start()

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

    def _menu(self, parent, **options):
        return tk.Menu(parent, tearoff=0, bg=BG, fg=TEXT, activebackground=HOVER,
                       activeforeground=TEXT, disabledforeground=MUTED, font=FONTS["body"],
                       relief="flat", bd=1, **options)

    # -- mini view --
    def _build_mini(self):
        mini = tk.Frame(self.container, bg=BG, padx=10, pady=8)
        row = tk.Frame(mini, bg=BG)
        row.pack(fill="x")
        ttk.Button(row, text="Full view", command=self.exit_mini).pack(side="left", padx=(0, 12))
        self.mini_dot = tk.Canvas(row, width=10, height=10, bg=BG, highlightthickness=0)
        self.mini_dot_id = self.mini_dot.create_oval(1, 1, 9, 9, fill=BORDER_STRONG, outline="")
        self.mini_dot.pack(side="left", padx=(0, 6))
        self.mini_time = tk.Label(row, text="00:00:00", font=FONTS["mono"], fg=TEXT2, bg=BG)
        self.mini_time.pack(side="left")
        self.mini_controls = tk.Frame(row, bg=BG)
        self.mini_controls.pack(side="left", padx=(12, 0))
        self.mini_start = ttk.Button(self.mini_controls, text="Start", style="Accent.TButton", 
                                     command=self.start_live)
        self.mini_pause = ttk.Button(self.mini_controls, text="Pause", style="Accent.TButton", 
                                     command=self.toggle_pause)
        self.mini_stop = ttk.Button(self.mini_controls, text="Stop", style="Accent.TButton", 
                                    command=self.stop_live)
        self.mini_text = tk.Text(mini, height=2, wrap="word", font=FONTS["text"], bg=BG, fg=TEXT,
                                 relief="flat", highlightthickness=0, state="disabled",
                                 cursor="arrow", padx=2, pady=2)
        self.mini_text.tag_configure("muted", foreground=MUTED)
        self.mini_text.pack(fill="both", expand=True, pady=(6, 0))
        return mini

    def enter_mini(self):
        """Shrink to a small always-on-top window showing the latest words."""
        if self.mini_active:
            return
        self._close_editing()
        self._full_geometry = self.root.geometry()
        self._full_minsize = self.root.minsize()
        self.transcript_screen.pack_forget()
        self.mini.pack(fill="both", expand=True)
        self.mini_active = True
        self._update_mini_controls()
        self._update_mini_text()
        self.root.update_idletasks()
        scale = self._scale
        height = self.mini.winfo_reqheight()
        self.root.minsize(int(380 * scale), height)
        self.root.geometry(f"{int(560 * scale)}x{height}+{self.root.winfo_x()}+{self.root.winfo_y()}")
        self.root.attributes("-topmost", True)

    def exit_mini(self):
        if not self.mini_active:
            return
        self.mini_active = False
        self.root.attributes("-topmost", False)
        self.mini.pack_forget()
        self.transcript_screen.pack(fill="both", expand=True)
        self.root.minsize(*self._full_minsize)
        self.root.geometry(self._full_geometry)
        self.text.see("end")

    def _update_mini_controls(self):
        if self.mini_active:
            self._show_live_buttons(self.mini_controls, self.mini_start, self.mini_pause,
                                    self.mini_stop)
            self.mini_dot.itemconfigure(self.mini_dot_id, fill=self._live_dot())

    def _show_live_buttons(self, frame, start, pause, stop):
        """Start, or Pause/Resume and Stop, for the live state (main window or mini view)."""
        for w in frame.winfo_children():
            w.pack_forget()
        s = self.live_state
        if s in ("idle", "stopped", "loading"):
            start.configure(state="disabled" if s == "loading" else "normal")
            start.pack(side="left")
        else:
            pause.configure(text="Resume" if s == "paused" else "Pause",
                            state="disabled" if s == "stopping" else "normal")
            stop.configure(state="disabled" if s == "stopping" else "normal")
            pause.pack(side="left")
            stop.pack(side="left", padx=(6, 0))

    def _live_dot(self):
        return {"recording": DANGER, "paused": MUTED}.get(self.live_state, BORDER_STRONG)

    def _update_mini_text(self):
        """The newest words; the box is two lines tall, so older ones scroll out of view."""
        if not self.mini_active:
            return
        t = self.mini_text
        t.configure(state="normal")
        t.delete("1.0", "end")
        words = "".join(p["text"] for p in self.pieces[-120:]).strip()
        if words:
            t.insert("end", words)
            if self._pending:
                t.insert("end", " …", "muted")
        else:
            t.insert("end", "Press Start, then speak." if self.live_state in ("idle", "stopped")
                     else "Listening…", "muted")
        t.configure(state="disabled")
        t.after_idle(self._scroll_mini)

    def _scroll_mini(self):
        """Show the last two lines, once Tk has worked out where the text wraps."""
        self.mini_text.update_idletasks()
        self.mini_text.yview_moveto(1.0)

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
                               self.stop_event, self.num_speakers)).start()

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
            if s == "stopped" and self.busy:      # finding speakers in the recording
                self.cancel_btn.configure(state="normal")
                self.cancel_btn.pack(side="left")
            else:
                self._show_live_buttons(self.controls_left, self.start_btn, self.pause_btn,
                                        self.stop_btn)
                if s == "stopped" and self.pieces and self.audio is not None:
                    self.find_spk_btn.pack(side="left", padx=(6, 0))
            self.dot.itemconfigure(self.dot_id, fill=self._live_dot())
            self._update_mini_controls()
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
        self.save_btn.configure(state="normal" if self._can_save() else "disabled")
        self.clear_btn.pack(side="left", padx=(6, 0))
        self._update_par_tools()

    def _update_header_tags(self):
        for w in self.header_tags.winfo_children():
            w.destroy()
        if self.session_language == AUTO:
            lang = (f"{language_name(self.detected_language)} (auto)"
                    if self.detected_language else "Auto-detect")
        else:
            lang = language_name(self.session_language)
        tags = [f"whisper-{self.session_model}", lang]
        if self.speaker_count:
            tags.append(f"{self.speaker_count} speaker{'s' if self.speaker_count > 1 else ''}")
        if self.mode == "live" and self.mic_name:
            name = self.mic_name if len(self.mic_name) <= 28 else self.mic_name[:27] + "…"
            tags.insert(0, f"{MIC_ICON} {name}")
        for t in tags:
            tag = self._tag(self.header_tags, t)
            tag.pack(side="left", padx=(6, 0))
            if t.endswith(("speaker", "speakers")):
                tag.configure(cursor="hand2")
                tag.bind("<Button-1>", lambda e: self.open_speakers())

    def _tick(self):
        if self.mode == "live" and self.live_t0 is not None and self.live_state in (
                "recording", "paused", "stopping"):
            elapsed = fmt_time(time.monotonic() - self.live_t0)
            self.sub_lbl.configure(text=elapsed)
            self.mini_time.configure(text=elapsed)
        self.root.after(500, self._tick)

    # -------------------------------------------------------- rendering --
    def _render_all(self, placeholder=None, anchor=None):
        """Rebuild the transcript. The view stays still: anchor (from _view_anchor) is a
        word to keep in the same place on screen; by default, the first word showing."""
        t = self.text
        self._end_flash()
        anchor = anchor or self._view_anchor()
        t.configure(state="normal")
        t.delete("1.0", "end")
        self._r = {"prev": None, "par_len": 0}
        self._pars = []
        if not self.pieces:
            if placeholder:
                t.insert("end", placeholder, "placeholder")
            t.configure(state="disabled", cursor=self._text_cursor())
            self._update_par_tools()
            if self._find_win is not None:
                self._find_win.refresh()
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
                self._bind_speaker(link, spk)
                first_line = False
                for par in block["paragraphs"]:
                    k = len(self._pars)
                    self._pars.append(dict(par, speaker=spk))
                    t.insert("end-1c", "\n", "para")
                    t.insert("end-1c", fmt_time(par["start"]), ("ts", "para", f"par:{k}"))
                    t.insert("end-1c", "  ", ("para", f"par:{k}"))
                    t.insert("end-1c", par["text"], ("para", f"par:{k}", f"ptext:{k}"))
            else:
                for par in block["paragraphs"]:
                    k = len(self._pars)
                    self._pars.append(dict(par, speaker=None))
                    if not first_line:
                        t.insert("end-1c", "\n")
                    t.insert("end-1c", fmt_time(par["start"]), ("ts", "para", f"par:{k}"))
                    t.insert("end-1c", "  ", ("para", f"par:{k}"))
                    t.insert("end-1c", par["text"], ("para", f"par:{k}", f"ptext:{k}"))
                    first_line = False
        if not use_speakers and self.pieces:
            self._r = {"prev": self.pieces[-1],
                       "par_len": len(t.get("end-1c linestart", "end-1c"))}
        t.configure(state="disabled", cursor=self._text_cursor())
        self._restore_view(anchor)
        self._grey_fillers()
        self._mark_notes()
        self._show_open_paragraph(scroll=False)
        self._draw_merge()
        self._update_par_tools()
        if self._find_win is not None:
            self._find_win.refresh()

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
        word = text.lstrip()
        t.insert("end-1c", text[:len(text) - len(word)], "para")
        t.insert("end-1c", word, ("para", "filler") if fillers.is_filler(word) else "para")
        r["par_len"] += len(text)
        r["prev"] = piece
        t.configure(state="disabled")
        if at_bottom:
            t.see("end")
        self._update_mini_text()

    def _set_pending(self, on):
        self._pending = on
        self._update_mini_text()
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
        return export.speaker_name(self.names, spk)

    def _bind_speaker(self, tag, spk):
        self.text.tag_bind(tag, "<Button-1>", lambda e: self.open_speakers(spk))
        self.text.tag_bind(tag, "<Enter>", lambda e: self.text.configure(cursor="hand2"))
        self.text.tag_bind(tag, "<Leave>", lambda e: self.text.configure(cursor=self._text_cursor()))

    # -- speakers: rename, merge, find again --
    def open_speakers(self, focus=None):
        if self.busy or not self.speaker_count:
            return
        if self._speakers_win is None:
            self._speakers_win = SpeakersWindow(self)
        self._speakers_win.show(focus)

    def _speaker_ids(self):
        """Speaker ids in order of first appearance."""
        return list(dict.fromkeys(p["speaker"] for p in self.pieces
                                  if p.get("speaker") is not None))

    def _speakers_changed(self, status=None, refresh_window=True):
        self.speaker_count = len(self._speaker_ids())
        self.dirty = True
        self._render_all()
        self._update_header_tags()
        if status:
            self.status_lbl.configure(text=status)
        if refresh_window and self._speakers_win is not None:
            self._speakers_win.refresh()

    def rename_speaker(self, spk, name):
        name = name.strip()
        if name and name != self._speaker_name(spk):
            self._checkpoint("rename")
            self.names[spk] = name
            self._speakers_changed(refresh_window=False)

    def merge_speakers(self, src, dst):
        """Give all of speaker src's words to speaker dst."""
        self._set_mode(None)
        self._close_editing()
        self._checkpoint("speaker merge")
        message = f"Merged {self._speaker_name(src)} into {self._speaker_name(dst)}."
        for p in self.pieces:
            if p.get("speaker") == src:
                p["speaker"] = dst
        self.names.pop(src, None)
        self._speakers_changed(message)

    # -- undo and redo --
    def _checkpoint(self, label):
        """Call before changing the transcript; label says what the change is."""
        self.history.push(label, history.snapshot(self.pieces, self.names))

    def undo(self, redo=False):
        """Undo (or redo) the last change to the transcript."""
        if self.busy:
            return
        self._set_mode(None)
        self._close_editing()        # saving an open correction is a change of its own
        label = self.history.redo_label() if redo else self.history.undo_label()
        if label is None:
            self.status_lbl.configure(text=f"Nothing to {'redo' if redo else 'undo'}.")
            return
        step = self.history.redo if redo else self.history.undo
        _, snap = step(history.snapshot(self.pieces, self.names))
        self.pieces, self.names = history.restore(snap)
        self.sel = None
        self._speakers_changed(f"Redid the {label}." if redo else
                               f"Undid the {label}. Ctrl+Y redoes it.")

    # ------------------------------------------------------ find and replace --
    def open_find(self):
        """Ctrl+F: the Find window, starting from the text selected, if any."""
        if not self.transcript_screen.winfo_ismapped():
            return None
        self._set_mode(None)
        picked = ""
        for w in (self.text, self.edit_box):
            if w.tag_ranges("sel"):
                picked = w.get("sel.first", "sel.last")
                break
        if self._find_win is None:
            self._find_win = FindWindow(self)
        if picked and "\n" not in picked:
            self._find_win.query.set(picked)
        self._find_win.focus()
        return "break"

    def replace_matches(self, spans, new):
        """Replace these matches [(paragraph, start, end)] with new, exactly as typed, as one
        undo step. Unchanged words keep their times (timing.retime), as in a correction, and
        an open paragraph stays open."""
        if self.busy or not spans:
            return
        self._set_mode(None)
        open_i0 = self._save_edit()
        self._checkpoint("replacement" if len(spans) == 1 else "replacements")
        by_par = {}
        for k, s, e in spans:
            by_par.setdefault(k, []).append((s, e))
        shift = 0          # how far the open paragraph's first piece moves
        for k in sorted(by_par, reverse=True):     # from the end, so i0 / i1 stay right
            par = self._pars[k]
            i0, i1 = par["i0"], par["i1"]
            new_pieces = timing.retime(self.pieces[i0:i1],
                                       find.replace_spans(par["text"], by_par[k], new))
            if open_i0 is not None and i0 < open_i0:
                shift += len(new_pieces) - (i1 - i0)
            self.pieces[i0:i1] = new_pieces
        self.sel = None
        self.dirty = True
        self._render_all()
        if open_i0 is not None and self._pars:
            self.open_editor(self._par_at_piece(open_i0 + shift))
        n = len(spans)
        self.status_lbl.configure(text=f"Replaced {n} {'match' if n == 1 else 'matches'}. "
                                       "Ctrl+Z undoes it.")

    def _undo_key(self, redo):
        """Ctrl+Z / Ctrl+Y, unless a text box with its own undo is being typed in."""
        w = self.root.focus_get()
        typing = w is not None and w.winfo_ismapped() and (
            isinstance(w, (tk.Entry, ttk.Entry)) or
            (isinstance(w, tk.Text) and str(w.cget("state")) == "normal"))
        if not typing and self.transcript_screen.winfo_ismapped():
            self.undo(redo)
            return "break"
        return None

    def find_live_speakers(self):
        """Label speakers in a stopped live recording, after asking how many there are."""
        if self.busy or self.mode != "live" or not self.pieces or self.audio is None:
            return
        message = ("How many people speak in this recording? If you're not sure, "
                   "diemut counts them.")
        if self.speaker_count:
            message += "\n\n" + REPLACES_LABELS
        ok, n = ask_speaker_count(self.root, "Find speakers", message, "Find speakers")
        if ok:
            self.refind_speakers(n)

    def refind_speakers(self, num_speakers):
        """Run speaker labelling again; the text and its corrections are kept."""
        if self.busy or self.audio is None or not self.pieces:
            return
        self._set_mode(None)
        self._close_editing()
        self._relabel = True
        emit = self._new_session()
        self.busy = True
        self.stage = None
        self.progress.configure(value=0)
        self.progress.pack(side="right", padx=(0, 10))
        self._update_controls()
        duration = self.duration or self.audio.end
        threading.Thread(target=transcribe.speaker_worker, daemon=True,
                         args=(self.audio, duration, [dict(p) for p in self.pieces],
                               num_speakers, emit, self.stop_event)).start()

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
            self._set_mode(None)
            if self._relabel:
                self._checkpoint("new speaker labels")
            ids, count = payload
            for piece, spk in zip(self.pieces, ids):
                piece["speaker"] = spk
            self.speaker_count = count
            self.names = {}
            self._render_all()
            self._update_header_tags()
            if self._speakers_win is not None:
                self._speakers_win.refresh()
        elif kind in ("done", "error"):
            self.busy = False
            self._relabel = False
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

    # --------------------------------------------------------- sessions --
    @property
    def dirty(self):
        """True while there are changes that aren't saved in a session file."""
        return self._dirty

    @dirty.setter
    def dirty(self, value):
        self._dirty = value
        self._update_title()

    def _update_title(self):
        """The window title: the session's file name, with * while there are unsaved changes."""
        if self.session_path:
            name = os.path.basename(self.session_path)
        elif self.pieces:
            name = "Unsaved session"
        else:
            self.root.title("diemut")
            return
        self.root.title(f"{'*' if self.dirty else ''}{name} – diemut")

    def _default_name(self):
        """A file name (without extension) for saving or exporting this transcript."""
        if self.session_path:
            return os.path.splitext(os.path.basename(self.session_path))[0]
        if self.source_path:
            return os.path.splitext(os.path.basename(self.source_path))[0]
        return f"live_{self.session_start:%Y-%m-%d_%H%M}"

    def _can_save(self):
        return bool(self.pieces) and not self.busy

    def _save_key(self, ask):
        if self.transcript_screen.winfo_ismapped() and self._can_save():
            self.save_session(ask=ask)
        return "break"

    def save_session(self, ask=False):
        """Save to the session's file, asking where the first time (or with ask: Save as).
        Returns True if it was saved."""
        self._save_edit()
        if not self._can_save():
            messagebox.showinfo("Nothing to save", "There's no transcript to save yet."
                                if not self.pieces else "Wait for diemut to finish, then save.")
            return False
        path = self.session_path
        if ask or not path:
            path = filedialog.asksaveasfilename(
                title="Save session", defaultextension=sessions.SUFFIX,
                initialfile=self._default_name() + sessions.SUFFIX,
                filetypes=[("diemut session", f"*{sessions.SUFFIX}"), ("All files", "*.*")])
            if not path:
                return False
        self.status_lbl.configure(text="Saving…")
        self.root.configure(cursor="watch")
        self.root.update_idletasks()
        try:
            sessions.save(path, self._session_state(), self.audio)
        except Exception as exc:
            messagebox.showerror("Session not saved",
                                 f"diemut couldn't save the session to {path}. Check that the "
                                 f"folder exists and can be written to.\n\nDetails: {exc}")
            self.status_lbl.configure(text="Session not saved.")
            return False
        finally:
            self.root.configure(cursor="")
        self.session_path = path
        self.dirty = False
        self._drop_recovery()
        self.status_lbl.configure(text=f"Saved {os.path.basename(path)}")
        return True

    def _session_state(self):
        """Everything about the transcript that a session file keeps, except the audio."""
        return {
            "mode": self.mode,
            "source": os.path.basename(self.source_path) if self.source_path else None,
            "started": self.session_start.isoformat(timespec="seconds"),
            "model": self.session_model,
            "language": self.session_language,
            "detected_language": self.detected_language,
            "duration": self.duration,
            "speaker_count": self.speaker_count,
            "names": {str(k): v for k, v in self.names.items()},
            "pieces": self.pieces,
        }

    def open_session(self):
        path = filedialog.askopenfilename(
            title="Open a saved session",
            filetypes=[("diemut session", f"*{sessions.SUFFIX}"), ("All files", "*.*")])
        if not path or not self._confirm_discard("open another session"):
            return
        self.root.configure(cursor="watch")
        self.root.update_idletasks()
        try:
            state, clip = sessions.load(path)
        except sessions.SessionError as exc:
            messagebox.showerror("Couldn't open the session", str(exc))
            return
        finally:
            self.root.configure(cursor="")
        self._apply_session(state, clip, path)
        self.status_lbl.configure(text=f"Opened {os.path.basename(path)}" + (
            "" if clip is not None else ". Its audio is missing, so it can't be played."))

    def _apply_session(self, state, clip, path):
        """Show a session that was saved (path) or recovered (path None)."""
        self.reset_transcript()
        self.session_path = path
        self.pieces = state["pieces"]
        self.names = state["names"]
        self.speaker_count = state.get("speaker_count") or len(self._speaker_ids())
        self.session_model = state.get("model") or self.model_name
        self.session_language = state.get("language") or AUTO
        self.detected_language = state.get("detected_language")
        self.duration = state.get("duration")
        self.source_path = state.get("source")
        try:
            self.session_start = dt.datetime.fromisoformat(state["started"])
        except (KeyError, TypeError, ValueError):
            pass
        self.audio = clip
        end = max([clip.end if clip else 0.0] + [p["end"] for p in self.pieces])
        mode = "live" if state.get("mode") == "live" else "file"
        self._show_transcript(mode)
        if mode == "live":
            self.live_state = "stopped"
            self.live_t0 = time.monotonic() - end    # a recording that has finished
            self.sub_lbl.configure(text=fmt_time(end))
        else:
            name = self.source_path or os.path.basename(path or "") or "Recovered session"
            self.title_lbl.configure(text=f"{FILE_ICON} {name}")
            self.sub_lbl.configure(text=fmt_time(self.duration or end))
        self.dirty = path is None       # a recovered session hasn't been saved yet
        self._render_all()
        self._update_controls()
        self._update_header_tags()

    # -- the recovery copy, while there are unsaved changes --
    def _autosave(self):
        self._write_recovery()
        self.root.after(AUTOSAVE_MS, self._autosave)

    def _write_recovery(self):
        """Bring the recovery copy up to date, if there are unsaved changes. Writing it also
        tells another diemut that it's still in use (see sessions.left_behind)."""
        if not (self.dirty and self.pieces):
            return
        try:
            self._recovery = self._recovery or sessions.Recovery()
            self._recovery.write(self._session_state(), self.audio)
        except OSError:
            pass        # only a safety net: carry on without it

    def _drop_recovery(self):
        if self._recovery is not None:
            self._recovery.delete()
            self._recovery = None

    def _offer_recovery(self):
        """At start, offer back the newest session a diemut closed without saving."""
        found = sessions.left_behind()
        if not found:
            return
        folder, state = found[0]
        try:
            started = f"{dt.datetime.fromisoformat(state['started']):%d %b %Y %H:%M}"
        except (KeyError, TypeError, ValueError):
            started = "an unknown time"
        what = ("A live recording" if state.get("mode") == "live"
                else f"The transcript of {state.get('source') or 'a file'}")
        words = len(state.get("pieces") or [])
        choice = ask_choice(
            self.root, "Recover unsaved session?",
            f"diemut closed before a session was saved. {what}, from {started} "
            f"({words:,} words), can be recovered.",
            [("open", "Open it"), ("delete", "Delete it"), ("later", "Not now")])
        if choice == "delete":
            shutil.rmtree(folder, ignore_errors=True)
        elif choice == "open":
            try:
                state, clip = sessions.load_recovery(folder)
            except sessions.SessionError as exc:
                messagebox.showerror("Couldn't recover the session", str(exc))
                return
            self._apply_session(state, clip, None)
            self._write_recovery()
            shutil.rmtree(folder, ignore_errors=True)
            self.status_lbl.configure(text="Recovered the session. Save it to keep it.")

    # ----------------------------------------------------------- export --
    def export(self, kind):
        self._save_edit()
        if kind == "mp3":
            if self.audio is None or not self.audio.end:
                messagebox.showinfo("Nothing to save", "There's no recording yet.")
                return
        elif not self.pieces:
            messagebox.showinfo("Nothing to export", "There's no transcript yet.")
            return
        base = self._default_name()
        path = filedialog.asksaveasfilename(
            title=f"Export .{kind}", defaultextension=f".{kind}", initialfile=f"{base}.{kind}",
            filetypes=[(f"{kind.upper()} file", f"*.{kind}"), ("All files", "*.*")])
        if not path:
            return
        if kind == "mp3":
            try:
                export.export_mp3(path, self.audio)
            except Exception as exc:
                messagebox.showerror("Export failed", f"diemut couldn't save the file.\n\n{exc}")
                return
            self.status_lbl.configure(text=f"Saved {os.path.basename(path)}")
            return
        use_speakers = self.speaker_count > 1
        pieces = self.pieces if self.include_notes else notes.leave_out(self.pieces)
        if self.leave_out_fillers:
            pieces = fillers.leave_out(pieces, use_speakers)
        try:
            if kind == "txt":
                export.export_txt(path, pieces, self.names, use_speakers, self._ts_choice())
            elif kind == "vtt":
                export.export_vtt(path, pieces, self.names, use_speakers)
            elif kind == "srt":
                export.export_srt(path, pieces, self.names, use_speakers)
            else:
                lang = (language_name(self.detected_language) + " (detected)"
                        if self.session_language == AUTO and self.detected_language
                        else language_name(self.session_language))
                meta = [f"Source: {os.path.basename(self.source_path) if self.source_path else 'Live microphone'}",
                        f"Date: {self.session_start:%Y-%m-%d %H:%M}",
                        f"STT model: whisper-{self.session_model} (faster-whisper)",
                        f"Language: {lang}"]
                if self.speaker_count:
                    meta.append(f"Speakers: {self.speaker_count}")
                export.export_docx(path, pieces, self.names, use_speakers, self._ts_choice(),
                                   f"Transcript – {base}", meta)
        except Exception as exc:
            messagebox.showerror("Export failed", f"diemut couldn't save the file.\n\n{exc}")
            return
        self.status_lbl.configure(text=f"Saved {os.path.basename(path)}")

    def _ts_choice(self):
        """Which times .txt and .docx get: one of export.TIMESTAMPS."""
        return self.ts_mode if self.include_ts else "none"

    def clear(self):
        if self.busy or not self.pieces:
            return
        if not self._confirm_discard("clear it"):
            return
        keep_source = self.source_path
        self.close_editor(save=False)
        self.reset_transcript()
        self.source_path = keep_source
        self._render_all("")
        self._update_header_tags()
        self._update_controls()
        self.status_lbl.configure(text="Cleared.")
