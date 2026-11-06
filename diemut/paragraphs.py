"""Merging paragraphs (merge mode), the right-click menu, what clicks in the transcript do,
and where each word sits in a paragraph's text.

ParagraphsMixin is part of App (see ui.py) and uses its state: pieces, _pars, sel, text,
the Merge button, and the correction panel's methods.
"""

import bisect

from . import fillers
from .notes import NOTE, is_note
from .theme import BG, TEXT, mix


class ParagraphsMixin:
    """Merge mode, the right-click menu and clicks in the transcript, as part of App."""

    def _text_cursor(self):
        if self._mode == "merge":
            return self._needle
        return "hand2" if (self.pieces and not self.busy) else "arrow"

    # -- clicks in the transcript --
    def _par_at(self, event):
        """The paragraph under the mouse in the transcript, or None."""
        index = self.text.index(f"@{event.x},{event.y}")
        for tag in self.text.tag_names(index):
            if tag.startswith("par:"):
                return int(tag.split(":")[1])
        return None

    def _on_text_press(self, event):
        self._end_flash()
        if self.busy or not self.pieces:
            return None
        k = self._par_at(event)
        if self._mode == "merge":
            # [paragraph, dragged?, Shift held?]; there's no text selection while merging
            self._press = [k, False, bool(event.state & 0x0001)] if k is not None else None
            return "break"
        self._press = [k, False, False]
        return None   # Tk's own binding starts a text selection, for copying

    def _on_text_seek_click(self, event):
        """Ctrl+Alt+click: open the paragraph with the playhead at exactly the spot clicked.

        If audio was playing, it carries on from there.
        """
        if self.busy or not self.pieces or self._mode == "merge" or self.audio is None:
            return "break"
        k = self._par_at(event)
        if k is None:
            return "break"
        par = self._pars[k]
        index = self.text.index(f"@{event.x},{event.y}")
        ranges = self.text.tag_ranges(f"ptext:{k}")
        if ranges and self.text.compare(index, ">=", ranges[0]):
            t = self._time_at_offset(par, len(self.text.get(ranges[0], index)))
        else:
            t = par["start"]   # the timestamp: the paragraph's start
        # Work out t first: opening another paragraph saves this one, which can renumber
        was_playing = self.player.is_playing
        if k != self.sel:
            self.open_editor(k)
        self._clear_play_region()
        if was_playing and not self.player.is_playing:
            self._play(t, self._wave_span[1])
        else:
            self._seek_to(t)
        return "break"

    def _on_text_drag(self, event):
        if self._mode != "merge" or not self._press:
            return None   # outside merge mode, dragging selects text
        k = self._par_at(event)
        if k is not None and (k != self._press[0] or self._press[1]):
            self._press[1] = True
            self._check_run(self._press[0], k)
        return "break"

    def _on_text_release(self, event):
        press, self._press = self._press, None
        if not press or press[0] is None:
            return
        k, dragged, shift = press
        if self._mode == "merge":
            if not dragged:
                self._merge_check(k, shift)
        elif not self.text.tag_ranges("sel") and self._par_at(event) == k:
            self.open_editor(k)   # a click, not a text selection (to copy)

    # -- merge mode --
    MERGE_HINT = ("Click or drag over the paragraphs to merge: next to each other, from the "
                  "same speaker. Shift+click checks a run. Cancel or Esc stops.")

    def _set_mode(self, mode):
        """Turn merge mode on ("merge") or off (None). The correction panel stays where it
        is, greyed out while merging."""
        if mode == self._mode:
            return
        self._mode = mode
        self._merge_range = self._merge_anchor = None
        self._press = None
        if mode == "merge" and self.sel is not None:
            self._stop_playback()
            self._clear_play_region()
            self.root.focus_set()       # so the panel's shortcuts don't work while merging
        self._enable_panel(mode != "merge")
        self._draw_merge()
        self._show_open_paragraph(scroll=False)    # hidden while merging, back afterwards
        self.text.configure(cursor=self._text_cursor())
        self._update_par_tools()
        self.status_lbl.configure(text=self.MERGE_HINT if mode == "merge" else "")

    def toggle_merge_mode(self):
        """Merge, or Cancel while merging. The paragraphs selected in the transcript (by
        dragging over them) start out checked; with none, the open paragraph does."""
        if self._mode == "merge":
            self._set_mode(None)
            return
        # Note the pieces first: saving the open correction can renumber paragraphs
        t, picked = self.text, None
        if t.tag_ranges("sel"):
            touched = [par["i0"] for k, par in enumerate(self._pars)
                       if (r := t.tag_ranges(f"par:{k}")) and t.compare(r[0], "<", "sel.last")
                       and t.compare(r[-1], ">", "sel.first")]
            picked = (touched[0], touched[-1]) if touched else None
            t.tag_remove("sel", "1.0", "end")
        i0 = self._save_edit()
        if picked is None and i0 is not None:
            picked = (i0, i0)
        self._set_mode("merge")
        if picked is not None:
            a, b = (self._par_at_piece(i) for i in picked)
            self._check_run(a, b)      # stops at a speaker change, and says so

    def _mergeable(self, a, b):
        """True if paragraphs a to b are next to each other and all the same speaker."""
        return all(self._same_speaker(k, k + 1) for k in range(a, b))

    def _set_merge_range(self, new, anchor):
        self._merge_range, self._merge_anchor = new, anchor
        self._draw_merge()
        self._update_par_tools()
        n = new[1] - new[0] + 1 if new else 0
        self.status_lbl.configure(text=f"{n} paragraph{'' if n == 1 else 's'} checked." if n
                                  else "Check the paragraphs to merge.")

    def _check_run(self, k0, k):
        """Check the paragraphs dragged over, from k0 towards k, stopping at a speaker change."""
        step, end = (1 if k > k0 else -1), k0
        while end != k and self._same_speaker(end, end + step):
            end += step
        self._set_merge_range(tuple(sorted((k0, end))), k0)
        if end != k:
            self.status_lbl.configure(text="Stopped where the speaker changes: only one "
                                           "speaker's paragraphs can be merged.")

    def _merge_check(self, k, shift=False):
        """Check or uncheck paragraph k; with shift, check the run from the last one clicked.

        The checked paragraphs always stay one run, next to each other, from one speaker.
        """
        current, anchor = self._merge_range, self._merge_anchor
        if shift and anchor is not None and anchor != k:
            lo, hi = sorted((anchor, k))
            if current and lo <= current[1] + 1 and hi >= current[0] - 1:
                lo, hi = min(lo, current[0]), max(hi, current[1])
            if not self._mergeable(lo, hi):
                self.status_lbl.configure(text="Shift+click checks a run within one speaker's "
                                               "paragraphs.")
                return
            new = (lo, hi)
        elif not current:
            new = (k, k)
        else:
            a, b = current
            if k == a == b:
                new = None
            elif k in (a, b):
                new = (a + 1, b) if k == a else (a, b - 1)
            elif a < k < b:
                self.status_lbl.configure(text="To leave a paragraph out, uncheck from the first "
                                               "or last checked one.")
                return
            elif not self._mergeable(min(a, k), max(b, k)):
                self.status_lbl.configure(text="Only paragraphs next to each other, from the "
                                               "same speaker, can be merged.")
                return
            elif k not in (a - 1, b + 1):
                self.status_lbl.configure(text="To check the paragraphs in between too, hold "
                                               "Shift as you click, or drag over them.")
                return
            else:
                new = (min(a, k), max(b, k))
        self._set_merge_range(new, k)

    def _draw_merge(self):
        """In merge mode, the checked paragraphs are white on black, like a selection. Only
        tags change, so the text doesn't move."""
        t = self.text
        t.tag_remove("merging", "1.0", "end")
        if self._mode == "merge" and self._merge_range:
            a, b = self._merge_range
            for k in range(a, b + 1):
                ranges = t.tag_ranges(f"par:{k}")
                if ranges:
                    t.tag_add("merging", ranges[0], ranges[-1])

    def _update_par_tools(self):
        """The centred row: Merge (faded with only one paragraph), or in merge mode,
        Merge N paragraphs and Cancel."""
        if self.busy or not self.pieces:
            self.par_tools.place_forget()
            return
        self.par_tools.place(relx=0.5, rely=0, anchor="n")   # centred, top-aligned like the rest
        for w in self.par_tools.winfo_children():
            w.pack_forget()
        if self._mode == "merge":
            a, b = self._merge_range or (0, -1)
            n = b - a + 1
            self.merge_sel_btn.configure(text=f"Merge {n} paragraphs" if n > 1 else "Merge",
                                         state="normal" if n > 1 else "disabled")
            self.merge_sel_btn.pack(side="left", padx=(0, 6))
            self.merge_cancel_btn.pack(side="left")
        else:
            self.merge_mode_btn.configure(state="normal" if len(self._pars) > 1 else "disabled")
            self.merge_mode_btn.pack(side="left")

    def merge_selected(self):
        if self._mode != "merge" or not self._merge_range:
            return
        a, b = self._merge_range
        if b <= a or not self._mergeable(a, b):
            return
        marks = [(self._pars[k]["i0"], "join") for k in range(a + 1, b + 1)]
        keep = self._pars[a]["i0"]
        was_open = self.sel is not None
        self._set_mode(None)
        self._apply_marks(marks, keep, f"{b - a + 1} paragraphs merged.", reopen=was_open)

    def _apply_marks(self, marks, keep, status, reopen=True):
        """Split or merge paragraphs: set "par" marks on pieces, then (with reopen) open the
        paragraph holding piece keep. Any open correction must be saved first."""
        splits = all(mark == "start" for _, mark in marks)
        self._checkpoint("paragraph split" if splits else "paragraph merge")
        for i, mark in marks:
            self.pieces[i]["par"] = mark
        self.dirty = True
        self.sel = None   # paragraph numbers change, so don't let open_editor save into one
        self._render_all(anchor=self._view_anchor(keep))     # keep's paragraph stays put
        k = self._par_at_piece(keep)
        if k is not None and reopen:
            self.open_editor(k)
        if k is not None:       # the paragraphs that changed: one merged, or both halves
            self._flash(k, self._par_at_piece(max(i for i, _ in marks)))
        self.status_lbl.configure(text=status)

    # -- keeping the view still, and showing what just changed --
    # A merge's paragraphs are already white on black while checked: they stay that way
    FLASH_HOLD_MS = 500       # for this long after the merge…
    FLASH_STEPS, FLASH_STEP_MS = 8, 60     # …then faded back in these steps

    def _flash(self, k0, k1):
        """Show paragraphs k0 to k1 white on black for a moment, then fade them back."""
        t = self.text
        self._end_flash()
        for k in range(k0, k1 + 1):
            ranges = t.tag_ranges(f"par:{k}")
            if ranges:
                t.tag_add("flash", ranges[0], ranges[-1])

        def step(n):
            if n > self.FLASH_STEPS:
                self._end_flash()
                return
            f = n / self.FLASH_STEPS
            t.tag_configure("flash", background=mix(TEXT, BG, f), foreground=mix(BG, TEXT, f))
            self._flash_job = self.root.after(self.FLASH_STEP_MS, step, n + 1)

        t.tag_configure("flash", background=TEXT, foreground=BG)
        self._flash_job = self.root.after(self.FLASH_HOLD_MS, step, 1)

    def _end_flash(self):
        if self._flash_job is not None:
            self.root.after_cancel(self._flash_job)
            self._flash_job = None
        self.text.tag_remove("flash", "1.0", "end")

    def _piece_index(self, i):
        """Where piece i starts in the transcript (a Text index), or None."""
        k = self._par_at_piece(i)
        if k is None:
            return None
        ranges = self.text.tag_ranges(f"ptext:{k}")
        c0 = next((c0 for j, c0, _ in self._piece_spans(self._pars[k]) if j >= i), 0)
        return f"{ranges[0]} + {c0} chars" if ranges else None

    def _view_anchor(self, i=None):
        """(piece, pixels from the top of the transcript box, or None if it's off screen),
        for _restore_view: piece i, or the first word showing at the top."""
        t = self.text
        if not self._pars:
            return None
        if i is None:
            top = t.index("@0,0")
            for k, par in enumerate(self._pars):
                ranges = t.tag_ranges(f"ptext:{k}")
                if ranges and t.compare(ranges[-1], ">", top):
                    inside = t.compare(top, ">", ranges[0])
                    i = self._piece_at_offset(par, len(t.get(ranges[0], top)) if inside else 0)
                    break
            else:
                return None
        index = self._piece_index(i)
        line = t.dlineinfo(index) if index else None
        return i, (line[1] if line else None)

    def _restore_view(self, anchor):
        """Scroll so anchor's piece is where it was on screen (or at the top, if it was off
        screen), after a redraw."""
        index = self._piece_index(anchor[0]) if anchor else None
        if index is None:
            return
        t = self.text
        t.yview(index)
        if anchor[1] is not None:
            t.update_idletasks()
            line = t.dlineinfo(index)
            if line:
                t.yview_scroll(line[1] - anchor[1], "pixels")

    def _on_text_menu(self, event):
        if self.busy or not self.pieces:
            return
        if self.text.tag_ranges("sel"):
            menu = self.par_menu
            menu.delete(0, "end")
            menu.add_command(label="Copy", command=lambda: self.text.event_generate("<<Copy>>"))
            menu.tk_popup(event.x_root, event.y_root)
            return "break"
        self._set_mode(None)
        self._close_editing()      # can renumber paragraphs, so find the clicked one after
        index = self.text.index(f"@{event.x},{event.y}")
        k = self._par_at(event)
        if k is None:
            return
        par, menu = self._pars[k], self.par_menu
        menu.delete(0, "end")
        ranges = self.text.tag_ranges(f"ptext:{k}")
        if ranges and self.text.compare(index, ">=", ranges[0]):
            i = self._piece_at_offset(par, len(self.text.get(ranges[0], index)))
            word = self.pieces[i]["text"].strip()
            if is_note(word):
                menu.add_command(label="Delete note", command=lambda: self._delete_note(i))
            else:
                menu.add_command(label=f"Add note after “{word}”",
                                 command=lambda: self._add_note_after(i))
            menu.add_separator()
            if i > par["i0"]:
                menu.add_command(label=f"Split paragraph before “{word}”", command=lambda: (
                    self._apply_marks([(i, "start")], par["i0"], "Paragraph split.")))
        for other, label in ((k - 1, "Merge with paragraph above"),
                             (k + 1, "Merge with paragraph below")):
            if self._same_speaker(k, other):
                a, b = sorted((par["i0"], self._pars[other]["i0"]))
                menu.add_command(label=label, command=lambda a=a, b=b: (
                    self._apply_marks([(b, "join")], a, "Paragraphs merged.")))
        if menu.index("end") is None or menu.type("end") == "separator":
            menu.add_command(label="Nothing to split or merge here", state="disabled")
        undo, redo = self.history.undo_label(), self.history.redo_label()
        if undo or redo:
            menu.add_separator()
        if undo:
            menu.add_command(label=f"Undo {undo}", accelerator="Ctrl+Z", command=self.undo)
        if redo:
            menu.add_command(label=f"Redo {redo}", accelerator="Ctrl+Y",
                             command=lambda: self.undo(redo=True))
        menu.tk_popup(event.x_root, event.y_root)
        return "break"

    def _same_speaker(self, k, other):
        """True if paragraph other is next to paragraph k and has the same speaker."""
        if not 0 <= other < len(self._pars):
            return False
        a, b = sorted((self._pars[k], self._pars[other]), key=lambda par: par["i0"])
        return a["i1"] == b["i0"] and (self.pieces[a["i0"]].get("speaker")
                                       == self.pieces[b["i0"]].get("speaker"))

    def _piece_spans(self, par):
        """(piece index, char start, char end) of each piece in a paragraph's displayed text.

        The transcript shows the pieces joined and with runs of whitespace made single spaces
        (speakers.group_blocks), so this follows the same steps. A piece without a leading
        space, like "'t" after "don", sits right against the one before it.
        """
        out, pos, space = [], 0, False
        for i in range(par["i0"], par["i1"]):
            start = None
            for ch in self.pieces[i]["text"]:
                if ch.isspace():
                    space = pos > 0          # one space, and none at the very start
                    continue
                if space:
                    pos, space = pos + 1, False
                if start is None:
                    start = pos
                pos += 1
            out.append((i, pos if start is None else start, pos))
        return out

    def _piece_at_offset(self, par, offset):
        """The piece at a character offset in a paragraph's displayed text."""
        return next((i for i, _, c1 in self._piece_spans(par) if offset <= c1), par["i1"] - 1)

    def _time_at_offset(self, par, offset):
        """The time at a character offset in a paragraph's displayed text: part-way into a
        word by character, or the next word's start if the offset is in the space after one."""
        for i, c0, c1 in self._piece_spans(par):
            if offset < c1:
                p = self.pieces[i]
                return p["start"] + (p["end"] - p["start"]) * max(0, offset - c0) / max(1, c1 - c0)
        return self.pieces[par["i1"] - 1]["end"]

    def _grey_fillers(self):
        """Show fillers and false starts in grey all through the transcript."""
        t = self.text
        drop = fillers.removable(self.pieces)
        ordered = sorted(drop)
        for k, par in enumerate(self._pars):
            j = bisect.bisect_left(ordered, par["i0"])
            if j == len(ordered) or ordered[j] >= par["i1"]:
                continue        # no fillers here: skip working out where its words are
            ranges = t.tag_ranges(f"ptext:{k}")
            for i, c0, c1 in self._piece_spans(par) if ranges else ():
                if i in drop:
                    t.tag_add("filler", f"{ranges[0]} + {c0} chars", f"{ranges[0]} + {c1} chars")

    def _mark_notes(self):
        """Highlight the notes ([laughs]) all through the transcript."""
        t = self.text
        for k, par in enumerate(self._pars):
            if "[" not in par["text"]:
                continue
            start = t.tag_ranges(f"ptext:{k}")[0]
            for m in NOTE.finditer(par["text"]):
                a, b = m.span()
                t.tag_add("note", f"{start} + {a} chars", f"{start} + {b} chars")
                t.tag_add("note_i", f"{start} + {a + 1} chars", f"{start} + {b - 1} chars")

    def _add_note_after(self, i):
        """Open piece i's paragraph with an empty note typed after it, ready to fill in."""
        k = self._par_at_piece(i)
        self.open_editor(k)
        end = next(c1 for j, _, c1 in self._piece_spans(self._pars[k]) if j == i)
        self.edit_box.mark_set("insert", f"1.0 + {end} chars")
        self._insert_note()

    def _delete_note(self, i):
        """Take note i out of the transcript (one undo step)."""
        anchor = self._view_anchor(i)
        self._checkpoint("note deletion")
        note = self.pieces.pop(i)
        if note.get("par") and i < len(self.pieces) and "par" not in self.pieces[i]:
            self.pieces[i]["par"] = note["par"]       # so the paragraphs stay as they were
        self.dirty = True
        self._render_all(anchor=anchor)
        self.status_lbl.configure(text="Note deleted. Ctrl+Z brings it back.")

    def _par_at_piece(self, i):
        """The paragraph holding piece i (or the next one, if i is past its end)."""
        for k, par in enumerate(self._pars):
            if par["i0"] <= i < par["i1"]:
                return k
        for k, par in enumerate(self._pars):
            if par["i0"] >= i:
                return k
        return len(self._pars) - 1 if self._pars else None
