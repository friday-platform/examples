# PR Code Review (Bitbucket)

Automated pull request code review for Bitbucket Cloud. Accepts a Bitbucket PR
URL, clones the repository, performs a thorough code review with Claude Code, and
posts structured inline comments back on the PR.

## Pipeline

```
Signal: review-pr { pr_url }
         |
    step_clone_repo        Clones repo, checks out PR branch, reads conventions
         |
    step_review_pr         Reads full diff + changed files, reviews against 6 criteria
         |
    step_post_review       Formats findings as inline comments, posts to Bitbucket
         |
      completed
```

Also supports a **continue-review** signal that picks up where the last review
left off — responds to author replies on existing threads and reviews new
changes.

## Required credentials

```bash
ANTHROPIC_API_KEY=sk-ant-...
BITBUCKET_USERNAME=your-username
BITBUCKET_TOKEN=your-app-password
```

The Bitbucket app password needs `repository:read`, `repository:write`, and
`pullrequest:write` permissions.

## Quick start

1. Add credentials to your `.env` and start FAST with `docker compose up`
2. Publish the `@tempest/pr-code-review` skill via the Studio (drag the `skill/`
   folder) or API
3. Load the space via the Studio (drag `workspace.yml`) or API:

```bash
CONFIG=$(python3 -c "import yaml,json; print(json.dumps(yaml.safe_load(open('pr-review-bitbucket/workspace.yml'))))")
curl -s -X POST http://localhost:8080/api/workspaces/create \
  -H 'Content-Type: application/json' \
  -d "{\"config\":$CONFIG,\"workspaceName\":\"PR Review (Bitbucket)\"}"
```

4. Trigger a review:

```bash
curl -X POST http://localhost:8080/api/workspaces/<workspace-id>/signals/review-pr \
  -H 'Content-Type: application/json' \
  -d '{"payload":{"pr_url":"https://bitbucket.org/workspace/repo/pull-requests/123"}}'
```

5. Open the Studio at **http://localhost:5200** to watch the execution.

## Review criteria

| Category | What it catches |
|---|---|
| Correctness | Logic errors, off-by-one, null/undefined, race conditions |
| Security | Injection, auth bypass, secrets in code, OWASP Top 10 |
| Performance | N+1 queries, blocking I/O, unbounded loops, missing indexes |
| Error handling | Swallowed errors, missing validation, leaked internals |
| Testing | Missing coverage, untested edge cases, brittle mocks |
| Style | Convention violations, dead code, naming inconsistencies |
