# Build Prompts — Amaury's Requirements (2026-07)

Run each prompt as a **separate Claude Code session** in this repo, in order. Commit and verify each feature before starting the next. Each prompt is self-contained; CLAUDE.md + memory supply the stack context.

Amaury's clarifications (2026-07-15 email) baked into these prompts:
- "Real-time" = weekly/monthly standard report cadence. No streaming, no beat schedule.
- Curated source lists are accepted. No open-web crawling.
- "Instant" report = a few minutes is fine. Async jobs with progress UI are acceptable.
- AE posts: OK to keep in the database as long as **no human eye sees them** — filter at display/synthesis level, never delete. Patient posts (GDPR) still under legal review — do NOT build patient comment scraping yet.

---

## Prompt 1 — Burning Topics (backend + frontend)

```
Build the Burning Topics module. This is a persistent, user-defined topic tracker — NOT the same as the existing ad-hoc Topic Explorer.

DATA MODEL (new Alembic migration, follow existing migration numbering):
- Table `burning_topics`: id, name, description, language_filter (nullable), period_days (int, default 30), exclusion_words (JSON array), restriction_terms (JSON array), created_by (FK users), is_active, created_at.
- Table `burning_topic_reports`: id, topic_id FK, status (pending/running/done/failed), summary_md (text), key_findings (JSON), so_what (text), important_posts (JSON: url/title/author/engagement), main_authors (JSON), pdf_url (nullable), created_at.

BACKEND:
- New router backend/app/routers/burning_topics.py mounted under /api/burning-topics: CRUD for topics (admin+user can create own), POST /{id}/generate-report (enqueues Celery task), GET /{id}/reports, POST /{id}/reports/{report_id}/followup (conversational follow-up on a finished report using the report content as context, reuse the pattern from routers/agent.py).
- New Celery task in backend/app/tasks/ (follow the shape of existing tasks: acks_late is on, so make the task idempotent and check a should_stop-style flag between phases). Pipeline per report: 1) query already-scraped posts (scraped_posts + social posts) matching the topic terms within period_days, applying language filter and exclusion_words; 2) run ONE TinyFish discovery search for the topic to add fresh web context (TinyFish CLI subprocess ONLY — never requests/BeautifulSoup; reuse services/scraper.py invocation patterns); 3) synthesize with the existing LLM router (services/llm_router.py) into the report format: key findings / so what / important posts / main authors; 4) generate a PDF via services/pdf_generator.py and upload with services/vercel_blob_storage.py, store the URL.
- Respect existing LLM rate-limit handling. Do not add Celery beat. Do not create new LLM client code — go through llm_router.

FRONTEND:
- New page frontend/src/pages/BurningTopics.tsx, route + nav entry consistent with existing pages. List of topics with create/edit dialog (name, description, period, language, exclusion words). Per topic: "Generate report" button, report status polling (copy the 3s polling pattern from Settings.tsx pipeline status), report view rendering key findings / so what / important posts, PDF download button (direct blob URL like Reports.tsx), and a follow-up prompt box under a finished report.
- All API calls go in frontend/src/lib/api.ts like the existing ones. Auth headers same as existing pages.

DO NOT: touch the existing Topic Explorer, add scheduled jobs, introduce new scraping libraries, or refactor unrelated code.

ACCEPTANCE: create a topic "subcutaneous administration", generate a report against existing DB data, see key findings + so what + posts in UI, download the PDF, ask one follow-up question and get an answer grounded in the report. Run the backend + worker locally and demonstrate this end-to-end before finishing.
```

---

## Prompt 2 — Congress module (AFTER Prompt 1 is merged)

