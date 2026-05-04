from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
import time
from typing import Any, Dict, List, Optional
from urllib import request
from urllib.error import URLError

from src.advisor import LocalOrchestrationAdvisor
from src.schemas import CriteriaStageInput, InspirationStageInput, ItineraryBuildOutput
from src.session_manager import SessionManager
from src.stages import ActivitiesStage, BookingStage, CriteriaStage, InspirationStage, ItineraryStage
from src.tools import ActivitiesProvider, LocalComparatorProvider, ReviewsProvider, WeatherProvider


class TravelPlannerOrchestrator:
    PLANNING_STAGES = [
        "criteria_gathering",
        "inspiration",
        "itinerary_formation",
        "booking_planning",
        "activities_planning",
    ]

    def __init__(
        self,
        session: SessionManager,
        criteria_stage: CriteriaStage,
        inspiration_stage: InspirationStage,
        itinerary_stage: ItineraryStage,
        booking_stage: BookingStage,
        activities_stage: ActivitiesStage,
        weather_provider: WeatherProvider,
        reviews_provider: ReviewsProvider,
        activities_provider: ActivitiesProvider,
        comparator_provider: LocalComparatorProvider | None = None,
        use_local_comparator: bool = False,
    ) -> None:
        self.session = session
        self.weather_provider = weather_provider
        self.reviews_provider = reviews_provider
        self.activities_provider = activities_provider
        self.comparator_provider = comparator_provider
        self.use_local_comparator = use_local_comparator and comparator_provider is not None
        # Keep enrichment fan-out conservative to reduce quota bursts on free tier.
        self._executor = ThreadPoolExecutor(max_workers=2)
        self._comparison_job_inflight = False
        self._comparison_job_dirty = False
        self._advisor_job_inflight = False
        self._advisor_job_dirty = False
        self._advisor_pending_message = "System event: Startup"
        self._advisor_pending_use_local = False
        self._shutting_down = False
        self.enable_local_slm_probe = os.getenv("ENABLE_LOCAL_SLM_PROBE", "false").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        self.advisor_use_local_on_auto = os.getenv("ADVISOR_USE_LOCAL_ON_AUTO", "false").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        self.local_slm_model = os.getenv("LOCAL_SLM_MODEL", "llama3.2:1b")
        self.local_slm_api_url = os.getenv("LOCAL_SLM_API_URL", "http://127.0.0.1:11434/api/generate")
        self.local_slm_timeout_seconds = int(os.getenv("LOCAL_SLM_TIMEOUT_SECONDS", "35"))
        self.advisor = LocalOrchestrationAdvisor(
            model_name=self.local_slm_model,
            api_url=self.local_slm_api_url,
            timeout_seconds=self.local_slm_timeout_seconds,
        )
        self.stages = {
            criteria_stage.name: criteria_stage,
            inspiration_stage.name: inspiration_stage,
            itinerary_stage.name: itinerary_stage,
            booking_stage.name: booking_stage,
            activities_stage.name: activities_stage,
        }

    def run_stage(self, stage_name: str, stage_input: Any) -> Dict[str, Any]:
        if stage_name not in self.stages:
            raise ValueError(f"Unknown stage: {stage_name}")
        self.session.add_activity_log("stage", f"Starting stage: {stage_name}")
        output_obj = self.stages[stage_name].run(stage_input, self.session)
        output = output_obj.model_dump()
        self.session.add_stage_output(stage_name, output)
        self.session.add_activity_log("stage", f"Completed stage: {stage_name}")
        llm_info = self._get_llm_client().get_last_call_info()
        if llm_info and llm_info.get("cache_key"):
            self.session.add_activity_log(
                "llm",
                f"{stage_name}: {llm_info.get('message', 'LLM call finished')}",
            )
        return output

    def run_initial_flow(self, user_message: str) -> Dict[str, Any]:
        effective_message = self._resolve_user_message_for_run(user_message)
        self.session.add_user_message(effective_message)
        self._record_action(
            action_name="run_plan",
            source="user_button",
            args={"input_text": effective_message},
            note="User triggered Plan Trip.",
        )

        prior_context = {
            "traveler_profile": self.session.get_traveler_profile().model_dump(),
            "active_itinerary": (
                self.session.get_active_itinerary().model_dump() if self.session.get_active_itinerary() else None
            ),
        }
        criteria_output = self.run_stage(
            "criteria_gathering",
            CriteriaStageInput(user_message=effective_message, prior_context=prior_context),
        )
        traveler_profile = self.session.hydrate_traveler_profile_from_criteria(criteria_output)

        inspiration_output = self.run_stage(
            "inspiration",
            InspirationStageInput(
                criteria=criteria_output["extracted_criteria"],
                user_specifics=criteria_output.get("user_specifics", []),
                extracted_signals=criteria_output.get("extracted_signals", {}),
                constraints=criteria_output.get("constraints", []),
            ),
        )
        self._refresh_destination_alternatives(criteria_output, inspiration_output)
        self._kickoff_enrichment_for_generated_options(criteria_output)
        self._schedule_option_comparison_guidance()
        self._schedule_local_slm_probe()
        self._auto_refresh_advisor(trigger_message="Plan completed.")

        return {
            "session_id": self.session.state.session_id,
            "stages_run": ["criteria_gathering", "inspiration"],
            "breadcrumb": self._build_breadcrumb(),
            "replay_mode": self._get_llm_client().is_replay_mode(),
            "effective_user_message": effective_message,
            "traveler_profile": traveler_profile.model_dump(),
            "traveler_profiles": self.session.list_traveler_profiles(),
            "active_traveler_profile_id": self.session.state.active_traveler_profile_id,
            "itinerary_options": [o.model_dump() for o in self.session.state.itinerary_options],
            "active_itinerary_id": self.session.state.active_itinerary_id,
            "criteria_output": criteria_output,
            "inspiration_output": inspiration_output,
        }

    def _schedule_local_slm_probe(self) -> None:
        if not self.enable_local_slm_probe or self._shutting_down:
            return
        self.session.add_activity_log("local-slm", "Probe requested: checking local model connectivity.")
        self._executor.submit(self._run_local_slm_probe)

    def _run_local_slm_probe(self) -> None:
        if self._shutting_down:
            return
        payload = {
            "model": self.local_slm_model,
            "prompt": "Reply in one short line: LOCAL_OK",
            "stream": False,
            "options": {"temperature": 0.0},
        }
        req = request.Request(
            self.local_slm_api_url,
            method="POST",
            headers={"Content-Type": "application/json"},
            data=json.dumps(payload).encode("utf-8"),
        )
        try:
            with request.urlopen(req, timeout=self.local_slm_timeout_seconds) as resp:
                body = json.loads(resp.read().decode("utf-8"))
                text = str(body.get("response") or "").strip()
                short = text[:180] + ("..." if len(text) > 180 else "")
                self.session.add_activity_log(
                    "local-slm",
                    f"Probe success ({self.local_slm_model}): {short or '[empty response]'}",
                )
        except TimeoutError:
            self.session.add_activity_log(
                "local-slm",
                f"Probe timeout after {self.local_slm_timeout_seconds}s ({self.local_slm_model}).",
            )
        except URLError as exc:
            self.session.add_activity_log("local-slm", f"Probe connection error: {exc}")
        except Exception as exc:
            self.session.add_activity_log("local-slm", f"Probe failed: {exc}")

    def list_stages(self) -> List[str]:
        return list(self.stages.keys())

    def get_session_snapshot(self) -> Dict[str, Any]:
        return self.session.state.model_dump()

    def get_workspace_state(self) -> Dict[str, Any]:
        active = self.session.get_active_itinerary()
        return {
            "session_id": self.session.state.session_id,
            "breadcrumb": self._build_breadcrumb(),
            "replay_mode": self._get_llm_client().is_replay_mode(),
            "replay_cache_available": {
                "criteria_gathering": self._get_llm_client().has_cached_call("criteria_gathering"),
                "inspiration": self._get_llm_client().has_cached_call("inspiration"),
                "inspiration_additional": self._get_llm_client().has_cached_call("inspiration_additional"),
            },
            "llm_last_call_info": self._get_llm_client().get_last_call_info(),
            "last_cached_prompt_input": self._get_last_cached_user_message(),
            "traveler_profile": self.session.get_traveler_profile().model_dump(),
            "traveler_profiles": self.session.list_traveler_profiles(),
            "active_traveler_profile_id": self.session.state.active_traveler_profile_id,
            "itinerary_options": [o.model_dump() for o in self.session.state.itinerary_options],
            "active_itinerary_id": self.session.state.active_itinerary_id,
            "active_itinerary": active.model_dump() if active else None,
            "itinerary_fit_report": [x.model_dump() for x in self.session.state.itinerary_fit_report],
            "comparison_guidance": self.session.get_comparison_guidance().model_dump(),
            "comparison_mode": "local_auto" if self.use_local_comparator else "gemini_on_demand",
            "action_history": [a.model_dump() for a in self.session.state.action_history],
            "advisor_chat": [c.model_dump() for c in self.session.state.advisor_chat],
            "advisor_suggestion": self.session.get_advisor_suggestion().model_dump(),
            "llm_activity": [e.model_dump() for e in self.session.state.llm_activity],
            "history_count": len(self.session.state.stage_history),
        }

    def update_traveler_profile(self, patch: Dict[str, Any], source: str = "user_button") -> Dict[str, Any]:
        updated = self.session.upsert_traveler_profile(patch)
        self._record_action("update_traveler_profile", source=source, args={"patch": patch})
        if self.use_local_comparator:
            self._schedule_option_comparison_guidance()
        if source != "profile_autosave":
            self._auto_refresh_advisor(trigger_message="Traveler profile updated.")
        return updated.model_dump()

    def create_traveler_profile(self, profile_name: str, source: str = "user_button") -> Dict[str, Any]:
        created = self.session.create_traveler_profile(profile_name)
        self._record_action("create_profile", source=source, args={"profile_name": profile_name})
        self.session.add_activity_log("profile", f"Created traveler profile: {created.profile_name}")
        self._auto_refresh_advisor(trigger_message="Traveler profile created.")
        return created.model_dump()

    def select_traveler_profile(self, profile_id: str, source: str = "user_button") -> Dict[str, Any]:
        selected = self.session.select_traveler_profile(profile_id)
        self._record_action("switch_profile", source=source, args={"profile_id": profile_id})
        self.session.add_activity_log("profile", f"Switched traveler profile: {selected.profile_name}")
        self._auto_refresh_advisor(trigger_message="Traveler profile switched.")
        return selected.model_dump()

    def check_itinerary_fit(self, source: str = "user_button") -> List[Dict[str, Any]]:
        profile = self.session.get_traveler_profile()
        budget_max = profile.budget_max_usd
        hour_limit = self._extract_hour_limit(profile.travel_time_tolerance)
        report: List[Dict[str, Any]] = []
        for option in self.session.state.itinerary_options:
            destination = option.chosen_destination
            if not destination:
                continue
            issues: List[str] = []
            severity = 0

            if budget_max is not None and option.estimated_trip_cost_usd is not None:
                if option.estimated_trip_cost_usd > budget_max:
                    over = option.estimated_trip_cost_usd - budget_max
                    issues.append(f"Estimated cost exceeds budget by about ${over:,.0f}.")
                    severity = max(severity, 2 if over > max(300, budget_max * 0.15) else 1)

            if hour_limit is not None and option.estimated_travel_time_hours is not None:
                if option.estimated_travel_time_hours > hour_limit:
                    issues.append(
                        f"Estimated flight time ({option.estimated_travel_time_hours:.1f}h) exceeds tolerance ({hour_limit:.1f}h)."
                    )
                    severity = max(severity, 2 if option.estimated_travel_time_hours > hour_limit + 2 else 1)

            if profile.trip_type == "business" and (option.recommended_length_days or 0) > 7:
                issues.append("Recommended trip length may be long for a business-focused trip.")
                severity = max(severity, 1)

            fit_status = "fit"
            suggested_action = "Keep this option as-is."
            if severity == 1:
                fit_status = "caution"
                suggested_action = "Adjust trip length/flight routing or review budget assumptions."
            elif severity >= 2:
                fit_status = "conflict"
                suggested_action = "Revise this itinerary or deprioritize it."

            report.append(
                {
                    "option_id": option.option_id,
                    "name": option.name,
                    "destination": destination,
                    "fit_status": fit_status,
                    "issues": issues,
                    "suggested_action": suggested_action,
                }
            )

        self.session.set_itinerary_fit_report(report)
        self._record_action("check_itinerary_fit", source=source, args={})
        self.session.add_activity_log("fit-check", f"Itinerary fit check completed for {len(report)} option(s).")
        self._auto_refresh_advisor(trigger_message="Itinerary fit check completed.")
        return report

    def create_itinerary_option(self, name: str, source: str = "user_button") -> Dict[str, Any]:
        option = self.session.create_itinerary_option(name=name)
        self._record_action("create_itinerary_option", source=source, args={"name": name})
        self._auto_refresh_advisor(trigger_message="New itinerary option created.")
        return option.model_dump()

    def generate_more_options(self, count: int = 2, source: str = "user_button") -> List[Dict[str, Any]]:
        latest_criteria = self.session.get_latest_stage_output("criteria_gathering")
        if not latest_criteria:
            raise ValueError("No criteria context found. Run Plan Trip first.")

        existing_destinations = [
            o.chosen_destination
            for o in self.session.state.itinerary_options
            if o.chosen_destination
        ]
        signal_patch = dict(latest_criteria.get("extracted_signals", {}) or {})
        signal_patch["request_additional_options"] = count
        signal_patch["exclude_destinations"] = existing_destinations

        inspiration_output = self.run_stage(
            "inspiration",
            InspirationStageInput(
                criteria=latest_criteria["extracted_criteria"],
                user_specifics=latest_criteria.get("user_specifics", []),
                extracted_signals=signal_patch,
                constraints=latest_criteria.get("constraints", []),
            ),
        )
        destinations = [
            d for d in inspiration_output.get("top_destinations", []) if isinstance(d, dict) and d.get("destination")
        ]
        created = self.session.append_generated_destination_options(
            criteria_snapshot=latest_criteria.get("extracted_criteria"),
            destinations=destinations,
        )
        self._record_action("generate_more_options", source=source, args={"count": count})
        self._kickoff_enrichment_for_specific_options(
            criteria_output=latest_criteria,
            option_ids=[o.option_id for o in created],
        )
        if self.use_local_comparator:
            self._schedule_option_comparison_guidance()
        self._auto_refresh_advisor(trigger_message="Additional options generated.")
        return [o.model_dump() for o in created]

    def select_itinerary_option(self, option_id: str, source: str = "user_button") -> Dict[str, Any]:
        option = self.session.select_itinerary_option(option_id)
        if not option:
            raise ValueError(f"Unknown itinerary option id: {option_id}")
        self._record_action("select_itinerary_option", source=source, args={"option_id": option_id})
        if self.use_local_comparator:
            self._schedule_option_comparison_guidance()
        self._auto_refresh_advisor(trigger_message="Itinerary selection changed.")
        return option.model_dump()

    def update_itinerary_option(self, option_id: str, patch: Dict[str, Any], source: str = "user_button") -> Dict[str, Any]:
        option = self.session.update_itinerary_option(option_id, patch)
        if not option:
            raise ValueError(f"Unknown itinerary option id: {option_id}")
        self._record_action("update_itinerary_option", source=source, args={"option_id": option_id, "patch": patch})
        if self.use_local_comparator:
            self._schedule_option_comparison_guidance()
        self._auto_refresh_advisor(trigger_message="Itinerary option updated.")
        return option.model_dump()

    def delete_itinerary_option(self, option_id: str, source: str = "user_button") -> bool:
        removed = self.session.delete_itinerary_option(option_id)
        self._record_action("delete_itinerary_option", source=source, args={"option_id": option_id}, status="completed" if removed else "failed")
        if removed and self.use_local_comparator:
            self._schedule_option_comparison_guidance()
        if removed:
            self._auto_refresh_advisor(trigger_message="Itinerary option deleted.")
        return removed

    def generate_comparison_guidance_on_demand(self, source: str = "user_button") -> Dict[str, Any]:
        self._record_action("generate_help_me_choose", source=source, args={})
        options = [o for o in self.session.state.itinerary_options if o.chosen_destination]
        if len(options) < 2:
            guidance = self.session.set_comparison_guidance(
                {
                    "status": "not_started",
                    "summary": "Need at least two destination options to compare.",
                    "recommendation": None,
                    "option_notes": [],
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                    "model": "gemini-2.5-flash",
                    "error": None,
                }
            )
            return guidance.model_dump()

        self.session.set_comparison_guidance(
            {
                "status": "pending",
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "model": "gemini-2.5-flash",
                "error": None,
            }
        )
        self.session.add_activity_log("compare", "Generating Help Me Choose guidance (Cursor on-demand).")
        compact_options = [self._build_comparison_option_view(o.model_dump()) for o in options]
        traveler_profile = self.session.get_traveler_profile().model_dump()
        prompt = f"""
You are a travel decision assistant.
Return a clear human-readable comparison memo (plain text, not JSON).

Traveler profile:
{traveler_profile}

Itinerary options:
{compact_options}

Rules:
- Compare options for this traveler specifically.
- Keep concise and practical.
- Include sections with headings:
  1) Quick Recommendation
  2) Option-by-Option Pros/Cons
  3) What to Decide Next
- Recommendation should be specific and actionable.
"""
        llm_client = self._get_llm_client()
        prior_replay = llm_client.is_replay_mode()
        llm_client.set_replay_mode(False)
        try:
            output_text = llm_client.generate_text(
                prompt=prompt,
                cache_key="comparison_guidance_on_demand",
                metadata={"source": "on_demand_compare"},
            )
            guidance = self.session.set_comparison_guidance(
                {
                    "status": "completed",
                    "summary": output_text,
                    "recommendation": None,
                    "option_notes": [],
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                    "model": "gemini-2.5-flash",
                    "error": None,
                }
            )
            self.session.add_activity_log("compare", "Help Me Choose guidance completed.")
            self._auto_refresh_advisor(trigger_message="Comparison guidance generated.")
            return guidance.model_dump()
        except Exception as exc:
            guidance = self.session.set_comparison_guidance(
                {
                    "status": "error",
                    "summary": None,
                    "recommendation": None,
                    "option_notes": [],
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                    "model": "gemini-2.5-flash",
                    "error": str(exc),
                }
            )
            self.session.add_activity_log("compare", f"Help Me Choose failed: {exc}")
            return guidance.model_dump()
        finally:
            llm_client.set_replay_mode(prior_replay)

    def set_replay_mode(self, enabled: bool) -> Dict[str, Any]:
        self._get_llm_client().set_replay_mode(enabled)
        return {
            "replay_mode": self._get_llm_client().is_replay_mode(),
            "replay_cache_available": {
                "criteria_gathering": self._get_llm_client().has_cached_call("criteria_gathering"),
                "inspiration": self._get_llm_client().has_cached_call("inspiration"),
                "inspiration_additional": self._get_llm_client().has_cached_call("inspiration_additional"),
            },
        }

    def build_active_itinerary(self, source: str = "user_button") -> Dict[str, Any]:
        started = time.perf_counter()
        print("[itinerary-build] build_active_itinerary invoked")
        active = self.session.get_active_itinerary()
        if not active:
            self.session.add_activity_log("itinerary", "Build itinerary requested with no active itinerary.")
            print("[itinerary-build] no active itinerary")
            raise ValueError("No active itinerary selected.")
        if not active.chosen_destination:
            self.session.add_activity_log("itinerary", "Build itinerary requested but destination is missing.")
            print("[itinerary-build] active itinerary missing destination")
            raise ValueError("Active itinerary has no destination.")
        enrich_status = str(active.destination_enrichment.status or "").strip().lower()
        if enrich_status in {"pending", "not_started"}:
            self.session.add_activity_log(
                "itinerary",
                f"Build blocked for {active.chosen_destination}: enrichment status is '{active.destination_enrichment.status}'.",
            )
            print(f"[itinerary-build] blocked; enrichment status={active.destination_enrichment.status}")
            raise ValueError("Active itinerary enrichment is not complete yet.")
        if enrich_status == "error":
            self.session.add_activity_log(
                "itinerary",
                f"Building with enrichment warnings for {active.chosen_destination} due to enrichment errors.",
            )

        days = active.recommended_length_days or 6
        traveler_profile = self.session.get_traveler_profile().model_dump()
        enrichment = active.destination_enrichment.model_dump()
        schema_json = json.dumps(ItineraryBuildOutput.model_json_schema(), ensure_ascii=True)
        prompt = f"""
You are a travel itinerary planner.
Return strict JSON matching this schema:
{schema_json}

Destination option:
{self._build_comparison_option_view(active.model_dump())}

Traveler profile:
{traveler_profile}

Destination enrichment:
{enrichment}

Rules:
- Build a day-by-day plan using exactly {days} days.
- Include travel-in, daily city-to-stay + activity, and travel-out.
- Use destination enrichment as factual planning context, especially weather and top activities.
- If weather or conditions suggest a better split, choose multi-city and explain it.
- Avoid activities on days with likely poor weather when alternatives are available.
- If a multi-city plan is better, set `is_multi_city=true` and include `intercity_travel_time` on relevant days.
- If single-city, keep `intercity_travel_time` null/empty.
- Keep the human summary concise and easy to read.
"""
        llm_client = self._get_llm_client()
        prior_replay = llm_client.is_replay_mode()
        print(f"[itinerary-build] replay_mode_before={prior_replay}; forcing live call")
        llm_client.set_replay_mode(False)
        self.session.add_activity_log("itinerary", f"Building detailed itinerary for {active.chosen_destination}.")
        print(f"[itinerary-build] calling Cursor LLM for option_id={active.option_id} destination={active.chosen_destination}")
        try:
            output = llm_client.generate_structured(
                prompt=prompt,
                schema=ItineraryBuildOutput,
                cache_key=f"itinerary_build::{active.option_id}",
                metadata={"option_id": active.option_id, "destination": active.chosen_destination},
            )
            print("[itinerary-build] Cursor LLM returned structured itinerary")
            updated = self.session.update_itinerary_option(
                active.option_id,
                {
                    "itinerary_plan_human": output.human_readable_summary,
                    "itinerary_plan_json": output.model_dump(),
                    "stages_completed": sorted(set(active.stages_completed + ["itinerary_formation"])),
                },
            )
            self.session.add_activity_log("itinerary", f"Detailed itinerary built for {active.chosen_destination}.")
            self._record_action("build_chosen_itinerary", source=source, args={"option_id": active.option_id})
            self._auto_refresh_advisor(trigger_message="Chosen itinerary built.")
            print(f"[itinerary-build] itinerary option updated successfully elapsed_ms={int((time.perf_counter() - started) * 1000)}")
            return updated.model_dump() if updated else {}
        except Exception as exc:
            self.session.add_activity_log("itinerary", f"Itinerary build failed for {active.chosen_destination}: {exc}")
            print(f"[itinerary-build] failed after_ms={int((time.perf_counter() - started) * 1000)} error={exc}")
            raise
        finally:
            llm_client.set_replay_mode(prior_replay)
            print(f"[itinerary-build] replay_mode_restored={prior_replay}")

    def shortlist_itinerary_options(self, option_ids: List[str], source: str = "user_button") -> List[Dict[str, Any]]:
        shortlisted = self.session.shortlist_itinerary_options(option_ids)
        self._record_action("shortlist_itinerary_options", source=source, args={"option_ids": option_ids})
        self._auto_refresh_advisor(trigger_message="Itinerary shortlist updated.")
        return [o.model_dump() for o in shortlisted]

    def _refresh_destination_alternatives(
        self, criteria_output: Dict[str, Any], inspiration_output: Dict[str, Any]
    ) -> None:
        destinations = [
            d for d in inspiration_output.get("top_destinations", []) if isinstance(d, dict) and d.get("destination")
        ]
        self.session.replace_generated_destination_options(
            criteria_snapshot=criteria_output.get("extracted_criteria"),
            destinations=destinations,
        )

    def _kickoff_enrichment_for_generated_options(self, criteria_output: Dict[str, Any]) -> None:
        option_ids = [
            o.option_id for o in self.session.state.itinerary_options if o.source == "generated" and o.chosen_destination
        ]
        self._kickoff_enrichment_for_specific_options(criteria_output=criteria_output, option_ids=option_ids)

    def _kickoff_enrichment_for_specific_options(self, criteria_output: Dict[str, Any], option_ids: List[str]) -> None:
        if self._shutting_down:
            return
        for option_id in option_ids:
            option = next((o for o in self.session.state.itinerary_options if o.option_id == option_id), None)
            if not option or not option.chosen_destination:
                continue
            self.session.update_itinerary_option(
                option_id,
                {"destination_enrichment": {"status": "pending", "error": None}},
            )
            self._executor.submit(
                self._enrich_single_option,
                option_id,
                option.chosen_destination,
                criteria_output.get("extracted_criteria", {}),
            )

    def _enrich_single_option(self, option_id: str, destination: str, criteria: Dict[str, Any]) -> None:
        try:
            self.session.add_activity_log("enrichment", f"Enrichment started for {destination}.")
            travel_window = criteria.get("travel_dates") or "unspecified window"
            traveler_profile = self.session.get_traveler_profile().model_dump()
            self.session.add_activity_log("enrichment", f"Weather lookup for {destination}.")
            weather = self.weather_provider.fetch(
                {"destination": destination, "travel_window": travel_window, "traveler_profile": traveler_profile}
            )
            self.session.add_activity_log("enrichment", f"Reviews lookup for {destination}.")
            reviews = self.reviews_provider.fetch({"destination": destination, "traveler_profile": traveler_profile})
            self.session.add_activity_log("enrichment", f"Activities lookup for {destination}.")
            activities = self.activities_provider.fetch(
                {
                    "destination": destination,
                    "traveler_profile": traveler_profile,
                    "criteria": criteria,
                }
            )
            enrichment_patch = {
                "destination_enrichment": {
                    "status": "completed",
                    "weather_summary": weather.get("summary"),
                    "reviews_summary": reviews.get("sentiment_summary"),
                    "top_things_to_do": activities.get("top_five", []),
                    "weather_details": weather,
                    "reviews_details": reviews,
                    "activities_details": activities,
                    "sources": {
                        "weather": weather.get("sources", []),
                        "reviews": reviews.get("sources", []),
                        "activities": activities.get("sources", []),
                    },
                    "error": None,
                }
            }
            self.session.update_itinerary_option(option_id, enrichment_patch)
            self.session.add_activity_log("enrichment", f"Enrichment completed for {destination}.")
            if self.use_local_comparator:
                self._schedule_option_comparison_guidance()
        except Exception as exc:
            self.session.update_itinerary_option(
                option_id,
                {"destination_enrichment": {"status": "error", "error": str(exc)}},
            )
            self.session.add_activity_log("enrichment", f"Enrichment failed for {destination}: {exc}")
            if self.use_local_comparator:
                self._schedule_option_comparison_guidance()

    def _build_breadcrumb(self) -> List[Dict[str, str]]:
        completed = [r.stage_name for r in self.session.state.stage_history]
        completed_set = set(completed)
        current = completed[-1] if completed else self.PLANNING_STAGES[0]
        breadcrumb = []
        for stage in self.PLANNING_STAGES:
            if stage == current:
                status = "current"
            elif stage in completed_set:
                status = "completed"
            else:
                status = "upcoming"
            breadcrumb.append({"stage": stage, "status": status})
        return breadcrumb

    def _get_llm_client(self):
        return self.stages["criteria_gathering"].llm_client

    def _get_last_cached_user_message(self) -> Optional[str]:
        cached = self._get_llm_client().get_cached_call("criteria_gathering")
        if not cached:
            return None
        metadata = cached.get("metadata") or {}
        msg = metadata.get("user_message")
        return msg if isinstance(msg, str) and msg.strip() else None

    def _resolve_user_message_for_run(self, user_message: str) -> str:
        if not self._get_llm_client().is_replay_mode():
            return user_message
        cached = self._get_last_cached_user_message()
        if cached:
            return cached
        return user_message

    def _schedule_option_comparison_guidance(self) -> None:
        if not self.use_local_comparator:
            return
        if self._shutting_down:
            return
        if self._comparison_job_inflight:
            print("[local-comparator] comparison job already in-flight; marking dirty for rerun")
            self._comparison_job_dirty = True
            return
        print("[local-comparator] scheduling comparison job")
        self._comparison_job_inflight = True
        self._executor.submit(self._refresh_option_comparison_guidance_background)

    def _refresh_option_comparison_guidance_background(self) -> None:
        if self._shutting_down:
            return
        print("[local-comparator] background job started")
        try:
            self._refresh_option_comparison_guidance()
        except Exception as exc:
            print(f"[local-comparator] background job crashed: {exc}")
            self.session.set_comparison_guidance(
                {
                    "status": "error",
                    "summary": None,
                    "recommendation": None,
                    "option_notes": [],
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                    "model": self.comparator_provider.model_name,
                    "error": f"Background comparator failure: {exc}",
                }
            )
        finally:
            self._comparison_job_inflight = False
            print("[local-comparator] background job finished")
            if self._comparison_job_dirty:
                self._comparison_job_dirty = False
                print("[local-comparator] rerunning due to dirty flag")
                self._schedule_option_comparison_guidance()

    def _refresh_option_comparison_guidance(self) -> None:
        if not self.use_local_comparator:
            return
        if self._shutting_down:
            return
        options = [o for o in self.session.state.itinerary_options if o.chosen_destination]
        print(f"[local-comparator] refresh requested with {len(options)} comparable options")
        if len(options) < 2:
            print("[local-comparator] not enough options to compare")
            self.session.set_comparison_guidance(
                {
                    "status": "not_started",
                    "summary": "Need at least two destination options to compare.",
                    "recommendation": None,
                    "option_notes": [],
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                    "model": self.comparator_provider.model_name,
                    "error": None,
                }
            )
            return
        self.session.set_comparison_guidance(
            {
                "status": "pending",
                "error": None,
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "model": self.comparator_provider.model_name,
            }
        )
        print("[local-comparator] status set to pending; invoking local model")
        try:
            payload = self.comparator_provider.fetch(
                {
                    "traveler_profile": self.session.get_traveler_profile().model_dump(),
                    "itinerary_options": [self._build_comparison_option_view(o.model_dump()) for o in options],
                }
            )
            self.session.set_comparison_guidance(payload)
            print("[local-comparator] comparison completed successfully")
        except Exception as exc:
            print(f"[local-comparator] comparison failed: {exc}")
            self.session.set_comparison_guidance(
                {
                    "status": "error",
                    "summary": None,
                    "recommendation": None,
                    "option_notes": [],
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                    "model": self.comparator_provider.model_name,
                    "error": str(exc),
                }
            )

    @staticmethod
    def _build_comparison_option_view(option: Dict[str, Any]) -> Dict[str, Any]:
        enrich = option.get("destination_enrichment", {}) or {}
        things = enrich.get("top_things_to_do", []) or []
        return {
            "option_id": option.get("option_id"),
            "name": option.get("name"),
            "status": option.get("status"),
            "destination": option.get("chosen_destination"),
            "estimated_trip_cost_usd": option.get("estimated_trip_cost_usd"),
            "estimated_travel_time_hours": option.get("estimated_travel_time_hours"),
            "recommended_length_days": option.get("recommended_length_days"),
            "trip_highlight": option.get("trip_highlight"),
            "weather_summary": enrich.get("weather_summary"),
            "reviews_summary": enrich.get("reviews_summary"),
            "top_things_to_do": things[:3],
        }

    @staticmethod
    def _extract_hour_limit(tolerance_text: Optional[str]) -> Optional[float]:
        if not tolerance_text:
            return None
        text = tolerance_text.lower()
        import re

        m = re.search(r"(\d+(?:\.\d+)?)\s*hour", text)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                return None
        if "short-haul" in text:
            return 4.0
        if "medium-haul" in text:
            return 8.0
        if "long-haul" in text:
            return 14.0
        return None

    def advisor_suggest(
        self,
        user_message: str = "",
        persist_user_chat: bool = True,
        use_local_model: bool = True,
    ) -> Dict[str, Any]:
        message = (user_message or "").strip()
        print(
            f"[advisor] advisor_suggest called persist_user_chat={persist_user_chat} "
            f"use_local_model={use_local_model} message='{message[:120]}'"
        )
        if message and persist_user_chat:
            self.session.add_advisor_chat("user", message)
            self._record_action("advisor_user_message", source="advisor_chat", args={"message": message})

        workspace = self.get_workspace_state()
        action_history = [a.model_dump() for a in self.session.state.action_history]
        if not use_local_model:
            fallback = self.advisor.fallback_suggest(
                workspace_state=workspace,
                latest_user_message=message,
            )
            fallback["model"] = f"{self.local_slm_model} (rule-fast)"
            saved = self.session.set_advisor_suggestion(fallback)
            if saved.assistant_message:
                self.session.add_advisor_chat("assistant", saved.assistant_message)
            self.session.add_activity_log("advisor", "Rule-based advisor suggestion generated (fast path).")
            self._record_action("advisor_suggest", source="advisor_rule", args={"message": message})
            return saved.model_dump()
        try:
            suggestion = self.advisor.suggest(
                workspace_state=workspace,
                action_history=action_history,
                latest_user_message=message,
            )
            saved = self.session.set_advisor_suggestion(suggestion)
            if saved.assistant_message:
                self.session.add_advisor_chat("assistant", saved.assistant_message)
            self.session.add_activity_log("advisor", "Local advisor suggested next actions.")
            self._record_action("advisor_suggest", source="advisor", args={"message": message})
            print(
                f"[advisor] suggestion ready action={saved.primary_recommendation.action} "
                f"confidence={saved.confidence}"
            )
            return saved.model_dump()
        except Exception as exc:
            print(f"[advisor] suggest failed; using fallback error={exc}")
            fallback = self.advisor.fallback_suggest(
                workspace_state=workspace,
                latest_user_message=message,
            )
            fallback["error"] = str(exc)
            saved = self.session.set_advisor_suggestion(fallback)
            if saved.assistant_message:
                self.session.add_advisor_chat("assistant", saved.assistant_message)
            self.session.add_activity_log("advisor", f"Local advisor fallback used: {exc}")
            return saved.model_dump()

    def advisor_approve_and_execute(self) -> Dict[str, Any]:
        suggestion = self.session.get_advisor_suggestion()
        proposal = suggestion.primary_recommendation
        action = (proposal.action or "").strip()
        args = proposal.args or {}
        print(f"[advisor] approve requested action={action} args_keys={list(args.keys())}")
        if not action or action == "none":
            raise ValueError("No actionable advisor recommendation available.")
        result = self._execute_action(action=action, args=args, source="advisor_approved")
        self.session.add_activity_log("advisor", f"Approved and executed advisor action: {action}.")
        print(f"[advisor] approve executed action={action}")
        return {"executed_action": action, "result": result}

    def advisor_approve_alternative_and_execute(self, alternative_index: int = 0) -> Dict[str, Any]:
        suggestion = self.session.get_advisor_suggestion()
        alternatives = suggestion.alternatives or []
        if not alternatives:
            raise ValueError("No alternative advisor actions available.")
        if alternative_index < 0 or alternative_index >= len(alternatives):
            raise ValueError("Alternative index out of range.")
        proposal = alternatives[alternative_index]
        action = (proposal.action or "").strip()
        args = proposal.args or {}
        print(f"[advisor] approve alternative requested idx={alternative_index} action={action}")
        if not action or action == "none":
            raise ValueError("Selected alternative has no actionable operation.")
        result = self._execute_action(action=action, args=args, source="advisor_approved_alternative")
        self.session.add_activity_log(
            "advisor",
            f"Approved and executed advisor alternative action[{alternative_index}]: {action}.",
        )
        print(f"[advisor] approve alternative executed idx={alternative_index} action={action}")
        return {"executed_action": action, "alternative_index": alternative_index, "result": result}

    def _execute_action(self, action: str, args: Dict[str, Any], source: str) -> Any:
        print(f"[advisor] dispatch action={action} source={source}")
        if action == "run_plan":
            text = str(args.get("input_text") or "").strip()
            if not text:
                raise ValueError("run_plan requires 'input_text'.")
            return self.run_initial_flow(text)
        if action == "check_itinerary_fit":
            return self.check_itinerary_fit(source=source)
        if action == "generate_more_options":
            count = int(args.get("count", 2))
            return self.generate_more_options(count=count, source=source)
        if action == "generate_help_me_choose":
            return self.generate_comparison_guidance_on_demand(source=source)
        if action == "build_chosen_itinerary":
            return self.build_active_itinerary(source=source)
        if action == "switch_profile":
            profile_id = str(args.get("profile_id") or "").strip()
            if not profile_id:
                raise ValueError("switch_profile requires 'profile_id'.")
            return self.select_traveler_profile(profile_id=profile_id, source=source)
        if action == "update_traveler_profile":
            patch = args.get("patch") or {}
            if not isinstance(patch, dict):
                raise ValueError("update_traveler_profile requires object 'patch'.")
            return self.update_traveler_profile(patch=patch, source=source)
        raise ValueError(f"Unsupported advisor action: {action}")

    def _record_action(self, action_name: str, source: str, args: Dict[str, Any], note: Optional[str] = None, status: str = "completed") -> None:
        self.session.add_action_history(
            action_name=action_name,
            source=source,
            args=args,
            status=status,
            note=note,
        )

    def _auto_refresh_advisor(self, trigger_message: str) -> None:
        if self._shutting_down:
            return
        self.session.add_activity_log("advisor", f"Auto-suggest triggered: {trigger_message}")
        print(f"[advisor] auto_refresh trigger='{trigger_message}'")
        self._advisor_pending_message = f"System event: {trigger_message}"
        use_local = self.advisor_use_local_on_auto or self._should_use_local_for_trigger(trigger_message)
        self._advisor_pending_use_local = self._advisor_pending_use_local or use_local
        if self._advisor_job_inflight:
            self._advisor_job_dirty = True
            print("[advisor] auto_refresh coalesced (job in-flight)")
            return
        self._advisor_job_inflight = True
        print(f"[advisor] auto_refresh scheduling background suggestion use_local={use_local}")
        self._executor.submit(self._advisor_suggest_background)

    def _advisor_suggest_background(self) -> None:
        print("[advisor] background suggest start")
        try:
            self.advisor_suggest(
                user_message=self._advisor_pending_message,
                persist_user_chat=False,
                use_local_model=self._advisor_pending_use_local,
            )
        except Exception as exc:
            self.session.add_activity_log("advisor", f"Auto-suggest failed: {exc}")
            print(f"[advisor] background suggest failed: {exc}")
        finally:
            self._advisor_job_inflight = False
            self._advisor_pending_use_local = False
            print("[advisor] background suggest finished")
            if self._advisor_job_dirty and not self._shutting_down:
                self._advisor_job_dirty = False
                print("[advisor] background suggest rerun due to dirty flag")
                self._auto_refresh_advisor(trigger_message="Follow-up state update")

    @staticmethod
    def _should_use_local_for_trigger(trigger_message: str) -> bool:
        text = (trigger_message or "").lower()
        high_value = [
            "plan completed",
            "fit check completed",
            "additional options generated",
            "itinerary selection changed",
            "comparison guidance generated",
            "chosen itinerary built",
        ]
        return any(x in text for x in high_value)

    def shutdown(self) -> None:
        self._shutting_down = True
        self._comparison_job_dirty = False
        self._advisor_job_dirty = False
        if self.comparator_provider:
            try:
                self.comparator_provider.shutdown()
            except Exception as exc:
                print(f"[orchestrator] comparator shutdown warning: {exc}")
        try:
            self._executor.shutdown(wait=False, cancel_futures=True)
        except Exception as exc:
            print(f"[orchestrator] shutdown warning: {exc}")
