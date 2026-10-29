# DeepTutor 本地运行指南（Windows / PowerShell）

本文介绍如何从源码安装、启动、验证和停止 DeepTutor。命令以本项目当前路径 `D:\codex_base\DeepTutor` 为例；如果项目位于其他位置，请替换该路径。

## 1. 本次运行记录

本次已完成以下操作：

- 使用 Python 3.12.14 创建项目虚拟环境 `.venv`。
- 以可编辑模式安装当前源码及默认 Python 依赖。
- 使用 `npm ci` 安装前端锁文件中的依赖。
- 以开发模式启动前后端，并将服务放到后台运行。
- 后端健康检查返回 `{"status":"ready"}`，前端 `/chat` 返回 HTTP 200。

| 项目 | 本次运行信息 |
| --- | --- |
| DeepTutor | 1.6.15 |
| Python | 3.12.14 |
| Node.js | 24.11.1 |
| 前端 | <http://localhost:3782/chat> |
| 后端 | <http://127.0.0.1:8001> |
| 就绪检查 | <http://127.0.0.1:8001/health/ready> |
| 工作目录 | `D:\codex_base\DeepTutor` |
| 启动日志 | `data/user/runtime/launcher.log` |

以上是本次验证记录，不代表服务始终在线。重启电脑或停止服务后，需要重新启动。

## 2. 环境要求

| 组件 | 要求与说明 |
| --- | --- |
| Python | 项目声明支持 `>=3.11,<3.15`，本指南使用 3.12 |
| Node.js / npm | 启动 Web 界面需要；项目 CI 使用 Node.js 22，本次使用 24.11.1 |
| uv | 本指南用于选择 Python、创建虚拟环境及安装依赖；也可使用标准 `venv` / `pip` |
| 网络 | 首次安装依赖、调用远程模型和外部服务时需要 |

在 PowerShell 中检查环境：

```powershell
Set-Location D:\codex_base\DeepTutor
python --version
node --version
npm.cmd --version
uv --version
uv python list --only-installed
```

本机默认 `python` 指向 Python 3.10，不满足项目要求。因此，后续明确使用 Python 3.12 创建环境，并通过 `.venv\Scripts` 下的程序运行项目，不依赖全局 Python。

## 3. 首次安装

已完成安装的当前工作区可直接跳到“启动项目”，无需每次重新安装。

### 3.1 安装后端依赖

在项目根目录依次执行，确认每条命令成功后再执行下一条：

```powershell
Set-Location D:\codex_base\DeepTutor
uv venv --python 3.12 .venv
uv pip install --python .venv/Scripts/python.exe -e .
```

如果尚未安装 Python 3.12，可先执行：

```powershell
uv python install 3.12
```

