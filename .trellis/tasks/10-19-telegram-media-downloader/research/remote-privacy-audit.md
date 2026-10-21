# Research: Remote repository privacy audit before public visibility

- Query: Does the remote GitHub repository or the pending source contain credentials or personal information that would be exposed by making it public?
- Scope: Internal; offline inspection of fetched remote Git objects and intended working-tree source, without contacting Telegram or reading runtime credentials.
- Date: 2026-10-21

## Findings

### Audited snapshot and method

- Remote snapshot: `refs/remotes/origin/main` at `78a000adbff99a3745d2670a266d9ccee4e66d04`; `origin/HEAD` resolves to the same commit. The parent session fetched origin branches/tags and inspected advertised remote refs; only main and HEAD were advertised.
- Complete reachable history for that snapshot: **6 commits**, **326 unique blobs**, **0 binary blobs**; repository is not shallow. Remote HEAD contains **269 tracked files**.
- Also inspected **291 existing working-tree source files** returned by `git ls-files` plus nonignored untracked files at audit start. This includes pending feature/docs changes; concurrent changes after that snapshot require their own final review. Ignored runtime files, installed dependencies and build artifacts were deliberately excluded.
- Custom offline scanner searched every unique historical blob and current source for Telegram token shapes, API Hash assignments, common GitHub/cloud-provider token shapes, private-key headers, credential-bearing URLs, password/secret assignments, email addresses, Telegram identifiers, phone shapes, absolute paths and sensitive runtime filenames. Strong matches were reviewed by source context and structural properties without printing values. Commit author/committer identity metadata was inspected separately.
- No source contents were uploaded to a scanning service. No credential was tested for validity. Installed `gitleaks` / `trufflehog` were unavailable, so this was a custom scanner with contextual review, not an independently maintained detector suite.

### Credential findings

No confirmed real Bot Token, API Hash, password, private key, access token or proxy credential was identified in the audited snapshot.

| Candidate class | Historical occurrences | Review result / evidence |
| --- | ---: | --- |
| Telegram-shaped token | 8 | Test-only values: `backend/tests/test_management.py:107`, `backend/tests/test_recovery.py:171`, `backend/tests/test_request_safety.py:21`, `backend/tests/test_request_safety_runtime.py:16` and additional lines in those files. Six occurrences use one repeated character in the secret portion; two explicitly use synthetic test wording. |
| API Hash assignment | 3 | `frontend/e2e/managed-api.spec.ts:28`, `:32`, `:103`: identical deterministic ordered hexadecimal sequence used as a test fixture. |
| Credential-bearing URL / email-shaped URL fragment | 1 | `scripts/tests/test_acceptance.py:158`: synthetic test URL on an example domain. |
| Telegram user identifier literal | 8 | Two historical versions of `backend/tools/e2e_app.py`: seeded browser-test identities, not evidence of real Telegram accounts. |
| Other password/secret assignments | 88 broad historical matches | Schema types, environment-variable interpolation, function calls and test values. No real credential established by contextual review. |
| Private keys / common provider tokens | 0 | No matching private-key header or GitHub/AWS/Slack/OpenAI token shape. |

- Across the remote snapshots, no real `.env`, database, private-key file, persistent diagnostic log or downloaded media file was tracked. `.env.example` is a template. Trellis `.jsonl` files are context manifests rather than runtime logs.
- `.gitignore` excludes local `.runtime/` configuration, actual `.env` files and runtime database/log/media locations. This protection does **not** hide files already committed in history.

### Confirmed personal-information exposure

The repository cannot currently be described as containing no private or identifying information:

1. **Commit identity email:** all **6 reachable commits** use **one external email address** for both author and committer. It is **not a GitHub noreply address**. Public visibility exposes that email through Git history. One author/committer name or pseudonym is also present. The actual values are intentionally omitted from this report.
2. **Development journals and personal incident context:** `.trellis/workspace/*/journal-1.md` and `.trellis/workspace/*/index.md` are tracked remotely. They include a developer alias, session dates and references to the account/Bot incident investigation. Relevant remote-journal locations include lines `725`, `728`, `733`, `737`; the index references related sessions at `:47`. These files can reveal project activity and the personal troubleshooting timeline. Paths containing `*` intentionally mask the developer directory alias.
3. **Task/research context:** `.trellis/tasks/10-19-telegram-media-downloader/prd.md:28` records dated account-incident scope; `.trellis/tasks/10-19-telegram-media-downloader/research/bot-ban-code-audit.md:3`, `:50`, `:71` preserve the investigation context. These are internal development records, even though no credential was identified in them.

