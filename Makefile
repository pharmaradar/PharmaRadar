.PHONY: dev up down logs build migrate test lint frontend

# ── Local dev (infra only, run backend/frontend manually) ─
dev:
	docker compose -f docker-compose.yml -f docker-compose.dev.yml up postgres redis chromadb -d

# ── Full stack ─────────────────────────────────────────────
up:
	docker compose up --build -d

down:
	docker compose down

logs:
	docker compose logs -f

build:
	docker compose build

# ── DB migrations ─────────────────────────────────────────
migrate:
	cd backend && alembic upgrade head

migrate-new:
	cd backend && alembic revision --autogenerate -m "$(msg)"

# ── Tests ─────────────────────────────────────────────────
test:
	cd backend && pytest -v

# ── Frontend (local) ──────────────────────────────────────
frontend:
	cd frontend && npm run dev

install-frontend:
	cd frontend && npm install

# ── Backend (local, no Docker) ────────────────────────────
backend:
	cd backend && uvicorn app.main:app --port 8008 --reload

install-backend:
	cd backend && pip install -e ".[test]"

# ── Workers (local, each in its own terminal) ─────────────
worker-scrape:
	cd backend && celery -A app.tasks.celery_app.celery_app worker -Q scrape -c 4 -n scrape@local

worker-llm:
	cd backend && celery -A app.tasks.celery_app.celery_app worker -Q llm -c 2 -n llm@local

worker-pdf:
	cd backend && celery -A app.tasks.celery_app.celery_app worker -Q pdf -c 2 -n pdf@local

worker-embed:
	cd backend && celery -A app.tasks.celery_app.celery_app worker -Q embed -c 2 -n embed@local
