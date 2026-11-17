from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import PurePosixPath
from pathlib import Path
from typing import Any

import networkx as nx
import yaml

from backend.db.graph import neo4j_client
from backend.settings import settings

CONFIG_PATH = Path(__file__).with_name("wiki.yaml")


def load_config() -> dict[str, Any]:
    with CONFIG_PATH.open(encoding="utf-8") as stream:
        return yaml.safe_load(stream) or {}


def plan_project(project_id: str) -> dict[str, Any]:
    config = load_config()
    graph = neo4j_client.wiki_input(project_id)
    files = {row["id"]: row for row in graph["nodes"] if row["kind"] == "File"}
    node_by_id = {row["id"]: row for row in graph["nodes"]}
    file_by_path = {row["file_path"]: row["id"] for row in files.values()}
    excluded: Counter[str] = Counter()
    included: dict[str, dict] = {}
    for file_id, row in files.items():
        reason = _exclude_reason(row.get("file_path", ""), config)
        if reason:
            excluded[reason] += 1
        else:
            included[file_id] = row
    edges = []
    for edge in graph["edges"]:
        source = node_by_id.get(edge["source"]); target = node_by_id.get(edge["target"])
        if not source or not target or edge.get("resolution") in {"UNRESOLVED", None}:
            continue
        source_file, target_file = file_by_path.get(source.get("file_path")), file_by_path.get(target.get("file_path"))
        if source_file in included and target_file in included and source_file != target_file:
            edges.append({**edge, "source": source_file, "target": target_file})
    components, method, modularity = _components(included, edges, config)
    old = neo4j_client.latest_wiki_plan(project_id)
    old_components = old.get("components", []) if old else []
    component_rows = _component_rows(components, included, edges, graph["nodes"], method, config)
    _reuse_component_ids(component_rows, old_components)
    entry_points = _entry_points(project_id, graph["nodes"], included, config)
    pages = _pages(project_id, component_rows, entry_points, graph["nodes"], config)
    diagnostics = _diagnostics(included, edges, component_rows, entry_points, excluded, method, modularity, graph["nodes"])
    diff = _diff(old, pages)
    result = {"project_id": project_id, "planner_version": config["planner_version"], "components": component_rows, "entry_points": entry_points, "pages": pages, "diagnostics": diagnostics, "diff": diff}
    neo4j_client.write_wiki_plan(project_id, result)
    return result


def _exclude_reason(path: str, config: dict) -> str | None:
    p = path.replace("\\", "/").lower()
    if not config.get("include_tests", False) and any(_pattern(p, item.lower()) for item in config["test_path_patterns"]):
        return "test"
    if any(_pattern(p, item.lower()) for item in config["vendor_generated_patterns"]):
        return "vendor_or_generated"
    return None


def _pattern(path: str, pattern: str) -> bool:
    return pattern.rstrip("/") in path if pattern.endswith("/") else Path(path).match(pattern) or path.endswith(pattern)


def _components(files: dict, edges: list[dict], config: dict):
    graph = nx.Graph()
    graph.add_nodes_from(files)
    weights = config["edge_weights"]
    for edge in edges:
        source, target = edge["source"], edge["target"]
        weight = weights.get(edge["kind"].lower(), 1.0)
        graph.add_edge(source, target, weight=graph.get_edge_data(source, target, {}).get("weight", 0) + weight)
    for left, left_row in files.items():
        for right, right_row in files.items():
            if left < right and PurePosixPath(left_row["file_path"]).parent == PurePosixPath(right_row["file_path"]).parent:
                graph.add_edge(left, right, weight=graph.get_edge_data(left, right, {}).get("weight", 0) + weights["same_directory"])
    if len(files) <= config["small_repo_file_threshold"] or not any(edge["kind"] != "CONTAINS" for edge in edges):
        groups = defaultdict(list)
        for file_id, row in sorted(files.items(), key=lambda pair: pair[1]["file_path"]):
            groups[str(PurePosixPath(row["file_path"]).parent)].append(file_id)
        return list(groups.values()), "directory_fallback", None
    communities = nx.community.louvain_communities(graph, weight="weight", resolution=config["louvain_resolution"], seed=config["random_seed"])
    return _post_process([sorted(group) for group in communities], graph, config), "louvain", nx.community.modularity(graph, communities, weight="weight") if graph.number_of_edges() else 0.0


def _post_process(groups, graph, config):
    while len(groups) > config["max_components"]:
        groups.sort(key=len)
        small = groups.pop(0)
        target = max(groups, key=lambda group: sum(graph.get_edge_data(a, b, {}).get("weight", 0) for a in small for b in group))
        target.extend(small)
    return [sorted(group) for group in groups if group]


