#!/bin/bash
set -e

echo "[setup] Building React frontend (Figma Prototype)..."
cd "Figma Prototype"
npm install
npm run build
cd ..

echo "[setup] Installing Python dependencies..."
pip install -r "Travel Planner Prototype/requirements.txt"

echo "[start] Launching unified Travel Planner server on port ${PORT:-8000}..."
exec python "Travel Planner Prototype/main.py" --ui --host 0.0.0.0 --port "${PORT:-8000}"
