"""Unit tests for jira-bugfix-pr-router.

The router is a pure function — no daemon, no FSM, no database. Each test
builds the minimum ledger and event fixture needed to trigger exactly one
decision branch.
"""

import json

import pytest

from agent import (
    ReconcileInput,
    WebhookInput,
    _count_iterations,
    _reconcile,
    decide,
)
from friday_agent_sdk._result import ErrResult, OkResult


# ─────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────

REPO = "ws/repo"
REPO_URL = "https://bitbucket.org/ws/repo"
SHA_A = "a" * 12
SHA_B = "b" * 12
SHA_C = "c" * 12
COMMIT_URL_A = f"https://api.bitbucket.org/2.0/repositories/{REPO}/commit/{SHA_A}"
COMMIT_URL_B = f"https://api.bitbucket.org/2.0/repositories/{REPO}/commit/{SHA_B}"


def friday_pr_row(
    pr_id="42",
    sha=SHA_A,
    branch="fix/proj-1",
    ticket_key="PROJ-1",
    repo_slug=REPO,
    repo_url=REPO_URL,
    ts="2026-04-16T00:00:00Z",
):
    return {
        "pr_id": pr_id,
        "sha": sha,
        "branch": branch,
        "ticket_key": ticket_key,
        "repo_slug": repo_slug,
        "repo_url": repo_url,
        "_ts": ts,
    }


def pr_event_row(
    *,
    pr_id="42",
    type,
    sha="",
    trigger="",
    comment_id=None,
    ts="2026-04-16T00:10:00Z",
    **extra,
):
    row = {"pr_id": pr_id, "type": type, "_ts": ts}
    if sha:
        row["sha"] = sha
    if trigger:
        row["trigger"] = trigger
    if comment_id is not None:
        row["comment_id"] = comment_id
    row.update(extra)
    return row


def ci_event(sha=SHA_A, state="FAILED", key="BITBUCKET-PIPELINES"):
    return {
        "event_type": "ci_status",
        "state": state,
        "key": key,
        "commit_url": f"https://api.bitbucket.org/2.0/repositories/{REPO}/commit/{sha}",
    }


def comment_event(pr_id="42", body="@friday please fix", comment_id=7, commit_sha=""):
    return {
        "event_type": "comment",
        "pr_id": pr_id,
        "comment_id": comment_id,
        "comment_body": body,
        "commit_sha": commit_sha,
        "author": "alice",
    }


def merged_event(pr_id="42", merge_commit=SHA_C):
    return {
        "event_type": "merged",
        "pr_id": pr_id,
        "merge_commit": merge_commit,
    }


# ─────────────────────────────────────────────────────────────────
# Gate rejection paths — drop reasons
# ─────────────────────────────────────────────────────────────────


