请帮我设计并实现一个运行于 Linux / Ubuntu 的小型桌面 GUI 软件，用于方便地在本地目录和远程 HPC 目录之间通过 rsync 进行文件同步。

这个程序不是重新实现 rsync、SSH 或 SFTP，而应该尽可能直接调用系统现有的 `rsync` 和 OpenSSH，从而完整兼容用户已有的 `~/.ssh/config`、SSH Host 别名和 HPC 特殊认证机制。

## 一、项目背景

我目前在 Ubuntu/Linux 上通过如下方式访问南京大学 HPC：

```sshconfig
Host nju
    HostName entry.nju.edu.cn
    User "ww_suns/10.1.0.101/self"
    Port 22
    ServerAliveInterval 30
    ServerAliveCountMax 3
```

因此终端中可以直接：

```bash
ssh nju
```

或者：

```bash
rsync -avzP /local/path/ nju:/remote/path/
```

HPC 登录密码由两部分组成：

```text
固定密码 + 一个空格 + 当前 TOTP
```

例如：

```text
MyPassword 123456
```

其中：

- TOTP
- SHA1
- 30 秒周期
- 6 位数字

而且服务器的 TOTP 可能存在“使用成功一次后立即失效”的限制。

之前尝试 Beyond Compare 时，由于它自己实现 SFTP，而不是直接调用系统 OpenSSH，出现了 NJU 网关兼容性和重新认证问题。

因此本项目必须遵循一个重要原则：

**底层文件传输优先完全使用系统原生 `rsync + ssh`，不要自己重新实现 SFTP 协议。**

---

# 二、软件核心目标

我要做的是一个“小而实用的 rsync GUI”。

整体逻辑：

```text
GUI
 │
 ├─ 常用同步任务管理
 ├─ 本地/远程路径管理
 ├─ 上传/下载方向切换
 ├─ 普通同步/强制镜像
 ├─ SSH/TOTP认证
 ├─ rsync输出显示
 └─ 历史记录
       │
       ▼
    系统 rsync
       │
       ▼
    系统 OpenSSH
       │
       ▼
       HPC
```

请优先保证可靠性、简单性和安全性，不要过度设计。

---

# 三、推荐技术栈

优先使用：

```text
Python 3
PySide6
SQLite
rsync
OpenSSH
pexpect / pty
pyotp
```

建议：

- PySide6：桌面 GUI
- SQLite：保存同步 Profile 和操作历史
- `subprocess` / `QProcess`：普通外部程序调用
- 如果 rsync 的密码交互必须依赖 TTY，则使用 `pexpect` 或 Python `pty`
- `pyotp`：TOTP 自动计算
- JSON：保存本地认证凭据

不要自己实现 SSH 协议。

---

# 四、Profile：常用同步任务

程序需要允许创建多个“同步 Profile”。

这里 Profile 的意思是“一套保存好的同步配置”。

例如：

```text
NJU - siRNA
NJU - Hv1
NJU - NPC1
NJU - test
```

一个 Profile 至少包含：

```text
名称

本地路径
/local/path/

远程 Host
nju

远程路径
/fsb/home/xxx/project/

默认同步方向
Local → HPC
或者
HPC → Local

默认同步模式
普通
或者
强制镜像
```

数据库中不要把：

```text
nju:/fsb/home/...
```

完全作为不可拆分字符串存储。

最好拆分：

```text
remote_host = nju
remote_path = /fsb/home/...
```

执行时再组合：

```text
nju:/fsb/home/...
```

---

# 五、主界面设计

主界面希望保持简单。

建议：

```text
┌──────────────────────────────────────────────┐
│ HPC Sync                                     │
├───────────────┬──────────────────────────────┤
│ Profiles      │ 当前任务                     │
│               │                              │
│ ★ NJU-siRNA   │ 本地                         │
│ ★ NJU-Hv1     │ /home/me/project             │
│ ★ NJU-test    │ [选择]                       │
│               │                              │
│ + 新建        │ 远程                         │
│               │ nju:/fsb/home/...            │
│               │ [编辑]                       │
│               │                              │
│               │ [ Local → HPC ]              │
│               │ [ HPC → Local ]              │
│               │                              │
│               │ ○ 普通同步                   │
│               │ ○ 强制镜像                   │
│               │                              │
│               │ [ 预览 ]  [ 开始同步 ]       │
├───────────────┴──────────────────────────────┤
│ 日志                                         │
│ > sending incremental file list...           │
│ > xxx                                        │
│                                              │
└──────────────────────────────────────────────┘
```

