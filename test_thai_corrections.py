"""Quick test for Thai STT phonetic correction system."""

import sys
from pathlib import Path

# Add app to path
app_dir = Path(__file__).parent / "app"
sys.path.insert(0, str(app_dir.parent))

from app.thai_phonetic_corrector import (
    ThaiPhoneticCorrector,
    _get_consonant_variants,
    _get_tone_variants,
)


def test_consonant_variants():
    """Test consonant variant generation."""
    print("=" * 60)
    print("Testing Consonant Variants...")
    print("=" * 60)
    
    word = "บัญชี"
    variants = _get_consonant_variants(word)
    print(f"Word: {word}")
    print(f"Generated {len(variants)} consonant variants:")
    for v in variants[:5]:
        print(f"  - {v}")
    if len(variants) > 5:
        print(f"  ... and {len(variants) - 5} more")
    print()


def test_tone_variants():
    """Test tone mark variant generation."""
    print("=" * 60)
    print("Testing Tone Variants...")
    print("=" * 60)
    
    word = "ตรวจ"
    variants = _get_tone_variants(word)
    print(f"Word: {word}")
    print(f"Generated {len(variants)} tone variants:")
    for v in variants[:8]:
        print(f"  - {v}")
    if len(variants) > 8:
        print(f"  ... and {len(variants) - 8} more")
    print()


def test_phonetic_correction():
    """Test phonetic correction against keyword list."""
    print("=" * 60)
    print("Testing Phonetic Correction...")
    print("=" * 60)
    
    corrector = ThaiPhoneticCorrector(confidence_threshold=0.75)
    
    # Mock keyword list
    keywords = ["บัญชี", "โอนเงิน", "ตรวจสอบ", "ตำรวจ", "เพื่อตรวจสอบ"]
    
    test_cases = [
        ("บันชี", "บัญชี"),
        ("โอนน", "โอนเงิน"),
        ("ตรวด", "ตรวจ"),  # Note: exact match won't work, but phonetic should find ตรวจสอบ
        ("ตำรถ", "ตำรวจ"),
        ("เพื่อตรวจ", "เพื่อตรวจสอบ"),
    ]
    
    for input_word, expected in test_cases:
        result = corrector.correct_phonetic(input_word, keywords)
        status = "✓" if result == expected else "✗"
        print(f"{status} '{input_word}' → '{result}' (expected: '{expected}')")
    print()


def test_candidate_generation():
    """Test full candidate generation pipeline."""
    print("=" * 60)
    print("Testing Candidate Generation...")
    print("=" * 60)
    
    corrector = ThaiPhoneticCorrector()
    word = "บัญ"
    candidates = corrector.generate_correction_candidates(word)
    
    print(f"Word: {word}")
    print(f"Generated {len(candidates)} total candidates")
    print(f"First 10 candidates:")
    for c in candidates[:10]:
        print(f"  - {c}")
    print()


if __name__ == "__main__":
    try:
        print("\n" + "=" * 60)
        print("Thai STT Phonetic Correction System - Quick Test")
        print("=" * 60 + "\n")
        
        test_consonant_variants()
        test_tone_variants()
        test_phonetic_correction()
        test_candidate_generation()
        
        print("=" * 60)
        print("✓ All tests completed!")
        print("=" * 60)
        
    except Exception as e:
        print(f"✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