class TestDropReasons:
    def test_ci_status_no_sha_in_commit_url(self):
        event = {"event_type": "ci_status", "commit_url": "https://bitbucket.org/no/commit"}
        decision = decide(event, [friday_pr_row()], [], {})
        assert decision == {"action": "drop", "reason": "not_friday_owned"}

    def test_ci_status_sha_not_in_friday_prs(self):
        event = ci_event(sha="d" * 12)
        decision = decide(event, [friday_pr_row(sha=SHA_A)], [], {})
        assert decision == {"action": "drop", "reason": "not_friday_owned"}

    def test_ci_status_stale_sha(self):
        # Latest row for pr_id=42 has sha=SHA_B; event fires on the older SHA_A.
        rows = [
            friday_pr_row(sha=SHA_A, ts="2026-04-16T00:00:00Z"),
            friday_pr_row(sha=SHA_B, ts="2026-04-16T01:00:00Z"),
        ]
        decision = decide(ci_event(sha=SHA_A), rows, [], {})
        assert decision == {"action": "drop", "reason": "stale_sha"}

    def test_ci_status_merged_lifecycle(self):
        rows = [friday_pr_row()]
        events = [pr_event_row(type="merged")]
        decision = decide(ci_event(), rows, events, {})
        assert decision == {"action": "drop", "reason": "merged"}

    def test_ci_status_escalated_lifecycle(self):
        rows = [friday_pr_row()]
        events = [pr_event_row(type="escalated")]
        decision = decide(ci_event(), rows, events, {})
        assert decision == {"action": "drop", "reason": "escalated"}

    def test_ci_status_already_triaged_for_sha(self):
        rows = [friday_pr_row()]
        events = [pr_event_row(type="ci_triaged", sha=SHA_A)]
        decision = decide(ci_event(), rows, events, {})
        assert decision == {"action": "drop", "reason": "already_triaged"}

    def test_ci_status_successful_is_noop(self):
        rows = [friday_pr_row()]
        decision = decide(ci_event(state="SUCCESSFUL"), rows, [], {})
        assert decision == {"action": "noop"}

    def test_ci_status_non_allowlisted_key_is_noop(self):
        rows = [friday_pr_row()]
        decision = decide(ci_event(key="SNYK"), rows, [], {})
        assert decision == {"action": "noop"}

    def test_comment_pr_id_missing(self):
        decision = decide(comment_event(pr_id=""), [friday_pr_row()], [], {})
        assert decision == {"action": "drop", "reason": "pr_not_found"}

    def test_comment_not_friday_owned(self):
        decision = decide(comment_event(pr_id="999"), [friday_pr_row(pr_id="42")], [], {})
        assert decision == {"action": "drop", "reason": "not_friday_owned"}

    def test_comment_merged_lifecycle(self):
        rows = [friday_pr_row()]
        events = [pr_event_row(type="merged")]
        decision = decide(comment_event(), rows, events, {})
        assert decision == {"action": "drop", "reason": "merged"}

    def test_comment_escalated_non_actionable_drops(self):
        rows = [friday_pr_row()]
        events = [pr_event_row(type="escalated")]
        decision = decide(comment_event(body="random reviewer note"), rows, events, {})
        assert decision == {"action": "drop", "reason": "escalated"}

    def test_comment_not_actionable(self):
        rows = [friday_pr_row()]
        decision = decide(comment_event(body="looks good"), rows, [], {})
        assert decision == {"action": "drop", "reason": "not_actionable"}

    def test_merged_missing_pr_id(self):
        decision = decide(merged_event(pr_id=""), [friday_pr_row()], [], {})
        assert decision == {"action": "drop", "reason": "pr_not_found"}

    def test_merged_not_friday_owned(self):
        decision = decide(merged_event(pr_id="999"), [friday_pr_row(pr_id="42")], [], {})
        assert decision == {"action": "drop", "reason": "not_friday_owned"}

    def test_merged_already_merged_idempotent(self):
        rows = [friday_pr_row()]
        events = [pr_event_row(type="merged")]
        decision = decide(merged_event(), rows, events, {})
        assert decision == {"action": "drop", "reason": "merged"}

    def test_unknown_event_type(self):
        decision = decide({"event_type": "weird"}, [friday_pr_row()], [], {})
        assert decision == {"action": "drop", "reason": "not_friday_owned"}


# ─────────────────────────────────────────────────────────────────
# Decision variants — positive paths
# ─────────────────────────────────────────────────────────────────