---

# 六、本地路径与远程路径

必须同时支持：

### 1. 常用路径

保存到 Profile。

### 2. 临时路径

用户无需新建 Profile，也可以：

```text
Local:
/tmp/test

Remote:
nju:/fsb/home/.../tmp
```

然后直接同步。

临时同步也要进入历史记录。

---

# 七、同步方向

必须支持：

```text
Local → HPC
```

以及：

```text
HPC → Local
```

界面提供非常明显的方向切换按钮，例如：

```text
[ Local → HPC ]
```

点击交换：

```text
[ HPC → Local ]
```

不要真的把两个输入框内容交换。

建议始终保持：

```text
左边 / 上方 = Local
右边 / 下方 = HPC
```

只改变 Source 和 Destination。

这样可以避免使用 `--delete` 时由于路径交换而误删文件。

---

# 八、两种同步模式

需要严格支持两种 rsync 模式。

## 普通同步

等价于：

```bash
rsync -avzP SOURCE DESTINATION
```

特点：

- 复制新增文件
- 更新发生变化的文件
- 不删除 Destination 中额外存在的文件

界面名称：

```text
普通同步
```

---

## 强制镜像

等价于：

```bash
rsync -avzP --delete SOURCE DESTINATION
```

特点：

Destination 最终尽量与 Source 保持一致。

特别注意：

`--delete` 有误删风险。

因此强制镜像必须：

1. 使用明显的警告 UI
2. 默认不要选中
3. 执行前必须先 Preview
4. Preview 使用：

```bash
rsync -avzP --delete --dry-run SOURCE DESTINATION
```

显示：

- 将新增哪些文件
- 将更新哪些文件
- 将删除哪些文件

如果存在删除操作，应单独显示：

```text
⚠ 将删除 17 个文件
```

然后用户明确确认：

```text
确认执行镜像同步
```

才能真正执行。

---

# 九、Preview / Dry Run

普通模式也建议支持预览：

```bash
rsync -avzP --dry-run SOURCE DESTINATION
```

强制模式：

```bash
rsync -avzP --delete --dry-run SOURCE DESTINATION
```

最好解析 rsync 输出并尽量区分：

```text
新增
更新
删除
```

如果解析复杂，第一版至少原样显示 rsync dry-run 输出。

---

# 十、认证机制

这是本软件非常重要的部分。

不要在代码里硬编码用户名、密码或 TOTP Secret。

程序需要支持两种认证方式。

---

## 模式 A：人工输入完整密码

这是第一版必须优先完成的模式。

启动：

```bash
rsync ...
```

当 SSH 输出密码提示时，例如：

```text
Password:
```

GUI 弹出一个密码窗口：

```text
请输入 HPC 登录密码

[________________________]

[确认]
```

用户手动输入：

```text
固定密码 + 空格 + 当前TOTP
```

输入框必须使用 Password Echo Mode。

用户通过该弹窗临时输入的完整密码只存在于内存中。

完成本次认证之后立即清除。

禁止：

- 写入 SQLite
- 写入历史日志
- 写入 stdout
- 写入 rsync 日志
- 写入 Debug 日志

---

# 十一、自动 TOTP

在基础版本稳定以后增加自动 TOTP 功能。

用户可以配置：

```text
固定密码
TOTP Secret
算法：SHA1
周期：30秒
位数：6
```

程序调用：

```python
pyotp.TOTP(...)
```

在认证真正发生的那一刻生成：

```text
当前 TOTP
```

拼接：

```python
full_password = static_password + " " + current_totp
```

然后通过 pexpect / PTY 发送给 SSH。

特别注意：

**不要提前生成 TOTP。**

应该等真正检测到 Password Prompt 时才生成。

因为验证码只有 30 秒有效。

另外需要考虑：

**一个 TOTP 成功使用以后可能不能再次使用。**

因此：

