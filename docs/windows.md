# Windows 安装、开发与打包

适用范围：Windows 10/11 x64。Windows ARM64 和 32 位 Windows 尚未适配。

已补齐 Windows 启动和打包代码；本次在 macOS 完成构建、测试与启动检查，尚未在 Windows 实机完成安装验收。当前没有生成可分发的 Windows 安装包，需要先在 Windows 电脑执行下面的打包步骤。

## 1. 普通用户：安装后使用

拿到构建者生成的 `*-setup.exe` 后：

1. 双击安装程序，按向导完成安装。
2. 首次安装保持联网：缺少 WebView2 时，安装程序会下载并安装它。
3. 从开始菜单打开「记忆大师」，按界面提示注册或登录本机账户。
4. 在「模型API」中配置对话模型的 Base URL、API Key 和模型名称，以及 Embedding 模型配置；保存并分别测试连通性。模型服务需要可用的密钥和网络。
5. 添加一张记忆卡，进行对话并确认可引用记忆；关闭应用再打开，确认数据仍在。
6. 应用运行时可按 `Ctrl+P` 打开快捷添加窗口。

安装包内置 Python，不需要用户另外安装 Python、Node.js、Rust 或 pnpm。记忆的本地保存不依赖模型；AI 解析、生成和模型召回需要模型配置。

安装版数据库和日志位于 `%APPDATA%\com.memorymaster.desktop\`：

- `memory_master.db`：本机数据。备份前先退出应用，复制整个数据目录。
- `api.log`：本次启动的后端输出，用于排查问题；每次启动会覆盖。

开发模式的数据位于项目的 `storage\memory_master.db`，与安装版分开，不会自动迁移。不要同时启动多个安装版或同时运行开发版和安装版，它们使用相同的本地 API 端口。

## 2. 构建者：准备 Windows 环境

请在 Windows 电脑操作，不能直接使用 macOS 的 `.app` 或 `.dmg`。

安装：

- Node.js 22 LTS（x64，至少 22.12），附带 npm。
- pnpm 10：在 PowerShell 运行 `npm install -g pnpm@10`。
- Python 3.12（x64）：用于开发和测试，安装时勾选加入 PATH。安装包使用脚本下载的独立 Python，不依赖这份安装。
- Visual Studio 2022 Build Tools，选择「使用 C++ 的桌面开发」，包含 MSVC x64/x86 构建工具和 Windows SDK。
- Rust stable，使用 `x86_64-pc-windows-msvc` 工具链，可通过官方 rustup 安装。
- Microsoft Edge WebView2 Runtime（开发环境需要）。

安装完成后重新打开 PowerShell，检查：

```powershell
node --version
pnpm --version
py -3.12 --version
rustc --version
rustup show active-toolchain
```

Rust 应显示 `x86_64-pc-windows-msvc`。将项目源码复制到 Windows，例如 `C:\projects\memory-master`。不要复制 Mac 的 `node_modules`、`src-tauri\target`、`dist`，也不要把个人 `storage` 数据或密钥文件作为安装材料分发。

```powershell
cd C:\projects\memory-master
pnpm install --frozen-lockfile
```

如果 PowerShell 提示禁止运行 `pnpm.ps1`，可将命令改为 `pnpm.cmd`，不必修改全局执行策略。

## 3. 从源码运行

桌面模式：

```powershell
pnpm desktop:dev
```

首次启动会从 python.org 下载约十余 MB 的 Python 3.12.10 官方 x64 嵌入式运行时，随后启动本地 API、前端和桌面窗口。关闭开发进程时，在原终端按 `Ctrl+C`。

只运行浏览器模式（无需 Rust 编译）：

```powershell
pnpm dev
```

打开 `http://127.0.0.1:3001`。浏览器模式不提供桌面全局快捷键等原生功能。

开发脚本优先查找 Python 3.12，随后查找其他不低于 3.12 的解释器。如果找不到，可指定完整路径：

```powershell
$env:MEMORY_MASTER_PYTHON = 'C:\Users\你的用户名\AppData\Local\Programs\Python\Python312\python.exe'
pnpm desktop:dev
```

## 4. 生成 Windows 安装包

在同一 Windows 项目目录执行：

```powershell
pnpm desktop:build
```

这条命令会自动准备 Python 运行时、构建前端、编译桌面程序并生成 NSIS 安装包。第一次构建需要联网下载 Rust 依赖和安装器工具。Python 运行时保存在 `src-tauri\runtime\python`，后续构建会复用并校验版本和必要模块。

安装包输出目录：

```text
src-tauri\target\release\bundle\nsis\
```

将该目录中的 `*-setup.exe` 提供给 Windows 用户。不要仅分发 `target\release` 中的应用 exe：它缺少配套 Python、后端和提示词资源。当前未配置代码签名，发布给更多用户前应补充签名。

## 5. Windows 验收

源码侧检查：

```powershell
pnpm build
pnpm desktop:prepare
cargo check --manifest-path src-tauri/Cargo.toml
py -3.12 -m unittest discover -s evals -p 'test_*.py'
node evals/test_api_failures.mjs
```

安装包请在没有安装 Python 的 Windows x64 电脑或虚拟机中验收：

- 能完成安装并打开主窗口，无额外 Python 控制台弹出。
- 能注册/登录、创建和编辑记忆；退出重开后数据仍在。
- 对话和 Embedding 连通性测试通过，实际对话正常，记忆引用符合预期。
- `Ctrl+P` 可打开快捷添加窗口，保存后主窗口能看到记忆。
- 窗口拖动、最小化、最大化、关闭正常；100% 和 150% 缩放下界面可用。
- 退出应用后本次启动的后端停止，重新打开仍正常。
- 中文用户名和含空格的安装路径下可正常运行。

这些 Windows 实机项目尚未由本次 macOS 检查代替完成。

## 6. 常见问题

- **提示没有 Python**：开发模式按上文安装或设置 `MEMORY_MASTER_PYTHON`；安装版不应依赖 PATH，请确认使用的是新生成的完整安装包。
- **运行时下载失败**：确认能访问 `https://www.python.org`。若下载解压中断，删除项目内 `src-tauri\runtime` 后重新运行 `pnpm desktop:prepare`。
- **找不到 MSVC/link.exe/Windows SDK**：在 Visual Studio Installer 中补装 C++ 桌面开发组件，并重新打开终端。
- **后端连接失败**：先查看 `%APPDATA%\com.memorymaster.desktop\api.log`；确认端口 8765 没有被另一个开发版或安装版占用。开发前端另占用 3001。
- **页面正常但 AI 失败**：在「模型API」检查密钥、地址、模型名称和网络连接，分别执行连通性测试。
- **快捷键无反应**：检查是否被其他软件占用，并确保桌面应用正在运行。

## 7. 本次已执行的检查

2026-10-18，macOS 环境：

- 前端 TypeScript 检查及 Vite 生产构建通过。
- `cargo check` 通过（macOS 目标，不代表 Windows 编译通过）。
- 44 项 Python 单元测试、7 项前端 API 测试通过。
- 使用临时数据库启动新开发脚本，确认 API 与前端可访问，终止后两个服务均停止。
- 验证缺少 Python 时脚本失败并输出安装指引。
- 核对构建复制的后端脚本及提示词路径、内容与运行时代码一致。

尚未执行：Windows PowerShell 下载脚本、Windows Rust 编译、NSIS 安装包生成、无 Python 环境下的安装及界面验收。应在分发前完成第 5 节检查。
