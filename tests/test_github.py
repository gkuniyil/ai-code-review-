from unittest.mock import MagicMock, patch

import pytest
from github.GithubException import GithubException, RateLimitExceededException

from backend import github_client as gc


def _fake_file(filename: str, patch_text):
    f = MagicMock()
    f.filename = filename
    f.patch = patch_text
    return f


@patch("backend.github_client.github_client")
def test_fetch_pr_diff_combines_all_changed_files(mock_client):
    mock_repo = MagicMock()
    mock_pr = MagicMock()
    mock_pr.get_files.return_value = [
        _fake_file("a.py", "+print('a')"),
        _fake_file("b.py", "+print('b')"),
    ]
    mock_repo.get_pull.return_value = mock_pr
    mock_client.get_repo.return_value = mock_repo

    diff = gc.fetch_pr_diff("octocat/hello-world", 42)

    assert "--- a.py ---" in diff
    assert "--- b.py ---" in diff
    assert "+print('a')" in diff
    assert "+print('b')" in diff
    mock_client.get_repo.assert_called_once_with("octocat/hello-world")
    mock_repo.get_pull.assert_called_once_with(42)


@patch("backend.github_client.github_client")
def test_fetch_pr_diff_skips_files_with_no_patch(mock_client):
    """Binary files have no .patch — they should be skipped, not crash the join."""
    mock_repo = MagicMock()
    mock_pr = MagicMock()
    mock_pr.get_files.return_value = [_fake_file("image.png", None)]
    mock_repo.get_pull.return_value = mock_pr
    mock_client.get_repo.return_value = mock_repo

    diff = gc.fetch_pr_diff("octocat/hello-world", 42)

    assert diff == ""


@patch("backend.github_client.github_client")
def test_fetch_pr_diff_raises_readable_error_on_rate_limit(mock_client):
    mock_client.get_repo.side_effect = RateLimitExceededException(403, {}, {})

    with pytest.raises(Exception, match="rate limit"):
        gc.fetch_pr_diff("octocat/hello-world", 42)


@patch("backend.github_client.github_client")
def test_fetch_pr_diff_wraps_generic_github_errors(mock_client):
    mock_client.get_repo.side_effect = GithubException(404, {"message": "Not Found"}, {})

    with pytest.raises(Exception, match="github api error"):
        gc.fetch_pr_diff("octocat/hello-world", 42)


@patch("backend.github_client.github_client")
def test_get_latest_commit_sha_returns_head_sha(mock_client):
    mock_repo = MagicMock()
    mock_pr = MagicMock()
    mock_pr.head.sha = "abc123"
    mock_repo.get_pull.return_value = mock_pr
    mock_client.get_repo.return_value = mock_repo

    sha = gc.get_latest_commit_sha("octocat/hello-world", 42)

    assert sha == "abc123"


@patch("backend.github_client.github_client")
def test_post_review_comment_bubbles_up_errors_for_celery_retry(mock_client):
    """
    tasks.py relies on these functions raising (not swallowing) errors so that
    Celery's retry logic in review_pr() can catch them.
    """
    mock_client.get_repo.side_effect = GithubException(422, {"message": "bad"}, {})

    with pytest.raises(Exception, match="failed to post comment"):
        gc.post_review_comment("octocat/hello-world", 42, "a.py", 1, "comment", "sha")


@patch("backend.github_client.github_client")
def test_post_pr_summary_bubbles_up_errors_for_celery_retry(mock_client):
    mock_client.get_repo.side_effect = GithubException(422, {"message": "bad"}, {})

    with pytest.raises(Exception, match="failed to post summary"):
        gc.post_pr_summary("octocat/hello-world", 42, "summary text")
