# Thai Voice Scam Detector

A production-ready Python CLI tool for **Thai voice scam detection** using local Whisper transcription + hybrid scam analysis (keyword rules + AI-style reasoning).

## Features

- Accepts `.wav` and `.mp3` files
- Transcribes Thai speech with local Whisper (`language="th"`)
- Detects scam-related Thai keywords
- Loads extra keywords/categories from external JSON (`keywords.json` or `SCAM_KEYWORDS_FILE`)
- Calculates risk score (`0-100`) and risk level (`low`, `medium`, `high`)
- Uses mixed scoring (weighted keyword rules + probability signal)
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

## Realtime on PC (Phone -> PC -> Telegram)

Run this file on PC:

```bash
python realtime_pc.py
```

This mode keeps the same STT + correction + keyword/rule core and adds:

- chunked realtime processing
- sliding window with overlap
- AI gate for cost/latency control
- Telegram alert cooldown

Recommended realtime settings:

```bash
$env:REALTIME_CHUNK_SECONDS="2"
$env:REALTIME_WINDOW_SECONDS="8"
$env:REALTIME_OVERLAP_RATIO="0.5"
$env:REALTIME_AI_GATE_SCORE="45"
$env:REALTIME_AI_TRIGGERS="otp,โอนเงิน,เจ้าหน้าที่,บัญชี"
$env:REALTIME_ALERT_COOLDOWN_SECONDS="15"
$env:REALTIME_INPUT_DIR="./incoming_chunks"
```

### How phone audio reaches PC

`realtime_pc.py` reads chunk files from `REALTIME_INPUT_DIR`.

Common paths to feed chunks from phone:

1. Phone app records 2-3s chunks and uploads/copies to shared folder on PC.
2. Sync tool (e.g., Syncthing) mirrors chunk files from phone folder to PC folder.
3. A phone companion script/app sends chunk files to PC over local network.

As soon as files appear in the folder, realtime analysis starts automatically.

## Realtime on PC with Microphone (Direct Speaker -> PC Mic)

**Alternative: No file transfer needed!** Place the phone on speaker and let the PC microphone pick up the audio.

Run this file on PC:

```bash
python realtime_pc_mic.py
```

When prompted, press Enter to start recording. The script will:
- Continuously record from the default PC microphone
- Build a sliding window every 2-3 seconds
- Detect and alert on scams in real-time
- Save results to `./mic_results/`

Recommended mic settings:

```bash
$env:REALTIME_MIC_CHUNK_SECONDS="2"
$env:REALTIME_MIC_WINDOW_SECONDS="8"
$env:REALTIME_MIC_OVERLAP_RATIO="0.5"
$env:REALTIME_MIC_AI_GATE_SCORE="45"
$env:REALTIME_MIC_AI_TRIGGERS="otp,โอนเงิน,เจ้าหน้าที่,บัญชี"
$env:REALTIME_MIC_ALERT_COOLDOWN_SECONDS="15"
$env:REALTIME_MIC_DEVICE="-1"              # -1 = default device
$env:REALTIME_MIC_SAMPLE_RATE="16000"     # Hz
$env:REALTIME_MIC_OUTPUT_DIR="./mic_results"
```

### Setup (Windows)

1. Install sounddevice (if not already in requirements.txt):
   ```bash
   pip install sounddevice
   ```

2. Set default recording device:
   - Right-click speaker icon → Open Sound settings
   - Scroll to "Advanced" → App volume and device preferences
   - Find Python → Set to the microphone capturing your phone's speaker
   - (Or physically move the USB mic closer to your phone speaker)

3. Verify mic works:
   ```bash
   python -m sounddevice
   # Shows all audio devices. Note the device number for your mic.
   # Set REALTIME_MIC_DEVICE=<number> if not using default.
   ```

### Pros & Cons

| Mic Approach | File Approach |
|---|---|
| ✓ No file transfer | ✗ Need to move chunks |
| ✓ Real-time capture | ✓ Can batch transfer |
| ✗ Room noise affects accuracy | ✓ Cleaner audio |
| ✗ Phone must stay nearby | ✓ Phone can be elsewhere |

## Quick Start (Recommended)

1. Install dependencies

```bash
pip install -r requirements.txt
```

2. Enable auto-learning (default is enabled, but set explicitly in production)

```bash
$env:STT_AUTO_PROMOTE="1"
$env:STT_AUTO_PROMOTE_THRESHOLD="0.82"
$env:STT_AUTO_PROMOTE_MIN_OCCURRENCES="2"
$env:STT_AUTO_PROMOTE_BATCH="200"
```

