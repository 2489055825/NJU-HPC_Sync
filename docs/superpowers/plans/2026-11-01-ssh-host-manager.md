# SSH Host Manager Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a safe raw-text editor for the user's main SSH config file and expose it under the Management menu.

**Architecture:** Keep filesystem operations in `app/ssh_config_editor.py` as pure, testable helpers for validation, snapshots, backups and atomic writes. Add a `HostConfigDialog` in `app/dialogs.py` for editing only `~/.ssh/config`; the main window opens it and refreshes the existing Host selector after a successful save.

**Tech Stack:** Python 3.11, PySide6, pytest, standard library filesystem APIs.

**Spec:** `docs/superpowers/specs/2026-11-01-ssh-host-manager-design.md`

## Global Constraints

- Only the main file `Path.home() / ".ssh" / "config"` is writable; Include files remain read-only.
- Save through a same-directory temporary file and `os.replace`, preserving an existing mode or using `0600` for a new file; create `~/.ssh` as `0700` when missing.
- Never log or persist SSH configuration contents in history, credentials, screenshots or release metadata.
- Existing Host selection, Include discovery, transfer progress and Profile behavior must remain intact.

## Review Focus

- Failed backup, temporary-file write or replacement leaves the original configuration intact.
- External modification between dialog load and save cannot be silently overwritten.
- Existing `0600`/non-default mode is preserved and newly created files are private.
- Unknown valid OpenSSH directives and Include lines remain editable while malformed directive/quote lines are rejected with line numbers.
- Save success refreshes the selector without silently changing a still-valid Profile Host.

### Task 1: Configuration file operations

**Files:** Create `app/ssh_config_editor.py`, `tests/test_ssh_config_editor.py`.

**Interfaces:** `ssh_config_path() -> Path`, `load_ssh_config(path: Path | None = None) -> str`, `load_ssh_config_state(path: Path | None = None) -> tuple[str, str | None]`, `validate_ssh_config(text: str) -> list[str]`, `config_snapshot(path: Path | None = None) -> str | None`, and `save_ssh_config(text, path, *, expected_snapshot=...) -> Path | None`. The dialog always supplies the expected snapshot; the writer checks it again immediately before replacement.

- [x] Write failing tests for missing files, syntax/quote validation, backup creation, atomic save, permissions, invalid Unicode, and failure preserving the original.
- [x] Run the focused tests and observe failure because the module is absent.
- [x] Implement the standard-library helpers with no subprocesses or configuration-content logging.
- [x] Run focused tests and confirm they pass.

### Task 2: Host configuration editor dialog

**Files:** Modify `app/dialogs.py`, create or extend `tests/test_host_manager.py`.

**Interfaces:** `HostConfigDialog(QDialog)` exposes `config_saved = Signal()` and a save/reload/close UI for the main config only.

- [x] Write failing Qt tests for loading the main path, save signal, validation rejection, cancel-without-save, and external-change protection.
- [x] Run focused Qt tests and observe failure because the dialog/menu does not exist.
- [x] Implement the dialog with path display, editor, reload/save/close actions, confirmation prompts, and user-facing errors.
- [x] Run focused Qt tests and confirm they pass.

### Task 3: Management menu integration and documentation

**Files:** Modify `app/main_window.py`, `README.md`, `tests/test_main_window.py`.

- [x] Write failing tests for the Management menu action and selector refresh after `config_saved`.
- [x] Add “Host 管理”, open the dialog, connect save to `host_selector.refresh`, and document the editable main-file boundary and backup behavior.
- [x] Run the focused and full test suites, compileall, and diff checks.

### Task 4: Release, installation and cloud publication

**Files:** Modify `VERSION`; build ignored release artifacts.

- [x] Bump the patch version from 1.0.3 to 1.0.4 and build the Ubuntu 22.04 amd64 `.deb`.
- [x] Verify package checksum, embedded modules, packaged startup and full tests.
- [x] Install the package locally, verify `dpkg` reports 1.0.4 and installed startup.
- [ ] Commit, push `main`, create tag `1.0.4`, and upload the `.deb` plus `.sha256` to a GitHub Release.

## Execution and review record

The user authorized implementation, local installation and direct publication to their repository. Work proceeds in the current checkout on `main`. Full suite after review fixes: 74 passed; compileall and whitespace checks passed. The independent reviewer identified backup symlink traversal, missing write-time snapshot checks, reload failure after external edits, missing save confirmation paths, temporary descriptor cleanup and contradictory documentation. Regression tests reproduced the applicable defects and now pass. Backups are replaced atomically; snapshots are checked inside the writer; reload/confirmation behavior and descriptor cleanup are fixed. A main config that is itself a symlink is deliberately preserved and rejected for writing, so its target cannot be modified through this main-file-only editor. The sample screenshot uses an isolated temporary HOME and example hostnames.

The final 1.0.4 package checksum and embedded editor modules were verified. Both the extracted package and installed `/usr/bin/nju-hpc-sync` stayed running for the 5-second offscreen startup check with isolated HOME/config/history. `dpkg-query` reports `1.0.4 install ok installed`, and `dpkg --verify` reported no mismatches. Publication is the final action after committing this record; its remote commit, tag, asset checksums and Release URL are verified and reported in the delivery message.