class TestDecisionVariants:
    def test_fix_ci(self):
        rows = [friday_pr_row()]
        decision = decide(ci_event(), rows, [], {})
        assert decision == {
            "action": "fix_ci",
            "pr_id": "42",
            "sha": SHA_A,
            "branch": "fix/proj-1",
            "repo_slug": REPO,
            "repo_url": REPO_URL,
            "ticket_key": "PROJ-1",
            "source": "ci",
        }

    def test_fix_ci_type_build_without_explicit_key(self):
        # BB's 'type=build' is a valid alias for BITBUCKET-PIPELINES.
        rows = [friday_pr_row()]
        event = {
            "event_type": "ci_status",
            "state": "FAILED",
            "type": "build",
            "key": "some-pipeline-name",
            "commit_url": COMMIT_URL_A,
        }
        decision = decide(event, rows, [], {})
        assert decision["action"] == "fix_ci"

    def test_fix_comment_at_prefix(self):
        rows = [friday_pr_row()]
        decision = decide(comment_event(body="@friday fix the thing"), rows, [], {})
        assert decision["action"] == "fix_comment"
        assert decision["pr_id"] == "42"
        assert decision["comment_id"] == 7
        assert decision["comment_body"] == "@friday fix the thing"
        assert decision["source"] == "comment"
        assert decision["branch"] == "fix/proj-1"
        assert decision["ticket_key"] == "PROJ-1"

    def test_fix_comment_slash_prefix(self):
        rows = [friday_pr_row()]
        decision = decide(comment_event(body="/friday retry"), rows, [], {})
        assert decision["action"] == "fix_comment"

    def test_fix_comment_with_leading_whitespace(self):
        rows = [friday_pr_row()]
        decision = decide(comment_event(body="   @friday fix"), rows, [], {})
        assert decision["action"] == "fix_comment"

    def test_fix_comment_reactivates_escalated_pr(self):
        # User Story 16: an actionable @friday comment overrides escalated state.
        rows = [friday_pr_row()]
        events = [pr_event_row(type="escalated")]
        decision = decide(comment_event(body="@friday please retry"), rows, events, {})
        assert decision["action"] == "fix_comment"

    def test_record_merged(self):
        rows = [friday_pr_row()]
        decision = decide(merged_event(), rows, [], {})
        assert decision == {
            "action": "record_merged",
            "pr_id": "42",
            "merge_commit": SHA_C,
        }

    def test_noop_successful_ci(self):
        rows = [friday_pr_row()]
        decision = decide(ci_event(state="SUCCESSFUL"), rows, [], {})
        assert decision == {"action": "noop"}


# ─────────────────────────────────────────────────────────────────
# Iteration-budget rules
# ─────────────────────────────────────────────────────────────────


class TestIterationBudgets:
    def test_ci_total_push_cap_escalates(self):
        rows = [friday_pr_row()]
        # 10 fix_pushed rows with trigger=ci — tripping absolute cap.
        events = [
            pr_event_row(
                type="fix_pushed",
                trigger="comment",  # ensure ci_since_comment=0 to isolate total_cap
                sha=f"{i:040d}"[:12],
                ts=f"2026-04-16T00:0{i}:00Z",
            )
            for i in range(10)
        ]
        decision = decide(ci_event(sha=SHA_A), rows, events, {})
        assert decision["action"] == "escalate"
        assert decision["reason"]["cap"] == "total_pushes"
        assert decision["reason"]["total_pushes"] == 10

    def test_ci_consecutive_attempts_cap_escalates(self):
        rows = [friday_pr_row()]
        events = [
            pr_event_row(
                type="fix_pushed",
                trigger="ci",
                sha=f"sha{i}".ljust(12, "x"),
                ts=f"2026-04-16T00:0{i}:00Z",
            )
            for i in range(3)
        ]
        decision = decide(ci_event(sha=SHA_A), rows, events, {})
        assert decision["action"] == "escalate"
        assert decision["reason"]["cap"] == "ci_since_comment"
        assert decision["reason"]["ci_since_comment"] == 3

    def test_comment_resets_ci_budget(self):
        # Three ci pushes then one comment push → ci_since_comment resets to 0.
        rows = [friday_pr_row()]
        events = [
            pr_event_row(type="fix_pushed", trigger="ci", ts="2026-04-16T00:00:00Z"),
            pr_event_row(type="fix_pushed", trigger="ci", ts="2026-04-16T00:01:00Z"),
            pr_event_row(type="fix_pushed", trigger="ci", ts="2026-04-16T00:02:00Z"),
            pr_event_row(
                type="fix_pushed", trigger="comment", ts="2026-04-16T00:03:00Z"
            ),
        ]
        total, ci_since = _count_iterations(events)
        assert total == 4
        assert ci_since == 0
        decision = decide(ci_event(), rows, events, {})
        assert decision["action"] == "fix_ci"

    def test_comment_trigger_only_enforces_total_cap(self):
        # 3 ci pushes would trip ci_since_comment cap for CI path, but a
        # comment-driven event goes straight to fix_comment if total is under.
        rows = [friday_pr_row()]
        events = [
            pr_event_row(type="fix_pushed", trigger="ci", ts="2026-04-16T00:0{}:00Z".format(i))
            for i in range(3)
        ]
        decision = decide(comment_event(body="@friday retry"), rows, events, {})
        assert decision["action"] == "fix_comment"

    def test_comment_trigger_escalates_at_total_cap(self):
        rows = [friday_pr_row()]
        events = [
            pr_event_row(
                type="fix_pushed",
                trigger="comment",
                ts=f"2026-04-16T00:0{i}:00Z",
            )
            for i in range(10)
        ]
        decision = decide(comment_event(body="@friday retry"), rows, events, {})
        assert decision["action"] == "escalate"
        assert decision["reason"]["cap"] == "total_pushes"