- 每次重新认证必须重新生成 TOTP
- 不允许缓存完整动态密码
- 不允许自动重用上一次成功使用过的 OTP
- 如果发生重新连接，需要重新处理认证

---

# 十二、固定密码与 TOTP Secret 的本地存储

本软件主要供个人在自己的 Linux / Ubuntu 电脑上使用。

用户能够保证自己的电脑、本地账户和磁盘环境基本可信，因此固定密码和 TOTP Secret **允许持久化保存在本地**。

不需要为了凭据存储引入过度复杂的企业级密钥管理系统。

设计目标是在：

```text
使用方便
+
实现简单
+
具备基本安全性
```

之间取得平衡。

---

## 1. 推荐使用独立的本地凭据文件

建议使用：

```text
~/.config/hpc-sync/credentials.json
```

保存认证信息。

例如：

```json
{
  "nju": {
    "password": "MyStaticPassword",
    "totp_secret": "XXXXXXXXXXXXXXXX",
    "totp_algorithm": "SHA1",
    "totp_period": 30,
    "totp_digits": 6
  }
}
```

第一版允许使用这种简单方式。

不需要强制接入：

```text
GNOME Keyring
Secret Service
libsecret
```

之类的系统凭据服务。

---

## 2. 设置文件权限

虽然凭据保存在本地，但程序至少应该自动将该文件权限设置为：

```bash
chmod 600 ~/.config/hpc-sync/credentials.json
```

最终权限类似：

```text
-rw------- user user credentials.json
```

即正常情况下：

```text
只有当前 Linux 用户
可以读取和修改
```

程序创建或更新凭据文件以后，应主动确保权限为 `600`，而不是完全依赖系统默认 umask。

---

## 3. Credential 与 Profile 分离

不要让每一个 Profile 都重复保存一份密码和 TOTP Secret。

例如：

```text
Profile:
NJU - siRNA

Credential:
nju
```

以及：

```text
Profile:
NJU - Hv1

Credential:
nju
```

都可以引用同一个：

```text
Credential: nju
```

其结构：

```text
nju
├── static_password
├── totp_secret
├── totp_algorithm
├── totp_period
└── totp_digits
```

因此：

```text
NJU - siRNA ─┐
NJU - Hv1    ├──→ Credential: nju
NJU - NPC1   │
NJU - test  ─┘
```

以后如果 HPC 固定密码发生变化，只修改一次即可。

Profile 中可以保存：

```text
credential_name = nju
```

然后在执行同步时找到对应凭据。

---

## 4. GUI 增加凭据管理页面

增加一个简单的：

```text
凭据管理
```

页面。

例如：

```text
┌────────────────────────────────────────┐
│ 凭据：NJU                              │
│                                        │
│ 固定密码                               │
│ [••••••••••••••••••]       [显示]     │
│                                        │
│ TOTP Secret                            │
│ [••••••••••••••••••]       [显示]     │
│                                        │
│ Algorithm        SHA1                  │
│ Period           30                    │
│ Digits           6                     │
│                                        │
│ 当前验证码                             │
│ 583104                                 │
│                                        │
│ [测试 TOTP]                  [保存]    │
└────────────────────────────────────────┘
```

默认隐藏：

```text
固定密码
TOTP Secret
```

用户主动点击：

```text
显示
```

之后才显示明文。

如果方便，可以让：

```text
当前验证码
```

每秒刷新倒计时，例如：

```text
583104
剩余 17 秒
```

这样还能方便用户验证程序生成的 TOTP 是否与手机验证码一致。

---

## 5. 自动认证流程

如果用户启用了自动认证，并已经保存：

```text
static_password
totp_secret
```

那么同步过程中可以自动完成认证。

流程：

```text
点击开始同步
       ↓
启动 rsync
       ↓
rsync 启动 OpenSSH
       ↓
pexpect 等待认证提示
       ↓
检测到 Password:
       ↓
读取固定密码
       ↓
读取 TOTP Secret
       ↓
此时生成 TOTP
       ↓
password + " " + TOTP
       ↓
发送给 SSH
       ↓
继续同步
```

例如：

```text
固定密码：
MyPassword

当前TOTP：
583104
```

程序生成：

```text
MyPassword 583104
```

