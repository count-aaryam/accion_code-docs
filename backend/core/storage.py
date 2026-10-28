import json
from dataclasses import asdict
from pathlib import Path

from backend.core.models import ParseResult


def save_parse_result(result: ParseResult, output_path: str | Path) -> Path:
    """Save parser output as portable, human-readable JSON."""
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(result)
    for entity in payload["entities"]:
        entity["kind"] = entity["kind"].value
    for relationship in payload["relationships"]:
        relationship["kind"] = relationship["kind"].value
    destination.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return destination
