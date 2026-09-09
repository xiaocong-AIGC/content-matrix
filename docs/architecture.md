# 架构设计

## 产品边界

系统不是网页自动发布器。Web 只负责任务编排和可观测性；真正的发布执行发生在安装了 Agent 的 Android 手机上，由 AccessibilityService 驱动真实抖音 App。

## 三端职责

### Web 前端

- 创建发布任务，填写内容、话题和执行策略。
- 查看任务队列、当前步骤、设备、日志和截图。
- 对等待确认的任务执行人工确认或取消。

### 后端服务

- 持久化任务、设备、日志、截图。
- 通过租约安全地把任务交给设备，租约过期后可重新调度。
- 校验状态流转，防止旧设备或重复请求覆盖新状态。
- 接收设备心跳、能力信息和执行遥测。

### Android Agent

- 注册设备并维持心跳。
- 主动领取任务，续租并上报执行步骤。
- 通过页面识别器判断抖音页面，通过动作层点击、输入、返回和滑动。
- 遇到验证码、安全验证、页面未知或最终发布确认时停机并请求人工处理。

## 数据模型

### PublishTask

任务保存业务内容和调度状态。内容字段包括发布类型、封面标题、发布标题、正文、话题、媒体引用和发布策略；调度字段包括优先级、计划时间、当前状态、当前步骤、设备、租约令牌、租约过期时间和错误摘要。

### Device

设备保存稳定设备编码、显示名、Agent/Android/抖音版本、能力集合、在线状态、最近心跳、当前任务和认证令牌摘要。生产环境应把设备令牌存为哈希。

### ExecutionLog

日志是不可变事件流，记录任务、设备、级别、步骤、消息、结构化上下文和时间。任务状态变化也写入日志，便于追踪。

### Screenshot

截图独立保存文件元数据、所属任务/设备、执行步骤、尺寸和创建时间。二进制文件放对象存储或本地 storage，数据库只保存引用。

## 调度策略

设备主动调用 claim 接口。后端选择可执行任务并原子写入设备、租约令牌和过期时间。后续状态、日志和截图请求必须携带租约令牌。设备离线或租约超时后，调度器可将非终态任务放回队列；涉及已触发发布按钮的任务不能自动重试。

## 状态机

```text
queued -> leased -> running -> waiting_confirmation -> succeeded
                   |          |                    |
                   +----------+--------------------+-> failed
queued/leased/waiting_confirmation -> cancelled
```

执行步骤建议：

```text
claimed
launching_douyin
opening_publish_entry
selecting_publish_type
selecting_media
editing_cover
filling_title
filling_body
adding_topics
reviewing
waiting_confirmation
publishing
verifying_result
completed
```

## 分阶段实现

1. 当前：Web、后端、SQLite、模拟 Agent、状态和日志闭环。
2. Android：设备注册、心跳、WorkManager、AccessibilityService、MediaProjection 截图和页面识别。
3. 抖音流程：基于真实版本采集页面特征，逐步骤实现并建立版本适配规则。
4. 生产强化：设备鉴权、对象存储、WebSocket/SSE、租约回收、审计、限流和人工接管。

