# UI Lifecycle Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use executing-plans and test-driven-development. Implement inline and request one independent review before delivery.

**Goal:** Fix the four verified 1.0.5 UI lifecycle issues and deliver version 1.0.6 locally and on GitHub.

**Architecture:** Share a per-worker cancellation event across TOTP waits, prompt brokers and the PTY runner; cancellation remains set before startup and before sending responses. Close the main window asynchronously only after thread cleanup, keeping history open until final callbacks finish. Stop/delete closed credential dialogs and reset the task display when selecting another Profile.

**Tech Stack:** Python 3.11, PySide6, threading.Event, pexpect, pytest, existing Debian build.

**Spec:** User approved fixes for TOTP stop/close failure, lost early cancellation, retained credential dialogs/timers and stale Profile status; also authorized local installation, direct main push and Release publication.

## Constraints and review focus

- Keep the seven prior safety fixes, redaction, preview binding and all existing tests working.
- No real HPC connections or credential/config modifications during verification; use temporary HOME and generated data.
- Cancellation before spawn, during TOTP waiting and while a prompt is queued must not restart, send credentials, or revive old UI states.
- Closing cannot destroy a running QThread or close the database before final history callbacks. The UI must keep processing events while shutting down.
- Credential modal closure must stop its timer and release its QObject tree; cancellation and Profile changes must leave consistent controls/status.

### Task 1: Cooperative cancellation

- [x] Add failing tests for cancelled TOTP waiting, early runner cancellation, stopped prompt requests and delayed authentication responses.
- [x] Add a shared cancellation event and cancellation exceptions; remove runner cancellation reset and recheck cancellation before launch/responses.
- [x] Run focused tests and full suite.

### Task 2: UI lifecycle and cleanup

- [x] Add failing Qt tests for TOTP stop, asynchronous close, queued status/progress, credential dialog disposal and Profile status reset.
- [x] Make close wait asynchronously for cleanup; guard stale UI events, stop timers/delete dialogs and reset idle Profile display.
- [x] Run full suite, compileall and isolated repeated-task/shutdown checks; obtain independent review and fix concrete findings with regression tests.

Validation: 121 pytest cases passed, compileall and diff whitespace checks passed. Includes eight consecutive Profile/direction/mode cycles, password/host-key modal close and startup credential-error visibility. A separate QApplication process stopped during TOTP waiting and exited normally after saving Cancelled history. Independent read-only review found no actionable findings and additionally exercised 20 local modal stop/close cycles. All tasks used synthetic local processes and temporary data; native window-manager behavior and real HPC authentication were not exercised.

### Task 3: Deliver 1.0.6

- [x] Update VERSION/README, build/checksum package and verify embedded code and packaged startup.
- [x] Install and verify dpkg/integrity/installed startup.
- Publication follows this committed snapshot: push main and 1.0.6 tag, publish deb/checksum Release, and verify remote refs/digests. Live publication evidence is reported in the delivery conversation.

Local delivery validation: deb SHA-256 passed; embedded cancellation/UI code inspected; clean and malformed-credential packaged startup passed without rewriting the malformed file. dpkg reports 1.0.6, all 356 installed package file hashes match the deb, and the installed launcher starts with temporary configuration. Stop-state screenshot checked using synthetic sample data.
