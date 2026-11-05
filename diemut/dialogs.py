"""Small windows: tooltips, the Speakers window, the speaker-count and Export dialogs, the
list of shortcuts, Settings, and a question with a few choices."""

import tkinter as tk
from tkinter import messagebox, ttk

from . import export, playback, transcribe
from .export import fmt_time
from .theme import ACCENT, BG, BORDER, BORDER_STRONG, FONTS, MUTED, TEXT, TEXT2

SPEAKER_COUNTS = (None, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10)


def count_label(n):
    return "Not sure (diemut counts them)" if n is None else \
        f"{n} {'person' if n == 1 else 'people'}"


def _scale(widget):
    """The display scaling (1.5 at 150%)."""
    return max(1.0, widget.winfo_fpixels("1i") / 96.0)


def tip_window(widget, title, text, x, y):
    """A small pop-up with a title and a line or two of text, with its top-left near x, y
    (screen coordinates). x is kept on screen. Destroy it to hide it."""
    tip = tk.Toplevel(widget)
    tip.wm_overrideredirect(True)
    try:
        tip.attributes("-topmost", True)
    except tk.TclError:
        pass
    frame = tk.Frame(tip, bg=BG, highlightthickness=1,
                     highlightbackground=BORDER_STRONG, padx=10, pady=7)
    tk.Label(frame, text=title, font=FONTS["small_bold"], fg=TEXT2, bg=BG,
             anchor="w").pack(fill="x")
    tk.Label(frame, text=text, font=FONTS["small"], fg=TEXT, bg=BG, wraplength=260,
             justify="left", anchor="w").pack(fill="x")
    frame.pack()
    tip.update_idletasks()
    x = max(0, min(x, tip.winfo_screenwidth() - tip.winfo_reqwidth()))
    tip.geometry(f"+{x}+{y}")
    return tip


class Tooltip:
    """A small pop-up that appears while the mouse is over a widget, unless hover hints are
    turned off in Settings (Tooltip.enabled)."""

    enabled = True

    def __init__(self, widget, title, text):
        self.widget, self.title, self.text, self.tip = widget, title, text, None
        widget.bind("<Enter>", self.show, add="+")
        widget.bind("<Leave>", self.hide, add="+")

    def show(self, _event=None):
        if self.tip is not None or not Tooltip.enabled:
            return
        text = self.text() if callable(self.text) else self.text
        w = self.widget
        y = w.winfo_rooty() + w.winfo_height() + 6      # centred below the widget
        self.tip = tip = tip_window(w, self.title, text, 0, y)
        x = w.winfo_rootx() + w.winfo_width() // 2 - tip.winfo_reqwidth() // 2
        tip.geometry(f"+{max(0, x)}+{y}")

    def hide(self, _event=None):
        if self.tip is not None:
            self.tip.destroy()
            self.tip = None


def center_over(win, parent):
    """Place a pop-up window over the middle of parent's window, kept on screen."""
    win.update_idletasks()
    top = parent.winfo_toplevel()
    w, h = win.winfo_reqwidth(), win.winfo_reqheight()
    x = top.winfo_rootx() + (top.winfo_width() - w) // 2
    y = top.winfo_rooty() + (top.winfo_height() - h) // 3
    x = max(0, min(x, win.winfo_screenwidth() - w))
    y = max(0, min(y, win.winfo_screenheight() - h - 40))
    win.geometry(f"+{x}+{y}")


def make_dialog(parent, title):
    """A small window belonging to parent, hidden until show_dialog places it."""
    win = tk.Toplevel(parent, bg=BG, padx=16, pady=14)
    win.withdraw()
    win.title(title)
    win.transient(parent)
    win.resizable(False, False)
    return win


def show_dialog(win, parent, modal=False):
    """Show win over the middle of parent; a modal one takes all clicks until it closes."""
    center_over(win, parent)
    win.deiconify()
    win.lift()
    if modal:
        win.wait_visibility()
        win.grab_set()


REPLACES_LABELS = ("This replaces the current speaker labels, including names, merges and "
                   "speakers you changed. Your text corrections are kept.")


