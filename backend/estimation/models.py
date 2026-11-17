from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class PlannedCall:
    node_ids: tuple[str, ...]
    node_type: str
    level: str
    prompt_parts: tuple[str, ...]
    estimated_input_tokens: int
    estimated_output_p50: int
    estimated_output_p90: int
    batched: bool = False
    features: dict[str, float] = field(default_factory=dict)


@dataclass
class Estimate:
    meta: dict[str, Any]
    levels: dict[str, Any]
    totals: dict[str, Any]
    assumptions: list[str]
    top_expensive: list[dict[str, Any]]
