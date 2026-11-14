# 独立同步 CLI 设计

日期：2026-11-12

状态：用户已确认并授权实施；实现及最终验证记录见同日期的 cli 实施计划。

## 1. 意图与已确定的范围

让能够运行命令行的 agent 使用 NJU-HPC Sync 完成远程目录同步。软件负责连接、认证、传输和保存结果，agent 负责发起操作、决定是否允许镜像删除，以及读取命令结果。

以下决定已由用户明确选择：

- CLI 独立执行，不将任务发送给 GUI，不要求打开 GUI。
- 既能使用已有 Profile，也能临时指定同步目录和方向。
- 第一版实现核心同步及相关查询；不提供配置、Profile、SSH Host 或凭据的增删改命令。
- 使用已有的固定密码和 TOTP 密钥自动认证，不要求用户输入密码或验证码。
- 开放强制镜像；删除授权由调用 agent 决定，软件提供明确的命令参数。
- 同步开始后由软件独立接管。即使 agent 的调用终端关闭，任务仍须继续执行。
- 不做特定 agent 的消息推送、回调或唤醒集成；通过命令输出和退出码返回结果。
- 一次只执行一个任务，重复提交时返回忙碌；第一版没有任务队列或并行传输。
- 采用方案 A：每次任务启动独立后台执行进程，完成后退出。

后续章节中的命令名称、参数细节、输出协议、恢复策略和 GUI 共存方式是设计者提出的默认方案，不冒充用户已逐项确认的决定。用户可在本稿评审时调整。

## 2. 方案比较

### A. 每个任务启动独立后台进程（用户已选）

CLI 启动脱离调用终端的 worker。worker 独立执行认证和 rsync，并将状态、日志和最终结果保存到用户数据目录。CLI 可以等待结果，也可以提交后立即返回。

优点是无需安装和维持常驻服务，适合一次一个任务的范围；需要实现可靠的启动交接、进程互斥和异常状态识别。

### B. 常驻本地服务（本轮未选）

由持续运行的服务接收 CLI 请求并管理任务，便于将来扩展队列和恢复策略；同时需要服务的安装、启动、通信和维护机制。本轮没有需要常驻服务才能满足的功能。

## 3. 用户可见的执行行为

默认同步命令等待并返回最终结果，但真正的传输由独立 worker 完成。CLI 的等待进程和 worker 生命周期分离。

| 情况 | 行为 |
| --- | --- |
| 调用终端一直打开 | 显示进度，任务结束后返回最终结果和退出码 |
| 调用终端关闭、等待进程退出或被终止 | worker 继续，最终结果保存在本地 |
| 使用 `--detach` | worker 确认接管后立即返回任务编号和当前状态 |
| 等待达到指定超时 | 等待命令返回超时，传输继续 |
| 再次提交任务 | 返回忙碌和占用者信息，不新启动传输 |
| 明确调用取消命令 | 请求软件停止指定任务，保存取消结果 |
| 软件执行进程异常退出 | 显示中断或结果不确定，检查残留传输后再允许新任务 |

客户端收到 Ctrl+C 或终止信号只结束等待，不隐式取消传输；提示用户使用 `cancel`。这与独立接管的语义一致。操作系统重启后的自动续跑不属于第一版，恢复查询应说明原任务已中断。

软件不对已经关闭的终端发送完成消息。结果由仍在等待的命令返回，或由后续 `status` / `wait` 命令读取。

## 4. 命令与参数

沿用可执行文件名 `nju-hpc-sync`。无参数启动保留当前 GUI 行为；有 CLI 子命令时先分发，再按需导入代码，CLI 路径不初始化 Qt。

| 命令 | 用途 |
| --- | --- |
| `sync` | 提交实际同步，默认等待完成 |
| `preview` | 提交 rsync dry-run，参数与同步一致，不传输或删除目标文件 |
| `status [TASK_ID]` | 查询任务的当前状态或最终结果 |
| `wait [TASK_ID]` | 等待已有任务结束并返回其最终结果，不重新提交 |
| `cancel [TASK_ID]` | 明确取消任务 |
| `logs [TASK_ID]` | 查看已脱敏的任务日志 |
| `profiles list` | 查询已有 Profile 及关联凭据名称 |
| `hosts list` | 查询现有 SSH Host 别名和配置读取提示 |
| `credentials list` | 仅查询凭据名称和参数完整性，不显示密码、密钥或验证码 |
| `history list` / `history show ID` | 查询现有同步历史 |
| `doctor` | 检查依赖、配置可读性和本地运行条件；不测试远程连接或自动修复配置 |

