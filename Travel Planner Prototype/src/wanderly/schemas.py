from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator


class SmokeProfilePillDTO(BaseModel):
    key: str
    label: str
    value: str
    icon: str = "📌"
    editable: bool = True


def sanitize_traveler_place_pill_value(key: str, value: str) -> str:
    """
    Strip LLM prose tails from location-like pills (used verbatim in Expedia ``from:`` / typeahead).

    Models sometimes emit ``Seattle-based`` or ``… based``; those yield broken flight URLs.
    """
    k = (key or "").strip().lower()
    if k != "from":
        return value
    v = " ".join(str(value or "").split())
    if not v:
        return v
    v = re.sub(r"(?i)-based(?=,|\s|$)", "", v)
    v = re.sub(r"(?i)\s+based(?=,|\s|$)", "", v)
    v = re.sub(r"(?i)-centric(?=,|\s|$)", "", v)
    return " ".join(v.split()).strip(" ,-|")


def coerce_profile_pill(p: dict[str, Any]) -> SmokeProfilePillDTO | None:
    """
    Validate a raw pill dict.

    The ``tags`` pill is not stored on the traveler profile (advisor nuance goes to ``advisor_note`` / UI insights).
    """
    try:
        dto = SmokeProfilePillDTO.model_validate(p)
    except ValidationError:
        return None
    if str(dto.key).strip() == "tags":
        return None
    cleaned = sanitize_traveler_place_pill_value(str(dto.key), str(dto.value))
    if cleaned != str(dto.value):
        dto = dto.model_copy(update={"value": cleaned})
    return dto


class SmokeDestDTO(BaseModel):
    id: str
    name: str
    country: str = ""
    hook: str = ""
    score: int = 85

    @field_validator("id", mode="before")
    @classmethod
    def _id_to_str(cls, v: object) -> str:
        return str(v).strip() if v is not None else ""


class WanderlyWorkspaceDTO(BaseModel):
    """Stable JSON shape consumed by the Wanderly React adapter."""

    assistant_markdown: str = ""
    profile_pills: list[SmokeProfilePillDTO] = Field(default_factory=list)
    destinations: list[dict[str, Any]] = Field(default_factory=list)
    agent_status_hint: str | None = None
    enrichment_pending: bool = False
    weather_by_destination_id: dict[str, str] = Field(default_factory=dict)
    commerce_disclaimer: str = (
        "Prices are illustrative placeholders — not live inventory or availability."
    )
    advisor_suggestion: dict[str, Any] | None = None
    breadcrumb: list[dict[str, Any]] = Field(default_factory=list)