class SpeakersWindow:
    """A small window for renaming and merging speakers, or finding them again."""

    def __init__(self, app):
        self.app = app
        self.player = playback.Player()
        self._sample = None       # (button, speaker) being played
        win = self.win = make_dialog(app.root, "Speakers")
        win.protocol("WM_DELETE_WINDOW", self.close)
        win.bind("<Escape>", lambda e: self.close())
        tk.Label(win, text="Speakers", font=FONTS["header"], fg=TEXT, bg=BG,
                 anchor="w").pack(fill="x")
        tk.Label(win, text="If one person has several labels, merge them. If two people share "
                           "a label, select one person's words in the correction panel and "
                           "choose their speaker there.",
                 font=FONTS["small"], fg=TEXT2, bg=BG, anchor="w", justify="left",
                 wraplength=int(430 * app._scale)).pack(fill="x", pady=(2, 10))
        self.rows = tk.Frame(win, bg=BG)
        self.rows.pack(fill="x")

        bottom = tk.Frame(win, bg=BG)
        bottom.pack(fill="x", pady=(14, 0))
        self.again_btn = ttk.Button(bottom, text="Find speakers again…", style="Accent.TButton",
                                    command=self.find_again)
        self.again_btn.pack(side="left")
        ttk.Button(bottom, text="Close", command=self.close).pack(side="right")
        self.undo_btn = ttk.Button(bottom, text="Undo merge", command=app.undo_merge)
        self.undo_btn.pack(side="right", padx=(0, 6))
        self.refresh()

    def show(self, focus=None):
        show_dialog(self.win, self.app.root)
        entry = self._entries.get(focus)
        (entry or self.win).focus_set()
        if entry:
            entry.select_range(0, "end")

    def refresh(self):
        """Rebuild the rows from the transcript."""
        app = self.app
        self._stop_sample()
        for w in self.rows.winfo_children():
            w.destroy()
        self._entries = {}
        ids = app._speaker_ids()
        talk = {spk: 0.0 for spk in ids}
        for p in app.pieces:
            if p.get("speaker") in talk:
                talk[p["speaker"]] += p["end"] - p["start"]
        for c, text in enumerate(("Name", "Talks for", "", "")):
            tk.Label(self.rows, text=text, font=FONTS["small"], fg=MUTED, bg=BG).grid(
                row=0, column=c, sticky="w", padx=(0, 10))
        for r, spk in enumerate(ids, 1):
            entry = tk.Entry(self.rows, font=FONTS["body"], fg=TEXT, bg=BG, relief="flat",
                             highlightthickness=1, highlightbackground=BORDER,
                             highlightcolor=ACCENT, width=18, insertbackground=TEXT)
            entry.insert(0, app._speaker_name(spk))
            entry.grid(row=r, column=0, sticky="w", padx=(0, 10), pady=3, ipady=2)
            entry.bind("<Return>", lambda e, s=spk: app.rename_speaker(s, e.widget.get()))
            entry.bind("<FocusOut>", lambda e, s=spk: app.rename_speaker(s, e.widget.get()))
            self._entries[spk] = entry
            tk.Label(self.rows, text=fmt_time(talk[spk]), font=FONTS["mono"], fg=TEXT2,
                     bg=BG).grid(row=r, column=1, sticky="w", padx=(0, 10))
            play = ttk.Button(self.rows, text="▶", width=3)
            play.configure(command=lambda b=play, s=spk: self._toggle_sample(b, s))
            play.grid(row=r, column=2, padx=(0, 10))
            Tooltip(play, "Listen", "Plays a few seconds of this speaker.")
            merge = ttk.Combobox(self.rows, style="Subtle.TCombobox", state="readonly",
                                 font=FONTS["dropdown"], width=20)
            # The list is filled when it opens, so it always shows the current names.
            merge.configure(postcommand=lambda m=merge, s=spk: m.configure(values=[
                f"Merge into {app._speaker_name(o)}" for o in app._speaker_ids() if o != s]))
            merge.set("Merge into…")
            if len(ids) < 2:
                merge.configure(state="disabled")
            merge.bind("<<ComboboxSelected>>", lambda e, s=spk: self._merge(e, s))
            merge.grid(row=r, column=3, sticky="w")
        busy = app.busy
        self.undo_btn.configure(state="normal" if app._merge_undo and not busy else "disabled")
        self.again_btn.configure(state="disabled" if busy or app.audio is None else "normal")

    def _merge(self, event, src):
        others = [o for o in self.app._speaker_ids() if o != src]
        index = event.widget.current()
        if 0 <= index < len(others):
            self.app.merge_speakers(src, others[index])

    def _toggle_sample(self, button, spk):
        playing = self._sample is not None and self._sample[1] == spk
        self._stop_sample()
        if playing or self.app.audio is None:
            return
        # A few seconds from this speaker's longest stretch of talking
        best, run = None, None
        for p in self.app.pieces:
            if p.get("speaker") == spk:
                run = [run[0], p["end"]] if run else [p["start"], p["end"]]
                if best is None or run[1] - run[0] > best[1] - best[0]:
                    best = list(run)
            else:
                run = None
        if best is None:
            return
        start, end = best[0], min(best[1], best[0] + 6.0)
        self.app._stop_playback()
        self.player.load(self.app.audio.slice(start, end), start)
        try:
            self.player.play(start, end)
        except Exception as exc:
            messagebox.showerror("diemut", "diemut couldn't play the audio. Check that speakers "
                                           f"or headphones are connected.\n\nDetails: {exc}",
                                 parent=self.win)
            return
        self._sample = (button, spk)
        button.configure(text="‖")
        self._watch_sample()

    def _watch_sample(self):
        if self._sample is None:
            return
        if not self.player.is_playing:
            self._stop_sample()
            return
        self.win.after(100, self._watch_sample)

    def _stop_sample(self):
        self.player.pause()
        if self._sample is not None:
            try:
                self._sample[0].configure(text="▶")
            except tk.TclError:
                pass     # the row was rebuilt
            self._sample = None

    def find_again(self):
        ok, n = ask_speaker_count(
            self.win, "Find speakers again", "diemut will label the speakers again from the "
            "audio. How many people speak? If you're not sure, it counts them.\n\n"
            + REPLACES_LABELS, "Find speakers")
        if ok:
            self.close()
            self.app.refind_speakers(n)

    def close(self):
        self._stop_sample()
        self.player.close()
        self.win.destroy()
        self.app._speakers_win = None