# ─────────────────────────────────────────────────────────────────
# Actionability rule
# ─────────────────────────────────────────────────────────────────


class TestActionability:
    @pytest.mark.parametrize(
        "body,expected",
        [
            ("@friday fix", "fix_comment"),
            ("/friday retry", "fix_comment"),
            ("   @friday with whitespace", "fix_comment"),
            ("\n@friday newline-led", "fix_comment"),
            ("hello @friday embedded", "drop"),
            ("looks good", "drop"),
            ("", "drop"),
        ],
    )
    def test_prefix_recognition(self, body, expected):
        rows = [friday_pr_row()]
        decision = decide(comment_event(body=body), rows, [], {})
        assert decision["action"] == expected
        if expected == "drop":
            assert decision["reason"] == "not_actionable"


# ─────────────────────────────────────────────────────────────────
# Row filtering — rows for other pr_ids must be ignored
# ─────────────────────────────────────────────────────────────────


class TestRowFiltering:
    def test_unrelated_pr_merged_does_not_block(self):
        rows = [friday_pr_row(pr_id="42"), friday_pr_row(pr_id="99", ticket_key="PROJ-99")]
        events = [pr_event_row(pr_id="99", type="merged")]
        decision = decide(ci_event(), rows, events, {})
        assert decision["action"] == "fix_ci"
        assert decision["pr_id"] == "42"

    def test_unrelated_pr_escalated_does_not_block(self):
        rows = [friday_pr_row(pr_id="42"), friday_pr_row(pr_id="99", ticket_key="PROJ-99")]
        events = [pr_event_row(pr_id="99", type="escalated")]
        decision = decide(ci_event(), rows, events, {})
        assert decision["action"] == "fix_ci"

    def test_unrelated_pr_pushes_dont_count_toward_budget(self):
        # 10 pushes on a different pr_id — our pr_id has zero.
        rows = [friday_pr_row(pr_id="42")]
        events = [
            pr_event_row(pr_id="99", type="fix_pushed", trigger="ci", ts=f"2026-04-16T00:0{i}:00Z")
            for i in range(10)
        ]
        decision = decide(ci_event(), rows, events, {})
        assert decision["action"] == "fix_ci"

    def test_unrelated_pr_triaged_does_not_block(self):
        rows = [friday_pr_row(pr_id="42")]
        events = [pr_event_row(pr_id="99", type="ci_triaged", sha=SHA_A)]
        decision = decide(ci_event(sha=SHA_A), rows, events, {})
        assert decision["action"] == "fix_ci"

    def test_prefilter_parity(self):
        # Decisions are identical whether we pass raw multi-pr rows or
        # pre-filtered rows — router filters internally.
        our = friday_pr_row(pr_id="42")
        other = friday_pr_row(pr_id="99", ticket_key="PROJ-99")
        our_event = pr_event_row(pr_id="42", type="ci_triaged", sha=SHA_B)
        noise_event = pr_event_row(pr_id="99", type="merged")

        raw_decision = decide(
            ci_event(sha=SHA_B),
            [our, other],
            [our_event, noise_event],
            {},
        )
        filtered_decision = decide(ci_event(sha=SHA_B), [our], [our_event], {})
        # Both should drop with already_triaged.
        assert raw_decision == filtered_decision


