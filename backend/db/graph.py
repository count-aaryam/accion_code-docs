import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path

from neo4j import Driver, GraphDatabase
from backend.core.models import EntityKind, ParseResult
from backend.settings import settings

from backend.settings import settings


class Neo4jClient:
    def __init__(self) -> None:
        self._driver: Driver | None = None

    def connect(self) -> None:
        if self._driver is None:
            self._driver = GraphDatabase.driver(
                settings.neo4j_uri,
                auth=(settings.neo4j_user, settings.neo4j_password),
            )

    def verify_connectivity(self) -> None:
        self.connect()
        assert self._driver is not None
        self._driver.verify_connectivity()

    def close(self) -> None:
        if self._driver is not None:
            self._driver.close()
            self._driver = None

    def create_project(self, project: dict) -> None:
        self.connect()
        assert self._driver is not None
        with self._driver.session() as session:
            session.run(
                "CREATE (p:Project $properties) SET p.created_at = datetime()",
                properties=project,
            ).consume()

    def update_project(self, project_id: str, **fields: str | None) -> None:
        self.connect()
        assert self._driver is not None
        assignments = ", ".join(f"p.{key} = ${key}" for key in fields)
        with self._driver.session() as session:
            session.run(f"MATCH (p:Project {{id: $project_id}}) SET {assignments}", project_id=project_id, **fields).consume()

    def list_projects(self) -> list[dict]:
        self.connect()
        assert self._driver is not None
        with self._driver.session() as session:
            return [dict(record["p"]) for record in session.run("MATCH (p:Project) RETURN p")]

    def get_project(self, project_id: str) -> dict | None:
        self.connect()
        assert self._driver is not None
        with self._driver.session() as session:
            record = session.run("MATCH (p:Project {id: $project_id}) RETURN p", project_id=project_id).single()
            return dict(record["p"]) if record else None

    def write_parse_result(self, project_id: str, result: ParseResult, repo_path: str | Path | None = None, commit_sha: str | None = None, commit_parsed_at: str | None = None, branch: str | None = None) -> tuple[int, int]:
        """Replace this project's code graph using batched UNWIND/MERGE writes."""
        self.connect()
        assert self._driver is not None
        node_by_parser_id: dict[str, str] = {}
        nodes: list[dict] = []
        for entity in result.entities:
            graph_id = _graph_entity_id(project_id, entity)
            node_by_parser_id[entity.id] = graph_id
            node = {
                "id": graph_id, "project_id": project_id, "kind": entity.kind.value,
                "name": entity.name, "file_path": entity.file_path,
                "qualified_name": entity.qualified_name, "start_line": entity.start_line,
                "end_line": entity.end_line, "content_hash": entity.content_hash,
                "source_type": "parser", "commit_sha": commit_sha,
                "commit_parsed_at": commit_parsed_at,
            }
            node.update(_source_properties(repo_path, entity))
            if entity.kind == EntityKind.FILE and repo_path:
                node["blob_sha"] = _git_blob_sha(Path(repo_path), entity.file_path)
            nodes.append(node)
        edges: list[dict] = []
        placeholder_nodes: dict[str, dict] = {}
        for relationship in result.relationships:
            source_id = node_by_parser_id.get(relationship.source_id)
            if not source_id:
                continue
            target_id = node_by_parser_id.get(relationship.target_id or "")
            if not target_id:
                target_label = relationship.target_name or "unknown"
                target_id = f"{project_id}:Symbol:{relationship.kind.value}:{target_label}"
                placeholder_nodes.setdefault(target_id, {
                    "id": target_id, "project_id": project_id,
                    "kind": "ExternalSymbol" if relationship.resolution and relationship.resolution.value == "EXTERNAL" else "UnresolvedSymbol",
                    "name": target_label, "file_path": "", "qualified_name": target_label,
                    "start_line": 0, "end_line": 0, "content_hash": "", "source_type": "parser",
                })
            edges.append({
                "source_id": source_id, "target_id": target_id,
                "project_id": project_id, "kind": relationship.kind.value,
                "resolution": relationship.resolution.value if relationship.resolution else None,
                "line": relationship.line, "module": relationship.module,
                "names": list(relationship.names), "source": "parser", "confidence": 1.0,
            })
        nodes.extend(placeholder_nodes.values())
        with self._driver.session() as session:
            session.run(
                "CREATE CONSTRAINT code_entity_id_unique IF NOT EXISTS FOR (n:CodeEntity) REQUIRE n.id IS UNIQUE"
            ).consume()
            session.run(
                "UNWIND $nodes AS row MERGE (n:CodeEntity {id: row.id}) SET n += row",
                nodes=nodes,
            ).consume()
            session.run(
                """
                UNWIND $edges AS row
                MATCH (source:CodeEntity {id: row.source_id, project_id: row.project_id})
                MATCH (target:CodeEntity {id: row.target_id, project_id: row.project_id})
                MERGE (source)-[r:CODE_RELATIONSHIP {
                    project_id: row.project_id, kind: row.kind,
                    source_id: row.source_id, target_id: row.target_id,
                    line_key: coalesce(row.line, -1)
                }]->(target)
                SET r += row
                """,
                edges=edges,
            ).consume()
        return len(nodes), len(edges)

    def project_stats(self, project_id: str) -> dict:
        self.connect()
        assert self._driver is not None
        with self._driver.session() as session:
            node_rows = session.run(
                "MATCH (n:CodeEntity {project_id: $project_id}) RETURN n.kind AS type, count(n) AS count",
                project_id=project_id,
            )
            edge_rows = session.run(
                "MATCH (:CodeEntity {project_id: $project_id})-[r:CODE_RELATIONSHIP {project_id: $project_id}]->(:CodeEntity {project_id: $project_id}) RETURN r.kind AS type, count(r) AS count",
                project_id=project_id,
            )
            source_rows = session.run(
                """
                MATCH (n:CodeEntity {project_id: $project_id})
                RETURN count(CASE WHEN n.source IS NOT NULL THEN 1 END) AS with_source,
                       count(CASE WHEN n.source_omitted = true THEN 1 END) AS omitted,
                       count(CASE WHEN n.source_truncated = true THEN 1 END) AS truncated,
                       count(CASE WHEN n.source_redacted = true THEN 1 END) AS redacted
                """,
                project_id=project_id,
            ).single()
            return {
                "project_id": project_id,
                "nodes": {row["type"]: row["count"] for row in node_rows},
                "edges": {row["type"]: row["count"] for row in edge_rows},
                "source": dict(source_rows) if source_rows else {"with_source": 0, "omitted": 0, "truncated": 0, "redacted": 0},
            }

    def get_node_source(self, project_id: str, node_id: str) -> dict | None:
        self.connect()
        assert self._driver is not None
        with self._driver.session() as session:
            record = session.run(
                "MATCH (n:CodeEntity {project_id: $project_id, id: $node_id}) RETURN n.id AS id, n.kind AS kind, n.name AS name, n.file_path AS file_path, n.source AS source, n.source_omitted AS source_omitted, n.source_truncated AS source_truncated, n.source_redacted AS source_redacted",
                project_id=project_id, node_id=node_id,
            ).single()
            return dict(record) if record else None

    def delete_project(self, project_id: str) -> None:
        self.connect()
        assert self._driver is not None
        with self._driver.session() as session:
            session.run(
                "MATCH (n:CodeEntity {project_id: $project_id}) DETACH DELETE n",
                project_id=project_id,
            ).consume()
            session.run("MATCH (p:Project {id: $project_id}) DELETE p", project_id=project_id).consume()

    def enrich_call_budget(self) -> int:
        return settings.enrich_max_calls_per_run

    def enrichment_targets(self, project_id: str, kind: str) -> list[dict]:
        self.connect()
        assert self._driver is not None
        with self._driver.session() as session:
            if kind == "Project":
                query = """
                MATCH (n:CodeEntity {project_id: $project_id, kind: 'File'})
                OPTIONAL MATCH (n)-[:HAS_SUMMARY {project_id: $project_id}]->(summary:Summary {status: 'complete'})
                RETURN $project_id AS id, 'Project' AS kind, $project_id AS name,
                       collect(summary.text) AS children,
                       null AS input_hash
                """
            else:
                query = """
                MATCH (n:CodeEntity {project_id: $project_id, kind: $kind})
                OPTIONAL MATCH (n)-[:CODE_RELATIONSHIP {project_id: $project_id, kind: 'CONTAINS'}]->(child:CodeEntity)
                OPTIONAL MATCH (child)-[:HAS_SUMMARY {project_id: $project_id}]->(child_summary:Summary {status: 'complete'})
                OPTIONAL MATCH (n)-[:HAS_SUMMARY {project_id: $project_id}]->(own_summary:Summary {status: 'complete'})
                RETURN n.id AS id, n.kind AS kind, n.name AS name, n.file_path AS file_path,
                       n.content_hash AS content_hash, n.start_line AS start_line, n.end_line AS end_line,
                       own_summary.input_hash AS input_hash, own_summary.status AS summary_status,
                       collect(child_summary.text) AS children
                ORDER BY size([(n)<-[:CODE_RELATIONSHIP {kind: 'CALLS'}]-() | 1]) DESC
                """
            return [dict(record) for record in session.run(query, project_id=project_id, kind=kind)]

    def get_enrichment_target(self, node_id: str, project_id: str) -> dict | None:
        self.connect()
        assert self._driver is not None
        with self._driver.session() as session:
            record = session.run(
                "MATCH (n:CodeEntity {id: $node_id, project_id: $project_id}) RETURN n.id AS id, n.kind AS kind, n.name AS name, n.file_path AS file_path, n.start_line AS start_line, n.end_line AS end_line",
                node_id=node_id, project_id=project_id,
            ).single()
            return dict(record) if record else None

    def write_summary(self, project_id: str, target_id: str, text: str, model_id: str, actual_model: str, prompt_version: str, input_hash: str, status: str = "complete", failure_reason: str | None = None) -> None:
        self.connect()
        assert self._driver is not None
        summary_id = f"{project_id}:Summary:{target_id}:{prompt_version}"
        with self._driver.session() as session:
            session.run(
                """
                MATCH (target)
                WHERE target.id = $target_id AND (target.project_id = $project_id OR target:Project)
                OPTIONAL MATCH (target)-[old_rel:HAS_SUMMARY {project_id: $project_id}]->(old_summary:Summary)
                DELETE old_rel, old_summary
                WITH target
                MERGE (summary:Summary {id: $summary_id})
                SET summary += {project_id: $project_id, text: $text, model: $model,
                    model_id: $model_id, status: $status, failure_reason: $failure_reason,
                    prompt_version: $prompt_version, generated_at: datetime(),
                    input_hash: $input_hash, confidence: 1.0, stale: false, source: 'llm'}
                MERGE (target)-[:HAS_SUMMARY {project_id: $project_id, source: 'llm'}]->(summary)
                """,
                project_id=project_id, target_id=target_id, summary_id=summary_id,
                text=text, model=actual_model, model_id=model_id, status=status, failure_reason=failure_reason,
                prompt_version=prompt_version, input_hash=input_hash,
            ).consume()

    def fresh_summaries(self, project_id: str) -> list[dict]:
        self.connect()
        assert self._driver is not None
        with self._driver.session() as session:
            result = session.run(
                """
                MATCH (target)
                WHERE (target:CodeEntity AND target.project_id = $project_id) OR (target:Project AND target.id = $project_id)
                      -[:HAS_SUMMARY {project_id: $project_id}]->
                      (summary:Summary {project_id: $project_id, status: 'complete'})
                RETURN target.id AS target_id, target.kind AS target_kind,
                       target.name AS target_name, summary.id AS summary_id,
                       summary.text AS text, summary.model_id AS model_id,
                       summary.model AS model, summary.prompt_version AS prompt_version,
                       summary.input_hash AS input_hash, summary.generated_at AS generated_at,
                       summary.confidence AS confidence
                """,
                project_id=project_id,
            )
            return [dict(record) for record in result]

    def graph_data(self, project_id: str, focus: str | None = None, depth: int = 1, types: list[str] | None = None, limit: int = 500) -> dict:
        self.connect()
        assert self._driver is not None
        depth = max(0, min(depth, 5))
        type_filter = [item for item in (types or []) if item]
        with self._driver.session() as session:
            if focus:
                query = f"""
                MATCH (focus:CodeEntity {{id: $focus, project_id: $project_id}})
                MATCH p=(focus)-[:CODE_RELATIONSHIP*0..{depth}]-(n:CodeEntity {{project_id: $project_id}})
                WHERE ($types_empty OR n.kind IN $types)
                WITH collect(DISTINCT n)[0..$limit] AS selected
                UNWIND selected AS n
                OPTIONAL MATCH (n)-[:HAS_SUMMARY {{project_id: $project_id}}]->(s:Summary {{status: 'complete'}})
                RETURN collect(DISTINCT {{id: n.id, kind: n.kind, name: n.name, file_path: n.file_path,
                    start_line: n.start_line, end_line: n.end_line, summary: s.text}}) AS nodes
                """
                node_record = session.run(query, focus=focus, project_id=project_id, types=type_filter, types_empty=not type_filter, limit=limit).single()
            else:
                query = """
                MATCH (n:CodeEntity {project_id: $project_id})
                WHERE ($types_empty OR n.kind IN $types)
                WITH collect(n)[0..$limit] AS selected
                UNWIND selected AS n
                OPTIONAL MATCH (n)-[:HAS_SUMMARY {project_id: $project_id}]->(s:Summary {status: 'complete'})
                RETURN collect(DISTINCT {id: n.id, kind: n.kind, name: n.name, file_path: n.file_path,
                    start_line: n.start_line, end_line: n.end_line, summary: s.text}) AS nodes
                """
                node_record = session.run(query, project_id=project_id, types=type_filter, types_empty=not type_filter, limit=limit).single()
            nodes = node_record["nodes"] if node_record else []
            node_ids = [node["id"] for node in nodes]
            edge_records = session.run(
                """
                MATCH (source:CodeEntity {project_id: $project_id})-[r:CODE_RELATIONSHIP {project_id: $project_id}]->(target:CodeEntity {project_id: $project_id})
                WHERE source.id IN $node_ids AND target.id IN $node_ids
                RETURN source.id AS source, target.id AS target, r.kind AS kind,
                       r.resolution AS resolution, r.source AS source_type, r.confidence AS confidence
                """,
                project_id=project_id, node_ids=node_ids,
            )
            return {"nodes": nodes, "edges": [dict(record) for record in edge_records]}

    def update_enrichment_state(self, project_id: str, state: dict) -> None:
        self.update_project(project_id, enrichment_status=state.get("status"), enrichment_nodes_done=state.get("nodes_done", 0), enrichment_nodes_total=state.get("nodes_total", 0), enrichment_tokens_used=state.get("tokens_used", 0), enrichment_paused=state.get("paused", False), enrichment_resumable=state.get("resumable", False))


