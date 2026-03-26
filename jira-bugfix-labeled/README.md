# Jira Bug Fix (Labeled)

Autonomous bug fix pipeline that picks up Jira tickets labeled `ai-fix`.
Searches a project for the highest-priority bug in "To Do" status, claims it,
implements the fix, opens a Bitbucket PR, comments on the ticket, and transitions
it to Done.

## Pipeline

```
Signal: process-labeled-bugs { project_key, repo_url }
         |
    step_search_tickets    Jira agent searches for ai-fix tickets in To Do
         |
    step_claim_ticket      Jira agent transitions the ticket to In Progress
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
    step_transition_review Jira agent transitions the ticket to Done
         |
      completed
```

If no `ai-fix` tickets are found in "To Do", the pipeline completes immediately
without error.

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
CONFIG=$(python3 -c "import yaml,json; print(json.dumps(yaml.safe_load(open('jira-bugfix-labeled/workspace.yml'))))")
curl -s -X POST http://localhost:8080/api/workspaces/create \
  -H 'Content-Type: application/json' \
  -d "{\"config\":$CONFIG,\"workspaceName\":\"Jira Bug Fix (Labeled)\"}"
```

3. Trigger the pipeline:

```bash
curl -X POST http://localhost:8080/api/workspaces/<workspace-id>/signals/process-labeled-bugs \
  -H 'Content-Type: application/json' \
  -d '{
    "payload": {
      "project_key": "PROJ",
      "repo_url": "https://bitbucket.org/workspace/repo"
    }
  }'
```

4. Open the Studio at **http://localhost:5200** to watch the execution.

## How it differs from jira-bugfix-bitbucket

| | jira-bugfix-bitbucket | jira-bugfix-labeled |
|---|---|---|
| **Input** | Specific `issue_key` + `repo_url` | `project_key` + `repo_url` — finds the ticket automatically |
| **Ticket selection** | You choose the ticket | Picks highest-priority `ai-fix` ticket in "To Do" |
| **Claiming** | No status change | Transitions ticket to "In Progress" before starting |
| **Completion** | Comments with PR link | Comments with PR link AND transitions to "Done" |

This makes the labeled variant ideal for cron-triggered automation — point it at
a project and let it continuously process the backlog.
