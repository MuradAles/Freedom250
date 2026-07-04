# Freedom250

Full-stack app with a **React (CRA)** frontend and a **FastAPI (Python)** backend.

## Project structure

```
freedom250/
├── frontend/     # React app (Create React App)
├── backend/      # FastAPI app (managed with uv)
└── README.md
```

## Prerequisites

- **Node.js** 18+ and npm — for the frontend
- **Python** 3.11+ and [**uv**](https://docs.astral.sh/uv/) — for the backend
  - Install uv: `curl -LsSf https://astral.sh/uv/install.sh | sh`

## Backend (FastAPI)

`uv` manages the virtual environment and dependencies — there is no manual
`activate` step. Prefix commands with `uv run` to execute them inside the venv.

```bash
cd backend
uv sync                                    # create .venv + install deps
cp .env.example .env                       # first time only: configure env
uv run uvicorn app.main:app --reload       # start dev server -> http://localhost:8000
```

| Task | Command |
|------|---------|
| Install / update deps | `uv sync` |
| Run dev server | `uv run uvicorn app.main:app --reload` |
| Run tests | `uv run pytest` |
| Add a dependency | `uv add <package>` |
| Remove a dependency | `uv remove <package>` |

- API root: http://localhost:8000
- Health check: http://localhost:8000/health
- Interactive API docs (Swagger): http://localhost:8000/docs

Backend layout and details: see [`backend/README.md`](backend/README.md).

## Frontend (React)

```bash
cd frontend
npm install                # first time only
npm start                  # dev server -> http://localhost:3000
```

| Task | Command |
|------|---------|
| Install deps | `npm install` |
| Run dev server | `npm start` |
| Build for production | `npm run build` |
| Run tests | `npm test` |

The frontend calls the backend at `http://localhost:8000`. CORS on the backend
already allows `http://localhost:3000` (configurable via `CORS_ORIGINS` in
`backend/.env`).

## Running the full stack locally

Open two terminals:

```bash
# Terminal 1 — backend
cd backend && uv run uvicorn app.main:app --reload

# Terminal 2 — frontend
cd frontend && npm start
```

## Notes for AI agents / contributors

- **Backend uses `uv`, not bare `pip`.** Always run Python commands via
  `uv run ...` from inside `backend/`. Dependencies live in
  `backend/pyproject.toml`; the lockfile is `backend/uv.lock`.
- **Add backend routes** as modules under `backend/app/routers/`, then include
  the router in `backend/app/main.py`.
- **Config** is env-driven via `backend/app/config.py` (pydantic-settings);
  add new settings there and document them in `backend/.env.example`.
- **Tests** live in `backend/tests/` (pytest) and `frontend/src/` (React
  Testing Library). Run backend tests with `uv run pytest`.
- **Never commit** `.env`, `node_modules/`, or `.venv/` — all are covered by
  the root `.gitignore`.
```

