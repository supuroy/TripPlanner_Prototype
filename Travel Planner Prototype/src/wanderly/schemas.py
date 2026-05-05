from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator


class SmokeProfilePillDTO(BaseModel):
    key: str
    label: str
    value: str
    icon: str = "📌"
    editable: bool = True


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