并发送。

---

## 6. 不保存完整动态密码

允许持久化：

```text
固定密码        ✅
TOTP Secret    ✅
TOTP Algorithm ✅
TOTP Period    ✅
TOTP Digits    ✅
```

但是不要持久化：

```text
当前 TOTP               ❌
password + TOTP 完整串  ❌
```

因为这两项没有长期保存价值。

完整动态密码：

```text
MyPassword 583104
```

只应该在认证发生时临时生成，并短暂存在于程序内存中。

认证结束以后及时释放相关引用。

---

## 7. TOTP 必须在认证时实时生成

不要在用户点击：

```text
开始同步
```

的一瞬间就生成 TOTP。

正确流程是：

```text
开始同步
    ↓
启动 rsync
    ↓
连接服务器
    ↓
等待 Password Prompt
    ↓
此时才调用 pyotp
    ↓
生成当前验证码
```

因为服务器连接、SSH 握手等过程可能花费数秒。

如果提前生成验证码，就可能出现：

```text
生成验证码
    ↓
等待连接
    ↓
验证码刚好跨过30秒边界
    ↓
认证失败
```

---

## 8. 防止重复使用同一个 TOTP

目标 HPC 可能存在这样的规则：

> 一个动态验证码成功使用一次以后，即使还没有超过 30 秒，也不能再次使用。

因此程序需要记录当前运行期间：

```text
last_used_totp
last_used_totp_counter
```

这两个值只需要存在于内存中。

例如：

```text
10:00:05
生成 583104
认证成功

10:00:18
SSH 再次要求认证
```

此时：

```text
当前 pyotp 仍然生成 583104
```

程序不能直接再次提交。

应该：

```text
发现当前验证码 == last_used_totp
        ↓
等待进入下一个 TOTP 时间窗口
        ↓
生成新的验证码
        ↓
提交
```

GUI 可以显示：

```text
当前动态验证码已经用于上一轮认证。

正在等待下一验证码……
```

进入新周期后：

```text
生成新 TOTP
→ 自动继续认证
```

---

## 9. TOTP 时间边界优化

为了避免验证码马上过期，可以增加一个简单优化。

例如：

```text
当前验证码剩余时间 < 3 秒
```

此时不要立即提交。

而是：

```text
等待下一周期
→ 获得完整约30秒有效期的新验证码
→ 再提交
```

阈值可以设置为：

```text
3 秒
```

或者：

```text
5 秒
```

不需要过度复杂。

---

## 10. 保留人工认证备用模式

即使已经保存固定密码和 TOTP Secret，也必须保留手工认证模式。

例如：

```text
认证方式：

● 自动 TOTP
○ 手工输入
```

自动模式：

```text
固定密码
+
TOTP Secret
        ↓
程序自动认证
```

手工模式：

遇到 SSH Password Prompt 时弹出：

```text
请输入完整 HPC 密码：

[____________________________]
```

用户自己输入：

```text
固定密码 + 空格 + 当前TOTP
```

这样即使未来：

- 自动认证逻辑出现问题
- HPC 改变登录规则
- TOTP Secret 尚未配置
- 用户暂时不希望自动认证

仍然可以继续正常使用 rsync。

---



## 12. 基本安全要求

虽然允许将固定密码和 TOTP Secret 保存在本地，但仍需要遵守以下最低要求：

- 固定密码不能输出到日志
- TOTP Secret 不能输出到日志
- 当前 TOTP 不应输出到普通运行日志
- 完整 `password + TOTP` 绝对不能输出到日志
- GUI 默认隐藏固定密码
- GUI 默认隐藏 TOTP Secret
- credentials.json 权限必须为 `600`
- credentials.json 不得进入 Git
- `.gitignore` 中明确排除凭据文件
- Debug 模式也不能打印密码或 Secret
- Exception traceback 中不要附带凭据内容
- 不要把完整密码作为 shell 参数传递
- 不要把密码放进环境变量
- 不要为了调试把认证字符串写入临时文件

除此之外，不需要为了个人本地小工具采用过于复杂的企业级安全方案。

本项目优先追求：

> **本地使用方便、稳定、实现简单，同时保持合理的基础安全性。**

---

