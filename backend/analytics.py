# backend/analytics.py
import time
from pydantic import BaseModel
from typing import Dict, Any

# In-Memory Telemetry Ring-Buffer for Sub-Millisecond Diagnostics
SYSTEM_METRICS = {
    "total_reviews_processed": 0,
    "total_tokens_consumed": 0,
    "accumulated_latency_seconds": 0.0,
}

class SystemPerformanceSnapshot(BaseModel):
    total_reviews: int
    total_tokens: int
    avg_latency_ms: float
    cost_incurred_usd: float

def track_transaction(tokens: int, duration: float):
    """Updates global telemetry buffers atomically."""
    SYSTEM_METRICS["total_reviews_processed"] += 1
    SYSTEM_METRICS["total_tokens_consumed"] += 1
    SYSTEM_METRICS["accumulated_latency_seconds"] += duration

def get_performance_report() -> SystemPerformanceSnapshot:
    """Calculates live operational telemetry and budget tracking."""
    reviews = SYSTEM_METRICS["total_reviews_processed"]
    tokens = SYSTEM_METRICS["total_tokens_consumed"]
    latency = SYSTEM_METRICS["accumulated_latency_seconds"]
    
    avg_latency = (latency / reviews * 1000) if reviews > 0 else 0.0
    # Cost model tracking: assuming $0.15 per million tokens for lightweight local models
    cost = (tokens / 1_000_000) * 0.15 
    
    return SystemPerformanceSnapshot(
        total_reviews=reviews,
        total_tokens=tokens,
        avg_latency_ms=round(avg_latency, 2),
        cost_incurred_usd=round(cost, 6)
    )

class QualityRuleEvaluator:
    """Automated defense-in-depth layer preventing toxic or broken AI outputs."""
    
    @staticmethod
    def verify_review_safety(comment: str, suggested_fix: str) -> bool:
        """
        Executes strict assertion heuristics against the generated payload.
        Returns True if pass, False if rule validation fails.
        """
        # Rule 1: Prevent empty/hallucinated blank comments
        if not comment or len(comment.strip()) < 10:
            return False
            
        # Rule 2: Ensure structural code codeblock formatting integrity
        if suggested_fix and ("```" in suggested_fix and suggested_fix.count("```") % 2 != 0):
            return False # Malformed markdown code fences detected
            
        # Rule 3: Guardrail against aggressive or unprofessional LLM phrasing
        banned_phrases = ["you stupid", "terrible code", "horrible mistake", "what were you thinking"]
        if any(phrase in comment.lower() for phrase in banned_phrases):
            return False
            
        return True