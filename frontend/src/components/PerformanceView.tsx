import { useCallback, useEffect, useState } from "react";
import {
  BarChart3,
  Eye,
  Heart,
  MessageCircle,
  RefreshCw,
  Star,
} from "lucide-react";

import {
  contentPerformance,
  refreshMetrics,
  type PerformanceRow,
  type TypeSummary,
  performanceSummary,
} from "../lib/api";
import { platformLabel } from "../lib/platform";
import { Pager, usePaged } from "./Pager";

function fmtDate(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

// Compact big-number formatting: 98250 -> 9.8万, 1200 -> 1,200.
function fmtNum(n: number): string {
  if (n >= 10000) return `${(n / 10000).toFixed(n >= 100000 ? 0 : 1)}万`;
  return n.toLocaleString("en-US");
}

export function PerformanceView() {
  const [rows, setRows] = useState<PerformanceRow[]>([]);
  // 结论走单独的接口 —— 它在全量上算，rows 是排序截断后的高分子集
  const [summary, setSummary] = useState<Record<string, TypeSummary>>({});
  const [error, setError] = useState("");
  const [refreshing, setRefreshing] = useState(false);
  const [refreshMsg, setRefreshMsg] = useState("");

  const load = useCallback(async () => {
    try {
      setRows(await contentPerformance());
      setSummary(await performanceSummary());
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "加载失败");
    }
  }, []);

  async function handleRefresh() {
    setRefreshing(true);
    setRefreshMsg("");
    try {
      const r = await refreshMetrics();
      setRefreshMsg(
        r.devices > 0
          ? `已通知 ${r.devices} 台在线且已登录的设备更新数据，请保持手机空闲，约 1–2 分钟后自动刷新`
          : "当前没有在线且已登录的账号，暂时无法更新数据",
      );
    } catch (reason) {
      setRefreshMsg(reason instanceof Error ? reason.message : "更新失败");
    } finally {
      setRefreshing(false);
    }
  }

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), 10000);
    return () => window.clearInterval(timer);
  }, [load]);

  const douyin = rows.filter((r) => r.platform === "douyin");
  const xhs = rows.filter((r) => r.platform === "xhs");
  const totalViews = rows.reduce((s, r) => s + r.views, 0);
  const totalEng = rows.reduce((s, r) => s + r.engagement, 0);

  return (
    <div className="catalog-page page-enter">
      <header className="catalog-hero history-hero">
        <div>
          <span>效果</span>
          <h2>哪些内容真的被看见了。</h2>
          <p>
            每天更新已发内容的播放、点赞、收藏和评论，抖音和小红书分开排名。
          </p>
        </div>
        <div className="perf-hero-aside">
          <button
            type="button"
            className="perf-refresh"
            onClick={() => void handleRefresh()}
            disabled={refreshing}
          >
            <RefreshCw size={14} className={refreshing ? "spin" : ""} />
            {refreshing ? "更新中…" : "更新数据"}
          </button>
          <div className="hero-stats">
            <div className="hero-stat">
              {/* 这里数的是「回采到数据的内容」，不是已发布总数。以前标成「已发布」，
                  加上后端写死只返回 100 条，看板就永远显示 100 篇不变。 */}
              <b>{rows.length}</b>
              <small>已统计内容</small>
            </div>
            <div className="hero-stat">
              <b>{fmtNum(totalViews)}</b>
              <small>累计阅读</small>
            </div>
            <div className="hero-stat accent">
              <b>{fmtNum(totalEng)}</b>
              <small>总互动</small>
            </div>
          </div>
        </div>
      </header>
      {refreshMsg ? <div className="perf-refresh-note">{refreshMsg}</div> : null}

      {rows.length ? (
        <div className="perf-boards">
          <PerfBoard
            title="抖音榜"
            platform="douyin"
            rows={douyin}
            summary={summary.douyin}
          />
          <PerfBoard
            title="小红书榜"
            platform="xhs"
            rows={xhs}
            summary={summary.xhs}
          />
        </div>
      ) : (
        <div className="catalog-empty compact">
          <BarChart3 size={36} strokeWidth={1.1} />
          <h3>还没有数据</h3>
          <p>
            账号发布内容后，每天会自动更新阅读、点赞等数据并显示在这里。
            {error ? ` （${error}）` : ""}
          </p>
        </div>
      )}
    </div>
  );
}

