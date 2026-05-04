from __future__ import annotations

import json

from src.orchestrator import TravelPlannerOrchestrator


def run_cli(orchestrator: TravelPlannerOrchestrator, user_message: str) -> None:
    result = orchestrator.run_initial_flow(user_message)
    print(json.dumps(result, indent=2))
