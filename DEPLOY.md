# 分发 / 服务端部署指南（给别人用）

目标：你（运营方）控制云机矩阵，把账号发给别人用，**7 天到期自动失效**，且**核心代码不外泄**。

## 架构：你的电脑 = 服务器，客户用浏览器
```
客户浏览器 ──(公网隧道)──► 你的电脑:8010 ──(网络 adb)──► 云机 6001-6020
   (只有界面+令牌)            后端+编排器+adb            Agent App
```
- 客户**什么都不用装**（不发 APK、不发后端代码 → 天然防反编译/#5）；只用浏览器开你的地址，输入你给的令牌。
- 7 天有效期由**你的服务器端**校验（不是客户本地），到期即停 —— 真授权。

## 运营方（你）要做的
1. **跑起服务**：双击桌面「图文矩阵营销平台」即可——它自带后端(serve UI + API) + 编排器(自动 `adb connect` 云机) + adb。
   - 云机地址默认 `192.168.1.200:6001-6020`，改用环境变量 `MATRIX_ADB_ENDPOINTS`（逗号分隔，支持 `host:start-end` 段）。
2. **公网入口（隧道）**：家用电脑没公网 IP，用 frp / cloudflared / ngrok 把本机 `:8010` 暴露成一个公网地址。
   - 例：`cloudflared tunnel --url http://127.0.0.1:8010` → 得到一个 https 地址发给客户。
3. **发令牌**：在「令牌管理」里新建 operator 令牌，设 **7 天有效期**；把【公网地址 + 令牌】发给客户。到期后该令牌自动失效（可在令牌管理续期）。

## 客户要做的
- 浏览器打开你的公网地址 → 输入令牌 → 正常用全部功能（内容库/发布/效果榜…）。
- **远控看屏（Escrcpy）另算**：Escrcpy 也是走 adb，客户的电脑要能网络访问到云机盒子 `192.168.1.200`（同内网 / 你给他 VPN / 云机厂商自带的远程画面）。仅用我们的发布功能则不需要 Escrcpy。

## 稳定隧道（已配好，2026-06-30）
- 域名 **ouyipu.xyz**（Cloudflare 免费版）。cloudflared 在 `D:\devtools\cloudflared.exe`，已 `tunnel login`（cert 在 `~/.cloudflared/cert.pem`）。
- 命名隧道 **matrix**（id `223d36e2-2e95-49e8-b053-c77663df2041`），CNAME **matrix.ouyipu.xyz → 该隧道**，配置 `~/.cloudflared/config.yml`（ingress: matrix.ouyipu.xyz → http://127.0.0.1:8010）。
- 跑起来：`cloudflared tunnel run matrix`（已在跑）。客户访问 **https://matrix.ouyipu.xyz** + 令牌。
- **前置：域名 NS 必须指向 Cloudflare（状态=活动）**，否则 matrix.ouyipu.xyz 不解析。在注册商处把 NS 改成 CF 分配的两个。
- **开机自启**（可靠）：管理员 `cloudflared service install` 装成 Windows 服务（读同一 config.yml）。

## 仍待加固（按需）
- **Agent APK 混淆**：客户拿不到 APK（只在云机上），但若有人从手机里拖出来，可开 R8/ProGuard 混淆（需配 accessibility/IME/反射 的 keep 规则）。
- 数据目录迁到 `%APPDATA%`、安装时播种 `.env`、代码签名（免 SmartScreen）、Mac 版走 CI——见 [桌面打包记忆]。

## 注意
- 桌面 App 与「单独跑后端」二选一占用 `:8010`，别同时开。
- 网络 adb 重启会掉，编排器已会按 `MATRIX_ADB_ENDPOINTS` 自动重连。
