import {
  AlertTriangle,
  Hash,
  Image,
  LockKeyhole,
  LoaderCircle,
  RefreshCw,
  Smartphone,
  Terminal,
} from "lucide-react";
import { useState } from "react";
import { createPortal } from "react-dom";

import { fileUrl, type TaskActionKind } from "../lib/api";
import type { PublishTask } from "../lib/types";
import { Pager, usePaged } from "./Pager";
import { StatusBadge } from "./StatusBadge";

const stepLabels: Record<string, string> = {
  queued: "排队等待设备",
  claimed: "设备已接手",
  launching_douyin: "启动应用",
  opening_publish_entry: "进入发布入口",
  selecting_publish_type: "选择发布类型",
  selecting_media: "选择发布素材",
  composing_text: "输入文字内容",
  selecting_template: "套用文字模板",
  editing_cover: "编辑封面",
  editing_content: "编辑标题与正文",
  filling_title: "填写发布标题",
  filling_body: "填写正文",
  adding_topics: "添加话题",
  reviewing: "发布前检查",
  page_recognition: "识别当前页面",
  security_challenge: "安全验证，需人工介入",
  waiting_confirmation: "需人工介入",
  publishing: "正在发布",
  verifying_result: "验证发布结果",
  unknown_page: "未识别页面",
  completed: "执行完成",
  cancelled: "任务已取消",
  group_message: "群发消息",
};

function formatDate(value: string) {
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(new Date(value));
}

function formatDateTime(value: string) {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(value));
}

const shotUrl = (id: number) => fileUrl(`/api/v1/agent/screenshots/${id}/file`);

interface Props {
  task?: PublishTask;
  onAction: (id: number, action: TaskActionKind) => Promise<void>;
}

