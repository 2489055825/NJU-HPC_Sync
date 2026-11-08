# 小幅加固与回归测试 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make local write failures in credential management and log export recoverable and visible, while adding regression coverage for the nearby untested error boundaries.

**Architecture:** Keep the existing Qt dialogs, `CredentialStore`, `Database`, and rsync runner interfaces. Credential mutations will be staged in a candidate mapping and committed to in-memory state only after the store write succeeds; log export will report `OSError` through the existing warning-dialog pattern. The remaining work adds isolated tests around current public behavior without changing the sync protocol.

**Tech Stack:** Python 3.11, PySide6, pytest, existing `CredentialStore`, SQLite database, and rsync/SSH preflight helpers.

**Spec:** `docs/superpowers/specs/2026-11-08-small-hardening-design.md`

## Global Constraints

- Do not change rsync argv, path normalization, TOTP generation, host-key confirmation, mirror-preview binding, or task cancellation behavior.
- Do not broaden SSH prompt matching without a real HPC prompt sample.
- Tests use temporary directories, isolated HOME, monkeypatches, and synthetic subprocesses only.
- Do not add runtime dependencies.
- Preserve credentials and the current editor state when a write fails.

## Review Focus

- Credential save failure must not leave a renamed or edited in-memory credential that was never persisted.
- Credential delete failure must not remove the item from the dialog's in-memory state.
- Log export failure must produce a warning and leave the main window usable.
- Missing rsync/SSH and non-zero rsync exit-code messages must remain deterministic.
- Manual authentication cancellation and empty-password behavior must remain cancellation, not a subprocess response.

### Task 1: Stage credential mutations before saving

**Files:**
- Modify: `app/dialogs.py:432-457`
- Modify after review: `app/credential_store.py:65-66`
- Test: `tests/test_recovery.py`

**Interfaces:**
- Consumes: `CredentialStore.save(credentials)` and the existing `CredentialsDialog` fields.
- Produces: `CredentialsDialog._save()` and `_delete()` update `self.credentials`, selection state, and `credentials_changed` only after a successful store save; caught write failures use the existing `QMessageBox.warning` path.

- [x] **Step 1: Write failing tests**

  Add tests that load a valid credential, monkeypatch `store.save` to raise `OSError`, invoke `_save()` or `_delete()`, and assert that the warning is shown, the original JSON remains unchanged, the dialog mapping remains unchanged, and no change signal is emitted.

- [x] **Step 2: Run the focused tests to verify they fail**

  Run: `conda run -n hpc-sync pytest -q tests/test_recovery.py -k 'credential_save_failure or credential_delete_failure'`

  Expected: the tests fail because the current dialog mutates its mapping before `store.save()` and does not catch `OSError`.

- [x] **Step 3: Implement staged save/delete in `app/dialogs.py`**

  Build a copied candidate mapping, apply the edit or deletion to that copy, call `store.save(candidate)`, then assign the candidate and refresh/emit only after success. Catch `OSError` together with the existing validation/runtime errors and preserve the current fields on failure.

- [x] **Step 4: Run the focused tests to verify they pass**

  Run: `conda run -n hpc-sync pytest -q tests/test_recovery.py -k 'credential_save_failure or credential_delete_failure'`

  Expected: all new tests pass.

### Task 2: Report log-export write failures

**Files:**
- Modify: `app/main_window.py:996-999`
- Test: `tests/test_main_window.py`

**Interfaces:**
- Consumes: `QFileDialog.getSaveFileName`, `Path.write_text`, and the main window warning-dialog convention.
- Produces: `_save_log()` catches `OSError`, calls `QMessageBox.warning`, and leaves the log and window available for another attempt.

- [x] **Step 1: Write the failing test**

  Stub the save-file dialog to return a path, make `Path.write_text` raise `OSError`, call `_save_log()`, and assert that the warning contains the write error and the log contents remain available.

- [x] **Step 2: Run the focused test to verify it fails**

  Run: `conda run -n hpc-sync pytest -q tests/test_main_window.py -k 'save_log_failure'`

  Expected: the current implementation raises `OSError` instead of showing a warning.

- [x] **Step 3: Implement the minimal `OSError` handler**

  Wrap only `Path(path).write_text(...)` in `try/except OSError`; keep the existing success behavior and use the existing window warning style.

