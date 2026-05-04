from __future__ import annotations

from src.schemas import ActivitiesStageInput, PlaceholderStageOutput, RecommendationItem
from src.session_manager import SessionManager
from src.stages.base import Stage


class ActivitiesStage(Stage):
    name = "activities_planning"

    def run(self, stage_input: ActivitiesStageInput, session: SessionManager) -> PlaceholderStageOutput:
        return PlaceholderStageOutput(
            stage=self.name,
            inputs_considered=["criteria", "chosen_destination"],
            assumptions=["Only mock activity inventory is available."],
            missing_info=["Mobility constraints", "Preferred activity intensity"],
            recommendations=[
                RecommendationItem(title="Slot experiences", detail="Map activities into morning/afternoon blocks.")
            ],
            reasoning_summary="Placeholder activities stage for future booking integration.",
            details={
                "bookable_experiences": ["Mock city tour", "Mock food walk"],
                "schedule_placement": "Place high-priority activity on Day 2 morning.",
            },
        )
