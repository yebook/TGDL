# Confirmation and filename templates

## 1. Scope / Trigger

Read for changes to settings templates, confirmation cards, planned filenames,
task creation and the frontend template editor. Templates are plain text, never
scripts, expressions or filesystem paths.

## 2. Signatures

`UserSettings` and `SettingsPatch` expose `confirmation_template` and
`filename_template`. Existing GET/PATCH settings and candidate validation carry
these fields with the usual revision, source and editable semantics.
`MediaItem.planned_filename` is nullable during collection and mandatory when
confirming a frozen card.
`app.bot.messages.status_label(state: str) -> str` formats Bot-only state labels;
it never changes persisted states or the API's enum values.

## 3. Contracts

Default confirmation text (actual newlines):

```text
📥 确认下载：{title}
📁 保存到：{destination}
⏳ 确认有效期：{expires_in_minutes} 分钟
{files}
```

Default filename template: `{name}_{id}`. Confirmation variables are `title`,
`file_count`, `total_size`, `files`, `destination`, `expires_in_minutes`.
The minute variable uses at most two decimals with trailing zeros removed.
Unsupported variables are rejected; no old-template rewriting or aliases.
Existing notification text and card deadlines stay unchanged. File sizes
use the existing readable-unit convention, with unknown totals remaining unknown.
`files` lists the planned saved filenames with readable sizes. Rendered Bot text
is bounded to 3500 UTF-16 units. On overflow, reserve literal text then budget
each placeholder occurrence, shortening values with `…` so a long early field
does not hide all later fields. Apply a final surrogate-safe cap if the literal
text alone exceeds the limit. Example preview follows the same bound.

Bot-owned messages use concise Chinese status text with sparse meaningful emoji;
never show internal status enums as user-facing result or `/status` labels.
Emoji decorate messages/buttons only, never persisted titles, filenames, folder
names, callback data or event keys. Keep the system task-ID line unchanged and
reserve UTF-16 space for the complete ID, including astral emoji in the body.
Use actionable safe reasons for known failures and WebUI guidance for unknown
errors, without interpolating arbitrary upstream exception text. Preserve the
existing notification cadence and all authorization/state/request-safety rules.
Changing the default must not rewrite saved custom templates or queued payloads;
existing users can explicitly restore the default and save it in WebUI.
Filename variables are `name` (stem), `date` (YYYYMMDD), `time` (HHMMSS),
`datetime` (YYYYMMDD_HHMMSS), `type` (video/image), `index` (1-based integer in
message order), and `id` (short media item ID). Date/time use request creation time
and the configured timezone when the card freezes.

`request_caption(items)` selects the first nonblank normalized caption from
message-ordered members. `planned_name(..., *, caption=None)` uses a nonblank
original filename first, that shared caption second, and the existing generated
media name last. `{name}` is the resulting stem. A caption containing dots is
text, not a source extension; retain the media's normal extension independently.
For caption-derived group names reserve `_<message-order-index>` immediately
before the extension after rendering/truncation, unless the template already
contains an actual `{index}` placeholder. Escaped `{{index}}` is literal text and
does not suppress the suffix. Original-name members retain their ordinary
template behavior; all members still participate in deterministic collision checks.
For an indexed template, reserve its literal/other-variable/extension budget
before truncating caption substitutions (including repeated `{name}` fields),
so the actual index survives. If template literals alone exceed the byte cap,
reserve an end suffix as a fallback even when an index placeholder exists.

`title` is the persisted caption-derived request title without an appended request ID,
shared with the task API and Bot status/result messages; do not derive a separate
display-only title in a template consumer. `destination` follows date-first
archives (`YYYY-MM-DD` for singles; a safe caption/fallback group folder below
that date for groups), as defined in runtime-contracts.md. Caption text never
changes the template grammar or creates extra path levels. Old cards and
their stored destinations/titles remain unchanged.

New task notifications append a system-owned line `任务编号：<full request ID>`
after rendering the body, outside the editable confirmation template. Apply this
once at the enqueue boundary for confirmations, queued notices and results;
status replies use the same formatting per task. Reserve space for the complete
ID line within the message's UTF-16 budget, including aggregate status replies.
Do not add a new template variable or rewrite existing custom templates or
already-stored notifications. Preview includes the same separate system line.

Both templates accept only simple whitelisted placeholders; `{{` and `}}` escape
literal braces. Reject unknown names, attribute/index access, conversions and
format specifications. Render once: substituted filenames containing braces
must not become new placeholders. Confirmation templates accept 1..2000
characters, filename templates 1..160; reject whitespace-only text. Filename
templates reject path separators, control characters and Windows reserved symbols.

The system retains a safe original extension (`.[A-Za-z0-9]{1,16}`) or appends
the media default (`.mp4`/`.jpg`), sanitizes substituted
values and bounds UTF-8 filename length while preserving the extension. Templates
do not change the archive directory. Same-group collisions resolve deterministically;
final publication still never overwrites an existing file.

Freeze planned filenames when issuing a new confirmation card; confirmed jobs use
those snapshots. Later template/timezone edits affect new cards only. A card
missing its planned filename is invalid and must not invent a new filename.
Job retry/restart preserves its stored path; existing successful files are untouched.

The WebUI shows editable templates, whitelisted variable help, example previews
and explicit restore-default controls. Examples are labeled as examples and never
perform a real download. Previews render text without interpreting HTML. Saving
remains offline and does not mean the Bot is connected.

## 4. Validation & Error Matrix

| Condition | Behavior |
| --- | --- |
| Bad braces, unsupported variable or expression | Local validation rejects; no Telegram call |
| Empty/whitespace or out-of-range length | Reject; preserve saved settings |
| Filename template contains directory/control syntax | Reject; never create template-defined directories |
| Original name contains unsafe characters | Sanitize substituted value; retain safe extension |
| Original name missing/blank | Shared caption stem, otherwise generated fallback |
| Caption-derived group name | Numeric message-order index; preserve suffix within byte limit |
| Unknown Bot status/error | Safe Chinese fallback and WebUI guidance, no raw enum/exception |
| Settings revision conflict | 409; retain unsaved editor input |
| Template changed after card generation | Existing planned names stay fixed |

## 5. Good / Base / Bad Cases

Good: `{date}_{name}` produces a safe dated filename plus its extension and
survives retries unchanged. Base: defaults retain the original-stem/short-ID
convention. Bad: using Python format traversal, treating a substituted filename's
braces as another expression, or silently renaming an already confirmed job.

## 6. Tests Required

Assert template validation and offline persistence, all supported variables,
escaped/literal braces, readable/unknown sizes, original-name sanitation,
shared caption fallback across mixed groups, numeric suffix preservation,
extension-preserving byte limits, deterministic collisions, card-time snapshots,
missing-snapshot rejection and current defaults. Browser tests verify persistence,
example preview, reset, invalid input and short mobile layout. No real Bot or
existing runtime data is used by these tests.

For Bot-copy changes, verify default/backend-preview parity, Chinese status/result
labels, meaningful failure guidance, confirmation/cancel/expired callback feedback,
unchanged callback payloads, complete separate IDs at emoji-heavy UTF-16 limits,
and custom template preservation. Use focused helper/integration tests rather
than real Telegram requests for presentation-only edits.

## 7. Wrong vs Correct

Wrong: recompute filenames from current settings during download or use unrestricted
`str.format` / JavaScript interpolation on user text. Correct: validate a limited
text grammar, substitute once and persist the planned filename before consent.
