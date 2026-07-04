# Freedom250 Backend

FastAPI backend for Freedom250.

## Setup

Uses [uv](https://docs.astral.sh/uv/) for dependency management.

```bash
cd backend
uv sync                       # create venv + install deps
cp .env.example .env          # configure environment
```

## Run

```bash
uv run uvicorn app.main:app --reload
```

- API root: http://localhost:8000
- Health check: http://localhost:8000/health
- Interactive docs: http://localhost:8000/docs

## Test

```bash
uv run pytest
```

## Structure

```
backend/
├── app/
│   ├── main.py         # FastAPI app + middleware
│   ├── config.py       # Settings (env-driven)
│   └── routers/        # Route modules
│       └── health.py
├── tests/
└── pyproject.toml
```
