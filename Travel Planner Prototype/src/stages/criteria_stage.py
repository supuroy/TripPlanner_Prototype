from __future__ import annotations

import json
import re

from typing import Any

from src.schemas import CriteriaStageInput, CriteriaStageOutput
from src.session_manager import SessionManager
from src.stages.base import Stage
from src.wanderly.itinerary_debug_log import log_itinerary_build


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
- `extracted_criteria.budget_max_usd`: **total trip budget cap in USD** when the user states one (e.g. 3000 for "$3k" or "three thousand dollars for the trip").
  Use **null** when no clear total-trip budget is stated. Never use pet fees, baggage-only lines, per-day meal estimates, or unrelated small dollar amounts as the trip cap.
- Never treat pet weight, child age, or mileage (e.g. "under 15 lb", "12 years old", "50 miles") as trip budget.
- Never treat "under 8 days" / "up to 2 weeks" as a dollar budget.
- Fill `stage` with "criteria_gathering".
"""
        output = self.llm_client.generate_structured(
            prompt=prompt,
            schema=CriteriaStageOutput,
            cache_key=self.name,
            metadata={"user_message": stage_input.user_message},
        )

        raw_llm = output.extracted_criteria.budget_max_usd
        try:
            llm_budget = float(raw_llm) if raw_llm is not None else None
        except (TypeError, ValueError):
            llm_budget = None

        parsed_budget = CriteriaStage._extract_budget_max_usd(stage_input.user_message)

        # LLM-first: keep the structured model value unless it is clearly garbage; then backfill with regex only if helpful.
        if llm_budget is not None and 0 < llm_budget < 150:
            output.extracted_criteria.budget_max_usd = None
            output.assumptions.append(
                "Dropped model budget_max_usd under $150 (not a credible whole-trip cap — often a misread)."
            )
            llm_budget = None

        if output.extracted_criteria.budget_max_usd is None and parsed_budget is not None:
            output.extracted_criteria.budget_max_usd = parsed_budget
            output.assumptions.append(
                f"Filled budget_max_usd from message heuristics: ~{int(parsed_budget)} USD (model left it unset)."
            )
            output.missing_info = [item for item in output.missing_info if "budget" not in item.lower()]
        elif llm_budget is not None and parsed_budget is not None and abs(parsed_budget - llm_budget) / max(llm_budget, 1.0) > 0.35:
            output.assumptions.append(
                f"Budget note: model={int(llm_budget)} USD vs heuristic parse={int(parsed_budget)} USD; keeping model."
            )

        final_b = output.extracted_criteria.budget_max_usd
        try:
            fb = float(final_b) if final_b is not None else None
        except (TypeError, ValueError):
            fb = None
        log_itinerary_build(
            f"path=criteria_gathering.budget final={fb!r} llm_raw={raw_llm!r} parsed_heuristic={parsed_budget!r} "
            f"msg_len={len(stage_input.user_message or '')}"
        )

        return output

    @staticmethod
    def _budget_window(lower: str, start: int, end: int, span: int = 120) -> str:
        lo = max(0, start - span)
        hi = min(len(lower), end + span)
        return lower[lo:hi]

    @staticmethod
    def _has_budget_intent(window: str) -> bool:
        return bool(
            re.search(
                r"\b(budget|spend(?:ing)?|afford|cap|usd|dollars?|total\s*(?:for\s*)?(?:the\s*)?trip|"
                r"trip\s*(?:cost|budget)|for\s+the\s+whole\s+trip|all-?in|including\s+flights)\b",
                window,
            )
        )

    @staticmethod
    def _extract_budget_max_usd(text: str) -> float | None:
        """Conservative heuristics — only used when the model leaves ``budget_max_usd`` unset."""
        lower = (text or "").lower()

        range_k = re.search(r"\b(\d)\s*-\s*(\d)\s*k\b", lower)
        if range_k and CriteriaStage._has_budget_intent(
            CriteriaStage._budget_window(lower, range_k.start(), range_k.end())
        ):
            return float(range_k.group(2)) * 1000.0

        keyword_pattern = re.search(
            r"(under|below|less than|max(?:imum)?|up to)\s+\$?\s*(\d[\d,]*(?:\.\d+)?)\s*(k)?",
            lower,
        )
        if keyword_pattern:
            tail = lower[keyword_pattern.end() : keyword_pattern.end() + 36]
            if re.match(
                r"\s*(lb|lbs|pound|pounds|oz|kg|gram|grams|year|years|yr|month|months|old|"
                r"mi|mile|miles|ft|foot|feet|inch|inches|"
                r"day|days|night|nights|week|weeks|wk|hour|hours|hrs|minute|minutes|"
                r"people|persons|guests|travelers|pax|adults|kids|children|stops?|connections?|"
                r"rooms?)\b",
                tail,
            ):
                keyword_pattern = None
        if keyword_pattern:
            amount = float(keyword_pattern.group(2).replace(",", ""))
            if keyword_pattern.group(3):
                amount *= 1000
            win = CriteriaStage._budget_window(lower, keyword_pattern.start(), keyword_pattern.end())
            if amount < 500 and not keyword_pattern.group(3) and not CriteriaStage._has_budget_intent(win):
                pass
            else:
                return amount

        best: float | None = None
        for m in re.finditer(r"\$\s*(\d[\d,]*(?:\.\d+)?)\s*(k)?", lower):
            amount = float(m.group(1).replace(",", ""))
            if m.group(2):
                amount *= 1000
            if amount < 200 and not m.group(2):
                continue
            win = CriteriaStage._budget_window(lower, m.start(), m.end())
            if amount < 500 and not m.group(2) and not CriteriaStage._has_budget_intent(win):
                continue
            best = amount if best is None else max(best, amount)
        return best
