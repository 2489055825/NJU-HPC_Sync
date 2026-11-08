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
- Test: `tests/test_recovery.py`

**Interfaces:**
- Consumes: `CredentialStore.save(credentials)` and the existing `CredentialsDialog` fields.
- Produces: `CredentialsDialog._save()` and `_delete()` update `self.credentials`, selection state, and `credentials_changed` only after a successful store save; caught write failures use the existing `QMessageBox.warning` path.

- [ ] **Step 1: Write failing tests**

  Add tests that load a valid credential, monkeypatch `store.save` to raise `OSError`, invoke `_save()` or `_delete()`, and assert that the warning is shown, the original JSON remains unchanged, the dialog mapping remains unchanged, and no change signal is emitted.

- [ ] **Step 2: Run the focused tests to verify they fail**

  Run: `conda run -n hpc-sync pytest -q tests/test_recovery.py -k 'credential.*failure or credential.*delete'`

  Expected: the tests fail because the current dialog mutates its mapping before `store.save()` and does not catch `OSError`.

- [ ] **Step 3: Implement staged save/delete in `app/dialogs.py`**

  Build a copied candidate mapping, apply the edit or deletion to that copy, call `store.save(candidate)`, then assign the candidate and refresh/emit only after success. Catch `OSError` together with the existing validation/runtime errors and preserve the current fields on failure.

- [ ] **Step 4: Run the focused tests to verify they pass**

  Run: `conda run -n hpc-sync pytest -q tests/test_recovery.py -k 'credential.*failure or credential.*delete'`

  Expected: all new tests pass.

### Task 2: Report log-export write failures

**Files:**
- Modify: `app/main_window.py:996-999`
- Test: `tests/test_main_window.py`

**Interfaces:**
- Consumes: `QFileDialog.getSaveFileName`, `Path.write_text`, and the main window warning-dialog convention.
- Produces: `_save_log()` catches `OSError`, calls `QMessageBox.warning`, and leaves the log and window available for another attempt.

- [ ] **Step 1: Write the failing test**

  Stub the save-file dialog to return a path, make `Path.write_text` raise `OSError`, call `_save_log()`, and assert that the warning contains the write error and the log contents remain available.

- [ ] **Step 2: Run the focused test to verify it fails**

  Run: `conda run -n hpc-sync pytest -q tests/test_main_window.py -k 'save_log_failure'`

  Expected: the current implementation raises `OSError` instead of showing a warning.

- [ ] **Step 3: Implement the minimal `OSError` handler**

  Wrap only `Path(path).write_text(...)` in `try/except OSError`; keep the existing success behavior and use the existing window warning style.

- [ ] **Step 4: Run the focused test to verify it passes**

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

- [ ] **Step 1: Add focused tests**

  Cover manual auth cancellation/empty input, missing rsync/SSH preflight, invalid retention and profile update/delete/missing lookups, exit codes 0/23/24/255/default, and Profile deletion/duplicate-name handling using isolated fixtures.

- [ ] **Step 2: Run the focused boundary tests**

  Run: `conda run -n hpc-sync pytest -q tests/test_auth.py tests/test_commands.py tests/test_database.py tests/test_runner.py tests/test_host_selector.py tests/test_main_window.py`

  Expected: all boundary tests pass without production changes.

### Task 4: Full verification

**Files:**
- Verify: all modified source and test files

- [ ] **Step 1: Run the complete test suite**

  Run: `conda run -n hpc-sync pytest -q`

  Expected: original 121 tests plus the new tests pass.

- [ ] **Step 2: Run strict warnings and compile checks**

  Run: `conda run -n hpc-sync pytest -q -W error` and `conda run -n hpc-sync python -m compileall -q app main.py`

  Expected: both exit with status 0.

- [ ] **Step 3: Check the diff and isolated startup**

  Run: `git diff --check` and an offscreen startup/close smoke test with a temporary HOME and database.

  Expected: no whitespace errors, no credentials outside temporary paths, and the main window shows and closes cleanly.

