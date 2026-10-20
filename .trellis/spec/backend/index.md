# Backend Development Guidelines

Read [runtime-contracts.md](runtime-contracts.md) before editing this layer and
[acceptance-contracts.md](acceptance-contracts.md) for local/API acceptance tooling.
Read [cloud-api-contracts.md](cloud-api-contracts.md) for cloud/local transport,
mode switching, proxy and acquisition recovery changes.
Read [managed-api-contracts.md](managed-api-contracts.md) for WebUI-managed local
API lifecycle, credentials, persisted transitions and supervisor control.
Read [request-safety-contracts.md](request-safety-contracts.md) for all Telegram
request pacing, cooldown, credential pause and explicit recovery changes.
Read [template-contracts.md](template-contracts.md) for confirmation and filename templates.
Read [task-deletion-contracts.md](task-deletion-contracts.md) for task record removal.
Other topic files remain bootstrap templates, not additional implemented conventions.

## Quality Check

Select regression checks by impact scope (user preference): run affected tests
and their direct integration boundaries first. From backend/, use
`uv run ruff check app tests tools ../scripts ../deploy` and `uv run mypy app`
for backend edits, plus `uv run pytest -q <affected test files or selectors>`.
Run CLI/browser checks when those tools or their contracts change. Broaden to
`uv run pytest -q` or other packages only for shared infrastructure changes,
release gates, failures or unresolved concerns. Record selected scope and results;
do not repeat already-passing checks without new evidence. Validate cross-layer
DTOs and deployment settings when affected. Real-device release checks are
tracked separately in docs/verification.md.
