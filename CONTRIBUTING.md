# Contributing

Thanks for your interest. Issues, ideas, and pull requests are welcome.

## Reporting bugs

Open a [GitHub issue](https://github.com/samr037/node-debug-dashboard/issues)
with:
- What you ran (`helm install ...` command, env vars, cluster kind / Talos
  version)
- What you expected
- What you saw — pod logs, the failing API response, screenshots

## Local development

The app is a FastAPI service. To iterate without going through a full
container build:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
HOST_ROOT=/ HOST_PROC=/proc uvicorn app.main:app --reload
```

The dashboard is then on `http://127.0.0.1:8000`. Most collectors will
work on a regular Linux host; Talos- and Kubernetes-specific sections
will be empty.

### Dependencies

`requirements.txt` (runtime) and `requirements-dev.txt` (runtime + test/lint
tools) are uv-generated locks with exact versions and hashes; the Dockerfile
installs with `--require-hashes`. Do not edit the pinned lines by hand: change
the ranges listed in the `requirements.txt` header, then regenerate both files
together with the `uv pip compile --universal --python-version 3.11
--generate-hashes ...` command given there, and keep the header.

## Building the image

```bash
docker buildx build --platform linux/amd64,linux/arm64 -t test-ndd .
```

CI builds the same image on every push to `main` (no push) and on every
`v*` tag (push to GHCR + Docker Hub, only after lint and tests pass).

## Helm chart

```bash
helm lint charts/node-debug-dashboard
helm template t charts/node-debug-dashboard --set ssh.enabled=true \
  --set ssh.authorizedKeys="ssh-ed25519 AAAA test@host"
```

## Pull requests

- Keep the change focused. One bug fix or one feature per PR.
- Run `ruff check app/ tests/` and `ruff format --check app/ tests/`
  before pushing — CI runs both.
- Update the relevant README / chart README / `docs/ssh.md` when you
  change a default, an env var, or a public endpoint.
- Bump the chart `version` in `charts/node-debug-dashboard/Chart.yaml`
  if your change touches anything under `charts/` — CI fails the PR
  otherwise, since a chart change under an already released version is
  never published. Bump `appVersion` if your change ships a new image.

## Releasing (maintainer)

1. Merge changes to `main`.
2. Tag `vX.Y.Z` — the release workflow runs lint and tests, then builds
   the multi-arch image and pushes it to GHCR (and Docker Hub if
   `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN` secrets are set). `latest`
   only moves for a plain `vX.Y.Z`: a pre-release such as `vX.Y.Z-rc.1`
   gets its own tag and a GitHub pre-release, nothing more.
3. The chart-release workflow runs on every push to `main` that touches
   `charts/**`, and again when the release workflow finishes. It publishes
   each chart version that is not out yet to the gh-pages Helm repo + OCI,
   and never overwrites a version that already exists. A chart whose
   `appVersion` image is not in GHCR yet is held back (with a warning)
   until the image release completes, so bumping `appVersion` before
   tagging is safe.

## Code of conduct

Be civil. No code of conduct document, but the
[Contributor Covenant](https://www.contributor-covenant.org/) is the
default expectation.
