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

def post_review_comment(
    repo_name: str,
    pr_number: int,
    file_path: str,    # which file to post comment 
    line_number: int,  # which line 
    comment: str,      # what to say 
    commit_sha: str    # which version of file 
) -> None:
    """posts one AI finding as comment on specific line of PR"""

    try:
        repo = github_client.get_repo(repo_name)
        pr = repo.get_pull(pr_number)
        commit = repo.get_commit(commit_sha) #fetch commit object so github knows which version of file to comment on 

        pr.create_review_comment(body=comment, 
                                 commit = commit, 
                                 path = file_path, 
                                 line = line_number)
    except GithubException as e:
        raise Exception(f"failed to post comment: {e.status} {e.data}")  # bubble up error so celery can retry
    
def post_pr_summary(repo_name: str, pr_number: int, summary: str) -> None:
    """posts overall review summary as general PR comment after all agents finish"""
    try:
        repo = github_client.get_repo(repo_name)  # find the repo
        pr = repo.get_pull(pr_number)             # find the PR
        pr.create_issue_comment(body=summary)     # post general comment on PR (not tied to specific line)

    except GithubException as e:
        raise Exception(f"failed to post summary: {e.status} {e.data}")  # bubble up so celery retries

def get_latest_commit_sha(repo_name: str, pr_number: int) -> str:
    """gets SHA of latest commit on PR — needed to attach line comments to correct file version"""
    try:
        repo = github_client.get_repo(repo_name)  # find the repo
        pr = repo.get_pull(pr_number)             # find the PR
        return pr.head.sha                         # head.sha = latest commit on this PR branch

    except GithubException as e:
        raise Exception(f"failed to get commit sha: {e.status} {e.data}")  # bubble up so celery retries