```
Build the Congress module on top of the Burning Topics infrastructure that already exists (models in burning_topics tables, report task, report UI).

DATA MODEL (new migration):
- Table `congresses`: id, name (e.g. "ASCO 2026"), hashtags (JSON array), start_date, end_date, disease_area (nullable), is_active, created_at.
- Table `congress_questions`: id, congress_id FK, question_text, created_at. (Example question: "during ASCO 2026, what were the top 10 studies posted on social media")
- Congress reports REUSE burning_topic_reports via a nullable congress_id column added to that table (one report row per generation, either topic_id or congress_id set — add a CHECK constraint).

BACKEND:
- Router backend/app/routers/congress.py under /api/congress: CRUD congresses, CRUD questions per congress, POST /{id}/generate-report.
- Report task: reuse the burning-topic report task code path (extract shared logic into a helper if needed, but keep the diff minimal). Differences: the search scope is the congress hashtags + name, the date window is start_date..end_date (not period_days), and the synthesis prompt must answer EACH configured question in its own section, followed by the standard main learnings / posts / articles / sum up / so what.

FRONTEND:
- Page Congress.tsx: congress list with dates, per-congress question editor (add/remove questions), generate + view reports with per-question answer sections, PDF download. Reuse the report-rendering components from BurningTopics.tsx — extract them into shared components rather than duplicating.

DO NOT: build a separate report pipeline, add scheduling, or scrape beyond TinyFish CLI discovery + existing DB data.

ACCEPTANCE: create "ASCO 2026" with 2 questions, generate a report, each question gets its own answered section, PDF downloads. Demonstrate end-to-end locally.
```

---

## Prompt 3 — Adverse Event filter

```
Add an Adverse Event (AE) filter across the pipeline. Regulatory context: AE posts MAY remain stored in the database, but must NEVER be shown to a human — not in any UI list, brief, synthesis, report, PDF, agent answer, or topic/congress report. Filter at read/synthesis time everywhere; never delete the rows.

BACKEND:
1. Migration: add `is_adverse_event` (bool, nullable = not yet classified) and `ae_reason` (text, nullable) to scraped_posts and to the social posts table.
2. Classification: add an AE classification step to the existing LLM extraction flow (services/extractor.py path) so every newly scraped post gets classified in the same LLM call where possible (extend the existing prompt/response schema rather than adding a second LLM call per post — cost matters). AE definition for the prompt: the post reports a specific patient experiencing a negative reaction/side effect/harm from a drug (not general discussion of side-effect profiles in studies).
3. Backfill: one Celery maintenance task (tasks/maintenance.py) to classify existing unclassified posts in batches with rate-limit-aware pacing through llm_router.
4. Enforcement: add `is_adverse_event IS NOT TRUE` to every query that feeds a human-visible surface: reports listing, dashboard briefs, social trends/top posts, agent chat context retrieval, discovery/kol-mentions, synthesis inputs, PDF generation inputs, and the burning-topic/congress report queries. Grep every router and task that reads scraped_posts/social posts and enumerate each place you changed in your final summary so I can audit it.

FRONTEND: no AE UI at all — no toggle to reveal them, no count badge. Invisible by design.

DO NOT: delete AE rows, add a "show AE" admin view, or change scraping behavior.

ACCEPTANCE: seed/mark one post as AE, verify it appears in a raw DB query but is absent from dashboard, social trends, agent answers, and a freshly generated report. List every query you modified.
```

---

## Prompt 4 — Competitor tracking

```
Add competitor monitoring using the existing KOL pipeline. Competitors are the same shape as KOL targets but a different category, and their headline feature is surfacing high-engagement publications.

BACKEND:
1. Migration: add `target_type` enum ('kol','competitor') to the targets table, default 'kol' (all existing rows stay kol).
2. Scrape/extract/synthesize: competitor targets flow through the EXACT same pipeline (TinyFish CLI scraping, extraction, dedup). Verify nothing in tasks/scrape.py or the extractor assumes "person" semantics in a way that breaks for a company account; fix minimally where it does.
3. Synthesis separation: competitor content must NOT bleed into the KOL brief. Add a competitor brief (same mechanism as the KOL brief in the synthesizer) and a /api/ endpoint exposing it, plus a "top competitor publications" endpoint ranked by engagement (views/reactions/comments where the scrape captured them; fall back to recency where engagement is missing — state which fields exist rather than inventing metrics).

FRONTEND:
1. Targets.tsx: type selector (KOL / Competitor) on create/edit, type column/filter in the list.
2. Dashboard: add a Competitor brief card alongside the existing combined/KOL/social cards, same interaction pattern.
3. New Competitors.tsx page (or a tab if the nav is crowded — match existing style): competitor list + top publications by engagement with links.
4.In the targets.tsx add a edit button in the table row to edit the target fields and remove delete button from the target list. and also filter option to search the target directly, andinstead of x/linkedin column, show all the given links which is added while adding target.

DO NOT: build a separate scraping path, engagement time-series, or internal-strategy comparison (explicitly out of scope).

ACCEPTANCE: add one competitor (e.g. a pharma company LinkedIn/X account), run a scrape for just that target, see its posts excluded from the KOL brief and present in the competitor brief and top-publications view. Demonstrate locally end-to-end.
```

