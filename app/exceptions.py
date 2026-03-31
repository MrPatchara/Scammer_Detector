"""Custom exceptions for the scam detector app."""


class ScamDetectorError(Exception):
    """Base exception for the scam detector package."""


class InvalidAudioFileError(ScamDetectorError):
    """Raised when an audio file does not exist or has an unsupported type."""


class WhisperLoadError(ScamDetectorError):
    """Raised when the Whisper model cannot be loaded."""


class TranscriptionError(ScamDetectorError):
    """Raised when transcription fails unexpectedly."""


class AIAnalysisError(ScamDetectorError):
    """Raised when AI analysis processing fails."""
