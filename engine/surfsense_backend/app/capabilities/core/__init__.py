"""Capability framework kernel: registry contracts, store and access doors."""

from app.capabilities.core.store import (
    all_capabilities,
    get_capability,
    register_capability,
)
from app.capabilities.core.types import (
    ActivityDescriptor,
    Capability,
    Executor,
)

__all__ = [
    "ActivityDescriptor",
    "Capability",
    "Executor",
    "all_capabilities",
    "get_capability",
    "register_capability",
]
