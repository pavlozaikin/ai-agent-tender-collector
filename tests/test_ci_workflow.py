"""Supply-chain hardening checks for the CI workflow (M6)."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

_WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml"

# A 40-hex-char git commit SHA — the only form considered "pinned".
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def _action_refs(workflow_text: str) -> list[str]:
    """Extract every `uses:` action reference from the workflow YAML."""
    doc = yaml.safe_load(workflow_text)
    refs: list[str] = []
    for job in doc.get("jobs", {}).values():
        for step in job.get("steps", []):
            uses = step.get("uses")
            if uses:
                refs.append(uses)
    return refs


def test_all_actions_pinned_to_commit_sha() -> None:
    """M6: every third-party action must be pinned to a full commit SHA."""
    refs = _action_refs(_WORKFLOW.read_text(encoding="utf-8"))
    assert refs, "expected at least one `uses:` action in the workflow"
    for ref in refs:
        _, _, version = ref.partition("@")
        assert _SHA_RE.match(version), f"action {ref!r} is not pinned to a commit SHA"
