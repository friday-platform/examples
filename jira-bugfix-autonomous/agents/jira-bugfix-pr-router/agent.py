"""jira-bugfix-pr-router — deterministic PR routing decisions.

Pure-function router consumed by the jira-bugfix-autonomous workspace's
handle-pr-event and reconcile-prs FSM jobs. Given a normalized webhook
event (or a reconcile candidate) plus the raw friday-prs / pr-events
ledger rows and behavioral config, returns a Decision discriminated union
that tells the FSM exactly what to do next.

No capabilities: no ctx.llm, no ctx.http, no ctx.tools, no ctx.state.
The FSM passes raw ledger rows in as input; the router filters them
internally by pr_id before applying gates.

See docs/plans/2026-04-16-autonomous-workspace-pr-router-design.v3.md
§ Track 1 for the design contract.
"""

from dataclasses import dataclass

from friday_agent_sdk import agent, err, ok, parse_operation
from friday_agent_sdk._bridge import Agent  # noqa: F401 — componentize-py needs this
from friday_agent_sdk._result import ErrResult, OkResult


# ─────────────────────────────────────────────────────────────────
# Input dataclasses
# ─────────────────────────────────────────────────────────────────


@dataclass
class WebhookInput:
    operation: str
    event: dict
    friday_prs: list[dict]
    pr_events: list[dict]
    config: dict


@dataclass
class ReconcileInput:
    operation: str
    friday_pr: dict
    pr_events: list[dict]
    bb_state: dict
    config: dict


_OPERATION_SCHEMAS: dict[str, type] = {
    "webhook": WebhookInput,
    "reconcile": ReconcileInput,
}


_DEFAULT_CONFIG = {
    "ci_status_keys": ["BITBUCKET-PIPELINES"],
    "actionable_prefixes": ["@friday", "/friday"],
    "max_ci_consecutive_attempts_without_comment_reset": 3,
    "max_total_pushes_per_pr": 10,
}


# ─────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────


def _resolve_config(raw: dict | None) -> dict:
    raw = raw or {}
    return {**_DEFAULT_CONFIG, **raw}


def _row_ts(row: dict) -> str:
    """Return a row's timestamp, preferring the state-cache `_ts` field.

    FSM state cache decorates each row with `_ts` (ISO). Synthetic fixtures
    may only carry the embedded `ts` field on the payload itself. Prefer
    `_ts` when present so live data and fixtures stay aligned.
    """
    ts = row.get("_ts")
    if ts:
        return str(ts)
    return str(row.get("ts") or "")


def _filter_friday_prs(rows: list[dict], pr_id: str) -> list[dict]:
    pr_id = str(pr_id)
    out = []
    for row in rows:
        if str(row.get("pr_id") or "") == pr_id:
            out.append(row)
    return out


def _filter_pr_events(rows: list[dict], pr_id: str) -> list[dict]:
    pr_id = str(pr_id)
    out = []
    for row in rows:
        if str(row.get("pr_id") or "") == pr_id:
            out.append(row)
    return out


def _latest_friday_pr(rows: list[dict]) -> dict | None:
    latest = None
    latest_ts = ""
    for row in rows:
        ts = _row_ts(row)
        if latest is None or ts > latest_ts:
            latest = row
            latest_ts = ts
    return latest


def _is_actionable(body: object, prefixes: list[str]) -> bool:
    if not isinstance(body, str):
        return False
    trimmed = body.lstrip()
    for prefix in prefixes:
        if trimmed.startswith(prefix):
            return True
    return False


def _has_lifecycle_row(pr_events: list[dict], row_type: str) -> bool:
    for ev in pr_events:
        if ev.get("type") == row_type:
            return True
    return False


def _extract_sha_from_commit_url(commit_url: object) -> str:
    """Extract the terminal commit sha from a BB commit URL."""
    if not isinstance(commit_url, str):
        return ""
    # .../commit/<sha> — strip any trailing slash first
    stripped = commit_url.rstrip("/")
    idx = stripped.rfind("/commit/")
    if idx < 0:
        return ""
    tail = stripped[idx + len("/commit/") :]
    # sha is hex; reject obviously bad tails
    if not tail:
        return ""
    for ch in tail:
        if ch not in "0123456789abcdefABCDEF":
            return ""
    return tail


