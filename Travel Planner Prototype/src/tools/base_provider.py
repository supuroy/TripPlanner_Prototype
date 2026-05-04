from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict


class BaseProvider(ABC):
    name: str

    @abstractmethod
    def fetch(self, query: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError
