from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List
from urllib import request


class LocalOrchestrationAdvisor:
    def __init__(
        self,
        model_name: str = "llama3.2:1b",
        api_url: str = "http://127.0.0.1:11434/api/generate",
        timeout_seconds: int = 35,
    ) -> None:
        self.model_name = model_name
        self.api_url = api_url
        self.timeout_seconds = timeout_seconds

    def suggest(
        self,
        workspace_state: Dict[str, Any],
        action_history: List[Dict[str, Any]],
        latest_user_message: str,
    ) -> Dict[str, Any]:
        compact_workspace = self._compact_workspace_state(workspace_state)
        compact_history = action_history[-6:]
        print(
            f"[advisor] suggest start model={self.model_name} "
            f"history_count={len(compact_history)} message_len={len(latest_user_message or '')}"
        )
        prompt = self._build_prompt(compact_workspace, compact_history, latest_user_message)
        payload = {
            "model": self.model_name,
            "prompt": prompt,
            "stream": False,
            "keep_alive": "10m",
            "options": {
                "temperature": 0.15,
                "num_predict": 140,
                "num_ctx": 1024,
            },
        }
        req = request.Request(
            self.api_url,
            method="POST",
            headers={"Content-Type": "application/json"},
            data=json.dumps(payload).encode("utf-8"),
        )
        with request.urlopen(req, timeout=self.timeout_seconds) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            text = str(body.get("response") or "").strip()
            parsed = self._parse_or_coerce_payload(
                text=text,
                latest_user_message=latest_user_message,
                workspace_state=workspace_state,
            )
            parsed["generated_at"] = datetime.now(timezone.utc).isoformat()
            parsed["model"] = self.model_name
            parsed["error"] = None
            print("[advisor] suggest success")
            return parsed

    def fallback_suggest(self, workspace_state: Dict[str, Any], latest_user_message: str) -> Dict[str, Any]:
        print("[advisor] fallback_suggest invoked")
        msg = (latest_user_message or "").lower()
        if "help me choose" in msg or "compare" in msg or "decide" in msg:
            return {
                "assistant_message": "I can compare current itinerary options to help you choose.",
                "primary_recommendation": {
                    "action": "generate_help_me_choose",
                    "args": {},
                    "requires_approval": True,
                    "why": "User asked for decision support across itinerary alternatives.",
                },
                "alternatives": [
                    {
                        "action": "check_itinerary_fit",
                        "args": {},
                        "requires_approval": True,
                        "why": "Fit check can narrow options before comparison.",
                    }
                ],
                "confidence": 0.72,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "model": f"{self.model_name} (fallback)",
                "error": None,
            }
        options = workspace_state.get("itinerary_options") or []
        fit_report = workspace_state.get("itinerary_fit_report") or []
        active = workspace_state.get("active_itinerary") or {}
        enrich_status = ((active.get("destination_enrichment") or {}).get("status") or "").lower()

        if not options:
            primary = {
                "action": "run_plan",
                "args": {"input_text": latest_user_message or "I want a warm trip in December under $3000"},
                "requires_approval": True,
                "why": "No itinerary options exist yet.",
            }
            message = "Start by planning destinations from your latest prompt."
        elif fit_report and all((x.get("fit_status") == "conflict") for x in fit_report):
            primary = {
                "action": "generate_more_options",
                "args": {"count": 2},
                "requires_approval": True,
                "why": "Current options are poor fit against traveler profile.",
            }
            message = "Current options conflict with your profile. Generate more options."
        elif enrich_status in {"completed", "error"} and active.get("chosen_destination"):
            primary = {
                "action": "build_chosen_itinerary",
                "args": {},
                "requires_approval": True,
                "why": "Active itinerary has enough enrichment to proceed.",
            }
            message = "You can build the chosen itinerary now."
        else:
            primary = {
                "action": "check_itinerary_fit",
                "args": {},
                "requires_approval": True,
                "why": "Fit check helps identify which option to continue with.",
            }
            message = "Next best step is to run itinerary fit check."

        return {
            "assistant_message": message,
            "primary_recommendation": primary,
            "alternatives": [],
            "confidence": 0.45,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "model": f"{self.model_name} (fallback)",
            "error": None,
        }

    @staticmethod
    def _build_prompt(workspace_state: Dict[str, Any], action_history: List[Dict[str, Any]], latest_user_message: str) -> str:
        return f"""
You are an orchestration advisor for a travel planner.
Recommend only the next best action plus one alternative.
Be concise and practical.

Allowed actions:
- run_plan
- check_itinerary_fit
- generate_more_options
- generate_help_me_choose
- build_chosen_itinerary
- switch_profile
- update_traveler_profile
- none

latest_user_message: {latest_user_message}
recent_action_history_json: {json.dumps(action_history, ensure_ascii=True)}
workspace_summary_json: {json.dumps(workspace_state, ensure_ascii=True)}

Rules:
- The first item in `alternatives` MUST be a different action than `primary_recommendation.action`.
- Do NOT repeat the same action+args in primary and alternatives.
- If no meaningful alternative exists, return `alternatives` as an empty array.

Return STRICT JSON with this schema:
{{
  "assistant_message": "short string",
  "primary_recommendation": {{
    "action": "allowed action",
    "args": {{}},
    "requires_approval": true,
    "why": "short string"
  }},
  "alternatives": [
    {{
      "action": "allowed action",
      "args": {{}},
      "requires_approval": true,
      "why": "short string"
    }}
  ],
  "confidence": 0.0
}}
"""

    @staticmethod
    def _compact_workspace_state(workspace_state: Dict[str, Any]) -> Dict[str, Any]:
        options = workspace_state.get("itinerary_options") or []
        active_id = workspace_state.get("active_itinerary_id")
        compact_options: List[Dict[str, Any]] = []
        for o in options[:2]:
            enrich = (o.get("destination_enrichment") or {})
            compact_options.append(
                {
                    "option_id": o.get("option_id"),
                    "name": o.get("name"),
                    "status": o.get("status"),
                    "destination": o.get("chosen_destination"),
                    "estimated_trip_cost_usd": o.get("estimated_trip_cost_usd"),
                    "estimated_travel_time_hours": o.get("estimated_travel_time_hours"),
                    "recommended_length_days": o.get("recommended_length_days"),
                    "enrichment_status": enrich.get("status"),
                }
            )

        active = workspace_state.get("active_itinerary") or {}
        active_enrich = (active.get("destination_enrichment") or {})
        fit = workspace_state.get("itinerary_fit_report") or []
        fit_counts = {"fit": 0, "caution": 0, "conflict": 0}
        for x in fit:
            status = str(x.get("fit_status") or "").strip().lower()
            if status in fit_counts:
                fit_counts[status] += 1

        traveler = workspace_state.get("traveler_profile") or {}
        critical_missing: List[str] = []
        if traveler.get("budget_max_usd") is None:
            critical_missing.append("budget_max_usd")
        if not traveler.get("travel_time_tolerance"):
            critical_missing.append("travel_time_tolerance")
        if not traveler.get("destination_preferences"):
            critical_missing.append("destination_preferences")

        return {
            "stage": (workspace_state.get("breadcrumb") or [])[-1] if workspace_state.get("breadcrumb") else None,
            "traveler": {
                "budget_max_usd": traveler.get("budget_max_usd"),
                "trip_type": traveler.get("trip_type"),
                "travel_time_tolerance": traveler.get("travel_time_tolerance"),
                "destination_preferences_count": len(traveler.get("destination_preferences") or []),
                "critical_missing": critical_missing,
            },
            "active_itinerary": {
                "option_id": active.get("option_id"),
                "name": active.get("name"),
                "destination": active.get("chosen_destination"),
                "status": active.get("status"),
                "enrichment_status": active_enrich.get("status"),
            },
            "options_total": len(options),
            "top_options": compact_options,
            "itinerary_fit_counts": fit_counts,
            "comparison_status": (workspace_state.get("comparison_guidance") or {}).get("status"),
        }

    @staticmethod
    def _parse_json_payload(text: str) -> Dict[str, Any]:
        raw = (text or "").strip()
        if not raw:
            raise ValueError("Advisor returned empty response.")
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            start = raw.find("{")
            end = raw.rfind("}")
            if start != -1 and end != -1 and end > start:
                return json.loads(raw[start : end + 1])
            preview = raw[:300].replace("\n", "\\n")
            raise ValueError(f"Advisor returned invalid JSON. Preview: {preview}")

    def _parse_or_coerce_payload(
        self,
        text: str,
        latest_user_message: str,
        workspace_state: Dict[str, Any],
    ) -> Dict[str, Any]:
        try:
            parsed = self._parse_json_payload(text)
            return self._sanitize_suggestion_payload(parsed)
        except Exception:
            coerced = self._coerce_freeform_payload(
                free_text=text,
                latest_user_message=latest_user_message,
                workspace_state=workspace_state,
            )
            return self._sanitize_suggestion_payload(coerced)

    @staticmethod
    def _coerce_freeform_payload(
        free_text: str,
        latest_user_message: str,
        workspace_state: Dict[str, Any],
    ) -> Dict[str, Any]:
        text = (free_text or "").strip()
        combined = f"{latest_user_message}\n{text}".lower()
        action = "none"
        why = "Could not parse strict JSON; inferred likely next action from text intent."

        if "help me choose" in combined or "compare" in combined or "decide" in combined:
            action = "generate_help_me_choose"
        elif "fit" in combined:
            action = "check_itinerary_fit"
        elif "more option" in combined:
            action = "generate_more_options"
        elif "build" in combined and "itinerary" in combined:
            action = "build_chosen_itinerary"
        elif "switch profile" in combined:
            action = "switch_profile"
        elif "plan trip" in combined or "run plan" in combined:
            action = "run_plan"
        else:
            active = workspace_state.get("active_itinerary") or {}
            enrich_status = str(((active.get("destination_enrichment") or {}).get("status") or "")).lower()
            if active.get("chosen_destination") and enrich_status in {"completed", "error"}:
                action = "build_chosen_itinerary"
            elif workspace_state.get("itinerary_options"):
                action = "check_itinerary_fit"
            else:
                action = "run_plan"

        msg = text[:280] if text else "I inferred a next action from your request."
        return {
            "assistant_message": msg,
            "primary_recommendation": {
                "action": action,
                "args": {},
                "requires_approval": True,
                "why": why,
            },
            "alternatives": [],
            "confidence": 0.4,
        }

    @staticmethod
    def _sanitize_suggestion_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
        primary = payload.get("primary_recommendation") or {}
        primary_action = str(primary.get("action") or "").strip()
        primary_args = primary.get("args") if isinstance(primary.get("args"), dict) else {}
        primary_sig = (primary_action, json.dumps(primary_args, sort_keys=True, ensure_ascii=True))

        cleaned_alts: List[Dict[str, Any]] = []
        seen = {primary_sig}
        for alt in payload.get("alternatives") or []:
            if not isinstance(alt, dict):
                continue
            alt_action = str(alt.get("action") or "").strip()
            alt_args = alt.get("args") if isinstance(alt.get("args"), dict) else {}
            if not alt_action:
                continue
            sig = (alt_action, json.dumps(alt_args, sort_keys=True, ensure_ascii=True))
            if sig in seen:
                continue
            seen.add(sig)
            cleaned_alts.append(
                {
                    "action": alt_action,
                    "args": alt_args,
                    "requires_approval": bool(alt.get("requires_approval", True)),
                    "why": str(alt.get("why") or ""),
                }
            )
            if len(cleaned_alts) >= 2:
                break

        payload["alternatives"] = cleaned_alts
        if not cleaned_alts:
            msg = str(payload.get("assistant_message") or "").strip()
            if msg and "no alternative" not in msg.lower():
                payload["assistant_message"] = msg + " No strong alternative action right now."
        return payload
