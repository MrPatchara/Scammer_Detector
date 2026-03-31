"""Shared CLI utility helpers."""

from __future__ import annotations

from pathlib import Path


def supports_ansi() -> bool:
    import sys

    return sys.stdout.isatty()


def color_text(text: str, color_code: str, enable: bool) -> str:
    if not enable:
        return text
    return f"\033[{color_code}m{text}\033[0m"


def highlight_keywords(text: str, keywords: list[str], use_color: bool = True) -> str:
    if not keywords:
        return text

    output = text
    for keyword in sorted(set(keywords), key=len, reverse=True):
        replacement = f"\033[1;33m{keyword}\033[0m" if use_color else f"[{keyword}]"
        output = output.replace(keyword, replacement)
    return output


def default_result_filename(audio_path: str) -> str:
    return f"{Path(audio_path).stem}_analysis.json"
