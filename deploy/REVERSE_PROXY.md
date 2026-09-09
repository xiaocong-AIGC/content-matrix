# 部署与安全加固（P1）

## 安全清单
1. **管理令牌**（已实现）：`ADMIN_TOKEN` 必设，控制台登录用。所有管理 API 需要它。
2. **设备注册密钥**（已实现）：设 `PROVISION_KEY`，则只有带相同密钥 provision 的手机能注册：
   ```
   python tools/provision_device.py --server http://<IP>:8010/api/v1 \
       --serial <序列号> --name 上海01 --provision-key <你的密钥>
   ```
3. **只绑内网 / 防火墙**：后端 `--host 0.0.0.0` 只在内网网卡可达；公网入口一律关闭。这是最关键的一道。
4. **CORS**：生产把 `FRONTEND_ORIGIN` 设成控制台的确切地址（如 `http://192.168.1.50:3010`），不要留 `*`。

## HTTPS（可选，内网通常 HTTP + 防火墙即可）
内网用 IP 时，自签证书 Android 不信任、较麻烦；**有域名**时推荐 Caddy 自动 HTTPS：

`Caddyfile`：
```
console.example.com {
    reverse_proxy 127.0.0.1:3010    # 前端
    handle /api/* {
        reverse_proxy 127.0.0.1:8010 # 后端
    }
}
```
然后手机 Agent 后端地址填 `https://console.example.com/api/v1`。

> 仅内网、无域名时：保持 HTTP，靠「ADMIN_TOKEN + PROVISION_KEY + 防火墙只放内网」三道即可。

## 多 worker / 大规模（与 Postgres 绑定）
设备上百台或要开多 worker（`uvicorn --workers N`）时：
1. 切 `DATABASE_URL` 到 Postgres。
2. 领任务改用 `SELECT … FOR UPDATE SKIP LOCKED`（`session.py` 已有 dialect 分支预留）。
单 worker + SQLite(WAL) 已实测可稳定支撑 40+ 台（见 `tools/stress_test.py`）。
