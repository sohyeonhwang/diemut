"""The correction panel: listening to a paragraph, correcting it, and saving it back.

EditorMixin is part of App (see ui.py) and uses its state: pieces, _pars, sel (the open
paragraph), text (the transcript), audio, player and status_lbl, among others.
"""

import tkinter as tk
from tkinter import messagebox, ttk

import numpy as np

from . import playback, timing
from .dialogs import Tooltip
from .export import fmt_time
from .theme import ACCENT, BG, BORDER, BORDER_STRONG, DANGER, FONTS, MUTED, SURFACE, TEXT, TEXT2

PLAY_LABEL = "▶ Play"
PAUSE_LABEL = "‖ Pause"


class EditorMixin:
    """The correction panel, as part of App."""

    # ----------------------------------------------- listen and correct --
    def _build_editor(self, screen):
        scale = max(1.0, self.root.winfo_fpixels("1i") / 96.0)
        self._scale = scale
        ed = tk.Frame(screen, bg=SURFACE, highlightthickness=1,
                      highlightbackground=BORDER_STRONG, padx=10, pady=8)
        row = tk.Frame(ed, bg=SURFACE)
        row.pack(fill="x")
        self.play_btn = ttk.Button(row, text=PLAY_LABEL, width=7, command=self.toggle_play)
        self.play_btn.pack(side="left")
        self.back_btn = ttk.Button(row, text="⟲ 3 s", command=self.skip_back)
        self.back_btn.pack(side="left", padx=(6, 0))
        self.loop_btn = ttk.Button(row, text="Loop", style="Toggle.TButton",
                                   command=self.toggle_loop)
        self.loop_btn.pack(side="left", padx=(6, 0))
        self.speed_box = ttk.Combobox(row, style="Subtle.TCombobox", state="readonly",
                                      font=FONTS["dropdown"], width=5,
                                      values=[self._speed_label(s) for s in reversed(playback.SPEEDS)])
        self.speed_box.set(self._speed_label(1.0))
        self.speed_box.pack(side="left", padx=(6, 0))
        Tooltip(self.speed_box, "Playback speed",
                "Faster and slower speeds keep voices at their normal pitch. "
                "Ctrl+↓ and Ctrl+↑ change it.")
        self.speed_box.bind("<<ComboboxSelected>>", self._on_speed)
        self.time_lbl = tk.Label(row, text="", font=FONTS["mono"], fg=TEXT2, bg=SURFACE)
        self.time_lbl.pack(side="left", padx=(10, 0))
        self.spk_frame = tk.Frame(row, bg=SURFACE)
        self.spk_lbl = tk.Label(self.spk_frame, text="Speaker", fg=TEXT2, bg=SURFACE,
                                font=FONTS["body"])
        self.spk_lbl.pack(side="left", padx=(0, 6))
        self.spk_box = ttk.Combobox(self.spk_frame, style="Subtle.TCombobox", state="readonly",
                                    font=FONTS["dropdown"], width=12)
        self.spk_box.pack(side="left")
        self.spk_box.bind("<<ComboboxSelected>>", self._on_speaker_choice)
        Tooltip(self.spk_box, "Speaker",
                lambda: "Gives the selected words to this speaker." if self._text_sel()
                else "Moves this paragraph to another speaker. Select words first to move "
                     "just those.")
        ttk.Button(row, text="Done", command=lambda: self.close_editor(save=True)).pack(side="right")
        ttk.Button(row, text="Next ▶", command=lambda: self._step(1)).pack(side="right", padx=(0, 6))
        ttk.Button(row, text="◀ Prev", command=lambda: self._step(-1)).pack(side="right", padx=(0, 6))

        # Audio bar: the paragraph's waveform, with a playhead and a selectable stretch
        self.wave = tk.Canvas(ed, height=int(64 * scale), bg=BG, highlightthickness=1,
                              highlightbackground=BORDER, cursor="hand2")
        self.wave.pack(fill="x", pady=(8, 0))
        self.wave.bind("<Configure>", lambda e: self._draw_wave())
        self.wave.bind("<ButtonPress-1>", self._wave_press)
        self.wave.bind("<B1-Motion>", self._wave_drag_to)
        self.wave.bind("<ButtonRelease-1>", self._wave_release)
        times = tk.Frame(ed, bg=SURFACE)
        times.pack(fill="x")
        self.wave_start_lbl = tk.Label(times, text="", font=FONTS["mono"], fg=TEXT2, bg=SURFACE)
        self.wave_start_lbl.pack(side="left")
        self.wave_end_lbl = tk.Label(times, text="", font=FONTS["mono"], fg=TEXT2, bg=SURFACE)
        self.wave_end_lbl.pack(side="right")

        self.edit_box = tk.Text(ed, height=4, wrap="word", font=FONTS["text"], bg=BG, fg=TEXT,
                                relief="flat", highlightthickness=1, highlightbackground=BORDER,
                                highlightcolor=ACCENT, padx=8, pady=6, undo=True,
                                insertbackground=TEXT, exportselection=False)
        self.edit_box.pack(fill="x", pady=(4, 4))
        self.edit_box.tag_configure("linked", background=TEXT, foreground=BG)
        shortcuts = ["Ctrl+Space plays or pauses (just the selected words, if any)",
                     "Alt+← back 3 s", "Ctrl+L loop", "Ctrl+↓ / Ctrl+↑ slower / faster",
                     "Double-click a word to move the playhead there",
                     "Enter splits the paragraph", "Ctrl+Enter saves", "Esc discards changes"]
        # Non-breaking spaces inside each shortcut, so lines only wrap between them
        hint = tk.Label(ed, text="  ·  ".join(t.replace(" ", "\u00a0") for t in shortcuts),
                        font=FONTS["small"], fg=MUTED, bg=SURFACE, anchor="w", justify="left")
        hint.pack(fill="x")
        hint.bind("<Configure>", lambda e: hint.configure(wraplength=max(100, e.width)))
        box = self.edit_box
        box.bind("<Control-Return>", lambda e: self._key(self._save_edit))
        box.bind("<Escape>", lambda e: self._key(
            (lambda: self._set_mode(None)) if self._mode else
            (lambda: self.close_editor(save=False))))
        box.bind("<Control-space>", lambda e: self._key(self.toggle_play))
        box.bind("<Alt-Left>", lambda e: self._key(self.skip_back))
        box.bind("<Control-l>", lambda e: self._key(self.toggle_loop))
        box.bind("<Control-L>", lambda e: self._key(self.toggle_loop))
        box.bind("<Control-Down>", lambda e: self._key(lambda: self._change_speed(-1)))
        box.bind("<Control-Up>", lambda e: self._key(lambda: self._change_speed(1)))
        box.bind("<<Selection>>", self._on_text_selection)
        box.bind("<Double-Button-1>", self._on_word_double_click)
        box.bind("<KeyRelease>", lambda e: self._refresh_linked_words(), add="+")
        return ed

    @staticmethod
    def _key(action):
        action()
        return "break"

    # -- the correction panel: opening, saving, closing --
    def open_editor(self, k):
        if self.sel is not None and k != self.sel:
            # Saving the open paragraph can renumber the ones after it, so follow paragraph k
            # by its first piece: that piece object is untouched by the save.
            target = self.pieces[self._pars[k]["i0"]]
            self._apply_edit()
            i = next((i for i, p in enumerate(self.pieces) if p is target), None)
            k = None if i is None else self._par_at_piece(i)
            if k is None:
                self.close_editor(save=False)
                return
        self._stop_playback()
        if k != self.sel and self._mode == "split":
            self._set_mode(None)
        self.sel = k
        par = self._pars[k]
        self._show_open_paragraph()
        self.edit_box.delete("1.0", "end")
        self.edit_box.insert("1.0", par["text"])
        self.edit_box.edit_reset()
        self._fill_speakers(par["speaker"] if par["speaker"] is not None
                            else self.pieces[par["i0"]].get("speaker"))
        self._load_audio(par)
        if not self.editor.winfo_ismapped():
            self.editor.pack(side="bottom", fill="x", padx=14, pady=(4, 0), before=self.body)
        self.edit_box.focus_set()
        if self._play_job is None:
            self._play_job = self.root.after(50, self._tick_playback)
        self._update_par_tools()

    def _show_open_paragraph(self, scroll=True):
        """Highlight the open paragraph in the transcript."""
        t = self.text
        t.tag_remove("selected", "1.0", "end")
        ranges = t.tag_ranges(f"par:{self.sel}") if self.sel is not None else ()
        if ranges:
            t.tag_add("selected", ranges[0], ranges[-1])
            if scroll:
                t.see(ranges[0])

    def _fill_speakers(self, current):
        ids = sorted({p["speaker"] for p in self.pieces if p.get("speaker") is not None})
        if not ids:
            self._spk_map = {}
            self.spk_frame.pack_forget()
            return
        labels = [self._speaker_name(i) for i in ids] + ["New speaker"]
        self._spk_map = dict(zip(labels, ids))
        self._spk_map["New speaker"] = max(ids) + 1
        self.spk_box.configure(values=labels)
        self.spk_box.set(self._speaker_name(current if current is not None else ids[0]))
        self.spk_lbl.configure(text="Speaker")
        self.spk_frame.pack(side="left", padx=(16, 0))

    def _apply_edit(self):
        """Write the editor's text and speaker into the transcript.

        Returns how many pieces the paragraph has now (0 if its text was all deleted), or
        None if nothing changed. Saving can renumber pieces and paragraphs.
        """
        if self.sel is None or self.sel >= len(self._pars):
            return None
        par = self._pars[self.sel]
        i0, i1 = par["i0"], par["i1"]
        old = self.pieces[i0:i1]
        # Each line typed in the box (Enter) becomes its own paragraph.
        lines = (" ".join(line.split()) for line in self.edit_box.get("1.0", "end-1c").split("\n"))
        new_text = "\n".join(line for line in lines if line)
        chosen = self._spk_map.get(self.spk_box.get()) if self._spk_map else None
        text_changed = new_text != par["text"]
        speaker_changed = chosen is not None and chosen != old[0].get("speaker")
        if not text_changed and not speaker_changed:
            return None
        # Unchanged words keep their times; corrected ones fill the gaps around them.
        new = timing.retime(old, new_text) if text_changed else [dict(p) for p in old]
        if speaker_changed:
            for p in new:
                p["speaker"] = chosen
        self.pieces[i0:i1] = new
        self.speaker_count = len(self._speaker_ids())
        self.dirty = True
        self._render_all()
        self._update_header_tags()
        self.status_lbl.configure(text="Correction saved." if new else "Paragraph removed.")
        return len(new)

    def _save_edit(self):
        """Save the open correction and keep the panel open on that paragraph.

        Returns the index of the paragraph's first piece, or None if no paragraph was open
        or its text was all deleted (the panel then closes).
        """
        if self.sel is None:
            return None
        i0 = self._pars[self.sel]["i0"]
        result = self._apply_edit()
        if result == 0:
            self.close_editor(save=False)
            return None
        if result is not None:
            self.sel = None
            self.open_editor(self._par_at_piece(i0))
        return i0

    def _close_editing(self):
        """Save the open correction and close the panel. Returns what _save_edit does."""
        if self.sel is None:
            return None
        i0 = self._pars[self.sel]["i0"]
        result = self._apply_edit()
        self.close_editor(save=False)
        return None if result == 0 else i0

    def _step(self, direction):
        if self.sel is None:
            return
        i0 = self._pars[self.sel]["i0"]
        removed = self._apply_edit() == 0
        current = self._par_at_piece(i0)
        if current is None:
            self.close_editor(save=False)
            return
        target = (current if direction > 0 else current - 1) if removed else current + direction
        self.sel = None
        self.open_editor(max(0, min(len(self._pars) - 1, target)))

    def close_editor(self, save=True):
        if save:
            self._apply_edit()
        self.player.close()
        if self._play_job is not None:
            self.root.after_cancel(self._play_job)
            self._play_job = None
        self.sel = None
        if self._mode == "split":
            self._set_mode(None)
        self.text.tag_remove("selected", "1.0", "end")
        self.editor.pack_forget()
        self._update_par_tools()

    # ---------------------------------------------------- audio review --
    @staticmethod
    def _speed_label(speed):
        return f"{speed:g}×"

    @staticmethod
    def _tenths(seconds):
        return fmt_time(seconds, with_ms=True)[:-2]   # HH:MM:SS.s

    def _load_audio(self, par):
        """Give the player and the audio bar the open paragraph, with 0.5 s either side."""
        t0 = max(0.0, par["start"] - 0.5)
        t1 = par["end"] + 0.5
        if self.mode == "file" and self.duration:
            t1 = min(t1, max(self.duration, par["end"]))
        audio = self.audio.slice(t0, t1) if self.audio is not None else np.zeros(0, np.float32)
        self.player.load(audio, t0)
        self._wave_span = (t0, t1)
        self._region, self._region_src = None, None
        self.edit_box.tag_remove("linked", "1.0", "end")
        state = "normal" if len(audio) else "disabled"
        for w in (self.play_btn, self.back_btn, self.loop_btn):
            w.configure(state=state)
        self.speed_box.configure(state="readonly" if len(audio) else "disabled")
        self.wave_start_lbl.configure(text=self._tenths(t0))
        self.wave_end_lbl.configure(text=self._tenths(t1))
        self.time_lbl.configure(text=self._tenths(t0))
        self._draw_wave()

    def toggle_play(self):
        if self.player.is_playing:
            self._stop_playback()
            return
        if self.sel is None or not len(self.player.source):
            return
        a, b = self._region or self._wave_span
        pos = self.player.position
        # Resume from the playhead if it's inside what will play; otherwise start over.
        self._play(pos if a <= pos < b - 0.05 else a, b)

    def _play(self, start, end):
        loop_from = (self._region or self._wave_span)[0] if self._loop else None
        try:
            self.player.play(start, end, loop_from)
        except Exception as exc:
            self.player.close()
            messagebox.showerror("diemut", "diemut couldn't play the audio. Check that speakers "
                                           f"or headphones are connected.\n\nDetails: {exc}")
            return
        self._show_playing()

    def _stop_playback(self):
        """Pause, keeping the playhead where it is."""
        self.player.pause()
        self._show_playing()

    def _show_playing(self):
        self._shown_playing = self.player.is_playing
        self.play_btn.configure(text=PAUSE_LABEL if self._shown_playing else PLAY_LABEL)

    def skip_back(self):
        if self.sel is None or not len(self.player.source):
            return
        t = max(self._wave_span[0], self.player.position - 3.0)
        if self.player.is_playing:
            self.player.seek(t)
        else:
            a, b = self._region or self._wave_span
            self._play(t, b if t < b else self._wave_span[1])

    def toggle_loop(self):
        if self.sel is None or not len(self.player.source):
            return
        self._loop = not self._loop
        self.loop_btn.state(["selected"] if self._loop else ["!selected"])
        if self.player.is_playing:
            self.player.set_loop((self._region or self._wave_span)[0] if self._loop else None)

    def _on_speed(self, _event=None):
        label = self.speed_box.get()
        speed = next(s for s in playback.SPEEDS if self._speed_label(s) == label)
        self.player.set_speed(speed)
        self.speed_box.selection_clear()
        self.edit_box.focus_set()

    def _change_speed(self, direction):
        if self.sel is None or not len(self.player.source):
            return
        speeds = playback.SPEEDS
        i = max(0, min(len(speeds) - 1, speeds.index(self.player.speed) + direction))
        self.speed_box.set(self._speed_label(speeds[i]))
        self.player.set_speed(speeds[i])

    def _tick_playback(self):
        """Move the playhead and time while the panel is open (about every 50 ms)."""
        if self.sel is not None and self.editor.winfo_ismapped():
            t = self.player.position
            self.time_lbl.configure(text=self._tenths(t))
            self._draw_head(t)
            if self._shown_playing != self.player.is_playing:   # it reached the end
                self._show_playing()
        self._play_job = self.root.after(50, self._tick_playback)

    # -- the audio bar --
    def _wave_x(self, t):
        b = int(self.wave.cget("highlightthickness"))
        t0, t1 = self._wave_span
        frac = (t - t0) / (t1 - t0) if t1 > t0 else 0.0
        return b + max(0.0, min(1.0, frac)) * (self.wave.winfo_width() - 2 * b)

    def _wave_time(self, x):
        b = int(self.wave.cget("highlightthickness"))
        t0, t1 = self._wave_span
        frac = (x - b) / max(1, self.wave.winfo_width() - 2 * b)
        return t0 + max(0.0, min(1.0, frac)) * (t1 - t0)

    def _draw_wave(self):
        c = self.wave
        c.delete("all")
        w, h = c.winfo_width(), c.winfo_height()
        b = int(c.cget("highlightthickness"))
        if w < 4 or self.sel is None:
            return
        c.create_rectangle(0, 0, 0, 0, fill=TEXT, outline="", state="hidden", tags="region")
        x = self.player.source
        mid = h / 2
        self._wave_cols = None
        if len(x):
            cols = max(1, min(w - 2 * b, len(x)))
            idx = np.linspace(0, len(x), cols, endpoint=False).astype(int)
            hi, lo = np.maximum.reduceat(x, idx), np.minimum.reduceat(x, idx)
            amp = (mid - b - 2) / max(float(np.abs(x).max()), 0.05)
            xs = b + np.arange(cols) * (w - 2 * b) / cols
            top = mid - np.maximum(hi * amp, 0.5)
            bottom = mid - np.minimum(lo * amp, -0.5)
            points = np.concatenate((np.column_stack((xs, top)),
                                     np.column_stack((xs[::-1], bottom[::-1])))).ravel()
            c.create_polygon(*points.tolist(), fill=MUTED, outline=MUTED)
            self._wave_cols = (xs, top, bottom)   # for the white copy over a selected stretch
        c.create_line(0, 0, 0, 0, fill=DANGER, width=max(2, int(2 * self._scale)), tags="head")
        self._draw_region()
        self._draw_head(self.player.position)

    def _draw_head(self, t):
        x = self._wave_x(t)
        self.wave.coords("head", x, 0, x, self.wave.winfo_height())

    def _draw_region(self):
        """The selected stretch: near-black, with its part of the waveform drawn in white."""
        c = self.wave
        c.delete("region_wave")
        if self._region is None:
            c.itemconfigure("region", state="hidden")
            return
        xa, xb = (self._wave_x(t) for t in self._region)
        c.coords("region", xa, 0, xb, c.winfo_height())
        c.itemconfigure("region", state="normal")
        if self._wave_cols is not None:
            xs, top, bottom = self._wave_cols
            keep = (xs >= xa) & (xs <= xb)
            if keep.sum() >= 2:
                xs, top, bottom = xs[keep], top[keep], bottom[keep]
                points = np.concatenate((np.column_stack((xs, top)),
                                         np.column_stack((xs[::-1], bottom[::-1])))).ravel()
                c.create_polygon(*points.tolist(), fill=BG, outline=BG, tags="region_wave")
        c.tag_raise("head")

    def _set_region(self, region, source):
        self._region, self._region_src = region, (source if region else None)
        self._draw_region()
        if self.player.is_playing and self._loop:
            self.player.set_loop((region or self._wave_span)[0])

    def _wave_press(self, event):
        if self.sel is None or not len(self.player.source):
            return
        self._drag = (event.x, self._wave_time(event.x), False)

    def _wave_drag_to(self, event):
        if self._drag is None:
            return
        x0, t0, moved = self._drag
        if not moved and abs(event.x - x0) < 4 * self._scale:
            return
        self._drag = (x0, t0, True)
        if not moved:
            self.edit_box.tag_remove("sel", "1.0", "end")
        t = self._wave_time(event.x)
        self._set_region((min(t0, t), max(t0, t)), "bar")
        self._refresh_linked_words()

    def _wave_release(self, event):
        if self._drag is None:
            return
        _, t0, moved = self._drag
        self._drag = None
        self.edit_box.focus_set()
        if moved and self._region and self._region[1] - self._region[0] > 0.05:
            self._play(*self._region)
            return
        # A click: clear any selection and play from there.
        self.edit_box.tag_remove("sel", "1.0", "end")
        self.edit_box.tag_remove("linked", "1.0", "end")
        self._set_region(None, None)
        self.player.seek(t0)
        self._play(t0, self._wave_span[1])

    # -- linking the edit box and the audio bar --
    def _word_spans(self):
        par = self._pars[self.sel]
        return timing.timed_spans(self.pieces[par["i0"]:par["i1"]],
                                  self.edit_box.get("1.0", "end-1c"))

    def _on_word_double_click(self, event):
        """Move the playhead to the start of the double-clicked word.

        If audio is playing it carries on from there; otherwise it waits for Play.
        """
        if self.sel is None or not len(self.player.source):
            return "break"
        box = self.edit_box
        index = box.index(f"@{event.x},{event.y}")
        offset = len(box.get("1.0", index))
        spans = self._word_spans()
        word = (next((w for w in spans if w[0] <= offset < w[1]), None)
                or next((w for w in spans if w[0] >= offset), None))
        box.mark_set("insert", index)
        if word is None:
            return "break"
        box.mark_set("insert", f"1.0 + {word[0]} chars")
        start = max(self._wave_span[0], word[2])
        self._set_region(None, None)
        box.tag_remove("linked", "1.0", "end")
        if self.player.is_playing:
            self._play(start, self._wave_span[1])
        else:
            self.player.seek(start)
            self._draw_head(start)
            self.time_lbl.configure(text=self._tenths(start))
        return "break"   # a double-click moves the playhead; it doesn't select the word

    def _on_text_selection(self, _event=None):
        if self.sel is None:
            return
        box = self.edit_box
        selected = self._text_sel()
        self.spk_lbl.configure(text="Selection" if selected else "Speaker")
        if not selected:
            if self._region_src == "text":
                self._set_region(None, None)
            return
        a, b = selected
        words = [w for w in self._word_spans() if w[1] > a and w[0] < b]
        box.tag_remove("linked", "1.0", "end")
        if not words:
            self._set_region(None, None)
            return
        t0, t1 = self._wave_span
        start = max(t0, min(w[2] for w in words) - 0.5)
        end = min(t1, max(w[3] for w in words) + 0.5)
        self._set_region((start, end), "text")

    def _text_sel(self):
        """The edit box's selection as character offsets (start, end), or None."""
        box = self.edit_box
        try:
            a = len(box.get("1.0", box.index("sel.first")))
            b = len(box.get("1.0", box.index("sel.last")))
        except tk.TclError:
            return None
        return (a, b) if b > a else None

    def _on_speaker_choice(self, _event=None):
        """With words selected, give just those words to the chosen speaker."""
        self.spk_box.selection_clear()
        selected = self._text_sel()
        chosen = self._spk_map.get(self.spk_box.get())
        if self.sel is None or not selected or chosen is None:
            return
        self._set_mode(None)      # this can renumber paragraphs
        par = self._pars[self.sel]
        i0, i1 = par["i0"], par["i1"]
        spans = timing.retime_spans(self.pieces[i0:i1], self.edit_box.get("1.0", "end-1c"))
        a, b = selected
        for piece, c0, c1 in spans:
            if c1 > a and c0 < b:
                piece["speaker"] = chosen
        self.pieces[i0:i1] = [piece for piece, _, _ in spans]
        self.sel = None
        self._speakers_changed(f"Gave the selected words to {self._speaker_name(chosen)}.")
        k = self._par_at_piece(i0)
        if k is not None:
            self.open_editor(k)

    def _refresh_linked_words(self):
        """Highlight the words under the stretch selected on the audio bar."""
        if self.sel is None:
            return
        box = self.edit_box
        box.tag_remove("linked", "1.0", "end")
        if self._region_src != "bar":
            return
        a, b = self._region
        words = [(c0, c1) for c0, c1, s, e in self._word_spans() if s < b and e > a]
        if words:   # one span, so the spaces between the words are highlighted too
            box.tag_add("linked", f"1.0 + {words[0][0]} chars", f"1.0 + {words[-1][1]} chars")