def _graph_entity_id(project_id: str, entity) -> str:
    if entity.kind == EntityKind.FILE:
        path_or_name = entity.file_path
    else:
        path_or_name = f"{entity.file_path}::{entity.qualified_name or entity.name}"
    return f"{project_id}:{entity.kind.value}:{path_or_name}"


_SECRET_PATH = re.compile(r"(^|/)(\.env|.*\.pem$|.*\.key$|.*credentials?.*|.*secrets?.*)(/|$)", re.I)
_SECRET_TEXT = [
    (re.compile(r"(?i)(api[_-]?key|token|password|secret)\s*=\s*(['\"]).*?\2"), r"\1 = \"[REDACTED]\""),
    (re.compile(r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----", re.S), "[REDACTED PRIVATE KEY]"),
]
_SOURCE_LIMIT = 100 * 1024


def _source_properties(repo_path: str | Path | None, entity) -> dict:
    if not repo_path or entity.kind not in {EntityKind.FILE, EntityKind.CLASS, EntityKind.FUNCTION, EntityKind.METHOD}:
        return {}
    relative = entity.file_path.replace("\\", "/")
    if _SECRET_PATH.search(relative):
        return {"source_omitted": True}
    path = (Path(repo_path).resolve() / relative).resolve()
    root = Path(repo_path).resolve()
    if root not in path.parents or not path.is_file():
        return {"source_omitted": True}
    try:
        raw = path.read_bytes()
        if entity.kind == EntityKind.FILE:
            code = raw
            if len(code) > _SOURCE_LIMIT:
                return {"source_omitted": True}
        else:
            lines = raw.decode("utf-8").splitlines(keepends=True)
            code = "".join(lines[entity.start_line - 1:entity.end_line]).encode("utf-8")
        text = code.decode("utf-8")
        redacted = False
        for pattern, replacement in _SECRET_TEXT:
            text, count = pattern.subn(replacement, text)
            redacted = redacted or count > 0
        encoded = text.encode("utf-8")
        truncated = len(encoded) > _SOURCE_LIMIT
        if truncated:
            encoded = encoded[:_SOURCE_LIMIT]
            text = encoded.decode("utf-8", errors="ignore")
        return {"source": text, "source_redacted": redacted, "source_truncated": truncated}
    except (OSError, UnicodeError):
        return {"source_omitted": True}


def _git_blob_sha(repo_path: Path, relative_path: str) -> str | None:
    import subprocess
    try:
        result = subprocess.run(["git", "rev-parse", f"HEAD:{relative_path.replace(chr(92), '/') }"], cwd=repo_path, capture_output=True, text=True, check=True)
        return result.stdout.strip() or None
    except (OSError, subprocess.CalledProcessError):
        return None


neo4j_client = Neo4jClient()