# 十三、PTY / pexpect

由于 SSH 密码通常通过 TTY 获取，不能简单：

```bash
echo password | rsync
```

也不要使用：

```text
sshpass
```

作为主要方案。

优先使用：

```python
pexpect
```

或者 PTY。

大致流程：

```text
spawn rsync
    ↓
监听输出
    ↓
匹配 Password:
    ↓
判断认证模式
    │
    ├─ 手工模式
    │     ↓
    │   GUI要求用户输入密码
    │
    └─ 自动模式
          ↓
        读取Credential
          ↓
        实时生成TOTP
          ↓
        拼接完整密码
    ↓
sendline(password)
    ↓
继续读取rsync输出
```

但实际 Password Prompt 可能不是严格：

```text
Password:
```

所以匹配逻辑需要兼容：

```text
password:
Password:
password for xxx:
Verification code:
OTP:
```

同时避免误判普通 rsync 输出。

如果目标 NJU HPC 实际只出现：

```text
Password:
```

并要求：

```text
固定密码 + 空格 + TOTP
```

则优先针对该模式保证稳定。

---

# 十四、实时日志

主界面需要实时显示 rsync 输出，例如：

```text
sending incremental file list
./
model.py
data/file01.dat

     8.31M  45%   20.3MB/s
```

最好能正确处理：

```text
\r
```

因为 rsync `-P` 的 progress 会在同一行不断刷新。

日志区提供：

```text
清空
复制
保存日志
```

但任何密码/TOTP都绝对不能出现在日志里。

---

# 十五、任务状态

每一次同步需要状态：

```text
Waiting
Connecting
Authenticating
Previewing
Transferring
Success
Failed
Cancelled
```

GUI 上显示。

例如：

```text
正在连接 nju...

正在认证...

正在同步...
██████████ 53%

同步完成
```

如果无法可靠计算总百分比，则第一版可以只显示 indeterminate progress bar 和当前文件。

不要为了进度条重新实现 rsync。

---

# 十六、取消同步

必须提供：

```text
停止
```

按钮。

点击后应该：

1. 优雅结束 rsync
2. 等待短暂时间
3. 如果没有退出再 terminate/kill
4. 历史记录标记：

```text
Cancelled
```

不要杀死系统中其他 rsync 进程。

只能终止本软件启动的那个进程及其子进程。

---

# 十七、历史记录

使用 SQLite。

每次同步至少保存：

```text
id
profile_name
start_time
end_time

local_path
remote_host
remote_path

direction
mode

dry_run / real_run

status
exit_code

duration
```

例如：

```text
2026-08-22 15:20
NJU - test
Local → HPC
普通同步
成功
耗时 12.4 秒
```

历史页面按照时间倒序。

建议界面：

```text
时间                Profile      方向          模式      状态
2026-08-22 15:20   NJU-test     Local→HPC    普通      成功
2026-08-22 14:11   NJU-Hv1      HPC→Local    普通      成功
2026-08-21 23:01   NJU-siRNA    Local→HPC    镜像      失败
```

点击一条可以查看详细 rsync 日志。

历史记录中严禁保存：

```text
固定密码
TOTP Secret
当前 TOTP
完整认证密码
```

---

# 十八、Profile 数据模型

建议：

```sql
profiles
--------
id
name

local_path

remote_host
remote_path

credential_name

default_direction
default_mode

created_at
updated_at
```

其中：

```text
credential_name
```

用于引用：

```text
~/.config/hpc-sync/credentials.json
```

中的凭据名称。

例如：

```text
Profile:
NJU - siRNA

credential_name:
nju
```

不要把密码直接存在 Profile 表中。

---

# 十九、运行前检查

每次同步前自动检查：

```bash
which rsync
which ssh
```

并检查：

```text
本地路径是否存在
remote_host是否为空
remote_path是否合法
```

如果启用了自动 TOTP，还需要检查：

```text
Credential 是否存在
固定密码是否存在
TOTP Secret 是否存在
TOTP 参数是否合法
```

还可以提供：

```text
测试 SSH
```

按钮：

```bash
ssh -T nju
```

不过考虑 HPC 可能没有正常 shell 行为，测试连接最好可配置。

也可以：

