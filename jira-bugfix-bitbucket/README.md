# Jira Bug Fix (Bitbucket)

End-to-end bug fix pipeline. Reads a Jira bug ticket, clones the Bitbucket repo,
implements the fix with Claude Code, opens a PR, and comments on the Jira ticket
with the PR link.

## Pipeline

```
Signal: fix-bug { issue_key, repo_url }
         |
    step_read_ticket       Jira agent reads the bug ticket details
         |
    step_clone_repo        Bitbucket agent clones the repository
         |
    step_implement_fix     Claude Code creates a branch, implements the fix, commits
         |
    step_push_branch       Bitbucket agent pushes the feature branch
         |
    step_create_pr         Bitbucket agent opens a PR with the fix
         |
    step_update_ticket     Jira agent comments on the ticket with the PR link
         |
      completed
```

The pipeline verifies the ticket has a `bug` label before proceeding.

## Required credentials

```bash
ANTHROPIC_API_KEY=sk-ant-...
BITBUCKET_USERNAME=your-username
BITBUCKET_TOKEN=your-app-password
JIRA_SITE=your-site.atlassian.net
JIRA_EMAIL=you@example.com
JIRA_API_TOKEN=your-api-token
```

## Quick start

1. Add credentials to your `.env` and start FAST with `docker compose up`
2. Load the space via the Studio (drag `workspace.yml`) or API:

```bash
CONFIG=$(python3 -c "import yaml,json; print(json.dumps(yaml.safe_load(open('jira-bugfix-bitbucket/workspace.yml'))))")
curl -s -X POST http://localhost:8080/api/workspaces/create \
  -H 'Content-Type: application/json' \
  -d "{\"config\":$CONFIG,\"workspaceName\":\"Jira Bug Fix (Bitbucket)\"}"
```

3. Trigger a bug fix:

```bash
curl -X POST http://localhost:8080/api/workspaces/<workspace-id>/signals/fix-bug \
  -H 'Content-Type: application/json' \
  -d '{
    "payload": {
      "issue_key": "PROJ-123",
      "repo_url": "https://bitbucket.org/workspace/repo"
    }
  }'
```

4. Open the Studio at **http://localhost:5200** to watch the execution.

## What happens

1. **Jira agent** reads the ticket — summary, description, labels, priority
2. **Bitbucket agent** clones the repo to an isolated workspace
3. **Claude Code** creates a `fix/<issue-key>` branch, explores the codebase,
   implements the fix, adds tests if applicable, and commits
4. **Bitbucket agent** pushes the branch and opens a PR
5. **Jira agent** comments on the ticket with a link to the PR

A human reviews the PR before merging — because ultimately, a person is
responsible for what gets shipped.
