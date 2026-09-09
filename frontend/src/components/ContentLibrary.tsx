import { useCallback, useEffect, useState } from "react";
import { Download, MapPin, Plus, RotateCcw, Trash2, Upload } from "lucide-react";

import {
  createContent,
  deleteContent,
  importContentCsv,
  listContent,
  returnContentToPending,
} from "../lib/api";
import type { ContentItem } from "../lib/types";
import { platformLabel } from "../lib/platform";
import { usePersistentState } from "../lib/usePersistentState";
import { FilterDropdown } from "./FilterDropdown";
import { FormModal } from "./FormModal";
import { Pager, usePaged } from "./Pager";

function formatTime(value?: string | null) {
  if (!value) return "";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(value));
}

export function ContentLibrary() {
  const [items, setItems] = useState<ContentItem[]>([]);
  const [showAdd, setShowAdd] = useState(false);
  const [showPublishing, setShowPublishing] = useState(false);
  const [cancellingId, setCancellingId] = useState<number>();
  // 筛选持久化：切页再回来仍保留选择（见 usePersistentState）。
  const [pendingCity, setPendingCity] = usePersistentState("content.pendingCity", "全部");
  const [pubCity, setPubCity] = usePersistentState("content.pubCity", "全部");
  const [pubAccount, setPubAccount] = usePersistentState("content.pubAccount", "全部");
  const [pubTime, setPubTime] = usePersistentState<"all" | "today" | "yesterday">(
    "content.pubTime",
    "all",
  );
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      setItems(await listContent());
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "加载失败");
    }
  }, []);

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), 3000);
    return () => window.clearInterval(timer);
  }, [load]);

  const cityOf = (i: ContentItem) => i.city || "通用";
  const accountOf = (i: ContentItem) =>
    i.published_device_nickname || i.published_device_name || "未知账号";
  // account nickname -> its platform, so the filter can show 抖音/小红书 per account.
  const accountPlatform = (nick: string) =>
    publishedItems.find((i) => accountOf(i) === nick)?.platform || "douyin";

  const pendingItems = items.filter((i) => i.status === "pending");
  const publishingItems = items.filter((i) => i.status === "publishing");
  // 已发布按「发布时间」倒序——内容的 created_at 可能远早于发布（如二改/导入的老内容），
  // 按 created_at 排会把刚发的内容沉到底部、翻页都看不到。
  const publishedItems = items
    .filter((i) => i.status === "published")
    .sort((a, b) => (b.published_at ?? "").localeCompare(a.published_at ?? ""));
  const pendingCount = pendingItems.length;

  const pendingCities = ["全部", ...new Set(pendingItems.map(cityOf))];
  const pubCities = ["全部", ...new Set(publishedItems.map(cityOf))];
  const pubAccounts = ["全部", ...new Set(publishedItems.map(accountOf))];

  const inTimeWindow = (value?: string | null) => {
    if (pubTime === "all") return true;
    if (!value) return false;
    const d = new Date(value);
    const now = new Date();
    const startToday = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    if (pubTime === "today") return d >= startToday;
    const startYesterday = new Date(startToday.getTime() - 86400000);
    return d >= startYesterday && d < startToday;
  };

  const shownPending = pendingItems.filter(
    (i) => pendingCity === "全部" || cityOf(i) === pendingCity,
  );
  const shownPublished = publishedItems.filter(
    (i) =>
      (pubCity === "全部" || cityOf(i) === pubCity) &&
      (pubAccount === "全部" || accountOf(i) === pubAccount) &&
      inTimeWindow(i.published_at),
  );

  const pendingPage = usePaged(shownPending, 10);
  const publishedPage = usePaged(shownPublished, 10);

  function downloadTemplate() {
    const header = "封面文字,正文,标题,话题,城市";
    const sample =
      "上海买房避坑,预算300万在上海买房怎么选？市区老破小还是远一点的次新？,300万上海买房,上海买房 沪漂买房,上海";
    const csv = "﻿" + header + "\n" + sample + "\n"; // BOM for Excel 中文
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "内容库导入模板.csv";
    a.click();
    URL.revokeObjectURL(url);
  }

  async function importCsv(file: File | undefined) {
    if (!file) return;
    setError("");
    try {
      const r = await importContentCsv(file);
      await load();
      setError(
        r.created
          ? ""
          : `没有可导入的行（正文必填${r.skipped ? `，跳过 ${r.skipped} 行` : ""}）`,
      );
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "导入失败");
    }
  }

  const renderRow = (item: ContentItem) => {
    // 大字报封面文字才是抖音文字帖发出去的视觉主体，title 在业务上是选填的备注。
    // 全站另外四处（AI 创作、排期弹窗、效果榜、二改接口）早就以 cover_title 为主标题，
    // 只有这个列表一直在渲染 title —— 于是运营填了封面却在库里看不见，
    // 只能靠正文前半句去认稿。
    const cover = item.cover_title?.trim();
    const heading = cover || item.title?.trim() || item.body.slice(0, 24) + "…";
    return (
    <article key={item.id} className="content-row">
      <div className="content-row-main">
        <div className="content-row-top">
          <strong className={cover ? "content-cover" : "content-cover weak"}>
            {heading}
          </strong>
          {!cover ? <span className="content-nocover">未填封面</span> : null}
          <span className={`platform-badge plat-${item.platform || "douyin"}`}>
            {item.platform === "xhs"
              ? "小红书"
              : item.platform === "both"
                ? "双平台"
                : "抖音"}
          </span>
          <span
            className={`content-city ${cityOf(item) === "通用" ? "generic" : ""}`}
          >
            <MapPin size={10} /> {cityOf(item)}
          </span>
        </div>
        {cover && item.title?.trim() ? (
          <small className="content-subtitle">内部标题：{item.title}</small>
        ) : null}
        <p>{item.body}</p>
        {item.topics.length ? (
          <div className="content-topics">
            {item.topics.map((topic) => (
              <span key={topic}>#{topic}</span>
            ))}
          </div>
        ) : null}
        {item.status === "published" ? (
          <small className="content-published">
            已由 <b className="meta-nick">{accountOf(item)}</b> 于{" "}
            <b className="meta-time">{formatTime(item.published_at)}</b> 发布
          </small>
        ) : null}
      </div>
      {item.status === "pending" ? (
        <button
          className="content-delete"
          aria-label="删除"
          onClick={() => void remove(item.id)}
        >
          <Trash2 size={15} />
        </button>
      ) : null}
    </article>
    );
  };

  async function addContent(values: Record<string, string>) {
    if (!values.body?.trim()) {
      setError("请填写正文内容");
      return;
    }
    setError("");
    try {
      await createContent({
        cover_title: values.cover_title ?? "",
        title: values.title ?? "",
        body: values.body,
        topics: (values.topics ?? "")
          .split(/[,，\s#]+/)
          .map((t) => t.trim())
          .filter(Boolean),
        city: (values.city ?? "").trim() || "通用",
        platform: (values.platform ?? "").trim() || "douyin",
      });
      setShowAdd(false);
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "添加失败");
    }
  }

  async function remove(id: number) {
    await deleteContent(id);
    await load();
  }

  async function cancelPublishing(item: ContentItem) {
    setCancellingId(item.id);
    setError("");
    try {
      await returnContentToPending(item.id);
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "取消失败");
    } finally {
      setCancellingId(undefined);
    }
  }

  return (
    <div className="content-page page-enter">
      <header className="catalog-hero content-hero">
        <div>
          <span>内容</span>
          <h2>素材备好，随时可发。</h2>
          <p>
            每条内容只会被一个账号发布一次，按城市自动匹配账号，不用手动分配。
          </p>
        </div>
        <div className="hero-stats">
          <div className="hero-stat">
            <b>{items.length}</b>
            <small>总条数</small>
          </div>
          <div className="hero-stat accent">
            <b>{pendingCount}</b>
            <small>待发布</small>
          </div>
          <div className="hero-stat">
            <b>{publishedItems.length}</b>
            <small>已发布</small>
          </div>
        </div>
      </header>

      <div className="content-toolbar">
        <button
          type="button"
          className="button primary"
          onClick={() => {
            setError("");
            setShowAdd(true);
          }}
        >
          <Plus size={16} /> 新增内容
        </button>
        <label className="content-import-btn">
          <input
            type="file"
            accept=".csv,text/csv"
            hidden
            onChange={(e) => {
              void importCsv(e.target.files?.[0]);
              e.target.value = "";
            }}
          />
          <Upload size={13} /> 批量导入 CSV
        </label>
        <button
          type="button"
          className="content-import-btn"
          onClick={downloadTemplate}
        >
          <Download size={13} /> 下载模板
        </button>
        {error ? <span className="content-toolbar-error">{error}</span> : null}
      </div>

      <div className="content-main">
        <div className="content-lists">
          {/* 待发布：按地区筛选 */}
          <section className="content-block">
            <header className="content-block-head">
              <div className="content-block-title">
                <span>待发布</span>
                <small>{shownPending.length}</small>
                {publishingItems.length ? (
                  <button
                    type="button"
                    className={`content-publishing-note ${showPublishing ? "on" : ""}`}
                    onClick={() => setShowPublishing((v) => !v)}
                    title="查看正在发布的内容和对应账号，可以取消并退回待发布"
                  >
                    · 发布中 {publishingItems.length}
                    <b>{showPublishing ? "收起" : "查看"}</b>
                  </button>
                ) : null}
              </div>
              {pendingCities.length > 1 ? (
                <FilterDropdown
                  label="城市"
                  icon={<MapPin size={13} />}
                  value={pendingCity}
                  onChange={setPendingCity}
                  options={pendingCities.map((c) => ({
                    value: c,
                    label: c === "全部" ? "全部城市" : c,
                    count:
                      c === "全部"
                        ? pendingItems.length
                        : pendingItems.filter((i) => cityOf(i) === c).length,
                  }))}
                />
              ) : null}
            </header>
            {showPublishing && publishingItems.length ? (
              <div className="content-publishing-panel">
                {publishingItems.map((item) => {
                  const acct = item.published_device_nickname || item.published_device_name;
                  return (
                    <article key={item.id} className="publishing-row">
                      <div className="publishing-row-main">
                        <strong>{item.title || item.body.slice(0, 24) || "（无标题）"}</strong>
                        <small>
                          {acct ? (
                            <>
                              发布账号 <b>{acct}</b>
                            </>
                          ) : (
                            <em className="publishing-stale">没有记录发布账号，可能是上次发布中断留下的</em>
                          )}
                          {item.published_at
                            ? ` · 发布于 ${formatTime(item.published_at)}`
                            : " · 发布中"}
                        </small>
                      </div>
                      <button
                        type="button"
                        className="publishing-cancel"
                        disabled={cancellingId === item.id}
                        onClick={() => void cancelPublishing(item)}
                      >
                        <RotateCcw size={13} />
                        {cancellingId === item.id ? "取消中…" : "取消并退回待发布"}
                      </button>
                    </article>
                  );
                })}
              </div>
            ) : null}
            <div className="content-block-body">
              {pendingPage.slice.length ? (
                pendingPage.slice.map(renderRow)
              ) : (
                <p className="content-block-empty">没有待发布内容（点上方「新增内容」添加）。</p>
              )}
            </div>
            <Pager
              page={pendingPage.page}
              pageCount={pendingPage.pageCount}
              total={pendingPage.total}
              onChange={pendingPage.setPage}
            />
          </section>

          {/* 已发布：按时间 / 地区 / 账号筛选 */}
          <section className="content-block">
            <header className="content-block-head">
              <div className="content-block-title">
                <span>已发布</span>
                <small>{shownPublished.length}</small>
              </div>
              <div className="content-pub-filters">
                <FilterDropdown
                  label="时间"
                  value={pubTime}
                  onChange={(v) =>
                    setPubTime(v as "all" | "today" | "yesterday")
                  }
                  options={[
                    { value: "all", label: "全部时间" },
                    { value: "today", label: "今天" },
                    { value: "yesterday", label: "昨天" },
                  ]}
                />
                <FilterDropdown
                  label="城市"
                  icon={<MapPin size={13} />}
                  value={pubCity}
                  onChange={setPubCity}
                  options={pubCities.map((c) => ({
                    value: c,
                    label: c === "全部" ? "全部城市" : c,
                    count:
                      c === "全部"
                        ? publishedItems.length
                        : publishedItems.filter((i) => cityOf(i) === c).length,
                  }))}
                />
                <FilterDropdown
                  label="账号"
                  value={pubAccount}
                  onChange={setPubAccount}
                  options={pubAccounts.map((a) => ({
                    value: a,
                    label: a === "全部" ? "全部账号" : a,
                    badge:
                      a === "全部" ? undefined : (
                        <span
                          className={`platform-badge plat-${accountPlatform(a)}`}
                        >
                          {platformLabel(accountPlatform(a))}
                        </span>
                      ),
                    count:
                      a === "全部"
                        ? publishedItems.length
                        : publishedItems.filter((i) => accountOf(i) === a).length,
                  }))}
                />
              </div>
            </header>
            <div className="content-block-body">
              {publishedPage.slice.length ? (
                publishedPage.slice.map(renderRow)
              ) : (
                <p className="content-block-empty">没有符合筛选条件的已发布内容。</p>
              )}
            </div>
            <Pager
              page={publishedPage.page}
              pageCount={publishedPage.pageCount}
              total={publishedPage.total}
              onChange={publishedPage.setPage}
            />
          </section>
        </div>
      </div>

      {showAdd ? (
        <FormModal
          title="新增内容"
          subtitle="加入内容库后，按城市自动分配给开启自动发布的账号"
          confirmText="加入内容库"
          fields={[
            {
              name: "cover_title",
              label: "封面文字（大字报）· 可选",
              value: "",
              type: "textarea",
              placeholder: "显示在封面上的文字，留空就用正文",
            },
            {
              name: "body",
              label: "正文内容 · 必填",
              value: "",
              type: "textarea",
              placeholder: "发出去的文案，可以比封面长",
            },
            { name: "title", label: "内部标题 · 可选", value: "" },
            {
              name: "topics",
              label: "话题",
              value: "",
              placeholder: "上海买房, 沪漂买房",
            },
            {
              name: "city",
              label: "城市（通用 = 任意账号可用）",
              value: "通用",
              type: "select" as const,
              // 城市是开放集合：既要能选已有的，也要能直接打一个新的
              creatable: true,
              // 去重：「通用」既写死在第一项、也会出现在已有城市里
              suggestions: [
                ...new Set(["通用", ...items.map((i) => i.city).filter(Boolean)]),
              ],
            },
            {
              name: "platform",
              label: "发布平台",
              type: "select" as const,
              value: "douyin",
              options: ["douyin", "xhs", "both"].map((v) => ({
                value: v,
                label: platformLabel(v),
              })),
            },
          ]}
          onConfirm={(values) => void addContent(values)}
          onClose={() => setShowAdd(false)}
        />
      ) : null}
    </div>
  );
}
