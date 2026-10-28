# Neo4j FastAPI + React monorepo

Minimal monorepo with a FastAPI backend, an official Neo4j Python driver connection, and a React + Vite frontend.

## Prerequisites

- Python 3.14.6
- Node.js 18+
- Neo4j Desktop with a local database running

Copy `.env.example` to `.env` and set the password used by your local Neo4j database. The backend reads `NEO4J_URI`, `NEO4J_USER`, and `NEO4J_PASSWORD` from `.env`.

## Run on Windows (PowerShell)

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r backend\requirements.txt
Copy-Item .env.example .env
python -m uvicorn backend.main:app --reload --reload-dir backend
```

In a second terminal:

```powershell
cd frontend
npm install
npm run dev
```

## Run on macOS/Linux

```bash
python3.14 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
cp .env.example .env
python -m uvicorn backend.main:app --reload --reload-dir backend
```

In a second terminal:

```bash
cd frontend
npm install
npm run dev
```

The API is available at `http://localhost:8000`, the health check is `http://localhost:8000/health`, and Vite serves the frontend at `http://localhost:5173`.

All database access is contained in `backend/db/graph.py`. This project uses only the official Neo4j driver and standard Cypher-compatible connectivity; it does not use APOC or Enterprise-only features. Uvicorn reload watching is limited to `backend`, so changes inside cloned repositories do not restart the API.

## Parse and save repository output

The parser is currently independent of Neo4j. When a project is created through `POST /projects`, parsing happens automatically after cloning. The output is saved at:

```text
REPO_STORAGE_PATH/<project_id>/parsed-output.json
```

You can also run the parser manually and save its plain dataclass output as JSON with:

```powershell
python -m backend.core.parse_cli tests/fixtures/animal_repo --output artifacts/animal-repo.json
```

For a cloned project, use its storage directory instead:

```powershell
python -m backend.core.parse_cli repos/<project_id> --output artifacts/<project_id>.json
```

Run the unit tests after installing the dependencies and pytest:

```powershell
python -m pip install -r backend\requirements.txt pytest
python -m pytest
```

The tests verify the Animal/Dog fixture counts, ignored-folder behavior, syntax-error logging, and JSON persistence.

## Source and Git metadata

When a repository is parsed, code nodes store redacted source where safe, plus `commit_sha` and `commit_parsed_at`. File nodes also store `blob_sha`. Secret/config files and files over 100 KB are omitted; source is capped at 100 KB per node. Raw parser `content_hash` values remain based on unredacted source.

Source is not returned by `/graph` or `/summaries`. Fetch it explicitly with:

```text
GET /projects/{project_id}/nodes/{node_id}/source
```

Use `POST /projects/{project_id}/backfill` to re-parse and MERGE metadata onto an existing project without creating duplicate code nodes. `GET /projects/{project_id}/stats` includes source coverage counts for stored, omitted, truncated, and redacted nodes. Deleting a project removes its Neo4j graph and cloned repository directory.

## Before committing

Run `git status` and confirm that no ignored categories appear as staged or untracked files. Never commit `.env`, API keys, database credentials, cloned repositories, generated outputs, logs, or other people's code. Keep `.env.example`, dependency manifests, `models.yaml`, `README.md`, and tests safe to commit.
