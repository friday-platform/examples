# FAST Examples

Starter spaces for [FAST](https://platform.hellofriday.ai/docs/) (Friday Agent Studio & Toolkit) — a configuration-driven agentic orchestration runtime. Each example is a self-contained `workspace.yml` that defines agents, jobs, and signals — ready to load into the Studio and run.

## Examples

- **[pr-review](pr-review/)** — Reviews a GitHub pull request, posts inline comments with findings. (GitHub)
- **[pr-review-bitbucket](pr-review-bitbucket/)** — Reviews a Bitbucket Cloud pull request, posts inline comments with findings. (Bitbucket)
- **[jira-bugfix-bitbucket](jira-bugfix-bitbucket/)** — Reads a Jira bug ticket, implements a fix, opens a Bitbucket pull request, and comments on the ticket. (Jira + Bitbucket)
- **[jira-bugfix-labeled](jira-bugfix-labeled/)** — Searches Jira for `ai-fix` labeled tickets, picks the highest-priority one, fixes it, opens a pull request, and transitions the ticket to Done. (Jira + Bitbucket)

## Quick start

See [STUDIO_QUICKSTART.md](STUDIO_QUICKSTART.md) for the full walkthrough:
Docker Compose setup, loading spaces, publishing skills, triggering jobs, and
connecting webhooks.

### TL;DR

```bash
# 1. Create .env with your API keys
cat > .env <<'EOF'
ANTHROPIC_API_KEY=sk-ant-...
GH_TOKEN=ghp_...
EOF

# 2. Start FAST
docker compose up

# 3. Open the Studio
open http://localhost:5200

# 4. Load a space via the Studio or API
curl -s -X POST http://localhost:8080/api/workspaces/create \
  -H 'Content-Type: application/json' \
  -d "{\"config\":$(python3 -c "import yaml,json; print(json.dumps(yaml.safe_load(open('pr-review/workspace.yml'))))"),\"workspaceName\":\"PR Review\"}"

# 5. Trigger a review
curl -X POST http://localhost:8080/api/workspaces/<workspace-id>/signals/review-pr \
  -H 'Content-Type: application/json' \
  -d '{"payload":{"pr_url":"https://github.com/owner/repo/pull/123"}}'
```

## Three building blocks

Each space is a `workspace.yml` with three building blocks:

- **Signals** — how external events kick off your jobs (webhooks, cron, manual triggers)
- **Agents** — built-in or custom agents that execute operations (Claude Code, GitHub, Bitbucket, Jira, and more)
- **Jobs** — workflows composed of agents, tools, skills, and data contracts that run step by step

## Prerequisites

- Docker with Docker Compose v2+
- [Anthropic API key](https://console.anthropic.com/) for Claude
- Integration credentials depending on the example (see table above)

## License

Apache 2.0 — see [LICENSE](LICENSE) for details.
