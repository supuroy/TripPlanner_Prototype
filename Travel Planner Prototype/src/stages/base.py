from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from src.session_manager import SessionManager


class Stage(ABC):
    name: str

    @abstractmethod
    def run(self, stage_input: Any, session: SessionManager) -> Any:
        raise NotImplementedError
