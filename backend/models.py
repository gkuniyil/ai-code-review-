"""
models.py

Purpose:
    Defines the database schema for the application.

Responsibilities:
    - Define PullRequest table
    - Define Review table
    - Define relationship between them
"""

from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.sql import func

from backend.database import Base


class PullRequest(Base):
    """
    Stores information about a GitHub pull request.
    """

    __tablename__ = "pull_requests"
    __table_args__ = (
        # PR numbers are only unique within a repo (two different repos can
        # both have a "PR #1") — github_pr_number alone must not be globally unique
        UniqueConstraint("repo_name", "github_pr_number", name="uq_repo_pr_number"),
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
    created_at = Column(DateTime, server_default=func.now())