def _component_rows(groups, files, edges, nodes, method, config):
    node_by_id = {node["id"]: node for node in nodes}
    owner = {file_id: index for index, group in enumerate(groups) for file_id in group}
    rows = []
    for index, group in enumerate(groups):
        paths = [files[file_id]["file_path"] for file_id in group]
        directory = str(PurePosixPath(sorted(paths)[0]).parent)
        name = _display_name(directory)
        relevant = [edge for edge in edges if owner.get(edge["source"]) == index and owner.get(edge["target"]) == index]
        total = [edge for edge in edges if owner.get(edge["source"]) == index or owner.get(edge["target"]) == index]
        members = [node for node in nodes if node["file_path"] in paths and node["kind"] != "File"]
        counts = Counter(node["kind"] for node in members)
        rows.append({"id": f"component:{index}", "display_name": None, "name": name, "directory": directory, "file_ids": sorted(group), "file_count": len(group), "class_count": counts["Class"], "function_count": counts["Function"], "method_count": counts["Method"], "key_nodes": [node["id"] for node in sorted(members, key=lambda n: (n["name"], n["id"]))[:10]], "cohesion": round(sum(edge.get("weight", 1) for edge in relevant) / max(sum(edge.get("weight", 1) for edge in total), 1), 4), "dependencies": [], "dependents": [], "method": method})
    for row in rows:
        row["id"] = f"component:{_slug(row['directory'])}"
    component_by_file = {file_id: row["id"] for row in rows for file_id in row["file_ids"]}
    for row in rows:
        dependencies = defaultdict(lambda: {"calls": 0, "imports": 0, "inherits": 0, "weight": 0.0})
        dependents = defaultdict(lambda: {"calls": 0, "imports": 0, "inherits": 0, "weight": 0.0})
        for edge in edges:
            source_component, target_component = component_by_file.get(edge["source"]), component_by_file.get(edge["target"])
            if source_component == row["id"] and target_component and target_component != row["id"]:
                kind = edge["kind"].lower(); dependencies[target_component][kind] = dependencies[target_component].get(kind, 0) + 1; dependencies[target_component]["weight"] += 1.0
            if target_component == row["id"] and source_component and source_component != row["id"]:
                kind = edge["kind"].lower(); dependents[source_component][kind] = dependents[source_component].get(kind, 0) + 1; dependents[source_component]["weight"] += 1.0
        row["dependencies"], row["dependents"] = [{"component_id": key, **value} for key, value in dependencies.items()], [{"component_id": key, **value} for key, value in dependents.items()]
    return rows


def _reuse_component_ids(rows, old):
    used = set()
    for row in rows:
        current = set(row["file_ids"])
        matches = [(len(current & set(previous.get("file_ids", []))) / max(len(current | set(previous.get("file_ids", []))), 1), previous) for previous in old if previous.get("id") not in used]
        if matches:
            score, previous = max(matches, key=lambda pair: (pair[0], pair[1].get("id", "")))
            if score >= 0.5:
                row["id"] = previous["id"]; used.add(previous["id"])


def _entry_points(project_id, nodes, files, config):
    result = []
    root = Path(settings.repo_storage_path).resolve() / project_id
    for file_id, file in files.items():
        path = file["file_path"]
        if path.endswith(".py"):
            try: text = (root / path).read_text(encoding="utf-8")
            except OSError: text = ""
            if "if __name__" in text and "__main__" in text: result.append(_entry("python_main", file_id, file, "if __name__ == '__main__'"))
            if re.search(r"\b(?:FastAPI|Flask|Starlette)\s*\(", text): result.append(_entry("app", file_id, file, "FastAPI/Flask/Starlette application creation"))
            for match in re.finditer(r"@(app|router|bp)\.(get|post|put|delete|patch|route)\(\s*['\"]([^'\"]+)", text):
                result.append(_entry("route", file_id, file, match.group(0), match.group(2).upper(), match.group(3)))
            if "argparse" in text or "click" in text or "typer" in text: result.append(_entry("cli", file_id, file, "argparse/click/typer import or usage"))
        elif path.endswith((".js", ".jsx", ".ts", ".tsx")):
            try: text = (root / path).read_text(encoding="utf-8")
            except OSError: text = ""
            if re.search(r"\b(createRoot|ReactDOM\.render)\s*\(", text): result.append(_entry("react_root", file_id, file, "createRoot or ReactDOM.render call"))
            for match in re.finditer(r"\b(app|router)\.(get|post|put|delete|patch|use)\(\s*['\"]([^'\"]+)", text): result.append(_entry("route", file_id, file, match.group(0), match.group(2).upper(), match.group(3)))
    if not result:
        result.append({"id": f"entry:{project_id}:none", "kind": "not_detected", "node_id": None, "file": None, "start_line": None, "evidence": "not detected"})
    for entry in result: entry["project_id"] = project_id; entry["source"] = "derived"
    return result


def _entry(kind, node_id, file, evidence, method=None, path=None):
    return {"id": f"entry:{node_id}:{kind}:{len(evidence)}", "kind": kind, "node_id": node_id, "file": file["file_path"], "start_line": 1, "evidence": evidence, "route_method": method, "route_path": path}


