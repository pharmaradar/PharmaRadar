#!/usr/bin/env bash
set +e

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
echo -e "${BOLD}  PharmaRadar v3 — starting${NC}"
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n"

# ── 1. Docker infra ───────────────────────────────────────
log "Starting Docker services (postgres, redis)..."
docker compose -f "$ROOT/docker-compose.yml" -f "$ROOT/docker-compose.dev.yml" \
  up -d postgres redis 2>&1 | grep -E "Started|Running|healthy|error|Created" || true

log "Waiting for postgres + redis..."
for i in $(seq 1 45); do
  pg=$(docker inspect --format='{{.State.Health.Status}}' pharmaradar-postgres-1 2>/dev/null)
  rd=$(docker inspect --format='{{.State.Health.Status}}' pharmaradar-redis-1 2>/dev/null)
  if [ "$pg" = "healthy" ] && [ "$rd" = "healthy" ]; then ok "Infra ready."; break; fi
  # Redis container can vanish after a full volume wipe — kick it back up
  if [ -z "$rd" ] && [ "$i" -gt 3 ]; then
    docker compose -f "$ROOT/docker-compose.yml" up -d redis --remove-orphans 2>/dev/null || true
  fi
  if [ "$i" = "45" ]; then echo -e "${RED}ERROR: infra not healthy after 90s. Check Docker.${NC}"; exit 1; fi
  sleep 2
done

# ── 2. Migrations ─────────────────────────────────────────
log "Running migrations..."
source "$ROOT/.venv/bin/activate"
cd "$ROOT/backend"
alembic upgrade head 2>&1 | grep -E "Running|finished|already" || true

# ── 3. Backend ────────────────────────────────────────────
log "Starting backend (port 8009)..."
uvicorn app.main:app --reload --port 8009 &
BACKEND_PID=$!
for i in $(seq 1 15); do sleep 1; code=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8009/health 2>/dev/null); if [ "$code" = "200" ]; then ok "Backend ready"; break; fi; done