# ─────────────────────────────────────────────────────────────────
# Config forwarding — custom caps / prefixes override defaults
# ─────────────────────────────────────────────────────────────────


class TestConfigForwarding:
    def test_custom_total_cap_lower(self):
        rows = [friday_pr_row()]
        events = [
            pr_event_row(type="fix_pushed", trigger="comment", ts=f"2026-04-16T00:0{i}:00Z")
            for i in range(5)
        ]
        decision = decide(
            ci_event(),
            rows,
            events,
            {"max_total_pushes_per_pr": 5},
        )
        assert decision["action"] == "escalate"
        assert decision["reason"]["cap"] == "total_pushes"

    def test_custom_ci_cap_lower(self):
        rows = [friday_pr_row()]
        events = [
            pr_event_row(type="fix_pushed", trigger="ci", ts=f"2026-04-16T00:0{i}:00Z")
            for i in range(2)
        ]
        decision = decide(
            ci_event(),
            rows,
            events,
            {"max_ci_consecutive_attempts_without_comment_reset": 2},
        )
        assert decision["action"] == "escalate"
        assert decision["reason"]["cap"] == "ci_since_comment"

    def test_custom_actionable_prefix(self):
        # A single-element list overrides the defaults; plain "@friday" no
        # longer counts.
        rows = [friday_pr_row()]
        cfg = {"actionable_prefixes": ["!bot"]}
        d1 = decide(comment_event(body="@friday fix"), rows, [], cfg)
        d2 = decide(comment_event(body="!bot fix"), rows, [], cfg)
        assert d1 == {"action": "drop", "reason": "not_actionable"}
        assert d2["action"] == "fix_comment"

    def test_custom_ci_status_key(self):
        rows = [friday_pr_row()]
        cfg = {"ci_status_keys": ["GITHUB-ACTIONS"]}
        # default key won't match any more.
        d1 = decide(ci_event(key="BITBUCKET-PIPELINES"), rows, [], cfg)
        d2 = decide(ci_event(key="GITHUB-ACTIONS"), rows, [], cfg)
        assert d1 == {"action": "noop"}
        assert d2["action"] == "fix_ci"

    def test_partial_config_merges_with_defaults(self):
        # Caller passes only ci_status_keys; other defaults apply.
        rows = [friday_pr_row()]
        events = [
            pr_event_row(type="fix_pushed", trigger="ci", ts=f"2026-04-16T00:0{i}:00Z")
            for i in range(3)
        ]
        decision = decide(
            ci_event(),
            rows,
            events,
            {"ci_status_keys": ["BITBUCKET-PIPELINES"]},
        )
        # Default ci cap still applies → escalate.
        assert decision["action"] == "escalate"


# ─────────────────────────────────────────────────────────────────
# Webhook ↔ Reconcile parity
# ─────────────────────────────────────────────────────────────────


