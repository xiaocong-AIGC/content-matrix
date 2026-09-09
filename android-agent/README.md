# Android Agent

第二阶段手机执行端，负责连接后端并通过无障碍服务驱动真实抖音 App。

## 已实现

- 设备注册与本地令牌保存
- 前台服务常驻、心跳、任务拉取和租约续期
- AccessibilityService 页面识别
- 文本点击、输入、返回和滑动基础动作
- 执行状态与日志回传
- Android 11+ 无障碍截图和上传
- 安全验证、未知页面和最终发布前的人工接管

## 构建

使用 Android Studio 打开本目录，或执行：

```powershell
.\gradlew.bat :app:assembleDebug
```

Debug APK 输出到 `app/build/outputs/apk/debug/app-debug.apk`。

## 真机接入

1. 电脑后端使用 `0.0.0.0:8010` 启动，手机与电脑连接同一局域网。
2. 在 Agent 中填写 `http://<电脑局域网IP>:8010/api/v1`。
3. 点击“保存配置并启动 Agent”。
4. 在系统设置中启用“抖音发布 Agent 无障碍服务”。
5. 在 Web 端创建任务并观察设备、任务日志和截图。

当前选择器只提供跨版本基础识别。素材自动选择、封面编辑和最终自动发布属于第三阶段，需要在目标抖音版本真机采集节点树后校准。
