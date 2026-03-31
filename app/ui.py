"""Simple microphone/upload UI for scam detection testing."""

from __future__ import annotations

import json
from typing import Any

import gradio as gr

from .exceptions import ScamDetectorError
from .pipeline import analyze_audio
from .utils import highlight_keywords


def _render_result(result: dict[str, Any]) -> tuple[str, str, str, str]:
    text = result.get("transcribed_text", "")
    keywords = result.get("keywords_found", [])
    highlighted = highlight_keywords(text, keywords, use_color=False)

    summary = (
        f"Risk: {result.get('risk_level')} ({result.get('risk_score')})\n"
        f"Keywords: {', '.join(keywords) if keywords else 'None'}\n"
        f"AI source: {result.get('ai_analysis', {}).get('source', 'simulation')}\n"
        f"Telegram: {result.get('telegram_alert', {}).get('sent')}"
    )

    return (
        highlighted,
        summary,
        json.dumps(result.get("ai_analysis", {}), ensure_ascii=False, indent=2),
        json.dumps(result, ensure_ascii=False, indent=2),
    )


def _analyze_from_ui(audio_file: str | None) -> tuple[str, str, str, str]:
    if not audio_file:
        return ("", "No audio received.", "{}", "{}")

    try:
        result = analyze_audio(audio_file)
        return _render_result(result)
    except ScamDetectorError as exc:
        return ("", f"Detector error: {exc}", "{}", "{}")
    except Exception as exc:
        return ("", f"Unexpected error: {exc}", "{}", "{}")


def launch_ui() -> None:
    with gr.Blocks(title="Thai Voice Scam Detector") as demo:
        gr.Markdown("# Thai Voice Scam Detector")
        gr.Markdown(
            "ทดสอบได้ทั้งอัปโหลดไฟล์และอัดจากไมค์ แล้วระบบจะถอดเสียง/จับคีย์เวิร์ด/ประเมินความเสี่ยง/แจ้งเตือน Telegram"
        )

        audio_input = gr.Audio(
            sources=["microphone", "upload"],
            type="filepath",
            label="Record from microphone or upload audio",
        )
        analyze_btn = gr.Button("Analyze")

        transcript_out = gr.Textbox(label="Transcribed Text (highlighted keywords)", lines=6)
        summary_out = gr.Textbox(label="Summary", lines=5)
        ai_json_out = gr.Code(label="AI Analysis JSON", language="json")
        full_json_out = gr.Code(label="Full Result JSON", language="json")

        analyze_btn.click(
            _analyze_from_ui,
            inputs=[audio_input],
            outputs=[transcript_out, summary_out, ai_json_out, full_json_out],
        )

    demo.launch(
        server_name="127.0.0.1",
        server_port=7860,
        inbrowser=True,
        share=False,
        quiet=True,
    )