def _count_iterations(pr_events: list[dict]) -> tuple[int, int]:
    """Return (total_pushes, ci_since_comment).

    total_pushes counts every {type:'fix_pushed'} row for the PR.
    ci_since_comment counts {type:'fix_pushed', trigger:'ci'} rows that
    arrived after the most recent {type:'fix_pushed', trigger:'comment'}
    row (by timestamp).
    """
    total_pushes = 0
    last_comment_ts = ""
    ci_pushes: list[str] = []
    for row in pr_events:
        if row.get("type") != "fix_pushed":
            continue
        total_pushes += 1
        trigger = row.get("trigger")
        ts = _row_ts(row)
        if trigger == "comment":
            if not last_comment_ts or ts > last_comment_ts:
                last_comment_ts = ts
        elif trigger == "ci":
            ci_pushes.append(ts)
    ci_since_comment = 0
    for ts in ci_pushes:
        if not last_comment_ts or ts > last_comment_ts:
            ci_since_comment += 1
    return total_pushes, ci_since_comment


# ─────────────────────────────────────────────────────────────────
# Decision constructors
# ─────────────────────────────────────────────────────────────────


def _drop(reason: str) -> dict:
    return {"action": "drop", "reason": reason}


def _noop() -> dict:
    return {"action": "noop"}


def _fix_ci(pr_record: dict, sha: str) -> dict:
    return {
        "action": "fix_ci",
        "pr_id": str(pr_record.get("pr_id") or ""),
        "sha": sha,
        "branch": str(pr_record.get("branch") or ""),
        "repo_slug": str(pr_record.get("repo_slug") or ""),
        "repo_url": str(pr_record.get("repo_url") or ""),
        "ticket_key": str(pr_record.get("ticket_key") or ""),
        "source": "ci",
    }


def _fix_comment(
    pr_record: dict,
    sha: str,
    comment_id: object,
    comment_body: str,
    thread: dict,
) -> dict:
    return {
        "action": "fix_comment",
        "pr_id": str(pr_record.get("pr_id") or ""),
        "sha": sha,
        "branch": str(pr_record.get("branch") or ""),
        "repo_slug": str(pr_record.get("repo_slug") or ""),
        "repo_url": str(pr_record.get("repo_url") or ""),
        "ticket_key": str(pr_record.get("ticket_key") or ""),
        "comment_id": comment_id,
        "comment_body": comment_body,
        "thread": thread,
        "source": "comment",
    }


def _record_merged(pr_id: str, merge_commit: str) -> dict:
    return {
        "action": "record_merged",
        "pr_id": pr_id,
        "merge_commit": merge_commit,
    }


def _escalate(pr_id: str, ticket_key: str, reason: dict) -> dict:
    return {
        "action": "escalate",
        "pr_id": pr_id,
        "ticket_key": ticket_key,
        "reason": reason,
    }


# ─────────────────────────────────────────────────────────────────
# Core decision function — webhook path
# ─────────────────────────────────────────────────────────────────