function PerfBoard({
  title,
  platform,
  rows,
  summary,
}: {
  title: string;
  platform: string;
  rows: PerformanceRow[];
  summary?: TypeSummary;
}) {
  const paged = usePaged(rows, 6);
  const boardViews = rows.reduce((s, r) => s + r.views, 0);
  const boardEng = rows.reduce((s, r) => s + r.engagement, 0);
  // 「二选一」是唯一被实测证明稳定更好的写法，而它一直是我们写得最少的
  // 一类。榜上不把这句话说出来，运营看一屏卡片也得不出这个结论。
  // ⚠ 倍数由**后端在全量上**算好（summary prop），这里绝不自己拿 rows 算 ——
  // rows 是先按互动排序再截断的，在它上面算倍数得到的是选样偏差。
  return (
    <section className={`perf-board board-${platform}`}>
      <header className="perf-board-head">
        <span className={`platform-badge plat-${platform}`}>
          {platformLabel(platform)}
        </span>
        <h3>{title}</h3>
        <div className="perf-board-stats">
          <div className="perf-bstat">
            <b>{rows.length}</b>
            <small>条</small>
          </div>
          <div className="perf-bstat">
            <b>{fmtNum(boardViews)}</b>
            <small>阅读</small>
          </div>
          <div className="perf-bstat accent">
            <b>{fmtNum(boardEng)}</b>
            <small>互动</small>
          </div>
        </div>
      </header>

      {summary ? (
        <p className="perf-insight">
          <b>「二选一」写法</b>的评论中位数是其余写法的
          <b> {summary.ratio} 倍</b>（{summary.best_median_comments} 比{" "}
          {summary.rest_median_comments}），而它只占已发内容的{" "}
          <b>{summary.best_share}%</b>。
          <em>
            就是给两个选项让人替他选，比如「南山还是龙华」。
            按全部 {summary.best_n + summary.rest_n} 条已发内容算，不是按这一屏。
          </em>
        </p>
      ) : null}

      {rows.length ? (
        <>
          <div className="perf-cards">
            {paged.slice.map((r, i) => {
              const rank = paged.page * 6 + i + 1;
              return (
              <article className="perf-card" key={`${r.content_id}-${i}`}>
                <div className="perf-card-head">
                  <b className={`perf-rank${rank <= 3 ? ` rank-${rank}` : ""}`}>
                    {rank}
                  </b>
                  <strong className="perf-cover">
                    {r.cover_title || r.title || "（无标题）"}
                  </strong>
                  {r.captured_at ? (
                    <time className="perf-time">{fmtDate(r.captured_at)}</time>
                  ) : null}
                </div>
                {r.content_type ? (
                  <div className="perf-kind">
                    <span
                      className={`perf-tier${
                        r.tier === "二选一" ? " is-best" : ""
                      }`}
                    >
                      {r.content_type}
                    </span>
                  </div>
                ) : null}
                {r.account_nickname || r.published_at ? (
                  <div className="perf-origin">
                    {r.account_nickname ? (
                      <span className="perf-origin-who">
                        {r.account_nickname}
                        {r.city ? <em>{r.city}</em> : null}
                      </span>
                    ) : null}
                    {r.published_at ? (
                      <span className="perf-origin-when">
                        {fmtDate(r.published_at)} 发布
                        {typeof r.age_days === "number" ? (
                          <em>{r.age_days === 0 ? "今天" : `${r.age_days} 天`}</em>
                        ) : null}
                      </span>
                    ) : null}
                  </div>
                ) : null}
                {r.body ? <p className="perf-body">{r.body}</p> : null}
                {r.topics && r.topics.length ? (
                  <div className="perf-topics">
                    {r.topics.slice(0, 6).map((t) => (
                      <span key={t} className="perf-topic">
                        #{t}
                      </span>
                    ))}
                  </div>
                ) : null}
                <div className="perf-metrics">
                  <span className="perf-metric perf-eng">
                    <b>{r.engagement}</b>
                    <small>互动</small>
                  </span>
                  {/* 评论排在阅读前面：它是我们判断内容好坏的主指标
                      （头部内容评论数常年高于点赞，见效果榜任意一屏），
                      阅读量是对外汇报口径，放次位。 */}
                  <span className="perf-metric">
                    <b><MessageCircle size={14} /> {r.comments}</b>
                    <small>评论</small>
                  </span>
                  <span className="perf-metric">
                    <b><Eye size={14} /> {r.views}</b>
                    <small>阅读</small>
                  </span>
                  <span className="perf-metric">
                    <b><Heart size={14} /> {r.likes}</b>
                    <small>点赞</small>
                  </span>
                  <span className="perf-metric">
                    <b><Star size={14} /> {r.collects}</b>
                    <small>收藏</small>
                  </span>
                </div>
              </article>
              );
            })}
          </div>
          <Pager
            page={paged.page}
            pageCount={paged.pageCount}
            total={paged.total}
            onChange={paged.setPage}
          />
        </>
      ) : (
        <div className="perf-board-empty">暂无{title}数据</div>
      )}
    </section>
  );
}