def ask_speaker_count(parent, title, message, action):
    """A small dialog asking how many people speak. Returns (confirmed, count or None)."""
    win = make_dialog(parent, title)
    tk.Label(win, text=message, font=FONTS["body"], fg=TEXT, bg=BG, justify="left",
             anchor="w", wraplength=int(380 * _scale(parent))).pack(fill="x")
    box = ttk.Combobox(win, style="Subtle.TCombobox", state="readonly",
                       font=FONTS["dropdown"], width=30,
                       values=[count_label(n) for n in SPEAKER_COUNTS])
    box.set(count_label(None))
    box.pack(anchor="w", pady=(10, 0))
    result = {}

    def confirm(_event=None):
        result["n"] = next(n for n in SPEAKER_COUNTS if count_label(n) == box.get())
        win.destroy()

    row = tk.Frame(win, bg=BG)
    row.pack(fill="x", pady=(14, 0))
    ttk.Button(row, text=action, style="Accent.TButton", command=confirm).pack(side="right")
    ttk.Button(row, text="Cancel", command=win.destroy).pack(side="right", padx=(0, 6))
    win.bind("<Return>", confirm)
    win.bind("<Escape>", lambda e: win.destroy())
    show_dialog(win, parent, modal=True)
    box.focus_set()
    parent.wait_window(win)
    return "n" in result, result.get("n")


