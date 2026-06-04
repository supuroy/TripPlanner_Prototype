from __future__ import annotations

import json
import hashlib
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from typing import Any
from src.tools.base_provider import BaseProvider


class ReviewsPayload(BaseModel):
    sentiment_summary: str
    common_pros: List[str] = Field(default_factory=list)
    common_cons: List[str] = Field(default_factory=list)
    traveler_fit_note: str
    sources: List[str] = Field(default_factory=list)


class ReviewsProvider(BaseProvider):
    name = "reviews_provider"

    def __init__(self, llm_client: Any) -> None:
        self.llm_client = llm_client

    def fetch(self, query: Dict[str, Any]) -> Dict[str, Any]:
        destination = query.get("destination", "")
        traveler_profile = query.get("traveler_profile", {})
        profile_fp = hashlib.sha1(
            json.dumps(traveler_profile or {}, sort_keys=True, ensure_ascii=True).encode("utf-8")
        ).hexdigest()[:10]
        prompt = f"""
You are a travel planning assistant providing destination sentiment context for trip planning.
Based on your training knowledge, summarize typical traveler experiences for this destination.
Return strict JSON matching this schema:
{json.dumps(ReviewsPayload.model_json_schema(), ensure_ascii=True)}

Destination: {destination}
Traveler profile:
{traveler_profile}

Rules:
- Summarize typical traveler sentiment and practical fit for this traveler profile.
- Keep pros/cons high-signal and concise.
- Set `sources` to an empty list.
"""
        payload = self.llm_client.generate_structured(
            prompt=prompt,
            schema=ReviewsPayload,
            cache_key=f"reviews_enrichment::{destination}::{profile_fp}",
            metadata={"provider": self.name, "destination": destination, "profile_fp": profile_fp},
            use_cache_if_available=True,
        )
        return payload.model_dump()