def decide(event: dict, friday_prs: list[dict], pr_events: list[dict], config: dict) -> dict:
    """Pure routing decision for a single normalized event.

    Also called internally by the reconcile path to filter candidates that
    the webhook path would drop — parity by construction.
    """
    cfg = _resolve_config(config)
    ci_status_keys = list(cfg.get("ci_status_keys") or [])
    actionable_prefixes = list(cfg.get("actionable_prefixes") or [])
    max_ci = int(cfg.get("max_ci_consecutive_attempts_without_comment_reset") or 0)
    max_total = int(cfg.get("max_total_pushes_per_pr") or 0)

    event_type = event.get("event_type")

    # ── ci_status ────────────────────────────────────────────────
    if event_type == "ci_status":
        sha = _extract_sha_from_commit_url(event.get("commit_url"))
        if not sha:
            return _drop("not_friday_owned")

        # Find friday-prs rows whose sha matches the event.
        matching_by_sha = []
        for row in friday_prs:
            if row.get("sha") == sha:
                matching_by_sha.append(row)
        if not matching_by_sha:
            # Preprocessor handles the force-push recovery lookup; if it
            # made it here without a matching row the PR isn't ours.
            return _drop("not_friday_owned")

        owned = _latest_friday_pr(matching_by_sha)
        if owned is None:
            return _drop("not_friday_owned")
        pr_id = str(owned.get("pr_id") or "")

        pr_friday_prs = _filter_friday_prs(friday_prs, pr_id)
        pr_events_for_id = _filter_pr_events(pr_events, pr_id)

        # Stale-SHA gate against the latest sha for this pr_id.
        latest = _latest_friday_pr(pr_friday_prs)
        if latest is not None and latest.get("sha") != sha:
            return _drop("stale_sha")

        # Lifecycle dedup — merged/escalated/already-triaged.
        if _has_lifecycle_row(pr_events_for_id, "merged"):
            return _drop("merged")
        if _has_lifecycle_row(pr_events_for_id, "escalated"):
            return _drop("escalated")
        for ev in pr_events_for_id:
            if ev.get("type") == "ci_triaged" and ev.get("sha") == sha:
                return _drop("already_triaged")

        # Dispatch filter — only act on FAILED pipelines we care about.
        state = event.get("state")
        key = event.get("key")
        type_hint = event.get("type")
        key_matches = key in ci_status_keys or (
            "BITBUCKET-PIPELINES" in ci_status_keys and type_hint == "build"
        )
        if state != "FAILED" or not key_matches:
            return _noop()

        # Iteration-budget gate.
        total_pushes, ci_since_comment = _count_iterations(pr_events_for_id)
        if max_total and total_pushes >= max_total:
            return _escalate(
                pr_id,
                str(owned.get("ticket_key") or ""),
                {
                    "trigger": "ci",
                    "total_pushes": total_pushes,
                    "ci_since_comment": ci_since_comment,
                    "cap": "total_pushes",
                },
            )
        if max_ci and ci_since_comment >= max_ci:
            return _escalate(
                pr_id,
                str(owned.get("ticket_key") or ""),
                {
                    "trigger": "ci",
                    "total_pushes": total_pushes,
                    "ci_since_comment": ci_since_comment,
                    "cap": "ci_since_comment",
                },
            )

        return _fix_ci(owned, sha)

    # ── comment ──────────────────────────────────────────────────
    if event_type == "comment":
        pr_id_raw = event.get("pr_id")
        if pr_id_raw is None or pr_id_raw == "":
            return _drop("pr_not_found")
        pr_id = str(pr_id_raw)

        pr_friday_prs = _filter_friday_prs(friday_prs, pr_id)
        owned = _latest_friday_pr(pr_friday_prs)
        if owned is None:
            return _drop("not_friday_owned")

        pr_events_for_id = _filter_pr_events(pr_events, pr_id)
        sha = str(event.get("commit_sha") or owned.get("sha") or "")

        # Actionability check — needed before lifecycle gate because an
        # actionable comment re-activates an escalated PR (User Story 16).
        comment_body = event.get("comment_body") or ""
        actionable = _is_actionable(comment_body, actionable_prefixes)

        if _has_lifecycle_row(pr_events_for_id, "merged"):
            return _drop("merged")
        if _has_lifecycle_row(pr_events_for_id, "escalated") and not actionable:
            return _drop("escalated")

        if not actionable:
            return _drop("not_actionable")

        # Comment-triggered triage re-arms the CI budget — only the absolute
        # total_pushes cap applies.
        total_pushes, ci_since_comment = _count_iterations(pr_events_for_id)
        if max_total and total_pushes >= max_total:
            return _escalate(
                pr_id,
                str(owned.get("ticket_key") or ""),
                {
                    "trigger": "comment",
                    "total_pushes": total_pushes,
                    "ci_since_comment": 0,
                    "cap": "total_pushes",
                },
            )

        return _fix_comment(
            owned,
            sha,
            event.get("comment_id"),
            comment_body,
            event.get("thread") or {},
        )

    # ── merged ──────────────────────────────────────────────────
    if event_type == "merged":
        pr_id_raw = event.get("pr_id")
        if pr_id_raw is None or pr_id_raw == "":
            return _drop("pr_not_found")
        pr_id = str(pr_id_raw)

        pr_friday_prs = _filter_friday_prs(friday_prs, pr_id)
        owned = _latest_friday_pr(pr_friday_prs)
        if owned is None:
            return _drop("not_friday_owned")

        pr_events_for_id = _filter_pr_events(pr_events, pr_id)
        if _has_lifecycle_row(pr_events_for_id, "merged"):
            return _drop("merged")

        merge_commit = str(event.get("merge_commit") or "")
        return _record_merged(pr_id, merge_commit)

    # Unknown event type — treat as not-friday-owned (defensive; the signal
    # schema's enum bounds event_type at the edge).
    return _drop("not_friday_owned")


# ─────────────────────────────────────────────────────────────────
# Reconcile operation
# ─────────────────────────────────────────────────────────────────


