"""Pluggable speech-to-text."""
from __future__ import annotations

from typing import Any


class SpeechToTextAdapter:
    name = "base"

    def available(self) -> bool:  # pragma: no cover - overridden
        return False

    def transcribe(self, data: bytes, filename: str = "audio.webm") -> dict[str, Any]:
        raise NotImplementedError


class FasterWhisperAdapter(SpeechToTextAdapter):
    """Local transcription via faster-whisper, if installed."""

    name = "faster-whisper"
    _model = None

    def available(self) -> bool:
        try:
            import faster_whisper  # noqa: F401

            return True
        except Exception:  # noqa: BLE001
            return False

    def transcribe(self, data: bytes, filename: str = "audio.webm") -> dict[str, Any]:
        import tempfile

        from faster_whisper import WhisperModel  # type: ignore

        if FasterWhisperAdapter._model is None:

            FasterWhisperAdapter._model = WhisperModel(
                "base", device="cpu", compute_type="int8")
        with tempfile.NamedTemporaryFile(suffix="_" + filename, delete=False) as tmp:
            tmp.write(data)
            tmp_path = tmp.name
        segments, info = FasterWhisperAdapter._model.transcribe(tmp_path)
        text = " ".join(seg.text for seg in segments).strip()
        return {"text": text, "engine": self.name, "language": info.language}


class StubAdapter(SpeechToTextAdapter):
    name = "stub"

    def available(self) -> bool:
        return True

    def transcribe(self, data: bytes, filename: str = "audio.webm") -> dict[str, Any]:
        return {
            "text": "",
            "engine": "none",
            "warning": (
                "Voice transcription is not configured. Install a local speech "
                "model to enable it, e.g. `pip install faster-whisper`, then the "
                "FasterWhisperAdapter activates automatically. Your audio was "
                f"received ({len(data)} bytes) and stored."
            ),
        }


def _select_adapter() -> SpeechToTextAdapter:
    fw = FasterWhisperAdapter()
    if fw.available():
        return fw
    return StubAdapter()


ACTIVE_ADAPTER: SpeechToTextAdapter = _select_adapter()


def transcribe_audio(data: bytes, filename: str = "audio.webm") -> dict[str, Any]:
    """Transcribe, or explain why not — but never raise."""
    try:
        return ACTIVE_ADAPTER.transcribe(data, filename)
    except Exception as exc:  # noqa: BLE001
        return {
            "text": "",
            "engine": ACTIVE_ADAPTER.name,
            "error": (
                f"Transcription failed in the {ACTIVE_ADAPTER.name} engine. Your "
                f"audio was received ({len(data)} bytes) and stored. ({exc})"
            ),
        }
