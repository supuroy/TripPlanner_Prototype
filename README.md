# TripPlanner (Wanderly AI Travel Planner)

Multi-stage agentic travel planning system pairing an interactive React frontend with a Cursor Cloud Agent-driven Python backend.

## Architecture

* **Frontend:** React + Vite + Tailwind CSS (`Figma Prototype/`)
* **Backend:** Python Multi-Stage Orchestrator & Wanderly SSE streaming server (`Travel Planner Prototype/`)
* **LLM Engine:** Powered by Cursor Cloud Agents API via `api.cursor.com`

---

## Running Locally

### Development Mode (Two Processes)
1. **Start the Python API server:**
   ```bash
   cd "Travel Planner Prototype"
   pip install -r requirements.txt
   python main.py --ui
   ```
   *Runs on `http://127.0.0.1:8000`.*

2. **Start the Vite frontend:**
   ```bash
   cd "Figma Prototype"
   npm install
   npm run dev
   ```
   *Runs on `http://localhost:5173`, proxying all `/api/*` and `/state` calls to port 8000.*

---

## Deploying as a Single Container (Cursor Apps / Production)

The Python backend automatically serves the compiled production React bundle if `Figma Prototype/dist/` exists:

1. **Build the frontend:**
   ```bash
   cd "Figma Prototype" && npm install && npm run build && cd ..
   ```
2. **Start the server:**
   ```bash
   python "Travel Planner Prototype/main.py" --ui --host 0.0.0.0 --port ${PORT:-8000}
   ```
   *The entire application runs as a single web service on the assigned `$PORT`.*