各命令提供 `--help`。数据命令支持 `--json`；默认面向人的简洁文本同样不弹窗或要求输入。

### 4.1 两种目标来源

使用已有 Profile：

```bash
nju-hpc-sync sync --profile example-project --json
nju-hpc-sync preview --profile example-project --direction download --json
```

临时指定任务：

```bash
nju-hpc-sync sync \
  --local /path/to/local/project \
  --host hpc-alias \
  --remote /path/to/remote/project \
  --direction upload \
  --credential example-account \
  --json
```

参数规则：

- `--profile NAME` 与 `--local/--host/--remote` 两类来源互斥，避免半个 Profile 被隐式覆盖。
- Profile 任务默认使用保存的方向；允许用 `--direction upload|download` 显式覆盖。
- 临时任务必须给出全部路径、Host 和方向，不猜测上传或下载。
- `--credential NAME` 可以显式覆盖 Profile 的凭据名称；不会因此修改 Profile。
- 第一版沿用 GUI 的具体 SSH Host 别名选择范围，使用已有 SSH 配置；不新增 HostName、User、Port 或私钥配置参数。
- 目录始终按内容同步，沿用现有尾部 `/` 规范化和无 shell 的 argv 构造。
- worker 接管的是已规范化的任务快照。执行期间 Profile 或 SSH 配置变化不应悄悄改写该快照；OpenSSH 实际连接仍使用系统配置。

### 4.2 普通同步与镜像

```bash
nju-hpc-sync preview --profile example-project --mirror --json
nju-hpc-sync sync --profile example-project --mirror --json
```

普通同步是 CLI 默认模式。即使 Profile 的 GUI 默认模式是镜像，CLI 实际镜像也必须明确提供 `--mirror`，避免仅选择一个 Profile 就隐式授权删除。结果中必须显示实际方向和模式。

`--mirror` 本身是调用方允许删除目标端多余文件的明确声明，不再增加人工确认、`--yes` 或预览批准令牌。

`preview --mirror` 单独提供删除预览，供 agent 按用户授权决定是否执行。CLI 不强制执行前先做一次远程预览，不在后台等待 agent 再批准；agent 可以选择先预览再同步。镜像预览是当时的观察结果，不是文件系统快照，实际执行会基于执行时的目录状态。

GUI 现有的预览及人工确认流程保持原有语义。

### 4.3 等待、脱离与取消

```bash
# 等待最终结果；关闭本命令不会停止后台任务
nju-hpc-sync sync --profile example-project --json

# 接管成功后立即返回
nju-hpc-sync sync --profile example-project --detach --json

# 等待最多 120 秒，超时后任务仍继续
nju-hpc-sync sync --profile example-project --wait-timeout 120 --json

# 以下示例中的 TASK_ID 来自提交结果
nju-hpc-sync status TASK_ID --json
nju-hpc-sync wait TASK_ID --timeout 120 --json
nju-hpc-sync cancel TASK_ID --json
nju-hpc-sync logs TASK_ID --tail 200 --json
```

`--detach` 与 `--wait-timeout` 互斥。查询和等待不会重新启动任务。

省略任务编号时，`status` / `wait` / `logs` 优先选择当前 CLI 任务，否则选择最近一个 CLI 任务，并在结果中返回实际任务编号与选择依据。完全没有记录时返回 `TASK_NOT_FOUND`。

`cancel` 省略编号时只选择当前 CLI 任务；没有当前任务时返回 `NO_ACTIVE_TASK`。选定编号后取消请求始终绑定该编号，不能因任务切换而误取消另一个任务。对已经结束的明确编号重复取消是幂等操作，返回其终态且说明没有新的取消动作。

取消命令先提交取消请求，并最多等待 10 秒。超出该等待时间仍未退出时返回 `state=cancelling`、`terminal=false`；这表示请求已接受，不表示进程已停止。不得因此启动第二个任务。

## 5. 认证与 SSH 信任

CLI 从现有凭据文件读取固定密码和 TOTP Secret，按现有规则在认证提示出现时生成 `固定密码 + 空格 + 当前验证码`。

