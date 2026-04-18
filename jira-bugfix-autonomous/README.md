# Jira Bug Fix (Autonomous)

An autonomous coding agent that fixes Jira bugs and manages the full PR lifecycle. It writes fixes, opens pull requests, responds to CI failures, and handles reviewer comments—all without manual intervention.

## What it does

1. Finds Jira tickets labeled `ai-fix` in `To Do` status
2. Claims the ticket and implements a fix
3. Pushes a branch and opens a Bitbucket PR
4. Watches the PR and reacts automatically:
   - **CI fails** → Reads logs, writes a fix, pushes
   - **Reviewers comment `@friday ...`** → Makes the change and replies
   - **Three fix attempts fail** → Comments on Jira and stops

## Prerequisites

- Docker installed
- Jira access with tickets labeled `ai-fix`
- Bitbucket access
- The `jira-bugfix-pr-router` agent from this examples repo

## Quick start

### 1. Add the agent

Copy the `jira-bugfix-pr-router` agent folder into your `starter-agents` directory.

### 2. Start the platform

```bash
docker compose up
```

Wait for the container to finish starting.

### 3. Load the workspace

Open the Friday Studio, then drag `workspace.yml` into the interface to create your space.

### 4. Connect the Bitbucket webhook

1. In Friday Studio, open your space and scroll to **Signals**
2. Copy the `pr-event` webhook URL and secret
3. In Bitbucket, go to **Repository settings → Workflow → Webhooks**
4. Click **Add webhook**:
   - **Title**: `Friday pr-event`
   - **URL**: Paste the URL from Studio
   - **Secret**: Paste the secret from Studio
   - **Triggers**: Check **Pull Request** (Approved, Changes requested, Comment created, Merged) and **Build status** (Created, Updated)
5. Save

The workspace now listens for CI status changes, PR comments, and merges.

### 5. Trigger a fix manually (optional)

To test without waiting for webhooks:

1. Find a Jira ticket with the `ai-fix` label in `To Do` status
2. In Studio, find the `fix-bug` signal
3. Enter your **project key** (e.g., `PROJ`) and **Bitbucket repo URL**
4. Submit

The agent claims the ticket, implements the fix, and opens a PR.

## How it works

The workspace runs three jobs:

- **`fix-bug`** — Entry point. Finds tickets, implements fixes, opens PRs.
- **`handle-pr-event`** — Reacts to Bitbucket webhooks (CI, comments, merges).
- **`reconcile-prs`** — Cron job (every 10 minutes) that catches missed events.

The `jira-bugfix-pr-router` agent decides what to do with each PR event. It returns one of seven actions: fix CI, fix a comment, record a merge, escalate after three failed attempts, or drop non-actionable events.

## Workspace variants

This example includes two workspace files:

| File | Description |
|------|-------------|
| `workspace.yml` | Uses the Python router agent. All routing logic lives in one testable file. |
| `workspace.pre-router.yml` | Uses native Friday state machines. Routing logic lives inline in YAML. |

Start with `workspace.yml`—it keeps the logic in one place and is easier to modify.

## Tips

- Budget three Claude runs per ticket on the happy path: one to write the fix, plus up to two CI repair attempts.
- The `friday-prs` ledger tracks which PRs the workspace owns. Do not delete it while PRs are open.
- Comments without `@friday` or `/friday` are ignored.
