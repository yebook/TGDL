# Frontend Development Guidelines

Read [ui-contracts.md](ui-contracts.md) before editing this layer. Other topic files remain bootstrap templates, not additional implemented conventions.

## Quality Check

Select regression checks by impact scope (user preference). From frontend/, run
`pnpm lint`, `pnpm typecheck` and the affected unit/browser cases; use `pnpm build`
when production assets are affected. Expand to `pnpm test:unit` / `pnpm test:e2e`
for shared infrastructure, release gates, failures or unresolved concerns. Backend
only changes need no browser run unless a UI contract is affected. The E2E command
builds again; one successful build there serves as the build gate. Record scope
and results, and do not repeat passing checks without new evidence. Validate
cross-layer DTOs and deployment settings when affected. Real-device release
checks are tracked separately in docs/verification.md.
