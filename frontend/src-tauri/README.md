# 桌面版打包（Tauri）

把控制台前端打包成 **Windows / macOS 桌面应用**。UI 仍是同一套 React/Vite 代码，
Tauri 只是把它装进一个原生窗口（Windows 用 WebView2，macOS 用 WKWebView）。

## 架构

- 桌面 App 内置打包好的前端（`frontend/dist`），窗口加载它。
- 前端通过**绝对地址** `http://127.0.0.1:8010` 访问后端（开发态走 Vite 代理，打包态没有代理，
  所以用 `.env.tauri` 里的 `VITE_API_ORIGIN`）。
- 后端（FastAPI/uvicorn）+ 编排器（adb）目前仍**单独运行**，桌面 App 是它们的前台界面。
  > 后续可把后端冻结成 sidecar 由 App 自启（见末尾「下一步」）。
- 后端 CORS 已放行 webview 来源（`http://tauri.localhost` 等，见 `backend/.env` 的 `FRONTEND_ORIGIN`）。

## 一次性环境（本机已装好）

- **Rust**：用 GNU 工具链 `stable-x86_64-pc-windows-gnu`（自带链接器，**无需** VS C++ Build Tools）。
  `rustup default stable-x86_64-pc-windows-gnu`
- **WebView2 运行时**：Win10/11 一般自带；缺则装 Evergreen Runtime。
- **Node**：构建前端用。
- 国内网络：`~/.cargo/config.toml` 已配 rsproxy 镜像，否则 crates.io 会超时。

> macOS 的 `.dmg` **不能在 Windows 上产出**，需在 Mac 上 `npm run tauri build`，或用 CI（GitHub Actions `tauri-apps/tauri-action`，矩阵 win+mac）。

## 构建命令（在 `frontend/` 下）

```bash
# 开发：原生窗口热重载（会自动起 vite dev）
npm run tauri dev

# 打包 Windows 安装包（NSIS .exe）
npm run tauri build
```

产物：
- 安装包：`frontend/src-tauri/target/release/bundle/nsis/*.exe`
- 免安装可执行：`frontend/src-tauri/target/release/图文矩阵营销平台.exe`

## 运行（一键，已实现）

**双击 App 即可** —— 它会自动拉起 backend + orchestrator + auto-provision，并用**随包内置的 adb**
（`platform-tools/`）；关窗时全部回收。无需手动起任何服务。

- 三个 sidecar 由 PyInstaller 冻结：`backend.exe`（uvicorn，读 cwd 的 `.env`+`data/agent.db`）、
  `orchestrator.exe`、`autoprovision.exe`。源在 `binaries/`（每个需 `-gnu` 和 `-msvc` 两个命名）。
- adb + 4 个依赖文件 + 安装用 APK 通过 `bundle.resources` 打进 `platform-tools/`，
  Rust 启动时把它加到 orchestrator/auto-provision 的 PATH。
- 数据目录：`MATRIX_HOME` 环境变量覆盖，默认指向本机 backend 目录（分发版应改 `app_data_dir`）。

## 下一步（面向分发）

1. 数据目录改 `app_data_dir`，安装时播种 `.env`（含密钥让用户填）。
2. macOS `.dmg`：在 Mac 或 CI（`tauri-apps/tauri-action`）上打。
3. 代码签名（避免 SmartScreen 拦截）。
4. auto-provision 加设备白名单（现在会给任何连上的未装机设备装 Agent）。
