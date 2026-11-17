"""Neo4j persistence for planner-owned nodes and relationships."""

from backend.db.graph import neo4j_client


def write_plan(project_id: str, result: dict) -> None:
    neo4j_client.write_wiki_plan(project_id, result)
