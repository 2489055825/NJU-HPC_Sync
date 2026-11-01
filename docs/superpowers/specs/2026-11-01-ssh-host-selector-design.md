# SSH Host 下拉选择

## 目的与范围

用户确认从 `~/.ssh/config` 及 Include 文件读取远程 Host。主界面和新建/编辑 Profile 使用不可编辑的下拉框，提供刷新；用户无需重复输入别名。保留已有 SSH 配置、认证和 rsync 参数构造方式。

## 方案

采用标准库静态枚举配置中的具体 Host，复用一个 PySide6 选择组件。直接调用 SSH 无法列出所有别名，还可能执行 Match exec；新增 SSH 库则增加不必要的依赖。读取只涉及 Host/Include，不运行配置中的指令，不读取密钥或凭据。

## 行为与约束

- 从 `~/.ssh/config` 开始读取，支持多别名、注释、大小写不敏感的指令和等号分隔。
- Include 支持绝对路径、相对于 `~/.ssh` 的路径、用户主目录和 glob；递归读取时避免循环。保持声明顺序并去重。
- 过滤通配、否定和不适合 rsync 的别名；不将 HostName、known_hosts 或 `/etc/hosts` 当成 Host 列表。
- 下拉框初始提示选择；刷新保留有效选择。旧 Profile 的别名缺失时显示缺失状态并阻止保存或执行，不能悄悄改选第一项。
- 无配置、无可用 Host、读取/解析失败时显示可操作的说明，应用仍可启动。
- 静态枚举不评估 Match/Host 条件；实际连接参数仍由 OpenSSH 决定。
- Python 3.11、PySide6；不添加依赖，不修改用户 SSH 文件，不执行真实远程连接。

## 验收

配置枚举单元测试和使用临时 HOME 的 Qt 集成测试覆盖读取、Include、刷新、选择保留、缺失 Host 拦截与 rsync 目标别名；完整 pytest、compileall 和示例数据界面截图用于交付验证。
