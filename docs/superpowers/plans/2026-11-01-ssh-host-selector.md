# SSH Host Selector Implementation Plan

> **For agentic workers:** Use executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Replace manual remote Host input with a selector populated from user SSH configuration.

**Architecture:** A standard-library reader enumerates explicit Host aliases and Include files. A reusable Qt widget owns the dropdown, refresh action and inline feedback; both application forms use it and validate the selection before use.

**Tech Stack:** Python 3.11, PySide6, pytest.

**Spec:** `docs/superpowers/specs/2026-11-01-ssh-host-selector-design.md`

## Global Constraints

- No additional dependencies, credential access, SSH configuration writes or real remote connections.
- Preserve the pre-existing transfer-progress edits in `app/main_window.py` and `tests/test_main_window.py`.
- Deliver reviewable workspace changes without committing unrelated user edits.

## Review Focus

- Relative nested Include paths resolve against `~/.ssh`, not the including file's directory.
- Cyclic Include and malformed configuration do not crash startup or expose configuration lines.
- Refresh cannot silently replace a removed selected host.
- Profile saving and sync requests reject an unavailable alias.
- Changing the host invalidates a completed mirror preview.

### Task 1: Enumerate explicit SSH aliases

**Files:** Create `app/ssh_config.py`, `tests/test_ssh_config.py`.

**Interfaces:** Produce `read_ssh_hosts(config_path: Path | None = None) -> tuple[list[str], list[str]]`, returning aliases and human-readable warnings. Default source is `Path.home() / '.ssh/config'`.

- [x] Write tests for aliases, comments, equals, wildcards/negations, nested/glob/quoted Include, cycles, missing/invalid files and no configuration writes.
- [x] Run `conda run -n hpc-sync pytest -q tests/test_ssh_config.py`; confirm failure because the reader is absent.
- [x] Implement read-only alias enumeration, stable deduplication and bounded recursive traversal.
- [x] Run the reader tests; expect all passing.

### Task 2: Integrate the shared selector

**Files:** Create `app/host_selector.py`, `tests/test_host_selector.py`; modify `app/main_window.py`, `app/dialogs.py`, `README.md`.

**Interfaces:** Consume Task 1's reader. Produce `SshHostSelector(QWidget)` with `combo`, `refresh_button`, `message`, `set_host(host: str) -> None` and `selected_host() -> str` (ValueError for missing selections).

- [x] Write Qt tests with temporary HOME for both forms, requested rsync alias, refresh preservation/removal, missing Profile rejection, empty configuration feedback, and preview invalidation.
- [x] Run `conda run -n hpc-sync pytest -q tests/test_host_selector.py`; confirm missing dropdown behavior.
- [x] Implement the widget and connect both forms; adapt Profile restoration, request construction and help text.
- [x] Run all tests with `conda run -n hpc-sync pytest -q`; expect zero failures.
- [x] Run `conda run -n hpc-sync python -m compileall -q app main.py`; expect exit 0.
- [x] Render both forms using example aliases and inspect the screenshots; review the diff for unintended edits and sensitive data.

## Execution notes

Baseline: 29 tests passed. Work continues in the current checkout on `codex/ssh-host-selector`, preserving existing uncommitted edits. The user's source confirmation settles the prior concrete proposal; execution continues without another approval round.

Final review identified and reproduced a P2 issue where shell tokenization truncated embedded `#` and removed backslashes. Regression tests failed first; a tokenizer following OpenSSH's comment, quote and escape rules fixed them. Whitespace-padded and unsupported backslash aliases are rejected instead of rewritten. Reader tests: 13 passed; Qt selector tests: 9 passed. Final complete suite: 51 passed. Compileall and diff whitespace checks passed. Example-only screenshots of the main form, Profile form and removed-host feedback were visually inspected. The pre-existing active-preview editing behavior remains outside this change; selection changes continue to invalidate completed previews.
