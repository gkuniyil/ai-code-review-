from groq import Groq
import json
from pydantic import BaseModel
from typing import List
from backend.config import settings


class ReviewFinding(BaseModel):
    """represents one finding from an AI agent"""
    agent_type: str          # bug / security / quality / optimization
    severity: str            # high / medium / low
    file_path: str           # "backend/auth.py"
    line_number: int | None  # None if issue spans whole file
    comment: str             # what the issue is
    suggested_fix: str | None  # None if no clear fix


class PRReview(BaseModel):
    """represents full review output from all agents"""
    findings: List[ReviewFinding]  # all findings from this agent
    summary: str                   # overall summary


# single shared groq client — reuse across all calls
client = Groq(api_key=settings.groq_api_key)

# each agent gets different prompt so it focuses on one thing deeply
AGENT_PROMPTS = {
    "security": """You are a security code reviewer. Analyze the PR diff for security vulnerabilities only.
    Focus on: SQL injection, hardcoded secrets, authentication issues, exposed sensitive data, unsafe inputs.
    Ignore style, performance, and logic issues — only security.""",

    "bug": """You are a bug detection code reviewer. Analyze the PR diff for logical errors only.
    Focus on: null pointer risks, unhandled edge cases, incorrect logic, missing error handling, off by one errors.
    Ignore style, security, and performance issues — only bugs.""",

    "quality": """You are a code quality reviewer. Analyze the PR diff for readability issues only.
    Focus on: missing docstrings, bad variable names, functions too long, missing type hints, no comments.
    Ignore security, performance, and logic issues — only code quality.""",

    "optimization": """You are a performance optimization reviewer. Analyze the PR diff for performance issues only.
    Focus on: N+1 queries, O(n²) loops, missing caching, unnecessary DB calls, inefficient algorithms.
    Ignore style, security, and logic issues — only performance."""
}


def analyze_pr(diff: str, agent_type: str, past_findings: str = "") -> PRReview:
    """
    sends PR diff to groq/llama and gets back structured review findings.
    agent_type tells the model which aspect of code to focus on.
    past_findings: optional hints from review memory (embeddings.py) — issues this
    repo had before in similar code.
    """
    # get right prompt for this agent type
    system_prompt = AGENT_PROMPTS[agent_type]

    # framed as hints, not instructions: the model should only report what's actually in this diff
    memory_section = ""
    if past_findings:
        memory_section = f"""
Issues previously found in similar code in this repository. Use them only as hints for
what to look for — report an issue only if it is actually present in the diff below.
{past_findings}
"""

    # call groq api — send diff + instructions, get back JSON findings
    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",  # free powerful model on groq
        max_tokens=1000,
        messages=[
            {"role": "system", "content": system_prompt},  # sets agent role
            {
                "role": "user",
                "content": f"""Analyze this PR diff and return JSON only. No other text.
{memory_section}
PR Diff:
{diff}

Return ONLY this exact JSON format:
{{
    "findings": [
        {{
            "agent_type": "{agent_type}",
            "severity": "high/medium/low",
            "file_path": "path/to/file.py",
            "line_number": 42,
            "comment": "description of issue",
            "suggested_fix": "how to fix it"
        }}
    ],
    "summary": "brief overall summary"
}}

If no issues found return findings as empty list []."""
            }
        ]
    )

    # extract text from groq response — different structure than anthropic
    raw_text = response.choices[0].message.content

    try:
        # parse JSON string into python dictionary
        parsed = json.loads(raw_text)
        # validate shape with pydantic before saving to DB
        return PRReview(**parsed)
    except (json.JSONDecodeError, ValueError) as e:
        raise Exception(f"groq returned invalid JSON: {e}\nraw response: {raw_text}")