```bash
ssh nju "echo connection-ok"
```

但不要假设所有 HPC 都允许这种方式。

---

# 二十、不要解析 ~/.ssh/config

这是一个重要设计原则。

程序不需要重新读取：

```text
HostName
User
Port
ProxyJump
IdentityFile
ServerAliveInterval
ServerAliveCountMax
```

例如：

```text
remote_host = nju
```

就直接交给：

```bash
ssh nju
```

和：

```bash
rsync ... nju:/path
```

让 OpenSSH 自己处理：

```text
~/.ssh/config
```

这样用户以后修改 SSH 配置，也不需要同步修改本软件。

例如当前：

```sshconfig
Host nju
    HostName entry.nju.edu.cn
    User "ww_suns/10.1.0.101/self"
    Port 22
    ServerAliveInterval 30
    ServerAliveCountMax 3
```

本程序只需要知道：

```text
nju
```

不要重复维护：

```text
entry.nju.edu.cn
ww_suns/10.1.0.101/self
22
```

等信息。

---

# 二十一、Shell 安全

不要通过字符串拼接：

```python
os.system("rsync " + user_input)
```

避免 shell injection。

应该使用参数列表。

例如：

```python
[
    "rsync",
    "-avzP",
    source,
    destination
]
```

除非确实有必要，否则不要使用：

```python
shell=True
```

同时正确处理：

- 路径带空格
- 中文路径
- 特殊字符

远程路径也不要直接作为 Shell Command 拼接。

例如：

```python
destination = f"{remote_host}:{remote_path}"
```

然后作为独立参数交给 rsync。

---

# 二十二、路径尾部 `/` 的问题

rsync 中：

```bash
rsync source destination
```

和：

```bash
rsync source/ destination
```

含义不同。

GUI 必须明确处理这个问题。

本项目默认语义希望是：

> 同步这个目录“里面的内容”，而不是把整个目录本身再套一层。

因此执行时建议规范化 Source：

```text
source/
```

但是必须在代码和界面中明确说明。

不要偷偷改变用户预期。

建议在 Profile 中统一记录目录本身：

```text
/home/user/project
```

生成 rsync 参数时转换为：

```text
/home/user/project/
```

远端源目录同样处理。

例如下载：

```text
nju:/fsb/home/user/project/
```

而不是：

```text
nju:/fsb/home/user/project
```

Destination 是否带 `/` 也需要统一处理。

---

# 二十三、错误处理

根据 rsync exit code 给出友好提示。

例如：

```text
0    成功

23   部分文件传输失败

24   某些源文件在传输过程中消失

255  SSH连接或认证失败
```

原始错误信息仍然保留。

不要只显示：

```text
同步失败
```

而应该：

```text
同步失败

rsync exit code: 255

SSH connection closed.
```

认证失败时可以进一步显示：

```text
SSH认证失败。

可能原因：

- 固定密码错误
- TOTP错误
- TOTP已经使用过
- TOTP已经过期
- 网络连接异常
```

但不要在错误信息里打印实际密码或验证码。

---

# 二十四、不要做的事情

第一版不要：

- 自己实现 SFTP
- 自己实现 SSH
- 自己实现增量同步算法
- 自己计算文件差异代替 rsync
- 自己写网络传输层
- 创建复杂服务器守护进程
- 使用 Electron，除非有非常充分理由
- 引入复杂账户系统
- 引入远程凭据服务器
- 引入复杂企业级密钥系统
- 把项目做成数万行的大系统

这个项目核心应该是：

```text
漂亮、简单、稳定、可靠的 rsync 前端
```

---

# 二十五、建议项目目录

```text
hpc-sync/
├── main.py
├── requirements.txt
├── README.md
├── .gitignore
│
├── app/
│   ├── main_window.py
│   ├── profile_dialog.py
│   ├── credential_dialog.py
│   ├── auth_dialog.py
│   ├── history_window.py
│   │
│   ├── rsync_runner.py
│   ├── ssh_auth.py
│   ├── credential_store.py
│   ├── totp.py
│   │
│   ├── database.py
│   └── models.py
│
└── data/
```

其中：

```text
profile_dialog.py
```

负责：

```text
同步任务配置
```

```text
credential_dialog.py
```