3. Launch UI

```bash
python mic_ui.py
```

4. Upload/record calls continuously. The system will:

- correct text in runtime
- store learning evidence in SQLite
- promote high-confidence corrections into dictionary automatically

## Run Microphone UI

```bash
python mic_ui.py
```

Open browser at `http://127.0.0.1:7860` then record from microphone or upload audio.

## Auto Learning (Create/Select/Promote)

The STT layer now uses an automatic pipeline for scalable correction growth:

1. `Create`: generate phonetic candidates around corrected terms
2. `Select`: score and filter candidates by confidence + recurrence
3. `Promote`: write high-confidence mappings into `thai_stt_corrections.json` under `common_errors.learned_auto`

Data is stored in `correction_learning.db` (SQLite) for large-scale learning without bloating JSON.

Runtime artifacts:

- `thai_stt_corrections.json`: primary correction dictionary
- `thai_stt_corrections.json -> common_errors.learned_auto`: auto-promoted entries
- `correction_learning.json`: learner history log
- `correction_learning.db`: scalable evidence store for create/select/promote

Environment knobs:

```bash
$env:STT_AUTO_PROMOTE="1"
$env:STT_AUTO_PROMOTE_THRESHOLD="0.82"
$env:STT_AUTO_PROMOTE_MIN_OCCURRENCES="2"
$env:STT_AUTO_PROMOTE_BATCH="200"
```

### Auto-Learning Profiles

Conservative profile (fewer false positives):

```bash
$env:STT_AUTO_PROMOTE="1"
$env:STT_AUTO_PROMOTE_THRESHOLD="0.88"
$env:STT_AUTO_PROMOTE_MIN_OCCURRENCES="3"
$env:STT_AUTO_PROMOTE_BATCH="80"
```

Balanced profile (recommended):

```bash
$env:STT_AUTO_PROMOTE="1"
$env:STT_AUTO_PROMOTE_THRESHOLD="0.82"
$env:STT_AUTO_PROMOTE_MIN_OCCURRENCES="2"
$env:STT_AUTO_PROMOTE_BATCH="200"
```

Aggressive profile (learn faster, more review needed):

```bash
$env:STT_AUTO_PROMOTE="1"
$env:STT_AUTO_PROMOTE_THRESHOLD="0.78"
$env:STT_AUTO_PROMOTE_MIN_OCCURRENCES="1"
$env:STT_AUTO_PROMOTE_BATCH="400"
```

Disable promotion temporarily:

```bash
$env:STT_AUTO_PROMOTE="0"
```

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

## External Keyword File

The detector auto-loads external keyword config from:

- `keywords.json` (project root, default)
- or custom file via environment variable: `$env:SCAM_KEYWORDS_FILE="C:\path\to\my_keywords.json"`

Supported JSON shape:

```json
{
  "replace": false,
  "keywords": ["custom term"],
  "categories": {
    "urgency": ["..."] ,
    "financial": ["..."],
    "sensitive": ["..."],
    "impersonation": ["..."],
    "link_install": ["..."],
    "legal_threat": ["..."],
    "fraud_context": ["..."]
  },
  "weights": {
    "base_keyword": 7,
    "keyword_cap": 50,
    "urgency": 9,
    "financial": 11,
    "sensitive": 17,
    "impersonation": 15,
    "link_install": 13,
    "legal_threat": 14,
    "fraud_context": 16,
    "probability_mix": 0.35,
    "high_threshold": 68,
    "medium_threshold": 38
  }
}
```

Notes:

- `replace: false` means merge with built-in defaults.
- `replace: true` means use only what is in the JSON file.
- Invalid JSON is ignored safely (system falls back to built-in defaults).

## Run Unit Tests

```bash
python -m unittest discover -s tests -v
```

The test suite contains 26 Thai sample messages (both scam and benign) to validate score/risk behavior and external keyword loading.

## Monitoring and Validation

Track learning growth:

- check `correction_learning.db` size over time
- watch `common_errors.learned_auto` in `thai_stt_corrections.json`
- sample outputs from real calls weekly and verify correction quality

Recommended weekly maintenance:

1. Review new entries in `learned_auto`
2. Keep good entries and remove wrong mappings
3. Keep thresholds conservative in production unless recall is too low

## Known Limits

- No rule-based system can guarantee 100% future-case coverage.
- Best results come from continuous real-world data and periodic dictionary review.
- Aggressive auto-promotion may introduce noisy mappings if thresholds are too low.

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
