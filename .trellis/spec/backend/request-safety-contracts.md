# Telegram request safety contracts

## 1. Scope / Trigger

Read before changing TelegramClient, runtime intake/dispatch/outbox, cloud file
HTTP, manual validation, managed migration or request protection state. This is
request hygiene, not a guarantee against Telegram restrictions or a diagnosis
of personal account logout. No personal-account login is introduced.

## 2. Signatures

`BotStatus.request_safety` contains `state: ready|throttled|backoff|auth_paused`,
`retry_at: ISO string|null`, a sanitized Chinese `message` and `can_retry: bool`.
`POST /api/v1/system/bot/retry` returns this status and uses existing auth,
same-origin and CSRF protection. The operation targets saved credentials only.
Use a separate migrated persistent model rather than local API lifecycle JSON.

## 3. Contracts

All clients share app-owned persistent protection: polling, sends, callbacks,
identity, getFile, logout, candidate validation and migration clients. Cloud file
HTTP must observe equivalent gating and 429/401 reporting. Do not gate each
stream byte chunk. Key state by credential digest; never persist raw tokens,
proxy passwords, token-bearing URLs or original upstream exception messages.
Mode/proxy changes and client/application restarts cannot reset a cooldown.

The private local `tgdGetFileProgress` snapshot read is an explicit exception to
Telegram request accounting: it reads existing native Client memory only and
must not initialize a Bot, invoke TDLib/network operations or alter flood/account
statistics. Keep it outside TelegramClient.request and RequestSafetyService
before/observe; its success cannot clear backoff/auth pause and its failure
cannot consume attempts or stop an already-owned download. See
[local-progress-contracts.md](local-progress-contracts.md).

Conservative application defaults are one second between Bot request starts,
1.1 seconds between sends to a private chat, and 3.1 seconds to a group. These
are application choices, not official safe thresholds. Reserve starts atomically
in short transactions, then release locks before remote I/O. Long polling and
streaming must not serialize unrelated requests for their full duration.
Use an explicit accepted reservation result; elapsed time after a denied
reservation never proves that a request owns a slot.

Any RetryAfter extends shared Bot cooldown using the maximum deadline; later
success never shortens it. Invalid/revoked credentials pause automatic requests
persistently. A chat-specific Forbidden response does not revoke the whole Bot.
Cloud HTTP must parse numeric and HTTP-date Retry-After headers and JSON waits,
using the longest applicable deadline without truncating long waits to one day.
Use bounded exponential network backoff. Manual recovery may probe saved
credentials at most once per 60 seconds; it cannot skip server cooldown or
managed-mode restrictions, execute logout, or clear auth pause without success.
Status reads and offline saves never probe or clear protection.
Cloud file proxy failures include aiohttp_socks ProxyConnectionError,
ProxyTimeoutError and ProxyError, even when not subclasses of ClientError;
they must establish the same shared backoff as ordinary network failures.

Distinguish short normal pacing from blocked/cooldown deferral. Do not wait for
minutes while holding connection/poll/notify locks. Skip new claims and outbox
attempt increments while blocked; final request gating must handle races without
burning retry budgets. Preserve durable results, pending tasks, file snapshots
and active local acquisition ownership. Persistent protection write failures
must not silently allow an untracked request.
Validate persisted protection values for type, bounds and finiteness; malformed
state fails closed with a sanitized diagnostic rather than allowing requests.

Check safety before recording irreversible logout intent. A provably unsent
request is deferral, not an uncertain logout; an actually issued logout with an
unknown result retains the existing durable ambiguity barrier. Neither manual
recovery nor validation can replay that logout or reconnect cloud prematurely.
An authentication rejection before logout must leave a failed, editable
migration instead of trapping the operator in draining. Saved-token recovery
may probe cloud identity only for known-applied-cloud, failed, pre-logout state
without an active cloud cooldown. It must not switch modes or replay logout.
For a known successful logout followed by local authentication rejection,
identity recovery may target local only when saved/target mode and credential
snapshot match, the operation failed without uncertainty, and the current
supervisor command is running with a fresh heartbeat. Recovery never commits
the mode transition; normal migration retry must complete it afterward.

## 4. Validation & Error Matrix

| Condition | Required behavior |
| --- | --- |
| Concurrent Bot / same-chat requests | Atomically spaced starts |
| RetryAfter from any method or file HTTP | Persist maximum deadline; shared wait |
| App/client/proxy/mode changes | Existing cooldown/auth pause retained |
| Token Unauthorized | Pause that credential, no automatic identity loop |
| One chat Forbidden | Fail that notification, other chats remain eligible |
| Connection failures | Bounded backoff with visible retry deadline |
| Saved-token manual recovery | Auth/CSRF, probe interval, same barriers |
| Paused dispatch/outbox | Retain records and budgets; no upstream request |
| Active local owner during pause | Do not release slot or claim upstream stopped |
| Cooldown before logout | No logout call or false logout uncertainty |

## 5. Good / Base / Bad Cases

Good: a getFile 429 stops the next notification and validation probe until its
deadline, including after restart. Base: private messages are paced and file
copies continue locally. Bad: rebuilding TelegramClient resets protection; a
401 starts a five-second retry loop; a known unsent gate failure becomes unknown
logout; selecting another pending notification bypasses chat/Bot cooldown.

## 6. Tests Required

Use fake clocks/transports and temporary persistence. Verify concurrent spacing,
shared maximum cooldown, restart, token isolation, auth pause, manual recovery
authentication/interval, direct file HTTP 429/401, no per-chunk gating, polling
backoff, deferred budgets and retained local ownership. Cover candidate/migration
barriers and actual unknown logout separately. Status must show saved-token
protection even when stale runtime connection state is online. Browser checks
use real REST in isolated fixtures; no real Telegram or user data access.

## 7. Wrong vs Correct

Wrong: retry a different notification immediately after one receives 429, or
clear the server deadline with a successful concurrent poll. Correct: all
request paths consult the same persisted maximum cooldown and defer cleanly.