负责：

```text
固定密码
TOTP Secret
TOTP参数
```

```text
credential_store.py
```

负责：

```text
读取/写入 credentials.json
设置 chmod 600
```

```text
totp.py
```

负责：

```text
生成 TOTP
剩余时间
防止重复使用
```

```text
ssh_auth.py
```

负责：

```text
处理认证状态
手工/自动认证
```

```text
rsync_runner.py
```

负责：

```text
启动 rsync
读取输出
终止任务
```

---

# 二十六、开发顺序

不要一次实现全部。

按照以下顺序逐步完成。

---

## Milestone 1：基础 GUI

实现：

```text
Local path
Remote Host
Remote path

Local → Remote
Remote → Local

普通同步
```

点击同步时首先能够生成正确 rsync 命令。

暂时可以不真正执行。

---

## Milestone 2：运行 rsync

真正运行：

```text
rsync
```

并实时显示输出。

使用：

```text
pexpect / PTY
```

处理 SSH 认证。

第一阶段只需要支持：

```text
手工输入完整密码
```

即：

```text
固定密码 + 空格 + TOTP
```

---

## Milestone 3：Profile 保存

使用 SQLite 保存：

```text
Profile名称
常用本地路径
Remote Host
常用HPC路径
默认同步方向
默认同步模式
credential_name
```

可以：

```text
新建
修改
删除
选择
```

Profile。

---

## Milestone 4：历史记录

实现：

```text
运行时间
Profile
Source
Destination
方向
同步模式
状态
耗时
exit code
日志
```

历史页面。

---

## Milestone 5：Preview 与强制镜像

增加：

```text
--dry-run
```

以及：

```text
--delete
```

实现：

```text
普通同步
强制镜像
```

并为 `--delete` 增加明确的危险操作确认。

---

## Milestone 6：自动 TOTP

增加：

```text
pyotp
```

支持：

```text
固定密码
+
TOTP Secret
        ↓
自动生成完整密码
        ↓
自动认证
```

需要正确处理：

```text
30秒周期
即将过期
OTP重复使用
重新认证
```

---

## Milestone 7：本地凭据保存

增加：

```text
~/.config/hpc-sync/credentials.json
```

实现：

```text
Credential创建
Credential修改
Credential删除
Profile绑定Credential
chmod 600
GUI默认隐藏密码
```

让用户可以实现：

```text
打开软件
→ 选择Profile
→ 点击同步
→ 自动完成TOTP认证
```

不再需要每次手工输入固定密码或验证码。

---

## Milestone 8：可选本地加密

如果前面的全部功能都已经稳定，再考虑：

```text
本地加密保存 credentials.json
```

该功能属于增强功能。

不是项目完成的必要条件。

不要因为这个功能破坏已有的稳定认证流程。

---

# 二十七、验收测试

至少测试以下情况。

---

### Case 1：普通上传

```text
Local → HPC
普通同步
```

只上传新增和改变的文件。

---

### Case 2：普通同步不删除

HPC 中存在额外文件。

普通同步后：

```text
额外文件仍然存在
```

---

### Case 3：强制同步

Dry run 显示：

```text
将删除 xxx
```

用户确认之后：

Destination 多余文件被删除。

---

### Case 4：反向下载

```text
HPC → Local
```

Source / Destination 正确。

不能因为方向切换而错误执行：

```text
Local → HPC
```

---

### Case 5：特殊路径

文件路径包含：

```text
空格
中文
()
[]
#
&
```

仍然正常工作。

---

### Case 6：SSH 密码错误

GUI 正确提示认证失败。

软件不能崩溃。

用户可以重新输入。

---

### Case 7：TOTP 正常

配置：

```text
SHA1
30秒
6位
```

程序生成的 TOTP 与手机 Authenticator 生成的验证码一致。

---

### Case 8：TOTP 接近过期

当前 TOTP：

```text
剩余 2 秒
```

程序不要立即提交。

等待下一验证码后再认证。

---

### Case 9：TOTP 已使用

例如：

```text
583104
```

已经成功认证过。

SSH 在同一个 30 秒周期再次要求 Password。

程序不能重复发送：

```text
583104
```

应该等待下一个验证码。

---

