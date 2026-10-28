from contextlib import asynccontextmanager
import os
from pathlib import Path
import subprocess
import shutil
from datetime import datetime, timezone
import logging
from uuid import uuid4
from typing import Any
import yaml
import httpx

from fastapi import BackgroundTasks, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, HttpUrl, field_validator

from backend.db.graph import neo4j_client
from backend.core.parser import parse_repository
from backend.core.storage import save_parse_result
from backend.settings import settings

logger = logging.getLogger(__name__)
from backend.llm.gemini import GeminiProvider
from backend.enrichment.job import debug_enrichment, run_enrichment, status_for


def available_models() -> list[dict[str, Any]]:
    with (Path(__file__).parent / "llm" / "models.yaml").open(encoding="utf-8") as file:
        return yaml.safe_load(file) or []


class GenerateRequest(BaseModel):
    model: str
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]] | None = None


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    github_url: HttpUrl
    branch: str = Field(default="main", min_length=1, max_length=200)
    access_token: str | None = Field(default=None, min_length=1)

    @field_validator("github_url")
    @classmethod
    def validate_github_url(cls, value: HttpUrl) -> HttpUrl:
        if value.scheme != "https" or value.host not in {"github.com", "www.github.com"}:
            raise ValueError("github_url must be an HTTPS GitHub URL")
        return value


def clone_project(project: ProjectCreate, project_id: str) -> None:
    storage_root = Path(settings.repo_storage_path).resolve()
    destination = storage_root / project_id
    storage_root.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    if project.access_token:
        env["GIT_CONFIG_COUNT"] = "1"
        env["GIT_CONFIG_KEY_0"] = "http.extraheader"
        env["GIT_CONFIG_VALUE_0"] = f"Authorization: Bearer {project.access_token}"
    completed = subprocess.run(
        ["git", "clone", "--depth", "1", "--branch", project.branch, str(project.github_url), str(destination)],
        capture_output=True, text=True, env=env,
    )
    if completed.returncode:
        detail = (completed.stderr or completed.stdout or "unknown git error").strip()
        if project.access_token:
            detail = detail.replace(project.access_token, "[REDACTED]")
        raise RuntimeError(detail[:500])


def git_metadata(repo_path: Path) -> tuple[str | None, str | None]:
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo_path, capture_output=True, text=True, check=True).stdout.strip() or None
        return commit, datetime.now(timezone.utc).isoformat()
    except (OSError, subprocess.CalledProcessError):
        logger.warning("Git metadata unavailable for repository")
        return None, None


def public_project(project: dict) -> dict:
    project.pop("created_at", None)
    return project


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    neo4j_client.close()


