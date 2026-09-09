import { useCallback, useEffect, useState } from "react";
import { useConfirm } from "./ConfirmModal";
import { Select } from "./Select";
import { usePrompt } from "./FormModal";
import {
  Check,
  Copy,
  KeyRound,
  Plus,
  QrCode,
  Snowflake,
  Sun,
  Trash2,
} from "lucide-react";

import {
  approveRenewal,
  type ConsoleToken,
  createConsoleToken,
  deleteConsoleToken,
  extendConsoleToken,
  freezeConsoleToken,
  getMe,
  getMyRenewal,
  getRenewalInfo,
  type Identity,
  listConsoleTokens,
  listRenewalRequests,
  rejectRenewal,
  type RenewalInfo,
  type RenewalRequest,
  renewalQrUrl,
  renewalScreenshotUrl,
  setRenewalConfig,
  submitRenewal,
} from "../lib/api";

const ROLE_LABEL: Record<string, string> = { admin: "管理员", operator: "运营" };
const STATUS_LABEL: Record<string, string> = {
  active: "生效中",
  frozen: "已冻结",
  expired: "已过期",
};

function fmtDate(iso: string | null): string {
  if (!iso) return "永久";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

export function TokenManagementView() {
  const [me, setMe] = useState<Identity | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      setMe(await getMe());
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "加载失败");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="catalog-page page-enter">
      <header className="catalog-hero history-hero">
        <div>
          <span>账号</span>
          <h2>谁能用，用到什么时候。</h2>
          <p>
            当前账号：<b>{me ? `${me.name} · ${ROLE_LABEL[me.role] ?? me.role}` : "…"}</b>
            {me && !me.auth ? "（本地开发：鉴权已关闭）" : ""}。
            {me?.role === "admin"
              ? "管理员可生成、冻结、延期、删除令牌，并配置续费收款码。"
              : "这里能看到你的账号还能用多久，到期前可以提交续费申请。"}
          </p>
        </div>
        <KeyRound size={72} strokeWidth={0.8} />
      </header>
      {error && <p className="form-error">{error}</p>}
      {me?.role === "admin" ? <AdminPanel /> : me ? <OperatorPanel me={me} /> : null}
    </div>
  );
}