### Case 10：自动认证

保存：

```text
固定密码
TOTP Secret
```

以后：

```text
选择 Profile
→ 点击 Sync
```

不需要人工输入密码即可完成认证。

---

### Case 11：手工认证备用

关闭：

```text
自动认证
```

以后仍然可以通过 GUI 手动输入：

```text
固定密码 + 空格 + TOTP
```

完成认证。

---

### Case 12：网络断开

任务显示：

```text
Failed
```

保存历史和错误日志。

---

### Case 13：取消任务

点击：

```text
Cancel
```

只结束当前软件启动的 rsync。

不能杀死：

```text
其他终端里的 rsync
其他 SSH
```

---

### Case 14：凭据文件权限

创建：

```text
~/.config/hpc-sync/credentials.json
```

以后检查：

```bash
ls -l ~/.config/hpc-sync/credentials.json
```

应该类似：

```text
-rw------- user user ...
```

---

### Case 15：日志安全

搜索所有：

```text
运行日志
历史记录
Debug输出
```

不能找到：

```text
固定密码
TOTP Secret
完整password+TOTP
```

---

# 二十八、最终使用体验

目标最终实现如下体验。

第一次配置：

```text
打开 HPC Sync

↓
添加 Credential：

名称：
nju

固定密码：
••••••••••••

TOTP Secret：
••••••••••••

Algorithm：
SHA1

Period：
30

Digits：
6

↓
保存
```

然后建立 Profile：

```text
名称：
NJU - siRNA

Local：
/home/me/siRNA

Remote Host：
nju

Remote Path：
/fsb/home/.../siRNA

Credential：
nju

默认方向：
Local → HPC

默认模式：
普通同步
```

以后日常使用：

```text
打开 HPC Sync

↓
点击：

NJU - siRNA

↓
路径自动出现：

Local:
/home/me/siRNA

Remote:
nju:/fsb/home/.../siRNA

↓
选择：

Local → HPC

普通同步

↓
点击：

开始同步

↓
程序启动：

rsync -avzP ...

↓
SSH要求Password

↓
程序读取：

固定密码
TOTP Secret

↓
实时生成：

当前TOTP

↓
自动发送：

固定密码 + 空格 + 当前TOTP

↓
rsync开始同步

↓
显示：

同步成功
153 files
2.31 GB
01:42

↓
自动加入历史记录
```

因此最终日常操作应该尽可能缩短为：

```text
选 Profile
→ 选方向
→ 选模式
→ Sync
```

如果 Profile 中已经保存：

```text
默认方向
默认模式
Credential
```

那么最常见情况下甚至可以：

```text
选 Profile
→ Sync
```

---

# 二十九、开发要求

请先不要直接输出所有代码。

首先：

1. 分析需求；
2. 给出总体架构；
3. 给出 UI 页面结构；
4. 给出 SQLite schema；
5. 说明 rsync + pexpect 的交互方案；
6. 说明自动 TOTP 的认证状态机；
7. 说明 `credentials.json` 的结构与读写方式；
8. 指出可能遇到的安全问题；
9. 给出项目目录结构；
10. 给出 Milestone 1 的开发计划。

等我确认后，再从 Milestone 1 开始逐步实现。

每完成一个 Milestone：

- 给出完整可运行代码；
- 给出安装依赖命令；
- 给出运行方法；
- 给出测试方法；
- 给出当前项目目录；
- 明确本 Milestone 新增了哪些功能；
- 不要破坏之前已经能工作的功能。

如果需要修改已有代码：

- 尽量局部修改；
- 不要无故大规模重构；
- 保持模块职责清晰；
- 保持已有 Profile 和历史数据兼容。

开发环境以 Ubuntu Linux 为主。

最终优先保证：

```text
1. rsync 本身可靠
2. OpenSSH兼容 ~/.ssh/config
3. NJU HPC认证可靠
4. TOTP自动认证可靠
5. --delete 不会因为UI设计造成误删
6. GUI简单易用
7. 日志与历史记录清晰
8. 代码结构简单、容易维护
```

整个项目的核心原则始终是：

> **不要重新实现 rsync 和 SSH，只做一个可靠、方便、针对 HPC 工作流优化的图形前端。**