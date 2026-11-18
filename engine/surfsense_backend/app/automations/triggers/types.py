"""``TriggerDefinition`` dataclass. Declarative; firing is the dispatcher's job."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel


@dataclass(frozen=True, slots=True)
class TriggerDefinition:
    type: str
    description: str
    params_model: type[BaseModel]
