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

## 3. Contracts

Default confirmation text (actual newlines):

```text
确认下载：{title}
保存到：{destination}
确认有效期：{expires_in_minutes} 分钟
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
Filename variables are `name` (stem), `date` (YYYYMMDD), `time` (HHMMSS),
`datetime` (YYYYMMDD_HHMMSS), `type` (video/image), `index` (1-based integer in
message order), and `id` (short media item ID). Date/time use request creation time
and the configured timezone when the card freezes.

`title` is the persisted caption-derived request title without an appended request ID,
shared with the task API and Bot status/result messages; do not derive a separate
display-only title in a template consumer. `destination` follows date-first
archives (`YYYY-MM-DD` for singles; a safe caption/fallback group folder below
that date for groups), as defined in runtime-contracts.md. Caption text never
changes filename template variables or creates extra path levels. Old cards and
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
extension-preserving byte limits, deterministic collisions, card-time snapshots,
missing-snapshot rejection and current defaults. Browser tests verify persistence,
example preview, reset, invalid input and short mobile layout. No real Bot or
existing runtime data is used by these tests.

## 7. Wrong vs Correct

Wrong: recompute filenames from current settings during download or use unrestricted
`str.format` / JavaScript interpolation on user text. Correct: validate a limited
text grammar, substitute once and persist the planned filename before consent.