def _reconcile(cfg: ReconcileInput) -> OkResult | ErrResult:
    config = _resolve_config(cfg.config)
    ci_status_keys = list(config.get("ci_status_keys") or [])
    actionable_prefixes = list(config.get("actionable_prefixes") or [])

    friday_pr = cfg.friday_pr or {}
    pr_id = str(friday_pr.get("pr_id") or "")
    sha = str(friday_pr.get("sha") or "")
    repo_slug = str(friday_pr.get("repo_slug") or "")

    pr_events_for_id = _filter_pr_events(cfg.pr_events or [], pr_id)

    # Lifecycle terminal-state gate — skip entirely if merged or escalated.
    if _has_lifecycle_row(pr_events_for_id, "merged"):
        return ok({"events": []})
    if _has_lifecycle_row(pr_events_for_id, "escalated"):
        return ok({"events": []})

    bb = cfg.bb_state or {}
    statuses = bb.get("commit_statuses") or []
    threads = bb.get("threads") or []
    pr_view = bb.get("pr_view") or {}

    events: list[dict] = []

    # ── CI status ──────────────────────────────────────────────
    ci_failed = False
    for s in statuses:
        if not isinstance(s, dict):
            continue
        if s.get("state") != "FAILED":
            continue
        key = s.get("key")
        if key in ci_status_keys or (
            "BITBUCKET-PIPELINES" in ci_status_keys and s.get("type") == "build"
        ):
            ci_failed = True
            break
    if ci_failed:
        commit_url = (
            f"https://api.bitbucket.org/2.0/repositories/{repo_slug}/commit/{sha}"
        )
        candidate = {
            "event_type": "ci_status",
            "state": "FAILED",
            "key": "BITBUCKET-PIPELINES",
            "commit_url": commit_url,
            "_source": "reconciler",
        }
        # Parity check — only fan out if the webhook path would actually
        # dispatch this event. Drops (stale-sha, already-triaged, escalated)
        # must not be resurrected through the reconciler.
        decision = decide(
            candidate,
            [friday_pr],
            cfg.pr_events or [],
            config,
        )
        if decision.get("action") not in ("drop", "noop"):
            events.append(candidate)

    # ── Comments ──────────────────────────────────────────────
    for thread in threads:
        if not isinstance(thread, dict):
            continue
        root = thread.get("root") or {}
        replies = thread.get("replies") or []
        candidates = []
        if isinstance(root, dict) and root.get("comment_id") is not None:
            candidates.append(root)
        for r in replies:
            if isinstance(r, dict):
                candidates.append(r)

        for c in candidates:
            body = c.get("body")
            if not _is_actionable(body, actionable_prefixes):
                continue
            comment_id = c.get("comment_id")
            if comment_id is None:
                continue
            # Dedup — any pr-events row for this comment_id means we've
            # already handled it.
            seen = False
            for pev in pr_events_for_id:
                if str(pev.get("comment_id") or "") == str(comment_id):
                    seen = True
                    break
            if seen:
                continue
            candidate = {
                "event_type": "comment",
                "pr_id": pr_id,
                "commit_sha": sha,
                "comment_id": comment_id,
                "comment_body": body,
                "comment_path": c.get("path") or "",
                "author": c.get("author") or "",
                "author_account_id": c.get("author_account_id") or "",
                "_source": "reconciler",
            }
            decision = decide(
                candidate,
                [friday_pr],
                cfg.pr_events or [],
                config,
            )
            if decision.get("action") not in ("drop", "noop"):
                events.append(candidate)

    # ── Merge ────────────────────────────────────────────────
    pr_state = ""
    if isinstance(pr_view, dict):
        pr_state = (
            pr_view.get("state")
            or (pr_view.get("pr") or {}).get("state")
            or ""
        )
    if isinstance(pr_state, str) and pr_state.upper() == "MERGED":
        already = False
        for mev in pr_events_for_id:
            if mev.get("type") == "merged":
                already = True
                break
        if not already:
            merge_commit = (
                pr_view.get("merge_commit")
                or (pr_view.get("pr") or {}).get("merge_commit")
                or ""
            )
            candidate = {
                "event_type": "merged",
                "pr_id": pr_id,
                "merge_commit": merge_commit,
                "_source": "reconciler",
            }
            decision = decide(
                candidate,
                [friday_pr],
                cfg.pr_events or [],
                config,
            )
            if decision.get("action") not in ("drop", "noop"):
                events.append(candidate)

    return ok({"events": events})


# ─────────────────────────────────────────────────────────────────
# Webhook operation
# ─────────────────────────────────────────────────────────────────


def _webhook(cfg: WebhookInput) -> OkResult | ErrResult:
    decision = decide(
        cfg.event or {},
        cfg.friday_prs or [],
        cfg.pr_events or [],
        cfg.config or {},
    )
    return ok(decision)


# ─────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────


@agent(
    id="jira-bugfix-pr-router",
    version="0.1.0",
    description=(
        "Deterministic router for jira-bugfix-autonomous PR events. Given a "
        "normalized webhook event or reconcile candidate plus ledger rows and "
        "config, returns the next FSM action. Pure function — no capabilities."
    ),
)
def execute(prompt: str, ctx) -> OkResult | ErrResult:
    try:
        config = parse_operation(prompt, _OPERATION_SCHEMAS)
    except ValueError as e:
        return err(str(e))

    match config.operation:
        case "webhook":
            return _webhook(config)
        case "reconcile":
            return _reconcile(config)
        case other:
            return err(f"Unknown operation: {other}")
