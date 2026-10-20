# Task record deletion

## 1. Scope / Trigger

Read when changing task removal, task queries/actions, Bot callbacks, notifications
or the task-list selection UI. This removes finished task records from normal use;
it never deletes media files or their deduplication/recovery metadata.

## 2. Signatures

- Migration `0005`: nullable `MediaRequest.deleted_at` epoch seconds; old rows NULL.
- `TaskSummary` and inherited `TaskDetail` add required `can_delete: bool`.
- `DELETE /api/v1/tasks/{id}` returns `{ "deleted_ids": [id] }`.
- `POST /api/v1/tasks/delete` accepts `{ "ids": [...] }` and returns
  `{ "deleted_ids": [...] }`; 1..100 unique nonempty request IDs, each at most
  100 characters. Unknown fields are rejected.
- Use the existing authenticated, same-origin and CSRF-protected request client.

## 3. Contracts

Eligibility is authoritative on the backend: a request must be terminal
(`succeeded`, `already_exists`, `partial_success`, `failed`, `cancelled`, `expired`)
and have only known terminal jobs (`succeeded`, `already_exists`, `failed`,
`cancelled`), or no jobs. Unknown job states are conservatively ineligible.
Pending confirmation, queued, downloading, saving,
cancelling, retry-wait or recovery-blocked requests require cancellation and
completion first. Check the whole batch in one immediate transaction; either mark
every requested row or change none. `can_delete` uses the same rule as mutation.

Soft deletion preserves requests/items/jobs, attempts, reservations, journals,
StoredFile entries and media bytes needed for historical deduplication and safe
recovery. Never unlink files or delete foreign-key dependencies. Exclude removed
requests from task listings/totals/detail/cancel/retry and Bot `/status`. An old
Bot button cannot enqueue/retry/cancel a removed request; authenticate ownership
before returning removed-record guidance. Keep media ingestion idempotency.

Suppress unsent request notifications on deletion and in notification dispatch;
do not send new task notifications for removed records. Durable worker ownership
and uncertain upstream slots must not be cleared as part of removal. Existing
in-flight transport acknowledgements cannot reverse a deleted marker or crash
because a retained outbox row vanished.

The UI selects eligible tasks on the current page only. Polling preserves chosen
IDs while pruning missing/ineligible rows; page/filter/search changes clear the
selection. Separate checkbox, details and delete controls without nested buttons.
Confirm before single/batch deletion and say media files are kept. On success,
refresh, close removed detail and correct an empty last page. On failure, keep
selection and display the actual error. Prevent duplicate submissions.

## 4. Validation & Error Matrix

| Condition | Result |
| --- | --- |
| No session / invalid CSRF or Origin | Existing 401 / 403 protection |
| Empty, oversized, repeated or malformed IDs | 422; no mutation |
| Missing/already removed request | 404; entire batch unchanged |
| Nonterminal request or nonterminal/unknown job state | 409 `TASK_DELETE_BUSY`; entire batch unchanged |
| Eligible single/batch | Persist marker, hide record, keep files and dedup entries |
| Deleted task's old Bot button | Friendly removed-record guidance; no task mutation |

## 5. Good / Base / Bad Cases

Good: remove two completed records; after restart they remain hidden and sending
the same media still finds its saved file. Base: a cancelled request is removable
after jobs stop. Bad: delete a request row with file/journal foreign keys, erase
an active download, or partially apply a mixed eligible/busy batch.

## 6. Tests Required

Use isolated temporary data. Assert actual persisted markers and unchanged media
bytes/dedup references; hidden list/detail/action behavior; no duplicate Bot work
or notification dispatch; auth/CSRF, input bounds, batch rollback and activity
checks; old-row migration and restart persistence. Target UI selection/polling,
cancelled confirmation, single/batch success/failure, pagination and mobile controls
through actual APIs. Never perform deletion against the user's local deployment.

## 7. Wrong vs Correct

Wrong: determine eligibility only from the rendered status and hard-delete a
request with storage dependencies. Correct: derive `can_delete` and enforce it
transactionally, retaining filesystem/dedup/recovery snapshots behind the marker.
