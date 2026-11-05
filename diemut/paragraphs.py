"""Merging and splitting paragraphs, and what clicks in the transcript do.

ParagraphsMixin is part of App (see ui.py) and uses its state: pieces, _pars, sel, text,
the Merge and Split buttons, and the correction panel's methods.
"""


class ParagraphsMixin:
    """Merge and Split, and clicks in the transcript, as part of App."""

    def _text_cursor(self):
        if self._mode == "split":
            return self._sword
        if self._mode == "merge":
            return self._needle
        return "hand2" if (self.pieces and not self.busy) else "arrow"

    # -- clicks in the transcript --
    def _par_at(self, event):
        """The paragraph under the mouse in the transcript (its check box counts), or None."""
        index = self.text.index(f"@{event.x},{event.y}")
        for tag in self.text.tag_names(index):
            if tag.startswith(("par:", "check:")):
                return int(tag.split(":")[1])
        return None

    def _on_text_press(self, event):
        if self.busy or not self.pieces:
            return None
        if self._mode == "split":
            self._split_at_click(event)
            return "break"
        k = self._par_at(event)
        if self._mode == "merge":
            # [paragraph, dragged?, Shift held?]; there's no text selection while merging
            self._press = [k, False, bool(event.state & 0x0001)] if k is not None else None
            return "break"
        self._press = [k, False, False]
        return None   # Tk's own binding starts a text selection, for copying

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
        if not press or press[0] is None or self._mode == "split":
            return
        k, dragged, shift = press
        if self._mode == "merge":
            if not dragged:
                self._merge_check(k, shift)
        elif not self.text.tag_ranges("sel") and self._par_at(event) == k:
            self.open_editor(k)   # a click, not a text selection (to copy)

    # -- merge and split modes --
    MODE_HINTS = {
        "merge": "Click or drag over the paragraphs to merge: next to each other, from the same "
                 "speaker. Shift+click checks a run. Merge again or Esc stops.",
        "split": "Click the word in the highlighted paragraph where the new paragraph should "
                 "start. Split again or Esc stops.",
    }

    def _set_mode(self, mode):
        """Turn merge or split mode on ("merge" / "split"), or whichever is on off (None)."""
        if mode == self._mode:
            return
        self._mode = mode
        self._merge_range = self._merge_anchor = None
        self._press = None
        self._draw_checks()
        self.text.configure(cursor=self._text_cursor())
        self._update_par_tools()
        self.status_lbl.configure(text=self.MODE_HINTS.get(mode, ""))

    def toggle_merge_mode(self):
        if self._mode == "merge":
            self._set_mode(None)
            return
        self._set_mode(None)
        i0 = self._close_editing()
        self._set_mode("merge")
        if i0 is not None:      # start from the paragraph that was open
            k = self._par_at_piece(i0)
            self._set_merge_range((k, k), k)

    def toggle_split_mode(self):
        if self._mode == "split":
            self._set_mode(None)
            return
        if self.sel is None:
            return
        self._set_mode(None)
        self._save_edit()
        if self.sel is not None:
            self._set_mode("split")

    def _mergeable(self, a, b):
        """True if paragraphs a to b are next to each other and all the same speaker."""
        return all(self._same_speaker(k, k + 1) for k in range(a, b))

    def _set_merge_range(self, new, anchor):
        self._merge_range, self._merge_anchor = new, anchor
        self._draw_checks()
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

    def _draw_checks(self):
        """A ☐ / ☑ in front of each paragraph in merge mode; none otherwise."""
        t = self.text
        t.configure(state="normal")
        ranges = t.tag_ranges("check")
        for i in range(len(ranges) - 2, -1, -2):
            t.delete(ranges[i], ranges[i + 1])
        if self._mode == "merge":
            a, b = self._merge_range or (-1, -2)
            for k in range(len(self._pars)):
                ranges = t.tag_ranges(f"par:{k}")
                if ranges:
                    checked = a <= k <= b
                    t.insert(ranges[0], ("☑" if checked else "☐") + " ",
                             # "para": a line's spacing comes from its first character
                             ("check", f"check:{k}", "checked" if checked else "unchecked",
                              "para"))
        t.configure(state="disabled")

    def _update_par_tools(self):
        """Merge and Split: shown with a transcript, faded when they can't be used, red when on.

        In merge mode, Merge N paragraphs floats at the top of the transcript.
        """
        if self.busy or not self.pieces:
            self.par_tools.place_forget()
            self.merge_sel_btn.place_forget()
            return
        self.par_tools.place(relx=0.5, rely=0, anchor="n")   # centred, top-aligned like the rest
        par = self._pars[self.sel] if self.sel is not None else None
        for btn, mode, usable in (
                (self.merge_mode_btn, "merge", len(self._pars) > 1),
                (self.split_btn, "split", par is not None and par["i1"] - par["i0"] > 1)):
            on = self._mode == mode
            btn.state(["selected" if on else "!selected",
                       "!disabled" if on or usable else "disabled"])
        if self._mode == "merge":
            a, b = self._merge_range or (0, -1)
            n = b - a + 1
            self.merge_sel_btn.configure(text=f"Merge {n} paragraphs" if n > 1 else "Merge",
                                         state="normal" if n > 1 else "disabled")
            self.merge_sel_btn.place(relx=0.5, rely=0.0, y=10, anchor="n")
            self.merge_sel_btn.lift()
        else:
            self.merge_sel_btn.place_forget()

    def merge_selected(self):
        if self._mode != "merge" or not self._merge_range:
            return
        a, b = self._merge_range
        if b <= a or not self._mergeable(a, b):
            return
        marks = [(self._pars[k]["i0"], "join") for k in range(a + 1, b + 1)]
        keep = self._pars[a]["i0"]
        self._set_mode(None)
        self._apply_marks(marks, keep, f"{b - a + 1} paragraphs merged.")

    def _split_at_click(self, event):
        k = self._par_at(event)
        if k is None or k != self.sel:
            self.status_lbl.configure(text="Click a word in the highlighted paragraph, or press "
                                           "Esc to stop splitting.")
            return
        par = self._pars[k]
        index = self.text.index(f"@{event.x},{event.y}")
        ranges = self.text.tag_ranges(f"ptext:{k}")
        if not ranges or self.text.compare(index, "<", ranges[0]):
            return
        i = self._piece_at_offset(par, len(self.text.get(ranges[0], index)))
        if i <= par["i0"]:
            self.status_lbl.configure(text="The paragraph already starts there. Click a later "
                                           "word.")
            return
        self._set_mode(None)
        self._apply_marks([(i, "start")], par["i0"], "Paragraph split.")

    def _apply_marks(self, marks, keep, status):
        """Split or merge paragraphs: set "par" marks on pieces, then open the paragraph
        holding piece keep. Any open correction must be saved first."""
        for i, mark in marks:
            self.pieces[i]["par"] = mark
        self.dirty = True
        self.sel = None   # paragraph numbers change, so don't let open_editor save into one
        self._render_all()
        k = self._par_at_piece(keep)
        if k is not None:
            self.open_editor(k)
        self.status_lbl.configure(text=status)

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
            if i > par["i0"]:
                word = self.pieces[i]["text"].strip()
                menu.add_command(label=f"Split paragraph before “{word}”", command=lambda: (
                    self._apply_marks([(i, "start")], par["i0"], "Paragraph split.")))
        for other, label in ((k - 1, "Merge with paragraph above"),
                             (k + 1, "Merge with paragraph below")):
            if self._same_speaker(k, other):
                a, b = sorted((par["i0"], self._pars[other]["i0"]))
                menu.add_command(label=label, command=lambda a=a, b=b: (
                    self._apply_marks([(b, "join")], a, "Paragraphs merged.")))
        if menu.index("end") is None:
            menu.add_command(label="Nothing to split or merge here", state="disabled")
        menu.tk_popup(event.x_root, event.y_root)
        return "break"

    def _same_speaker(self, k, other):
        """True if paragraph other is next to paragraph k and has the same speaker."""
        if not 0 <= other < len(self._pars):
            return False
        a, b = sorted((self._pars[k], self._pars[other]), key=lambda par: par["i0"])
        return a["i1"] == b["i0"] and (self.pieces[a["i0"]].get("speaker")
                                       == self.pieces[b["i0"]].get("speaker"))

    def _piece_at_offset(self, par, offset):
        """The piece at a character offset in a paragraph's displayed text."""
        spaced = " " in par["text"]
        pos = 0
        for i in range(par["i0"], par["i1"]):
            text = self.pieces[i]["text"]
            pos += len(" ".join(text.split()) if spaced else text.strip()) + (1 if spaced else 0)
            if offset < pos:
                return i
        return par["i1"] - 1

    def _par_at_piece(self, i):
        """The paragraph holding piece i (or the next one, if i is past its end)."""
        for k, par in enumerate(self._pars):
            if par["i0"] <= i < par["i1"]:
                return k
        for k, par in enumerate(self._pars):
            if par["i0"] >= i:
                return k
        return len(self._pars) - 1 if self._pars else None
