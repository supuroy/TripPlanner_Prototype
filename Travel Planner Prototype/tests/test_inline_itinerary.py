"""Tests for inline refine itinerary validation and lazy-build gating."""

from __future__ import annotations

from src.wanderly.inline_itinerary import (
    apply_itinerary_dirty_flags,
    candidate_rebuild_destination_ids,
    candidates_for_inline_attempt,
    destination_has_complete_inline_itinerary,
    needs_trip_wide_itinerary_confirm,
    partition_inline_vs_lazy,
    resolve_itinerary_delivery,
)


def test_destination_has_complete_inline_itinerary_requires_items():
    assert not destination_has_complete_inline_itinerary({"itinerary": []})
    assert not destination_has_complete_inline_itinerary({"itinerary": [{"items": []}]})
    assert not destination_has_complete_inline_itinerary(
        {"itinerary": [{"day": 1, "items": ["Morning market walk"]}]}
    )
    assert destination_has_complete_inline_itinerary(
        {
            "itinerary": [
                {"day": 1, "items": ["Morning market walk"]},
                {"day": 2, "items": ["Beach day"]},
            ]
        }
    )
    assert destination_has_complete_inline_itinerary(
        {
            "itinerary": [{"day": 1, "items": ["Hike"]}],
            "transport_plan": {"primary_mode": "drive"},
        }
    )


def test_partition_inline_vs_lazy():
    dests = [
        {
            "id": "a",
            "itinerary": [
                {"items": ["Hike"]},
                {"items": ["Beach"]},
            ],
        },
        {"id": "b", "itinerary": []},
    ]
    applied, lazy = partition_inline_vs_lazy(dests, ["a", "b"], try_inline=True)
    assert applied == ["a"]
    assert lazy == ["b"]


def test_partition_skips_inline_when_disabled():
    dests = [
        {
            "id": "a",
            "itinerary": [{"items": ["Hike"]}, {"items": ["Beach"]}],
        }
    ]
    applied, lazy = partition_inline_vs_lazy(dests, ["a"], try_inline=False)
    assert applied == []
    assert lazy == ["a"]


def test_candidates_for_inline_attempt_single_card_only():
    orch = {
        "workspace_action": "targeted_itinerary_edit",
        "itinerary_refresh": "focus_only",
        "focus_destination_ids": ["lake-tahoe"],
        "itinerary_delivery": "inline_in_refine",
    }
    assert candidates_for_inline_attempt(
        orchestration=orch,
        valid_ids={"lake-tahoe", "japan"},
        focus_destination_id="lake-tahoe",
    ) == ["lake-tahoe"]
    assert candidates_for_inline_attempt(
        orchestration={
            "workspace_action": "refine_workspace",
            "itinerary_refresh": "all",
            "itinerary_delivery": "inline_in_refine",
        },
        valid_ids={"lake-tahoe", "japan"},
        focus_destination_id="lake-tahoe",
    ) == []
    assert candidates_for_inline_attempt(
        orchestration={**orch, "itinerary_delivery": "lazy_build"},
        valid_ids={"lake-tahoe", "japan"},
        focus_destination_id="lake-tahoe",
    ) == []
    assert candidates_for_inline_attempt(
        orchestration={**orch, "focus_destination_ids": ["lake-tahoe", "japan"]},
        valid_ids={"lake-tahoe", "japan"},
        focus_destination_id=None,
    ) == []


def test_needs_trip_wide_itinerary_confirm():
    assert needs_trip_wide_itinerary_confirm({"itinerary_refresh": "all"})
    assert needs_trip_wide_itinerary_confirm(
        {"workspace_action": "refine_workspace", "itinerary_refresh": "auto"}
    )
    assert not needs_trip_wide_itinerary_confirm(
        {
            "workspace_action": "targeted_itinerary_edit",
            "itinerary_refresh": "focus_only",
        }
    )


def test_apply_itinerary_dirty_flags():
    rows = apply_itinerary_dirty_flags(
        [{"id": "a"}, {"id": "b"}],
        inline_applied_ids={"a"},
        lazy_needed_ids={"b"},
    )
    assert rows[0]["itinerary_dirty"] is False
    assert rows[1]["itinerary_dirty"] is True


def test_targeted_edit_candidates_focus_only():
    orch = {
        "workspace_action": "targeted_itinerary_edit",
        "itinerary_refresh": "focus_only",
        "focus_destination_ids": ["costa-rica"],
        "itinerary_delivery": "inline_in_refine",
    }
    ids = candidate_rebuild_destination_ids(
        orchestration=orch,
        user_message="redo the day plan",
        prior_pills=[],
        incoming_pills=None,
        valid_ids={"costa-rica", "japan"},
        focus_destination_id="costa-rica",
    )
    assert ids == ["costa-rica"]


def test_compare_places_skips_rebuild():
    orch = {"workspace_action": "compare_places", "itinerary_refresh": "auto"}
    ids = candidate_rebuild_destination_ids(
        orchestration=orch,
        user_message="compare pinned places",
        prior_pills=[],
        incoming_pills=None,
        valid_ids={"a", "b"},
        focus_destination_id=None,
    )
    assert ids == []


def test_resolve_itinerary_delivery():
    assert resolve_itinerary_delivery({"itinerary_delivery": "lazy_build"}) == "lazy_build"
    assert resolve_itinerary_delivery(None) == "auto"


def test_trip_wide_refine_skips_lazy_when_embedded_itineraries_complete():
    """Refine can return 5-day plans with day_logistics; must not mark all cards dirty for lazy rebuild."""
    from src.wanderly.schemas import WanderlyWorkspaceDTO
    from src.wanderly.smoke_service import _apply_inline_itinerary_delivery

    day = {
        "day": 1,
        "items": ["Hike to camp"],
        "day_logistics": {"lodging": {"destination_search_label": "Camp"}},
    }
    dto = WanderlyWorkspaceDTO(
        assistant_markdown="ok",
        profile_pills=[],
        destinations=[
            {"id": "heart-lake-trail", "name": "Heart Lake", "itinerary": [day] * 5},
            {"id": "bechler-river-trail", "name": "Bechler", "itinerary": [day] * 5},
        ],
    )
    orch = {
        "workspace_action": "refine_workspace",
        "itinerary_refresh": "all",
        "itinerary_delivery": "lazy_build",
    }
    out, applied, lazy = _apply_inline_itinerary_delivery(
        dto,
        user_message="redo all itineraries in the trip",
        prior_pills=[],
        orchestration=orch,
        focus_destination_id=None,
    )
    assert set(applied) == {"heart-lake-trail", "bechler-river-trail"}
    assert lazy == []
    rows = {str(d["id"]): d for d in out.destinations if isinstance(d, dict)}
    assert rows["heart-lake-trail"]["itinerary_dirty"] is False
    assert rows["bechler-river-trail"]["itinerary_dirty"] is False
