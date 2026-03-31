"""
Advanced Thai phonetic correction engine using rules and similarity matching.
Handles tone mark confusion, consonant swaps, and vowel variations.
Includes ML-like learning system to improve over time.
"""

from __future__ import annotations

from difflib import SequenceMatcher
import re
import json
from pathlib import Path
from typing import Any
from collections import defaultdict
import logging

# Try to import pythainlp for Thai-specific processing
try:
    from pythainlp.tokenize import word_tokenize
    from pythainlp.corpus import stopwords
    HAS_PYTHAINLP = True
except ImportError:
    HAS_PYTHAINLP = False
    word_tokenize = None

logger = logging.getLogger(__name__)
THAI_CONSONANT_GROUPS = {
    "ก": {"ค", "ข"},
    "ค": {"ก", "ข"},
    "ข": {"ก", "ค"},
    "จ": {"ช", "ซ"},
    "ช": {"จ", "ซ"},
    "ซ": {"จ", "ช"},
    "ด": {"ต", "ฏ"},
    "ต": {"ด", "ฏ"},
    "ฏ": {"ด", "ต"},
    "บ": {"ป", "พ"},
    "ป": {"บ", "พ"},
    "พ": {"บ", "ป"},
    "ม": {"น"},
    "น": {"ม"},
    "ส": {"ศ", "ซ"},
    "ศ": {"ส", "ซ"},
    "ร": {"ล"},
    "ล": {"ร"},
}

# Tone mark variations (Thai has 5 tones)
TONE_MARKS = ["", "่", "้", "๊", "๋"]

# Common vowel substitutions in Whisper Thai transcription
VOWEL_SUBSTITUTIONS = {
    "ั": {"ิ", "ื", "ุ"},
    "ิ": {"ั", "ื", "ุ"},
    "ื": {"ั", "ิ", "ุ"},
    "ุ": {"ั", "ิ", "ื"},
    "า": {"ำ"},
    "ำ": {"า"},
    "เ": {"แ", "โ"},
    "แ": {"เ", "โ"},
    "โ": {"เ", "แ"},
}


def _phonetic_similarity(s1: str, s2: str) -> float:
    """Calculate similarity ratio between two Thai strings."""
    if not s1 or not s2:
        return 0.0
    return SequenceMatcher(None, s1, s2).ratio()


def _get_consonant_variants(word: str) -> list[str]:
    """
    Generate consonant variants of a Thai word.
    Example: "ตรวจ" → ["ตรวจ", "ดรวจ", "ฏรวจ", ...]
    """
    variants = [word]
    
    for pos, char in enumerate(word):
        if char in THAI_CONSONANT_GROUPS:
            similar_consonants = THAI_CONSONANT_GROUPS[char]
            for alt_consonant in similar_consonants:
                variant = word[:pos] + alt_consonant + word[pos+1:]
                if variant not in variants:
                    variants.append(variant)
    
    return variants


def _get_tone_variants(word: str) -> list[str]:
    """
    Generate tone mark variants of a Thai word.
    Useful for Whisper tone confusion.
    Example: "บัญชี" → ["บัญชี", "บัญชี่", "บัญชี้", ...]
    """
    if not word:
        return [word]
    
    variants = [word]
    
    # For each character position, try different tone marks
    for pos in range(len(word)):
        char = word[pos]
        # Only apply tone marks to Thai letters (not punctuation or digits)
        if ord(char) >= 0x0E00 and ord(char) <= 0x0E7F:
            for tone in TONE_MARKS:
                # Try adding/replacing tone mark
                variant = word[:pos] + char + tone + word[pos+1:]
                if variant not in variants and len(variant) <= len(word) + 2:
                    variants.append(variant)
    
    return variants


def _levenshtein_distance(s1: str, s2: str) -> int:
    """Calculate Levenshtein distance between two strings."""
    if len(s1) < len(s2):
        return _levenshtein_distance(s2, s1)
    if len(s2) == 0:
        return len(s1)
    
    previous_row = range(len(s2) + 1)
    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row
    
    return previous_row[-1]


