import os
import re
import time
import json
from hashlib import sha1
from datetime import datetime
from urllib import request as urlrequest
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, Form, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from slugify import slugify
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased

from .database import Base, engine, get_db
from .models import AgentRun, Article, Claim, ClaimConflict, EditorialReview, RawItem, SourceReliability
from .schemas import ClaimPayload, IngestPayload

app = FastAPI(title="War News Local")
templates = Jinja2Templates(directory="app/templates")


@app.on_event("startup")
def on_startup() -> None:
    # Docker can start app before MySQL is ready to accept TCP connections.
    for _ in range(30):
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            Base.metadata.create_all(bind=engine)
            _run_phase2_schema_updates()
            _run_phase3_schema_updates()
            _run_phase4_schema_updates()
            _run_phase6_schema_updates()
            return
        except OperationalError:
            time.sleep(2)
    raise RuntimeError("Database is not reachable after startup retries")


def require_ingest_key(x_api_key: str = Header(default="")) -> None:
    expected = os.getenv("INGEST_API_KEY", "")
    if not expected or x_api_key != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")


def _compute_confidence(payload: IngestPayload) -> float:
    text = f"{payload.title} {payload.summary} {payload.body}".lower()
    score = 0.45
    if payload.source_url:
        score += 0.15
    if len(payload.summary) > 80:
        score += 0.1
    if "reuters" in text or "bbc" in text or "ap " in text or "un " in text:
        score += 0.15
    if "unconfirmed" in text or "rumor" in text:
        score -= 0.2
    return max(0.0, min(1.0, score))


def _confidence_band(score: float) -> str:
    if score >= 0.8:
        return "high"
    if score >= 0.55:
        return "medium"
    return "low"


def _region_from_text(payload: IngestPayload) -> str:
    text = f"{payload.title} {payload.summary} {payload.body}".lower()
    rules = {
        "europe": ["ukraine", "russia", "nato", "black sea", "poland"],
        "middle-east": ["gaza", "israel", "iran", "syria", "lebanon", "yemen"],
        "africa": ["sudan", "sahel", "somalia", "ethiopia", "congo"],
        "asia": ["taiwan", "south china sea", "myanmar", "korean peninsula"],
    }
    for region, keywords in rules.items():
        if any(k in text for k in keywords):
            return region
    return "global"


def _event_key_from_payload(payload: IngestPayload) -> str:
    base = re.sub(r"[^a-z0-9 ]+", " ", payload.title.lower())
    normalized = " ".join(base.split())[:120]
    if not normalized:
        normalized = str(payload.source_url)
    return sha1(normalized.encode("utf-8")).hexdigest()[:16]


def _run_phase2_schema_updates() -> None:
    # Lightweight migration for existing local DBs created before phase 2.
    stmts = [
        "ALTER TABLE articles ADD COLUMN region VARCHAR(80) NOT NULL DEFAULT 'global'",
        "ALTER TABLE articles ADD COLUMN event_key VARCHAR(120) NOT NULL DEFAULT ''",
        "ALTER TABLE articles ADD COLUMN extraction_model VARCHAR(80) NOT NULL DEFAULT 'heuristic'",
        "ALTER TABLE editorial_reviews ADD COLUMN confidence_band VARCHAR(10) NOT NULL DEFAULT 'medium'",
        "ALTER TABLE editorial_reviews ADD COLUMN source_count INT NOT NULL DEFAULT 1",
    ]
    with engine.begin() as conn:
        for stmt in stmts:
            try:
                conn.execute(text(stmt))
            except Exception:
                # Column already exists on subsequent runs.
                pass


def _run_phase3_schema_updates() -> None:
    stmts = [
        """
        CREATE TABLE claims (
            id INT AUTO_INCREMENT PRIMARY KEY,
            article_id INT NOT NULL,
            claim_text TEXT NOT NULL,
            citation_url VARCHAR(700) NOT NULL,
            confidence_score FLOAT NOT NULL DEFAULT 0.5,
            verdict VARCHAR(20) NOT NULL DEFAULT 'unverified',
            created_at DATETIME NOT NULL,
            INDEX ix_claims_article_id (article_id)
        )
        """
    ]
    with engine.begin() as conn:
        for stmt in stmts:
            try:
                conn.execute(text(stmt))
            except Exception:
                pass