class TestReconcileParity:
    def test_reconcile_ci_failed_emits_ci_status(self):
        cfg = ReconcileInput(
            operation="reconcile",
            friday_pr=friday_pr_row(),
            pr_events=[],
            bb_state={
                "commit_statuses": [
                    {"key": "BITBUCKET-PIPELINES", "state": "FAILED"}
                ],
            },
            config={},
        )
        result = _reconcile(cfg)
        assert isinstance(result, OkResult)
        events = result.data["events"]
        assert len(events) == 1
        assert events[0]["event_type"] == "ci_status"
        # Webhook-path decision for the same synthetic event must also fix_ci.
        webhook_decision = decide(
            events[0],
            [friday_pr_row()],
            [],
            {},
        )
        assert webhook_decision["action"] == "fix_ci"

    def test_reconcile_suppresses_already_triaged_ci(self):
        events = [pr_event_row(type="ci_triaged", sha=SHA_A)]
        cfg = ReconcileInput(
            operation="reconcile",
            friday_pr=friday_pr_row(sha=SHA_A),
            pr_events=events,
            bb_state={
                "commit_statuses": [
                    {"key": "BITBUCKET-PIPELINES", "state": "FAILED"}
                ],
            },
            config={},
        )
        result = _reconcile(cfg)
        assert isinstance(result, OkResult)
        assert result.data["events"] == []

    def test_reconcile_suppresses_terminal_states(self):
        for terminal in ("merged", "escalated"):
            cfg = ReconcileInput(
                operation="reconcile",
                friday_pr=friday_pr_row(),
                pr_events=[pr_event_row(type=terminal)],
                bb_state={
                    "commit_statuses": [
                        {"key": "BITBUCKET-PIPELINES", "state": "FAILED"}
                    ],
                    "threads": [
                        {
                            "root": {
                                "comment_id": 1,
                                "body": "@friday retry",
                                "author": "alice",
                            },
                            "replies": [],
                        }
                    ],
                    "pr_view": {"state": "MERGED"},
                },
                config={},
            )
            result = _reconcile(cfg)
            assert isinstance(result, OkResult)
            assert result.data["events"] == []

    def test_reconcile_actionable_comment_emits_comment(self):
        cfg = ReconcileInput(
            operation="reconcile",
            friday_pr=friday_pr_row(),
            pr_events=[],
            bb_state={
                "threads": [
                    {
                        "root": {
                            "comment_id": 17,
                            "body": "@friday please retry",
                            "author": "bob",
                        },
                        "replies": [],
                    }
                ],
            },
            config={},
        )
        result = _reconcile(cfg)
        assert isinstance(result, OkResult)
        events = result.data["events"]
        assert len(events) == 1
        assert events[0]["event_type"] == "comment"
        assert events[0]["comment_id"] == 17
        # And the webhook-path decision for that event fires fix_comment.
        decision = decide(events[0], [friday_pr_row()], [], {})
        assert decision["action"] == "fix_comment"

    def test_reconcile_skips_non_actionable_comments(self):
        cfg = ReconcileInput(
            operation="reconcile",
            friday_pr=friday_pr_row(),
            pr_events=[],
            bb_state={
                "threads": [
                    {
                        "root": {"comment_id": 1, "body": "LGTM", "author": "c"},
                        "replies": [],
                    }
                ],
            },
            config={},
        )
        result = _reconcile(cfg)
        assert result.data["events"] == []

    def test_reconcile_skips_seen_comments(self):
        events = [pr_event_row(type="responded", comment_id=7)]
        cfg = ReconcileInput(
            operation="reconcile",
            friday_pr=friday_pr_row(),
            pr_events=events,
            bb_state={
                "threads": [
                    {
                        "root": {
                            "comment_id": 7,
                            "body": "@friday retry",
                            "author": "a",
                        },
                        "replies": [],
                    }
                ],
            },
            config={},
        )
        result = _reconcile(cfg)
        assert result.data["events"] == []

    def test_reconcile_merged_emits_merged(self):
        cfg = ReconcileInput(
            operation="reconcile",
            friday_pr=friday_pr_row(),
            pr_events=[],
            bb_state={"pr_view": {"state": "MERGED", "merge_commit": SHA_C}},
            config={},
        )
        result = _reconcile(cfg)
        events = result.data["events"]
        assert len(events) == 1
        assert events[0] == {
            "event_type": "merged",
            "pr_id": "42",
            "merge_commit": SHA_C,
            "_source": "reconciler",
        }
        decision = decide(events[0], [friday_pr_row()], [], {})
        assert decision["action"] == "record_merged"

    def test_reconcile_merged_dedup(self):
        cfg = ReconcileInput(
            operation="reconcile",
            friday_pr=friday_pr_row(),
            pr_events=[pr_event_row(type="merged")],
            bb_state={"pr_view": {"state": "MERGED"}},
            config={},
        )
        # Already-merged row gates into an empty fan-out via the terminal
        # lifecycle gate at the top of _reconcile.
        result = _reconcile(cfg)
        assert result.data["events"] == []

    def test_webhook_reconcile_same_event_same_decision(self):
        # Run the same comment candidate through both paths; decisions agree.
        friday_row = friday_pr_row()
        reconcile_cfg = ReconcileInput(
            operation="reconcile",
            friday_pr=friday_row,
            pr_events=[],
            bb_state={
                "threads": [
                    {
                        "root": {
                            "comment_id": 99,
                            "body": "@friday please",
                            "author": "bob",
                        },
                        "replies": [],
                    }
                ],
            },
            config={},
        )
        rec_result = _reconcile(reconcile_cfg)
        synth = rec_result.data["events"][0]
        webhook_decision = decide(synth, [friday_row], [], {})
        # Run the reconcile's internal parity check too:
        internal = decide(synth, [friday_row], [], {})
        assert webhook_decision == internal
        assert webhook_decision["action"] == "fix_comment"


