from __future__ import annotations

import json
import hashlib
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from typing import Any
from src.tools.base_provider import BaseProvider


class WeatherPayload(BaseModel):
    summary: str
    average_high_c: Optional[float] = None
    average_low_c: Optional[float] = None
    rain_outlook: str
    outdoor_comfort_note: str
    sources: List[str] = Field(default_factory=list)


class WeatherProvider(BaseProvider):
    name = "weather_provider"

    def __init__(self, llm_client: Any) -> None:
        self.llm_client = llm_client

    def fetch(self, query: Dict[str, Any]) -> Dict[str, Any]:
        destination = query.get("destination", "")
        travel_window = query.get("travel_window", "")
        traveler_profile = query.get("traveler_profile", {})
        profile_fp = hashlib.sha1(
            json.dumps(traveler_profile or {}, sort_keys=True, ensure_ascii=True).encode("utf-8")
        ).hexdigest()[:10]
        prompt = f"""
You are a travel planning assistant providing seasonal weather context for trip planning.
Based on your training knowledge, provide a plausible weather summary for this destination and travel window.
Return strict JSON matching this schema:
{json.dumps(WeatherPayload.model_json_schema(), ensure_ascii=True)}

Destination: {destination}
Travel window: {travel_window}

Rules:
- Give a concise seasonal weather summary for the trip period.
- Include a likely temperature range and rain outlook based on typical conditions.
- Set `sources` to an empty list.
"""
        payload = self.llm_client.generate_structured(
            prompt=prompt,
            schema=WeatherPayload,
            cache_key=f"weather_enrichment::{destination}::{travel_window}::{profile_fp}",
            metadata={
                "provider": self.name,
                "destination": destination,
                "travel_window": travel_window,
                "profile_fp": profile_fp,
            },
            use_cache_if_available=True,
        )
        return payload.model_dump()