class ThaiPhoneticCorrector:
    """Corrects Thai text using phonetic rules and similarity matching."""
    
    def __init__(self, confidence_threshold: float = 0.75):
        """
        Args:
            confidence_threshold: Min similarity score (0-1) for auto-correction
        """
        self.confidence_threshold = confidence_threshold
    
    def correct_phonetic(self, text: str, candidate_words: list[str]) -> str:
        """
        Correct Thai text against a list of candidate words using phonetic matching.
        
        Args:
            text: Input text to correct
            candidate_words: List of known correct words in domain (keywords)
        
        Returns:
            Corrected text (original if no good match found)
        """
        if not text or not candidate_words:
            return text
        
        text_lower = text.lower().strip()
        
        # Try exact match first (fastest)
        if text_lower in [w.lower() for w in candidate_words]:
            return text
        
        best_match = None
        best_score = 0.0
        
        for candidate in candidate_words:
            candidate_lower = candidate.lower()
            
            # Profile 1: Direct phonetic similarity
            similarity = _phonetic_similarity(text_lower, candidate_lower)
            
            # Profile 2: Levenshtein distance (edit distance)
            max_len = max(len(text_lower), len(candidate_lower))
            if max_len > 0:
                distance = _levenshtein_distance(text_lower, candidate_lower)
                distance_score = 1.0 - (distance / max_len)
            else:
                distance_score = 0.0
            
            # Weighted combination
            combined_score = 0.6 * similarity + 0.4 * distance_score
            
            if combined_score > best_score:
                best_score = combined_score
                best_match = candidate
        
        # Return best match if confidence is high enough
        if best_score >= self.confidence_threshold:
            return best_match
        
        return text
    
    def _get_best_match_score(self, text: str, candidate_words: list[str]) -> float:
        """Get confidence score for best match (for learning/tracking)."""
        if not text or not candidate_words:
            return 0.0
        
        text_lower = text.lower().strip()
        best_score = 0.0
        
        for candidate in candidate_words:
            candidate_lower = candidate.lower()
            similarity = _phonetic_similarity(text_lower, candidate_lower)
            max_len = max(len(text_lower), len(candidate_lower))
            if max_len > 0:
                distance = _levenshtein_distance(text_lower, candidate_lower)
                distance_score = 1.0 - (distance / max_len)
            else:
                distance_score = 0.0
            
            combined_score = 0.6 * similarity + 0.4 * distance_score
            best_score = max(best_score, combined_score)
        
        return best_score
    
    def generate_correction_candidates(self, word: str) -> list[str]:
        """
        Generate potential correction candidates for a Thai word.
        Combines consonant, tone, and vowel variations.
        
        Args:
            word: Input Thai word
        
        Returns:
            List of candidate variations
        """
        candidates = set()
        
        # Add base word
        candidates.add(word)
        
        # Add consonant variants
        consonant_vars = _get_consonant_variants(word)
        candidates.update(consonant_vars)
        
        # Add tone variants for base word
        tone_vars = _get_tone_variants(word)
        candidates.update(tone_vars)
        
        # Add tone variants for each consonant variant
        for cvar in consonant_vars[:5]:  # Limit to avoid explosion
            candidates.update(_get_tone_variants(cvar))
        
        return list(candidates)


def batch_correct_with_candidates(
    text: str,
    candidate_words: list[str],
    threshold: float = 0.78
) -> str:
    """
    Correct Thai text by matching words against candidate list.
    
    Args:
        text: Input text
        candidate_words: List of known correct words
        threshold: Confidence threshold for corrections
    
    Returns:
        Corrected text
    """
    corrector = ThaiPhoneticCorrector(confidence_threshold=threshold)
    
    # Split by spaces and punctuation, correct each word
    words = re.split(r'(\s+|[^\w\u0E00-\u0E7F]+)', text)
    corrected_words = []
    
    for word in words:
        if word and re.match(r'[\u0E00-\u0E7F]+', word):  # Thai characters only
            corrected = corrector.correct_phonetic(word, candidate_words)
            corrected_words.append(corrected)
        else:
            corrected_words.append(word)
    
    return ''.join(corrected_words)