def _run_phase4_schema_updates() -> None:
    stmts = [
        """
        CREATE TABLE claim_conflicts (
            id INT AUTO_INCREMENT PRIMARY KEY,
            event_key VARCHAR(120) NOT NULL,
            claim_a_id INT NOT NULL,
            claim_b_id INT NOT NULL,
            score FLOAT NOT NULL DEFAULT 0.5,
            reason VARCHAR(120) NOT NULL DEFAULT 'contradiction',
            status VARCHAR(20) NOT NULL DEFAULT 'open',
            resolution_note TEXT NOT NULL,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL,
            INDEX ix_claim_conflicts_event_key (event_key)
        )
        """
    ]
    with engine.begin() as conn:
        for stmt in stmts:
            try:
                conn.execute(text(stmt))
            except Exception:
                pass
        # Forward migration for existing tables.
        for alter in [
            "ALTER TABLE claim_conflicts ADD COLUMN resolution_note TEXT NOT NULL",
            "ALTER TABLE claim_conflicts ADD COLUMN updated_at DATETIME NOT NULL",
        ]:
            try:
                conn.execute(text(alter))
            except Exception:
                pass


def _run_phase6_schema_updates() -> None:
    stmts = [
        """
        CREATE TABLE source_reliability (
            id INT AUTO_INCREMENT PRIMARY KEY,
            source_name VARCHAR(120) NOT NULL UNIQUE,
            reliability_score FLOAT NOT NULL DEFAULT 0.6,
            total_articles INT NOT NULL DEFAULT 0,
            conflicts_open INT NOT NULL DEFAULT 0,
            conflicts_resolved INT NOT NULL DEFAULT 0,
            conflicts_false_positive INT NOT NULL DEFAULT 0,
            needs_investigation INT NOT NULL DEFAULT 0,
            updated_at DATETIME NOT NULL
        )
        """
    ]
    with engine.begin() as conn:
        for stmt in stmts:
            try:
                conn.execute(text(stmt))
            except Exception:
                pass


def _get_or_create_source_row(db: Session, source_name: str) -> SourceReliability:
    row = db.execute(select(SourceReliability).where(SourceReliability.source_name == source_name)).scalar_one_or_none()
    if row:
        return row
    row = SourceReliability(source_name=source_name, updated_at=datetime.utcnow())
    db.add(row)
    db.flush()
    return row


def _recalculate_reliability(row: SourceReliability) -> None:
    score = 0.65
    score += min(0.15, row.conflicts_false_positive * 0.01)
    score += min(0.1, row.conflicts_resolved * 0.005)
    score -= min(0.35, row.needs_investigation * 0.03)
    score -= min(0.25, row.conflicts_open * 0.01)
    row.reliability_score = max(0.05, min(0.98, score))
    row.updated_at = datetime.utcnow()


def _rebuild_source_reliability(db: Session) -> int:
    # Reset all counters and recompute from current canonical data.
    sources = db.execute(select(SourceReliability)).scalars().all()
    by_name: dict[str, SourceReliability] = {}
    for row in sources:
        row.total_articles = 0
        row.conflicts_open = 0
        row.conflicts_resolved = 0
        row.conflicts_false_positive = 0
        row.needs_investigation = 0
        by_name[row.source_name] = row

    for src in db.execute(select(Article.source_name)).scalars().all():
        row = by_name.get(src) or _get_or_create_source_row(db, src)
        by_name[src] = row
        row.total_articles += 1

    claim_to_source: dict[int, str] = {}
    pairs = db.execute(select(Claim.id, Article.source_name).join(Article, Article.id == Claim.article_id)).all()
    for claim_id, src_name in pairs:
        claim_to_source[int(claim_id)] = src_name

    for conflict in db.execute(select(ClaimConflict)).scalars().all():
        src_a = claim_to_source.get(conflict.claim_a_id)
        src_b = claim_to_source.get(conflict.claim_b_id)
        for src in {src_a, src_b}:
            if not src:
                continue
            row = by_name.get(src) or _get_or_create_source_row(db, src)
            by_name[src] = row
            _adjust_source_status_counters(row, conflict.status, 1)

    for row in by_name.values():
        _recalculate_reliability(row)
    return len(by_name)


