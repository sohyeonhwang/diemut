"""Several files at once, as part of App: starting a batch, switching between its files,
cancelling, and saving each file as its session when it's done.

BatchMixin is part of App (see ui.py). App.batch is a batch.Batch while the transcript screen
shows one, else None. The file being transcribed reports through its own session id
(Batch.sid) into App._job, which keeps its words, audio and so on whether or not it's on
screen; when it is on screen, App.pieces is the same list, so the words also appear live.
App.busy keeps its meaning: the transcript on screen is still being made.
"""

import datetime as dt
import os
import threading
from tkinter import messagebox

from . import sessions, transcribe
from .batch import CANCELLED, DONE, ERROR, QUEUED, RUNNING, Batch
from .dialogs import ask_batch, ask_choice
from .export import fmt_time
from .theme import FILE_ICON, FONTS

PLACEHOLDER = "Your transcript will appear here as diemut works through the file."


def _ordinal(n):
    return {1: "next", 2: "2nd", 3: "3rd"}.get(n, f"{n}th")


class BatchMixin:
    """Batches of files, as part of App."""

    # -------------------------------------------------------- starting --
    def start_batch(self, paths, folder):
        if not os.path.isdir(folder) or not os.access(folder, os.W_OK):
            messagebox.showerror("Can't save there",
                                 f"diemut can't save sessions in {folder}. Choose another "
                                 "folder.")
            return
        self.reset_transcript()
        self.batch = Batch.from_files(paths, folder)
        self._batch_job_settings = (self.model_name, self.language, self.num_speakers)
        self._show_transcript("file")
        self._start_next()

    def open_sessions(self, paths):
        """Several saved sessions at once: the same file list, every file done."""
        self.reset_transcript()
        self.batch = Batch.from_sessions(paths)
        self._show_transcript("file")
        self._display_item(self.batch.items[0])

    def _start_next(self):
        """Start transcribing the next queued file, or finish the batch."""
        item = self.batch.next_queued()
        if item is None:
            self._batch_finished()
            return
        model, language, num_speakers = self._batch_job_settings
        item.status, item.stage, item.progress = RUNNING, None, 0.0
        item.stop = threading.Event()
        self._batch_seq = getattr(self, "_batch_seq", 0) + 1
        sid = self.batch.sid = ("batch", self._batch_seq)
        self._job = {"item": item, "pieces": [], "audio": None, "duration": None,
                     "language": None, "speaker_count": 0, "started": dt.datetime.now()}
        self._job_thread = threading.Thread(
            target=transcribe.file_worker, daemon=True,
            args=(self.stt, item.source, model, language,
                  lambda kind, payload: self.ui_q.put((sid, kind, payload)), item.stop,
                  num_speakers))
        self._job_thread.start()
        if self._job_shown():
            self._display_item(item)
        self._refresh_batch_header()

    def _job_shown(self):
        return self._job is not None and self.batch.items[self.batch.shown] is self._job["item"]

    # ----------------------------------------- the running file's reports --
    def _handle_batch(self, kind, payload):
        job = self._job
        if job is None:
            return
        item, shown = job["item"], self._job_shown()
        if kind == "status":
            if shown:
                self.status_lbl.configure(text=payload)
        elif kind == "stage":
            item.stage, item.progress = payload, 0.0
            if shown:
                self.stage = payload
                self.progress.configure(value=0)
        elif kind == "progress":
            item.progress = payload
            if shown:
                self.progress.configure(value=payload)
                label = "Transcribing…" if item.stage == "transcribing" else "Finding speakers…"
                self.status_lbl.configure(text=f"{label} {payload:.0f}%")
        elif kind == "duration":
            job["duration"] = payload
            if shown:
                self.duration = payload
                self.sub_lbl.configure(text=fmt_time(payload))
        elif kind == "device":
            job["device"] = payload
            if shown:
                self.session_device = payload
                self._update_header_tags()
        elif kind == "language":
            job["language"] = payload
            if shown:
                self.detected_language = payload
                self._update_header_tags()
        elif kind == "audio":
            job["audio"] = payload
            if shown:
                self.audio = payload
        elif kind == "piece":
            if shown:
                self._append_piece(payload)      # self.pieces is job["pieces"]
            else:
                job["pieces"].append(payload)
        elif kind == "reset":
            del job["pieces"][:]
            if shown:
                self._render_all("")
        elif kind == "speakers":
            ids, count = payload
            for piece, spk in zip(job["pieces"], ids):
                piece["speaker"] = spk
            job["speaker_count"] = count
            if shown:
                self.speaker_count, self.names = count, {}
                self._render_all()
                self._update_header_tags()
        elif kind in ("done", "error"):
            self._finish_job(kind, payload)
        elif kind == "saved":
            if payload is None:
                item.status = DONE
            else:
                item.status = ERROR
                item.error = (f"diemut couldn't save its session in {self.batch.folder}. "
                              f"Check that the folder can be written to.\n\nDetails: {payload}")
            self._after_job()
        self._refresh_batch_header()

    def _finish_job(self, kind, payload):
        """The worker is done: save the session (in the background), or note why not."""
        job, item = self._job, self._job["item"]
        item.stage = None
        if kind == "error":
            item.status, item.error = ERROR, self._friendly_error(payload)
        elif item.cancel:
            item.status = CANCELLED
        elif not job["pieces"]:
            item.status, item.error = ERROR, "No speech was found in this file."
        else:
            item.note, item.stage = payload, "saving"
            model, language, _ = self._batch_job_settings
            state = sessions.make_state("file", item.source, job["started"], model, language,
                                        job["language"], job["duration"], job["speaker_count"],
                                        {}, [dict(p) for p in job["pieces"]])
            sid, clip = self.batch.sid, job["audio"]

            def save():
                try:
                    sessions.save(item.out, state, clip)
                    self.ui_q.put((sid, "saved", None))
                except Exception as exc:
                    self.ui_q.put((sid, "saved", str(exc)))
            threading.Thread(target=save, daemon=True).start()
            if self._job_shown():
                self.status_lbl.configure(text="Saving…")
            return
        self._after_job()

    def _after_job(self):
        """The running file has finished (saved, failed or cancelled): show how it ended if
        it's on screen, then go on to the next."""
        item, shown = self._job["item"], self._job_shown()
        item.stage = None
        self._job = None
        if shown:
            if item.status == DONE:
                # Its words are already on screen: it just becomes its saved session
                self.busy = False
                self._relabel = False
                self.progress.pack_forget()
                self.session_path = item.out
                self.dirty = False
                self._drop_recovery()
                self._render_all()
                self._update_controls()
                self._update_header_tags()
                self.status_lbl.configure(text=item.note or "Done.")
            else:
                self._display_item(item)
        self._start_next()

    def _batch_finished(self):
        self.batch.sid = None
        done = sum(it.status == DONE for it in self.batch.items)
        n = len(self.batch.items)
        text = f"Finished: {done} of {n} files saved in {self.batch.folder}."
        if any(it.status == ERROR for it in self.batch.items):
            text += " Pick a file marked error in the list above to see what went wrong."
        self.status_lbl.configure(text=text)
        self._update_controls()

    # -------------------------------------------------------- switching --
    def _show_item(self, i):
        """Switch to file i of the batch, saving the one on screen first."""
        if i == self.batch.shown or not self._save_shown():
            return
        self.batch.shown = i
        self._display_item(self.batch.items[i])

    def _save_shown(self):
        """Save the file on screen if it has changes: to its session without asking, or (with
        none yet) by asking as usual. False if the person cancels."""
        self._save_edit()
        if self.busy or not self.dirty or not self.pieces:
            return True
        if self.session_path:
            return self.save_session()
        return self._confirm_discard("switch files")

    def _display_item(self, item):
        """Show a file of the batch: its session, the words so far, or why there are none."""
        self._set_mode(None)
        if item.status == DONE:
            self.root.configure(cursor="watch")
            self.root.update_idletasks()
            try:
                state, clip = sessions.load(item.out)
            except sessions.SessionError as exc:
                item.status, item.error = ERROR, str(exc)
            else:
                self._apply_session(state, clip, item.out)
                self.status_lbl.configure(text=item.note or f"Opened {item.name}")
                self._refresh_batch_header()
                return
            finally:
                self.root.configure(cursor="")
        self.reset_transcript()
        self.busy = False
        self.progress.pack_forget()
        self.source_path = item.source
        self._show_transcript("file")
        self.sub_lbl.configure(text="")
        if item.status == RUNNING:
            job = self._job
            self.pieces, self.audio = job["pieces"], job["audio"]
            self.duration, self.detected_language = job["duration"], job["language"]
            self.session_device = job.get("device")
            self.speaker_count, self.session_start = job["speaker_count"], job["started"]
            self.session_model, self.session_language, _ = self._batch_job_settings
            self.busy, self.stage = True, item.stage
            self.progress.configure(value=item.progress)
            self.progress.pack(side="right", padx=(0, 10))
            if self.duration:
                self.sub_lbl.configure(text=fmt_time(self.duration))
            self.dirty = bool(self.pieces)
            self._render_all(PLACEHOLDER)
            self.status_lbl.configure(text="Transcribing…" if item.stage else "Starting…")
        else:
            if item.status == QUEUED:
                text = (f"Queued: {_ordinal(self.batch.queue_place(item))} in line. Cancel "
                        "takes it out of the batch.")
            elif item.status == ERROR:
                text = f"diemut couldn't transcribe {item.name}.\n\n{item.error or ''}"
            else:
                text = "This file was cancelled. Add it with New file ↗ to transcribe it."
            self._render_all(text)
            self.status_lbl.configure(text="")
        self._update_controls()
        self._update_header_tags()
        self._refresh_batch_header()

    def _refresh_batch_header(self):
        if self.batch is not None:
            item = self.batch.items[self.batch.shown]
            name = item.name if len(item.name) <= 48 else item.name[:47] + "…"
            self.file_btn.configure(text=f"{FILE_ICON} {name}")
            self.batch_lbl.configure(text=self.batch.summary())

    def _fill_file_menu(self):
        """The file list, made afresh each time it opens."""
        menu = self.file_menu
        menu.delete(0, "end")
        for i, it in enumerate(self.batch.items):
            bold = {"font": FONTS["header"]} if i == self.batch.shown else {}
            menu.add_command(label=it.name, accelerator=it.menu_status(),
                             command=lambda i=i: self._show_item(i), **bold)

    # ------------------------------------------------- cancelling, leaving --
    def _cancel_in_batch(self):
        """Cancel: just the file on screen, or the whole batch."""
        batch = self.batch
        item = batch.items[batch.shown]
        both = [("one", "Cancel this file"), ("all", "Cancel all"), ("keep", "Keep going")]
        if item.status == RUNNING:
            message = f"Stop transcribing {item.name}?"
            if batch.next_queued() is not None:
                message += " The batch moves on to the next file if you cancel just this one."
            choices = both
        elif item.status == QUEUED:
            message, choices = f"Take {item.name} out of the batch?", both
        else:
            done = sum(it.status == DONE for it in batch.items)
            message = (f"Stop the batch? The finished files ({done}) are saved; the other "
                       f"{batch.left()} won't be transcribed.")
            choices = [("all", "Cancel all"), ("keep", "Keep going")]
        choice = ask_choice(self.root, "Cancel", message, choices)
        if self.batch is not batch:
            return
        if choice == "one" and item.status == RUNNING:
            self._drop_running()
        elif choice == "one" and item.status == QUEUED:
            item.status = CANCELLED
            self._display_item(item)
        elif choice == "all":
            was_running = item.status == RUNNING     # (_drop_running shows that one itself)
            self._stop_batch()
            if not was_running and item.status == CANCELLED:
                self._display_item(item)
        self._update_controls()
        self._refresh_batch_header()

    def _stop_batch(self):
        if self.batch.cancel_all() is not None and self._job is not None:
            self._drop_running()

    def _drop_running(self):
        """Cancel the file being transcribed, straight away: it's marked cancelled now and
        anything its worker still reports is ignored (Whisper only notices the stop between
        stretches of audio, and loading or reading the file can't be stopped at all). The
        next file starts once that worker has finished."""
        item, shown, thread = self._job["item"], self._job_shown(), self._job_thread
        item.cancel = True
        item.stop.set()
        item.status, item.stage = CANCELLED, None
        self.batch.sid = None
        self._job = None
        if shown:
            self._display_item(item)
        batch = self.batch

        def start_when_stopped():
            if self.batch is not batch:          # the batch was left meanwhile
                return
            if thread.is_alive():
                self.root.after(200, start_when_stopped)
            else:
                self._start_next()
        start_when_stopped()

    def _leave_current(self, action, quitting=False):
        """Before the transcript screen shows something else (or diemut quits): True if it
        can. In a batch, asks before stopping it, saves the file on screen without asking
        (it has its own session file), and leaves the batch; otherwise asks to save as
        usual."""
        if self.batch is None:
            return self._confirm_discard(action)
        left = self.batch.left()
        if left:
            if quitting:
                message = (f"{left} {'file is' if left == 1 else 'files are'} still waiting "
                           "or being transcribed. Quit anyway? Finished files are saved.")
                choices = [("go", "Quit"), ("keep", "Keep going")]
            else:
                message = (f"Stop transcribing? Finished files are saved; {left} "
                           f"{'file' if left == 1 else 'files'} won't be transcribed.")
                choices = [("go", "Stop"), ("keep", "Keep going")]
            if ask_choice(self.root, "Stop transcribing?", message, choices) != "go":
                return False
        if not self._save_shown():
            return False
        self._stop_batch()
        self._leave_batch()
        return True

    def _leave_batch(self):
        """Back to a single transcript: the file list goes."""
        if self.batch is None:
            return
        name = self.batch.items[self.batch.shown].name     # the title the file list leaves
        self.title_lbl.configure(text=f"{FILE_ICON} {name}")
        self.batch = None
        self._job = None
        if self.busy:                       # the file on screen was still being made
            self.busy = False
            self.progress.pack_forget()
        if self.transcript_screen.winfo_ismapped():
            self._show_transcript(self.mode)
