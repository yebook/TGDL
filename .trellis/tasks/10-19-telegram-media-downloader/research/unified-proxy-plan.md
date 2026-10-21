# Research: Unified deployment and WebUI proxy/API control

- Query: Replace direct/proxy Compose variants with one deployment; operate proxy and API mode through WebUI with minimal host intervention.
- Scope: internal source/deployment/spec research; no Docker, Telegram or private runtime inspection.
- Date: 2026-10-21

## Findings

### Files found and current patterns

- `compose.yaml`: current app plus nonroot managed API, no network sidecar; `TGD_PROXY_CAPABLE=false`.
- `compose.proxy.yaml`: complete alternate deployment, app accesses `http://telegram-network:8081`, API shares network namespace with network sidecar; sing-box, TUN device and narrow Linux capabilities.
- `deploy/network/supervisor.py:124`: sing-box 1.11.15 TUN config; HTTP CONNECT/SOCKS5 endpoint, remote DNS and no direct outbound configured.
- `deploy/network/supervisor.py:165`: IPv4 default-deny firewall; nonroot API cannot use bootstrap DNS or arbitrary direct outbound. IPv6 also default-deny (`:207`).
- `deploy/network/supervisor.py:280`: disabled proxy returns `restart_required` rather than applying direct mode; this is the primary behavior to replace.
- `deploy/network/supervisor.py:321`: startup installs blocked firewall before reporting ready; no initial desired config leaves networking blocked. Network polling is once per second but probes only changed revisions, not each tick.
- `deploy/network/supervisor.py:243`: network acknowledgement currently lacks heartbeat; stale `applied.json` could be presented as active after supervisor stops.
- `deploy/network/Dockerfile`: pinned sing-box upstream source; iptables/iproute2 runtime; GPL source license already included.
- `backend/app/services/settings.py:77`: cloud proxy status checks verified app connection signature; local disabled on proxy-capable deployment currently reports redeployment required.
- `backend/app/services/settings.py:173`: independent network revision remains stable across unrelated setting saves.
- `backend/app/services/settings.py:187`: desired writer uses current settings and skips cloud mode. Must support explicit snapshot parameters for migration.
- `backend/app/services/local_api.py:37`: persistent operation snapshot and original cloud proxy snapshot preserve migration semantics. Proxy-only changes in idle currently do not schedule an operation; failed-operation proxy edits do.
- `backend/app/services/local_api.py:359`: preflight only waits for proxy when enabled; writes CURRENT settings although checks FROZEN operation values. Disabling will need the same acknowledgement barrier; snapshot/write mismatch must be corrected.
- `backend/app/workers/runtime.py:131`: managed API and request-safety gates precede client creation, but local network acknowledgement is not checked there.
- `backend/app/workers/runtime.py:263`: proxy handoff waits for active downloads only; dispatch proxy gate currently treats disabled as immediately safe. Poll/notify loops need the same desired/applied network barrier, not only scheduler.
- `deploy/bot-api/supervisor.py:47`: private command/key reads enforce owner/mode and reject symlinks; startup uses mode 700 control directory. Do not loosen for Windows host binds.
- `deploy/app-entrypoint.py`: nonroot startup, no bootstrap root permission initializer.
- `.env.example`: private API control/network currently user-managed host bind paths; docs currently require manual mkdir/chown/chmod.
- `frontend/src/views/SettingsView.vue:241`: local proxy help requires alternate deployment and contrasts cloud implementation details; replace with unified workflow.
- `docs/deployment.md:170`, `docs/operations.md:3`: alternate-file and redeploy instructions to remove.

### Recommended minimal implementation

1. Make root `compose.yaml` the single supported topology: app + nonroot API + resident `telegram-network`. Reuse proxy topology, retain app separate network, and keep the API inaccessible from host ports. Delete alternate Compose rather than preserve compatibility wrappers; this is pre-release.
2. Default proxy disabled: the network supervisor accepts an explicit validated `enabled=false` desired command and atomically installs an intentional direct policy, stops sing-box, then acknowledges that exact revision as direct/disabled. Missing/invalid command is blocked, never implicitly direct. Direct mode must permit DNS and IPv4/IPv6 deliberately; documenting IPv4-only is also valid if IPv6 remains blocked consistently.
3. Enable or replace proxy: pause new local requests/claims, drain live owners and durable active downloads, close/join polling and notification requests, install DROP BEFORE resolution/start/verification. Stop old sing-box before routing changes. Only after endpoint validation, config validation, TUN start and fixed TLS probe acknowledge active. Failure remains blocked; it must not restore previous direct access when enabled intent failed. A validated prior proxy may be restored, but desired mismatch still reports failure and blocks new work.
4. Treat local direct and proxied modes as equally acknowledgement-gated. Pass frozen settings + explicit network revision into desired writer and status comparisons. Include proxy changes in a network-only persisted operation, or implement equivalent durable barrier covering poll/notify/client/dispatch. Do not reuse API switch flow in a way that logs out merely for changing proxy.
5. Cloud uses existing app HTTP/SOCKS connector; no TUN dependency in cloud request path. On cloud-to-local transition, apply and verify target network settings first, but perform cloud getMe/logOut through ORIGINAL cloud proxy snapshot. Only verified target identity commits API mode. On local-to-cloud, honor existing cloud cooldown and stop local child first as current flow specifies.
6. Persisted logout_done, logout_uncertain, 401 pause and 429 cooldown survive network edits/restarts. No direct fallback on enabled proxy failure, no cloud fallback on local verify failure, and no repeated logOut on ordinary retry. API/network status reads remain observation only.
7. Add network supervisor heartbeat and acknowledge revision + route mode. Status must reject stale/mismatched/invalid files; UI shows applying/ready-direct/active-proxy/failed/unavailable plainly. Distinguish API process state from Bot identity readiness.
8. Use named Linux volumes for API control and network handoff, initialized by a fixed one-shot root helper (or narrowly scoped bootstrap in existing root network service) with app UID/GID. API control mode 700 and app ownership; network share ownership/group 770 with desired secret files 640. Separate control and network volumes; network service must not read API key/hash. Helper mounts no app data/downloads/cache and cannot execute arbitrary user commands. Avoid recursive chmod/chown of user data. Preserve project-relative host data/download/cache paths.
9. Remove `.env` API ID/Hash fallback from normal public quick start and configure through WebUI. Removing old fallback code is optional cleanup, not necessary for unifying routing; never replace actual deployment credentials or delete private volumes. Retain host-only parameters for bind address, paths, UID/GID and subnet.