def _claim_polarity(text_in: str) -> int:
    t = text_in.lower()
    positive = ["confirmed", "secure", "recaptured", "ceasefire reached", "agreement signed"]
    negative = ["denied", "failed", "rejected", "collapsed", "violated"]
    if any(k in t for k in positive):
        return 1
    if any(k in t for k in negative):
        return -1
    return 0


def _extract_count(text_in: str) -> int | None:
    m = re.search(r"\b(\d{1,5})\b", text_in)
    return int(m.group(1)) if m else None


def _conflict_score(a: Claim, b: Claim) -> tuple[float, str] | None:
    ta = a.claim_text.lower()
    tb = b.claim_text.lower()
    # same topic hints to avoid unrelated cross-talk
    topic_terms = ["casualt", "killed", "injured", "ceasefire", "hostage", "strike", "attack"]
    if not any(term in ta and term in tb for term in topic_terms):
        return None
    pa = _claim_polarity(a.claim_text)
    pb = _claim_polarity(b.claim_text)
    if pa != 0 and pb != 0 and pa != pb:
        return (0.82, "opposite polarity claim")
    na = _extract_count(a.claim_text)
    nb = _extract_count(b.claim_text)
    if na is not None and nb is not None:
        hi = max(na, nb)
        if hi > 0 and abs(na - nb) / hi >= 0.45:
            return (0.78, "numeric mismatch")
    return None


def _adjust_source_status_counters(row: SourceReliability, status: str, delta: int) -> None:
    if status == "open":
        row.conflicts_open = max(0, row.conflicts_open + delta)
    elif status == "resolved":
        row.conflicts_resolved = max(0, row.conflicts_resolved + delta)
    elif status == "false_positive":
        row.conflicts_false_positive = max(0, row.conflicts_false_positive + delta)
    elif status == "needs_investigation":
        row.needs_investigation = max(0, row.needs_investigation + delta)


def _create_conflicts_for_event(db: Session, event_key: str, new_article_id: int) -> int:
    new_claims = db.execute(select(Claim).where(Claim.article_id == new_article_id)).scalars().all()
    if not new_claims:
        return 0
    existing_claims = db.execute(
        select(Claim)
        .join(Article, Article.id == Claim.article_id)
        .where(Article.event_key == event_key, Claim.article_id != new_article_id)
        .limit(500)
    ).scalars().all()
    created = 0
    sources_to_increment_open: set[str] = set()
    for nc in new_claims:
        for ec in existing_claims:
            score_and_reason = _conflict_score(nc, ec)
            if not score_and_reason:
                continue
            score, reason = score_and_reason
            claim_a_id = min(nc.id, ec.id)
            claim_b_id = max(nc.id, ec.id)
            exists = db.execute(
                select(ClaimConflict.id).where(
                    ClaimConflict.claim_a_id == claim_a_id,
                    ClaimConflict.claim_b_id == claim_b_id,
                )
            ).first()
            if exists:
                continue
            db.add(
                ClaimConflict(
                    event_key=event_key,
                    claim_a_id=claim_a_id,
                    claim_b_id=claim_b_id,
                    score=score,
                    reason=reason,
                    status="open",
                    resolution_note="",
                    updated_at=datetime.utcnow(),
                )
            )
            new_src = db.execute(
                select(Article.source_name).where(Article.id == nc.article_id)
            ).scalar_one_or_none()
            old_src = db.execute(
                select(Article.source_name).where(Article.id == ec.article_id)
            ).scalar_one_or_none()
            if new_src:
                sources_to_increment_open.add(new_src)
            if old_src:
                sources_to_increment_open.add(old_src)
            created += 1
    for src in sources_to_increment_open:
        row = _get_or_create_source_row(db, src)
        _adjust_source_status_counters(row, "open", 1)
        _recalculate_reliability(row)
    return created