---

## Prompt 5 — Stakeholder surfacing (emerging voices)

```
Build stakeholder identification from data we ALREADY collect — no new scraping. Goal: show authors who are talking about our topics but are NOT in the targets list ("emerging voices" outside the current audience).

BACKEND:
- Endpoint GET /api/discovery/emerging-voices with filters: topic/term (optional), period, language, platform. Implementation: aggregate over scraped_posts + social posts the authors whose handle/name does not match any row in targets; rank by (post count, total engagement); return author, platform, post count, engagement, top 2 example posts (respecting the AE filter from the AE-filter feature if present).
- This is a read-only aggregation endpoint — no new tables, no Celery task, no LLM call. If author identity fields are inconsistent between the two post tables, normalize in the query layer and say so in your summary.

FRONTEND:
- Section "Emerging voices" on the existing Discovery/TopicExplorer page (whichever fits the existing UX — look before choosing): filterable table of authors with expandable example posts, and an "Add as KOL/Competitor" button that pre-fills the existing target-creation flow.

DO NOT: scrape author profiles, build person enrichment, call any external API, or store any new personal data beyond what post rows already contain (GDPR: this feature must only re-present already-collected public post data).

ACCEPTANCE: with existing DB data, the endpoint returns non-target authors ranked by activity; filtering by a term narrows it; the add-as-target button lands in the create form pre-filled. Demonstrate locally.
```

---

## Prompt 6 — Combined dashboard synthesis (KOL + all population + burning topics)

```
Add the "global synthesis" Amaury asked for: one downloadable synthesis mixing the KOL sum-up + all-population sum-up + burning-topics sum-ups. Prereq: Burning Topics (Prompt 1) is live.

BACKEND:
- Endpoint POST /api/reports/global-synthesis (async: enqueue Celery task, plus a status/result GET). The task gathers the three EXISTING artifacts — latest KOL brief, latest social/all-population brief, latest report per active burning topic — and runs ONE llm_router synthesis pass merging them into: executive summary / key takeaways per section (KOL, population, burning topics) / so what / important posts. It must ONLY use those stored artifacts as input — no fresh scraping, no re-reading raw posts (keeps it cheap and fast). If a section has no data, say "no data this period" rather than inventing content.
- PDF via the existing pdf_generator + blob upload, same as other reports.

FRONTEND:
- Dashboard: a "Global synthesis" button near the existing brief cards → triggers generation, shows progress (existing polling pattern), then renders the synthesis with a PDF download. Cache the last result so revisiting doesn't regenerate (follow the brief caching pattern already used on the Dashboard).

DO NOT: regenerate the underlying briefs, add scheduling, or touch brief generation code.

ACCEPTANCE: with at least one KOL brief, one social brief and one burning-topic report in the DB, the button produces a merged synthesis referencing all three sections, and the PDF downloads. Demonstrate locally.
```

---

## Deferred — do NOT prompt for these yet
- **Patient comment analysis**: blocked on Amaury's legal team review (GDPR). When cleared: aggregate-only design (no comment text/usernames stored).
- **Internal-strategy benchmarking**: separate project, needs private storage + scoping call.
- **Real-time/streaming**: settled — weekly/monthly cadence is the agreed interpretation.
