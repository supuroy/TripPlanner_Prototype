from __future__ import annotations

from src.schemas import ItineraryStageInput, PlaceholderStageOutput, RecommendationItem
from src.session_manager import SessionManager
from src.stages.base import Stage


class ItineraryStage(Stage):
    name = "itinerary_formation"

    def run(self, stage_input: ItineraryStageInput, session: SessionManager) -> PlaceholderStageOutput:
        return PlaceholderStageOutput(
            stage=self.name,
            inputs_considered=["criteria", "chosen_destination"],
            assumptions=["Destination and dates are still flexible."],
            missing_info=["Exact date range", "Daily pace preference"],
            recommendations=[
                RecommendationItem(title="Draft skeleton", detail="Build a day-by-day rough plan placeholder.")
            ],
            reasoning_summary="Placeholder itinerary stage for future enhancement.",
            details={
                "trip_structure": ["Travel day", "Exploration days", "Return day"],
                "travel_mode_options": ["Flight", "Train (if regional)"],
                "timing_notes": "Refine once exact dates are known.",
                "tradeoffs": "Lower cost often means longer transit.",
            },
        )