# ─────────────────────────────────────────────────────────────────
# Top-level execute() dispatcher via parse_operation
# ─────────────────────────────────────────────────────────────────


class TestExecuteDispatcher:
    def test_webhook_end_to_end_through_execute(self):
        from agent import execute

        prompt = json.dumps(
            {
                "operation": "webhook",
                "event": ci_event(),
                "friday_prs": [friday_pr_row()],
                "pr_events": [],
                "config": {},
            }
        )
        result = execute(prompt, None)
        assert isinstance(result, OkResult)
        assert result.data["action"] == "fix_ci"

    def test_reconcile_end_to_end_through_execute(self):
        from agent import execute

        prompt = json.dumps(
            {
                "operation": "reconcile",
                "friday_pr": friday_pr_row(),
                "pr_events": [],
                "bb_state": {
                    "commit_statuses": [
                        {"key": "BITBUCKET-PIPELINES", "state": "FAILED"}
                    ]
                },
                "config": {},
            }
        )
        result = execute(prompt, None)
        assert isinstance(result, OkResult)
        assert len(result.data["events"]) == 1
        assert result.data["events"][0]["event_type"] == "ci_status"

    def test_unknown_operation_errors(self):
        from agent import execute

        prompt = json.dumps({"operation": "bogus"})
        result = execute(prompt, None)
        assert isinstance(result, ErrResult)

    def test_missing_operation_errors(self):
        from agent import execute

        prompt = "this has no json at all"
        result = execute(prompt, None)
        assert isinstance(result, ErrResult)

    def test_webhook_input_dataclass_round_trip(self):
        # WebhookInput/ReconcileInput must be constructable by parse_operation.
        from friday_agent_sdk import parse_operation

        prompt = json.dumps(
            {
                "operation": "webhook",
                "event": ci_event(),
                "friday_prs": [friday_pr_row()],
                "pr_events": [],
                "config": {"max_total_pushes_per_pr": 5},
            }
        )
        parsed = parse_operation(
            prompt,
            {"webhook": WebhookInput, "reconcile": ReconcileInput},
        )
        assert isinstance(parsed, WebhookInput)
        assert parsed.config == {"max_total_pushes_per_pr": 5}
