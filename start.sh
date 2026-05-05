#!/bin/bash
export PORT="${PORT:-8000}"
uvicorn api.main:app --host 0.0.0.0 --port $PORT
