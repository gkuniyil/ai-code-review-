from unittest.mock import patch

from backend.analytics import QualityRuleEvaluator, get_performance_report, track_transaction


def _fresh_metrics():
    return {
        "total_reviews_processed": 0,
        "total_tokens_consumed": 0,
        "accumulated_latency_seconds": 0.0,
    }


def test_track_transaction_counts_actual_tokens():
    """Regression test: tokens used to be incremented by 1 per call instead of by the real count."""
    with patch.dict("backend.analytics.SYSTEM_METRICS", _fresh_metrics()):
        track_transaction(tokens=1200, duration=2.0)
        track_transaction(tokens=800, duration=4.0)

        report = get_performance_report()

    assert report.total_reviews == 2
    assert report.total_tokens == 2000
    assert report.avg_latency_ms == 3000.0


def test_safety_check_passes_normal_finding():
    assert QualityRuleEvaluator.verify_review_safety(
        "User input is concatenated into the SQL query", "Use a parameterized query"
    ) is True


def test_safety_check_rejects_blank_or_tiny_comment():
    assert QualityRuleEvaluator.verify_review_safety("   ", None) is False
    assert QualityRuleEvaluator.verify_review_safety("bad", None) is False


def test_safety_check_rejects_unbalanced_code_fence():
    assert QualityRuleEvaluator.verify_review_safety(
        "Missing null check before access", "```python\nif x is None:"
    ) is False


def test_safety_check_rejects_unprofessional_phrasing():
    assert QualityRuleEvaluator.verify_review_safety(
        "This is terrible code and should be rewritten", None
    ) is False
