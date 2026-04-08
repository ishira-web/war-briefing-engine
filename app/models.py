from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class Article(Base):
    __tablename__ = "articles"
    __table_args__ = (UniqueConstraint("source_url", name="uq_articles_source_url"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    slug: Mapped[str] = mapped_column(String(350), unique=True, index=True, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    source_name: Mapped[str] = mapped_column(String(120), nullable=False)
    source_url: Mapped[str] = mapped_column(String(700), nullable=False)
    region: Mapped[str] = mapped_column(String(80), default="global", nullable=False)
    event_key: Mapped[str] = mapped_column(String(120), index=True, default="", nullable=False)
    extraction_model: Mapped[str] = mapped_column(String(80), default="heuristic", nullable=False)
    published_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    reviews: Mapped[list["EditorialReview"]] = relationship(back_populates="article")
    claims: Mapped[list["Claim"]] = relationship(back_populates="article")


class RawItem(Base):
    __tablename__ = "raw_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    source_name: Mapped[str] = mapped_column(String(120), nullable=False)
    raw_title: Mapped[str] = mapped_column(String(500), nullable=False)
    raw_content: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str] = mapped_column(String(700), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    run_id: Mapped[str] = mapped_column(String(120), index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class EditorialReview(Base):
    __tablename__ = "editorial_reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("articles.id"), unique=True, index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    confidence_score: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)
    confidence_band: Mapped[str] = mapped_column(String(10), default="medium", nullable=False)
    source_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    editor_note: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    article: Mapped["Article"] = relationship(back_populates="reviews")


class Claim(Base):
    __tablename__ = "claims"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("articles.id"), index=True, nullable=False)
    claim_text: Mapped[str] = mapped_column(Text, nullable=False)
    citation_url: Mapped[str] = mapped_column(String(700), nullable=False)
    confidence_score: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)
    verdict: Mapped[str] = mapped_column(String(20), default="unverified", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    article: Mapped["Article"] = relationship(back_populates="claims")


class ClaimConflict(Base):
    __tablename__ = "claim_conflicts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    event_key: Mapped[str] = mapped_column(String(120), index=True, nullable=False)
    claim_a_id: Mapped[int] = mapped_column(ForeignKey("claims.id"), nullable=False)
    claim_b_id: Mapped[int] = mapped_column(ForeignKey("claims.id"), nullable=False)
    score: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)
    reason: Mapped[str] = mapped_column(String(120), default="contradiction", nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="open", nullable=False)
    resolution_note: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class SourceReliability(Base):
    __tablename__ = "source_reliability"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    source_name: Mapped[str] = mapped_column(String(120), unique=True, index=True, nullable=False)
    reliability_score: Mapped[float] = mapped_column(Float, default=0.6, nullable=False)
    total_articles: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    conflicts_open: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    conflicts_resolved: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    conflicts_false_positive: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    needs_investigation: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