/* ------------------------------ Admin ------------------------------ */
function AdminPanel() {
  const [confirm, confirmUI] = useConfirm();
  const [prompt, promptUI] = usePrompt();
  const [tokens, setTokens] = useState<ConsoleToken[]>([]);
  const [name, setName] = useState("");
  const [role, setRole] = useState("operator");
  const [validDays, setValidDays] = useState(30);
  const [fresh, setFresh] = useState<{ name: string; token: string } | null>(null);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      setTokens(await listConsoleTokens());
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "加载失败");
    }
  }, []);
  useEffect(() => {
    void load();
  }, [load]);

  async function onCreate() {
    if (!name.trim()) return;
    setError("");
    try {
      const created = await createConsoleToken(name.trim(), role, validDays || null);
      setFresh({ name: created.name, token: created.token });
      setName("");
      setCopied(false);
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "创建失败");
    }
  }
  async function act(fn: () => Promise<unknown>) {
    setError("");
    try {
      await fn();
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "操作失败");
    }
  }

  return (
    <>
      {confirmUI}
      {promptUI}
      <section className="token-create">
        <input
          type="text"
          placeholder="令牌名称 / 账号备注（如：运营小李）"
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
        <Select
          value={role}
          options={[
            { value: "operator", label: "运营" },
            { value: "admin", label: "管理员" },
          ]}
          onChange={setRole}
        />
        <label className="token-days">
          有效期
          <input
            type="number"
            min={0}
            max={3650}
            value={validDays}
            onChange={(e) => setValidDays(Math.max(0, Number(e.target.value) || 0))}
          />
          天（0 表示永久）
        </label>
        <button className="button primary" onClick={onCreate} disabled={!name.trim()}>
          <Plus size={16} /> 生成令牌
        </button>
      </section>

      {error && <p className="form-error">{error}</p>}

      {fresh && (
        <div className="token-fresh">
          <div>
            <strong>已生成「{fresh.name}」的令牌</strong>
            <small>请立即复制保存——出于安全，它只显示这一次。</small>
            <code>{fresh.token}</code>
          </div>
          <button
            className="button secondary"
            onClick={() => {
              void navigator.clipboard?.writeText(fresh.token);
              setCopied(true);
            }}
          >
            {copied ? <Check size={15} /> : <Copy size={15} />}
            {copied ? "已复制" : "复制"}
          </button>
        </div>
      )}

      <section className="token-table">
        <header className="token-row token-head">
          <span>名称</span>
          <span>角色</span>
          <span>状态</span>
          <span>到期</span>
          <span>最近使用</span>
          <span>操作</span>
        </header>
        {tokens.length ? (
          tokens.map((t) => (
            <article className="token-row" key={t.id}>
              <span className="token-name">
                {t.name}
                {t.renewal_requested ? <em className="renew-flag">待审核</em> : null}
              </span>
              <span>
                <em className={`role-badge role-${t.role}`}>{ROLE_LABEL[t.role] ?? t.role}</em>
              </span>
              <span>
                <em className={`status-pill st-${t.status}`}>{STATUS_LABEL[t.status]}</em>
              </span>
              <span>
                {fmtDate(t.expires_at)}
                {t.days_left !== null ? (
                  <small className="days-left"> · 剩 {t.days_left} 天</small>
                ) : null}
              </span>
              <span className="muted-cell">{fmtDate(t.last_used_at)}</span>
              <span className="token-actions">
                <button
                  onClick={async () => {
                    const input = await prompt({
                      title: `为「${t.name}」延期`,
                      label: "延期天数",
                      value: "30",
                      confirmText: "延期",
                    });
                    const d = Number(input);
                    if (d > 0) void act(() => extendConsoleToken(t.id, d));
                  }}
                  title="延期"
                >
                  延期
                </button>
                <button
                  onClick={() => void act(() => freezeConsoleToken(t.id, !t.frozen))}
                  title={t.frozen ? "解冻" : "冻结"}
                >
                  {t.frozen ? <Sun size={14} /> : <Snowflake size={14} />}
                </button>
                <button
                  className="danger"
                  onClick={async () => {
                    if (
                      await confirm({
                        title: `删除令牌「${t.name}」？`,
                        detail: "删除后持有该令牌的人立即无法登录，且不可恢复。",
                        confirmText: "删除令牌",
                        danger: true,
                      })
                    ) {
                      void act(() => deleteConsoleToken(t.id));
                    }
                  }}
                  title="删除"
                >
                  <Trash2 size={14} />
                </button>
              </span>
            </article>
          ))
        ) : (
          <div className="token-empty">还没有生成过其他令牌。主令牌始终可以登录。</div>
        )}
      </section>

      <RenewalReviewPanel />
      <RenewalConfigEditor />
    </>
  );
}

function RenewalReviewPanel() {
  const [prompt, promptUI] = usePrompt();
  const [reqs, setReqs] = useState<RenewalRequest[]>([]);
  const [msg, setMsg] = useState("");

  const load = useCallback(async () => {
    try {
      setReqs(await listRenewalRequests("pending"));
    } catch {
      /* ignore */
    }
  }, []);
  useEffect(() => {
    void load();
    const t = window.setInterval(() => void load(), 15000);
    return () => window.clearInterval(t);
  }, [load]);

  async function onApprove(r: RenewalRequest) {
    const input = await prompt({
      title: `为「${r.token_name}」延期`,
      subtitle: "核对到账后再确认",
      label: "延期天数",
      value: "30",
      confirmText: "确认延期",
    });
    const d = Number(input);
    if (!d || d <= 0) return;
    setMsg("");
    try {
      await approveRenewal(r.id, d);
      await load();
    } catch (reason) {
      setMsg(reason instanceof Error ? reason.message : "操作失败");
    }
  }
  async function onReject(r: RenewalRequest) {
    const note = await prompt({
      title: "拒绝这笔续费申请",
      label: "拒绝原因（会展示给该用户）",
      value: "未收到款",
      confirmText: "拒绝",
    });
    if (note === null) return;
    try {
      await rejectRenewal(r.id, note);
      await load();
    } catch (reason) {
      setMsg(reason instanceof Error ? reason.message : "操作失败");
    }
  }

  return (
    <section className="renewal-review">
      <header>
        待审核续费申请
        <small>请先在你的微信/支付宝收款记录里核对到账，再通过</small>
        <em>{reqs.length}</em>
      </header>
      {msg && <p className="form-error">{msg}</p>}
      {reqs.length ? (
        <div className="review-list">
          {reqs.map((r) => (
            <article className="review-item" key={r.id}>
              <div className="review-main">
                <strong>{r.token_name}</strong>
                <span className="review-meta">
                  金额 <b>{r.amount || "—"}</b> · 付款时间 {r.paid_at_text || "—"} · 单号/备注{" "}
                  {r.reference || "—"}
                </span>
                {r.has_screenshot ? (
                  <a href={renewalScreenshotUrl(r.id)} target="_blank" rel="noreferrer">
                    查看付款截图
                  </a>
                ) : (
                  <span className="muted-cell">无截图</span>
                )}
              </div>
              <div className="review-actions">
                <button className="button primary" onClick={() => void onApprove(r)}>
                  核对无误，通过并延期
                </button>
                <button className="button secondary" onClick={() => void onReject(r)}>
                  拒绝
                </button>
              </div>
            </article>
          ))}
        </div>
      ) : (
        <p className="review-empty">暂无待审核的续费申请。</p>
      )}
    </section>
  );
}

