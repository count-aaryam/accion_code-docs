import json
from pathlib import Path

import pytest

from backend.core.parser import parse_repository
from backend.wiki import planner

FIXTURE = Path(__file__).parent / "fixtures" / "wiki_fixture"


def graph_for(root: Path, project_id: str):
    parsed = parse_repository(root)
    nodes = [{"id": f"{project_id}:{entity.kind.value}:{entity.file_path if entity.kind.value == 'File' else entity.file_path + '::' + (entity.qualified_name or entity.name)}", "kind": entity.kind.value, "name": entity.name, "file_path": entity.file_path, "start_line": entity.start_line, "content_hash": entity.content_hash} for entity in parsed.entities]
    id_map = {entity.id: node["id"] for entity, node in zip(parsed.entities, nodes)}
    edges = [{"source": id_map[edge.source_id], "target": id_map.get(edge.target_id), "kind": edge.kind.value, "resolution": edge.resolution.value if edge.resolution else None} for edge in parsed.relationships if edge.target_id and edge.target_id in id_map]
    return {"nodes": nodes, "edges": edges}


class FakeClient:
    def __init__(self, graph):
        self.graph = graph
        self.plan = None
        self.writes = []

    def wiki_input(self, project_id):
        return self.graph

    def latest_wiki_plan(self, project_id):
        return self.plan

    def write_wiki_plan(self, project_id, result):
        self.plan = result
        self.writes.append(result)


@pytest.fixture
def fake(monkeypatch):
    project_id = "wiki_fixture"
    client = FakeClient(graph_for(FIXTURE, project_id))
    monkeypatch.setattr(planner, "neo4j_client", client)
    monkeypatch.setattr(planner.settings, "repo_storage_path", str(FIXTURE.parent))
    return client, project_id, FIXTURE


def test_fixture_matches_expected_and_is_deterministic(fake):
    client, project_id, _ = fake
    first = planner.plan_project(project_id)
    second = planner.plan_project(project_id)
    expected = json.loads((FIXTURE / "expected-plan.json").read_text(encoding="utf-8"))
    assert _projection(first) == expected
    assert _stable(first) == _stable(second)


def test_components_cover_each_nonexcluded_file_and_are_bounded(fake):
    client, project_id, _ = fake
    result = planner.plan_project(project_id)
    excluded = set(result["diagnostics"]["excluded_files"])
    files = [node["id"] for node in client.graph["nodes"] if node["kind"] == "File" and "tests/" not in node["file_path"]]
    members = [file_id for component in result["components"] for file_id in component["file_ids"]]
    assert sorted(files) == sorted(members)
    assert len(members) == len(set(members))
    assert len(result["components"]) <= 12
    assert all(component["file_count"] for component in result["components"])
    assert excluded == {"test"}


def test_ids_stable_and_only_affected_page_changes(fake):
    client, project_id, repo = fake
    first = planner.plan_project(project_id)
    for node in client.graph["nodes"]:
        if node["file_path"] == "app/services/user_service.py":
            node["content_hash"] = "changed"
    second = planner.plan_project(project_id)
    assert {item["id"] for item in first["components"]} == {item["id"] for item in second["components"]}
    assert set(second["diff"]["changed"]) <= {"overview", "getting-started", "data-models", "api-reference"}


def test_fallback_and_unresolved_edges_are_ignored(monkeypatch):
    client = FakeClient({"nodes": [{"id": "p:File:a.py", "kind": "File", "name": "a.py", "file_path": "a.py", "start_line": 1, "content_hash": "x"}], "edges": [{"source": "p:External:x", "target": "p:File:a.py", "kind": "CALLS", "resolution": "UNRESOLVED"}]})
    monkeypatch.setattr(planner, "neo4j_client", client); monkeypatch.setattr(planner.settings, "repo_storage_path", str(FIXTURE.parent))
    result = planner.plan_project("tiny")
    assert result["components"][0]["method"] == "directory_fallback"
    assert result["diagnostics"]["internal_edge_weight_percent"] == 0


def test_entry_points_have_evidence_or_not_detected(fake):
    result = planner.plan_project(fake[1])
    assert sum(entry["kind"] == "route" for entry in result["entry_points"]) == 4
    assert any(entry["kind"] == "app" and entry["evidence"] for entry in result["entry_points"])
    assert all(entry["evidence"] for entry in result["entry_points"])


def test_zero_llm_calls_and_isolation(monkeypatch, fake):
    class NoLLM:
        def __init__(self, *args, **kwargs): raise AssertionError("LLM must not be called")
    monkeypatch.setattr("backend.wiki.planner.GeminiProvider", NoLLM, raising=False)
    result = planner.plan_project(fake[1])
    assert result["project_id"] == "wiki_fixture"


def test_include_tests_option(monkeypatch, fake):
    original = planner.load_config
    config = original()
    config["include_tests"] = True
    monkeypatch.setattr(planner, "load_config", lambda: config)
    result = planner.plan_project(fake[1])
    assert result["diagnostics"]["excluded_files"] == {}
    assert any("tests/test_rides.py" in file_id for component in result["components"] for file_id in component["file_ids"])


@pytest.mark.parametrize("name", ["animals", "billing", "edge_cases", "injection_test", "config_secrets"])
def test_named_tiny_repositories_use_directory_fallback(monkeypatch, name):
    project_id = f"tiny-{name}"
    client = FakeClient({"nodes": [{"id": f"{project_id}:File:src/main.py", "kind": "File", "name": "main.py", "file_path": "src/main.py", "start_line": 1, "content_hash": name}], "edges": []})
    monkeypatch.setattr(planner, "neo4j_client", client)
    monkeypatch.setattr(planner.settings, "repo_storage_path", str(FIXTURE.parent))
    assert planner.plan_project(project_id)["components"][0]["method"] == "directory_fallback"


def test_project_isolation(fake):
    first = planner.plan_project("project-a")
    second = planner.plan_project("project-b")
    assert first["project_id"] == "project-a"
    assert second["project_id"] == "project-b"


def test_project_delete_removes_planner_labels():
    class Result:
        def consume(self):
            return None
    class Session:
        def __init__(self): self.queries = []
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def run(self, query, **kwargs): self.queries.append(query); return Result()
    class Driver:
        def __init__(self): self.session_obj = Session()
        def session(self): return self.session_obj
    from backend.db.graph import Neo4jClient
    client = Neo4jClient(); client._driver = Driver()
    client.delete_project("project-a")
    queries = " ".join(client._driver.session_obj.queries)
    assert all(label in queries for label in ("WikiPlan", "WikiComponent", "EntryPoint", "WikiPage"))


def _stable(result):
    return {key: value for key, value in result.items() if key not in {"project_id", "diff"}}


def _projection(result):
    return {"components": [{"name": item["name"], "file_count": item["file_count"], "method": item["method"]} for item in result["components"]], "entry_points": [{"kind": item["kind"], "file": item["file"], "route_method": item.get("route_method"), "route_path": item.get("route_path"), "evidence": item["evidence"]} for item in result["entry_points"]], "pages": [{"slug": page["slug"], "type": page["type"], "status": page["status"]} for page in result["pages"]], "excluded_files": result["diagnostics"]["excluded_files"]}
