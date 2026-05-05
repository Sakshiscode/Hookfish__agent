FROM python:3.11-slim

WORKDIR /app

# Install system dependencies needed for audio processing and SSL
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libffi-dev \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies first (Docker layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY voice_agent.py .
COPY db_helper.py .
COPY google_calendar.py .
COPY whatsapp_helper.py .

# Copy Google credentials if present
COPY google_credentials.json* ./

# The voice agent listens on LiveKit WebSocket, not HTTP.
# But Fly.io needs a health check port, so we expose 8080.
EXPOSE 8080

# Run in production mode (not dev — no file watcher)
CMD ["python", "voice_agent.py", "start"]
