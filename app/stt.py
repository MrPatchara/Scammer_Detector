"""Speech-to-text service using local Whisper for Thai."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import whisper

from .config import DEFAULT_WHISPER_MODEL, SUPPORTED_AUDIO_EXTENSIONS
from .exceptions import InvalidAudioFileError, TranscriptionError, WhisperLoadError


class WhisperThaiTranscriber:
    """Loads Whisper once and provides Thai transcription."""

    def __init__(self, model_name: str = DEFAULT_WHISPER_MODEL) -> None:
        self.model_name = model_name
        self._model: Any | None = None

    def _validate_audio_path(self, audio_path: str) -> Path:
        path = Path(audio_path)
        if not path.exists() or not path.is_file():
            raise InvalidAudioFileError(f"Audio file not found: {audio_path}")

        if path.suffix.lower() not in SUPPORTED_AUDIO_EXTENSIONS:
            allowed = ", ".join(sorted(SUPPORTED_AUDIO_EXTENSIONS))
            raise InvalidAudioFileError(
                f"Unsupported audio format '{path.suffix}'. Supported formats: {allowed}"
            )

        return path

    def _load_model(self) -> Any:
        if self._model is not None:
            return self._model

        try:
            self._model = whisper.load_model(self.model_name)
            return self._model
        except Exception as exc:
            raise WhisperLoadError(
                f"Failed to load Whisper model '{self.model_name}'."
            ) from exc

    def speech_to_text(self, audio_path: str) -> str:
        path = self._validate_audio_path(audio_path)
        model = self._load_model()

        try:
            result = model.transcribe(str(path), language="th")
        except Exception as exc:
            raise TranscriptionError(f"Failed to transcribe audio file: {audio_path}") from exc

        return (result or {}).get("text", "").strip()


_DEFAULT_TRANSCRIBER: WhisperThaiTranscriber | None = None


def get_default_transcriber() -> WhisperThaiTranscriber:
    global _DEFAULT_TRANSCRIBER
    if _DEFAULT_TRANSCRIBER is None:
        _DEFAULT_TRANSCRIBER = WhisperThaiTranscriber()
    return _DEFAULT_TRANSCRIBER


def speech_to_text(audio_path: str) -> str:
    """Required public interface: transcribe audio path to Thai text."""
    return get_default_transcriber().speech_to_text(audio_path)