- 凭据来自 `--credential` 或 Profile。缺少名称、名称不存在或内容无效时返回可识别的配置错误，不随意选择其他凭据。
- 不提供密码、Secret、完整动态密码或验证码的命令行参数，也不通过环境变量传递这些值。
- CLI 不提供手工密码输入、GUI 弹窗或交互式认证备用流程。
- 第一版的 CLI 认证要求选定已有 TOTP 凭据；OpenSSH 可以继续使用其既有连接设置，但不增加新的密钥配置入口或单独的密钥认证模式。
- 陌生主机指纹需要确认、指纹发生变化、认证失败或遇到不支持的提示时，任务失败并返回清晰错误；不自动接受指纹、不改 SSH 配置、不无限尝试认证。
- 具体提示匹配沿用已经验证的 runner 规则，不在没有真实提示样本时扩展协议兼容性。

`TotpReplayGuard` 使用进程内状态，记录规范化 Secret 与算法、周期、位数组合对应的最近窗口。CLI worker 和 GUI 任务不会并发执行，因为它们共享同一把传输锁；因此不再把 TOTP 窗口声明写入磁盘，也不把认证关键路径绑定到 `fsync`。

时间窗口记录不保存或返回 Secret、固定密码、完整动态密码或验证码。已使用或临近过期的时间窗口等待下一窗口，取消可以中断等待。程序或 worker 崩溃后内存记录会丢失；服务端若拒绝同一窗口的再次使用，认证失败后等待下一窗口，不自动重试传输。

## 6. 后台执行与单任务互斥

### 6.1 启动交接

1. 解析参数、解析 Profile，执行本地运行前检查。
2. 取得用户级传输锁，检查是否存在需要恢复的旧任务。
3. 分配随机任务编号，可靠保存任务快照和初始状态。
4. 启动独立会话中的 worker，将 stdin 与调用终端分离，stdout/stderr 写入受控任务日志。
5. 将传输锁安全交给 worker；worker 确认已持有任务所有权、初始化了状态保存后，才向客户端确认接管。
6. 客户端关闭自己的锁描述符，然后等待结果或返回提交结果。

只有第 5 步成功，命令才能报告 `accepted=true`。准备或启动失败必须清理交接状态并报告失败；不能返回一个没有执行者的成功提交。

worker 内部同步调用现有 rsync/PTY 执行能力。其日志、PTY、取消监听和状态写入不能依赖 CLI 客户端继续存活，也不能保留调用终端的标准流作为任务输出目的地。

启动交接中被关闭的客户端可能尚未拿到编号。新终端可通过 `status` 找回当前任务；不应通过重复提交来猜测前一次是否成功。

### 6.2 互斥范围与 GUI 共存（设计者提出）

同一系统用户的所有新版 NJU-HPC Sync 传输共用一把内核级运行锁，包括 CLI 的实际同步、预览和 GUI 的 rsync 执行阶段。多个提交并发竞争时，只允许一个获得所有权，其余立即返回忙碌，不排队。

GUI 只增加底层运行锁和进程内 TOTP guard 的接入，保持原界面、认证交互和镜像确认流程。CLI 不向 GUI 派发任务，也不提供取消 GUI 任务的控制接口。

CLI 的忙碌结果包含占用者类型；CLI worker 占用时提供任务编号，GUI 占用时明确返回 `owner=gui`。GUI 已运行时，不要求先关闭窗口，只要求其没有正在执行的传输。旧版本程序无法遵守新增互斥协议，本轮保证针对同一新版程序。

### 6.3 异常退出与取消

任务状态至少包括 `starting`、`running`、`cancelling`、`succeeded`、`failed`、`cancelled`、`interrupted`，并独立记录 connecting/authenticating/previewing/transferring 等进度阶段。

取消请求通过绑定任务编号的本地控制记录传给 worker。worker 共享一个取消事件，通知 TOTP 等待和 rsync runner，先停止传输，再保存终态并释放运行锁。仅根据状态文件或未经验证的 PID 发送终止信号不可接受。

内核锁负责互斥，状态文件负责描述。过期 PID、心跳超时或残留状态文件不能单独证明任务结束。需要核对系统启动身份、进程实例身份和传输子进程；无法排除残留传输时返回 `RECOVERY_REQUIRED` 并拒绝新提交。

worker 异常死亡时，必须处理仍可能存活的 rsync/SSH 子进程。只有确认它们已停止或完成安全清理，才允许下一次传输。具体采用锁继承、子进程生命周期约束或经身份核验的清理，由实施计划选择并通过强制终止测试证明。