def _pages(project_id, components, entries, nodes, config):
    component_files = {file_id for component in components for file_id in component["file_ids"]}
    file_paths = {node["file_path"] for node in nodes if node["id"] in component_files}
    covered = [node["id"] for node in nodes if node["file_path"] in file_paths and node["kind"] not in {"External", "Unresolved"}]
    pages = [{"id": f"page:{project_id}:overview", "slug": "overview", "title": "Overview", "type": "overview", "parent_slug": None, "sort_order": 0, "component_ids": [c["id"] for c in components], "covered_node_ids": covered[:200], "entry_point_ids": [e["id"] for e in entries], "planned_diagrams": ["architecture"], "summary_coverage": 0.0, "input_hash": _hash(covered, config["planner_version"]), "status": "planned", "markdown": ""}]
    for index, component in enumerate(components, 1):
        pages.append({"id": f"page:{project_id}:component:{_slug(component['id'])}", "slug": _slug(component["name"]), "title": component["name"], "type": "component", "parent_slug": "overview", "sort_order": index, "component_ids": [component["id"]], "covered_node_ids": component["file_ids"][:200], "entry_point_ids": [], "planned_diagrams": ["architecture", "class", "sequence"], "summary_coverage": 0.0, "input_hash": _hash(component["file_ids"], config["planner_version"]), "status": "planned", "markdown": ""})
    if any(entry["kind"] == "route" for entry in entries):
        pages.append({"id": f"page:{project_id}:api-reference", "slug": "api-reference", "title": "API Reference", "type": "api-reference", "parent_slug": "overview", "sort_order": 100, "component_ids": [], "covered_node_ids": [], "entry_point_ids": [e["id"] for e in entries if e["kind"] == "route"], "planned_diagrams": ["architecture", "sequence"], "summary_coverage": 0.0, "input_hash": _hash("api", [e["id"] for e in entries], config["planner_version"]), "status": "planned", "markdown": ""})
    if any(node["kind"] == "Class" and ("models/" in node.get("file_path", "") or "model" in node.get("name", "").lower()) for node in nodes):
        pages.append({"id": f"page:{project_id}:data-models", "slug": "data-models", "title": "Data Models", "type": "data-models", "parent_slug": "overview", "sort_order": 101, "component_ids": [], "covered_node_ids": [node["id"] for node in nodes if node["kind"] == "Class" and "models/" in node.get("file_path", "")], "entry_point_ids": [], "planned_diagrams": ["class"], "summary_coverage": 0.0, "input_hash": _hash("models", config["planner_version"]), "status": "planned", "markdown": ""})
    pages.append({"id": f"page:{project_id}:getting-started", "slug": "getting-started", "title": "Getting Started", "type": "getting-started", "parent_slug": "overview", "sort_order": 999, "component_ids": [], "covered_node_ids": [], "entry_point_ids": [e["id"] for e in entries], "planned_diagrams": [], "summary_coverage": 0.0, "input_hash": _hash([e["id"] for e in entries], config["planner_version"]), "status": "planned", "markdown": ""})
    return pages


def _diagnostics(files, edges, components, entries, excluded, method, modularity, nodes):
    internal = sum(1 for edge in edges if any(edge["source"] in c["file_ids"] and edge["target"] in c["file_ids"] for c in components))
    return {"modularity": modularity, "component_size_distribution": [c["file_count"] for c in components], "community_assignment_percent": 100.0 if method == "louvain" else 0.0, "internal_edge_weight_percent": round(internal / max(len(edges), 1) * 100, 2), "isolated_file_count": sum(c["file_count"] == 1 for c in components), "excluded_files": dict(excluded), "entry_point_counts": dict(Counter(e["kind"] for e in entries)), "resolved_call_rate": round(sum(e["kind"] == "CALLS" and e.get("resolution") == "INTERNAL" for e in edges) / max(sum(e["kind"] == "CALLS" for e in edges), 1), 4), "warnings": (["one component holds more than 50 percent of files"] if components and max(c["file_count"] for c in components) > len(files) / 2 else [])}


def _diff(old, pages):
    old_pages = {p["slug"]: p for p in (old or {}).get("pages", [])}; new_pages = {p["slug"]: p for p in pages}
    return {"added": sorted(set(new_pages) - set(old_pages)), "removed": sorted(set(old_pages) - set(new_pages)), "changed": sorted(slug for slug in set(old_pages) & set(new_pages) if old_pages[slug].get("input_hash") != new_pages[slug].get("input_hash"))}


def _display_name(directory):
    value = PurePosixPath(directory).name or "root"
    return value.replace("_", " ").replace("-", " ").title()


def _slug(value):
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", value).strip("-") or "root"


def _hash(*parts):
    return hashlib.sha256(repr(parts).encode()).hexdigest()
