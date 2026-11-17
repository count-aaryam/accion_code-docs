from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from backend.core.models import EntityKind
from backend.db.graph import neo4j_client
from backend.enrichment.job import PROMPT_VERSION, SYSTEM_PROMPT, build_enrichment_prompt
from backend.llm.gemini import GeminiProvider
from backend.settings import settings
from backend.estimation.models import Estimate, PlannedCall


def count_tokens(text_or_messages: str | list[dict[str, Any]]) -> dict[str, Any]:
    text = text_or_messages if isinstance(text_or_messages, str) else "\n".join(str(item.get("content", "")) for item in text_or_messages)
    return {"tokens": max(1, (len(text) + 3) // 4), "exact": False}


def plan(project_id: str, strategy: str = "bottom-up", options: dict[str, Any] | None = None) -> list[PlannedCall]:
    options = options or {}
    calls = []
    for kind in (EntityKind.FUNCTION.value, EntityKind.METHOD.value, EntityKind.CLASS.value, EntityKind.FILE.value, "Project"):
        for target in neo4j_client.enrichment_targets(project_id, kind):
            try:
                prompt, _ = build_enrichment_prompt(project_id, kind, target)
            except ValueError:
                continue
            input_tokens = count_tokens([{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}])["tokens"]
            default = settings.estimation_default_output_tokens.get(kind, 120)
            calls.append(PlannedCall((target["id"],), kind, kind.lower(), (SYSTEM_PROMPT, prompt), input_tokens, min(default, settings.estimation_max_output_tokens), min(default * 2, settings.estimation_max_output_tokens), features={"source_chars": len(prompt), "level": 0}))
    return calls


def estimate_project(project_id: str, model_id: str, strategy: str = "bottom-up", mode: str = "full", sample_size: int = 30, calibrate: bool = False) -> dict[str, Any]:
    calls = plan(project_id, strategy)
    records = neo4j_client.usage_records(project_id, model_id, PROMPT_VERSION)
    actual_by_kind: dict[str, list[int]] = defaultdict(list)
    for record in records:
        if record.get("outcome") == "success" and record.get("output_tokens", 0):
            actual_by_kind[record.get("node_type", "")].append(int(record["output_tokens"]))
    by_level: dict[str, list[PlannedCall]] = defaultdict(list)
    for call in calls:
        by_level[call.level].append(call)
    levels = {}
    for level, items in by_level.items():
        outputs = [round(statistics.median(actual_by_kind[level]))] * len(items) if len(actual_by_kind[level]) >= 10 else [i.estimated_output_p50 for i in items]
        levels[level] = {"nodes_total": len(items), "nodes_skipped": 0, "calls": len(items), "batched_calls": sum(i.batched for i in items), "input_tokens": _range([i.estimated_input_tokens for i in items]), "output_tokens": _range(outputs)}
    total_input_p50 = sum(item["input_tokens"]["p50"] for item in levels.values())
    total_input_p90 = sum(item["input_tokens"]["p90"] for item in levels.values())
    total_output_p50 = sum(item["output_tokens"]["p50"] for item in levels.values())
    total_output_p90 = sum(item["output_tokens"]["p90"] for item in levels.values())
    return {
        "meta": {"project_id": project_id, "model_id": model_id, "actual_model": settings.gemini_model if model_id == "gemini-free" else settings.groq_model, "strategy": strategy, "mode": mode, "prompt_version": PROMPT_VERSION, "generated_at": datetime.now(timezone.utc).isoformat(), "method": {level: "default" for level in levels}, "sample_size": min(sample_size, len(calls)), "confidence": "low", "reasons": ["Using conservative local token heuristic; no calibration records available"]},
        "levels": levels,
        "totals": {"calls": len(calls), "input_tokens": {"p50": total_input_p50, "p90": total_input_p90}, "output_tokens": {"p50": total_output_p50, "p90": total_output_p90}, "retry_overhead_factor": 1.0, "estimated_cost": 0, "fits_free_tier": True, "estimated_wall_clock_seconds": round(len(calls) / max(settings.llm_max_concurrency, 1) * 5, 1), "days_needed": 0},
        "assumptions": ["Token counts use approximately four characters per token", "Reasoning tokens are not measured", "No pilot generations were run"],
        "top_expensive": [{"name": call.node_ids[0], "path": call.node_ids[0], "estimated_tokens": call.estimated_input_tokens + call.estimated_output_p90} for call in sorted(calls, key=lambda x: x.estimated_input_tokens + x.estimated_output_p90, reverse=True)[:10]],
    }


def _range(values: list[int]) -> dict[str, int]:
    if not values:
        return {"p50": 0, "p90": 0}
    return {"p50": round(statistics.median(values)), "p90": sorted(values)[min(len(values) - 1, round(len(values) * .9))]}
