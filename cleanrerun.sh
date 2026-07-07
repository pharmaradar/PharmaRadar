#!/usr/bin/env bash
# cleanrerun.sh — full wipe and fresh start for PharmaRadar v3
# Usage: ./cleanrerun.sh

set +e   # don't exit on errors — kill/docker commands return non-zero when nothing to kill
ROOT="$(cd "$(dirname "$0")" && pwd)"

RED='\033[0;31m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
BOLD='\033[1m'
NC='\033[0m'

log()  { echo -e "${CYAN}${BOLD}[•]${NC} $*"; }
ok()   { echo -e "${GREEN}${BOLD}[✓]${NC} $*"; }
warn() { echo -e "${YELLOW}${BOLD}[!]${NC} $*"; }

echo -e "\n${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BOLD}  PharmaRadar v3 — Clean Rerun${NC}"
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n"

# ── 1. Kill all running processes ─────────────────────────
log "Killing all running processes..."

pkill -9 -f "uvicorn app.main:app" 2>/dev/null || true
pkill -9 -f "celery" 2>/dev/null || true
pkill -9 -f "vite --port" 2>/dev/null || true

# Kill anything squatting on our ports
for port in 8009 5173; do
  pid=$(lsof -ti :$port 2>/dev/null)
  if [ -n "$pid" ]; then
    kill -9 $pid 2>/dev/null || true
    warn "Freed port $port (PID $pid)"
  fi
done

sleep 2
ok "All processes killed"

# ── 2. Docker — full wipe ──────────────────────────────────
log "Bringing Docker down (removing volumes)..."
cd "$ROOT"
docker compose -f docker-compose.yml -f docker-compose.dev.yml down --volumes 2>&1 \
  | grep -E "Remov|Stop|Kill" | sed 's/^/    /' || true
ok "Docker wiped"

# ── 3. Clear local caches, reports, logs ─────────────────
log "Clearing caches, reports, and logs..."

find "$ROOT/backend" -name "*.pyc" -delete 2>/dev/null || true
find "$ROOT/backend" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true

