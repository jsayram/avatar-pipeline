# Web UI Dashboard — Implementation Plan

> **Status: Phase 1, Phase 2, and Phase 3 implemented (2026-07-03).** This
> document is the agreed design for a local web dashboard. Implementation
> happens in phases (see [Milestones](#milestones)) — each phase is
> independently useful, so the plan can be executed across multiple sessions.
> Next implementation target: Phase 4 integrations.

## Why this exists

The pipeline currently has **no native UI**. All human interaction flows
through:

- **Telegram** — approve/reject the two gates, text in TikTok links
- **iCloud Numbers sheets** — daily link queue + read-glance status CSV
- **Terminal/log files** — service health, run logs, debugging

The goal is a single **dark, WaveSpeed-style web dashboard** ("mission
control") that mirrors everything Telegram can do, plus full visibility of
every moving part: pipeline queue, pending approvals with images, service
health, log tails, WaveSpeed balance, Tailscale status, RunPod pods.

Decisions locked in with the operator:

| Decision | Choice |
|---|---|
| Access | **Tailnet-only** via `tailscale serve` (never public Funnel) — phone access anywhere, zero public exposure |
| "Pods" panel | **RunPod GPU pods** via RunPod REST API (`RUNPOD_API_KEY` in `.env`, degrades gracefully if unset) |
| Frontend | **FastAPI + single-page vanilla HTML/JS** (clones the `scripts/face_gate.py` service pattern, no npm build) |
| Scope | **Full control in v1** — submit links, approve/reject both gates, provider toggle |

### Hard constraints

1. **Approval gates are structurally unbypassable** (standing code-enforced
   rule — same as the Telegram bot). There is deliberately **no endpoint that
   calls `run_full`**. The provider toggle changes *which provider* runs a
   stage, never *whether the human gate happens* — the gates live in the
   worker phase structure (`save_pending` → exit → separate resume
   invocation), which the dashboard cannot skip.
2. **No sensitive values written to tracked files.** The real ts.net
   hostname, balance, pod info, chat ids are fetched at runtime from
   `.env`/config/CLI/live APIs, returned as JSON, rendered in the DOM —
   never persisted. The repo stays clean to push publicly.
3. Config-driven everything, no hardcoded paths, tests in `tests/`,
   **zero new Python dependencies** (`fastapi`, `uvicorn`, `pydantic`,
   `requests` are already in `requirements.txt`).

## Architecture

One new long-running FastAPI service `scripts/dashboard.py`, bound to
**127.0.0.1:8190**, exposed to the tailnet only via `tailscale serve --bg
8190`. Same service pattern as `scripts/face_gate.py`: module-level app,
uvicorn on localhost, launchd plist in `ops/launchd/`, log at
`~/Library/Logs/avatar-dashboard.log`.

```
Browser (tailnet) → tailscale serve → 127.0.0.1:8190 dashboard.py (FastAPI)
                                          │
   ┌── direct import ──┬── subprocess ────┼── HTTP probes ──┬── HTTPS APIs ──┐
   worker.run_*     tailscale/launchctl   n8n :5678         WaveSpeed balance
   lib/{status_sheet, (read-only,         ComfyUI :8188     RunPod REST
   pending,state}     list-args)          gate :8189
```

Key principles:

- **Direct library calls, not shell-outs.** The dashboard imports `worker`
  and calls `run_prepare / run_generate_avatar / run_reject_frame /
  run_animate / run_regenerate` (each returns `(dict, exit_code)`), exactly
  as `scripts/handle_telegram_reply.py` already does.
- **Single background job thread** (`queue.Queue` + daemon thread). The
  pipeline's own invariant is one link in flight / one pending approval at a
  time (`lib/pending.py`, `lib/processing_lock.py`), so one worker thread is
  the correct concurrency model — no celery/redis. POST endpoints return
  `202 {job_id}`; the UI polls `GET /api/jobs/{id}`.
- **Telegram stays in sync for free.** The worker phase functions themselves
  send the Telegram photos/messages, so a dashboard approval still produces
  the normal Telegram trail.

## New files

| File | Purpose |
|---|---|
| `scripts/dashboard.py` | FastAPI app; `create_app(config_path)` factory for testability; `--config`/`--port` flags (+`DASHBOARD_PORT` env, default 8190); serves `scripts/static/` |
| `scripts/lib/dashboard_jobs.py` | `JobManager`: one worker thread + queue; `JobRecord` dataclass (state queued/running/done/error, worker result dict, exit_code, timestamps); `deque(maxlen=50)` history; stdlib only |
| `scripts/lib/config_overrides.py` | Guarded provider-toggle writer → `config.overrides.yaml` (see [Provider toggle](#provider-toggle)) |
| `scripts/lib/approval_lock.py` | Per-id claim file `work/<id>/.approval_action.lock` closing the Telegram-vs-dashboard double-approve race; clones `lib/processing_lock.py` mechanics (`O_CREAT\|O_EXCL`, stale-steal ~2h — must exceed the 1800s WaveSpeed animate poll) |
| `scripts/lib/service_health.py` | HTTP probes (n8n, ComfyUI `/system_stats`, gate `/health`) + `launchctl list <label>` (read-only), gathered in parallel via a small ThreadPoolExecutor |
| `scripts/lib/tailscale_status.py` | `tailscale status --json` + `tailscale serve status --json` (list-args, no shell); returns hostname/serve/funnel maps; flags `dashboard_funneled` → drives a red UI warning (tailnet-only rule) |
| `scripts/lib/runpod_pods.py` | Mirrors `lib/wavespeed_balance.py`: `GET https://rest.runpod.io/v1/pods` with Bearer `RUNPOD_API_KEY`; returns `{"configured": false}` when the env var is unset (verify exact REST field names against docs.runpod.io at implementation time) |
| `scripts/lib/log_tail.py` | Reverse block-read tail (no full-file load) + fixed allowlist registry (`n8n/comfyui/gate/dashboard` → `~/Library/Logs/avatar-*.log`; `run:<id>` → `work/<id>/run.log` with id validated against existing dirs). Never accepts raw paths |
| `scripts/static/index.html` + `app.js` + `style.css` | Single dark page (near-black `#0a0a0f`, panel `#141420`, blue/violet accents); vanilla `fetch` pollers (status 5s, jobs 2s while active, balance/pods/tailscale 60s) |
| `ops/launchd/com.jramirez.avatar.dashboard.plist` | Clone of the gate plist; logs → `~/Library/Logs/avatar-dashboard.log` |
| `tests/test_dashboard_{api,jobs,media}.py`, `tests/test_{config_overrides,approval_lock,service_health,tailscale_status,runpod_pods,log_tail}.py` | See [Testing](#testing) |

## Modified files

| File | Change |
|---|---|
| `scripts/lib/config.py` | (1) `load_config` merges `config.overrides.yaml` (whitelisted keys only) before validation; (2) new optional `Dashboard` dataclass (port 8190, n8n_url, launchd_labels, log_tail_lines) — all defaulted so existing configs keep working |
| `scripts/handle_telegram_reply.py` | `_handle_yes_no` wraps dispatch in `approval_lock.try_claim`/release (~10 lines); on a held claim replies "that approval is already being processed" |
| `config.example.yaml` | Commented `dashboard:` section + `endpoints.n8n_url` |
| `.env.example` | `RUNPOD_API_KEY=` (optional; dashboard degrades gracefully) + `DASHBOARD_PORT` note |
| `.gitignore` | `config.overrides.yaml` (runtime-written, machine-specific) |
| `README.md`/`SETUP.md` | Document the service, `tailscale serve --bg 8190`, plist install |

## API endpoints

All JSON; errors are FastAPI-style `{"detail": ...}`. Config reloaded on
mtime change of `config.yaml`/`config.overrides.yaml`.

| Endpoint | Notes |
|---|---|
| `GET /` | `index.html` |
| `GET /api/status` | `lib/status_sheet.build_status_rows(cfg)` in a threadpool with a 5s TTL cache (numbers-parser is genuinely slow); adds `/api/media/...` URLs for outputs |
| `GET /api/pending` | `find_pending_id` + `load_pending`; converts absolute frame/avatar paths to media URLs |
| `POST /api/links` `{url}` | Same flow as `_handle_new_link`: extract id → pending guard → `processing_lock.try_acquire` → job runs `run_prepare`, lock released in `finally`. **Parity: also appends to the `linksThroughTelegram` archive** (same `lib/telegram_links_archive.append_link` call the Telegram path makes) so the "keep" log stays complete regardless of intake surface. `409` if a pending approval exists or the lock is held, `422` if not a TikTok URL |
| `POST /api/queue/run-next` | Runs the noon cron's logic on demand: `pick_next.select_next` → same pending/lock guards → job runs `run_prepare`. Lets the operator pull the next queued link without waiting for the schedule or texting the URL manually. `409`/`204` (queue empty) semantics |
| `POST /api/flagged/{id}/unflag` | Removes the id from `processed.json`'s `flagged` list via a new whitelisted `lib/state.py` helper (`unflag(path, id)`, atomic write) — replaces today's manual JSON hand-edit. Confirm-guarded in UI ("this link becomes eligible for the pipeline again"). This is the ONE deliberate exception to the no-direct-state-mutation rule, because no worker function exposes it |
| `POST /api/pending/{id}/decision` `{stage, decision}` | Verify pending id+stage match the request (protects stale browser tabs) → `approval_lock.try_claim` → job dispatches the exact Telegram mapping: frame+yes→`generate_avatar`, frame+no→`reject_frame`, avatar+yes→`animate`, avatar+no→`regenerate`. `409` on mismatch / claim held / already resolved |
| `GET /api/jobs`, `GET /api/jobs/{id}` | JobRecord polling |
| `GET /api/media/work/{id}/{filename}`, `GET /api/media/out/{relpath}` | `resolve()` then `is_relative_to(root)` containment + extension allowlist (`.png .jpg .jpeg .webp .mp4 .mov`) — defeats both `../` traversal and symlink escape. `FileResponse` gives Range support so videos scrub in a `<video>` tag |
| `GET /api/services` | health gather |
| `GET /api/logs/{name}?lines=` | allowlist registry only |
| `GET /api/wavespeed/balance` | `lib/wavespeed_balance.get_balance(cfg)`, 60s in-memory cache |
| `GET /api/tailscale` | runtime-only hostname/serve/funnel status |
| `GET /api/runpod/pods` | 60s cache, graceful degrade when key unset |
| `GET/PUT/DELETE /api/config/providers` | effective values + per-key "overridden" badges; PUT validates via a `load_config` re-parse (rollback the overlay on `ConfigError`); DELETE reverts to plain `config.yaml` |

Long-running phases (animate polls WaveSpeed up to 1800s) execute inside the
single job thread; request handlers never block. Known accepted limitation
(identical to the existing n8n path): if the dashboard process dies mid-job,
the in-memory job record is lost, but `work/<id>/run.log` and the file-based
pipeline state survive.

## How n8n and ComfyUI fit in

**n8n — peer orchestrator, not replaced, zero workflow changes.** The
dashboard never calls n8n; both are front-ends over the same core. n8n keeps
its two jobs exactly as today: the noon cron (`pick_next.py` → worker
`--phase prepare`) and the Telegram webhook (`handle_telegram_reply.py`).
The dashboard calls the same worker functions and shares the same file-based
state (`pending_approval.json`, `processed.json`, `processing_lock`), so a
dashboard action and a Telegram action are indistinguishable to the
pipeline. Consequences:

- The only n8n-adjacent code change is the ~10-line `approval_lock` patch to
  `handle_telegram_reply.py` — needed **because** two surfaces now share one
  state machine (dashboard "yes" vs Telegram "yes" race).
- `POST /api/queue/run-next` duplicates the cron's pick-next logic on
  demand, through the same locks — a simultaneous scheduled run can't
  double-process.
- The dashboard's relationship *to* n8n is observability only: health dot
  (:5678), log tail, and the Tailscale panel confirming the Funnel feeding
  n8n's Telegram webhook is still alive.
- If n8n is down, the dashboard still works fully (it never depends on n8n);
  only the *scheduled* intake and *Telegram* replies stop.

**ComfyUI — the "local" half of the provider toggle.** The live config
currently runs WaveSpeed for both stages (Seedream stills, Kling animation);
ComfyUI (:8188) is fallback-only. The dashboard toggle is what makes
switching to full local generation practical:

- `avatar_frame.provider: local_comfyui` → FR-4 stills render through the
  RealVisXL + LoRA workflow (`comfyui/avatar_into_frame.api.json`), zero API
  cost.
- `animation.provider: local_comfyui` → FR-5 animates through Wan 2.2
  (`comfyui/wan_animate.api.json`) — known-slow on M1 Max (~2.5h+ per clip,
  measured), which is why the live config pivoted to Kling. The toggle makes
  the trade-off (free-but-slow vs paid-but-minutes) a per-run choice instead
  of a config-editing session.
- Guardrails when toggling to local: the PUT's `load_config` re-parse fails
  if `paths.lora_path` is missing, and the services panel shows whether
  ComfyUI is even up — check it before approving a gate that will render
  locally. Worth surfacing a small inline warning on the providers panel
  when a local provider is selected while ComfyUI's health check is red.
- A local Wan animate occupies the single job thread for hours. That is
  accepted: one-thing-in-flight is the pipeline's own invariant, and the
  `run:<id>` log tail gives live progress for the duration.

## Provider toggle

**Overlay file, not `config.yaml` rewrites.** A gitignored
`config.overrides.yaml` next to the config holds ONLY three keys —
`avatar_frame.provider`, `animation.provider`, `wavespeed.enabled` — merged
inside `load_config` before validation:

- Never rewrites the hand-commented `config.yaml` (a PyYAML round-trip would
  destroy its comments).
- The whitelist is enforced structurally on both write AND merge — a bug
  can't smuggle in `paths.out_dir` or `telegram.enabled`.
- Atomic temp + `os.replace` (same pattern as `lib/state.py`), so a
  concurrent n8n cron process calling `load_config` mid-toggle sees either
  old or new values, never a torn file.
- Every consumer is a short-lived process that calls `load_config` fresh, so
  toggles take effect on the next phase run with **zero changes to n8n**.
- UI: confirm-guarded control with an explicit "subsequent approvals will
  spend real money" warning when switching to WaveSpeed, per-key
  "overridden" badges, and a "revert to config.yaml" button.

## UI layout (single page)

```
TOPBAR: service dots (n8n/ComfyUI/Gate/Dashboard/Tailscale) · WaveSpeed $balance · RunPod pods/cost
┌──────────────────────────────────────────────┬─────────────────────┐
│ PENDING APPROVAL (hero card)                 │ PROVIDERS (toggles  │
│  stage frame: single image, yes/no           │  + override badges) │
│  stage avatar: side-by-side compare, yes/no  ├─────────────────────┤
│  buttons state the cost consequence, confirm │ SERVICES (health)   │
│  dialogs, spinner while a job runs           ├─────────────────────┤
├──────────────────────────────────────────────┤ TAILSCALE (runtime  │
│ SUBMIT LINK [input] [send] + job progress    │  hostname, serve ✓, │
├──────────────────────────────────────────────┤  funnel ⚠ warning)  │
│ QUEUE/STATUS table (status chips, cosine,    ├─────────────────────┤
│  output links → /api/media/out/...)          │ RUNPOD pods         │
├──────────────────────────────────────────────┼─────────────────────┤
│ RECENT JOBS                                  │ LOGS (tabbed tails) │
└──────────────────────────────────────────────┴─────────────────────┘
```

The pending card mirrors the state machine: stage `frame` shows one image
with "yes = generate avatar still (Seedream, paid)" / "no = flag & skip";
stage `avatar` shows the frame-vs-still comparison with "yes = animate
(Kling, paid)" / "no = regenerate (paid, attempt n/max)". Button labels state
the cost consequence explicitly, mirroring the Telegram captions.

Additional UI behaviors:

- **Mobile-responsive** — phone access over the tailnet is a primary use
  case (it's the whole point of `tailscale serve`), so the two-column grid
  collapses to a single column below ~700px with the pending-approval card
  first. Test at iPhone width, not just desktop.
- **Live job progress** — `run_animate` can poll WaveSpeed for up to 30
  minutes; a bare spinner is not enough. While a job is running, the LOGS
  panel auto-selects the `run:<id>` tail so the operator watches real
  progress (download %, WaveSpeed poll status, gate scores) without doing
  anything.
- **New-approval attention cue** — the 5s `/api/pending` poll drives a
  browser-tab title change (e.g. `(1) avatar-pipeline`) + favicon badge when
  a pending approval appears while the tab is open (the noon cron can create
  one unprompted). No Web Notifications API needed in v1.
- **Flagged rows are actionable** — status-table rows with `flagged:*`
  status show the un-flag button (see `POST /api/flagged/{id}/unflag`) and
  the identity cosine that caused the flag, so "review whether the gate was
  too strict → retry" is one click, not a JSON edit.
- **Cosine history mini-chart** — small sparkline over `done.csv`'s
  `identity_cosine` column with the `identity.cosine_min` threshold line
  drawn in. There's a standing open question about whether the 0.88 DINOv2
  threshold is scene-dominated/too strict (see HANDOFF); this makes the
  evidence visible at a glance instead of requiring CSV spelunking.

Telegram-off operation: the dashboard must work as the **sole** approval
surface when `telegram.enabled: false` — the gates live in the pending-file
mechanism, not in Telegram, and all Telegram sends in the worker are already
best-effort. Verify at implementation that no phase hard-fails when Telegram
is disabled, and add a test for it.

## Security

- Bind 127.0.0.1 only; reachability is tailnet-scoped by `tailscale serve`.
  The Tailscale panel actively checks `funnel status` and warns red if 8190
  ever appears there.
- Sensitive values are fetched at request time, cached in memory only, never
  written to disk; API keys are never echoed by any endpoint.
- Subprocess calls use list-args and read-only verbs only (`launchctl list`,
  `tailscale status`).
- Media routes: resolve-then-containment + extension allowlist. Log routes:
  fixed name registry, no paths accepted.
- No endpoint mutates `processed.json`/`done.csv` directly — only via the
  existing worker functions. Single deliberate exception: the un-flag
  endpoint, which goes through a new whitelisted `lib/state.py` helper
  (atomic write, id-existence validated) rather than raw JSON editing.
- Phase 5 optional hardening: middleware rejecting mutating methods when the
  `Host` header is neither localhost:8190 nor the runtime-discovered ts.net
  name (cheap CSRF guard). Also optional: `tailscale serve` injects
  `Tailscale-User-Login` identity headers on proxied requests — logging (or
  checking) that header is a nearly-free second factor if the tailnet ever
  gains more users/devices than just the operator's.

## Deliberately out of scope (decided, don't re-add casually)

- **Service restart buttons** — health panel is read-only by design;
  `launchctl kickstart` from a web UI is a footgun (and env-var changes need
  a full bootout/bootstrap cycle anyway, per HANDOFF). Restart from a
  terminal.
- **Editing `identity.cosine_min` / other config from the UI** — the config
  overlay whitelist stays at exactly 3 provider keys. Threshold tuning is a
  deliberate hand-edit of `config.yaml` after looking at the cosine chart.
- **Writing to `links.numbers`** — hard requirement (PRODUCT-SPEC §5 #6):
  the Numbers sheet is read-only input. Queue additions happen via link
  submission (immediate) or editing the sheet by hand (scheduled).
- **Job cancellation** — killing a mid-flight WaveSpeed poll wastes the
  spend that's already committed (same reasoning as the ZP8smK3Uj live-run
  precedent). If a job truly wedges, the stale-steal timeouts on both locks
  recover the system without UI involvement. Revisit only if it bites.
- **Multi-user/auth beyond the tailnet** — tailnet membership IS the auth
  model; the dashboard never becomes Funnel-public.

## Testing

Repo conventions: pure functions + `fastapi.testclient.TestClient` + mocks.
Reuse `tests/conftest.py:make_config` extended with a `dashboard` section;
app under test via `create_app(config_path)`; worker functions monkeypatched
with fakes returning `(dict, code)` — no network, no real providers.

Key cases:
- decision → worker-fn mapping (all four transitions)
- `409`s: no pending, id mismatch, stage mismatch, claim held, processing
  lock held
- media: happy paths under both roots; `../` traversal, symlink escape, bad
  extension, unknown id → 404
- overrides: whitelist enforcement, invalid provider → 422, atomic write,
  merge precedence, DELETE revert, regression (configs without overlay
  unchanged)
- approval_lock: exclusivity, stale-steal, release; updated
  `test_handle_telegram_reply.py` cases for claim-held replies
- unflag: id removed from `flagged` only, unknown id → 404, `processed` list
  untouched, atomic write; run-next: queue-empty → 204, same 409 guards as
  link submit; link submit appends to the archive (parity with Telegram)
- telegram-disabled: all four phase dispatches succeed with
  `telegram.enabled: false` (dashboard as sole approval surface)
- degraded modes: no RunPod key, tailscale CLI missing, service down
- log tail correctness on multi-block files

## Milestones

Each phase is independently useful:

- **Phase 0** — this document. ✅
- **Phase 1 — Read-only monitor** ✅: `dashboard.py` skeleton + static shell,
  `/api/status`, `/api/pending` (card, no buttons), `/api/services`,
  `/api/logs`, `/api/media`, launchd plist + `tailscale serve`.
- **Phase 2 — Control (Telegram parity)** ✅: `dashboard_jobs.py`,
  `approval_lock.py` + telegram-reply patch, `POST /api/links` (with archive
  parity), decision endpoint, `POST /api/queue/run-next`,
  `POST /api/flagged/{id}/unflag`, live buttons/spinners, auto-selected
  `run:<id>` log tail for the active job.
- **Phase 3 — Provider toggle** ✅: `config_overrides.py` + `load_config`
  merge + providers panel.
- **Phase 4 — Integrations**: WaveSpeed balance, Tailscale panel, RunPod
  pods, cosine history mini-chart.
- **Phase 5 — Polish/hardening**: Host guard, theme refinement, mobile
  layout pass, title-badge attention cue, telegram-disabled verification +
  test, docs, funnel-exposure warning banner.

## Verification (per phase)

- `./venv/bin/python -m pytest tests/ -q` — all existing tests (183 at plan
  time) + new ones pass.
- `./venv/bin/python scripts/dashboard.py --config config.yaml` → open
  http://localhost:8190, confirm panels render with real state.
- Mock providers (`avatar_frame.provider: mock`) allow the full
  approve/reject flow end-to-end with zero API cost.
- `curl` the media endpoint with `../` traversal attempts → 404.
- After plist install: `launchctl list | grep avatar.dashboard`,
  `tailscale serve status` shows 8190, phone on the tailnet loads the page;
  `tailscale funnel status` does **NOT** show 8190.

## Critical reference files

- `scripts/worker.py` — the five `run_*` phase functions the dashboard calls
- `scripts/handle_telegram_reply.py` — the dispatch/lock pattern to mirror;
  gets the approval-claim patch
- `scripts/lib/config.py` — overlay merge point, provider whitelists, new
  `Dashboard` section
- `scripts/lib/pending.py` — the pending-approval contract both surfaces share
- `scripts/face_gate.py` — the FastAPI service + launchd pattern being cloned
