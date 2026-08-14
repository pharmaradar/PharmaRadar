# PharmaRadar v3 — Production Deploy Runbook

Backend + workers on **Railway**, frontend on **Vercel**, PDFs on **Vercel Blob**.
Deploy order matters: Railway databases → Railway services → Vercel Blob → Vercel frontend → pin CORS.

---

## 1. Railway project

Create one Railway project (e.g. `pharmaradar`), connected to the GitHub repo `pharmaradar/PharmaRadar`.

### 1a. Databases (add these FIRST)

| Plugin | Notes |
|---|---|
| **PostgreSQL** | ⚠️ Must support **pgvector** — deploy Railway's `pgvector` template image (e.g. `pgvector/pgvector:pg16`), NOT the plain Postgres plugin. Migration `008` runs `CREATE EXTENSION vector` and the backend will crash-loop on boot if the extension binary is missing. |
| **Redis** | Default settings are fine. |

### 1b. App services (create 4 services from the SAME repo)

For **each** service, in Settings:
- **Root Directory**: `backend`
- **Config-as-code file path**: see table (paths are from the repo root)

| Service name | Config file | What it runs |
|---|---|---|
| `backend` | `/railway/backend.json` | Alembic migrations + uvicorn API (healthcheck on `/health`) |
| `worker-scrape` | `/railway/worker-scrape.json` | Celery `-Q scrape -c6` (TinyFish + Apify) |
| `worker-llm` | `/railway/worker-llm.json` | Celery `-Q llm -c4` (Gemini extraction/synthesis) |
| `worker-pdf` | `/railway/worker-pdf.json` | Celery `-Q pdf -c2 -B` (WeasyPrint + embedded beat) |

> The start commands live in those JSON files (single-service Railway schema).
> If a service ignores its config file, paste the `startCommand` from the JSON
> into the dashboard's Custom Start Command instead.

### 1c. Environment variables (set on backend AND all 3 workers)

Use Railway's shared variables / reference syntax where possible.

