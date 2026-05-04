from __future__ import annotations

from uuid import uuid4
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class RecommendationItem(BaseModel):
    title: str
    detail: str


class StageOutputBase(BaseModel):
    stage: str
    inputs_considered: List[str]
    assumptions: List[str]
    missing_info: List[str]
    recommendations: List[RecommendationItem]
    reasoning_summary: str


class TravelerCriteria(BaseModel):
    travel_dates: Optional[str] = None
    travelers: Optional[str] = None
    destination_preferences: Optional[List[str]] = None
    activities: Optional[List[str]] = None
    budget_max_usd: Optional[float] = None
    travel_time_tolerance: Optional[str] = None
    climate_preference: Optional[str] = None


class UserSpecificItem(BaseModel):
    key: str
    value: str
    confidence: float = Field(ge=0.0, le=1.0)
    source_text: Optional[str] = None


class TravelerProfileHint(BaseModel):
    field: str
    value: str
    confidence: float = Field(ge=0.0, le=1.0)
    source_text: Optional[str] = None


class CriteriaStageInput(BaseModel):
    user_message: str
    prior_context: Dict[str, Any] = Field(default_factory=dict)


class CriteriaStageOutput(StageOutputBase):
    extracted_criteria: TravelerCriteria
    user_specifics: List[UserSpecificItem] = Field(default_factory=list)
    traveler_profile_hints: List[TravelerProfileHint] = Field(default_factory=list)
    extracted_signals: Dict[str, Any] = Field(default_factory=dict)
    constraints: List[str] = Field(default_factory=list)
    follow_up_questions: List[str]


class InspirationStageInput(BaseModel):
    criteria: TravelerCriteria
    user_specifics: List[UserSpecificItem] = Field(default_factory=list)
    extracted_signals: Dict[str, Any] = Field(default_factory=dict)
    constraints: List[str] = Field(default_factory=list)


class DestinationOption(BaseModel):
    destination: str
    why_fit: str
    pros: List[str]
    cons: List[str]
    estimated_total_cost_usd: Optional[float] = None
    estimated_travel_time_hours: Optional[float] = None


class InspirationStageOutput(StageOutputBase):
    top_destinations: List[DestinationOption]
    best_recommendation: str


class ItineraryStageInput(BaseModel):
    criteria: TravelerCriteria
    chosen_destination: Optional[str] = None


class BookingStageInput(BaseModel):
    criteria: TravelerCriteria
    chosen_destination: Optional[str] = None
    proposed_dates: Optional[str] = None


class ActivitiesStageInput(BaseModel):
    criteria: TravelerCriteria
    chosen_destination: Optional[str] = None


class PlaceholderStageOutput(StageOutputBase):
    details: Dict[str, Any] = Field(default_factory=dict)


class StageRecord(BaseModel):
    stage_name: str
    output: Dict[str, Any]


class LLMActivityEntry(BaseModel):
    timestamp: str
    kind: str
    message: str


class ActionHistoryEntry(BaseModel):
    timestamp: str
    action_name: str
    source: str
    args: Dict[str, Any] = Field(default_factory=dict)
    status: str = "completed"
    note: Optional[str] = None


class AdvisorActionProposal(BaseModel):
    action: str
    args: Dict[str, Any] = Field(default_factory=dict)
    requires_approval: bool = True
    why: str = ""


class AdvisorSuggestion(BaseModel):
    assistant_message: str = ""
    primary_recommendation: AdvisorActionProposal = Field(
        default_factory=lambda: AdvisorActionProposal(action="none", args={}, requires_approval=False, why="")
    )
    alternatives: List[AdvisorActionProposal] = Field(default_factory=list)
    confidence: float = 0.0
    generated_at: Optional[str] = None
    model: Optional[str] = None
    error: Optional[str] = None


class AdvisorChatEntry(BaseModel):
    timestamp: str
    role: str
    message: str


class TravelerProfile(BaseModel):
    traveler_summary: Optional[str] = None
    group_size: int = 1
    group_type: str = "solo"
    trip_type: str = "leisure"
    traveler_origin: Optional[str] = None
    budget_max_usd: Optional[float] = None
    destination_preferences: List[str] = Field(default_factory=list)
    preferred_climate: Optional[str] = None
    preferred_activities: List[str] = Field(default_factory=list)
    pace_preference: str = "balanced"
    accommodation_style: str = "mid-range"
    travel_time_tolerance: str = "medium-haul acceptable"
    dietary_or_accessibility_notes: List[str] = Field(default_factory=list)
    assumptions_used: List[str] = Field(default_factory=list)
    editable_notes: Optional[str] = None


