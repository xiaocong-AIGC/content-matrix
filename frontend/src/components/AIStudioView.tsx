import { useCallback, useEffect, useState } from "react";
import { Check, RefreshCw, Sparkles, Trash2, Wand2 } from "lucide-react";

import {
  acceptDraft,
  type AiConfig,
  type ContentDraft,
  contentPerformance,
  createPreset,
  deletePreset,
  type DraftStats,
  draftStats,
  engineStatus,
  generateDrafts,
  getAiConfig,
  getEnginePrompt,
  listCities,
  listDrafts,
  listPresets,
  type PerformanceRow,
  previewEnginePrompt,
  type PromptPreset,
  rejectDraft,
  saveAiConfig,
  setEnginePrompt,
  updateDraft,
  updatePreset,
} from "../lib/api";
import { platformLabel } from "../lib/platform";

import { useConfirm } from "./ConfirmModal";
import { Select } from "./Select";
const PLATFORMS = ["xhs", "douyin", "both"] as const;

// Compact token count for the hero: 12000 -> 1.2万, 2000 -> 2,000.
function fmtTokens(n: number): string {
  if (n >= 10000) return `${(n / 10000).toFixed(1)}万`;
  return n.toLocaleString("en-US");
}

export function AIStudioView() {
  const [confirm, confirmUI] = useConfirm();
  const [configured, setConfigured] = useState(true);
  const [drafts, setDrafts] = useState<ContentDraft[]>([]);
  const [stats, setStats] = useState<DraftStats | null>(null);
  const [count, setCount] = useState(3);
  const [platform, setPlatform] = useState<string>("xhs");
  const [theme, setTheme] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  // Editable system prompt + the assembled-input preview.
  const [prompt, setPrompt] = useState("");
  const [promptDefault, setPromptDefault] = useState("");
  const [isCustom, setIsCustom] = useState(false);
  const [promptMsg, setPromptMsg] = useState("");
  const [preview, setPreview] = useState("");
  // AI 配置（仅管理员可见/可改；operator 请求会 403 → keyInfo 保持 null → 面板不渲染）。
  const [keyInfo, setKeyInfo] = useState<AiConfig | null>(null);
  const [keyInput, setKeyInput] = useState("");
  const [urlInput, setUrlInput] = useState("");
  const [modelInput, setModelInput] = useState("");
  const [keyMsg, setKeyMsg] = useState("");
  // 生成预设：多套「提示词+样本」，生成时任选一套；不选 = 当前默认逻辑。
  const [presets, setPresets] = useState<PromptPreset[]>([]);
  const [presetId, setPresetId] = useState<number | null>(null);
  const [editing, setEditing] = useState<Partial<PromptPreset> | null>(null);
  const [presetMsg, setPresetMsg] = useState("");
  // 城市下拉选项（草稿采纳时用）+ 效果榜数据（预设"从效果榜选样本"时用）。
  const [cities, setCities] = useState<string[]>(["通用", "全国"]);
  const [perfRows, setPerfRows] = useState<PerformanceRow[]>([]);

  const load = useCallback(async () => {
    try {
      const [st, list, pr, ds] = await Promise.all([
        engineStatus(),
        listDrafts("pending"),
        getEnginePrompt(),
        draftStats().catch(() => null),
      ]);
      setConfigured(st.configured);
      setDrafts(list);
      setPrompt(pr.prompt);
      setPromptDefault(pr.default);
      setIsCustom(pr.is_custom);
      setStats(ds);
      // admin-only: operators 403 here → hide the config panel.
      const cfg = await getAiConfig().catch(() => null);
      setKeyInfo(cfg);
      if (cfg) {
        setUrlInput(cfg.base_url);
        setModelInput(cfg.model);
      }
      setPresets(await listPresets().catch(() => []));
      setCities(await listCities().catch(() => ["通用", "全国"]));
      setPerfRows(await contentPerformance().catch(() => []));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "加载失败");
    }
  }, []);

  async function onSaveKey() {
    setKeyMsg("");
    try {
      // Only send the key if the admin typed one (blank = keep current).
      const info = await saveAiConfig({
        ...(keyInput.trim() ? { api_key: keyInput.trim() } : {}),
        base_url: urlInput.trim(),
        model: modelInput.trim(),
      });
      setKeyInfo(info);
      setKeyInput("");
      setUrlInput(info.base_url);
      setModelInput(info.model);
      setKeyMsg("已保存");
      setConfigured(info.configured);
    } catch (reason) {
      setKeyMsg(reason instanceof Error ? reason.message : "保存失败");
    }
  }

  async function savePrompt() {
    setPromptMsg("");
    try {
      const r = await setEnginePrompt(prompt);
      setPrompt(r.prompt);
      setIsCustom(r.is_custom);
      setPromptMsg("已保存");
    } catch (reason) {
      setPromptMsg(reason instanceof Error ? reason.message : "保存失败");
    }
  }

  async function loadPreview() {
    setPromptMsg("");
    try {
      const p = await previewEnginePrompt(platform, theme.trim() || undefined, presetId);
      setPreview(`【System / 角色与要求】\n${p.system}\n\n【User / 实际输入（含${p.exemplar_count}个样本）】\n${p.user}`);
    } catch (reason) {
      setPromptMsg(reason instanceof Error ? reason.message : "预览失败");
    }
  }

  async function savePreset() {
    if (!editing) return;
    setPresetMsg("");
    const payload = {
      name: (editing.name ?? "").trim() || "未命名预设",
      system_prompt: editing.system_prompt ?? "",
      sample_mode: editing.sample_mode ?? "default",
      sample_text: editing.sample_text ?? "",
      sample_ids: editing.sample_ids ?? [],
    };
    try {
      const saved = editing.id
        ? await updatePreset(editing.id, payload)
        : await createPreset(payload);
      setPresets((prev) => {
        const rest = prev.filter((p) => p.id !== saved.id);
        return [saved, ...rest];
      });
      setEditing(null);
      setPresetMsg("已保存");
    } catch (reason) {
      setPresetMsg(reason instanceof Error ? reason.message : "保存失败");
    }
  }

  async function onDeletePreset(id: number) {
    if (!(await confirm({ title: "删除这套预设？", confirmText: "删除", danger: true }))) return;
    try {
      await deletePreset(id);
      setPresets((prev) => prev.filter((p) => p.id !== id));
      if (presetId === id) setPresetId(null);
    } catch (reason) {
      setPresetMsg(reason instanceof Error ? reason.message : "删除失败");
    }
  }

  useEffect(() => {
    void load();
  }, [load]);

  async function onGenerate() {
    setBusy(true);
    setError("");
    try {
      const created = await generateDrafts({
        count,
        platform,
        theme: theme.trim() || undefined,
        ...(presetId ? { preset_id: presetId } : {}),
      });
      // Duplicates are auto-filtered out of the pending review queue server-side.
      setDrafts((prev) => [...created.filter((d) => d.status !== "duplicate"), ...prev]);
      const dropped = created.length - created.filter((d) => d.status !== "duplicate").length;
      if (dropped > 0) {
        setError(`这次有 ${dropped} 条和库里已有的内容太像，已经去掉。`);
      }
      void draftStats().then(setStats).catch(() => {});
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "生成失败");
    } finally {
      setBusy(false);
    }
  }

  function patch(id: number, change: Partial<ContentDraft>) {
    setDrafts((prev) => prev.map((d) => (d.id === id ? { ...d, ...change } : d)));
  }

  async function onAccept(d: ContentDraft, city: string) {
    setError("");
    try {
      // Persist any inline edits, then accept into the content pool.
      await updateDraft(d.id, {
        cover_title: d.cover_title,
        title: d.title,
        body: d.body,
        topics: d.topics,
      });
      await acceptDraft(d.id, city.trim() || "通用");
      setDrafts((prev) => prev.filter((x) => x.id !== d.id));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "采纳失败");
    }
  }

  async function onReject(id: number) {
    try {
      await rejectDraft(id);
      setDrafts((prev) => prev.filter((x) => x.id !== id));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "操作失败");
    }
  }

  return (
    <div className="catalog-page page-enter">
      {confirmUI}
      <header className="catalog-hero history-hero">
        <div>
          <span>创作</span>
          <h2>从表现好的内容里，找到可复用的写法。</h2>
          <p>
            参考互动高的作品重新写一篇，风格不重样。生成的先放进草稿，
            你看过并采纳之后才会进入内容库。
          </p>
        </div>
        {stats && (
          <div className="hero-stats">
            <div className="hero-stat">
              <b>¥{stats.cost_cny.toFixed(2)}</b>
              <small>累计成本</small>
            </div>
            <div className="hero-stat">
              <b>{fmtTokens(stats.prompt_tokens + stats.completion_tokens)}</b>
              <small>消耗 token</small>
            </div>
            <div className="hero-stat">
              <b>{stats.total_drafts}</b>
              <small>累计生成</small>
            </div>
            <div className="hero-stat accent">
              <b>{stats.duplicate}</b>
              <small>查重拦截</small>
            </div>
          </div>
        )}
      </header>

      {!configured && (
        <div className="catalog-empty compact" style={{ marginBottom: 16 }}>
          <Wand2 size={32} strokeWidth={1.1} />
          <h3>还没接入 AI 服务</h3>
          <p>
            在后端配好环境变量 <code>DEEPSEEK_API_KEY</code> 并重启，就能开始生成。
          </p>
        </div>
      )}

      {keyInfo && (
        <details className="ai-prompt-panel ai-key-panel">
          <summary>
            🔑 AI 接口配置（密钥 / 网关 / 模型）· 仅管理员
            <span className={`ai-prompt-tag ${keyInfo.configured ? "custom" : ""}`}>
              {keyInfo.configured
                ? keyInfo.source === "custom"
                  ? "已配置"
                  : "环境变量"
                : "未配置"}
            </span>
          </summary>
          <p className="ai-prompt-hint">
            管理员配置 AI 提供方（保存后覆盖环境变量，运营角色看不到）。
            <b>接口地址填任意 OpenAI 兼容网关</b>，公司内网网关和公有云都行；
            密钥当前：<code>{keyInfo.masked || "无"}</code>。
          </p>
          <label className="ai-field">
            接口地址（base_url，OpenAI 兼容）
            <input
              type="text"
              placeholder="https://api.deepseek.com 或公司内网网关地址"
              value={urlInput}
              onChange={(e) => setUrlInput(e.target.value)}
            />
          </label>
          <label className="ai-field">
            模型（model）
            <input
              type="text"
              placeholder="如 gpt-4o / deepseek-chat"
              value={modelInput}
              onChange={(e) => setModelInput(e.target.value)}
            />
          </label>
          <div className="ai-prompt-actions">
            <input
              className="ai-field"
              style={{ flex: 1 }}
              type="password"
              placeholder="粘贴新的 API Key（留空=不改）"
              value={keyInput}
              onChange={(e) => setKeyInput(e.target.value)}
            />
            <button className="button primary" onClick={onSaveKey}>
              保存配置
            </button>
            {keyMsg && <span className="ai-prompt-msg">{keyMsg}</span>}
          </div>
        </details>
      )}

      <details className="ai-prompt-panel">
        <summary>
          ⚙️ 提示词设置（调整给 AI 的指令）
          <span className={`ai-prompt-tag ${isCustom ? "custom" : ""}`}>
            {isCustom ? "已自定义" : "默认"}
          </span>
        </summary>
        <p className="ai-prompt-hint">
          这是发给 AI 的「角色与要求」。生成时会自动带上样本（互动高的笔记，含封面、正文和数据），
          点「预览完整输入」可看到实际发送内容。
        </p>
        <textarea
          className="ai-prompt-text"
          rows={12}
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
        />
        <div className="ai-prompt-actions">
          <button className="button secondary" onClick={() => setPrompt(promptDefault)}>
            恢复默认
          </button>
          <button className="button secondary" onClick={loadPreview}>
            预览完整输入
          </button>
          <button className="button primary" onClick={savePrompt}>
            保存提示词
          </button>
          {promptMsg && <span className="ai-prompt-msg">{promptMsg}</span>}
        </div>
        {preview && <pre className="ai-prompt-preview">{preview}</pre>}
      </details>

      <details className="ai-prompt-panel">
        <summary>
          🗂 生成预设（多套提示词/样本，生成时任选）
          <span className={`ai-prompt-tag ${presets.length ? "custom" : ""}`}>
            {presets.length ? `${presets.length} 套` : "无"}
          </span>
        </summary>
        <p className="ai-prompt-hint">
          每套预设 = 一份「提示词 + 样本」。样本可以用<b>默认</b>（自动挑已发布内容里互动高的），
          也可以<b>粘贴自定义样本</b>（把想模仿的文案复制进来）。生成时在下方选择用哪套；不选就用默认。
        </p>
        {presetMsg && <span className="ai-prompt-msg">{presetMsg}</span>}
        {editing ? (
          <div className="ai-preset-editor">
            <label className="ai-field">
              预设名称
              <input
                value={editing.name ?? ""}
                placeholder="如：口语化爆款风 / 专业测评风"
                onChange={(e) => setEditing({ ...editing, name: e.target.value })}
              />
            </label>
            <label className="ai-field">
              提示词（留空 = 用系统默认提示词）
              <textarea
                rows={8}
                value={editing.system_prompt ?? ""}
                placeholder="给 AI 的角色与要求…（可从上方「提示词设置」复制过来再改）"
                onChange={(e) => setEditing({ ...editing, system_prompt: e.target.value })}
              />
            </label>
            <label className="ai-field">
              样本来源
              <Select
                variant="field"
                value={editing.sample_mode ?? "default"}
                options={[
                  { value: "default", label: "默认（自动取高互动内容）" },
                  { value: "pick", label: "从效果榜选样本（勾选表现好的做参照）" },
                  { value: "custom", label: "自定义（粘贴样本文案）" },
                ]}
                onChange={(v) => setEditing({ ...editing, sample_mode: v })}
              />
            </label>
            {editing.sample_mode === "pick" && (
              <div className="ai-field">
                <span>
                  从效果榜勾选要模仿的内容（AI 会参考它们的结构和风格）· 已选{" "}
                  {(editing.sample_ids ?? []).length}
                </span>
                <div className="ai-sample-pick">
                  {perfRows.length ? (
                    perfRows.map((r) => {
                      const sel = (editing.sample_ids ?? []).includes(r.content_id);
                      return (
                        <label
                          key={r.content_id}
                          className={`ai-sample-row ${sel ? "on" : ""}`}
                        >
                          <input
                            type="checkbox"
                            checked={sel}
                            onChange={() => {
                              const cur = editing.sample_ids ?? [];
                              setEditing({
                                ...editing,
                                sample_ids: sel
                                  ? cur.filter((x) => x !== r.content_id)
                                  : [...cur, r.content_id],
                              });
                            }}
                          />
                          <span className={`platform-badge plat-${r.platform}`}>
                            {platformLabel(r.platform)}
                          </span>
                          <b>{r.cover_title || r.title || "（无标题）"}</b>
                          <small>互动 {r.engagement} · 阅读 {r.views}</small>
                        </label>
                      );
                    })
                  ) : (
                    <p className="ai-prompt-hint">
                      效果榜还没有数据。账号发布、数据更新后，这里就能勾选内容做样本。
                    </p>
                  )}
                </div>
              </div>
            )}
            {editing.sample_mode === "custom" && (
              <label className="ai-field">
                自定义样本（把要模仿的文案整段粘贴进来，可多篇）
                <textarea
                  rows={8}
                  value={editing.sample_text ?? ""}
                  placeholder={"样本1：\n封面：…\n正文：…\n\n样本2：…"}
                  onChange={(e) => setEditing({ ...editing, sample_text: e.target.value })}
                />
              </label>
            )}
            <div className="ai-prompt-actions">
              <button className="button secondary" onClick={() => setEditing(null)}>
                取消
              </button>
              <button className="button primary" onClick={savePreset}>
                保存预设
              </button>
            </div>
          </div>
        ) : (
          <div className="ai-preset-list">
            {presets.map((p) => (
              <div key={p.id} className="ai-preset-row">
                <b>{p.name}</b>
                <small>
                  {p.sample_mode === "custom" ? "自定义样本" : "默认样本"}
                  {p.system_prompt.trim() ? " · 自定义提示词" : " · 默认提示词"}
                  {p.owner ? ` · ${p.owner}` : ""}
                </small>
                <button className="button secondary" onClick={() => setEditing({ ...p })}>
                  编辑
                </button>
                <button className="button secondary" onClick={() => onDeletePreset(p.id)}>
                  删除
                </button>
              </div>
            ))}
            <button
              className="button secondary add-slot"
              onClick={() => setEditing({ sample_mode: "default" })}
            >
              ＋ 新建预设
            </button>
          </div>
        )}
      </details>

      <section className="ai-generate-bar">
        <label>
          数量
          <input
            type="number"
            min={1}
            max={10}
            value={count}
            onChange={(e) => setCount(Math.max(1, Math.min(10, Number(e.target.value) || 1)))}
          />
        </label>
        <label>
          平台
          <Select
            variant="field"
            value={platform}
            options={PLATFORMS.map((p) => ({ value: p, label: platformLabel(p) }))}
            onChange={setPlatform}
          />
        </label>
        <label>
          预设
          <Select
            variant="field"
            value={presetId ? String(presetId) : ""}
            placeholder="默认"
            options={[
              { value: "", label: "默认" },
              ...presets.map((p) => ({ value: String(p.id), label: p.name })),
            ]}
            onChange={(v) => setPresetId(v ? Number(v) : null)}
          />
        </label>
        <label className="ai-theme">
          选题方向（可选）
          <input
            type="text"
            placeholder="如：上海二手房砍价 / 首付不够怎么办"
            value={theme}
            onChange={(e) => setTheme(e.target.value)}
          />
        </label>
        <button className="button primary" disabled={busy || !configured} onClick={onGenerate}>
          {busy ? <RefreshCw size={16} className="spin" /> : <Wand2 size={16} />}
          {busy ? "生成中…" : "生成草稿"}
        </button>
      </section>

      {error && <p className="form-error">{error}</p>}

      {drafts.length ? (
        <section className="ai-draft-grid">
          {drafts.map((d) => (
            <DraftCard
              key={d.id}
              draft={d}
              cities={cities}
              onChange={(c) => patch(d.id, c)}
              onAccept={(city) => onAccept(d, city)}
              onReject={() => onReject(d.id)}
            />
          ))}
        </section>
      ) : (
        <div className="catalog-empty compact">
          <Sparkles size={36} strokeWidth={1.1} />
          <h3>还没有要审的草稿</h3>
          <p>设好数量和选题方向，点「生成草稿」，AI 会参考互动高的内容重新写几篇。</p>
        </div>
      )}
    </div>
  );
}