### Unavoidable prerequisites / user-facing flow

- One Compose can remove alternate deployment files and Docker restarts for proxy switches, but the chosen resident TUN topology still requires `/dev/net/tun`, iptables/ip6tables and `NET_ADMIN` on Linux Docker host. fnOS kernel/device support must be verified; Docker Desktop provides a Linux VM, so Windows host browser/system proxy alone does not guarantee container connectivity.
- With unconditional Compose `devices: /dev/net/tun`, a host lacking TUN may reject container creation even when proxy disabled. Be explicit: choose guaranteed prerequisite support for the single deployment, or use a more complex optional TUN bootstrap device approach. Do not claim pure cloud deployment becomes portable to hosts without TUN automatically.
- Host proxy must listen on LAN/Docker-reachable interface with access allowed. `127.0.0.1` in containers is not host proxy; offer `host.docker.internal` via host-gateway and an inline hint. Host proxy software TUN is no longer required when app's local API TUN routes through reachable HTTP/SOCKS server; containers still need their own TUN capability.
- Fixed Docker subnet must not overlap LAN/VPN/Docker/TUN routes. Retain advanced optional subnet setting and concise conflict error.
- WebUI workflow: deploy once → initialize → Token/user allowlist → optional proxy → save → observe network state → local credentials and local mode → save → observe drain/network/API/identity → ready. Offline saves remain valid.
- Root network service capabilities remain limited and are never delegated to app/API; no privileged, Docker socket or host network needed.

### Targeted verification and affected documentation

- `deploy/network/test_supervisor.py`: direct acknowledgement, no-config block, both route direction transitions, DROP before enabled startup, enabled failure after previous direct never returns direct, dead sing-box, stale heartbeat, monotonic revisions, unchanged configuration avoids probes, permissions/symlinks/type/size limits.
- `backend/tests/test_local_api.py`: cloud-original versus target-local proxy snapshots; enable/disable gating before irreversible logout; retry after successful logout never replays it; network-only edits do not logout/restart child unnecessarily; failed edits remain recoverable and unsaved form preserved.
- `backend/tests/test_recovery.py`, `test_cloud_api.py`, `test_request_safety.py`: disabled pending ack blocks claims/poll/notify; drained owners preserve files; stale network prevents online; no proxy fallback; cooldown not reset. Adjust old redeploy assertions rather than maintain compatibility behavior.
- `backend/tests/test_log_retention.py`: unrelated log settings must preserve network desired bytes/revision and not reprobe.
- Browser settings/local API tests: same form switches cloud/local and proxy on/off, no Compose advice, saved versus applied states, blocked/failure reasons, dirty form preservation, desktop/mobile layout.
- Compose `config --quiet` with empty credentials; static mounts/caps/dependencies checks; never output expanded secrets. Actual root network integration needs isolated Linux smoke test; mocks cannot certify fnOS or real routes.
- Update `.env.example`, README, docs index/quick-start/deployment/telegram-api/operations/faq/local-run/verification and any docs-site search/link references to removed Compose file. Preserve unrelated docs-only Pages workflow.

### Related specs and references

- `.trellis/spec/backend/managed-api-contracts.md`: owner drain, cloud snapshot, durable logout/cooldown, private handoff, command acknowledgement; update deployment-topology-only restriction.
- `.trellis/spec/backend/cloud-api-contracts.md`: current legacy/manual sections are outdated versus managed default and can be simplified under pre-release policy; preserve download ownership/20 MiB invariants.
- `.trellis/spec/backend/runtime-contracts.md:85,122`: independent proxy revisions and no save-equals-active; replace redeploy-only disable rule.
- `.trellis/spec/backend/request-safety-contracts.md`: protection must persist across mode/proxy/client restarts and uncertainty remains explicit.
- Upstream provenance in `deploy/sources.md`; sing-box pinned v1.11.15 source revision `bc35aca01704497c179da1a03e45ad8e32f1a51b` in Dockerfile. No external web references consulted.

## Caveats / Not Found

- Research-only role: no product edits, no runtime/credentials inspection, no Telegram/Docker calls, no actual network certification.
- Task has no `research.jsonl`; read task PRD/design, workflow and directly relevant specs. Did not load implement/check manifests per role isolation.
- One Compose topology broadens default host requirements; this tradeoff must be reflected honestly in docs rather than hidden behind a WebUI switch.
- Existing direct local deployment cannot gain network namespace routing through an app-only image rebuild. A one-time deployment topology recreation is necessary; preserve actual mount identities/credential state and require deliberate local deployment work, separate from code edits.