不自动重试失败任务，也不在软件重启后自动重放镜像。重新执行由 agent 根据原任务结果决定。

## 7. 状态、结果与日志保存

复用现有位置：

- 凭据：`~/.config/nju-hpc-sync/credentials.json`，本轮只读。
- Profile 和历史：`~/.local/share/nju-hpc-sync/nju-hpc-sync.sqlite3`。

新增运行数据建议置于同一应用数据目录：

```text
~/.local/share/nju-hpc-sync/
  transfer.lock
  tasks/
    <task-id>/
      task.json
      output.log
      cancel.request
```

`task.json` 是该 CLI 任务状态及最终结果的权威记录，包括规范化请求、所有者身份、状态、进度摘要、最终结果及关联历史编号。`output.log` 保存脱敏后的完整任务输出；`cancel.request` 仅在请求取消时出现。

任务目录使用私有权限，记录和日志为 `600`。任务记录不包含密码、密钥或验证码。worker 是活跃任务状态的唯一写入者；取消请求单独保存，防止客户端覆盖 worker 状态。状态更新使用同目录临时文件、原子替换及必要的落盘同步。

每次已被接管的 CLI 预览或同步，在结束时向现有历史写入一条记录。等待、查询和重复取消不重复写历史；全程的唯一任务编号用于串联状态与关联历史。

传输成功但写 SQLite 历史失败时，持久任务结果保留真实传输成功状态，返回 `history_saved=false` 和警告，不将其伪装成需要重新传输的失败。若连最终任务记录也无法可靠保存，后续查询应报告结果不确定及恢复需要，不从 worker 消失推断传输成功。

CLI 任务文件沿用现有日志保留天数（默认 60 天）；只能在提交/完成任务等写入路径清理已终结且超过保留期的记录，绝不清理活动任务。只读查询不初始化、迁移、清理或覆盖凭据、SSH 配置和数据库；没有已有配置时返回空列表或明确错误。

## 8. 命令输出协议

### 8.1 标准流

使用 `--json` 时，stdout 只输出一个最终 JSON 对象；进度、等待提示和脱敏日志输出到 stderr。正常等待模式在接管后立即通过 stderr 输出任务编号，以便调用方在等待被中断时找回任务。

机器处理只能依赖 stdout 的版本化字段和退出码，不解析 stderr 文本。stderr 关闭、日志消费者退出或 BrokenPipe 不能终止 worker。

脱离提交的结果明确区分“软件已接管”和“传输成功”：

```json
{
  "schema_version": 1,
  "command": "sync",
  "accepted": true,
  "task_id": "example-task-id",
  "state": "running",
  "terminal": false,
  "transfer_success": null
}
```

正常结束的结果至少包含：

```json
{
  "schema_version": 1,
  "command": "sync",
  "accepted": true,
  "task_id": "example-task-id",
  "state": "succeeded",
  "terminal": true,
  "transfer_success": true,
  "direction": "upload",
  "mode": "normal",
  "dry_run": false,
  "rsync_exit_code": 0,
  "summary": {
    "transferred_files": 12,
    "deleted_entries": 0,
    "transferred_file_bytes": 1048576
  },
  "history_saved": true,
  "history_id": 42,
  "warnings": [],
  "error": null
}
```

还需提供规范化的本地/远程目标、时间、耗时和日志位置。未得到的统计使用 `null`，不能用 `0` 冒充已知结果。`transfer_success` 在预览时表示该次 dry-run 是否成功，`dry_run=true` 必须明确显示。

预览结果包含新增、更新、删除计数及最多 100 条变更样例；样例截断时提供 `changes_truncated=true`，完整输出可从任务日志读取。rsync 未提供的完整计数可以为 `null`。

错误结构包含稳定 `code`、脱敏 `message` 和可执行的下一步建议；不得把未处理的异常对象、凭据对象或私密配置内容直接输出。忙碌错误带当前占用者信息，不能把它伪装成提交成功。

### 8.2 退出码

| 退出码 | 含义 |
| --- | --- |
| `0` | 命令操作成功；等待同步时必须是任务成功，脱离提交时只代表接管成功 |
| `2` | 参数、Profile、Host、凭据或任务编号有误 |
| `3` | 正在忙碌，未接管新任务 |
| `4` | 本地依赖、文件权限或启动前运行条件不满足 |
| `5` | SSH 信任或认证失败，需要处理连接配置或凭据 |
| `6` | rsync 传输失败；JSON 同时提供原始 rsync 退出码 |
| `7` | 等待的任务已取消 |
| `8` | worker 中断、结果不确定、内部错误或需要恢复 |
| `124` | 等待超时，后台任务继续 |
| `130` / `143` | 等待进程被中断或终止，后台任务继续 |

