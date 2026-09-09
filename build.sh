#!/usr/bin/env bash
# ============================================================================
# 发布室 一键打包（在 git-bash 里跑：./build.sh [web|backend|app|all]）
#
# 产物：
#   frontend/dist-web  浏览器版前端（API 相对路径）→ 内嵌进 backend.exe
#   frontend/dist      桌面版前端（API 写死 127.0.0.1:8010）→ 打进 app.exe
#   D:/cmbuild/release/backend.exe   后端 + 浏览器版控制台
#   D:/cmbuild/release/app.exe       桌面应用
#
# ⚠️ 两份前端必须分开输出，绝不能共用 dist：曾经把桌面版前端打进 backend.exe，
#    同事用浏览器访问时 JS 去连自己电脑的 8010 → “后端连接失败”。
# ============================================================================
set -e
ROOT="/d/抖音自动发布agent"
FE="$ROOT/frontend"
BE="$ROOT/backend"
OUT="D:/cmbuild"
TARGET="${1:-all}"

# 这台机器的工具链（node/rust/mingw 都装在 D:，不在系统 PATH 里）
export PATH="/d/nodejs:$FE/node_modules/.bin:/d/devtools/winlibs/mingw64/bin:/d/devtools/cargo/bin:$PATH"
export RUSTUP_HOME="D:\\devtools\\rustup"
export CARGO_HOME="D:\\devtools\\cargo"
export CARGO_NET_GIT_FETCH_WITH_CLI=true
export CARGO_TARGET_DIR="$OUT"

step() { echo ""; echo "===== $* ====="; }

build_front() {
  step "前端类型检查"
  (cd "$FE" && tsc -b)
  step "前端·浏览器版 → dist-web（相对地址）"
  (cd "$FE" && vite build)
  step "前端·桌面版 → dist（绝对地址 127.0.0.1:8010）"
  (cd "$FE" && vite build --mode tauri)
  # 兜底校验，防止再次搞混
  if grep -qoE 'http://127\.0\.0\.1:8010' "$FE"/dist-web/assets/index-*.js 2>/dev/null; then
    echo "!! dist-web 里出现了写死地址，构建配置被改坏了"; exit 1
  fi
  echo "校验通过：dist-web=相对路径, dist=绝对地址"
}

build_backend() {
  step "冻结 backend.exe（内嵌 dist-web）"
  (cd "$BE" && ./.venv/Scripts/pyinstaller.exe --onefile --name backend \
    --collect-all uvicorn --collect-all sqlmodel --collect-all sqlalchemy \
    --collect-all pydantic --collect-all pydantic_settings \
    --collect-submodules app \
    --hidden-import python_multipart --hidden-import email_validator \
    --add-data "D:/抖音自动发布agent/frontend/dist-web;frontend/dist" \
    --distpath "$OUT/sidecar" --workpath "$OUT/sidecar/build" -y run_server.py)
  cp -f "$OUT/sidecar/backend.exe" "$FE/src-tauri/binaries/backend-x86_64-pc-windows-gnu.exe"
  cp -f "$OUT/sidecar/backend.exe" "$FE/src-tauri/binaries/backend-x86_64-pc-windows-msvc.exe"
  cp -f "$OUT/sidecar/backend.exe" "$OUT/release/backend.exe"
  echo "backend.exe 就绪"
}

build_tools() {
  # orchestrator / autoprovision 以前是手工 pyinstaller 出来的，不在这个脚本里 →
  # 改了 tools/ 却只跑 ./build.sh，打出来的 app.exe 里还是旧的装机程序。
  step "冻结 orchestrator.exe / autoprovision.exe"
  for t in orchestrator:agent_orchestrator autoprovision:auto_provision; do
    name="${t%%:*}"; src="${t##*:}"
    (cd "$BE" && ./.venv/Scripts/pyinstaller.exe --onefile --name "$name" \
      --paths "$ROOT/tools" --distpath "$OUT/release" \
      --workpath "$OUT/toolbuild" -y "$ROOT/tools/$src.py" >/dev/null)
    for triple in gnu msvc; do
      cp -f "$OUT/release/$name.exe" \
        "$FE/src-tauri/binaries/$name-x86_64-pc-windows-$triple.exe"
    done
    echo "$name.exe 就绪"
  done
}

build_app() {
  step "打包 app.exe（前端已构建，跳过 beforeBuildCommand）"
  # 关掉正在运行的实例，否则 release/ 下的文件被占用会报 os error 5
  /c/Windows/System32/taskkill.exe //F //IM app.exe //IM adb.exe >/dev/null 2>&1 || true
  sleep 2
  (cd "$FE" && tauri build --no-bundle 2>&1 | tail -20)
  echo "app.exe: $(ls -la "$OUT/release/app.exe" | awk '{print $5" bytes  "$6" "$7" "$8}')"
}

case "$TARGET" in
  web)     build_front ;;
  backend) build_front; build_backend ;;
  tools)   build_tools ;;
  app)     build_app ;;
  all)     build_front; build_backend; build_tools; build_app ;;
  *) echo "用法: ./build.sh [web|backend|tools|app|all]"; exit 1 ;;
esac
step "完成"
