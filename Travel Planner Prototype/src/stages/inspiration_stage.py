from __future__ import annotations

import json

from typing import Any
from src.schemas import InspirationStageInput, InspirationStageOutput
from src.session_manager import SessionManager
from src.stages.base import Stage


class InspirationStage(Stage):
    name = "inspiration"

    def __init__(self, llm_client: Any) -> None:
        self.llm_client = llm_client

    def run(self, stage_input: InspirationStageInput, session: SessionManager) -> InspirationStageOutput:
        schema_json = json.dumps(InspirationStageOutput.model_json_schema(), ensure_ascii=True)
        requested_count = stage_input.extracted_signals.get("request_additional_options", 3)
        try:
            target_count = int(requested_count)
        except (TypeError, ValueError):
            target_count = 3
        if target_count < 1:
            target_count = 1
        exclude_destinations = stage_input.extracted_signals.get("exclude_destinations", [])
        prompt = f"""
You are planning assistant stage: inspiration.
Return ONLY strict JSON matching this schema: {schema_json}

Traveler criteria:
{stage_input.criteria.model_dump()}

User-specific details:
{[item.model_dump() for item in stage_input.user_specifics]}

Constraints:
{stage_input.constraints}

Extracted signals:
{stage_input.extracted_signals}

Rules:
- Propose exactly {target_count} destinations in `top_destinations`.
- Include concise pros/cons, estimated total cost, estimated travel time.
- Recommend one best option with tradeoff-aware rationale.
- Respect constraints and high-confidence user specifics.
- Avoid proposing destinations in this exclusion list: {exclude_destinations}
- Fill `stage` with "inspiration".
"""
        output = self.llm_client.generate_structured(
            prompt=prompt,
            schema=InspirationStageOutput,
            cache_key=self.name if target_count == 3 else f"{self.name}_additional",
            metadata={
                "source": "criteria_to_inspiration",
                "target_count": target_count,
                "exclude_destinations": exclude_destinations,
            },
        )
        return output