`status` 成功读取到一个失败任务时仍返回 `0`，任务成败由 `state` 判断；`wait` 和默认 `sync/preview` 根据任务终态返回成功、失败或取消的退出码。`cancel` 在取消请求已接受时可返回 `0`，必须同时用 `terminal` 区分请求接受与停止完成。

## 9. 与现有代码的衔接

可直接复用的核心包括 `models.py`、`rsync_command.py`、`rsync_runner.py`、`auth.py`、`totp.py`、`credential_store.py`、`database.py` 和 SSH Host 枚举。

需要独立于 Qt 的职责包括参数分发、任务管理/状态存储、后台 worker、结果序列化，以及进度与统计解析。现有 `MainWindow` 中的解析函数可提取为无 Qt 的共用函数；认证和 runner 组装也应共用一处核心接口，避免 GUI 与 CLI 漂移。

不得为了复用解析或同步逻辑而在 CLI 中导入 `MainWindow`、构造 QApplication 或运行 Qt 事件循环。`RsyncRunner.run()` 现有 `preflight` 参数并不执行检查，新的执行路径必须实际调用运行前检查。

GUI 原有 Profile 编辑、镜像确认、关闭窗口和认证交互保留。必要的底层互斥、进程内 TOTP guard 和纯函数提取属于本轮共享改动；不扩展 GUI 的后台任务界面。

PyInstaller 目前只打包 `main.py`。需要让同一个安装包支持 CLI 分发和内部 worker 启动，并验证冻结程序的重新执行、标准流和退出码。源码启动与已打包程序均须成立，不能只验证 `python main.py`。

具体模块数量、类名、锁交接手段、进程身份验证细节及内部 worker 参数属于实施计划的技术选择；不得改变上述用户可见语义。

## 10. 验收与验证

所有自动验证使用临时数据、合成凭据和本地模拟进程，不使用真实账户、SSH 配置或 HPC 目录。

必须覆盖：

1. Profile 和临时参数的上传/下载、路径规范化、特殊字符及无 shell argv。
2. CLI 不初始化 Qt，参数错误不弹窗、不读取 stdin 等待密码。
3. 固定密码 + TOTP 自动认证、单进程窗口防重复、等待可取消及日志脱敏。
4. 普通同步与显式镜像；预览无写入/删除；Profile 的镜像默认值不会隐式授权 CLI 删除。
5. 客户端关闭 stdin/stdout/stderr、退出、收到 SIGHUP/SIGTERM 或等待超时后，worker 仍完成任务并可查询结果。
6. 并发提交只有一个任务被接管；忙碌结果包含占用者信息；新版 GUI 与 CLI 的互斥也被验证。
7. 接管前后的启动竞态、worker 强制终止、PID 复用防护及残留传输处理，不误启动第二个传输或误杀其他进程。
8. 正在认证/传输时取消、重复取消、取消与完成竞争、取消超过等待上限时的真实状态。
9. 查询、重复等待、终端关闭后的结果找回，不重复执行或重复写历史。
10. stdout JSON 纯净、退出码和任务状态一致、rsync 部分失败统计、日志截断与脱敏。
11. 状态写入或历史写入失败、损坏记录、过期记录清理，失败不会被报告为接管成功或传输成功。
12. 既有完整 pytest、compileall 和 GUI 生命周期回归；源码与打包后的 CLI 接管、终端关闭、结果查询和取消冒烟验证。

本设计阶段不包含产品实现、安装升级或发布。用户后续已确认设计并授权实施；安装升级或发布仍不属于本次实施范围。

## 11. 技术审阅记录

2026-11-12，由独立审阅者从 Linux 后台进程、CLI 与打包实施角度检查本文。结论为 `nothing important left`：没有制定实施计划前必须再次向用户确认的关键缺口。独立 worker 与客户端等待最终结果可以同时成立；镜像授权、终端关闭后的任务存活、单任务互斥及结果找回语义均已明确。锁交接、进程身份验证和打包后重启 worker 等仍须在实施阶段以验证结果证明，审阅不代表功能已实现或已通过运行测试。