def ask_choice(parent, title, message, choices):
    """A small modal question with a button for each of choices, [(key, label)]: the first
    is the main one (Enter). Returns the key chosen, or None if it was closed (Esc)."""
    win = make_dialog(parent, title)
    tk.Label(win, text=message, font=FONTS["body"], fg=TEXT, bg=BG, justify="left",
             anchor="w", wraplength=int(380 * _scale(parent))).pack(fill="x")
    result = {}

    def choose(key):
        result["key"] = key
        win.destroy()

    row = tk.Frame(win, bg=BG)
    row.pack(fill="x", pady=(14, 0))
    for i, (key, label) in reversed(list(enumerate(choices))):
        ttk.Button(row, text=label, style="Accent.TButton" if i == 0 else "TButton",
                   command=lambda k=key: choose(k)).pack(side="right", padx=(6, 0))
    win.bind("<Return>", lambda e: choose(choices[0][0]))
    win.bind("<Escape>", lambda e: win.destroy())
    show_dialog(win, parent, modal=True)
    win.focus_set()
    parent.wait_window(win)
    return result.get("key")


def shortcuts_window(app, shortcuts):
    """Every correction-panel shortcut: shortcuts is [(keys, what they do, ...)]. One window
    at a time; asking again brings it to the front."""
    win = app._shortcuts_win
    if win is not None and win.winfo_exists():
        win.deiconify()
        win.lift()
        return
    win = app._shortcuts_win = make_dialog(app.root, "Shortcuts")
    grid = tk.Frame(win, bg=BG)
    grid.pack(fill="x")
    for r, (keys, action, *_) in enumerate(shortcuts):
        tk.Label(grid, text=keys, font=FONTS["header"], fg=TEXT, bg=BG).grid(
            row=r, column=0, sticky="w", padx=(0, 16), pady=2)
        tk.Label(grid, text=action, font=FONTS["body"], fg=TEXT2, bg=BG).grid(
            row=r, column=1, sticky="w", pady=2)
    tk.Label(win, text="The keys work while you're typing in the text box.", font=FONTS["small"],
             fg=MUTED, bg=BG).pack(anchor="w", pady=(10, 0))
    ttk.Button(win, text="Close", command=win.destroy).pack(anchor="e", pady=(10, 0))
    win.bind("<Escape>", lambda e: win.destroy())
    show_dialog(win, app.root)
    win.focus_set()


DEFAULT_MIC = "System default"


def settings_window(app):
    """Settings: the microphone, hover hints, and the fillers default for Export. Changes
    apply and are saved straight away (App.change_setting). One window at a time."""
    win = app._settings_win
    if win is not None and win.winfo_exists():
        win.deiconify()
        win.lift()
        return
    win = app._settings_win = make_dialog(app.root, "Settings")
    wrap = int(340 * _scale(win))
    tk.Label(win, text="Settings", font=FONTS["header"], fg=TEXT, bg=BG,
             anchor="w").pack(fill="x", pady=(0, 10))

    def note(text):
        tk.Label(win, text=text, font=FONTS["small"], fg=MUTED, bg=BG, anchor="w",
                 justify="left", wraplength=wrap).pack(fill="x", pady=(2, 0))

    row = tk.Frame(win, bg=BG)
    row.pack(fill="x")
    tk.Label(row, text="Microphone", font=FONTS["body"], fg=TEXT2, bg=BG).pack(side="left")
    mic_box = ttk.Combobox(row, style="Subtle.TCombobox", state="readonly",
                           font=FONTS["dropdown"], width=34)
    mic_box.configure(postcommand=lambda: mic_box.configure(   # fresh each time it opens
        values=[DEFAULT_MIC] + [n for _, n in transcribe.input_devices()]))
    mic_box.set(app.settings["microphone"] or DEFAULT_MIC)
    mic_box.pack(side="left", padx=(8, 0))

    def on_mic(_event=None):
        mic_box.selection_clear()
        label = mic_box.get()
        app.change_setting("microphone", None if label == DEFAULT_MIC else label)
    mic_box.bind("<<ComboboxSelected>>", on_mic)
    note("What live mode records from. A microphone or headset plugged in after diemut "
         "started shows up here too. A change takes effect at the next Start.")

    for key, label, text in (
            ("hover_hints", "Show hover hints",
             "Short explanations when the mouse rests on a button or box."),
            ("leave_out_fillers", "Leave out fillers and false starts when exporting",
             "Whether Export starts with this ticked. The transcript always keeps them.")):
        var = tk.BooleanVar(value=app.settings[key])
        ttk.Checkbutton(win, text=label, variable=var,
                        command=lambda k=key, v=var: app.change_setting(k, v.get())).pack(
            anchor="w", pady=(14, 0))
        note(text)

    ttk.Button(win, text="Close", style="Accent.TButton", command=win.destroy).pack(
        anchor="e", pady=(16, 0))
    win.bind("<Escape>", lambda e: win.destroy())
    show_dialog(win, app.root)
    win.focus_set()


