# 多机保活工具

接「主板多机」时，用这套保证每台手机的 Agent 一直在线。

## 1. 手机端：开机自启 + 后台保活（每台手机做一次）
1. 打开 Agent App，填好后端地址，点「保存配置并启动 Agent」（这会记录"已启动"，重启后自动恢复）。
2. 点「保持后台运行（电池白名单）」，同意。
3. **手动**到 系统设置 → 应用 → 抖音发布 Agent → **自启动** 打开（MIUI 的自启动开关无法由程序代开）。
4. 之后手机重启会自动把 Agent 拉起来重新上线（无障碍授权重启后仍在；录屏授权重启后失效，下次 App 被前置时自动重新授权——看门狗会前置 App）。

## 2. PC/Mac 端：看门狗（监控所有手机，掉了自动拉起）
只需 `adb` 在 PATH 上，Windows / macOS 通用。

```bash
# USB 接法（默认）：自动重连 + 每轮重设 adb reverse + 进程死了就拉起
python tools/agent_watchdog.py

# 局域网接法（手机填服务器 IP）：不需要 adb reverse
python tools/agent_watchdog.py --lan

# 只看指定手机
python tools/agent_watchdog.py --serials 6127f7f6,abc123

# 自定义端口 / 检查间隔
python tools/agent_watchdog.py --port 8010 --interval 30
```

输出示例：
```
[18:17:07] 6127f7f6 DOWN -> relaunch
[18:17:10] 6127f7f6 OK
```

> 已实测：force-stop 掉 App 后，看门狗在下一轮自动把它拉起并恢复 OK。

## 规模化建议
- **USB 多机**：一台 PC + USB hub 接多机，跑一个 watchdog 即可（自动遍历所有 `adb devices`）。注意 hub 供电稳定，避免设备掉线。
- **局域网多机（更推荐）**：手机 Agent 填服务器局域网 IP，watchdog 用 `--lan`；省去 adb reverse，扩展更干净。
- 想开机自动跑 watchdog：Windows 用「任务计划程序」，Mac 用 `launchd`/`crontab @reboot`。