rm -rf "$ROOT/backend/reports"/* 2>/dev/null || true

truncate -s 0 /tmp/backend.log  2>/dev/null || true
truncate -s 0 /tmp/celery.log   2>/dev/null || true
truncate -s 0 /tmp/vite.log     2>/dev/null || true
truncate -s 0 /tmp/pharmaradar-backend.log 2>/dev/null || true

rm -f /tmp/celerybeat-schedule 2>/dev/null || true

ok "Caches cleared"

# ── 4. Docker — bring infra up ────────────────────────────
log "Starting Docker services (postgres, redis)..."
docker compose -f docker-compose.yml -f docker-compose.dev.yml \
  up -d postgres redis 2>&1 \
  | grep -E "Started|Created|Running" | sed 's/^/    /' || true

log "Waiting for postgres + redis to be healthy..."
for i in $(seq 1 45); do
  sleep 2
  pg=$(docker inspect --format='{{.State.Health.Status}}' pharmaradar-postgres-1 2>/dev/null)
  rd=$(docker inspect --format='{{.State.Health.Status}}' pharmaradar-redis-1 2>/dev/null)
  if [ "$pg" = "healthy" ] && [ "$rd" = "healthy" ]; then
    ok "Postgres + Redis healthy"
    break
  fi
  # Redis container can vanish after a full volume wipe — kick it back up
  if [ -z "$rd" ] && [ "$i" -gt 3 ]; then
    docker compose -f docker-compose.yml up -d redis --remove-orphans 2>/dev/null || true
  fi
  if [ "$i" = "45" ]; then
    echo -e "${RED}ERROR: services not healthy after 90s. Check Docker.${NC}"
    exit 1
  fi
done

# ── 5. Run Alembic migrations ──────────────────────────────
log "Running database migrations..."
source "$ROOT/.venv/bin/activate"
cd "$ROOT/backend"
alembic upgrade head 2>&1 | grep -E "Running|INFO.*migration|finished" | sed 's/^/    /' || true
ok "Migrations complete"

# ── 6. Start backend ──────────────────────────────────────
log "Starting backend (port 8009)..."
nohup uvicorn app.main:app --reload --port 8009 > /tmp/backend.log 2>&1 &
BACKEND_PID=$!

for i in $(seq 1 15); do
  sleep 1
  code=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8009/health 2>/dev/null)
  if [ "$code" = "200" ]; then
    ok "Backend ready (PID $BACKEND_PID)"
    break
  fi
  if [ "$i" = "15" ]; then
    echo -e "${RED}ERROR: backend failed to start. Check /tmp/backend.log${NC}"
    exit 1
  fi
done

# ── 7. Seed targets if needed ─────────────────────────────
TARGETS=$(curl -s http://localhost:8009/api/targets/ 2>/dev/null | python3 -c "import sys,json; print(len(json.load(sys.stdin)))" 2>/dev/null || echo "0")
if [ "$TARGETS" = "0" ]; then
  log "Seeding 15 KOL targets..."
  python3 -c "
import json, urllib.request
targets = json.load(open('app/targets.json'))
created = 0
for t in targets:
    body = json.dumps({'name': t['name'], 'known_urls': t.get('known_urls', []), 'notes': t.get('notes', '')}).encode()
    req = urllib.request.Request('http://localhost:8009/api/targets/', data=body, headers={'Content-Type': 'application/json'}, method='POST')
    try:
        urllib.request.urlopen(req)
        created += 1
    except:
        pass
print(f'    Created {created} targets')
" 2>/dev/null
  ok "Targets seeded"
else
  ok "Targets already in DB ($TARGETS found)"
fi

# ── 8. Set LLM provider from .env ─────────────────────────
log "Configuring LLM provider..."
python3 -c "
import os, sys
sys.path.insert(0, '.')
os.chdir('$ROOT/backend')

# Read .env directly
env = {}
try:
    with open('$ROOT/.env') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, v = line.split('=', 1)
                env[k.strip()] = v.strip()
except: pass

gemini  = env.get('GEMINI_API_KEY', '')
nvidia  = env.get('NVIDIA_API_KEY', '')
openai  = env.get('OPENAI_API_KEY', '')
anthropic = env.get('ANTHROPIC_API_KEY', '')

if gemini:
    provider, model = 'gemini', 'gemini-2.5-flash'
elif nvidia:
    provider, model = 'nvidia', 'meta/llama-3.3-70b-instruct'
elif anthropic:
    provider, model = 'anthropic', 'claude-haiku-4-5-20251001'
elif openai:
    provider, model = 'openai', 'gpt-4o-mini'
else:
    provider, model = 'vertex', 'gemini-2.5-flash'

import urllib.request, json
body = json.dumps({'llm_provider': provider, 'llm_model': model}).encode()
req = urllib.request.Request('http://localhost:8009/api/settings/', data=body, headers={'Content-Type': 'application/json'}, method='POST')
urllib.request.urlopen(req)
print(f'    Provider: {provider} | Model: {model}')
" 2>/dev/null
ok "LLM provider configured"

# ── 9. Start Celery worker + beat ─────────────────────────
log "Starting Celery worker..."
nohup ../.venv/bin/celery -A app.tasks.celery_app.celery_app worker \
  -Q scrape,llm,pdf -c 4 -n worker@local --loglevel=info > /tmp/celery.log 2>&1 &
WORKER_PID=$!

log "Starting Celery beat (scheduler)..."
nohup ../.venv/bin/celery -A app.tasks.celery_app.celery_app beat \
  -s /tmp/celerybeat-schedule --loglevel=warning >> /tmp/celery.log 2>&1 &
BEAT_PID=$!

for i in $(seq 1 12); do
  sleep 1
  if grep -q "ready\." /tmp/celery.log 2>/dev/null; then
    ok "Celery worker ready"
    break
  fi
done

# ── 10. Start frontend ────────────────────────────────────
log "Starting frontend (port 5173)..."
cd "$ROOT/frontend"
nohup npm run dev > /tmp/vite.log 2>&1 &
FRONTEND_PID=$!

for i in $(seq 1 15); do
  sleep 1
  code=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:5173/ 2>/dev/null)
  if [ "$code" = "200" ]; then
    ok "Frontend ready"
    break
  fi
done

# ── Done ──────────────────────────────────────────────────
echo ""
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}${BOLD}  All systems running — clean slate${NC}"
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
PROVIDER=$(curl -s http://localhost:8009/api/settings/ 2>/dev/null | python3 -c "import sys,json;d=json.load(sys.stdin);print(d['llm_provider']+' / '+d['llm_model'])" 2>/dev/null || echo "unknown")
TARGETS=$(curl -s http://localhost:8009/api/targets/ 2>/dev/null | python3 -c "import sys,json;print(len(json.load(sys.stdin)))" 2>/dev/null || echo "?")
echo -e "  ${BOLD}Frontend :${NC}  http://localhost:5173"
echo -e "  ${BOLD}Backend  :${NC}  http://localhost:8009"
echo -e "  ${BOLD}Provider :${NC}  $PROVIDER"
echo -e "  ${BOLD}Targets  :${NC}  $TARGETS KOLs loaded (all active)"
echo -e "  ${BOLD}DB       :${NC}  clean — 0 posts, 0 insights, 0 runs"
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "  Press ${BOLD}Ctrl+C${NC} to stop all services.\n"

trap "echo ''; log 'Shutting down...'; kill $BACKEND_PID $WORKER_PID $BEAT_PID $FRONTEND_PID 2>/dev/null; wait 2>/dev/null; ok 'Stopped.'; exit" INT TERM
wait
