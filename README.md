# War News Creator - Local MySQL Starter

This project gives you a fully local starter stack:

- `n8n` automation
- `FastAPI` website/API
- `MySQL` database

Included workflow files:

- `n8n-warnews-local.json` (local ingest test workflow)
- `n8n-warnews-15feeds.json` (phase 2: resilient 15-feed workflow with validation/enrichment)
- `n8n-ai-agent-starter.json` (chat agent starter, optional)

## 1) Setup

1. Copy `.env.example` to `.env`.
2. Update secrets in `.env`.
3. Start services:
   - `docker compose up --build`

## 2) Open apps

- Website/API: `http://localhost:8000`
- n8n: `http://localhost:5678`

## 3) Import n8n local ingest workflow

1. Open n8n.
2. Go to **Workflows -> Import from File**.
3. Select `n8n-warnews-local.json`.
4. Run with **Manual Trigger**.

The workflow sends a test article to FastAPI using `POST /ingest`.

## 4) Verify article publishing

1. Visit `http://localhost:8000/articles`.
2. Open the new article detail page.

## API notes

- Ingest endpoint: `POST /ingest`
- Header required: `x-api-key: <INGEST_API_KEY>`
- Health check: `GET /health`
- Admin review UI: `GET /admin/review?admin_key=<ADMIN_API_KEY>`
- Admin actions:
  - `POST /admin/review/{article_id}/publish?admin_key=<ADMIN_API_KEY>`
  - `POST /admin/review/{article_id}/reject?admin_key=<ADMIN_API_KEY>`

## Editorial workflow (pro MVP)

1. n8n ingests incoming news to `POST /ingest`.
2. Each item is placed in `pending` editorial review with an auto confidence score.
3. Public pages show only `published` articles.
4. Open admin queue and publish/reject per item.

## Phase 2 quality upgrades

- n8n pipeline now validates content quality before ingest.
- Each item includes:
  - `region`
  - `event_key` (for event clustering)
  - `confidence_hint`
- API computes:
  - source-agreement adjusted confidence
  - confidence band (`high`/`medium`/`low`)
  - source match count for same event
- Optional auto-publish for strong items:
  - set `AUTO_PUBLISH_HIGH_CONFIDENCE=true` in `.env`

## Phase 3 claim extraction

- Each ingested article now stores claim blocks with:
  - claim text
  - citation URL
  - per-claim confidence
  - verdict (`confirmed`/`provisional`/`disputed`)
- Article detail page now shows citation blocks and confidence per claim.
- Extraction mode:
  - OpenRouter recommended: set `OPENROUTER_API_KEY` and model
  - fallback: `OPENAI_API_KEY`
  - if no key is set: heuristic fallback extraction
- Optional env:
  - `OPENAI_EXTRACTION_MODEL=qwen/qwen3-coder:free`
  - `LLM_API_BASE=https://openrouter.ai/api/v1`
  - `OPENROUTER_API_KEY=...`
  - `OPENROUTER_SITE_URL=http://localhost:8000`
  - `OPENROUTER_APP_NAME=war-news-agent`

## Phase 4 contradiction detection

- Detects likely contradictions between claims in the same `event_key`.
- Flags conflicts when:
  - opposite polarity language is found (e.g., "confirmed" vs "denied")
  - key numeric mismatch appears in similar claim topics
- Stores conflict alerts in `claim_conflicts`.
- Admin conflict dashboard:
  - `GET /admin/conflicts?admin_key=<ADMIN_API_KEY>`

## Phase 5 conflict actions + feedback loop

- Conflict actions available in dashboard:
  - `resolved`
  - `false_positive`
  - `needs_investigation`
- New endpoint:
  - `POST /admin/conflicts/{conflict_id}/status?admin_key=<ADMIN_API_KEY>`
- Feedback logic:
  - when `needs_investigation` is selected, linked article confidence is reduced
  - low-confidence published items are moved back to `pending` automatically

## Phase 6 source reliability + analytics

- New reliability model per source (`source_reliability`) learns from conflict outcomes.
- Source reliability score updates on:
  - ingest volume
  - open conflicts
  - resolved conflicts
  - false positives
  - needs investigation
- New admin analytics page:
  - `GET /admin/analytics?admin_key=<ADMIN_API_KEY>`
  - shows conflict trend snapshot
  - region distribution
  - lowest reliability sources first
- Reliability repair endpoint:
  - `POST /admin/analytics/rebuild-reliability?admin_key=<ADMIN_API_KEY>`
  - recalculates all source reliability counters/scores from current database state

## Deploy on Vercel

This repo now includes Vercel config:

- `api/index.py` (serverless entrypoint)
- `vercel.json` (route all requests to FastAPI app)

### 1) Prerequisites

- Host MySQL externally (Vercel has no local MySQL service).
- Keep n8n hosted separately (n8n Cloud / VPS / Railway) and point it to your Vercel URL.

### 2) Required Vercel env vars

Set these in Vercel project settings:

- `DATABASE_URL` (remote MySQL URL using `mysql+pymysql://...`)
- `INGEST_API_KEY`
- `ADMIN_API_KEY`
- `AUTO_PUBLISH_HIGH_CONFIDENCE` (`true` or `false`)
- `OPENROUTER_API_KEY` (if using model extraction)
- `OPENAI_EXTRACTION_MODEL` (default: `qwen/qwen3-coder:free`)
- `LLM_API_BASE` (`https://openrouter.ai/api/v1`)
- `OPENROUTER_SITE_URL` (your Vercel domain)
- `OPENROUTER_APP_NAME` (any app name)

### 3) Deploy commands

From project root:

1. `npm i -g vercel` (if needed)
2. `vercel login`
3. `vercel`
4. For production: `vercel --prod`

### 4) Post-deploy

- Test health: `/health`
- Update n8n `POST /ingest` URL to your Vercel domain
- Keep admin routes protected with `admin_key`

Add `ADMIN_API_KEY` in your `.env`, then restart:

- `docker compose up -d --build`

## Next step

After this local test works, replace the `Build Payload` node in n8n with:

- RSS/News source nodes
- AI summarization node
- same `POST /ingest` node

## Home Page
<img width="1920" height="1226" alt="image" src="https://github.com/user-attachments/assets/67e6a0c3-3b42-4b93-8a1a-31c1fec903a6" />

## Articel
<img width="1920" height="1278" alt="image" src="https://github.com/user-attachments/assets/98467ee6-3ec2-498a-a8a0-a18ac7a86797" />

##Admin Panel
<img width="1915" height="905" alt="image" src="https://github.com/user-attachments/assets/a6b1c2d1-285b-4914-ab75-c80d5e9d7531" />

##Analytic
<img width="1920" height="946" alt="image" src="https://github.com/user-attachments/assets/de4a672c-b43c-4f05-95f9-440112a5b221" />

##n8n Workflow
<img width="1920" height="917" alt="image" src="https://github.com/user-attachments/assets/ac59ea25-ecd7-441b-ab8b-4a5261817e1d" />


