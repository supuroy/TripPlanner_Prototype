from __future__ import annotations

from typing import Any, Dict

from src.tools.base_provider import BaseProvider


class FlightsProvider(BaseProvider):
    name = "flights_provider"

    def fetch(self, query: Dict[str, Any]) -> Dict[str, Any]:
        return {"provider": self.name, "status": "placeholder", "query": query, "data": []}
