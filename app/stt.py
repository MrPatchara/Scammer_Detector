"""Speech-to-text service using local Whisper for Thai."""

from __future__ import annotations

import shutil
import tempfile
import uuid
import os
import subprocess
import json
import logging
from pathlib import Path
from typing import Any
from difflib import SequenceMatcher

import imageio_ffmpeg
import torch
import whisper

from .config import DEFAULT_WHISPER_MODEL, SUPPORTED_AUDIO_EXTENSIONS, THAI_TEXT_CORRECTIONS, load_thai_stt_corrections, get_runtime_config
from .exceptions import InvalidAudioFileError, TranscriptionError, WhisperLoadError
from .thai_phonetic_corrector import ThaiPhoneticCorrector, CorrectionLearner, ThaiContextAwareCorrector
from .auto_learning import AutoLearningEngine


class WhisperThaiTranscriber:
    """Loads Whisper once and provides Thai transcription."""

    def __init__(self, model_name: str = DEFAULT_WHISPER_MODEL) -> None:
        self.model_name = self._resolve_model_name(model_name)
        self._model: Any | None = None
        # Load corrections at init time to avoid repeated file I/O
        self._corrections = load_thai_stt_corrections()
        # Initialize phonetic corrector for advanced matching
        self._phonetic_corrector = ThaiPhoneticCorrector(confidence_threshold=0.78)
        
        # Initialize learning system for continuous improvement
        learner_path = Path(__file__).parent.parent / "correction_learning.json"
        self._learner = CorrectionLearner(log_file=learner_path)
        
        # Context-aware corrector with learning capability
        self._context_corrector = ThaiContextAwareCorrector(
            learner=self._learner,
            confidence_threshold=0.78
        )
        self._logger = logging.getLogger(__name__)
        self._corrections_json_path = Path(__file__).parent.parent / "thai_stt_corrections.json"
        self._auto_learning = AutoLearningEngine(
            db_path=Path(__file__).parent.parent / "correction_learning.db",
            corrections_json_path=self._corrections_json_path,
        )

    def _auto_promote_learned_corrections(self) -> None:
        """Promote high-confidence learned corrections into thai_stt_corrections.json."""
        if os.getenv("STT_AUTO_PROMOTE", "1").strip().lower() in {"0", "false", "no", "off"}:
            return

        threshold_raw = os.getenv("STT_AUTO_PROMOTE_THRESHOLD", "0.82")
        try:
            threshold = float(threshold_raw)
        except ValueError:
            threshold = 0.82

        min_occurrences_raw = os.getenv("STT_AUTO_PROMOTE_MIN_OCCURRENCES", "2")
        try:
            min_occurrences = int(min_occurrences_raw)
        except ValueError:
            min_occurrences = 2
        if min_occurrences < 1:
            min_occurrences = 1

        suggestions = self._learner.get_suggested_additions(
            threshold=threshold,
            min_occurrences=min_occurrences,
        )
        max_batch_raw = os.getenv("STT_AUTO_PROMOTE_BATCH", "200")
        try:
            max_batch = int(max_batch_raw)
        except ValueError:
            max_batch = 200
        if max_batch < 1:
            max_batch = 1

        payload: dict[str, Any] = {"common_errors": {}}
        if self._corrections_json_path.exists():
            try:
                with open(self._corrections_json_path, "r", encoding="utf-8-sig") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    payload = loaded
            except Exception as exc:
                self._logger.warning("Failed to load corrections JSON for auto-promote: %s", exc)
                return

        common_errors = payload.setdefault("common_errors", {})
        if not isinstance(common_errors, dict):
            common_errors = {}
            payload["common_errors"] = common_errors

        learned_auto = common_errors.setdefault("learned_auto", {})
        if not isinstance(learned_auto, dict):
            learned_auto = {}
            common_errors["learned_auto"] = learned_auto

        added_from_learner = 0
        for wrong, correct in suggestions:
            normalized_wrong = str(wrong).strip()
            normalized_correct = str(correct).strip()

            if not normalized_wrong or not normalized_correct:
                continue
            if normalized_wrong in self._corrections:
                continue
            if normalized_wrong in learned_auto:
                continue

            learned_auto[normalized_wrong] = normalized_correct
            added_from_learner += 1

        if added_from_learner > 0:
            try:
                with open(self._corrections_json_path, "w", encoding="utf-8") as f:
                    json.dump(payload, f, ensure_ascii=False, indent=2)
            except Exception as exc:
                self._logger.error("Failed to save learner promotions: %s", exc)
                return

        # Second path: scalable auto-engine selects promotable candidates from
        # observed + generated evidence in SQLite.
        promotion_result = self._auto_learning.promote(
            min_count=min_occurrences,
            min_score=threshold,
            max_batch=max_batch,
        )
        total_added = added_from_learner + promotion_result.promoted_items

        if total_added <= 0:
            return

        try:
            self._corrections = load_thai_stt_corrections()
            self._logger.info("Auto-promoted %s learned corrections to %s", total_added, self._corrections_json_path.name)
        except Exception as exc:
            self._logger.error("Failed to reload auto-promoted corrections: %s", exc)

    def _resolve_model_name(self, model_name: str) -> str:
        configured = os.getenv("WHISPER_MODEL", "").strip().lower()
        if configured:
            return configured

        speed_profile = os.getenv("STT_SPEED_PROFILE", "balanced").strip().lower()
        if speed_profile not in {"fast", "balanced", "accurate"}:
            speed_profile = "fast"

        has_gpu = bool(torch.cuda.is_available())
        if has_gpu:
            return model_name

        if speed_profile == "fast":
            return "tiny"
        if speed_profile == "balanced":
            return "base"
        return model_name

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

    def _prepare_audio_for_transcription(self, path: Path) -> Path:
        """Normalize to 16k mono WAV and optionally clip long audio to avoid hangs."""
        self._ensure_ffmpeg_available()

        temp_name = f"scam_audio_{uuid.uuid4().hex}.wav"
        temp_path = Path(tempfile.gettempdir()) / temp_name

        max_audio_sec = int(os.getenv("STT_MAX_AUDIO_SECONDS", "90"))
        ffmpeg_timeout_sec = int(os.getenv("STT_FFMPEG_TIMEOUT_SECONDS", "90"))

        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
        ]

        if max_audio_sec > 0:
            cmd.extend(["-t", str(max_audio_sec)])

        cmd.append(str(temp_path))

        try:
            subprocess.run(
                cmd,
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                timeout=ffmpeg_timeout_sec,
            )
        except subprocess.TimeoutExpired as exc:
            raise TranscriptionError(
                "Audio preprocessing timed out before transcription. "
                f"Increase STT_FFMPEG_TIMEOUT_SECONDS (current {ffmpeg_timeout_sec})."
            ) from exc
        except subprocess.CalledProcessError as exc:
            tail = (exc.stderr or "").strip().splitlines()[-1:] or ["unknown ffmpeg error"]
            raise TranscriptionError(
                "Audio preprocessing failed. "
                f"Reason: {tail[0]}"
            ) from exc

        return temp_path

    def _ensure_ffmpeg_available(self) -> None:
        """Inject bundled ffmpeg binary into PATH when system ffmpeg is unavailable."""
        if shutil.which("ffmpeg"):
            return

        try:
            ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception as exc:
            raise TranscriptionError(
                "ffmpeg is not available and bundled ffmpeg could not be resolved. "
                f"Reason: {exc}"
            ) from exc

        resolved_exe = Path(ffmpeg_exe).resolve()
        alias_dir = Path(tempfile.gettempdir()) / "scam_detector_bin"
        alias_dir.mkdir(parents=True, exist_ok=True)
        alias_exe = alias_dir / "ffmpeg.exe"
        if not alias_exe.exists():
            shutil.copy2(resolved_exe, alias_exe)

        ffmpeg_dir = str(alias_dir)
        current_path = os.environ.get("PATH", "")
        if ffmpeg_dir not in current_path.split(os.pathsep):
            os.environ["PATH"] = ffmpeg_dir + os.pathsep + current_path

    def _apply_thai_corrections(self, text: str) -> str:
        """Apply context-aware Thai transcription corrections.
        
        Multi-stage correction system:
        1. High-confidence manual corrections from JSON/dict (<1ms)
        2. Phonetic matching with word segmentation (50-200ms)
        3. Learning system tracks corrections for future improvement
        """
        if not text:
            return text
        
        # Stage 1: Manual corrections (highest confidence, fastest)
        corrected = text
        for wrong, correct in self._corrections.items():
            corrected = corrected.replace(wrong, correct)
        
        # Stage 2: Context-aware phonetic matching with learning
        try:
            config = get_runtime_config()
            thai_keywords = config.get("keywords", [])
            
            if thai_keywords:
                # Use context-aware corrector with Thai word segmentation
                corrected, corrections_info = self._context_corrector.correct_with_segmentation(
                    corrected,
                    thai_keywords
                )

                if corrections_info:
                    self._auto_learning.record_corrections(corrections_info)
                
                # Optional: Log correction attempts for analysis
                if corrections_info:
                    self._logger.debug("Applied %s phonetic corrections", len(corrections_info))
                self._auto_promote_learned_corrections()
        except Exception as e:
            # Fallback to manual corrections only if advanced matching fails
            self._logger.warning("Context-aware correction failed, using manual fallback: %s", e)
        
        return corrected
    
    def get_learning_suggestions(self) -> dict[str, Any]:
        """Get suggestions for improving the correction dictionary based on learned patterns."""
        if not hasattr(self, '_learner'):
            return {}
        
        return self._context_corrector.get_improvement_suggestions()

    def get_auto_learning_stats(self) -> dict[str, int]:
        """Return scalable auto-learning stats from SQLite engine."""
        return self._auto_learning.get_stats()

    @staticmethod
    def _fuzzy_match_similarity(s1: str, s2: str, threshold: float = 0.75) -> bool:
        """Check if two strings are similar enough (for Thai keyword matching)."""
        ratio = SequenceMatcher(None, s1, s2).ratio()
        return ratio >= threshold

    def speech_to_text(self, audio_path: str) -> str:
        path = self._validate_audio_path(audio_path)
        model = self._load_model()
        prepared_path = self._prepare_audio_for_transcription(path)

        try:
            # Context prompt for scam detection domain (Thai)
            context_prompt = (
                "สวัสดี หน้าที่ตำรวจ ตรวจสอบ บัญชี โอนเงิน "
                "โอนด่วน กรุณา รหัส OTP ยืนยันตัวตน ธนาคาร "
                "ผิด กระทำความผิด ดำเนินคดี เจ้าหน้าที่ "
            )
            
            result = model.transcribe(
                str(prepared_path),
                language="th",
                fp16=False,
                temperature=0.5,
                beam_size=5,
                best_of=5,
                without_timestamps=True,
                condition_on_previous_text=False,
                no_speech_threshold=0.4,
                initial_prompt=context_prompt,
            )
        except Exception as exc:
            raise TranscriptionError(
                "Failed to transcribe audio file. "
                f"Path: {audio_path}. "
                f"Reason: {exc}. "
                "Tip: Reduce file length or lower STT_MAX_AUDIO_SECONDS for faster turnaround."
            ) from exc
        finally:
            try:
                if prepared_path.exists():
                    prepared_path.unlink()
            except Exception:
                # Best-effort cleanup only.
                pass

        raw_text = (result or {}).get("text", "").strip()
        corrected_text = self._apply_thai_corrections(raw_text)
        return corrected_text


_DEFAULT_TRANSCRIBER: WhisperThaiTranscriber | None = None


def get_default_transcriber() -> WhisperThaiTranscriber:
    global _DEFAULT_TRANSCRIBER
    if _DEFAULT_TRANSCRIBER is None:
        _DEFAULT_TRANSCRIBER = WhisperThaiTranscriber()
    return _DEFAULT_TRANSCRIBER


def speech_to_text(audio_path: str) -> str:
    """Required public interface: transcribe audio path to Thai text."""
    return get_default_transcriber().speech_to_text(audio_path)