- [x] **Step 4: Run the focused test to verify it passes**

  Run: `conda run -n hpc-sync pytest -q tests/test_main_window.py -k 'save_log_failure'`

  Expected: the new test passes.

### Task 3: Add boundary regression coverage without behavior changes

**Files:**
- Test: `tests/test_auth.py`
- Test: `tests/test_commands.py`
- Test: `tests/test_database.py`
- Test: `tests/test_runner.py`
- Test: `tests/test_host_selector.py` or `tests/test_main_window.py` where the existing fixtures already cover the relevant Qt object

**Interfaces:**
- Consumes: existing `ManualAuthProvider`, `make_provider`, `preflight`, `Database`, `friendly_exit_message`, and Profile UI behavior.
- Produces: regression tests documenting current error messages and state transitions; no production interface changes.

- [x] **Step 1: Add focused tests**

  Cover manual auth cancellation/empty input, missing rsync/SSH preflight, invalid retention and profile update/delete/missing lookups, exit codes 0/23/24/255/default, and Profile deletion/duplicate-name handling using isolated fixtures.

- [x] **Step 2: Run the focused boundary tests**

  Run: `conda run -n hpc-sync pytest -q tests/test_auth.py tests/test_commands.py tests/test_database.py tests/test_runner.py tests/test_host_selector.py tests/test_main_window.py`

  Expected: all boundary tests pass without production changes.

### Task 4: Full verification

**Files:**
- Verify: all modified source and test files

- [x] **Step 1: Run the complete test suite**

  Run: `conda run -n hpc-sync pytest -q`

  Expected: original 121 tests plus the new tests pass.

- [x] **Step 2: Run strict warnings and compile checks**

  Run: `conda run -n hpc-sync pytest -q -W error` and `conda run -n hpc-sync python -m compileall -q app main.py`

  Expected: both exit with status 0.

- [x] **Step 3: Check the diff and isolated startup**

  Run: `git diff --check` and an offscreen startup/close smoke test with a temporary HOME and database.

  Expected: no whitespace errors, no credentials outside temporary paths, and the main window shows and closes cleanly.

## Verification record

The initial implementation added 17 cases to the original 121: the credential save/delete tests and log-export test first failed on unhandled `OSError`, then passed after the UI changes. Full suite and strict-warning runs both passed with 138 cases.

An independent read-only review found two important gaps. First, `CredentialStore.save()` could raise from a redundant destination `chmod` after `os.replace()` had already committed. Two regression cases reproduced the disk/dialog mismatch for save and delete. The destination inherits mode `600` from the temporary file, so removing the late `chmod` makes replacement the commit point without weakening permissions. Both regression cases then passed. Save/delete failure tests now inject failures into the real store's `fsync` and `replace` operations, verify original bytes and temporary-file cleanup, and retry successfully. Log-export coverage also verifies a successful retry.

Second, Profile creation/editing and duplicate-name handling lacked main-window coverage. Four cases now exercise the real dialog validation, database persistence, selection refresh, and duplicate-name warning paths. They characterize existing behavior; no Profile implementation change was needed.

Final validation: `conda run -n hpc-sync pytest -q -W error` reported **146 passed in 9.32s**. The focused credential/Profile/window tests reported **52 passed**. `compileall` and `git diff --check` passed. An offscreen QApplication using a temporary home/database showed the main window, closed it through the event loop, and exited with status 0. No real HPC connection or user credential was used.

Execution adjustments: missing task bookkeeping scripts were replaced with the available task-brief script and ledger; the invalid regex-style pytest `-k` expression was corrected above. Work stays in the confirmed local workspace, with local commits only. The strongest-model reviewer hit a usage limit; the completed independent review used the available GPT-6.1 Sol reviewer. The review's two important findings were resolved in one fix pass; there were no deferred minor findings.

Review boundaries: SSH/HPC compatibility remains outside this change. Export failures preserve the displayed log for retry; atomic replacement of an existing exported log was not part of the approved design. An unchanged deferred-scroll callback observed only under synthetic back-to-back operations without Qt event processing was not changed; retry tests process events between user actions. Native desktop event ordering and real HPC transfers remain unverified.

