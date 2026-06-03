#!/usr/bin/env python3
"""Generate a "Last week in AWS Deadline Cloud" digest comment.

Enumerates the GitHub Releases published across all aws-deadline/* repos in a
trailing window (default 7 days), asks Claude Opus on Bedrock to condense each
repo's release notes into a few customer-facing highlights, and renders a
markdown comment that links to the full release notes for each release.

Mirrors the Bedrock tool-use pattern used by
.github/scripts/generate_release_notes.py, but operates on published GitHub
Releases across the org rather than on git commits in a single repo.
"""

import argparse
import datetime as dt
import json
import subprocess
import sys

import boto3

ORG = "aws-deadline"
MODEL_ID = "us.anthropic.claude-opus-4-8"

# Explicit allowlist of public, customer-facing repos to summarize. We use an
# allowlist (not a denylist) so newly created repos — including private ones —
# are never surfaced by accident.
#
# Included: core, all public DCC integrations, samples, job-attachments, and
# the worker agent. Deliberately excluded: deadline-cloud-test-fixtures (test
# tooling), .github (no releases), and every private repo — which is why the
# Rhino, RenderMan, and ShotGrid DCCs are absent (they are private repos).
ALLOWED_REPOS = {
    "deadline-cloud",
    "deadline-cloud-worker-agent",
    "deadline-cloud-job-attachments",
    "deadline-cloud-samples",
    # DCC integrations (public)
    "deadline-cloud-for-3ds-max",
    "deadline-cloud-for-after-effects",
    "deadline-cloud-for-blender",
    "deadline-cloud-for-cinema-4d",
    "deadline-cloud-for-houdini",
    "deadline-cloud-for-keyshot",
    "deadline-cloud-for-maya",
    "deadline-cloud-for-nuke",
    "deadline-cloud-for-unreal-engine",
    "deadline-cloud-for-vred",
}

TOOL_DEFINITION = {
    "name": "emit_digest",
    "description": "Emit condensed weekly highlights, one group per repository. Call exactly once.",
    "input_schema": {
        "type": "object",
        "properties": {
            "repos": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "repo": {
                            "type": "string",
                            "description": "The repository name exactly as given in the input (e.g. 'deadline-cloud').",
                        },
                        "highlights": {
                            "type": "array",
                            "description": "1-4 short customer-facing highlights for this repo across all its releases this week. Omit a repo entirely if nothing is customer-noticeable.",
                            "items": {"type": "string"},
                        },
                    },
                    "required": ["repo", "highlights"],
                },
            }
        },
        "required": ["repos"],
    },
}

SYSTEM_PROMPT = """\
You are writing a concise weekly digest titled "Last week in AWS Deadline Cloud" \
for users of the AWS Deadline Cloud open-source ecosystem (the aws-deadline GitHub org).

You will receive the GitHub Release notes published in the last week, grouped by repository. \
Call the emit_digest tool with condensed highlights.

For each repository, distill its releases into 1-4 SHORT bullet highlights that a USER would care about:
- New features, CLI commands, flags, or APIs they can now use
- Bug fixes that affected their workflows
- Breaking changes and what they must do differently
- Notable deprecations

EXCLUDE noise even if it appears in the notes: CI/CD changes, test changes, internal refactors, \
dependency bumps, docs-only changes, version-only churn, release-process changes.

Rules:
- If a repo has nothing a user would notice, OMIT that repo from your output entirely.
- Keep each highlight to one sentence. Do not include PR numbers or version numbers — those are \
added automatically around your text.
- Write from the user's perspective ("You can now...", "Fixed an issue where...").
- Combine related changes across multiple releases of the same repo into single highlights.
- Use the repo name EXACTLY as provided.
- ALWAYS call the emit_digest tool. Never respond with plain text."""


