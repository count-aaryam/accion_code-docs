from pathlib import Path

from backend.core.models import EntityKind, RelationshipKind, Resolution
from backend.core.parser import parse_repository


FIXTURE = Path(__file__).parent / "fixtures" / "js_ts_fixture"


def test_js_ts_fixture_entities_and_resolution():
    result = parse_repository(FIXTURE)
    assert {e.language for e in result.entities} == {"javascript", "typescript", "tsx"}
    assert {e.kind for e in result.entities} >= {EntityKind.INTERFACE, EntityKind.TYPE_ALIAS, EntityKind.ENUM}
    assert any(e.name == "double" and e.kind == EntityKind.FUNCTION for e in result.entities)
    imports = [r for r in result.relationships if r.kind == RelationshipKind.IMPORTS]
    assert imports and all(r.resolution == Resolution.INTERNAL for r in imports)
    assert "broken.ts" in result.skipped_files
    assert result.skip_reasons["bundle.min.js"] == "generated_or_minified"


def test_js_ts_parse_is_deterministic():
    first = parse_repository(FIXTURE)
    second = parse_repository(FIXTURE)
    assert first == second

