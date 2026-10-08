"""Whisper on this PC: Flow's ears.

faster-whisper (MIT) running OpenAI's Whisper base.en model on the CPU. An
8-second sentence takes about 0.6 s here, comes back punctuated, and spells
names from a hint list, so plain dictation needs no model call at all.
Free, private (audio never leaves the machine), and optional: if it is not
installed, Flow falls back to the browser's speech recognition.

The browser sends raw 16 kHz mono PCM, so PyAV (faster-whisper's audio
decoder, which some Windows Application Control policies block) is never
needed; a stand-in module is registered when it cannot load.

The model (~140 MB) downloads from Hugging Face on first use and is cached.
"""

from __future__ import annotations

import sys
import threading
import time
import types
from typing import Any

MODEL = "base.en"
RATE = 16000
MAX_SECONDS = 120

_model = None
_error = ""
_loading = False
_lock = threading.Lock()          # one transcription at a time; the model is not re-entrant


def _import():
    try:
        import av  # noqa: F401
    except Exception:
        sys.modules.setdefault("av", types.ModuleType("av"))
    from faster_whisper import WhisperModel
    return WhisperModel


def installed() -> bool:
    try:
        _import()
        return True
    except Exception:
        return False


def _load() -> None:
    global _model, _error, _loading
    try:
        WhisperModel = _import()
        _model = WhisperModel(MODEL, device="cpu", compute_type="int8")
        _error = ""
    except Exception as exc:
        _error = f"{type(exc).__name__}: {exc}"[:300]
    finally:
        _loading = False


def warm() -> None:
    """Load in the background so the first dictation does not wait."""
    global _loading
    if _model is not None or _loading:
        return
    _loading = True
    threading.Thread(target=_load, name="whisper-load", daemon=True).start()


def status() -> dict[str, Any]:
    ok = installed()
    if ok:
        warm()
    return {"installed": ok, "ready": _model is not None, "loading": _loading, "model": MODEL,
            "error": _error if ok else "faster-whisper is not installed (pip install faster-whisper)"}


def transcribe(pcm: bytes, hint: str = "") -> dict[str, Any]:
    """Raw little-endian int16 mono PCM at 16 kHz -> {"text", "seconds", "audio_seconds"}."""
    import numpy as np
    if len(pcm) < RATE // 5 * 2:                         # under 0.2 s: nothing said
        return {"text": "", "seconds": 0.0, "audio_seconds": 0.0}
    if len(pcm) > MAX_SECONDS * RATE * 2:
        raise ValueError(f"that is over {MAX_SECONDS} seconds of audio")
    audio = np.frombuffer(pcm[: len(pcm) // 2 * 2], dtype=np.int16).astype(np.float32) / 32768
    if float(np.sqrt(np.mean(audio ** 2))) < 0.004:      # silence: Whisper would invent a "Thank you."
        return {"text": "", "seconds": 0.0, "audio_seconds": round(len(audio) / RATE, 2)}
    if _model is None:
        if not installed():
            raise RuntimeError("faster-whisper is not installed")
        with _lock:
            if _model is None:
                _load()
        if _model is None:
            raise RuntimeError(f"Whisper could not load: {_error}")
    began = time.time()
    with _lock:
        segs, _ = _model.transcribe(audio, language="en", beam_size=1, vad_filter=True,
                                    vad_parameters={"min_silence_duration_ms": 500},
                                    initial_prompt=(hint[:400] or None), condition_on_previous_text=False)
        text = " ".join(s.text.strip() for s in segs).strip()
    return {"text": text, "seconds": round(time.time() - began, 2), "audio_seconds": round(len(audio) / RATE, 2)}
