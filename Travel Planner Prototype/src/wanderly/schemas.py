from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator


class SmokeProfilePillDTO(BaseModel):
    key: str
    label: str
    value: str
    icon: str = "📌"
    editable: bool = True


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
