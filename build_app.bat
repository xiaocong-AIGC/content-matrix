@echo off
REM ===================================================================
REM  打包桌面版 app.exe
REM  为什么要在这里重设 PATH：本机的“用户 PATH”注册表值被写坏了
REM  （条目之间用空格而不是分号分隔、还混进了字面量 %%Path%%），
REM  导致 cmd 里找不到 npm/node/cargo，tauri 的 beforeBuildCommand 必挂。
REM  这里显式给一份干净的 PATH，不依赖系统 PATH。
REM ===================================================================
set "PATH=D:\nodejs;D:\devtools\cargo\bin;D:\devtools\winlibs\mingw64\bin;C:\Windows\system32;C:\Windows;C:\Windows\System32\Wbem"
set "RUSTUP_HOME=D:\devtools\rustup"
set "CARGO_HOME=D:\devtools\cargo"
set "CARGO_TARGET_DIR=D:\cmbuild"
set "CARGO_NET_GIT_FETCH_WITH_CLI=true"

cd /d "D:\抖音自动发布agent\frontend"

echo [1/2] 关闭正在运行的实例（否则 release 下文件被占用会报 os error 5）
taskkill /F /IM app.exe >nul 2>&1
taskkill /F /IM adb.exe >nul 2>&1
timeout /t 2 /nobreak >nul

echo [2/2] tauri build
call npx --no-install tauri build --no-bundle
echo EXITCODE=%errorlevel%
