from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from src.schemas import (
    ActionHistoryEntry,
    AdvisorChatEntry,
    AdvisorSuggestion,
    ItineraryFitAssessment,
    ItineraryOption,
    SessionState,
    StageRecord,
    TravelerProfile,
    TravelerProfileEntry,
)
from src.schemas import LLMActivityEntry, OptionComparisonGuidance


class SessionManager:
    def __init__(
        self,
        session_id: str | None = None,
        traveler_memory_path: str = ".memory/traveler_profile.json",
    ) -> None:
        self.state = SessionState(session_id=session_id or str(uuid4()))
        self.traveler_memory_path = Path(traveler_memory_path)
        self._load_traveler_profile_memory()
        self._ensure_profile_bootstrap()

    def add_user_message(self, message: str) -> None:
        self.state.raw_user_messages.append(message)

    def add_stage_output(self, stage_name: str, output: dict) -> None:
        self.state.stage_history.append(StageRecord(stage_name=stage_name, output=output))

    def add_activity_log(self, kind: str, message: str) -> None:
        entry = LLMActivityEntry(
            timestamp=datetime.now(timezone.utc).isoformat(),
            kind=kind,
            message=message,
        )
        self.state.llm_activity.append(entry)
        if len(self.state.llm_activity) > 400:
            self.state.llm_activity = self.state.llm_activity[-400:]

    def add_action_history(
        self,
        action_name: str,
        source: str,
        args: Optional[Dict[str, Any]] = None,
        status: str = "completed",
        note: Optional[str] = None,
    ) -> None:
        self.state.action_history.append(
            ActionHistoryEntry(
                timestamp=datetime.now(timezone.utc).isoformat(),
                action_name=action_name,
                source=source,
                args=args or {},
                status=status,
                note=note,
            )
        )
        if len(self.state.action_history) > 200:
            self.state.action_history = self.state.action_history[-200:]

    def add_advisor_chat(self, role: str, message: str) -> None:
        self.state.advisor_chat.append(
            AdvisorChatEntry(
                timestamp=datetime.now(timezone.utc).isoformat(),
                role=role,
                message=message,
            )
        )
        if len(self.state.advisor_chat) > 120:
            self.state.advisor_chat = self.state.advisor_chat[-120:]

    def set_advisor_suggestion(self, payload: Dict[str, Any]) -> AdvisorSuggestion:
        merged = self.state.advisor_suggestion.model_dump()
        merged.update(payload)
        self.state.advisor_suggestion = AdvisorSuggestion.model_validate(merged)
        return self.state.advisor_suggestion

    def get_advisor_suggestion(self) -> AdvisorSuggestion:
        return self.state.advisor_suggestion

    def get_latest_stage_output(self, stage_name: str) -> dict | None:
        for record in reversed(self.state.stage_history):
            if record.stage_name == stage_name:
                return record.output
        return None

    def get_traveler_profile(self) -> TravelerProfile:
        active = self._get_active_profile_entry()
        self.state.traveler_profile = active.profile
        return active.profile

    def list_traveler_profiles(self) -> List[Dict[str, Any]]:
        return [
            {
                "profile_id": p.profile_id,
                "profile_name": p.profile_name,
                "is_default": p.is_default,
            }
            for p in self.state.traveler_profiles
        ]

    def create_traveler_profile(self, profile_name: str) -> TravelerProfileEntry:
        name = profile_name.strip() or "New Profile"
        entry = TravelerProfileEntry(profile_name=name, profile=TravelerProfile())
        self.state.traveler_profiles.append(entry)
        self.state.active_traveler_profile_id = entry.profile_id
        self.state.traveler_profile = entry.profile
        self._save_traveler_profile_memory()
        return entry

    def select_traveler_profile(self, profile_id: str) -> TravelerProfileEntry:
        for entry in self.state.traveler_profiles:
            if entry.profile_id == profile_id:
                self.state.active_traveler_profile_id = entry.profile_id
                self.state.traveler_profile = entry.profile
                self._save_traveler_profile_memory()
                return entry
        raise ValueError(f"Unknown traveler profile id: {profile_id}")

    def get_comparison_guidance(self) -> OptionComparisonGuidance:
        return self.state.comparison_guidance

    def set_comparison_guidance(self, patch: Dict[str, Any]) -> OptionComparisonGuidance:
        merged = self.state.comparison_guidance.model_dump()
        merged.update(patch)
        self.state.comparison_guidance = OptionComparisonGuidance.model_validate(merged)
        return self.state.comparison_guidance

    def upsert_traveler_profile(self, profile_patch: Dict[str, Any]) -> TravelerProfile:
        active = self._get_active_profile_entry()
        merged = active.profile.model_dump()
        merged.update(profile_patch)
        updated_profile = TravelerProfile.model_validate(merged)
        active.profile = updated_profile
        self.state.traveler_profile = updated_profile
        self._save_traveler_profile_memory()
        return updated_profile

    def hydrate_traveler_profile_from_criteria(self, criteria_output: Dict[str, Any]) -> TravelerProfile:
        current_profile = self.get_traveler_profile()
        criteria = criteria_output.get("extracted_criteria", {})
        user_specifics = criteria_output.get("user_specifics", [])
        profile_hints = criteria_output.get("traveler_profile_hints", [])
        constraints = criteria_output.get("constraints", [])
        hint_map = self._build_profile_hint_map(profile_hints)

        patch: Dict[str, Any] = {}
        assumptions: List[str] = []

        # Reset trip-scoped fields per new planning prompt to avoid stale carryover.
        patch["budget_max_usd"] = None
        patch["preferred_climate"] = None
        patch["destination_preferences"] = []
        patch["preferred_activities"] = []
        patch["travel_time_tolerance"] = "unspecified"
        patch["editable_notes"] = None

        budget = criteria.get("budget_max_usd")
        if budget is not None:
            patch["budget_max_usd"] = budget

        destination_prefs = criteria.get("destination_preferences")
        if destination_prefs:
            patch["destination_preferences"] = destination_prefs

        climate = criteria.get("climate_preference")
        if climate:
            patch["preferred_climate"] = climate
        if hint_map.get("preferred_climate"):
            patch["preferred_climate"] = hint_map["preferred_climate"]["value"]

        activities = criteria.get("activities")
        if activities:
            patch["preferred_activities"] = activities
        elif not current_profile.preferred_activities:
            patch["preferred_activities"] = ["mix of relaxation and local highlights"]
            assumptions.append("Preferred activities defaulted to balanced sightseeing + relaxation.")

        travel_time_tol = criteria.get("travel_time_tolerance")
        if travel_time_tol:
            patch["travel_time_tolerance"] = travel_time_tol
        if hint_map.get("travel_time_tolerance"):
            patch["travel_time_tolerance"] = hint_map["travel_time_tolerance"]["value"]

        travelers = criteria.get("travelers")
        if travelers:
            patch["traveler_summary"] = travelers
            if hint_map.get("group_size"):
                parsed_hint_size = self._to_int(hint_map["group_size"]["value"])
                if parsed_hint_size is not None:
                    patch["group_size"] = parsed_hint_size
            else:
                parsed_group_size = self._infer_group_size(travelers)
                if parsed_group_size is not None:
                    patch["group_size"] = parsed_group_size

            if hint_map.get("group_type"):
                patch["group_type"] = hint_map["group_type"]["value"]
            else:
                parsed_group_type = self._infer_group_type(travelers)
                if parsed_group_type is not None:
                    patch["group_type"] = parsed_group_type
        elif not current_profile.traveler_summary:
            patch["traveler_summary"] = "One adult traveler (provisional)."
            assumptions.append("Group summary defaulted to solo adult until user edits.")
            patch["group_size"] = 1
            patch["group_type"] = "solo"

        if not current_profile.group_type:
            patch["group_type"] = "solo"

        if criteria.get("travel_dates"):
            current_note = current_profile.editable_notes or ""
            if not current_note or current_note.lower().startswith("likely travel window:"):
                patch["editable_notes"] = f"Likely travel window: {criteria['travel_dates']}."

        for item in user_specifics:
            key = (item.get("key") or "").lower()
            val = item.get("value")
            if not val:
                continue
            if "origin" in key or "departure" in key:
                patch["traveler_origin"] = val
            if "pace" in key and not patch.get("pace_preference"):
                patch["pace_preference"] = val
            if any(token in key for token in ["destination", "region", "country", "location"]):
                existing = list(patch.get("destination_preferences") or [])
                existing.append(str(val))
                patch["destination_preferences"] = sorted(set(existing))
            if "activity" in key:
                existing_acts = list(patch.get("preferred_activities") or [])
                existing_acts.append(str(val))
                patch["preferred_activities"] = sorted(set(existing_acts))
            if "travel time" in key or "flight" in key:
                patch["travel_time_tolerance"] = str(val)

        if hint_map.get("traveler_origin"):
            patch["traveler_origin"] = hint_map["traveler_origin"]["value"]
        if hint_map.get("pace_preference"):
            patch["pace_preference"] = hint_map["pace_preference"]["value"]
        if hint_map.get("trip_type"):
            patch["trip_type"] = hint_map["trip_type"]["value"]
        if hint_map.get("accommodation_style"):
            patch["accommodation_style"] = hint_map["accommodation_style"]["value"]
        else:
            inferred_accommodation = self._infer_accommodation_style(
                criteria_output=criteria_output,
                fallback=current_profile.accommodation_style,
            )
            if inferred_accommodation:
                patch["accommodation_style"] = inferred_accommodation

        if not patch.get("trip_type"):
            inferred_trip_type = self._infer_trip_type(criteria_output=criteria_output, fallback=current_profile.trip_type)
            if inferred_trip_type:
                patch["trip_type"] = inferred_trip_type

        if constraints:
            notes = list(current_profile.dietary_or_accessibility_notes)
            for c in constraints:
                if "access" in c.lower() or "mobility" in c.lower() or "diet" in c.lower():
                    notes.append(c)
            if notes:
                patch["dietary_or_accessibility_notes"] = sorted(set(notes))

        patch["assumptions_used"] = sorted(set(assumptions))
        return self.upsert_traveler_profile(patch)

    def create_itinerary_option(
        self,
        name: str,
        criteria_snapshot: Optional[Dict[str, Any]] = None,
        destination_candidates: Optional[list[str]] = None,
    ) -> ItineraryOption:
        option = ItineraryOption(
            name=name,
            source="manual",
            status="draft",
            criteria_snapshot=criteria_snapshot,
            traveler_snapshot=self.get_traveler_profile(),
            destination_candidates=destination_candidates or [],
        )
        self.state.itinerary_options.append(option)
        self.state.active_itinerary_id = option.option_id
        return option

    def get_active_itinerary(self) -> Optional[ItineraryOption]:
        active_id = self.state.active_itinerary_id
        if not active_id:
            return None
        for option in self.state.itinerary_options:
            if option.option_id == active_id:
                return option
        return None

    def select_itinerary_option(self, option_id: str) -> Optional[ItineraryOption]:
        for option in self.state.itinerary_options:
            if option.option_id == option_id:
                self.state.active_itinerary_id = option_id
                return option
        return None

    def update_itinerary_option(self, option_id: str, patch: Dict[str, Any]) -> Optional[ItineraryOption]:
        for idx, option in enumerate(self.state.itinerary_options):
            if option.option_id == option_id:
                merged = option.model_dump()
                merged.update(patch)
                updated = ItineraryOption.model_validate(merged)
                self.state.itinerary_options[idx] = updated
                return updated
        return None

    def delete_itinerary_option(self, option_id: str) -> bool:
        before = len(self.state.itinerary_options)
        self.state.itinerary_options = [o for o in self.state.itinerary_options if o.option_id != option_id]
        removed = len(self.state.itinerary_options) < before
        if removed and self.state.active_itinerary_id == option_id:
            self.state.active_itinerary_id = (
                self.state.itinerary_options[0].option_id if self.state.itinerary_options else None
            )
        return removed

    def replace_generated_destination_options(
        self,
        criteria_snapshot: Optional[Dict[str, Any]],
        destinations: List[Dict[str, Any]],
    ) -> List[ItineraryOption]:
        self.state.itinerary_options = [
            o for o in self.state.itinerary_options if o.source != "generated"
        ]

        created: List[ItineraryOption] = []
        for idx, item in enumerate(destinations, start=1):
            destination = item.get("destination")
            if not destination:
                continue
            option = ItineraryOption(
                name=f"Option {idx}: {destination}",
                source="generated",
                status="in_progress",
                criteria_snapshot=criteria_snapshot,
                traveler_snapshot=self.get_traveler_profile(),
                chosen_destination=destination,
                estimated_trip_cost_usd=item.get("estimated_total_cost_usd"),
                estimated_travel_time_hours=item.get("estimated_travel_time_hours"),
                recommended_length_days=self._recommend_length_days(criteria_snapshot),
                trip_highlight=item.get("why_fit"),
                destination_candidates=[destination],
                itinerary_data={
                    "pros": item.get("pros", []),
                    "cons": item.get("cons", []),
                },
                stages_completed=["criteria_gathering", "inspiration"],
            )
            created.append(option)

        self.state.itinerary_options.extend(created)
        if created:
            self.state.active_itinerary_id = created[0].option_id
        return created

    def append_generated_destination_options(
        self,
        criteria_snapshot: Optional[Dict[str, Any]],
        destinations: List[Dict[str, Any]],
    ) -> List[ItineraryOption]:
        existing_destinations = {
            (o.chosen_destination or "").strip().lower()
            for o in self.state.itinerary_options
            if o.chosen_destination
        }
        generated_count = sum(1 for o in self.state.itinerary_options if o.source == "generated")
        created: List[ItineraryOption] = []
        next_idx = generated_count + 1
        for item in destinations:
            destination = (item.get("destination") or "").strip()
            if not destination:
                continue
            if destination.lower() in existing_destinations:
                continue
            option = ItineraryOption(
                name=f"Option {next_idx}: {destination}",
                source="generated",
                status="in_progress",
                criteria_snapshot=criteria_snapshot,
                traveler_snapshot=self.get_traveler_profile(),
                chosen_destination=destination,
                estimated_trip_cost_usd=item.get("estimated_total_cost_usd"),
                estimated_travel_time_hours=item.get("estimated_travel_time_hours"),
                recommended_length_days=self._recommend_length_days(criteria_snapshot),
                trip_highlight=item.get("why_fit"),
                destination_candidates=[destination],
                itinerary_data={
                    "pros": item.get("pros", []),
                    "cons": item.get("cons", []),
                },
                stages_completed=["criteria_gathering", "inspiration"],
            )
            created.append(option)
            existing_destinations.add(destination.lower())
            next_idx += 1

        self.state.itinerary_options.extend(created)
        if created:
            self.state.active_itinerary_id = created[0].option_id
        return created

    def shortlist_itinerary_options(self, option_ids: List[str]) -> List[ItineraryOption]:
        shortlisted: List[ItineraryOption] = []
        target_ids = set(option_ids)
        for idx, option in enumerate(self.state.itinerary_options):
            if option.option_id in target_ids:
                updated = option.model_copy(update={"status": "selected_for_next"})
                self.state.itinerary_options[idx] = updated
                shortlisted.append(updated)
            elif option.status == "selected_for_next":
                fallback_status = "in_progress" if option.source == "generated" else "draft"
                updated = option.model_copy(update={"status": fallback_status})
                self.state.itinerary_options[idx] = updated
        if shortlisted:
            self.state.active_itinerary_id = shortlisted[0].option_id
        return shortlisted

    def set_itinerary_fit_report(self, items: List[Dict[str, Any]]) -> List[ItineraryFitAssessment]:
        self.state.itinerary_fit_report = [ItineraryFitAssessment.model_validate(x) for x in items]
        return self.state.itinerary_fit_report

    @staticmethod
    def _recommend_length_days(criteria_snapshot: Optional[Dict[str, Any]]) -> int:
        if not criteria_snapshot:
            return 6
        dates = (criteria_snapshot.get("travel_dates") or "").lower()
        if "week" in dates:
            return 7
        if "weekend" in dates:
            return 3
        if "month" in dates:
            return 8
        return 6

    def _load_traveler_profile_memory(self) -> None:
        if not self.traveler_memory_path.exists():
            return
        raw = self.traveler_memory_path.read_text(encoding="utf-8").strip()
        if not raw:
            return
        payload = json.loads(raw)
        if isinstance(payload, dict) and isinstance(payload.get("profiles"), list):
            profiles: List[TravelerProfileEntry] = []
            for item in payload.get("profiles", []):
                try:
                    profiles.append(TravelerProfileEntry.model_validate(item))
                except Exception:
                    continue
            self.state.traveler_profiles = profiles
            self.state.active_traveler_profile_id = payload.get("active_profile_id")
            if profiles:
                active = self._get_active_profile_entry()
                self.state.traveler_profile = active.profile
            return
        # Backward compatibility: previously this file held a single TravelerProfile object.
        self.state.traveler_profile = TravelerProfile.model_validate(payload)

    def _save_traveler_profile_memory(self) -> None:
        self.traveler_memory_path.parent.mkdir(parents=True, exist_ok=True)
        active = self._get_active_profile_entry()
        self.state.traveler_profile = active.profile
        self.traveler_memory_path.write_text(
            json.dumps(
                {
                    "version": 2,
                    "active_profile_id": self.state.active_traveler_profile_id,
                    "profiles": [p.model_dump() for p in self.state.traveler_profiles],
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    def _ensure_profile_bootstrap(self) -> None:
        if not self.state.traveler_profiles:
            entry = TravelerProfileEntry(
                profile_name="Default",
                is_default=True,
                profile=self.state.traveler_profile,
            )
            self.state.traveler_profiles = [entry]
            self.state.active_traveler_profile_id = entry.profile_id
        active = self._get_active_profile_entry()
        self.state.traveler_profile = active.profile
        self._save_traveler_profile_memory()

    def _get_active_profile_entry(self) -> TravelerProfileEntry:
        if not self.state.traveler_profiles:
            entry = TravelerProfileEntry(
                profile_name="Default",
                is_default=True,
                profile=self.state.traveler_profile,
            )
            self.state.traveler_profiles = [entry]
            self.state.active_traveler_profile_id = entry.profile_id
            return entry

        active_id = self.state.active_traveler_profile_id
        if active_id:
            for entry in self.state.traveler_profiles:
                if entry.profile_id == active_id:
                    return entry

        # Fallback to default/first profile when active id is missing/stale.
        for entry in self.state.traveler_profiles:
            if entry.is_default:
                self.state.active_traveler_profile_id = entry.profile_id
                return entry
        self.state.active_traveler_profile_id = self.state.traveler_profiles[0].profile_id
        return self.state.traveler_profiles[0]

    @staticmethod
    def _infer_group_size(travelers: str) -> Optional[int]:
        text = travelers.lower()
        human_counts = [
            int(match[0])
            for match in re.findall(r"\b(\d+)\s*(adult|adults|child|children|kid|kids)\b", text)
        ]
        if human_counts:
            return sum(human_counts)
        numbers = [int(n) for n in re.findall(r"\b(\d+)\b", text)]
        if numbers:
            return sum(numbers)
        if "wife" in text or "husband" in text or "partner" in text:
            return 2
        if "family" in text:
            return 3
        if "solo" in text or "just me" in text:
            return 1
        return None

    @staticmethod
    def _infer_group_type(travelers: str) -> Optional[str]:
        text = travelers.lower()
        if "wife" in text or "husband" in text or "partner" in text or "couple" in text:
            return "couple"
        if "family" in text:
            return "family"
        if "friends" in text:
            return "friends"
        if "solo" in text or "just me" in text:
            return "solo"
        return None

    @staticmethod
    def _infer_accommodation_style(criteria_output: Dict[str, Any], fallback: str) -> Optional[str]:
        criteria = criteria_output.get("extracted_criteria", {}) or {}
        user_specifics = criteria_output.get("user_specifics", []) or []
        constraints = criteria_output.get("constraints", []) or []
        combined_chunks = []
        combined_chunks.extend(constraints)
        for item in user_specifics:
            if isinstance(item, dict):
                combined_chunks.append(str(item.get("key") or ""))
                combined_chunks.append(str(item.get("value") or ""))
        combined_chunks.append(str(criteria_output.get("reasoning_summary") or ""))
        combined_chunks.append(str(criteria.get("travelers") or ""))
        combined = " ".join(combined_chunks).lower()

        if any(token in combined for token in ["luxury", "high-end", "five-star", "5-star"]):
            return "luxury"
        if any(token in combined for token in ["budget", "cheap", "low-cost"]):
            return "budget"
        if any(token in combined for token in ["boutique", "upscale", "premium"]):
            return "upscale"
        return fallback

    @staticmethod
    def _infer_trip_type(criteria_output: Dict[str, Any], fallback: str) -> Optional[str]:
        criteria = criteria_output.get("extracted_criteria", {}) or {}
        user_specifics = criteria_output.get("user_specifics", []) or []
        constraints = criteria_output.get("constraints", []) or []
        chunks = [str(criteria_output.get("reasoning_summary") or ""), str(criteria.get("travelers") or "")]
        chunks.extend([str(x) for x in constraints])
        for item in user_specifics:
            if isinstance(item, dict):
                chunks.append(str(item.get("key") or ""))
                chunks.append(str(item.get("value") or ""))
        text = " ".join(chunks).lower()
        if any(t in text for t in ["business", "work trip", "conference", "client meeting"]):
            return "business"
        if any(t in text for t in ["vacation", "holiday", "leisure", "relaxing", "adventure"]):
            return "leisure"
        if any(t in text for t in ["bleisure", "mix of work", "mixed"]):
            return "mixed"
        return fallback

    @staticmethod
    def _build_profile_hint_map(hints: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        best: Dict[str, Dict[str, Any]] = {}
        for hint in hints:
            if not isinstance(hint, dict):
                continue
            field = str(hint.get("field") or "").strip()
            value = hint.get("value")
            confidence = hint.get("confidence")
            if not field or value is None:
                continue
            try:
                conf = float(confidence)
            except (TypeError, ValueError):
                continue
            if conf < 0.6:
                continue
            existing = best.get(field)
            if existing is None or conf > float(existing["confidence"]):
                best[field] = {"value": str(value), "confidence": conf}
        return best

    @staticmethod
    def _to_int(value: str) -> Optional[int]:
        try:
            return int(value.strip())
        except (TypeError, ValueError, AttributeError):
            return None
