# NJU-HPC Sync

NJU-HPC Sync 是一个面向 Ubuntu/Linux 的小型 `rsync + OpenSSH` 图形前端。它不解析或复制 `~/.ssh/config`，也不实现 SSH/SFTP；Profile 中只保存 SSH Host 别名，实际连接完全交给系统 `rsync` 和 OpenSSH。

## 功能

- Profile 保存本地目录、SSH Host、远程目录、默认方向/模式和凭据引用。
- 不创建 Profile 也可以直接填写临时任务。
- `Local → NJU-HPC` 与 `NJU-HPC → Local` 双向同步，目录语义固定为同步目录内容。
- 普通同步：`rsync -avzP --stats --itemize-changes`；镜像同步额外使用 `--delete`。
- 普通和镜像都支持 `--dry-run` 预览；镜像执行前强制预览并确认，界面显示待删除数量。
- PTY/pexpect 处理 SSH password、passphrase、verification code 和 OTP prompt。
- 手工模式输入完整的“固定密码 + 空格 + TOTP”；自动模式在 SSH 真正出现 prompt 时实时生成 TOTP。
- 自动 TOTP 支持 SHA1/SHA256/SHA512、周期和位数，并避免连续的预览与正式同步重复提交已使用的时间窗口。
- 实时 rsync 日志、状态、当前输出、停止、复制、保存和 SQLite 历史；历史与详细日志默认保留 60 天，可在“管理 → 日志管理”中修改。
- `credentials.json` 原子写入、目录 `700`、文件 `600`，Profile 与凭据分离。

## 独立环境

项目提供 `environment.yml`。创建环境不会修改 base 或其它 conda 环境：

```bash
conda env create -f environment.yml
conda activate hpc-sync
```

也可以在已创建的环境中更新依赖：

```bash
conda run -n hpc-sync python -m pip install -r requirements.txt
```

系统仍需安装命令行工具：

```bash
sudo apt install rsync openssh-client
```

## 运行

在项目根目录执行：

```bash
conda run -n hpc-sync python main.py
```

首次使用可以先在“管理 → 凭据管理”创建凭据，再新建 Profile：

```text
Profile: NJU - siRNA
Local: /home/me/siRNA
Remote Host: nju
Remote Path: /fsb/home/.../siRNA
Credential: nju
```

Host `nju` 会原样传给 `ssh`/`rsync`，程序不会重新读取或维护 HostName、User、Port、ProxyJump 等 SSH 配置。

自动模式保存固定密码和 TOTP Secret；完整动态密码只在 PTY 认证瞬间生成并发送，不写入命令行、环境变量、SQLite 或日志。手工模式可以作为自动认证的备用方式。

## 测试

```bash
conda run -n hpc-sync pytest -q
```

测试覆盖命令构造和目录尾部 `/` 语义、特殊字符路径、TOTP RFC 向量、凭据权限、SQLite 历史、PTY password prompt 和日志脱敏。没有真实 HPC 凭据时可以使用手工模式或 fake SSH/rsync 做本地验证。

## 数据位置

```text
~/.config/nju-hpc-sync/credentials.json   # 固定密码/TOTP Secret，权限 600
~/.local/share/nju-hpc-sync/nju-hpc-sync.sqlite3  # Profile 和历史，不含凭据
```

## 构建 Ubuntu deb

发布包固定在 Ubuntu 22.04 amd64 环境构建，支持 Ubuntu 22.04 及以上版本。程序包内置 Python 和 Python 依赖，但继续使用系统的 `rsync` 与 OpenSSH。

```bash
conda activate hpc-sync
python -m pip install -r build-requirements.txt
./scripts/build_deb.sh
```

产物写入 `release/`，同时生成 SHA-256 校验文件：

```text
release/nju-hpc-sync_1.0.0_amd64.deb
release/nju-hpc-sync_1.0.0_amd64.deb.sha256
```

使用 `apt` 安装可以自动复用或补齐系统依赖：

```bash
sudo apt install ./release/nju-hpc-sync_1.0.0_amd64.deb
```

安装后可从应用菜单启动，也可以运行 `nju-hpc-sync`。升级和普通卸载不会删除用户主目录中的 Profile、历史或凭据。

Ubuntu 22.04 完成功能测试后，可以使用 Docker 对更新版本执行安装和 offscreen 启动冒烟测试：

```bash
./scripts/smoke_test_deb.sh
```

## 退出码提示

本地目录和远程目录最终都会规范化为带一个尾部 `/` 的目录路径，程序按“同步目录内容”的语义处理。主界面日志按轮次显示命令、连接摘要、状态、文件统计、数据量、耗时和最多 10 条变更明细，开始与结束时间按操作系统本地时区显示；同步历史仍保存原始 rsync 输出。超过日志保留时间的历史会在应用启动、设置变更及每次同步完成后自动清理。界面对 `0`、`23`、`24`、`255` 提供友好提示，认证失败不会显示实际密码或验证码。停止只向本次启动的 rsync 进程组发送信号，不触碰其它终端中的 rsync/SSH。

## 目录

```text
main.py
app/
  main_window.py       # 主窗口、线程和同步工作流
  dialogs.py           # Profile、凭据、手工认证、历史
  rsync_runner.py      # PTY runner、prompt、取消、脱敏
  rsync_command.py     # argv 构造与运行前检查
  auth.py              # 手工/自动认证 provider
  totp.py              # TOTP 生成、剩余时间、运行期防重用
  credential_store.py  # credentials.json 与权限
  database.py          # SQLite schema 和 CRUD
  models.py            # 数据模型与枚举
  paths.py             # 路径规范化和日志脱敏
tests/
```