def _band_from_score(score: float) -> str:
    if score >= 0.8:
        return "high"
    if score >= 0.55:
        return "medium"
    return "low"


def _apply_conflict_feedback(
    db: Session, conflict: ClaimConflict, old_status: str, new_status: str, note: str
) -> None:
    if old_status == new_status:
        return
    claim_a = db.execute(select(Claim).where(Claim.id == conflict.claim_a_id)).scalar_one_or_none()
    claim_b = db.execute(select(Claim).where(Claim.id == conflict.claim_b_id)).scalar_one_or_none()
    if not claim_a or not claim_b:
        return
    article_a = db.execute(select(Article).where(Article.id == claim_a.article_id)).scalar_one_or_none()
    article_b = db.execute(select(Article).where(Article.id == claim_b.article_id)).scalar_one_or_none()
    impacted_sources = []
    if article_a:
        impacted_sources.append(article_a.source_name)
    if article_b:
        impacted_sources.append(article_b.source_name)

    if new_status == "needs_investigation":
        for article_id in [claim_a.article_id, claim_b.article_id]:
            review = db.execute(select(EditorialReview).where(EditorialReview.article_id == article_id)).scalar_one_or_none()
            if not review:
                continue
            review.confidence_score = max(0.0, review.confidence_score - 0.12)
            review.confidence_band = _band_from_score(review.confidence_score)
            if review.status == "published" and review.confidence_band == "low":
                review.status = "pending"
            note_prefix = "[conflict review required]"
            merged_note = f"{note_prefix} {note}".strip()
            if merged_note not in (review.editor_note or ""):
                review.editor_note = f"{review.editor_note}\n{merged_note}".strip()
            review.updated_at = datetime.utcnow()

    # source reliability learning from conflict outcomes (delta-safe).
    for src in set(impacted_sources):
        row = _get_or_create_source_row(db, src)
        _adjust_source_status_counters(row, old_status, -1)
        _adjust_source_status_counters(row, new_status, 1)
        _recalculate_reliability(row)


def _heuristic_claims(payload: IngestPayload) -> list[ClaimPayload]:
    source_url = str(payload.source_url)
    combined = f"{payload.summary}. {payload.body}"
    sentences = [s.strip() for s in re.split(r"[.!?]\s+", combined) if len(s.strip()) > 35]
    claims: list[ClaimPayload] = []
    for sentence in sentences[:4]:
        score = 0.55
        lower = sentence.lower()
        if "reportedly" in lower or "unconfirmed" in lower:
            score = 0.35
        elif any(k in lower for k in ["confirmed", "official", "according to", "ministry"]):
            score = 0.7
        claims.append(
            ClaimPayload(
                claim_text=sentence,
                citation_url=source_url,
                confidence_score=score,
                verdict="provisional",
            )
        )
    return claims


