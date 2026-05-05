FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the API folder and helpers
COPY api/ ./api/
COPY db_helper.py .
COPY batch_call_campaign.py .
COPY call_trigger.py .
COPY batch_call_trigger.py .

# Expose port for Fly.io routing
EXPOSE 8080

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8080"]
