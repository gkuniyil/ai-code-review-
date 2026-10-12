from github import Github #main class 
from github.GithubException import GithubException, RateLimitExceededException #handles errors 
from backend.config import settings #token to authenticate with github 

github_client = Github(settings.github_token) # creates authetnicated connection to GitHub using token 

def fetch_pr_diff(repo_name: str, pr_number: int) -> str:
    """fetches all code changes from a PR as one combined string for agents to analyze"""
    try:
        repo = github_client.get_repo(repo_name)  # get repo object
        pr = repo.get_pull(pr_number)             # get PR object
        files = pr.get_files()                    # get all changed files

        diff = ""
        for file in files:          # loop through every changed file
            if file.patch:          # skip binary files with no patch
                diff += f"\n--- {file.filename} ---\n"  # add filename header
                diff += file.patch  # append this file's code changes to diff string

        return diff  # ← must be OUTSIDE the for loop, after all files processed

    except RateLimitExceededException:
        raise Exception("github rate limit exceeded, retry later")
    except GithubException as e:
        raise Exception(f"github api error: {e.status} {e.data}")

def new_file_lines(diff: str) -> dict[str, dict[int, str]]:
    """
    maps each file in the diff to {new-file line number: code on that line}, for every
    line that appears in the diff hunks. expects the "--- filename ---" format built
    by fetch_pr_diff.
    """
    lines_by_file: dict[str, dict[int, str]] = {}
    current_file = None
    new_line = 0

    for line in diff.splitlines():
        if line.startswith("--- ") and line.endswith(" ---"):
            current_file = line[4:-4]                        # strip "--- " and " ---"
            lines_by_file[current_file] = {}
        elif current_file is None:
            continue
        elif line.startswith("@@"):
            # hunk header: "@@ -10,4 +12,6 @@" → new-file lines start at 12
            new_start = line.split("+", 1)[1].split(" ", 1)[0].split(",")[0]
            new_line = int(new_start)
        elif line.startswith("+") or line.startswith(" "):
            lines_by_file[current_file][new_line] = line[1:]  # added ("+") or unchanged context (" ") line, prefix stripped
            new_line += 1
        # anything else — removed ("-") lines, "\ No newline" markers, the blank
        # separator fetch_pr_diff puts between files — isn't a line in the new file

    return lines_by_file


def commentable_lines(diff: str) -> dict[str, set[int]]:
    """
    maps each file in the diff to the new-file line numbers github will accept comments on.
    github rejects (422) inline comments on lines outside the diff hunks — and the LLM
    sometimes invents line numbers — so findings are checked against this before posting.
    """
    return {path: set(lines) for path, lines in new_file_lines(diff).items()}


def code_snippet(diff: str, file_path: str, line_number: int | None, context: int = 2) -> str | None:
    """
    the code a finding points at: its line plus `context` lines either side.
    returns None if the line isn't in the diff (e.g. an LLM-invented line number).
    """
    lines = new_file_lines(diff).get(file_path, {})
    if line_number not in lines:
        return None
    nearby = range(line_number - context, line_number + context + 1)
    return "\n".join(lines[n] for n in nearby if n in lines)


def diff_chunks(diff: str, chunk_size: int = 8) -> list[str]:
    """
    splits the new-file code in a diff into small chunks of consecutive lines.
    the embedding model only reads ~256 tokens, so a whole diff can't be one query —
    each chunk is searched separately against past findings' code.
    """
    chunks = []
    for lines in new_file_lines(diff).values():
        numbers = sorted(lines)
        for start in range(0, len(numbers), chunk_size):
            chunk = "\n".join(lines[n] for n in numbers[start:start + chunk_size])
            if chunk.strip():
                chunks.append(chunk)
    return chunks


def post_review(
    repo_name: str,
    pr_number: int,
    commit_sha: str,      # which version of the files the comments refer to
    body: str,            # overall summary shown at the top of the review
    comments: list[dict]  # inline comments: [{"path": ..., "line": ..., "body": ...}]
) -> int:
    """
    posts the summary + all inline comments as ONE github review.
    one api call means it either fully succeeds or fully fails — a retry can never
    leave half the comments posted and then post them again.
    returns the review id so the caller can record that posting already happened.
    """
    try:
        repo = github_client.get_repo(repo_name)
        pr = repo.get_pull(pr_number)
        commit = repo.get_commit(commit_sha)  # fetch commit object so github knows which version of file to comment on

        review = pr.create_review(
            commit=commit,
            body=body,
            event="COMMENT",  # leave feedback without approving or blocking the PR
            comments=comments
        )
        return review.id
    except GithubException as e:
        raise Exception(f"failed to post review: {e.status} {e.data}")  # bubble up so celery retries

def get_latest_commit_sha(repo_name: str, pr_number: int) -> str:
    """gets SHA of latest commit on PR — needed to attach line comments to correct file version"""
    try:
        repo = github_client.get_repo(repo_name)  # find the repo
        pr = repo.get_pull(pr_number)             # find the PR
        return pr.head.sha                         # head.sha = latest commit on this PR branch

    except GithubException as e:
        raise Exception(f"failed to get commit sha: {e.status} {e.data}")  # bubble up so celery retries