def run(*args: str, timeout: int = 60) -> str:
    result = subprocess.run(list(args), capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(f"command failed: {' '.join(args)}\n{result.stderr.strip()}")
    return result.stdout.strip()


def list_repos() -> list[str]:
    """The allowlisted repos that currently exist as non-archived public repos.

    We intersect ALLOWED_REPOS with the live org listing so an allowlisted repo
    that is later archived, renamed, or made private simply drops out (and is
    logged) instead of producing failed release lookups.
    """
    out = run(
        "gh",
        "repo",
        "list",
        ORG,
        "--no-archived",
        "--visibility",
        "public",
        "--limit",
        "200",
        "--json",
        "name",
        "-q",
        ".[].name",
    )
    present = {r for r in out.splitlines() if r}
    repos = ALLOWED_REPOS & present
    missing = ALLOWED_REPOS - present
    if missing:
        print(
            f"  ! allowlisted repos not found as public/active: {', '.join(sorted(missing))}",
            file=sys.stderr,
        )
    return sorted(repos)


def releases_in_window(repo: str, since: dt.datetime) -> list[dict]:
    """Releases for a repo published at or after `since` (skips drafts/prereleases)."""
    try:
        out = run(
            "gh",
            "release",
            "list",
            "--repo",
            f"{ORG}/{repo}",
            "--limit",
            "30",
            "--json",
            "tagName,publishedAt,isDraft,isPrerelease",
        )
    except RuntimeError as e:
        print(f"  ! could not list releases for {repo}: {e}", file=sys.stderr)
        return []

    rows = json.loads(out) if out else []
    recent = []
    for row in rows:
        if row.get("isDraft") or row.get("isPrerelease"):
            continue
        published = row.get("publishedAt")
        if not published:
            continue
        when = dt.datetime.fromisoformat(published.replace("Z", "+00:00"))
        if when >= since:
            recent.append({"tag": row["tagName"], "publishedAt": published, "when": when})
    return recent


def release_body(repo: str, tag: str) -> str:
    try:
        return run(
            "gh",
            "release",
            "view",
            tag,
            "--repo",
            f"{ORG}/{repo}",
            "--json",
            "body",
            "-q",
            ".body",
        )
    except RuntimeError:
        return ""


def release_url(repo: str, tag: str) -> str:
    return f"https://github.com/{ORG}/{repo}/releases/tag/{tag}"


def collect(since: dt.datetime) -> dict[str, list[dict]]:
    """Map of repo -> list of release dicts (tag, url, body, when) in the window."""
    data: dict[str, list[dict]] = {}
    for repo in list_repos():
        recent = releases_in_window(repo, since)
        if not recent:
            continue
        for rel in recent:
            rel["url"] = release_url(repo, rel["tag"])
            rel["body"] = release_body(repo, rel["tag"])
        data[repo] = recent
        print(f"  {repo}: {len(recent)} release(s)", file=sys.stderr)
    return data


def build_input_text(data: dict[str, list[dict]]) -> str:
    blocks = []
    for repo, releases in data.items():
        lines = [f"## repo: {repo}"]
        for rel in releases:
            lines.append(f"### release {rel['tag']} ({rel['publishedAt'][:10]})")
            lines.append(rel["body"] or "(no release notes)")
            lines.append("")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def summarize(data: dict[str, list[dict]], region: str) -> dict[str, list[str]]:
    client = boto3.client("bedrock-runtime", region_name=region)
    response = client.invoke_model(
        modelId=MODEL_ID,
        contentType="application/json",
        accept="application/json",
        body=json.dumps(
            {
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 4096,
                "system": SYSTEM_PROMPT,
                "tools": [TOOL_DEFINITION],
                "tool_choice": {"type": "tool", "name": "emit_digest"},
                "messages": [{"role": "user", "content": build_input_text(data)}],
            }
        ),
    )
    body = json.loads(response["body"].read())
    for block in body.get("content", []):
        if block.get("type") == "tool_use" and block.get("name") == "emit_digest":
            return {
                r["repo"]: r["highlights"] for r in block["input"]["repos"] if r.get("highlights")
            }
    raise RuntimeError(f"No tool_use block in response: {json.dumps(body, indent=2)}")


def render_comment(
    data: dict[str, list[dict]],
    highlights: dict[str, list[str]],
    since: dt.datetime,
    until: dt.datetime,
) -> str:
    title = "## Last week in AWS Deadline Cloud"
    window = f"_Releases from {since.date().isoformat()} to {until.date().isoformat()}_"

    if not highlights:
        return f"{title}\n\n{window}\n\nNo customer-facing releases this week.\n"

    lines = [title, "", window, ""]
    for repo in sorted(highlights):
        bullets = highlights[repo]
        releases = data.get(repo, [])
        # Newest first; build "tag (link)" references for the full notes.
        refs = ", ".join(
            f"[`{rel['tag']}`]({rel['url']})"
            for rel in sorted(releases, key=lambda r: r["when"], reverse=True)
        )
        lines.append(f"### {repo}")
        for b in bullets:
            lines.append(f"- {b}")
        lines.append(f"\nFull notes: {refs}")
        lines.append("")
    return "\n".join(lines)


def render_slack_payload(
    data: dict[str, list[dict]],
    highlights: dict[str, list[str]],
    since: dt.datetime,
    until: dt.datetime,
) -> dict:
    """Render a flat Slack payload for a Workflow Builder webhook trigger.

    Workflow Builder webhooks accept only a flat JSON object of the variables
    declared in the builder (NOT Block Kit `blocks`), AND they insert a
    variable's value as literal plain text — no markup of any dialect is
    parsed. So we emit PLAIN TEXT: no *bold*, no <url|label>.

    We intentionally omit per-release URLs here. Workflow Builder ignores the
    `unfurl_links` payload field, so any bare URL would unfurl into a preview
    card — several repos means a wall of cards. Slack is just the notification;
    the full clickable release links live in the GitHub comment (the canonical
    digest).

    In Workflow Builder, declare one variable named `text` and insert it into
    the "Send a message" step.
    """
    span = f"{since.month}/{since.day:02d} to {until.month}/{until.day:02d}"
    lines = [f"Last week in AWS Deadline Cloud ({span})", ""]

    if not highlights:
        lines.append("No customer-facing releases this week.")
        return {"text": "\n".join(lines)}

    for repo in sorted(highlights):
        lines.append(repo)
        for b in highlights[repo]:
            lines.append(f"• {b}")
        lines.append("")

    return {"text": "\n".join(lines).rstrip()}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate the weekly AWS Deadline Cloud release digest comment."
    )
    parser.add_argument("--days", type=int, default=7, help="Trailing window in days (default: 7).")
    parser.add_argument("--now", help="Override 'now' as ISO date (e.g. 2026-06-02) for testing.")
    parser.add_argument("--region", default="us-west-2", help="AWS region for Bedrock.")
    parser.add_argument(
        "--out", help="Write the rendered GitHub comment to this file (default: stdout)."
    )
    parser.add_argument("--slack-out", help="Write a Slack Block Kit JSON payload to this file.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip Bedrock; print collected release input instead.",
    )
    args = parser.parse_args()

    if args.now:
        until = dt.datetime.fromisoformat(args.now).replace(tzinfo=dt.timezone.utc)
    else:
        # No Date.now() concerns here — this is a normal CLI tool.
        until = dt.datetime.now(dt.timezone.utc)
    since = until - dt.timedelta(days=args.days)

    print(f"Collecting aws-deadline releases since {since.isoformat()}...", file=sys.stderr)
    data = collect(since)
    if not data:
        print("No releases in window.", file=sys.stderr)
        comment = render_comment({}, {}, since, until)
        _emit(comment, args.out)
        if args.slack_out:
            _emit_slack(render_slack_payload({}, {}, since, until), args.slack_out)
        return

    if args.dry_run:
        print(build_input_text(data))
        return

    print("Summarizing with Bedrock...", file=sys.stderr)
    highlights = summarize(data, args.region)
    comment = render_comment(data, highlights, since, until)
    _emit(comment, args.out)
    if args.slack_out:
        _emit_slack(render_slack_payload(data, highlights, since, until), args.slack_out)


def _emit(comment: str, out: str | None) -> None:
    if out:
        with open(out, "w") as f:
            f.write(comment)
        print(f"Wrote comment to {out}", file=sys.stderr)
    else:
        print(comment)


def _emit_slack(payload: dict, out: str) -> None:
    with open(out, "w") as f:
        json.dump(payload, f, indent=2)
    print(f"Wrote Slack payload to {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
