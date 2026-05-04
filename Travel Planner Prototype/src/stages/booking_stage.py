from __future__ import annotations

from src.schemas import BookingStageInput, PlaceholderStageOutput, RecommendationItem
from src.session_manager import SessionManager
from src.stages.base import Stage


class BookingStage(Stage):
    name = "booking_planning"

    def run(self, stage_input: BookingStageInput, session: SessionManager) -> PlaceholderStageOutput:
        return PlaceholderStageOutput(
            stage=self.name,
            inputs_considered=["criteria", "chosen_destination", "proposed_dates"],
            assumptions=["No live pricing APIs are connected yet."],
            missing_info=["Preferred airport", "Hotel style"],
            recommendations=[
                RecommendationItem(title="Collect candidates", detail="Prepare shortlist of flights/hotels.")
            ],
            reasoning_summary="Placeholder booking stage until provider integrations are added.",
            details={
                "candidate_flights": ["Mock flight A", "Mock flight B"],
                "candidate_hotels": ["Mock hotel A", "Mock hotel B"],
                "date_refinement": "Aim for flexible +/- 2 days to reduce cost.",
            },
        )
