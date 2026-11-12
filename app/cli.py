"""Noninteractive command interface; importing this module never imports Qt."""
from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import signal
import sqlite3
import sys
from collections import deque
from dataclasses import asdict
from pathlib import Path

from .credential_store import CredentialStore
from .auth import validate_automatic_credential
from .database import Database
from .models import Direction, SyncMode, SyncRequest
from .runtime import CliError, data_root
from .ssh_config import read_ssh_hosts
from .tasks import cancel_task, get_task, submit_task, wait_task


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise CliError("INVALID_ARGUMENTS", message)


class WaitInterrupted(Exception):
    def __init__(self, signum, task_id):
        self.signum = signum
        self.task_id = task_id


def _seconds(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise argparse.ArgumentTypeError("秒数必须是非负有限数值")
    return number


def _positive(value: str) -> int:
    number = int(value)
    if not 1 <= number <= 100000:
        raise argparse.ArgumentTypeError("数量必须在 1 到 100000 之间")
    return number


def _json_option(parser):
    parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                        help="向标准输出返回一个 JSON 结果；进度输出到标准错误")
    return parser


def parser() -> Parser:
    root = _json_option(Parser(prog="nju-hpc-sync", description="独立执行 rsync 同步并查询持久任务结果"))
    commands = root.add_subparsers(dest="command", required=True)
    for name in ("sync", "preview"):
        cmd = _json_option(commands.add_parser(name, help="同步目录" if name == "sync" else "预览文件变化"))
        cmd.add_argument("--profile", metavar="NAME", help="使用已有 Profile")
        cmd.add_argument("--local", help="本地目录")
        cmd.add_argument("--host", help="已有 SSH Host 别名")
        cmd.add_argument("--remote", help="远程目录")
        cmd.add_argument("--direction", choices=["upload", "download"])
        cmd.add_argument("--credential", metavar="NAME", help="已有 TOTP 凭据名称")
        cmd.add_argument("--mirror", action="store_true", help="明确允许删除目标端多余文件")
        wait = cmd.add_mutually_exclusive_group()
        wait.add_argument("--detach", action="store_true", help="后台接管后立即返回任务编号")
        wait.add_argument("--wait-timeout", type=_seconds, help="等待秒数；超时不会取消任务")
    for name in ("status", "wait", "cancel", "logs"):
        cmd = _json_option(commands.add_parser(name))
        cmd.add_argument("task_id", nargs="?", help="省略时选择当前任务；查询可回退到最近任务")
        if name == "wait":
            cmd.add_argument("--timeout", type=_seconds)
        elif name == "logs":
            cmd.add_argument("--tail", type=_positive, default=200)
    for name in ("profiles", "hosts", "credentials", "history"):
        group = _json_option(commands.add_parser(name))
        actions = group.add_subparsers(dest="action", required=True)
        listing = _json_option(actions.add_parser("list"))
        if name == "history":
            listing.add_argument("--limit", type=_positive, default=200)
            showing = _json_option(actions.add_parser("show"))
            showing.add_argument("id", type=int)
    _json_option(commands.add_parser("doctor", help="只检查本地运行条件，不连接远程或修改配置"))
    return root


def _database() -> Database | None:
    path = data_root() / "nju-hpc-sync.sqlite3"
    return Database(path, readonly=True) if path.exists() else None


def _profiles() -> list:
    db = _database()
    if db is None:
        return []
    with db:
        return db.list_profiles()


def _request(args) -> tuple[SyncRequest, str, str]:
    profile = None
    if args.profile is not None:
        if any(value is not None for value in (args.local, args.host, args.remote)):
            raise CliError("INVALID_ARGUMENTS", "--profile 不能与 --local/--host/--remote 混用。")
        profile = next((value for value in _profiles() if value.name == args.profile), None)
        if profile is None:
            raise CliError("PROFILE_NOT_FOUND", "找不到指定的 Profile。")
    elif not all((args.local, args.host, args.remote, args.direction)):
        raise CliError("INVALID_ARGUMENTS", "临时任务必须提供 --local、--host、--remote 和 --direction。")
    host = profile.remote_host if profile else args.host
    hosts, _warnings = read_ssh_hosts()
    if host not in hosts:
        raise CliError("HOST_NOT_FOUND", "远程 Host 不在已有 SSH 配置中。")
    credential_name = args.credential if args.credential is not None else (profile.credential_name if profile else "")
    if not credential_name:
        raise CliError("CREDENTIAL_REQUIRED", "需要指定已有 TOTP 凭据；可使用 --credential NAME。")
    request = SyncRequest(profile.local_path if profile else args.local, host,
                          profile.remote_path if profile else args.remote,
                          Direction(args.direction or profile.default_direction),
                          SyncMode.MIRROR if args.mirror else SyncMode.NORMAL,
                          args.command == "preview")
    return request, credential_name, profile.name if profile else ""


def _wait(task_id: str | None, timeout: float | None) -> dict:
    selected = get_task(task_id)
    task_id = selected["task_id"]
    handlers = {}

    def interrupt(signum, _frame):
        raise WaitInterrupted(signum, task_id)

    last = None

    def progress(record):
        nonlocal last
        value = (record.get("phase"), record.get("progress"))
        if value != last:
            try:
                print(f"[{record.get('phase', record['state'])}] "
                      + json.dumps(record.get("progress", {}), ensure_ascii=False), file=sys.stderr, flush=True)
            except (OSError, ValueError):
                pass
            last = value

    try:
        for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            handlers[signum] = signal.signal(signum, interrupt)
        try:
            print(f"task_id={task_id}", file=sys.stderr, flush=True)
        except OSError:
            pass
        return wait_task(task_id, timeout, on_progress=progress)
    finally:
        for signum, handler in handlers.items():
            signal.signal(signum, handler)