def _openai_claims(payload: IngestPayload) -> tuple[list[ClaimPayload], str]:
    openrouter_key = os.getenv("OPENROUTER_API_KEY", "")
    openai_key = os.getenv("OPENAI_API_KEY", "")
    model = os.getenv("OPENAI_EXTRACTION_MODEL", "qwen/qwen3-coder:free")
    api_base = os.getenv("LLM_API_BASE", "https://openrouter.ai/api/v1")
    api_key = openrouter_key or openai_key
    if not api_key:
        return (_heuristic_claims(payload), "heuristic")

    prompt = {
        "title": payload.title,
        "summary": payload.summary,
        "body": payload.body[:6000],
        "source_url": str(payload.source_url),
        "instruction": "Extract up to 5 factual claims. Return strict JSON with key claims only. Do not invent facts.",
    }
    req_body = {
        "model": model,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": "You extract factual claims for conflict news with strict JSON output."},
            {"role": "user", "content": json.dumps(prompt)},
        ],
        "temperature": 0.1,
    }
    req = urlrequest.Request(
        f"{api_base}/chat/completions",
        data=json.dumps(req_body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "HTTP-Referer": os.getenv("OPENROUTER_SITE_URL", "http://localhost:8000"),
            "X-Title": os.getenv("OPENROUTER_APP_NAME", "war-news-agent"),
        },
    )
    try:
        with urlrequest.urlopen(req, timeout=20) as resp:
            parsed = json.loads(resp.read().decode("utf-8"))
        content = parsed["choices"][0]["message"]["content"]
        obj = json.loads(content)
        out: list[ClaimPayload] = []
        for c in obj.get("claims", [])[:5]:
            out.append(
                ClaimPayload(
                    claim_text=str(c.get("claim_text", "")).strip()[:1200],
                    citation_url=str(c.get("citation_url") or payload.source_url),
                    confidence_score=float(c.get("confidence_score", 0.5)),
                    verdict=str(c.get("verdict", "provisional"))[:20],
                )
            )
        if not out:
            return (_heuristic_claims(payload), "heuristic")
        return (out, model)
    except Exception:
        return (_heuristic_claims(payload), "heuristic")


def _is_admin(request: Request) -> bool:
    expected = os.getenv("ADMIN_API_KEY", "")
    supplied = request.query_params.get("admin_key", "")
    return bool(expected) and supplied == expected


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/ingest", dependencies=[Depends(require_ingest_key)])
def ingest(payload: IngestPayload, db: Session = Depends(get_db)) -> dict[str, str | int]:
    if len(payload.title.strip()) < 12 or len(payload.summary.strip()) < 40:
        return {"status": "skipped_low_quality", "article_id": 0, "slug": ""}

    source_name = (payload.source_name or "").strip()
    if not source_name:
        source_name = (urlparse(str(payload.source_url)).hostname or "unknown-source").lower()

    raw = RawItem(
        source_name=source_name,
        raw_title=payload.raw_title or payload.title,
        raw_content=payload.raw_content or payload.summary,
        source_url=str(payload.source_url),
    )
    db.add(raw)

    run = AgentRun(
        run_id=payload.run_id,
        status=payload.status,
        tokens=payload.tokens,
        error=payload.error,
    )
    db.add(run)

    base_slug = slugify(payload.title)[:300] or "untitled"
    slug = base_slug
    for i in range(1, 50):
        exists = db.execute(select(Article.id).where(Article.slug == slug)).first()
        if not exists:
            break
        slug = f"{base_slug}-{i}"

    event_key = payload.event_key or _event_key_from_payload(payload)
    similar_count = db.execute(
        select(Article.id).where(Article.event_key == event_key).limit(10)
    ).scalars().all()
    source_count = max(1, len(similar_count) + 1)

    base_score = payload.confidence_hint if payload.confidence_hint is not None else _compute_confidence(payload)
    source_boosted_score = min(1.0, base_score + min(0.25, (source_count - 1) * 0.08))
    band = _confidence_band(source_boosted_score)
    auto_publish_enabled = os.getenv("AUTO_PUBLISH_HIGH_CONFIDENCE", "false").lower() == "true"
    initial_status = "published" if auto_publish_enabled and band == "high" else "pending"
    extracted_claims, extraction_model = (payload.claims, "from-payload") if payload.claims else _openai_claims(payload)

    article = Article(
        title=payload.title,
        slug=slug,
        summary=payload.summary,
        body=payload.body,
        source_name=source_name,
        source_url=str(payload.source_url),
        region=payload.region or _region_from_text(payload),
        event_key=event_key,
        extraction_model=extraction_model,
        published_at=payload.published_at or datetime.utcnow(),
    )
    db.add(article)
    db.flush()
    source_row = _get_or_create_source_row(db, source_name)
    source_row.total_articles += 1
    _recalculate_reliability(source_row)

    review = EditorialReview(
        article_id=article.id,
        status=initial_status,
        confidence_score=source_boosted_score,
        confidence_band=band,
        source_count=source_count,
    )
    db.add(review)
    for c in extracted_claims[:5]:
        db.add(
            Claim(
                article_id=article.id,
                claim_text=c.claim_text,
                citation_url=str(c.citation_url),
                confidence_score=max(0.0, min(1.0, float(c.confidence_score))),
                verdict=(c.verdict or "provisional")[:20],
            )
        )
    try:
        db.flush()
        conflict_count = _create_conflicts_for_event(db, event_key=event_key, new_article_id=article.id)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        if "uq_articles_source_url" in str(exc):
            return {"status": "duplicate", "article_id": 0, "slug": ""}
        raise

    return {"status": "stored", "article_id": article.id, "slug": article.slug, "conflicts_flagged": conflict_count}


