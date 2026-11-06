# Sync Safety Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use executing-plans and test-driven-development. Implement inline, then request one independent review.

**Goal:** Fix all seven reproduced issues from the 1.0.4 audit and deliver 1.0.5 locally and on GitHub.

**Architecture:** Bind reusable previews to a complete normalized SyncRequest and disable task/config editing during a worker run. Confirm every actual mirror run immediately before execution. Buffer output across chunks before redaction, provide an independent host-key confirmation callback, and recover from malformed credentials without overwriting them. Protect Host editor drafts on every close path.

**Tech Stack:** Python 3.11, PySide6, pexpect, pytest, existing Debian packaging.

**Spec:** The user's approved request to fix all seven findings in this conversation: mirror refusal bypass, stale preview targets, grouped-number parsing, fragmented secret redaction, first-connection host-key prompts, malformed credential startup, and unsaved Host edits.

## Global Constraints

- Preserve shell-free argv, SSH configuration, real credentials and history. Tests use isolated HOME and synthetic data only.
- Do not auto-accept host keys or send authentication material to host-key prompts.
- Keep the simplified README, updating only affected behavior and the package version.
- User authorizes local installation, direct push to main, and a new Release with deb/checksum assets.

## Review Focus

- Rejecting confirmation, explicit Preview, auto-preview, target/direction changes and modal-time changes must never bypass a fresh mirror confirmation.
- Successful stale, cancelled and failed runs must not authorize another target; normalize paths before comparing requests.
- Redaction must protect both live callbacks and persisted output across arbitrary chunk boundaries and EOF.
- Host-key accept/reject is separate from manual/TOTP authentication and includes the server fingerprint context.
- Invalid credential files remain untouched and allow manual operation; cancelling editor close/save must preserve the draft.

### Task 1: Mirror state and counts

- [x] Add failing regression tests for cancelled confirmations, changed preview inputs, manual preview, subsequent runs and comma-separated stats.
- [x] Bind preview snapshots to requests, lock task editing during execution, require confirmation before each mirror run, and parse grouped counts.
- [x] Run focused tests and full suite.

### Task 2: Output and host-key handling

- [x] Add failing fragmented-output, EOF, host-key accept/reject and worker callback tests with real synthetic subprocesses.
- [x] Implement streaming redaction and an independent host-key UI/broker callback; preserve cancellation and PTY progress.
- [x] Run focused tests and full suite.

### Task 3: Startup and editor recovery

- [x] Add failing tests for malformed JSON/encoding, manual fallback, and Save/Discard/Cancel on close/Escape/window close.
- [x] Implement error recovery without rewriting credentials and close confirmation that retains failed saves.
- [x] Run full suite, compileall and UI checks; obtain independent review and resolve concrete findings with regression tests.

### Task 4: Deliver 1.0.5

- [x] Update version and README, build deb/checksum, verify embedded modules and packaged startup.
- [x] Install locally and verify dpkg/integrity/startup.
- [ ] Commit and push main, create 1.0.5 tag/Release, verify remote commit and asset checksums.

## Verification record

All seven reproduced 1.0.4 defects have regression coverage. Full suite after fixes: **105 passed**; compileall and diff whitespace checks passed. Independent review found two further cases within the fixes: overlapping static/full credential prefixes could reveal a verification suffix, and overflowing JSON numbers bypassed credential recovery. Both were reproduced in failing tests and fixed; an invalid stored TOTP period is now rejected as well. No review findings were deferred. Real synthetic subprocesses exercise fragmented echoes, host-key accept/reject and task-control locking. Offscreen UI checks captured host-key confirmation (default No, fingerprint visible) and editor close choices using temporary HOME and example hostnames only. Release/install verification and publication follow this committed implementation record.

The final package checksum, version/architecture and embedded safety code were verified. The extracted GUI passed 5-second startup checks with both normal and malformed credentials. Local installation reports `nju-hpc-sync 1.0.5 install ok installed`; `dpkg --verify` is clean. The installed launcher passed the same startup check with malformed temporary credentials, whose original bytes remained unchanged. Publication is the final external action; the final response records the verified remote commit/tag and Release assets.