**Absolute path review:** broad initial path heuristics produced many false positives from type annotations, URI suffixes, regular expressions and generic examples. A more specific review did **not** identify a real local Windows account name or the actual development workspace path in remote content. Generic path examples in `.codex/hooks/session-start.py` are not proof of a personal path disclosure. Do not report the broad heuristic counts as confirmed path leaks.

### Public documentation artifacts versus public repository history

- A static documentation build that excludes `.trellis`, runtime files and internal historical documents does not remove those files from the GitHub repository.
- Deleting or redacting a journal in a new commit does not remove its previous versions, nor does changing future Git identity remove the email from the existing six commits.
- Before making the repository public, decide whether to accept the already committed email and internal development history as public. If not, prepare a separately reviewed history rewrite or a clean public repository/export; this audit does not authorize rewriting history, deleting remote refs or changing visibility.
- Future commits should use a GitHub-provided noreply address if the owner wants email privacy. Use the actual address shown in the account's GitHub email settings; do not invent a numeric GitHub noreply identifier.
- A docs-only workflow should upload only the selected static build directory. It should not upload the repository root or install project/runtime credentials. This is a separate publication-scope control, not a substitute for history review.

## Files Found

- `.gitignore`: excludes runtime state, local secrets and build outputs.
- `.env.example`: deployment configuration template, not actual runtime credentials.
- `.trellis/workspace/*/journal-1.md`: remotely tracked session journal with activity dates and incident context.
- `.trellis/workspace/*/index.md`: remotely tracked developer/session index.
- `.trellis/tasks/10-19-telegram-media-downloader/prd.md`: dated internal requirements additions.
- `.trellis/tasks/10-19-telegram-media-downloader/research/bot-ban-code-audit.md`: prior account/Bot incident investigation.
- `backend/tests/test_request_safety.py`, `backend/tests/test_request_safety_runtime.py`: synthetic token fixtures.
- `frontend/e2e/managed-api.spec.ts`: deterministic test API Hash values.
- `scripts/tests/test_acceptance.py`: synthetic credential-URL test fixture.

## Related Specs / References

- `.trellis/spec/backend/diagnostic-log-contracts.md`: excludes credentials, raw URLs, media names and Telegram identifiers from diagnostic events.
- `.trellis/spec/frontend/documentation-contracts.md`: public-site source boundaries and internal-document exclusion.
- GitHub email privacy documentation: https://docs.github.com/en/account-and-profile/how-tos/email-preferences/setting-your-commit-email-address
- GitHub sensitive-data removal guidance: https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository
- References are provided for follow-up; no online vendor audit service was used.

## Caveats / Not Found

- Pattern-based inspection cannot prove that an arbitrary secret, encoded value, high-entropy string or personal caption is absent. No guarantee of a completely clean repository is justified.
- This scope covers fetched, reachable main history, not deleted/unadvertised refs, GitHub pull-request refs, server-side retained unreachable objects, caches, forks, LFS payloads, releases, packages, Issues/PR comments, Actions logs/artifacts or repository settings/secrets. No LFS/submodule payload was fetched; remote HEAD has no submodule entries and the audited blobs were text.
- Did not access actual local runtime configuration, actual Telegram credentials, actual media or live diagnostic files; did not compare source against runtime secret values.
- The remote may change after the fetched snapshot, and local source changes may occur after the 291-file initial scan. Recheck the exact final commit before changing visibility.
- No repository visibility change, history rewrite, force-push, deployment or actual user-log cleanup was performed.

## Final source-only recheck before documentation push

- Date: 2026-10-21. User explicitly accepted the previously identified commit-email/internal-notes disclosure; no history rewriting was requested or performed.
- Rechecked **293 existing tracked/nonignored source files** after feature commit `76ea00a`, including the pending documentation, documentation workflow, publishing helpers, specifications and this report. Actual ignored runtime credentials and installed/build artifacts remained excluded.
- Results: **no new confirmed real credentials identified**. Strong-pattern matches remain the same eight synthetic Telegram-token test occurrences, three deterministic API-Hash fixture occurrences and one example-domain credential-URL fixture occurrence. Other broad secret-assignment matches remain code types, environment interpolation or test fixtures. No private-key/provider-token pattern was found.
- This is an offline source-pattern recheck of that working-tree snapshot, not a guarantee against every possible credential format or a fresh audit of external GitHub content. It does not withdraw the accepted privacy findings above.
