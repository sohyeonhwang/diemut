"""Playback for the correction panel: pause, resume, seek, loop and slow speeds.

Audio plays through one sounddevice.OutputStream whose callback reads from a
buffer, so the position can be tracked and changed without restarting the
stream. Other speeds use WSOLA time-stretching, which keeps voices at their
normal pitch.
"""

import threading

import numpy as np

from .transcribe import SAMPLE_RATE, _resample

SPEEDS = (0.5, 0.75, 1.0, 1.25, 1.5)

# WSOLA settings, in samples at 16 kHz: 40 ms frames, half-overlapping,
# searching up to 10 ms either side for the best-matching continuation.
_FRAME = 640
_TOLERANCE = 160


def stretch(x, speed):
    """Play x at speed (0.5 = half speed) without changing its pitch."""
    x = np.asarray(x, dtype=np.float32)
    if speed == 1.0 or len(x) < _FRAME:
        return x
    n, hop, tol = _FRAME, _FRAME // 2, _TOLERANCE
    window = np.hanning(n + 1)[:n].astype(np.float32)   # sums to 1 at 50% overlap
    padded = np.concatenate((np.zeros(tol, np.float32), x, np.zeros(n + tol + hop, np.float32)))
    frames = int(len(x) / (hop * speed)) + 1
    out = np.zeros(frames * hop + n, dtype=np.float32)
    prev = None
    for k in range(frames):
        nominal = tol + int(k * hop * speed)
        if prev is None:
            pos = nominal
        else:
            # Pick the frame near the nominal position that best continues the last one.
            follow = padded[prev + hop:prev + hop + n]
            region = padded[nominal - tol:nominal + tol + n]
            pos = nominal - tol + int(np.argmax(np.correlate(region, follow, mode="valid")))
        out[k * hop:k * hop + n] += padded[pos:pos + n] * window
        prev = pos
    return out[:int(round(len(x) / speed))]


class Player:
    """Plays one stretch of the session's audio (the open paragraph and its margins).

    Times are in seconds on the transcript's clock, whatever the speed.
    """

    def __init__(self):
        self.stream = None
        self.rate = SAMPLE_RATE
        self.lock = threading.Lock()
        self.source = np.zeros(0, dtype=np.float32)
        self.t0 = 0.0
        self.speed = 1.0
        self._buffers = {}
        self.buf = np.zeros(0, dtype=np.float32)
        self.pos = 0              # in samples of buf
        self.stop_at = 0          # playback ends (or loops) here
        self.loop_from = None     # samples of buf, or None when not looping
        self.playing = False

    # -- the audio --------------------------------------------------------
    def load(self, audio, t0):
        """Use audio (16 kHz float32, starting at t0 seconds) for playback."""
        with self.lock:
            self.playing = False
            self.source = np.asarray(audio, dtype=np.float32)
            self.t0 = t0
            self._buffers = {}
            self.buf = self._buffer(self.speed)
            self.pos = 0
            self.stop_at = len(self.buf)
            self.loop_from = None

    @property
    def t1(self):
        return self.t0 + len(self.source) / SAMPLE_RATE

    def _buffer(self, speed):
        if speed not in self._buffers:
            self._buffers[speed] = _resample(stretch(self.source, speed), SAMPLE_RATE, self.rate)
        return self._buffers[speed]

    def _index(self, t):
        t = min(max(t, self.t0), self.t1)
        return int(round((t - self.t0) / self.speed * self.rate))

    def _time(self, index):
        return self.t0 + index * self.speed / self.rate

    # -- the stream -------------------------------------------------------
    def _open(self):
        if self.stream is not None:
            return
        import sounddevice as sd
        try:
            stream = sd.OutputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                                     callback=self._callback)
            rate = SAMPLE_RATE
        except Exception:
            rate = int(sd.query_devices(kind="output")["default_samplerate"])
            stream = sd.OutputStream(samplerate=rate, channels=1, dtype="float32",
                                     callback=self._callback)
        with self.lock:
            if rate != self.rate:
                position = self._time(self.pos)
                self.rate, self._buffers = rate, {}
                self.buf = self._buffer(self.speed)
                self.pos = self._index(position)
                self.stop_at = len(self.buf)
        stream.start()
        self.stream = stream

    def _callback(self, outdata, frames, time_info, status):
        out = outdata[:, 0]
        out.fill(0)
        with self.lock:
            filled = 0
            while self.playing and filled < frames:
                n = min(frames - filled, self.stop_at - self.pos)
                if n > 0:
                    out[filled:filled + n] = self.buf[self.pos:self.pos + n]
                    self.pos += n
                    filled += n
                if self.pos >= self.stop_at:
                    if self.loop_from is not None and self.loop_from < self.stop_at:
                        self.pos = self.loop_from
                    else:
                        self.playing = False

    def close(self):
        """Stop playing and release the audio device."""
        with self.lock:
            self.playing = False
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:
                pass
            self.stream = None

    # -- controls ---------------------------------------------------------
    def play(self, start, end, loop_from=None):
        """Play from start to end (seconds). With loop_from, go back there at the end
        and keep repeating until paused.

        Raises if there's no audio output device.
        """
        self._open()
        with self.lock:
            a, b = self._index(start), max(self._index(start), self._index(end))
            self.pos, self.stop_at = a, b
            self.loop_from = None if loop_from is None else self._index(loop_from)
            self.playing = b > a

    def set_loop(self, start):
        """Loop back to start (seconds) when playback reaches its end; None stops looping."""
        with self.lock:
            self.loop_from = None if start is None else self._index(start)

    def pause(self):
        with self.lock:
            self.playing = False

    def seek(self, t):
        with self.lock:
            self.pos = min(self._index(t), self.stop_at) if self.playing else self._index(t)

    def set_speed(self, speed):
        with self.lock:
            if speed == self.speed:
                return
            position, end = self._time(self.pos), self._time(self.stop_at)
            loop = None if self.loop_from is None else self._time(self.loop_from)
            self.speed = speed
            self.buf = self._buffer(speed)
            self.pos, self.stop_at = self._index(position), self._index(end)
            self.loop_from = None if loop is None else self._index(loop)

    @property
    def position(self):
        with self.lock:
            return self._time(self.pos)

    @property
    def is_playing(self):
        return self.playing
