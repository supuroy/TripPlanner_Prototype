"""
v5 placeholder: stable commerce / fare metadata for UI copy — not live pricing.

Wire real inventory providers here later; keep function signatures stable for the mapper.
"""

from __future__ import annotations

from typing import Any


def placeholder_price_band(destination_name: str) -> dict[str, Any]:
    return {
        "destination": destination_name,
        "typical_range_usd": None,
        "currency": "USD",
        "disclaimer": "Illustrative band only — not a quote, fare, or availability.",
    }


def attach_commerce_placeholders(destinations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for d in destinations:
        row = dict(d)
        row["commerce"] = placeholder_price_band(str(row.get("name") or "Destination"))
        out.append(row)
    return out