| Variable | Value |
|---|---|
| `DATABASE_URL` | `${{Postgres.DATABASE_URL}}` (postgres:// is auto-converted to asyncpg) |
| `REDIS_URL` | `${{Redis.REDIS_URL}}/0` — ⚠️ keep the explicit `/0` suffix |
| `CELERY_BROKER_URL` | `${{Redis.REDIS_URL}}/1` |
| `CELERY_RESULT_BACKEND` | `${{Redis.REDIS_URL}}/2` |
| `SECRET_KEY` | fresh random: `python -c "import secrets; print(secrets.token_urlsafe(48))"` — backend refuses to boot in production without it |
| `ENVIRONMENT` | `production` |
| `SEED_ADMIN_EMAIL` | your admin login email (used by migration 016 on first deploy) |
| `SEED_ADMIN_PASSWORD` | strong unique password — do NOT reuse the dev one |
| `SEED_ADMIN_NAME` | display name |
| `GEMINI_API_KEY` | Google AI Studio key (primary LLM) |
| `TINYFISH_API_KEYS` | comma-separated key(s), no spaces |
| `APIFY_API_TOKEN` | Apify starter-plan token |
| `VOYAGE_API_KEY` | Voyage AI (embeddings; FastEmbed fallback works without it but adds ~300MB RAM per worker process) |
| `VERCEL_BLOB_TOKEN` | from step 2 |
| `RUN_TRIGGER_URL` | `https://<backend-domain>/api/runs/trigger` (set after backend domain exists) |
| `ALLOWED_ORIGINS` | `*` initially → pin in step 4 |
| `SENTRY_DSN` | optional |

The explicit `/0` on `REDIS_URL` matters: the reset-all endpoint derives DB 0/1/2
URLs by stripping the last path segment — a bare URL breaks that logic silently.

### 1d. Resource limits (Settings → Resources, per service)

Railway bills actual usage; these are safety ceilings against OOM/runaway:

| Service | Memory | vCPU |
|---|---|---|
| backend | 512 MB | 1 |
| worker-scrape | **2 GB** | 1 |
| worker-llm | 1 GB | 1 |
| worker-pdf | 1 GB | 1 |

⚠️ worker-scrape needs the 2 GB: each concurrent TinyFish scrape spawns a headless
browser (~300–500 MB), so `-c6` can peak near 2 GB. v2 OOM'd here once — the kernel
SIGKILLs the worker, `acks_late` re-queues the task, and it death-spirals
(`WorkerLostError ... signal 9` in a loop). If you ever see that: flush Redis
(Settings → Destroy Zone, or Railway Redis → CLI → `FLUSHALL`), then restart in
order **Redis → backend → workers**.

---

## 2. Vercel Blob (PDF storage)

In the Vercel dashboard: Storage → Create Blob store (any name, e.g. `pharmaradar-reports`).
**Access: PUBLIC** — the app emails/links raw blob URLs; the Reports UI opens them directly.
Copy the read-write token into Railway as `VERCEL_BLOB_TOKEN`.

## 3. Vercel frontend

- New Vercel project from the same repo, **Root Directory: `frontend`** (it will pick up `frontend/vercel.json`: SPA rewrites + `npm run build` + `dist`).
- Env var (build-time): `VITE_API_URL` = `https://<backend-domain>` — **no trailing slash, no `/api` suffix** (the client appends `/api` itself).
- Redeploy after changing `VITE_API_URL` (it's baked in at build time).

## 4. Seed the monitoring configuration

The migrations seed 3 targets and 32 tracked accounts. Everything added since —
the rest of the KOLs, the competitors, the remaining accounts, burning topics and
congresses — lives only in whichever deployment it was typed into, so copy it
across rather than retyping it:

```bash
cd backend
# 1. from the deployment that HAS the config (reads its .env)
./.venv/bin/python scripts/seed_config.py export config.json

# 2. against the new one. Railway → Postgres → Variables → DATABASE_PUBLIC_URL
#    (the internal *.railway.internal host does not resolve from a laptop).
#    Prints a plan and writes nothing:
TARGET_DATABASE_URL='postgresql://…' ./.venv/bin/python scripts/seed_config.py import config.json

# 3. same command with --commit once the plan reads right
```

- **Configuration only.** No posts, insights or reports travel, and an account's
  scan health and cached analysis are dropped — they measure the source
  deployment's corpus, and in an empty one they would state a post count and an
  AI summary for content that isn't there. The destination generates its own.
- Rows match on a **natural key** (`targets.name`, `tracked_accounts.platform+handle`,
  topic/congress `name`), never on id, because the seed migrations already
  created some of them under different ids. Re-running creates nothing.
- Rows already present are **left alone**; pass `--update-existing` to overwrite
  them with the source's version.
- Run it **after** the backend has booted once: `app_settings` has no row until
  then, so the social keywords and Facebook page list would be skipped.
- `config.json` holds no credentials, but it does hold the client's target list —
  don't commit it.

## 5. Post-deploy checklist

1. `GET https://<backend-domain>/health` → `{"status":"ok","version":"3.0.0"}`.
2. Log in with the `SEED_ADMIN_*` credentials; create real users from the Users page.
3. Set `ALLOWED_ORIGINS=https://<frontend-domain>` on the backend service (comma-add localhost origins only if you want local dev against prod API).
4. Add 1–2 targets, trigger a small run (`limit` = 2) from the UI, watch Railway logs on all 3 workers, confirm a PDF lands in the Reports page.
5. Rotate any credential that was ever pasted in chat/notes.

## Operational notes

- **Celery beat runs embedded in worker-pdf** (`-B` flag — worker-pdf is single-replica; never scale it to multiple replicas or the schedule fires twice). What beat actually does:
  - The stale-run and stale-report reapers fire every 5 min (auto-clears runs/reports orphaned by a dead worker).
  - Scheduled scrape runs / social scans stay OFF unless the Schedule toggles in Settings are enabled (they default off — beat just checks the toggles every minute).
  - The AE-classification backfill sweeps unclassified posts in small batches.
- Scrape tasks have hard time limits (8–12 min) and `acks_late` — a killed worker re-queues its task.
- Task results auto-expire from Redis after 1h (`result_expires`).
- TinyFish free keys = 500 credits/month each; 50 KOLs weekly ≈ 200+/mo — watch usage, add keys to `TINYFISH_API_KEYS` (rotation is automatic).
