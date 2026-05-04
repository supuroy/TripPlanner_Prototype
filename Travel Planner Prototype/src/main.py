from __future__ import annotations

import argparse

from src.llm import CursorLlmClient
from src.orchestrator import TravelPlannerOrchestrator
from src.session_manager import SessionManager
from src.stages import ActivitiesStage, BookingStage, CriteriaStage, InspirationStage, ItineraryStage
from src.tools import ActivitiesProvider, ReviewsProvider, WeatherProvider
from src.ui.cli import run_cli
from src.ui.web_server import run_web_server


def build_orchestrator(replay_mode: bool = False, *, for_web_ui: bool = False) -> TravelPlannerOrchestrator:
    llm_client = CursorLlmClient()
    if for_web_ui:
        print(
            "[ui] Planner LLM: Cursor Cloud Agents (criteria, inspiration, tools, comparisons). "
            "Wanderly smoke/refine also use Cursor.",
            flush=True,
        )
    llm_client.set_replay_mode(replay_mode)
    session = SessionManager()
    return TravelPlannerOrchestrator(
        session=session,
        criteria_stage=CriteriaStage(llm_client=llm_client),
        inspiration_stage=InspirationStage(llm_client=llm_client),
        itinerary_stage=ItineraryStage(),
        booking_stage=BookingStage(),
        activities_stage=ActivitiesStage(),
        weather_provider=WeatherProvider(llm_client=llm_client),
        reviews_provider=ReviewsProvider(llm_client=llm_client),
        activities_provider=ActivitiesProvider(llm_client=llm_client),
        comparator_provider=None,
        use_local_comparator=False,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Multi-stage AI travel planner prototype.")
    parser.add_argument(
        "--input",
        default="I want to go somewhere warm in March under $3000",
        help="Natural language user request.",
    )
    parser.add_argument("--ui", action="store_true", help="Run browser UI server.")
    parser.add_argument("--replay", action="store_true", help="Replay from cached LLM responses.")
    parser.add_argument("--host", default="127.0.0.1", help="Web UI host.")
    parser.add_argument("--port", type=int, default=8000, help="Web UI port.")
    args = parser.parse_args()

    if args.ui:
        run_web_server(
            orchestrator_factory=lambda: build_orchestrator(
                replay_mode=args.replay,
                for_web_ui=True,
            ),
            host=args.host,
            port=args.port,
        )
        return

    orchestrator = build_orchestrator(replay_mode=args.replay)
    try:
        run_cli(orchestrator, user_message=args.input)
    finally:
        orchestrator.shutdown()


if __name__ == "__main__":
    main()
