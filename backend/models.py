"""
models.py

Purpose:
    Defines the database schema for the application.

Responsibilities:
    - Define PullRequest table
    - Define Review table
    - Define relationship between them
"""

from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, Index, LargeBinary
from sqlalchemy.sql import func

from backend.database import Base


class PullRequest(Base):
    """
    Stores one review run of a GitHub pull request.

    A PR gets a new row every time it's opened or receives new commits
    ("synchronize"), so the same (repo_name, github_pr_number) can appear
    many times — one row per webhook delivery. github_delivery_id is the
    only thing that must be unique.
    """

    __tablename__ = "pull_requests"
    __table_args__ = (
        # non-unique index — makes "all review runs for this PR" lookups fast
        # without blocking a second review of the same PR
        Index("ix_repo_pr_number", "repo_name", "github_pr_number"),
    )

    id =Column(Integer, primary_key=True)
    github_delivery_id = Column(String(255), nullable=False, unique=True)
    github_pr_number = Column(Integer, nullable=False)
    repo_name = Column(String(500), nullable=False)
    title = Column(String(500), nullable=False)
    author = Column(String(255), nullable=False)
    diff = Column(Text, nullable=False)
    status = Column(String(50), nullable=False, default="pending")
    created_at = Column(DateTime, server_default=func.now())
    review_started_at = Column(DateTime, nullable = True)
    completed_at = Column(DateTime, nullable = True)
    # set as soon as the github review is posted — a retry that sees this skips posting again
    github_review_id = Column(Integer, nullable=True)


class Review(Base):
    """
    Stores one AI review finding for a pull request.
    """

    __tablename__ = "reviews"

    id = Column(Integer, primary_key=True)
    pr_id = Column(Integer, ForeignKey("pull_requests.id"), nullable=False)
    agent_type = Column(String(50), nullable=False)
    severity = Column(String(50), nullable=False)
    file_path = Column(String(500), nullable=False)
    line_number = Column(Integer, nullable=True)
    comment = Column(Text, nullable=False)
    suggested_fix = Column(Text, nullable=True)
    # review memory (embeddings.py): the code this finding pointed at + its 384-float
    # embedding as raw bytes. null when the line wasn't in the diff or embedding failed
    code_snippet = Column(Text, nullable=True)
    embedding = Column(LargeBinary, nullable=True)
    created_at = Column(DateTime, server_default=func.now())