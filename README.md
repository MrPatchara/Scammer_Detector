# Thai Voice Scam Detector

A production-ready Python CLI tool for **Thai voice scam detection** using local Whisper transcription + hybrid scam analysis (keyword rules + AI-style reasoning).

## Features

- Accepts `.wav` and `.mp3` files
- Transcribes Thai speech with local Whisper (`language="th"`)
- Detects scam-related Thai keywords
- Calculates risk score (`0-100`) and risk level (`low`, `medium`, `high`)
- Performs AI-style analysis using prompt structure (simulation fallback)
- Colored terminal output
- Keyword highlighting in transcribed text
- Saves full output to JSON
- Microphone testing UI (Gradio)
- Gemini (free-tier capable) with automatic fallback to keyword/rule engine
- Telegram alerting for high-risk detection

## Project Structure

```text
/scam_detector
├── app/
│   ├── __init__.py
│   ├── ai_analysis.py
│   ├── alerts.py
│   ├── cli.py
│   ├── config.py
│   ├── exceptions.py
│   ├── pipeline.py
│   ├── risk.py
│   ├── stt.py
│   ├── ui.py
│   └── utils.py
├── main.py
├── mic_ui.py
├── detector.py
├── ai_module.py
├── requirements.txt
└── README.md
```

### Architecture Notes

- `app/stt.py`: Thai speech-to-text service (Whisper loaded once via singleton)
- `app/risk.py`: keyword detection and risk scoring logic
- `app/ai_analysis.py`: prompt template + API-ready AI analysis flow
- `app/pipeline.py`: shared end-to-end analysis flow (used by CLI and UI)
- `app/alerts.py`: Telegram notification integration
- `app/ui.py`: microphone/upload testing UI
- `app/cli.py`: CLI orchestration and result output
- `app/config.py`: centralized constants and keyword lists
- `detector.py` and `ai_module.py`: compatibility layer for legacy imports

## Installation

1. Create and activate a virtual environment:

```bash
python -m venv .venv
# Windows PowerShell
.venv\Scripts\Activate.ps1
```

2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. (Optional) If you want to connect a real LLM API later, set:

```bash
# Windows PowerShell
$env:LLM_API_KEY="your-api-key"
```

## How to Run

```bash
python main.py test.wav
# or
python main.py test.mp3
```

## Run Microphone UI

```bash
python mic_ui.py
```

Open browser at `http://127.0.0.1:7860` then record from microphone or upload audio.

## Gemini + Fallback Modes

```bash
# auto | gemini | keyword
$env:AI_MODE="auto"

# Gemini settings
$env:GEMINI_API_KEY="your-gemini-key"
$env:GEMINI_MODEL="gemini-1.5-flash"
$env:GEMINI_TIMEOUT_SEC="7"
$env:GEMINI_COOLDOWN_SEC="600"
```

Behavior:

- `auto`: try Gemini first, fallback to keyword/rules if unavailable or quota exhausted
- `gemini`: force Gemini attempt first, still fallback if call fails
- `keyword`: always use local keyword/rule engine (lowest latency)

## Telegram Alert Setup

```bash
$env:TELEGRAM_BOT_TOKEN="123456:ABC..."
$env:TELEGRAM_CHAT_ID="-100xxxxxxxxxx"
$env:ALERT_MIN_RISK_LEVEL="high"
$env:ALERT_MIN_SCORE="70"
```

When risk passes threshold, the system sends alert details to Telegram automatically.

## Example Output

```text
=== Thai Voice Scam Detection Result ===
Transcribed text: กรุณาโอนเงินทันทีเพื่อยืนยันตัวตน และแจ้ง OTP
Keywords found: โอนเงิน, ทันที, ยืนยันตัวตน, OTP
Risk score: 88
Risk level: high
AI analysis result (JSON):
{
  "risk_level": "high",
  "score": 88,
  "is_scam": true,
  "keywords_detected": [
    "โอนเงิน",
    "OTP",
    "ทันที",
    "ยืนยันตัวตน"
  ],
  "reason": "พบเจตนาทางการเงินหรือคำที่เกี่ยวกับบัญชี | พบแรงกดดันด้านเวลา | พบการร้องขอข้อมูลอ่อนไหว"
}

Saved full analysis to: test_analysis.json
```

## AI Prompt Used

The AI module uses this exact prompt structure:

```text
You are a scam detection AI.

Analyze this Thai text:
"{text}"

Return JSON:
{
"risk_level": "low | medium | high",
"score": 0-100,
"is_scam": true/false,
"keywords_detected": [],
"reason": "short explanation"
}

Rules:

* Financial requests increase risk
* Urgency increases risk
* Asking for personal data = high risk
* Impersonation (เจ้าหน้าที่) = high risk
```

If Gemini is not configured or unavailable, the system automatically falls back to deterministic heuristic simulation.

## Future Improvements

- Real-time microphone streaming detection
- Speaker diarization and impersonation pattern detection
- Raspberry Pi deployment with lightweight Whisper model tuning
- Dashboard/API mode for fraud operations teams
- Model-assisted Thai intent classification with active learning