def _queries(args) -> dict:
    if args.command == "profiles":
        return {"profiles": [asdict(profile) for profile in _profiles()]}
    if args.command == "hosts":
        hosts, warnings = read_ssh_hosts()
        return {"hosts": hosts, "warnings": warnings}
    if args.command == "credentials":
        credentials = []
        for credential in CredentialStore().load().values():
            ready = bool(credential.static_password and credential.totp_secret)
            try:
                validate_automatic_credential(credential)
            except Exception:
                ready = False
            credentials.append({"name": credential.name, "ready": ready,
                                "algorithm": credential.totp_algorithm, "period": credential.totp_period,
                                "digits": credential.totp_digits})
        return {"credentials": credentials}
    db = _database()
    if db is None:
        if args.action == "list":
            return {"history": []}
        raise CliError("HISTORY_NOT_FOUND", "没有找到历史记录。")
    with db:
        if args.action == "list":
            return {"history": [asdict(item) for item in db.list_history(args.limit)]}
        record = db.get_history(args.id)
        if record is None:
            raise CliError("HISTORY_NOT_FOUND", "没有找到历史记录。")
        return {"record": asdict(record)}


def _doctor() -> dict:
    checks = [{"name": name, "ok": shutil.which(name) is not None} for name in ("rsync", "ssh")]
    hosts, warnings = read_ssh_hosts()
    checks.append({"name": "ssh_hosts", "ok": bool(hosts), "count": len(hosts)})
    try:
        creds = CredentialStore().load()
        valid = False
        for credential in creds.values():
            try:
                validate_automatic_credential(credential)
                valid = True
            except (ValueError, TypeError):
                pass
        checks.append({"name": "credentials", "ok": valid,
                       "count": len(creds)})
    except RuntimeError:
        checks.append({"name": "credentials", "ok": False})
    try:
        _profiles()
        checks.append({"name": "database", "ok": True})
    except (OSError, sqlite3.Error):
        checks.append({"name": "database", "ok": False})
    parent = data_root()
    while not parent.exists() and parent != parent.parent:
        parent = parent.parent
    checks.append({"name": "runtime_writable", "ok": os.access(parent, os.W_OK | os.X_OK)})
    return {"ready": all(check["ok"] for check in checks), "checks": checks, "warnings": warnings}


def _execute(args) -> tuple[dict, int]:
    if args.command in {"sync", "preview"}:
        request, credential_name, profile_name = _request(args)
        record = submit_task(request, credential_name, profile_name)
        if args.detach:
            return record, 0
        record = _wait(record["task_id"], args.wait_timeout)
        return record, record.get("cli_exit_code", 8)
    if args.command == "wait":
        record = _wait(args.task_id, args.timeout)
        return record, record.get("cli_exit_code", 8)
    if args.command == "status":
        return get_task(args.task_id), 0
    if args.command == "cancel":
        return cancel_task(args.task_id), 0
    if args.command == "logs":
        record = get_task(args.task_id)
        path = Path(record.get("log_path") or data_root() / "tasks" / record["task_id"] / "output.log")
        if path.exists():
            with path.open(encoding="utf-8") as stream:
                lines = list(deque(stream, maxlen=args.tail))
        else:
            lines = []
        return {"task_id": record["task_id"], "state": record["state"], "lines": lines}, 0
    if args.command == "doctor":
        result = _doctor()
        return result, 0 if result["ready"] else 4
    return _queries(args), 0


def _emit(result: dict, json_mode: bool) -> None:
    if json_mode:
        print(json.dumps(result, ensure_ascii=False, allow_nan=False), flush=True)
    elif result.get("error"):
        print(f"{result['error']['code']}: {result['error']['message']}")
        if result.get("task_id"):
            print(f"任务编号：{result['task_id']}")
    elif result.get("task_id"):
        print(f"任务 {result['task_id']}：{result.get('state', 'unknown')}")
        if result.get("summary"):
            print(json.dumps(result["summary"], ensure_ascii=False))
        if "lines" in result:
            print("".join(result["lines"]), end="")
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    json_mode = "--json" in argv
    command = next((value for value in argv if not value.startswith("-")), "")
    try:
        args = parser().parse_args(argv)
        command = args.command
        result, exit_code = _execute(args)
    except WaitInterrupted as exc:
        exit_code = 128 + exc.signum
        result = {**get_task(exc.task_id), "error": {"code": "WAIT_INTERRUPTED",
                  "message": "已结束等待；后台任务继续。停止传输请使用 cancel。"}}
    except CliError as exc:
        exit_code = exc.exit_code
        details = dict(exc.details)
        result = {"accepted": False, **details.pop("task", {}), **details,
                  "error": {"code": exc.code, "message": str(exc),
                            "next_action": "根据错误检查参数或配置；已有任务请先查询状态。"}}
    except (ValueError, RuntimeError) as exc:
        exit_code = 2
        result = {"accepted": False, "error": {"code": "CONFIG_INVALID", "message": "参数或已有配置无效，请检查配置。"}}
    except (OSError, sqlite3.Error):
        exit_code = 4
        result = {"accepted": False, "error": {"code": "LOCAL_IO_FAILED", "message": "无法访问本地依赖或数据，请检查权限和存储。"}}
    result = {"schema_version": 1, "command": command, **result}
    try:
        _emit(result, json_mode)
    except (BrokenPipeError, OSError):
        # Output consumers may disappear; they never own the background task.
        pass
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