class CorrectionLearner:
    """
    Learns from corrections to improve future accuracy.
    Tracks which corrections work best and suggests entries for manual dictionary.
    """
    
    def __init__(self, log_file: str | Path | None = None):
        """
        Args:
            log_file: Path to store correction patterns. If None, uses in-memory tracking.
        """
        self.log_file = Path(log_file) if log_file else None
        
        # Statistics tracking
        self.corrections_made: dict[str, dict[str, Any]] = defaultdict(lambda: {
            "count": 0,
            "average_score": 0.0,
            "target_word": "",
            "frequency": 0,
        })
        
        self.failed_corrections: list[dict[str, Any]] = []
        self.high_confidence_candidates: dict[str, float] = {}
        
        self._load_history()
    
    def _load_history(self) -> None:
        """Load previous correction history if log file exists."""
        if self.log_file and self.log_file.exists():
            try:
                with open(self.log_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.corrections_made = defaultdict(
                    lambda: {"count": 0, "average_score": 0.0, "target_word": "", "frequency": 0},
                    data.get("corrections", {})
                )
                self.high_confidence_candidates = data.get("high_confidence", {})
            except Exception as e:
                logger.warning(f"Failed to load correction history: {e}")
    
    def _save_history(self) -> None:
        """Save correction statistics for learning."""
        if not self.log_file:
            return
        
        try:
            self.log_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.log_file, "w", encoding="utf-8") as f:
                json.dump({
                    "corrections": dict(self.corrections_made),
                    "high_confidence": self.high_confidence_candidates,
                    "total_corrections": len(self.corrections_made),
                }, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Failed to save correction history: {e}")
    
    def record_correction(
        self,
        original: str,
        corrected_to: str,
        confidence_score: float
    ) -> None:
        """
        Record a correction for learning purposes.
        
        Args:
            original: Original (wrong) text
            corrected_to: What it was corrected to
            confidence_score: Confidence score (0-1) of the correction
        """
        if original == corrected_to:
            return  # No actual correction
        
        key = original.lower()
        stats = self.corrections_made[key]
        
        old_count = stats["count"]
        stats["count"] += 1
        stats["target_word"] = corrected_to
        
        # Update running average
        old_avg = stats["average_score"]
        stats["average_score"] = (old_avg * old_count + confidence_score) / stats["count"]
        stats["frequency"] += 1
        
        # Track high-confidence corrections for auto-suggestion
        if confidence_score >= 0.85:
            self.high_confidence_candidates[original] = corrected_to
        
        self._save_history()
    
    def record_failure(
        self,
        original: str,
        attempted_match: str,
        score: float,
        candidate_words: list[str]
    ) -> None:
        """
        Record a failed correction for future improvement.
        
        Args:
            original: Original text that couldn't be corrected confidently
            attempted_match: What it tried to match to
            score: The score it got (below threshold)
            candidate_words: Full list of candidates it checked
        """
        self.failed_corrections.append({
            "original": original,
            "attempted_match": attempted_match,
            "score": score,
            "candidate_count": len(candidate_words),
        })
    
    def get_suggested_additions(
        self,
        threshold: float = 0.82,
        min_occurrences: int = 3,
    ) -> list[tuple[str, str]]:
        """
        Get high-confidence corrections to suggest adding to manual dictionary.
        
        Args:
            threshold: Only suggest if average score >= this value
            min_occurrences: Min times pattern must be seen before suggesting
        
        Returns:
            List of (wrong, correct) tuples to add to thai_stt_corrections.json
        """
        if min_occurrences < 1:
            min_occurrences = 1

        suggestions = []
        for original, stats in self.corrections_made.items():
            if (stats["average_score"] >= threshold and 
                stats["count"] >= min_occurrences):
                suggestions.append((original, stats["target_word"]))
        
        return sorted(suggestions, key=lambda x: x[1])  # Sort by target word
    
    def get_stats(self) -> dict[str, Any]:
        """Get overall correction statistics."""
        total_corrections = sum(s["count"] for s in self.corrections_made.values())
        avg_confidence = (
            sum(s["average_score"] * s["count"] for s in self.corrections_made.values()) / 
            total_corrections
            if total_corrections > 0 else 0.0
        )
        
        return {
            "total_corrections": total_corrections,
            "unique_patterns": len(self.corrections_made),
            "average_confidence": avg_confidence,
            "high_confidence_suggestions": len(self.high_confidence_candidates),
            "failed_corrections": len(self.failed_corrections),
        }
    
    def clear_history(self) -> None:
        """Clear all learned data."""
        self.corrections_made.clear()
        self.high_confidence_candidates.clear()
        self.failed_corrections.clear()
        if self.log_file and self.log_file.exists():
            self.log_file.unlink()


class ThaiContextAwareCorrector:
    """
    AI-like corrector that uses Thai linguistic knowledge + learning.
    Combines phonetic matching with Thai word segmentation and context.
    """
    
    def __init__(
        self,
        learner: CorrectionLearner | None = None,
        confidence_threshold: float = 0.78
    ):
        """
        Args:
            learner: Optional CorrectionLearner for improvement over time
            confidence_threshold: Min score for auto-correction
        """
        self.base_corrector = ThaiPhoneticCorrector(confidence_threshold)
        self.learner = learner
        self.confidence_threshold = confidence_threshold
    
    def correct_with_segmentation(
        self,
        text: str,
        candidate_words: list[str]
    ) -> tuple[str, list[dict[str, Any]]]:
        """
        Correct Thai text using word segmentation for better accuracy.
        
        Args:
            text: Text to correct
            candidate_words: List of known correct words
        
        Returns:
            (corrected_text, list of correction details)
        """
        corrections_applied = []
        
        # Use pythainlp for Thai word segmentation if available
        if HAS_PYTHAINLP and word_tokenize:
            try:
                words = word_tokenize(text, keep_whitespace=True)
            except Exception:
                # Fallback to space splitting
                words = text.split()
        else:
            words = text.split()
        
        corrected_words = []
        
        for word in words:
            if not word.strip():
                corrected_words.append(word)
                continue
            
            # Skip non-Thai words
            if not any(ord(c) >= 0x0E00 and ord(c) <= 0x0E7F for c in word):
                corrected_words.append(word)
                continue
            
            # Try correction
            original = word.strip()
            corrected = self.base_corrector.correct_phonetic(original, candidate_words)
            
            if corrected != original:
                # Get score for learning
                score = self.base_corrector._get_best_match_score(original, candidate_words)
                if self.learner:
                    self.learner.record_correction(original, corrected, score)
                
                corrections_applied.append({
                    "original": original,
                    "corrected": corrected,
                    "score": score,
                })
            
            corrected_words.append(word.replace(original, corrected))
        
        return ''.join(corrected_words), corrections_applied
    
    def get_improvement_suggestions(self) -> dict[str, Any]:
        """Get suggestions for improving the correction dictionary."""
        if not self.learner:
            return {}
        
        suggestions = self.learner.get_suggested_additions(threshold=0.82)
        return {
            "suggested_additions": suggestions,
            "stats": self.learner.get_stats(),
            "note": f"Add these {len(suggestions)} corrections to thai_stt_corrections.json for better future performance"
        }