app = FastAPI(title="Neo4j FastAPI Starter", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post("/projects", status_code=status.HTTP_201_CREATED)
def create_project(project: ProjectCreate) -> dict:
    project_id = str(uuid4())
    record = {"id": project_id, "name": project.name, "github_url": str(project.github_url), "branch": project.branch, "status": "created", "failure_reason": None}
    neo4j_client.create_project(record)
    neo4j_client.update_project(project_id, status="cloning", failure_reason=None)
    try:
        clone_project(project, project_id)
    except (OSError, RuntimeError) as exc:
        reason = str(exc) if isinstance(exc, RuntimeError) else "Git is not installed or could not be started"
        neo4j_client.update_project(project_id, status="failed", failure_reason=reason)
    else:
        try:
            repository_path = Path(settings.repo_storage_path).resolve() / project_id
            neo4j_client.update_project(project_id, status="parsing", failure_reason=None)
            parsed = parse_repository(repository_path)
            save_parse_result(parsed, repository_path / "parsed-output.json")
            commit_sha, commit_parsed_at = git_metadata(repository_path)
            if commit_sha is None:
                neo4j_client.update_project(project_id, failure_reason="Git metadata unavailable; parsed without commit metadata")
            neo4j_client.update_project(project_id, commit_sha=commit_sha, commit_parsed_at=commit_parsed_at, branch=project.branch)
            node_count, edge_count = neo4j_client.write_parse_result(project_id, parsed, repository_path, commit_sha, commit_parsed_at, project.branch)
        except (OSError, RuntimeError) as exc:
            neo4j_client.update_project(project_id, status="failed", failure_reason=str(exc)[:500])
        else:
            neo4j_client.update_project(
                project_id, status="ready", failure_reason=None,
                node_count=node_count, edge_count=edge_count,
            )
    return public_project(neo4j_client.get_project(project_id) or record)


@app.get("/projects")
def list_projects() -> list[dict]:
    return [public_project(project) for project in neo4j_client.list_projects()]


@app.get("/projects/{project_id}")
def get_project(project_id: str) -> dict:
    project = neo4j_client.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return public_project(project)


@app.get("/projects/{project_id}/stats")
def project_stats(project_id: str) -> dict:
    if neo4j_client.get_project(project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return neo4j_client.project_stats(project_id)


@app.get("/projects/{project_id}/graph")
def project_graph(project_id: str, focus: str | None = None, depth: int = 1, types: str | None = None) -> dict:
    if neo4j_client.get_project(project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")
    selected_types = [item.strip() for item in types.split(",")] if types else None
    return neo4j_client.graph_data(project_id, focus=focus, depth=depth, types=selected_types)


@app.get("/models")
def list_models() -> list[dict[str, Any]]:
    return available_models()


@app.post("/llm/generate")
async def generate(request: GenerateRequest) -> dict[str, Any]:
    model = next((item for item in available_models() if item["id"] == request.model), None)
    if model is None:
        raise HTTPException(status_code=400, detail="Unknown model")
    if not model.get("available"):
        raise HTTPException(status_code=400, detail="Selected model is not available")
    if model.get("provider") != "gemini":
        raise HTTPException(status_code=400, detail="Selected model provider is not configured")
    if not settings.gemini_api_key:
        raise HTTPException(status_code=503, detail="GEMINI_API_KEY is not configured")
    try:
        response = await GeminiProvider().generate(request.messages, request.tools)
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text[:500] or "Gemini request failed"
        raise HTTPException(status_code=502, detail=detail) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Could not reach Gemini ({type(exc).__name__}): {str(exc)[:300]}",
        ) from exc
    return {
        "text": response.text,
        "tool_calls": [{"id": call.id, "name": call.name, "arguments": call.arguments} for call in response.tool_calls],
        "usage": {
            "prompt_tokens": response.usage.prompt_tokens,
            "completion_tokens": response.usage.completion_tokens,
            "total_tokens": response.usage.total_tokens,
        },
    }


@app.delete("/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(project_id: str) -> None:
    if neo4j_client.get_project(project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")
    neo4j_client.delete_project(project_id)
    shutil.rmtree(Path(settings.repo_storage_path).resolve() / project_id, ignore_errors=True)


@app.post("/projects/{project_id}/backfill", status_code=status.HTTP_202_ACCEPTED)
def backfill_project(project_id: str, background_tasks: BackgroundTasks) -> dict:
    project = neo4j_client.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    repository_path = Path(settings.repo_storage_path).resolve() / project_id
    if not repository_path.is_dir():
        raise HTTPException(status_code=400, detail="Cloned repository is missing")
    def backfill() -> None:
        parsed = parse_repository(repository_path)
        commit_sha, commit_parsed_at = git_metadata(repository_path)
        neo4j_client.write_parse_result(project_id, parsed, repository_path, commit_sha, commit_parsed_at, project.get("branch"))
    background_tasks.add_task(backfill)
    return {"project_id": project_id, "status": "queued"}


@app.post("/projects/{project_id}/enrich", status_code=status.HTTP_202_ACCEPTED)
async def enrich_project(project_id: str, background_tasks: BackgroundTasks, model_id: str = "gemini-free") -> dict:
    if neo4j_client.get_project(project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")
    model = next((item for item in available_models() if item["id"] == model_id), None)
    if not model or not model.get("available"):
        raise HTTPException(status_code=400, detail="Selected model is not available")
    if status_for(project_id).get("status") == "running":
        raise HTTPException(status_code=409, detail="Enrichment is already running")
    background_tasks.add_task(run_enrichment, project_id, model_id)
    return {"project_id": project_id, "status": "queued", "model": model_id}


@app.get("/projects/{project_id}/enrich/status")
def enrichment_status(project_id: str) -> dict:
    if neo4j_client.get_project(project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return status_for(project_id)


@app.get("/projects/{project_id}/nodes/{node_id:path}/source")
def node_source(project_id: str, node_id: str) -> dict:
    if neo4j_client.get_project(project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")
    source = neo4j_client.get_node_source(project_id, node_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Node not found")
    return source


@app.get("/projects/{project_id}/summaries")
def project_summaries(project_id: str) -> list[dict]:
    if neo4j_client.get_project(project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return neo4j_client.fresh_summaries(project_id)


@app.post("/projects/{project_id}/enrich/debug/{node_id:path}")
def enrichment_debug(project_id: str, node_id: str) -> dict:
    if neo4j_client.get_project(project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")
    try:
        return debug_enrichment(project_id, node_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/health")
def health() -> JSONResponse:
    try:
        neo4j_client.verify_connectivity()
        return JSONResponse({"status": "ok", "neo4j": "connected"})
    except Exception as exc:
        return JSONResponse(
            status_code=503,
            content={"status": "error", "neo4j": "unavailable", "detail": str(exc)},
        )
