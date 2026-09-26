"""Audio cues for Phase 8 session transitions (R6).

Non-blocking, user-toggleable, fail completely silent:

- Windows: a short synthesized WAV blip played with
  ``winsound.PlaySound(..., SND_ASYNC | SND_FILENAME)`` — the call
  returns immediately, the sound plays on a system thread.
- Anywhere else: no-op.
- Every public call is wrapped so a cue can never raise, block, or
  hang the app. If anything fails, there is simply no sound.

The WAVs are synthesized at runtime with the stdlib ``wave`` module
(sine blips with a quick fade to avoid clicks) and cached in the
system temp dir once per process — no binary assets in the repo.
"""

import math
import os
import struct
import sys
import tempfile
import wave

# cue name -> (frequency Hz, seconds)
CUES = {
    "cycle_end": (880.0, 0.22),    # work done -> break (bright blip)
    "break_end": (660.0, 0.22),    # break over -> back to work
    "session_end": (523.25, 0.45),  # session complete (softer, longer)
}

_SAMPLE_RATE = 22050
_cache = {}


def _synthesize(name, freq, seconds):
    """Write the cue WAV to the temp dir; returns the path."""
    path = os.path.join(tempfile.gettempdir(),
                        "focuscore_cue_%s.wav" % name)
    if os.path.exists(path):
        return path
    n = max(1, int(_SAMPLE_RATE * seconds))
    frames = bytearray()
    for i in range(n):
        t = i / _SAMPLE_RATE
        # Sine blip with raised-cosine fade in/out (no clicks).
        env = math.sin(math.pi * i / n) ** 0.5
        sample = int(16000 * env * math.sin(2 * math.pi * freq * t))
        frames += struct.pack("<h", max(-32768, min(32767, sample)))
    with wave.open(path, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(_SAMPLE_RATE)
        wav.writeframes(bytes(frames))
    return path


def play_cue(name, enabled=True):
    """Play a cue; returns True if a sound was requested.

    Never raises, never blocks — everything (including first-call WAV
    synthesis) happens on a short-lived daemon thread; this function
    returns as soon as the thread is started. ``enabled`` is the
    user's ``audio_cues`` setting.
    """
    try:
        if not enabled:
            return False
        spec = CUES.get(name)
        if spec is None:
            return False
        if sys.platform != "win32":
            return False  # no-op off Windows (R6)

        def _runner():
            try:
                import winsound
                path = _cache.get(name)
                if path is None:
                    path = _synthesize(name, *spec)
                    _cache[name] = path
                winsound.PlaySound(
                    path, winsound.SND_ASYNC | winsound.SND_FILENAME)
            except Exception:
                pass  # fail completely silent (R6)

        import threading
        threading.Thread(target=_runner, daemon=True,
                         name="focuscore-cue").start()
        return True
    except Exception:
        return False  # fail completely silent (R6)