# ── 4. Seed targets ───────────────────────────────────────
TARGETS=$(curl -s http://localhost:8009/api/targets/ 2>/dev/null | python3 -c "import sys,json; print(len(json.load(sys.stdin)))" 2>/dev/null || echo "0")
if [ "$TARGETS" = "0" ]; then
  log "Seeding targets..."
  python3 -c "
import json, urllib.request
targets = json.load(open('app/targets.json'))
created = 0
for t in targets:
    body = json.dumps({'name': t['name'], 'known_urls': t.get('known_urls',[]), 'notes': t.get('notes','')}).encode()
    req = urllib.request.Request('http://localhost:8009/api/targets/', data=body, headers={'Content-Type':'application/json'}, method='POST')
    try: urllib.request.urlopen(req); created += 1
    except: pass
print(f'    {created} targets seeded')
" 2>/dev/null
fi

# ── 5. Set LLM provider ───────────────────────────────────
log "Configuring LLM provider..."
python3 -c "
import os, urllib.request, json
env = {}
try:
    with open('$ROOT/.env') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, v = line.split('=', 1)
                env[k.strip()] = v.strip()
except: pass
if env.get('GEMINI_API_KEY'):      p,m = 'gemini','gemini-2.5-flash'
elif env.get('NVIDIA_API_KEY'):    p,m = 'nvidia','meta/llama-3.3-70b-instruct'
elif env.get('ANTHROPIC_API_KEY'): p,m = 'anthropic','claude-haiku-4-5-20251001'
elif env.get('OPENAI_API_KEY'):    p,m = 'openai','gpt-4o-mini'
else:                              p,m = 'vertex','gemini-2.5-flash'
body = json.dumps({'llm_provider':p,'llm_model':m}).encode()
req = urllib.request.Request('http://localhost:8009/api/settings/', data=body, headers={'Content-Type':'application/json'}, method='POST')
urllib.request.urlopen(req)
print(f'    {p} / {m}')
" 2>/dev/null
ok "LLM configured"

# ── 6. Celery workers (dedicated per queue) ───────────────
#
# Worker topology (scales to 100-150 targets):
#   scrape-worker  — 6 concurrent targets (each does 5 parallel URL fetches)
#   llm-worker     — 3 concurrent LLM calls (Gemini rate limit)
#   misc-worker    — PDF + embed (2 concurrent, I/O light)
#   beat           — daily/weekly scheduler
#
log "Starting Celery workers..."

# Kill any leftover workers/beat from a previous (crashed or Ctrl+C'd) run, then
# clear the beat schedule file. Without this, a stale beat keeps the gdbm lock on
# /tmp/celerybeat-schedule and the new beat dies with "[Errno 11] Resource
# temporarily unavailable" — plus orphaned workers pile up and waste RAM.
pkill -f "celery -A app.tasks.celery_app.celery_app" 2>/dev/null && sleep 1
rm -f /tmp/celerybeat-schedule*

../.venv/bin/celery -A app.tasks.celery_app.celery_app worker \
  -Q scrape -c 6 -n scrape@local --loglevel=info >> /tmp/celery-scrape.log 2>&1 &
SCRAPE_PID=$!

../.venv/bin/celery -A app.tasks.celery_app.celery_app worker \
  -Q llm -c 3 -n llm@local --loglevel=info >> /tmp/celery-llm.log 2>&1 &
LLM_PID=$!

../.venv/bin/celery -A app.tasks.celery_app.celery_app worker \
  -Q pdf -c 2 -n misc@local --loglevel=info >> /tmp/celery-misc.log 2>&1 &
MISC_PID=$!

../.venv/bin/celery -A app.tasks.celery_app.celery_app beat \
  -s /tmp/celerybeat-schedule --loglevel=warning >> /tmp/celery-beat.log 2>&1 &
BEAT_PID=$!

for i in $(seq 1 15); do
  sleep 1
  READY=$(grep -c "ready\." /tmp/celery-scrape.log 2>/dev/null || echo 0)
  if [ "$READY" -ge 1 ]; then ok "Celery workers ready"; break; fi
done

# ── 7. Frontend ───────────────────────────────────────────
log "Starting frontend (port 5173)..."
cd "$ROOT/frontend"
npm run dev >> /tmp/vite.log 2>&1 &
FRONTEND_PID=$!
for i in $(seq 1 15); do sleep 1; code=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:5173/ 2>/dev/null); if [ "$code" = "200" ]; then ok "Frontend ready"; break; fi; done

# ── Done ──────────────────────────────────────────────────
echo ""
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}${BOLD}  All services running${NC}"
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
PROVIDER=$(curl -s http://localhost:8009/api/settings/ 2>/dev/null | python3 -c "import sys,json;d=json.load(sys.stdin);print(d['llm_provider']+' / '+d['llm_model'])" 2>/dev/null || echo "unknown")
TARGETS=$(curl -s http://localhost:8009/api/targets/ 2>/dev/null | python3 -c "import sys,json;t=json.load(sys.stdin);a=len([x for x in t if x['active']]);print(f'{a} active')" 2>/dev/null || echo "?")
echo -e "  ${BOLD}Frontend :${NC}  http://localhost:5173"
echo -e "  ${BOLD}Backend  :${NC}  http://localhost:8009"
echo -e "  ${BOLD}Provider :${NC}  $PROVIDER"
echo -e "  ${BOLD}Targets  :${NC}  $TARGETS"
echo -e "  ${BOLD}Workers  :${NC}  scrape×6  llm×3  pdf×2  beat"
echo -e "  ${BOLD}Capacity :${NC}  150 targets ~6 min (premium) / ~50 min (free)"
echo -e "  ${BOLD}Logs     :${NC}  /tmp/celery-{scrape,llm,misc}.log"
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "  Press ${BOLD}Ctrl+C${NC} to stop all services.\n"

trap "echo ''; log 'Shutting down...'; kill $BACKEND_PID $SCRAPE_PID $LLM_PID $MISC_PID $BEAT_PID $FRONTEND_PID 2>/dev/null; wait 2>/dev/null; ok 'Stopped.'; exit" INT TERM
wait