function RenewalConfigEditor() {
  const [note, setNote] = useState("");
  const [wechat, setWechat] = useState<File>();
  const [alipay, setAlipay] = useState<File>();
  const [msg, setMsg] = useState("");
  const [info, setInfo] = useState<RenewalInfo | null>(null);

  const load = useCallback(async () => {
    try {
      const i = await getRenewalInfo();
      setInfo(i);
      setNote(i.note);
    } catch {
      /* ignore */
    }
  }, []);
  useEffect(() => {
    void load();
  }, [load]);

  async function save() {
    setMsg("");
    try {
      await setRenewalConfig(note, wechat, alipay);
      setWechat(undefined);
      setAlipay(undefined);
      setMsg("已保存");
      await load();
    } catch (reason) {
      setMsg(reason instanceof Error ? reason.message : "保存失败");
    }
  }

  return (
    <section className="renewal-config">
      <header>
        <QrCode size={18} /> 续费收款配置
        <small>运营点「续费延期」时会看到下面的说明和收款码</small>
      </header>
      <textarea
        rows={3}
        placeholder="续费说明，如：月费 ¥99，扫码支付后填写付款信息提交，管理员核对到账后为你延期。"
        value={note}
        onChange={(e) => setNote(e.target.value)}
      />
      <div className="renewal-uploads">
        <label>
          微信收款码
          <input type="file" accept="image/*" onChange={(e) => setWechat(e.target.files?.[0])} />
          {info?.wechat ? (
            <img src={renewalQrUrl("wechat")} alt="微信收款码" />
          ) : (
            <span className="qr-empty">未配置</span>
          )}
        </label>
        <label>
          支付宝收款码
          <input type="file" accept="image/*" onChange={(e) => setAlipay(e.target.files?.[0])} />
          {info?.alipay ? (
            <img src={renewalQrUrl("alipay")} alt="支付宝收款码" />
          ) : (
            <span className="qr-empty">未配置</span>
          )}
        </label>
      </div>
      <div className="renewal-save">
        <button className="button primary" onClick={save}>
          保存收款配置
        </button>
        {msg && <span className="ai-prompt-msg">{msg}</span>}
      </div>
    </section>
  );
}

/* ------------------------------ Operator ------------------------------ */
const REQ_STATUS: Record<string, string> = {
  pending: "待管理员核对",
  approved: "已通过",
  rejected: "已拒绝",
};

