"""Duration pill mapping: profile_patch stores some UI options under Trip length in editable_notes."""

from src.wanderly.mapper import map_workspace_to_wanderly


def _minimal_state(tp: dict) -> dict:
    return {"traveler_profile": tp, "itinerary_options": []}


def test_duration_pill_prefers_last_trip_length_in_notes() -> None:
    w = map_workspace_to_wanderly(
        _minimal_state(
            {
                "group_size": 2,
                "group_type": "couple",
                "traveler_summary": "Mid-March",
                "traveler_origin": "Seattle (SEA)",
                "destination_preferences": ["beach"],
                "budget_max_usd": 8000,
                "pace_preference": "2+ weeks",
                "editable_notes": "Trip length: 10-14 days",
            },
        ),
    )
    pills = w["profile_pills"]
    dur = next(p for p in pills if p["key"] == "duration")
    assert dur["value"] == "10-14 days"


def test_duration_pill_falls_back_to_pace_preference() -> None:
    w = map_workspace_to_wanderly(
        _minimal_state(
            {
                "group_size": 2,
                "group_type": "couple",
                "traveler_summary": "Mid-March",
                "traveler_origin": "Seattle (SEA)",
                "destination_preferences": ["beach"],
                "budget_max_usd": 8000,
                "pace_preference": "7 days",
                "editable_notes": "",
            },
        ),
    )
    pills = w["profile_pills"]
    dur = next(p for p in pills if p["key"] == "duration")
    assert dur["value"] == "7 days"
