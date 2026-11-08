# NJU-HPC Sync

在 Linux 上用图形界面通过 **rsync + SSH** 同步本地与 HPC 集群的目录。支持上传、下载、预览变更、保存同步任务，以及手工密码或自动 TOTP 认证。

## 等价于什么命令？

```bash
# 上传：本地 → HPC
rsync -avzP --stats --itemize-changes -- /path/to/local/project/ hpc-alias:/path/to/remote/project/

# 下载：HPC → 本地
rsync -avzP --stats --itemize-changes -- hpc-alias:/path/to/remote/project/ /path/to/local/project/
```

| 界面操作 | 对应行为 |
| --- | --- |
| 普通同步 | 执行上述命令，增量传输新增或变化的文件，不删除目标端多余文件 |
| 预览 | 增加 `--dry-run`，查看预计变化，不实际传输或删除 |
| 强制镜像 | 增加 `--delete`，**删除目标目录中源目录没有的文件**；每次执行前都需要预览并确认，运行期间锁定任务参数 |

程序同步的是**目录里的内容**，会自动补齐路径末尾的 `/`。

## 安装和启动

适用于 **Ubuntu 22.04 及以上的 amd64 桌面系统**。从 [Releases](https://github.com/2489055825/NJU-HPC_Sync/releases/latest) 下载 `.deb` 安装包，在下载目录执行：

```bash
sudo apt install ./nju-hpc-sync_1.0.7_amd64.deb
nju-hpc-sync
```

也可以从应用菜单打开 **NJU-HPC Sync**。安装包已包含 Python 环境，无需另装 Conda；系统依赖由 apt 安装。

## 第一次打开：按这个顺序配置

### 1. 配置 SSH Host：告诉软件连接哪里

打开 **管理 → Host 管理**，在 `~/.ssh/config` 主文件中添加：

```sshconfig
Host hpc-alias
    HostName cluster.example.com
    User your_username
    Port 22
```

- `hpc-alias`：自己起的连接别名，之后从“远程 Host”下拉框选择它。
- `HostName`、`User`、`Port`：替换为集群提供的登录地址、账号和 SSH 端口。

点击“保存”并确认，软件会备份为 `~/.ssh/config.bak`，自动刷新 Host 列表。**已有 SSH Host 配置可直接使用，无需重复添加。** 列表也会读取 `Include` 子文件；Host 管理只编辑主文件。

首次连接出现主机指纹提示时，请先与管理员提供的指纹核对，再选择接受。Host 编辑器有未保存修改时，关闭会询问保存、放弃或取消。

### 2. 选择认证方式：告诉软件怎么登录

**手工登录：无需创建凭据。** 在主界面将“认证方式”切换为 **手工输入完整密码**，连接时按提示输入集群要求的完整密码。若集群要求“固定密码 + 空格 + 当前 TOTP 验证码”，就按这个格式输入。已配置 SSH 密钥免密登录的用户也选择此模式，无需保存 TOTP 凭据。

**自动 TOTP：需要先保存凭据。** 打开 **管理 → 凭据管理 → 新建**，填写：

| 字段 | 填写内容 |
| --- | --- |
| 名称 | 自己起的凭据名称，如“我的集群账号” |
| 固定密码 | 集群账号的固定密码 |
| TOTP Secret | 绑定认证器时的长期密钥（Base32），**不是当前的验证码** |
| Algorithm / Period / Digits | 与集群要求一致；界面默认 `SHA1 / 30 秒 / 6 位` |

保存后，回到主界面选择这条“凭据”，将“认证方式”设为 **自动 TOTP**。程序会在认证提示出现时生成验证码，并提交“固定密码 + 空格 + 验证码”。没有 TOTP Secret 时，使用手工登录即可。

> 主界面默认是“自动 TOTP”。手工登录时必须切换“认证方式”，只在“凭据”里选择“（手工输入）”还不够。

### 3. 新建 Profile：保存一组同步目录

点击左侧 **Profiles → ＋ 新建**：

| 字段 | 示例或说明 |
| --- | --- |
| 名称 | 我的同步项目 |
| 本地目录 | `/path/to/local/project/`，可点击“选择”浏览 |
| 远程 Host | 选择刚才配置的 `hpc-alias` |
| 远程目录 | `/path/to/remote/project/`，填写集群上的目录路径 |
| 凭据 | 自动认证选已保存的凭据；手工认证选“（手工输入）” |
| 默认方向 / 默认模式 | 按需选上传或下载；首次建议选“普通同步” |

将示例替换为自己的目录后保存。**Profile 保存连接别名和同步设置，主界面的“认证方式”仍需单独选择。** 只同步一次时，也可以不建 Profile，直接填写主界面。

## 开始同步，以及下次如何使用

1. 选择 Profile，确认本地目录、远程目录、凭据和认证方式。
2. 选择方向：**Local → NJU-HPC** 是上传，**NJU-HPC → Local** 是下载。
3. 点击 **预览**，核对文件变化；确认无误后点击 **开始同步**。使用强制镜像时，务必核对删除提示。
4. 在主界面查看当前文件、进度和日志；需要中止时点击 **停止**，等待验证码期间也可以取消。任务运行时关闭窗口，会先停止任务并保存结果，再自动关闭。过往结果在 **管理 → 同步历史** 中查看。

以后选择已保存的 Profile，检查方向和认证方式后，按同样步骤执行即可。

## 配置时遇到问题

| 现象 | 先检查什么 |
| --- | --- |
| Host 下拉框为空 | 是否写了具体的 `Host hpc-alias`，并保存、刷新；`Host *` 不会作为可选主机 |
| 提示缺少凭据 | 自动 TOTP 模式需要选已保存的凭据；手工登录请切换“认证方式” |
| 凭据读取失败 | 软件会提示并切换为手工认证，原凭据文件不会被改写；修复文件后可重新打开凭据管理 |
| SSH 连接或认证失败 | 可先在终端用 `ssh hpc-alias` 核对地址、账号、端口、网络及密码格式 |

保存的密码和 TOTP Secret 位于本机 `~/.config/nju-hpc-sync/credentials.json`，不要分享该文件。Profile 和历史位于 `~/.local/share/nju-hpc-sync/nju-hpc-sync.sqlite3`。

<details>
<summary>从源码运行与开发</summary>

需要 Conda，以及系统的 rsync 和 OpenSSH。在项目目录执行：

```bash
sudo apt install rsync openssh-client
conda env create -f environment.yml
./run.sh
```

测试：`conda run -n hpc-sync pytest -q`。打包：先安装 `build-requirements.txt`，再运行 `scripts/build_deb.sh`（Ubuntu 22.04 amd64）。

测试覆盖率（pytest-cov）：

```bash
conda run -n hpc-sync pytest -q --cov=app --cov-report=term-missing --cov-report=html
```

终端会显示覆盖率及未覆盖行；HTML 报告位于 `htmlcov/index.html`。无桌面环境时，在命令前加 `QT_QPA_PLATFORM=offscreen`。

</details>