export function TaskDetail({ task, onAction }: Props) {
  const [actionPending, setActionPending] = useState<TaskActionKind>();
  // 点击截图放大：桌面版(Tauri)没有浏览器标签页，<a target=_blank> 点了没反应，
  // 改成应用内灯箱。
  const [lightbox, setLightbox] = useState<string | null>(null);
  const allLogs = task?.logs ? [...task.logs].reverse() : [];
  const logsPaged = usePaged(allLogs, 10);

  if (!task) {
    return (
      <section className="detail-placeholder">
        <span>任务详情</span>
        <strong>选择一条任务</strong>
        <p>任务内容、执行日志和手机截图会显示在这里。</p>
      </section>
    );
  }

  const isGroup = task.publish_type === "group_message";
  const shots = task.screenshots ?? [];
  const coverShot =
    shots.find((s) => s.step === "selecting_template") ??
    shots.find((s) => s.step === "published") ??
    shots.find((s) => s.step === "composing_text") ??
    shots[0];
  const shotsTitle =
    task.status === "succeeded"
      ? "发布成功截图"
      : isGroup
        ? "群发截图"
        : "执行截图";
  const taskId = task.id;
  const publishTime = task.finished_at ?? task.scheduled_at ?? null;
  const inProgress = !["succeeded", "failed", "cancelled"].includes(task.status);

  async function performAction(action: TaskActionKind) {
    setActionPending(action);
    try {
      await onAction(taskId, action);
    } finally {
      setActionPending(undefined);
    }
  }

  return (
    <div className="detail-stack">
      <section className="manuscript-sheet">
        <header className="manuscript-head">
          <div>
            <span>任务 #{String(task.id).padStart(4, "0")}</span>
            <h2>{task.name}</h2>
            {publishTime ? (
              <small className="manuscript-time">
                {task.finished_at ? "发布时间" : "计划时间"}{" "}
                {formatDateTime(publishTime)}
              </small>
            ) : null}
          </div>
          <div className="manuscript-actions">
            <StatusBadge status={task.status} />
          </div>
        </header>

        <div className="manuscript-progress">
          <div className="progress-copy">
            <span>
              <i />
              {stepLabels[task.current_step] ?? task.current_step}
            </span>
            <strong>
              {inProgress
                ? `${String(task.progress).padStart(2, "0")}%`
                : task.status === "succeeded"
                  ? "已完成"
                  : task.status === "failed"
                    ? "执行失败"
                    : "已取消"}
            </strong>
          </div>
          {inProgress ? (
            <div className="progress-track">
              <i style={{ width: `${task.progress}%` }} />
            </div>
          ) : null}
        </div>

        <div className="proof-layout">
          <div className="cover-proof">
            <span className="proof-label">
              {isGroup ? "群发截图 · 来自手机" : "文字卡片 · 来自手机"}
            </span>
            {coverShot ? (
              <button
                type="button"
                className="cover-shot"
                onClick={() => setLightbox(shotUrl(coverShot.id))}
              >
                <img src={shotUrl(coverShot.id)} alt="发布结果截图" />
              </button>
            ) : (
              <div className="cover-canvas">
                <Image size={26} strokeWidth={1.2} />
                <strong>
                  {isGroup
                    ? "等待群发截图"
                    : "等待文字卡片截图"}
                </strong>
                <i>关键步骤的截图会自动出现在这里</i>
              </div>
            )}
          </div>

          {isGroup ? (
            <div className="copy-proof">
              <span className="proof-label">消息文字</span>
              <p>{task.body || "尚未填写消息文字"}</p>
              <span className="proof-label body-label">发往群</span>
              <div className="topic-list">
                {(task.target_groups ?? []).length ? (
                  (task.target_groups ?? []).map((g) => (
                    <span key={g}>{g}</span>
                  ))
                ) : (
                  <span className="muted">未记录群名</span>
                )}
              </div>
              <span className="proof-label body-label">@所有人</span>
              <p>{task.mention_all ? "是（已尝试 @所有人）" : "否"}</p>
            </div>
          ) : (
            <div className="copy-proof">
              <span className="proof-label">发布标题</span>
              <h3>{task.publish_title || "尚未设置发布标题"}</h3>
              <span className="proof-label body-label">正文内容</span>
              <p>{task.body || "尚未填写正文内容"}</p>
              <div className="topic-list">
                {task.topics.length ? (
                  task.topics.map((topic) => (
                    <span key={topic}>
                      <Hash size={12} />
                      {topic}
                    </span>
                  ))
                ) : (
                  <span className="muted">未设置话题</span>
                )}
              </div>
            </div>
          )}
        </div>

        <div className="policy-ribbon">
          <div className="policy-icon">
            <LockKeyhole size={18} />
          </div>
          <div>
            <span>执行策略</span>
            <strong>Agent 自动完成{isGroup ? "群发" : "发布"}</strong>
          </div>
          <p>遇到验证码、安全验证或未识别页面时自动停止并转交人工处理。</p>
        </div>

        {task.status === "failed" ? (
          <div className="failure-box">
            <div className="failure-text">
              <AlertTriangle size={16} />
              <div>
                <strong>执行失败</strong>
                <p>{task.error_message || "未记录具体原因，可重发再试。"}</p>
              </div>
            </div>
            <div className="failure-actions">
              <button
                className="button primary"
                disabled={Boolean(actionPending)}
                onClick={() => void performAction("retry")}
              >
                {actionPending === "retry" ? (
                  <LoaderCircle size={15} className="spinning" />
                ) : (
                  <RefreshCw size={15} />
                )}
                {actionPending === "retry" ? "正在重发" : "重发"}
              </button>
              <button
                className="button secondary"
                disabled={Boolean(actionPending)}
                onClick={() => void performAction("cancel")}
                title="取消后，这篇内容会退回内容库"
              >
                {actionPending === "cancel" ? "正在取消" : "取消"}
              </button>
            </div>
          </div>
        ) : null}

        {inProgress && task.status !== "waiting_confirmation" ? (
          <div className="confirmation-actions">
            <p className="confirmation-hint">
              取消后，这篇内容会退回内容库。
            </p>
            <button
              className="button secondary"
              disabled={Boolean(actionPending)}
              onClick={() => void performAction("cancel")}
            >
              {actionPending === "cancel" ? (
                <LoaderCircle size={15} className="spinning" />
              ) : null}
              {actionPending === "cancel" ? "正在取消" : "取消任务"}
            </button>
          </div>
        ) : null}

        {task.status === "waiting_confirmation" ? (
          <div className="confirmation-actions">
            <p className="confirmation-hint">
              遇到验证码、安全验证或未识别页面，已暂停。
              请在手机上处理后取消这条任务，或稍后重发。
            </p>
            <button
              className="button secondary"
              disabled={Boolean(actionPending)}
              onClick={() => void performAction("cancel")}
            >
              {actionPending === "cancel" ? (
                <LoaderCircle size={15} className="spinning" />
              ) : null}
              {actionPending === "cancel" ? "正在取消" : "取消任务"}
            </button>
          </div>
        ) : null}
      </section>

      <div className="execution-layout">
        <section className="timeline-sheet">
          <header className="subsection-heading">
            <div>
              <Terminal size={17} />
              <span>执行日志</span>
            </div>
            <strong>{allLogs.length} 条</strong>
          </header>

          <div className="timeline">
            {allLogs.length ? (
              logsPaged.slice.map((log, index) => (
                <article
                  key={log.id}
                  className={`timeline-entry ${log.level}`}
                  style={{ animationDelay: `${index * 35}ms` }}
                >
                  <span className="timeline-index">
                    {String(
                      allLogs.length - (logsPaged.page * 10 + index),
                    ).padStart(2, "0")}
                  </span>
                  <i />
                  <div>
                    <header>
                      <strong>{stepLabels[log.step] ?? log.step}</strong>
                      <time>{formatDate(log.created_at)}</time>
                    </header>
                    <p>{log.message}</p>
                  </div>
                </article>
              ))
            ) : (
              <p className="timeline-empty">暂无执行日志</p>
            )}
          </div>
          <Pager
            page={logsPaged.page}
            pageCount={logsPaged.pageCount}
            total={logsPaged.total}
            onChange={logsPaged.setPage}
          />
        </section>

        <section className="phone-sheet">
          <header className="subsection-heading">
            <div>
              <Smartphone size={17} />
              <span>{shotsTitle}</span>
            </div>
            <strong>{shots.length} 张</strong>
          </header>

          <div className="shot-gallery">
            {shots.length ? (
              shots.map((shot) => (
                <button
                  type="button"
                  key={shot.id}
                  className="shot-thumb"
                  onClick={() => setLightbox(shotUrl(shot.id))}
                >
                  <img src={shotUrl(shot.id)} alt={`${stepLabels[shot.step] ?? shot.step} 截图`} />
                  <small>{stepLabels[shot.step] ?? shot.step}</small>
                </button>
              ))
            ) : (
              <div className="phone-empty">
                <Image size={26} strokeWidth={1.2} />
                <strong>暂无截图</strong>
                <span>手机执行到关键步骤时会自动截图</span>
              </div>
            )}
          </div>

          <footer className="device-facts">
            <span>
              <i className={task.device ? "online" : ""} />
              {task.device?.douyin_nickname || task.device?.name || "未分配"}
            </span>
            <span>Agent {task.device?.agent_version ?? "--"}</span>
          </footer>
        </section>
      </div>
      {/* Portal to <body>: the .page-enter ancestor has a transform, which would
          trap a position:fixed overlay inside it (off-center + scrolls with the
          page). Rendering at body level makes it a true viewport overlay. */}
      {lightbox
        ? createPortal(
            <div className="image-lightbox" onClick={() => setLightbox(null)}>
              <img src={lightbox} alt="放大截图" />
              <span className="image-lightbox-cap">点击任意处关闭</span>
            </div>,
            document.body,
          )
        : null}
    </div>
  );
}
