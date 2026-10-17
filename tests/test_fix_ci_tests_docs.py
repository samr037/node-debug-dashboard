"""Regression tests for the release workflows' guard rails.

These only parse .github/workflows/*.yml; nothing runs on GitHub.
"""

import re
from pathlib import Path

import yaml

WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"


def load(name: str) -> dict:
    wf = yaml.safe_load((WORKFLOWS / name).read_text())
    # PyYAML reads the bare `on:` key as boolean True.
    wf["on"] = wf.pop(True, wf.get("on"))
    return wf


def steps(job: dict) -> list[dict]:
    return job.get("steps", [])


def test_latest_tag_is_not_forced_on_prereleases():
    job = load("release.yml")["jobs"]["build-and-push"]
    meta = next(s for s in steps(job) if "metadata-action" in s.get("uses", ""))
    tags = meta["with"]["tags"]
    # An unconditional raw `latest` rule moves `latest` on v0.4.0-rc.1.
    assert not re.search(r"type=raw,value=latest(?!.*enable=)", tags)
    assert "latest=true" not in meta["with"].get("flavor", "")


def test_release_runs_ci_before_publishing():
    wf = load("release.yml")
    assert wf["jobs"]["ci"]["uses"] == "./.github/workflows/ci.yml"
    needs = wf["jobs"]["build-and-push"]["needs"]
    assert "ci" in ([needs] if isinstance(needs, str) else needs)
    assert "workflow_call" in load("ci.yml")["on"]


def test_ci_build_job_still_runs_on_push_and_pr():
    # `inputs.skip_build` is null outside workflow_call: the condition must
    # treat that as "build", not as false.
    cond = load("ci.yml")["jobs"]["build"]["if"]
    assert "!inputs.skip_build" in cond


def test_helm_release_never_overwrites_or_marks_latest():
    wf = load("helm-release.yml")
    assert set(wf["permissions"]) == {"contents", "packages"}
    run = "\n".join(s.get("run", "") for s in steps(wf["jobs"]["release"]))
    assert "--skip-existing" in run
    assert "--make-release-latest=false" in run
    # OCI push checks for an existing version first.
    assert re.search(r"helm show chart \"\$\{registry\}/\$\{name\}\" --version", run)
    # Charts whose appVersion image is missing are held back.
    assert "imagetools inspect" in run
    uses = [s.get("uses", "") for s in steps(wf["jobs"]["release"])]
    assert not any("chart-releaser-action" in u for u in uses)


def test_ci_is_read_only():
    assert load("ci.yml")["permissions"] == {"contents": "read"}


def test_third_party_actions_pinned_by_sha():
    for path in WORKFLOWS.glob("*.yml"):
        for ref in re.findall(r"uses:\s*([^\s#]+)", path.read_text()):
            if ref.startswith("./"):
                continue
            assert re.search(r"@[0-9a-f]{40}$", ref), f"{path.name}: {ref}"