class TravelerProfileEntry(BaseModel):
    profile_id: str = Field(default_factory=lambda: str(uuid4()))
    profile_name: str = "Default"
    is_default: bool = False
    profile: TravelerProfile = Field(default_factory=TravelerProfile)


class ItineraryFitAssessment(BaseModel):
    option_id: str
    name: str
    destination: Optional[str] = None
    fit_status: str = "fit"
    issues: List[str] = Field(default_factory=list)
    suggested_action: Optional[str] = None


class DestinationEnrichment(BaseModel):
    status: str = "not_started"
    weather_summary: Optional[str] = None
    reviews_summary: Optional[str] = None
    top_things_to_do: List[str] = Field(default_factory=list)
    weather_details: Dict[str, Any] = Field(default_factory=dict)
    reviews_details: Dict[str, Any] = Field(default_factory=dict)
    activities_details: Dict[str, Any] = Field(default_factory=dict)
    sources: Dict[str, List[str]] = Field(default_factory=dict)
    error: Optional[str] = None


class OptionComparisonItem(BaseModel):
    option_id: str
    name: str
    destination: Optional[str] = None
    pros: List[str] = Field(default_factory=list)
    cons: List[str] = Field(default_factory=list)
    fit_note: str


class OptionComparisonGuidance(BaseModel):
    status: str = "not_started"
    summary: Optional[str] = None
    recommendation: Optional[str] = None
    option_notes: List[OptionComparisonItem] = Field(default_factory=list)
    updated_at: Optional[str] = None
    model: Optional[str] = None
    error: Optional[str] = None


class OptionComparisonLLMOutput(BaseModel):
    summary: str
    recommendation: str
    option_notes: List[OptionComparisonItem] = Field(default_factory=list)


class ItineraryDayPlan(BaseModel):
    day_number: int
    city_to_stay: str
    activity: str
    intercity_travel_time: Optional[str] = None


class ItineraryBuildOutput(BaseModel):
    human_readable_summary: str
    travel_in: str
    is_multi_city: bool
    daily_plan: List[ItineraryDayPlan] = Field(default_factory=list)
    travel_out: str


class ItineraryOption(BaseModel):
    option_id: str = Field(default_factory=lambda: str(uuid4()))
    name: str = "Option"
    status: str = "draft"
    source: str = "manual"
    criteria_snapshot: Optional[TravelerCriteria] = None
    traveler_snapshot: Optional[TravelerProfile] = None
    chosen_destination: Optional[str] = None
    estimated_trip_cost_usd: Optional[float] = None
    estimated_travel_time_hours: Optional[float] = None
    recommended_length_days: Optional[int] = None
    trip_highlight: Optional[str] = None
    travel_dates: Optional[str] = None
    stopovers: List[str] = Field(default_factory=list)
    flights: List[Dict[str, Any]] = Field(default_factory=list)
    hotels: List[Dict[str, Any]] = Field(default_factory=list)
    activities: List[Dict[str, Any]] = Field(default_factory=list)
    destination_enrichment: DestinationEnrichment = Field(default_factory=DestinationEnrichment)
    itinerary_plan_human: Optional[str] = None
    itinerary_plan_json: Dict[str, Any] = Field(default_factory=dict)
    stages_completed: List[str] = Field(default_factory=list)
    destination_candidates: List[str] = Field(default_factory=list)
    itinerary_data: Dict[str, Any] = Field(default_factory=dict)
    comparison_notes: List[str] = Field(default_factory=list)


class SessionState(BaseModel):
    session_id: str
    raw_user_messages: List[str] = Field(default_factory=list)
    stage_history: List[StageRecord] = Field(default_factory=list)
    llm_activity: List[LLMActivityEntry] = Field(default_factory=list)
    action_history: List[ActionHistoryEntry] = Field(default_factory=list)
    advisor_chat: List[AdvisorChatEntry] = Field(default_factory=list)
    advisor_suggestion: AdvisorSuggestion = Field(default_factory=AdvisorSuggestion)
    traveler_profile: TravelerProfile = Field(default_factory=TravelerProfile)
    traveler_profiles: List[TravelerProfileEntry] = Field(default_factory=list)
    active_traveler_profile_id: Optional[str] = None
    itinerary_options: List[ItineraryOption] = Field(default_factory=list)
    active_itinerary_id: Optional[str] = None
    comparison_guidance: OptionComparisonGuidance = Field(default_factory=OptionComparisonGuidance)
    itinerary_fit_report: List[ItineraryFitAssessment] = Field(default_factory=list)
