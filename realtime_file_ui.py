"""Gradio UI for audio file-based scam detection.

Run: python realtime_file_ui.py
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

import gradio as gr

from app.ai_analysis import ai_analyze
from app.alerts import send_telegram_alert, should_alert
from app.risk import calculate_risk, keyword_detect
from app.stt import speech_to_text


# Suppress asyncio warnings on Windows
if sys.platform == "win32":
    import asyncio
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# Reduce verbose logging
logging.getLogger("gradio").setLevel(logging.WARNING)
logging.getLogger("gradio_client").setLevel(logging.WARNING)


SUPPORTED_AUDIO = {'.wav', '.mp3', '.m4a', '.ogg', '.webm', '.mp4', '.flac'}
OUTPUT_DIR = Path("./file_results").resolve()
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

AI_GATE_SCORE = 45
AI_TRIGGERS = {'otp', 'โอนเงิน', 'เจ้าหน้าที่', 'บัญชี'}


def _build_result(audio_path: str, text: str) -> dict[str, Any]:
    """Build analysis result from transcribed text."""
    if not text.strip():
        return {
            "audio_file": audio_path,
            "transcribed_text": "",
            "keywords_found": [],
            "risk_score": 0,
            "risk_level": "low",
            "ai_analysis": {
                "risk_level": "low",
                "score": 0,
                "is_scam": False,
                "reason": "No speech detected",
                "source": "empty_audio",
            },
        }
    
    keywords = keyword_detect(text)
    risk = calculate_risk(text, keywords)

    lowered_keywords = {k.lower() for k in keywords}
    trigger_hit = any(k in lowered_keywords for k in AI_TRIGGERS)
    use_ai = int(risk.get("score", 0)) >= AI_GATE_SCORE or trigger_hit

    if use_ai:
        ai_result = ai_analyze(text)
    else:
        ai_result = {
            "risk_level": risk.get("risk_level", "low"),
            "score": int(risk.get("score", 0)),
            "is_scam": risk.get("risk_level") == "high",
            "keywords_detected": keywords,
            "reason": "keyword gate: score below AI threshold and no trigger keyword",
            "source": "keyword_gate",
        }

    result: dict[str, Any] = {
        "audio_file": audio_path,
        "transcribed_text": text,
        "keywords_found": keywords,
        "risk_score": risk["score"],
        "risk_level": risk["risk_level"],
        "ai_analysis": ai_result,
    }

    return result


def analyze_audio(audio_file) -> tuple[str, str, str]:
    """Analyze audio file and return results.
    
    Returns: (status, result_text, result_json)
    """
    if audio_file is None:
        return "❌ Please upload an audio file", "", ""

    audio_path = audio_file if isinstance(audio_file, str) else audio_file.name

    try:
        # Check format
        suffix = Path(audio_path).suffix.lower()
        if suffix not in SUPPORTED_AUDIO:
            return f"❌ Unsupported format: {suffix}\nSupported: {SUPPORTED_AUDIO}", "", ""

        # Transcribe
        status = "🔄 Running STT (Whisper)..."
        yield status, "", ""

        text = speech_to_text(audio_path)

        status = "🔄 Analyzing keywords and risk..."
        yield status, "", ""

        # Analyze
        result = _build_result(audio_path, text)

        # Format output
        risk_level = result.get("risk_level", "low")
        risk_score = result.get("risk_score", 0)
        keywords = result.get("keywords_found", [])
        transcribed_text = result.get("transcribed_text", "")
        ai_source = result.get("ai_analysis", {}).get("source", "unknown")
        ai_is_scam = result.get("ai_analysis", {}).get("is_scam", False)
        ai_reason = result.get("ai_analysis", {}).get("reason", "")

        result_text = f"""
📊 ANALYSIS RESULTS
{'='*60}

📁 File: {Path(audio_path).name}
🎙️  Transcribed Text:
{transcribed_text}