def export_dialog(app):
    """A small dialog: what to export, and with which timestamps."""
    app._save_edit()
    has_text = bool(app.pieces)
    audio_ready = (app.mode == "live" and app.live_state in ("idle", "stopped")
                   and not app.busy and app.audio is not None and bool(app.audio.end))
    formats = [("txt", "Text (.txt)", has_text), ("vtt", "Subtitles (.vtt)", has_text),
               ("docx", "Word (.docx)", has_text)]
    if app.mode == "live":
        formats.append(("mp3", "Audio (.mp3)" if audio_ready or app.live_state == "idle"
                        else "Audio (.mp3), after Stop", audio_ready))
    available = [k for k, _, ok in formats if ok]
    if not available:
        messagebox.showinfo("Nothing to export", "There's no transcript yet.")
        return

    win = make_dialog(app.root, "Export")
    tk.Label(win, text="Export", font=FONTS["header"], fg=TEXT, bg=BG,
             anchor="w").pack(fill="x", pady=(0, 8))
    kind = tk.StringVar(value=app.export_kind if app.export_kind in available
                        else available[0])
    for k, label, ok in formats:
        ttk.Radiobutton(win, text=label, value=k, variable=kind,
                        state="normal" if ok else "disabled").pack(anchor="w", pady=1)

    ts_row = tk.Frame(win, bg=BG)
    ts_row.pack(fill="x", pady=(12, 0))
    tk.Label(ts_row, text="Timestamps", font=FONTS["body"], fg=TEXT2, bg=BG).pack(side="left")
    ts_box = ttk.Combobox(ts_row, style="Subtle.TCombobox", state="readonly",
                          font=FONTS["dropdown"], width=18,
                          values=[app.ts_labels[t] for t in export.TIMESTAMPS])
    ts_box.set(app.ts_labels[app.ts_mode])
    ts_box.pack(side="left", padx=(8, 0))
    ts_note = tk.Label(win, text="", font=FONTS["small"], fg=MUTED, bg=BG, anchor="w",
                       justify="left", wraplength=int(300 * app._scale))
    ts_note.pack(fill="x", pady=(4, 0))
    leave_out = tk.BooleanVar(value=app.leave_out_fillers)
    leave_out_box = ttk.Checkbutton(win, text="Leave out fillers (um, uh…) and false starts",
                                    variable=leave_out)
    leave_out_box.pack(anchor="w", pady=(10, 0))

    def on_kind(*_):
        k = kind.get()
        ts_box.configure(state="readonly" if k in ("txt", "docx") else "disabled")
        leave_out_box.configure(state="disabled" if k == "mp3" else "normal")
        turns = ("Each speaker turn: one time when the speaker changes (on every "
                 "paragraph, without speaker labels).")
        ts_note.configure(text={
            "txt": turns, "docx": turns,
            "vtt": "Subtitles always have times.",
            "mp3": "The recording, with paused stretches kept as silence so its times "
                   "match the transcript.",
        }[k])
    kind.trace_add("write", on_kind)
    on_kind()

    def go(_event=None):
        app.export_kind = kind.get()
        app.ts_mode = next(t for t, label in app.ts_labels.items()
                            if label == ts_box.get())
        app.leave_out_fillers = leave_out.get()
        win.destroy()
        app.export(app.export_kind)

    row = tk.Frame(win, bg=BG)
    row.pack(fill="x", pady=(14, 0))
    ttk.Button(row, text="Export…", style="Accent.TButton", command=go).pack(side="right")
    ttk.Button(row, text="Cancel", command=win.destroy).pack(side="right", padx=(0, 6))
    win.bind("<Return>", go)
    win.bind("<Escape>", lambda e: win.destroy())
    show_dialog(win, app.root, modal=True)
    win.focus_set()
