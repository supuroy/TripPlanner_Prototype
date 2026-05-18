"""Itinerary PDF export HTML + filename helpers."""

from __future__ import annotations

import json
from pathlib import Path

from src.wanderly.itinerary_pdf_export import (
    build_itinerary_print_html,
    itinerary_pdf_filename,
    load_trip_destination,
)


def test_itinerary_pdf_filename_slug():
    assert itinerary_pdf_filename({"id": "port-renfrew-bc", "name": "Port Renfrew"}) == "port-renfrew-bc-itinerary.pdf"


def test_print_html_includes_cover_and_compact_days():
    trip = Path(__file__).resolve().parents[1] / ".cache" / "wanderly_trips" / "trip-9693f3b3d414.json"
    if not trip.is_file():
        return
    dest, pills = load_trip_destination(trip, "port-renfrew-bc")
    html = build_itinerary_print_html(dest, pills)
    assert 'class="cover"' in html
    assert "cover-img" in html
    assert 'class="day-card compact"' in html
    assert "route-line" in html
    assert "Port Renfrew" in html


def test_print_html_accepts_client_profile_icon_as_emoji():
    fixture = {
        "id": "x",
        "name": "X",
        "itinerary": [{"day": 1, "label": "One", "items": ["a"]}],
    }
    pills = [{"key": "when", "label": "When", "value": "June", "icon": "📅"}]
    html = build_itinerary_print_html(fixture, pills)
    assert "📅" in html
    assert "June" in html


def test_print_html_from_fixture():
    fixture = {
        "id": "test-dest",
        "name": "Testville",
        "country": "Nowhere",
        "hook": "A sample hook.",
        "image": "https://example.com/hero.jpg",
        "itinerary": [
            {
                "day": 1,
                "label": "Arrival",
                "base": "Testville",
                "items": ["Do something"],
                "day_logistics": {
                    "surface_legs": [
                        {"kind": "drive", "from_label": "A, WA, United States", "to_label": "B, WA, United States"}
                    ],
                    "lodging": {
                        "destination_search_label": "Testville, Nowhere",
                        "check_in": "2026-06-01",
                        "check_out": "2026-06-02",
                    },
                },
            }
        ],
    }
    html = build_itinerary_print_html(fixture, [{"label": "When", "value": "June", "emoji": "📅"}])
    assert "Testville" in html
    assert "example.com/hero.jpg" in html
    assert "day-card compact" in html