🔍 Keywords Found: {', '.join(keywords) if keywords else 'None'}

⚠️  Risk Level: {risk_level.upper()}
📈 Risk Score: {risk_score}/100

🤖 AI Analysis:
  • Source: {ai_source}
  • Is Scam: {'YES ⚠️' if ai_is_scam else 'No ✓'}
  • Reason: {ai_reason}
"""

        result_json = json.dumps(result, ensure_ascii=False, indent=2)

        # Save result
        now = time.time()
        out_path = OUTPUT_DIR / f"result_{int(now)}.json"
        out_path.write_text(result_json, encoding="utf-8")

        status = f"✅ Analysis complete!\nSaved: {out_path.name}"
        yield status, result_text, result_json

    except Exception as exc:
        status = f"❌ Error: {exc}"
        yield status, "", ""


def process_batch(folder_path: str) -> tuple[str, str]:
    """Process all audio files in a folder.
    
    Returns: (status, summary)
    """
    if not folder_path:
        return "❌ Please enter folder path", ""

    folder = Path(folder_path)
    if not folder.exists() or not folder.is_dir():
        return f"❌ Folder not found: {folder_path}", ""

    # Find audio files
    audio_files = [
        f for f in folder.iterdir()
        if f.is_file() and f.suffix.lower() in SUPPORTED_AUDIO
    ]

    if not audio_files:
        return f"⚠️  No audio files found in {folder_path}", ""

    audio_files.sort(key=lambda p: p.stat().st_mtime)

    summary_lines = [
        f"📂 Processing {len(audio_files)} files from: {folder_path}\n",
        f"{'='*60}\n",
    ]

    high_risk_count = 0
    medium_risk_count = 0
    low_risk_count = 0

    for i, audio_file in enumerate(audio_files, 1):
        try:
            status = f"Processing {i}/{len(audio_files)}: {audio_file.name}"
            summary_lines.append(f"\n[{i}/{len(audio_files)}] {audio_file.name}...")

            text = speech_to_text(str(audio_file))
            result = _build_result(str(audio_file), text)

            risk_level = result.get("risk_level", "low")
            risk_score = result.get("risk_score", 0)
            keywords = result.get("keywords_found", [])

            if risk_level == "high":
                high_risk_count += 1
                icon = "🔴"
            elif risk_level == "medium":
                medium_risk_count += 1
                icon = "🟡"
            else:
                low_risk_count += 1
                icon = "🟢"

            keywords_str = ", ".join(keywords) if keywords else "None"
            summary_lines.append(
                f"  {icon} Risk: {risk_level.upper()} ({risk_score}/100)\n"
                f"     Keywords: {keywords_str}\n"
                f"     Text: {text[:100]}...\n"
            )

            # Save result
            now = time.time()
            out_path = OUTPUT_DIR / f"result_{int(now)}.json"
            out_path.write_text(
                json.dumps(result, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )

        except Exception as exc:
            summary_lines.append(f"  ❌ Error: {exc}\n")

    summary_lines.append(f"\n{'='*60}\n")
    summary_lines.append(
        f"📊 Summary:\n"
        f"  🔴 High Risk: {high_risk_count}\n"
        f"  🟡 Medium Risk: {medium_risk_count}\n"
        f"  🟢 Low Risk: {low_risk_count}\n"
        f"  📂 Output: {OUTPUT_DIR}\n"
    )

    final_status = f"✅ Batch processing complete! Processed {len(audio_files)} files"
    summary = "".join(summary_lines)

    return final_status, summary


def main():
    with gr.Blocks(title="Scam Detection - Audio File Analyzer") as demo:
        gr.Markdown("""
# 🔐 Thai Voice Scam Detector
## Audio File Analysis Interface