function OperatorPanel({ me }: { me: Identity }) {
  const [showRenew, setShowRenew] = useState(false);
  const [info, setInfo] = useState<RenewalInfo | null>(null);
  const [mine, setMine] = useState<RenewalRequest | null>(null);
  const [amount, setAmount] = useState("");
  const [paidAt, setPaidAt] = useState("");
  const [reference, setReference] = useState("");
  const [shot, setShot] = useState<File>();
  const [msg, setMsg] = useState("");

  const loadMine = useCallback(async () => {
    try {
      setMine(await getMyRenewal());
    } catch {
      /* ignore */
    }
  }, []);
  useEffect(() => {
    void loadMine();
  }, [loadMine]);

  async function openRenew() {
    setMsg("");
    try {
      setInfo(await getRenewalInfo());
      setShowRenew(true);
    } catch (reason) {
      setMsg(reason instanceof Error ? reason.message : "加载失败");
    }
  }
  async function onSubmit() {
    if (!amount.trim()) {
      setMsg("请填写付款金额");
      return;
    }
    setMsg("");
    try {
      await submitRenewal({
        amount: amount.trim(),
        paid_at_text: paidAt.trim(),
        reference: reference.trim(),
        screenshot: shot,
      });
      setShowRenew(false);
      setAmount("");
      setPaidAt("");
      setReference("");
      setShot(undefined);
      await loadMine();
    } catch (reason) {
      setMsg(reason instanceof Error ? reason.message : "提交失败");
    }
  }

  const danger = me.status !== "active" || (me.days_left ?? 99) <= 7;

  return (
    <>
      <section className="my-token-card">
        <div className="my-token-main">
          <span className={`status-pill st-${me.status}`}>{STATUS_LABEL[me.status]}</span>
          <h3>{me.name}</h3>
          <p>角色：{ROLE_LABEL[me.role] ?? me.role}</p>
          <p>
            到期日期：<b>{fmtDate(me.expires_at ?? null)}</b>
            {me.days_left != null ? (
              <span className={`days-left ${danger ? "danger" : ""}`}>
                {" "}· {me.days_left >= 0 ? `剩 ${me.days_left} 天` : `已过期 ${-me.days_left} 天`}
              </span>
            ) : null}
          </p>
          {mine ? (
            <p className={`my-renewal-status rs-${mine.status}`}>
              最近一次续费申请：{REQ_STATUS[mine.status] ?? mine.status}
              {mine.status === "approved" && mine.days_granted
                ? `（已延期 ${mine.days_granted} 天）`
                : ""}
              {mine.status === "rejected" && mine.review_note
                ? `（原因：${mine.review_note}）`
                : ""}
            </p>
          ) : null}
        </div>
        <button className="button primary" onClick={() => void openRenew()}>
          续费延期
        </button>
      </section>
      {msg && <p className="form-error">{msg}</p>}

      {showRenew && info && (
        <div className="renew-modal" onClick={() => setShowRenew(false)}>
          <div className="renew-box" onClick={(e) => e.stopPropagation()}>
            <h3>续费延期</h3>
            <p className="renew-note">
              {info.note || "请扫码支付，然后填写付款信息提交申请，管理员核对到账后为你延期。"}
            </p>
            <div className="renew-qrs">
              {info.wechat ? (
                <figure>
                  <img src={renewalQrUrl("wechat")} alt="微信收款码" />
                  <figcaption>微信</figcaption>
                </figure>
              ) : null}
              {info.alipay ? (
                <figure>
                  <img src={renewalQrUrl("alipay")} alt="支付宝收款码" />
                  <figcaption>支付宝</figcaption>
                </figure>
              ) : null}
              {!info.wechat && !info.alipay ? (
                <span className="qr-empty">还没有上传收款码，请直接联系管理员。</span>
              ) : null}
            </div>
            <div className="renew-form">
              <input
                placeholder="付款金额（必填，如 99）"
                value={amount}
                onChange={(e) => setAmount(e.target.value)}
              />
              <input
                placeholder="付款时间（如 6月23日 14:30）"
                value={paidAt}
                onChange={(e) => setPaidAt(e.target.value)}
              />
              <input
                placeholder="付款单号 / 备注（便于核对）"
                value={reference}
                onChange={(e) => setReference(e.target.value)}
              />
              <label className="renew-upload">
                付款截图（可选）
                <input type="file" accept="image/*" onChange={(e) => setShot(e.target.files?.[0])} />
              </label>
            </div>
            <p className="renew-hint">
              提交后为「待管理员核对」状态——管理员在收款记录中确认到账后才会为你延期。
            </p>
            <button className="button primary" onClick={() => void onSubmit()}>
              提交续费申请
            </button>
            <button className="button secondary" onClick={() => setShowRenew(false)}>
              关闭
            </button>
          </div>
        </div>
      )}
    </>
  );
}
