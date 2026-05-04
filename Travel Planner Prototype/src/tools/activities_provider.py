from __future__ import annotations

import json
import hashlib
from typing import Any, Dict, List

from pydantic import BaseModel, Field

from typing import Any
from src.tools.base_provider import BaseProvider


class ActivitiesPayload(BaseModel):
    top_five: List[str] = Field(default_factory=list)
    rationale_summary: str
    sources: List[str] = Field(default_factory=list)


class ActivitiesProvider(BaseProvider):
    name = "activities_provider"

    def __init__(self, llm_client: Any) -> None:
        self.llm_client = llm_client

    def fetch(self, query: Dict[str, Any]) -> Dict[str, Any]:
        destination = query.get("destination", "")
        traveler_profile = query.get("traveler_profile", {})
        criteria = query.get("criteria", {})
        profile_fp = hashlib.sha1(
            json.dumps(traveler_profile or {}, sort_keys=True, ensure_ascii=True).encode("utf-8")
        ).hexdigest()[:10]
        criteria_fp = hashlib.sha1(
            json.dumps(criteria or {}, sort_keys=True, ensure_ascii=True).encode("utf-8")
        ).hexdigest()[:10]
        domains = [
            "tripadvisor.com",
            "getyourguide.com",
            "viator.com",
            "atlasobscura.com",
        ]
        prompt = f"""
You are an activity-planning summarizer.
Use only these domains conceptually: {domains}
Return strict JSON matching this schema:
{json.dumps(ActivitiesPayload.model_json_schema(), ensure_ascii=True)}

Destination: {destination}
Traveler profile:
{traveler_profile}
Criteria:
{criteria}

Rules:
- Return exactly 5 activity suggestions in `top_five`.
- Match all suggestions to traveler profile and criteria (pace, activities, budget/luxury cues, group composition, pet context).
- Prioritize activities that can satisfy mixed preferences when profile indicates different traveler interests.
- Avoid suggestions that conflict with obvious profile constraints.
- Include source domains in `sources`.
"""
        payload = self.llm_client.generate_structured(
            prompt=prompt,
            schema=ActivitiesPayload,
            cache_key=f"activities_enrichment::{destination}::{profile_fp}::{criteria_fp}",
            metadata={
                "provider": self.name,
                "destination": destination,
                "profile_fp": profile_fp,
                "criteria_fp": criteria_fp,
            },
            use_cache_if_available=True,
        )
        return payload.model_dump()