@app.get("/", response_class=HTMLResponse)
def home(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    rows = db.execute(
        select(Article)
        .join(EditorialReview, EditorialReview.article_id == Article.id)
        .where(EditorialReview.status == "published")
        .order_by(Article.published_at.desc())
        .limit(30)
    ).scalars().all()
    return templates.TemplateResponse("index.html", {"request": request, "articles": rows})


@app.get("/articles", response_class=HTMLResponse)
def articles(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    rows = db.execute(
        select(Article)
        .join(EditorialReview, EditorialReview.article_id == Article.id)
        .where(EditorialReview.status == "published")
        .order_by(Article.published_at.desc())
        .limit(100)
    ).scalars().all()
    return templates.TemplateResponse("articles.html", {"request": request, "articles": rows})


@app.get("/articles/{slug}", response_class=HTMLResponse)
def article_detail(slug: str, request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    article = db.execute(
        select(Article)
        .join(EditorialReview, EditorialReview.article_id == Article.id)
        .where(Article.slug == slug, EditorialReview.status == "published")
    ).scalar_one_or_none()
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")
    claims = db.execute(select(Claim).where(Claim.article_id == article.id).order_by(Claim.confidence_score.desc())).scalars().all()
    return templates.TemplateResponse("article_detail.html", {"request": request, "article": article, "claims": claims})


@app.get("/admin/review", response_class=HTMLResponse)
def admin_review(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    if not _is_admin(request):
        raise HTTPException(status_code=401, detail="Unauthorized admin access")
    rows = db.execute(
        select(Article, EditorialReview)
        .join(EditorialReview, EditorialReview.article_id == Article.id)
        .where(EditorialReview.status == "pending")
        .order_by(Article.created_at.desc())
        .limit(200)
    ).all()
    return templates.TemplateResponse("admin_review.html", {"request": request, "rows": rows})


@app.get("/admin/published", response_class=HTMLResponse)
def admin_published(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    if not _is_admin(request):
        raise HTTPException(status_code=401, detail="Unauthorized admin access")
    rows = db.execute(
        select(Article, EditorialReview)
        .join(EditorialReview, EditorialReview.article_id == Article.id)
        .where(EditorialReview.status == "published")
        .order_by(Article.created_at.desc())
        .limit(200)
    ).all()
    return templates.TemplateResponse("admin_published.html", {"request": request, "rows": rows})


@app.get("/admin/conflicts", response_class=HTMLResponse)
def admin_conflicts(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    if not _is_admin(request):
        raise HTTPException(status_code=401, detail="Unauthorized admin access")
    claim_a = aliased(Claim)
    claim_b = aliased(Claim)
    article_a = aliased(Article)
    article_b = aliased(Article)
    rows = db.execute(
        select(ClaimConflict, claim_a, article_a, claim_b, article_b)
        .join(claim_a, claim_a.id == ClaimConflict.claim_a_id)
        .join(article_a, article_a.id == claim_a.article_id)
        .join(claim_b, claim_b.id == ClaimConflict.claim_b_id)
        .join(article_b, article_b.id == claim_b.article_id)
        .where(ClaimConflict.status == "open")
        .order_by(ClaimConflict.score.desc(), ClaimConflict.created_at.desc())
        .limit(200)
    ).all()
    return templates.TemplateResponse("admin_conflicts.html", {"request": request, "rows": rows})


@app.get("/admin/analytics", response_class=HTMLResponse)
def admin_analytics(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    if not _is_admin(request):
        raise HTTPException(status_code=401, detail="Unauthorized admin access")

    reliability_rows = db.execute(
        select(SourceReliability).order_by(SourceReliability.reliability_score.asc()).limit(50)
    ).scalars().all()

    region_counts: dict[str, int] = {}
    for region in db.execute(select(Article.region)).scalars().all():
        region_counts[region] = region_counts.get(region, 0) + 1
    region_stats = sorted(region_counts.items(), key=lambda x: x[1], reverse=True)[:12]

    conflict_counts: dict[str, int] = {"open": 0, "resolved": 0, "false_positive": 0, "needs_investigation": 0}
    for status in db.execute(select(ClaimConflict.status)).scalars().all():
        if status in conflict_counts:
            conflict_counts[status] += 1

    return templates.TemplateResponse(
        "admin_analytics.html",
        {
            "request": request,
            "reliability_rows": reliability_rows,
            "region_stats": region_stats,
            "conflict_counts": conflict_counts,
        },
    )


@app.post("/admin/analytics/rebuild-reliability")
def admin_rebuild_reliability(request: Request, db: Session = Depends(get_db)) -> RedirectResponse:
    if not _is_admin(request):
        raise HTTPException(status_code=401, detail="Unauthorized admin access")
    _rebuild_source_reliability(db)
    db.commit()
    return RedirectResponse(url=f"/admin/analytics?admin_key={request.query_params.get('admin_key', '')}", status_code=303)


@app.post("/admin/conflicts/{conflict_id}/status")
def update_conflict_status(
    conflict_id: int,
    request: Request,
    status: str = Form(default="open"),
    resolution_note: str = Form(default=""),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    if not _is_admin(request):
        raise HTTPException(status_code=401, detail="Unauthorized admin access")
    allowed = {"open", "resolved", "false_positive", "needs_investigation"}
    if status not in allowed:
        raise HTTPException(status_code=400, detail="Invalid conflict status")
    conflict = db.execute(select(ClaimConflict).where(ClaimConflict.id == conflict_id)).scalar_one_or_none()
    if not conflict:
        raise HTTPException(status_code=404, detail="Conflict not found")
    old_status = conflict.status
    conflict.status = status
    conflict.resolution_note = resolution_note
    conflict.updated_at = datetime.utcnow()
    _apply_conflict_feedback(db, conflict, old_status, status, resolution_note)
    db.commit()
    return RedirectResponse(url=f"/admin/conflicts?admin_key={request.query_params.get('admin_key', '')}", status_code=303)


@app.post("/admin/review/{article_id}/publish")
def publish_article(
    article_id: int,
    request: Request,
    editor_note: str = Form(default=""),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    if not _is_admin(request):
        raise HTTPException(status_code=401, detail="Unauthorized admin access")
    review = db.execute(select(EditorialReview).where(EditorialReview.article_id == article_id)).scalar_one_or_none()
    if not review:
        raise HTTPException(status_code=404, detail="Review record not found")
    review.status = "published"
    review.editor_note = editor_note
    review.updated_at = datetime.utcnow()
    db.commit()
    return RedirectResponse(url=f"/admin/review?admin_key={request.query_params.get('admin_key', '')}", status_code=303)


@app.post("/admin/review/{article_id}/reject")
def reject_article(
    article_id: int,
    request: Request,
    editor_note: str = Form(default=""),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    if not _is_admin(request):
        raise HTTPException(status_code=401, detail="Unauthorized admin access")
    review = db.execute(select(EditorialReview).where(EditorialReview.article_id == article_id)).scalar_one_or_none()
    if not review:
        raise HTTPException(status_code=404, detail="Review record not found")
    review.status = "rejected"
    review.editor_note = editor_note
    review.updated_at = datetime.utcnow()
    db.commit()
    return RedirectResponse(url=f"/admin/review?admin_key={request.query_params.get('admin_key', '')}", status_code=303)
