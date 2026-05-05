# Hookfish: AI Cold-Calling Platform

Hookfish is a high-performance, low-latency AI voice agent platform designed for real estate outbound calling. It integrates multi-cloud LLMs, TTS, and STT engines with real-time telephony via LiveKit.

## 🚀 Features
- **Low Latency**: Near-human response times (sub-1s).
- **Hinglish Support**: Natural Indian English and Hindi conversation.
- **Multimodal Integration**: WhatsApp follow-ups, Google Calendar scheduling, and CRM logging.
- **Batch Campaigns**: Trigger multiple calls in parallel.

---

## 🛠️ Local Setup

### 1. Prerequisites
- **Python 3.10+**
- **LiveKit CLI**: [Install Guide](https://docs.livekit.io/realtime/cli/)
- **LiveKit Cloud Account**: For WebRTC transport.

### 2. Installation
```bash
# Clone the repository
git clone https://github.com/vineet-78/EstateEcho.git
cd EstateEcho

# Create and activate virtual environment
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### 3. Environment Variables
Create a `.env` file in the root directory (refer to `.env.example` if available). Key variables include:
- `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`
- `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT`
- `SMALLEST_API_KEY` (for TTS)
- `DEEPGRAM_API_KEY` (for STT)
- `DB_HOST`, `DB_USER`, `DB_PASSWORD` (Aiven MySQL)

---

## 🏃 Running Locally

### 1. Start the Voice Agent
The agent listens for room requests from LiveKit and handles the conversation logic.
```bash
python voice_agent.py dev
```

### 2. Trigger an Outbound Call
Use the trigger script to dispatch the agent to a specific phone number.
```bash
# Edit batch_call_trigger.py to add your number to BATCH_NUMBERS
python batch_call_trigger.py
```

### 3. Run the Dashboard API
Starts the FastAPI backend for the management dashboard.
```bash
python -m api.main
```
The API will be available at `http://localhost:8000`.

---

## 🚢 Deployment (Fly.io)

The project is configured for deployment on **Fly.io** using separate apps for the Agent and the API.

### Prerequisites
- Install [Fly CLI](https://fly.io/docs/hands-on/install-flyctl/).
- Login via `fly auth login`.

### 1. Deploy the Voice Agent
The agent uses `Dockerfile.agent` and `fly.toml`.
```bash
fly deploy -c fly.toml
```

### 2. Deploy the Dashboard API
The API uses `api.Dockerfile` and `fly.api.toml`.
```bash
fly deploy -c fly.api.toml
```

### Secrets Management
Set production secrets on Fly.io:
```bash
fly secrets set KEY=VALUE -c fly.toml
```

---

## 🧪 Testing & Debugging
- **Test Database**: `python test_db_connection.py`
- **Test TTS**: `python test_smallest_tts.py`
- **Test Calendar**: `python test_calendar.py`
- **Latency Benchmark**: `python benchmark_azure.py`

---

## 🤝 Contributing
For internal KT details, refer to the [KT_Walkthrough.md](KT_Walkthrough.md).