Analyze audio files for scam detection using whisper STT + keyword detection + AI analysis.
        """)

        with gr.Tabs():
            # Tab 1: Single File Analysis
            with gr.Tab("📁 Analyze Single File"):
                gr.Markdown("Upload an audio file to analyze it for scam content.")

                with gr.Row():
                    with gr.Column(scale=1):
                        audio_input = gr.Audio(
                            label="Upload Audio File",
                            type="filepath",
                            interactive=True,
                        )
                        analyze_btn = gr.Button("🔍 Analyze", scale=1, size="lg")

                    with gr.Column(scale=1):
                        status_output = gr.Textbox(
                            label="Status",
                            interactive=False,
                            lines=4,
                        )

                with gr.Row():
                    result_output = gr.Textbox(
                        label="Analysis Results",
                        interactive=False,
                        lines=15,
                    )

                with gr.Row():
                    json_output = gr.Code(
                        label="Detailed JSON Output",
                        language="json",
                        interactive=False,
                    )

                analyze_btn.click(
                    analyze_audio,
                    inputs=audio_input,
                    outputs=[status_output, result_output, json_output],
                )

            # Tab 2: Batch Processing
            with gr.Tab("📂 Batch Process Folder"):
                gr.Markdown("""
Select a folder containing multiple audio files to process them all.
Results will be saved to `./file_results/`
                """)

                with gr.Row():
                    with gr.Column(scale=3):
                        folder_input = gr.Textbox(
                            label="Folder Path",
                            placeholder="e.g., ./audio_files/ or C:\\Users\\...\\audio\\",
                            interactive=True,
                        )

                    with gr.Column(scale=1):
                        batch_btn = gr.Button("▶️ Start Batch", scale=1, size="lg")

                batch_status = gr.Textbox(
                    label="Status",
                    interactive=False,
                    lines=2,
                )

                batch_summary = gr.Textbox(
                    label="Summary",
                    interactive=False,
                    lines=20,
                )

                batch_btn.click(
                    process_batch,
                    inputs=folder_input,
                    outputs=[batch_status, batch_summary],
                )

            # Tab 3: Info
            with gr.Tab("ℹ️ Info"):
                gr.Markdown("""
## Supported Audio Formats
- WAV (.wav)
- MP3 (.mp3)
- M4A (.m4a)
- OGG (.ogg)
- WebM (.webm)
- MP4 (.mp4)
- FLAC (.flac)

## Configuration
Set environment variables to customize behavior:

```bash
# AI gate threshold (0-100)
$env:REALTIME_FILE_AI_GATE_SCORE="45"

# Keywords that trigger AI analysis
$env:REALTIME_FILE_AI_TRIGGERS="otp,โอนเงิน,เจ้าหน้าที่,บัญชี"

# Alert cooldown in seconds
$env:REALTIME_FILE_ALERT_COOLDOWN_SECONDS="15"

# Output directory
$env:REALTIME_FILE_OUTPUT_DIR="./file_results"
```

## Risk Levels
- 🟢 **Low**: Score < 30
- 🟡 **Medium**: Score 30-60
- 🔴 **High**: Score > 60

## How It Works
1. Upload audio file or select folder
2. Script transcribes using Whisper STT
3. Detects Thai scam keywords
4. Runs AI analysis if score is high or keywords detected
5. Returns risk level and detailed analysis
6. Saves JSON result to `file_results/`

## Example Scam Keywords
- โอนเงิน (transfer money)
- เจ้าหน้าที่ (official)
- บัญชี (account)
- OTP
- ดำเนินคดี (legal action)
                """)

    demo.launch(server_name="127.0.0.1", server_port=7860, share=False)


if __name__ == "__main__":
    print("""
╔════════════════════════════════════════════════════════════════╗
║  Gradio UI for Scam Detection - File Based                     ║
║  Starting server...                                             ║
╚════════════════════════════════════════════════════════════════╝

Open your browser to: http://127.0.0.1:7860

Features:
  • Single file analysis with upload
  • Batch folder processing
  • Real-time STT transcription
  • Keyword detection + AI analysis
  • JSON result export
  • Risk scoring (0-100)
    """)
    main()
