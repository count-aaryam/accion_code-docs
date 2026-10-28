from pathlib import Path

from backend.core.parser import parse_repository
from backend.core.models import EntityKind, RelationshipKind, Resolution
from backend.core.storage import save_parse_result


FIXTURE = Path(__file__).parent / "fixtures" / "animal_repo"


def test_animal_repo_entities_and_relationships():
    result = parse_repository(FIXTURE)

    assert len(result.entities) == 6
    assert {entity.kind for entity in result.entities} == {EntityKind.FILE, EntityKind.CLASS, EntityKind.FUNCTION, EntityKind.METHOD}
    assert sum(r.kind == RelationshipKind.CONTAINS for r in result.relationships) == 5
    assert sum(r.kind == RelationshipKind.INHERITS for r in result.relationships) == 1
    assert sum(r.kind == RelationshipKind.CALLS for r in result.relationships) == 1
    assert sum(r.kind == RelationshipKind.IMPORTS for r in result.relationships) == 1
    assert result.skipped_files == []


def test_syntax_errors_are_skipped(tmp_path, caplog):
    (tmp_path / "broken.py").write_text("def broken(:\n", encoding="utf-8")
    result = parse_repository(tmp_path)
    assert result.skipped_files == ["broken.py"]
    assert "Skipping broken.py" in caplog.text


def test_parse_result_can_be_saved_as_json(tmp_path):
    output = save_parse_result(parse_repository(FIXTURE), tmp_path / "parsed.json")
    data = output.read_text(encoding="utf-8")
    assert '"entities"' in data
    assert '"relationships"' in data
    assert '"skipped_files"' in data
    assert '"kind": "Class"' in data


def test_import_resolution_and_contains_metadata(tmp_path):
    (tmp_path / "main.py").write_text("import requests\nfrom .missing import Thing\n\nclass Example:\n    pass\n", encoding="utf-8")
    result = parse_repository(tmp_path)
    imports = [r for r in result.relationships if r.kind == RelationshipKind.IMPORTS]
    assert next(r for r in imports if r.module == "requests").resolution == Resolution.EXTERNAL
    assert next(r for r in imports if r.module == ".missing").resolution == Resolution.UNRESOLVED
    contains = next(r for r in result.relationships if r.kind == RelationshipKind.CONTAINS)
    assert contains.resolution is None
    assert contains.line is None


def test_bare_name_in_method_resolves_module_function():
    result = parse_repository(FIXTURE)
    call = next(r for r in result.relationships if r.kind == RelationshipKind.CALLS and r.target_name == "make_noise")
    assert call.source_id == "method:animals.py::Dog.make_noise"
    assert call.target_id == "function:animals.py::make_noise"
    assert call.resolution == Resolution.INTERNAL
    assert call.line == 11


def test_self_call_resolves_to_sibling_method(tmp_path):
    (tmp_path / "self_call.py").write_text("class Dog:\n    def make_noise(self):\n        return self.make_noise()\n", encoding="utf-8")
    result = parse_repository(tmp_path)
    call = next(r for r in result.relationships if r.kind == RelationshipKind.CALLS)
    assert call.target_id == "method:self_call.py::Dog.make_noise"
    assert call.resolution == Resolution.INTERNAL


def test_recursive_function_resolves_to_itself(tmp_path):
    (tmp_path / "recursive.py").write_text("def f():\n    return f()\n", encoding="utf-8")
    result = parse_repository(tmp_path)
    call = next(r for r in result.relationships if r.kind == RelationshipKind.CALLS)
    assert call.source_id == call.target_id == "function:recursive.py::f"
    assert call.resolution == Resolution.INTERNAL


def test_non_recursive_calls_do_not_self_resolve():
    result = parse_repository(FIXTURE)
    for call in (r for r in result.relationships if r.kind == RelationshipKind.CALLS):
        if call.source_id == call.target_id:
            assert call.target_name == "f"
