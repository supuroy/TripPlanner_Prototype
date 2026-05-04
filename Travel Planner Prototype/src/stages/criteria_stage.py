from __future__ import annotations

import json
import re

from typing import Any
from src.schemas import CriteriaStageInput, CriteriaStageOutput
from src.session_manager import SessionManager
from src.stages.base import Stage


class CriteriaStage(Stage):
    name = "criteria_gathering"

    def __init__(self, llm_client: Any) -> None:
        self.llm_client = llm_client

    def run(self, stage_input: CriteriaStageInput, session: SessionManager) -> CriteriaStageOutput:
        schema_json = json.dumps(CriteriaStageOutput.model_json_schema(), ensure_ascii=True)
        prompt = f"""
You are planning assistant stage: criteria gathering.
Return ONLY strict JSON matching this schema: {schema_json}

User message: {stage_input.user_message}
Prior context: {stage_input.prior_context}

Rules:
- Extract explicit and implicit decision factors.
- Prioritize the current user message over prior context for trip-specific preferences.
- Use prior context only for stable identity details (e.g., origin, group composition) when the user message is silent.
- Identify missing critical inputs.
- Add focused follow-up questions.
- Keep assumptions realistic and minimal.
- Fill canonical fields in `extracted_criteria` when clear.
- Capture user-specific details not covered by canonical fields in `user_specifics`.
- Fill `traveler_profile_hints` for these prioritized profile fields when inferable:
  `traveler_origin`, `group_size`, `group_type`, `accommodation_style`,
  `pace_preference`, `travel_time_tolerance`, `preferred_climate`, `trip_type`.
- For each hint include confidence and source text.
- Put hard constraints and must-have limits in `constraints`.
- Put extra metadata and latent signals in `extracted_signals`.
- If a numeric budget appears (e.g. "$3000", "under 3000", "3k"), set `extracted_criteria.budget_max_usd`.
- Fill `stage` with "criteria_gathering".
"""
        output = self.llm_client.generate_structured(
            prompt=prompt,
            schema=CriteriaStageOutput,
            cache_key=self.name,
            metadata={"user_message": stage_input.user_message},
        )
        parsed_budget = self._extract_budget_max_usd(stage_input.user_message)
        if output.extracted_criteria.budget_max_usd is None and parsed_budget is not None:
            output.extracted_criteria.budget_max_usd = parsed_budget
            output.assumptions.append(
                f"Budget cap inferred deterministically from user message: {int(parsed_budget)} USD."
            )
            output.missing_info = [
                item for item in output.missing_info if "budget" not in item.lower()
            ]
        return output

    @staticmethod
    def _extract_budget_max_usd(text: str) -> float | None:
        lower = text.lower()

        keyword_pattern = re.search(
            r"(under|below|less than|max(?:imum)?|up to)\s+\$?\s*(\d[\d,]*(?:\.\d+)?)\s*(k)?",
            lower,
        )
        if keyword_pattern:
            amount = float(keyword_pattern.group(2).replace(",", ""))
            if keyword_pattern.group(3):
                amount *= 1000
            return amount

        dollar_pattern = re.search(r"\$\s*(\d[\d,]*(?:\.\d+)?)\s*(k)?", lower)
        if dollar_pattern:
            amount = float(dollar_pattern.group(1).replace(",", ""))
            if dollar_pattern.group(2):
                amount *= 1000
            return amount

        return None
