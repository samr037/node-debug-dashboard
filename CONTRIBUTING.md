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

## Building the image

```bash
docker buildx build --platform linux/amd64,linux/arm64 -t test-ndd .
```

CI builds the same image on every push to `main` (no push) and on every
`v*` tag (push to GHCR + Docker Hub).

## Helm chart

```bash
helm lint charts/node-debug-dashboard
helm template t charts/node-debug-dashboard --set ssh.enabled=true \
  --set ssh.authorizedKeys="ssh-ed25519 AAAA test@host"
```

## Pull requests

- Keep the change focused. One bug fix or one feature per PR.
- Run `ruff check app/` and `ruff format --check app/` before pushing —
  CI runs both.
- Update the relevant README / chart README / `docs/ssh.md` when you
  change a default, an env var, or a public endpoint.
- Bump the chart `version` in `charts/node-debug-dashboard/Chart.yaml`
  if your change touches anything under `charts/`. Bump `appVersion`
  if your change ships a new image.

## Releasing (maintainer)

1. Merge changes to `main`.
2. Tag `vX.Y.Z` — the release workflow builds the multi-arch image and
   pushes it to GHCR (and Docker Hub if `DOCKERHUB_USERNAME` and
   `DOCKERHUB_TOKEN` secrets are set).
3. The chart-release workflow runs on every push to `main` that touches
   `charts/**` and publishes to the gh-pages Helm repo + OCI.

## Code of conduct

Be civil. No code of conduct document, but the
[Contributor Covenant](https://www.contributor-covenant.org/) is the
default expectation.