function DraftCard({
  draft,
  cities,
  onChange,
  onAccept,
  onReject,
}: {
  draft: ContentDraft;
  cities: string[];
  onChange: (c: Partial<ContentDraft>) => void;
  onAccept: (city: string) => void;
  onReject: () => void;
}) {
  // 默认用 AI 按内容判断的城市；下拉里能改成任意已接入城市。
  const [city, setCity] = useState(draft.city || "通用");
  const cityOptions = [...new Set([draft.city, ...cities].filter(Boolean))];
  return (
    <article className="ai-draft-card">
      <div className="ai-draft-head">
        <span className={`platform-badge plat-${draft.platform}`}>
          {platformLabel(draft.platform)}
        </span>
        <span className="ai-draft-src" title={draft.source_note}>
          改写自 {draft.source_note || "—"} · {draft.model}
        </span>
        {draft.dup_score >= 0.75 && (
          <span
            className="ai-draft-dup"
            title={`与「${draft.dup_of}」相似度 ${Math.round(draft.dup_score * 100)}%`}
          >
            疑似重复 {Math.round(draft.dup_score * 100)}%
          </span>
        )}
      </div>
      <label className="ai-field">
        封面大字报
        <input
          value={draft.cover_title}
          onChange={(e) => onChange({ cover_title: e.target.value })}
        />
      </label>
      <label className="ai-field">
        标题（选填）
        <input value={draft.title} onChange={(e) => onChange({ title: e.target.value })} />
      </label>
      <label className="ai-field">
        正文
        <textarea
          rows={6}
          value={draft.body}
          onChange={(e) => onChange({ body: e.target.value })}
        />
      </label>
      <label className="ai-field">
        话题（空格分隔）
        <input
          value={draft.topics.join(" ")}
          onChange={(e) =>
            onChange({
              topics: e.target.value
                .split(/[\s,，#]+/)
                .map((t) => t.trim())
                .filter(Boolean),
            })
          }
        />
      </label>
      <div className="ai-draft-actions">
        <label className="ai-city">
          城市
          <Select
            variant="field"
            value={city}
            // 城市是开放集合，草稿采纳时也要能现填一个新的
            creatable
            options={cityOptions.map((c) => ({ value: c, label: c }))}
            onChange={setCity}
          />
        </label>
        <button className="button secondary" onClick={onReject}>
          <Trash2 size={15} /> 拒绝
        </button>
        <button className="button primary" onClick={() => onAccept(city)}>
          <Check size={15} /> 采纳进库
        </button>
      </div>
    </article>
  );
}