如果未使用 uv，但已通过 Windows Python Launcher 安装 Python 3.12，可使用：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
```

`-e .` 表示使用当前源码进行可编辑安装。默认依赖涵盖完整应用；Manim、部分文档解析引擎和伙伴渠道 SDK 等可选功能，需按需安装 [项目依赖声明](../pyproject.toml) 中对应的 extras。

### 3.2 安装前端依赖

```powershell
Set-Location D:\codex_base\DeepTutor\web
npm.cmd ci --no-audit --no-fund
Set-Location D:\codex_base\DeepTutor
```

`npm ci` 按 [前端锁文件](../web/package-lock.json) 安装依赖。安装过程中可能出现第三方包弃用提示；是否安装成功应以命令退出结果为准。

使用 `npm.cmd` 可以避免 PowerShell 对 `npm.ps1` 的执行策略限制。

## 4. 启动项目

### 4.1 后台开发模式（本次使用）

```powershell
Set-Location D:\codex_base\DeepTutor
.\.venv\Scripts\deeptutor.exe start --home D:/codex_base/DeepTutor --dev --detach --no-browser
```

| 参数 | 含义 |
| --- | --- |
| `start` | 统一启动后端与前端 |
| `--home` | 指定运行工作目录，数据保存在其下的 `data` 目录 |
| `--dev` | 使用 Next.js 开发服务器，适合本地体验和前端开发 |
| `--detach` | 后台运行，脱离当前控制台 |
| `--no-browser` | 启动后不自动打开浏览器 |

看到“starting in the background”仅表示后台启动器已经创建。应继续检查日志和 HTTP 响应，确认前后端都已就绪。

启动完成后访问：<http://localhost:3782/chat>。

首次访问需要编译页面，本次首次编译耗时数十秒，后续访问明显更快。

### 4.2 前台开发模式

需要直接查看输出时，省略 `--detach`：

```powershell
.\.venv\Scripts\deeptutor.exe start --home D:/codex_base/DeepTutor --dev --no-browser
```

保持终端打开，使用 `Ctrl+C` 停止。切换运行模式前，先停止已启动的实例。

### 4.3 生产构建模式

省略 `--dev` 后，源码启动器会准备并使用前端生产构建：

```powershell
.\.venv\Scripts\deeptutor.exe start --home D:/codex_base/DeepTutor --detach --no-browser
```

首次构建比开发模式启动更耗时。本次仅验证了开发模式；生产构建模式未在本次启动过程中验证。

## 5. 验证运行状态

### 查看日志

```powershell
Get-Content .\data\user\runtime\launcher.log -Tail 60
```

实时跟踪日志：

```powershell
Get-Content .\data\user\runtime\launcher.log -Tail 30 -Wait
```

对日志跟踪命令按 `Ctrl+C` 只会退出跟踪，不会停止后台服务。

正常启动时，日志包含 `Backend is ready.` 和 `Frontend is ready.`。

### 检查 HTTP 响应

```powershell
Invoke-RestMethod -Uri http://127.0.0.1:8001/health/ready -TimeoutSec 15
(Invoke-WebRequest -Uri http://localhost:3782/chat -UseBasicParsing -TimeoutSec 120).StatusCode
```

预期后端返回 `status: ready`，前端返回 `200`。这说明应用服务已运行，不代表模型凭据或所有可选功能均已配置。

## 6. 首次模型配置

本次启动时没有配置活动 LLM 模型，因此日志出现：

```text
No active LLM model is configured. Please set it in Settings > Catalog.
```

这不会阻止 Web 界面启动，但使用 AI 对话前需要完成配置：

当前界面已将模型配置拆分为 **Providers** 和 **Language models**；错误中的 `Catalog` 是旧称，不是当前侧栏中的独立入口。

1. 打开 [Settings → Providers](http://localhost:3782/settings/connections)，按供应商要求配置连接、API 地址及认证信息。
2. 打开 [Settings → Language models](http://localhost:3782/settings/llm)，添加或选择该连接下的模型。
3. 保存配置，并选择用于对话的活动模型；如果界面提示应用变更，请完成应用。
4. 发送一条简单问题，验证模型调用是否成功。

知识库、语音、图像或视频生成可能需要另外配置相应模型与服务。启动成功不意味着这些外部服务已经可用。

## 7. 数据、配置与端口

运行数据位于 `--home` 指定目录下：

```text
DeepTutor/
├── .venv/                     Python 虚拟环境
├── web/node_modules/          前端依赖
└── data/user/
    ├── settings/              运行配置
    │   └── system.json        包括前后端端口等设置
    ├── runtime/
    │   └── launcher.log       后台启动日志
    └── logs/                  应用日志
```

运行配置主要保存在 `data/user/settings/`。项目运行时不会自动加载根目录 `.env`；不要依赖修改 `.env` 来配置本次源码启动。

`--home` 应指向项目根目录，不要设为项目下的 `data` 或 `data/user`。

默认端口是后端 `8001`、前端 `3782`。需要调整时，先停止服务，再修改 `data/user/settings/system.json` 中的 `backend_port`、`frontend_port`，保留其他设置，然后重新启动。如果配置过显式 API 地址，也应检查其是否仍匹配新的后端地址。以启动日志显示的最终地址为准。

本指南提供本机访问地址，但本次后端日志显示监听 `0.0.0.0:8001`；前端也显示网络地址。能否被其他设备访问取决于网络和防火墙配置，不能将 localhost 链接理解为仅绑定本机。

## 8. 停止与重新启动

停止通过 `--detach` 启动的前后端：

```powershell
Set-Location D:\codex_base\DeepTutor
.\.venv\Scripts\deeptutor.exe stop --home D:/codex_base/DeepTutor
```

重新启动时，确认停止命令成功，再执行：

```powershell
.\.venv\Scripts\deeptutor.exe start --home D:/codex_base/DeepTutor --dev --detach --no-browser
```

停止服务不会删除学习数据或配置。不要通过删除 `data` 目录来处理普通启动故障。

## 9. 常见问题

| 现象 | 处理方法 |
| --- | --- |
| 默认 Python 是 3.10 | 使用 `uv venv --python 3.12 .venv`，通过虚拟环境中的程序运行 |
| 找不到 `deeptutor` 命令 | 使用 `.\.venv\Scripts\deeptutor.exe`，无需激活环境 |
| PowerShell 禁止执行 `npm.ps1` | 改用 `npm.cmd` |
| uv 提示不能创建硬链接，回退为复制 | 本次跨磁盘安装也出现此提示；若最终安装成功，无需处理 |
| 下载依赖失败 | 检查网络、代理和包仓库访问情况，保留错误输出后重试对应安装步骤 |
| 日志或缓存提示 Permission denied | 确认当前用户对相应目录有权限；若在受限代理沙箱内执行，需要批准沙箱外安装或启动 |
| 页面首次访问超时 | 查看是否正在 `Compiling /chat`，等待编译完成后重试；若出现编译错误，按日志排查 |
| 后端 ready，但对话不可用 | 在 Settings → Providers 配置连接，在 Language models 配置活动模型，并检查认证信息和模型服务连接 |
| 端口已被占用 | 确认是否已有本项目实例；优先用 `deeptutor stop` 停止自己的实例，或修改端口 |
| 关闭终端后还在运行 | `--detach` 的预期行为，使用上述停止命令关闭 |

排查端口占用：

```powershell
Get-NetTCPConnection -State Listen -LocalPort 8001,3782 -ErrorAction SilentlyContinue |
    Select-Object LocalAddress,LocalPort,OwningProcess
```

本次在代理沙箱内发生过缓存访问、日志写入和本机 HTTP 连接受限，批准沙箱外执行后完成安装与验证。这是当时执行环境的限制，不表示普通 PowerShell 启动必须使用管理员权限。

## 10. 使用本地代理进行 Codex 认证与对话

DeepTutor 支持连接已经运行的本地代理软件，不会创建 VPN 隧道或启动代理服务。

1. 确认代理软件已启动，并取得实际监听地址及协议。
2. 打开 **Settings → Network → Codex network proxy**。
3. 在 **Proxy URL** 填写地址，例如 `http://127.0.0.1:7890` 或 `socks5://127.0.0.1:1080`。这些只是格式示例，必须替换为实际地址。
4. 点击 **Apply changes**，然后按本指南停止并重新启动 DeepTutor。
5. 返回 **Settings → Providers**，重新发起 Codex 登录；之前失败的授权码不应重复使用。

代理覆盖 Codex 的授权码换令牌、令牌刷新与撤销、模型列表、客户端版本查询，以及对话推理。浏览器打开授权页面仍使用浏览器自身的网络设置，本地 OAuth 回调也不会经过该代理；其他模型供应商的请求不受此设置影响。

支持 `http://`、`https://` 和 `socks5://`，要求包含主机及端口，不支持 URL 内携带用户名、密码、查询参数或额外路径。HTTPS 代理指代理服务器本身使用 TLS；多数本地 HTTP/Mixed 端口应填写 `http://`，它仍能通过 CONNECT 转发 HTTPS 请求。TLS 证书校验不会因配置代理而关闭。

设置保存在 `data/user/settings/system.json` 的 `codex_proxy_url` 字段。留空时沿用 HTTPX 的环境代理行为；如需恢复原来的访问方式，清空字段、应用更改并重启即可。在容器中，`127.0.0.1` 指容器自身，应使用容器能够访问到的代理地址。

代理连通不代表账号授权成功；如果出口仍返回 `unsupported_country_region_territory`，则 OpenAI 不支持该出口所在地区。代理停止或不可达时，Codex 请求会失败，不会自动切换为直连。

## 11. 相关源码与说明

- [对话请求与 LLM 转接原理](./CHAT_REQUEST_FLOW.md)
- [状态建模与流转分析](./STATE_MODELING.md)
- [Python 依赖与可选功能](../pyproject.toml)
- [前端依赖与脚本](../web/package.json)
- [CLI 启动和停止命令](../deeptutor_cli/main.py)
- [前后端启动器](../deeptutor/runtime/launcher.py)
- [工作目录解析](../deeptutor/runtime/home.py)
- [运行配置及默认端口](../deeptutor/services/config/runtime_settings.py)
- [容器化说明](./CONTAINERIZATION.md)
