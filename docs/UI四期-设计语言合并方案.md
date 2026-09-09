# UI 四期：两套设计语言的合并方案

> 产出方式：23 个 agent 并行盘点 + 四个独立方案对抗评审。盘点共 229 条，
> 全部带 `file:line` 证据。四个方案均分 5.7~6.3，没有一个是压倒性的 ——
> 所以定稿是**取骨架 + 嫁接**，不是选一个照抄。
>
> ⚠ 下面这份是**待拍板稿**。第 7 节有四件事需要人来定，不要替它做主。

## 动手前先看：我自己复核过的三条

**① 901–1244px 之间，设备详情右栏被裁——真的，但幅度比原文小。**
原文写「右边详情栏是被 `overflow:hidden` 直接裁掉了，不是出滚动条，是够不着」，
读起来像整个详情栏没了。我在本地量了：

| 视口 | 右栏右沿 | 越界 | 能不能横向滚回来 |
|---|---|---|---|
| 1100px | 1136 | **36px** | 不能（`.app-shell{overflow:hidden}` styles.css:276） |
| 1200px | 1216 | **16px** | 不能 |
| 1245px | — | 0（-29 余量） | — |

**裁掉的是右边缘 16~36px，不是整栏。** 空态下被裁的只有 `.detail-placeholder`
的右边框。但 `overflow:hidden` 意味着这一条永远拿不回来 —— 真实任务详情里
如果有右对齐的操作按钮，就是够不着。**是缺陷，不是灾难**，按缺陷排。
1366 笔记本开 125% 缩放（有效 ~1093 CSS px）落在区间里。

**② 「去掉硬投影能提高密度」是假的。** `box-shadow` 画在 border box 外，
不参与布局。删掉 8px 硬影，`.kpi-strip`/`.account-grid`/`.device-grid`
三个 gap 一个不变，列数一列不多。四份提案里三份把红利押在这，全是虚的。
真正的密度杠杆是那张 min-height 表（`.history-table > article` 90px 等）。

**③ 语言A的边是 1px 不是 2px。** `2px solid var(--ink)` 全站只有 2 条
（955 / 2152）。「A 太重」这个印象本身就是错的。

---

# 四期定稿：两套设计语言的处置

写给拍板和排期的人。所有数字我自己在 `frontend/src/styles.css`（6008 行）和 30 个 tsx 上重新 grep 过，四份提案和评审里被证伪的论据我在第 1 节先清掉——那几条正是最容易拿来当理由的。

---

## 0. 一句话结论

**选「纸面分级」（方案 C）作为规则骨架，但把它的排期整个翻过来：先交付运营每天在撞的四件事和 B 自己的收敛，形状统一放后半程。四期的目标不是「一套语言」，是「两层语言 + 一条 grep 能验的判据」。**

理由三条，都是数字：

1. **乱的是 B 不是 A。** 语言 A 的边全站 10 处，全是 `1px solid var(--ink)`（styles.css:667/767/785/888/1873/2229/2344/2634/2864/4019），硬投影 16 条全是同一族公式 `Npx Npx 0`，语义连续（静止 5 → 抬起 8 → 卡片 12 → 弹窗 18/20）。语言 B 那边：9px 一个值挂三个令牌名（`--r-ctl` 15 + `--r-c10` 16 + `--in-r` 10 = 41 处），`--line`(65) 与 `--line-strong`(44) 五五开且无规则，22 个容器摊出 11 种配方。**「统一到 B」的真实成本不在改 A，在先把 B 自己从 11 种配方里收敛出来**——这活不管选哪个方向都得干，那就先干它。
2. **「统一到 A」踩不起。** 全站方角后，`.msg-chip` 这类 13 个纯静态 `<span>` 徽标会和真按钮长得一样；引入任何第三方日期/图表组件都要额外改一遍皮。而「刻线」方案把层级押在 `--paper-deep`/`--paper`/`--white` 三档底色上，实测相邻两档只有 **1.105:1 / 1.11:1**，总跨度 1.227:1——去掉投影后这 1.1:1 同时要承担「这是一层」「鼠标在这上面」「这条被压下去了」三种信息，在运营那台外接屏上就是同一个颜色。不能上。
3. **C 是四个里唯一不用维护白名单的。** 「一张封闭清单」三个月后必然膨胀成第二份混乱；「看这个元素坐在哪一层」是新人自己能答的。

---

## 1. 先清三个被反复引用、但站不住的论据

拍板前必须知道这三条是假的，否则会拿错理由买单。

**① 去投影不换密度。** `box-shadow` 画在 border box 外面，不参与布局。`.kpi-strip { gap: var(--sp-c18) }`(styles.css:2338) = 16px、`.account-grid` 同(2626)、`.device-grid { gap: 20px }`(1864) —— 删掉 8px 硬影这三个数一个不变，`.account-grid` 的 `minmax(260px,1fr)` 也不变，**列数一列不多**。四份提案里有三份把「密度红利」押在这一步，全是虚的。

真正的密度杠杆我列在这，一个都没在四份提案的改动清单里：

| 位置 | 现值 | 消费面 |
|---|---|---|
| `.section-heading { min-height: 62px }` styles.css:950 | 62px | 11 处 |
| `.history-table > article { min-height: 90px }` 2251 | 90px | 归档记录每一行 |
| `.device-card { min-height: 250px }` 1872 | 250px | 设备列表全部 |
| `.button { min-height: 42px }` 641 | 42px | 66 处 |
| `--topbar-h: 94px` 31 | 94px | 每一页 |
| `.detail-placeholder { min-height: 590px }` 1224 | 590px | 任务详情空位 |

**② 卡片 hover 不抖。** `.device-card-clickable:hover`(2033-2036) 和 `.account-card:hover`(2641-2644) 只改 `background` 和 `box-shadow`，**没有 transform**；2030/2638 那两行 `transition: transform` 是空转。全站真正带位移的 hover 只有两条：`.button:hover { transform: translateY(-2px) }`（**styles.css:653，基类，66 处全吃**）和 `.schedule-btn:hover`(2826)。要治抖动，改的是 653 一行，跟卡片没关系。

**③ `.field` 改框式不涨高度。** styles.css:241 有全局 `* { box-sizing: border-box }`，4236-4239 已经写死 `.field input, .field select { height: var(--ctl-h-md); padding: 0 11px }`。从「只有 border-bottom」改成四边 1px，高度变化 **0px**，35 处消费点里只有 `.field textarea`(4243) 会多约 1px。这一条被两份提案标成「最大风险、要单独 commit 单独验收」，实际是半天的活。

顺带修正三条前提：**A 的边是 1px 不是 2px**（`2px solid var(--ink)` 全站只有 955 和 2152 两条）；`box-shadow` grep 到 42 行里含 2 行注释(193/194) + 4 行 `transition:` + 1 行 `none`(5948)，**真实声明 35 条**；`--r-c10` 注释写 21 处(styles.css:114)、实测 16 处，`--shadow` 注释说「0 24px 70px、次序反了」(120-121)，实际值是 `0 1px 2px + 0 8px 24px`(30)，二期已经修好——照这条注释做决策会白改一轮。

---

## 2. 分层规则：三问，10 秒出答案

写进 styles.css 顶部，取代现在 105-134 那段。**规则只管形状和层次，不管颜色**（颜色单列在 2.3）。

### 2.1 判定流程

**问 1：能点 / 能输 / 能选吗？** → **控件**：`--r-ctl` + `--bd-ctl` + `--ctl-h-sm|md|lg` + 无投影。
　例外只有两条，都显式豁免：**深色面上的控件**走 `--line-on-dark*`（侧栏 12 项导航 + 品牌区，ink 边在 #191813 上是隐形的）；**印章白名单**（见 2.2）。

**问 2：是个「面」吗？**（有自己的边或底，用来装东西）→ 看它坐在哪：
- 直接坐在页面底（`--paper-deep`）或遮罩上 → **一级面**：`--r-0` + `--bd-face-1`（ink）+ 无投影
- 装在另一个面里 → **二级面**：`--r-card` + `--bd-face-2`（line）+ 无投影
- 浮层 → 弹窗 `--r-0` + `--bd-face-1` + `--elev-modal`；下拉/通知 `--r-card` + `--bd-ctl` + `--elev-pop`

**问 3：既不是控件也不是面？** → 线走 `--rule-major`（页级，2px ink）或 `--rule-minor`（面内，1px line）；徽章走 `--r-pill`/`--r-chip` + 无投影；数字与全大写英文走 Oswald + 字距，**不参与形状规则**。

### 2.2 对照表

| 这是… | 判定 | 圆角 | 边 | 投影 | 高度 |
|---|---|---|---|---|---|
| 按钮/输入/下拉/开关/分段/图标按钮 | 控件 | `--r-ctl` | `--bd-ctl` | none | `--ctl-h-sm/md/lg` |
| 深色面上的控件（侧栏 12 项导航、品牌区） | **豁免** | `--r-0` | `--line-on-dark-soft` | none | 48px（现值） |
| 顶栏主操作 + 各弹窗底栏主操作（**印章白名单，共 8 处**） | 印章 | `--r-0` | 0（coral 实底） | `--elev-stamp` | `--ctl-h-lg` |
| 页面底上的分区块、KPI/设备/账号卡、消息卡、榜单板 | 一级面 | `--r-0` | `--bd-face-1` | **none** | — |
| 装在一级面里的行、子卡、时段块 | 二级面 | `--r-card` | `--bd-face-2` | **none** | — |
| 弹窗外壳（4 个 → 1 个配方） | 浮层 A | `--r-0` | `--bd-face-1` | `--elev-modal` | — |
| 下拉菜单 / 通知面板 / 气泡 / 灯箱 | 浮层 B | `--r-card` | `--bd-ctl` | `--elev-pop` | — |
| 页级小节标题下线、tab 基线、ink 实底表头 | 线 | — | `--rule-major` | — | — |
| 面内部的标题线 | 线 | — | `--rule-minor` | — | — |
| 状态/平台徽章（不可点） | 徽章 | `--r-pill`/`--r-chip` | 无或 1px | none | — |
| 状态环 / 吸顶发丝线（5 处：293/530/1937/4364/5274） | **第三族，不动** | — | — | 保留 | — |

⚠ **5274 是三期表头吸顶那条 `inset 0 -1px 0`，动它等于三期返工。**

### 2.3 `--line` 与 `--line-strong` 的分工（嫁接自 B 案，这是它最好的一条）

> **围一个面用 `--line`，围一个能点的东西用 `--line-strong`。**

今天 109 处细边在两个值之间五五开、同视图兄弟节点还分叉（`.supply-block` 5238 用 `--line`、`.supply-switch` 5322 用 `--line-strong`）。按这条规则回看，**那对「铁证」今天就是对的**——`.supply-switch` 有 `cursor:pointer`。所以第 4 步的真实工作量不是「109 处全改」，是**先按规则跑一遍分类、只改不合规的那部分**。开工前必须先出这份分类清单，不能拍。

### 2.4 选中态只有两种颜色（嫁接自 A 案）

- **ink 实心白字 = 我选了这一项**（tab / 分段 / 单选行 / 单选网格）
- **coral 实心 = 这个开着**（开关 on）+ 主操作按钮底色 + 告警

绿(`.active-toggle` 2439)、cyan(`.auto-toggle` 2802)、amber(`.content-publishing-note` 3700) 退回状态语义。

**为什么选中用 ink 不用 coral**：白字压 `--ink` #171611 约 **18:1**，压 `--coral` #f24f3d **3.52:1**（我算的，13-14px/700 过不了 AA 4.5）。这个项目的注释自己是量对比度的（styles.css:12-16 记着二期把 `--muted` 从 3.68 改到 5.27，说这是「二期里唯一运营一眼能看出来的变化」）。拿 3.5:1 做全站主选中色，跟这套纪律正面打架。**这条不留给拍板，直接定。**

---

## 3. 令牌层改动

可直接贴进 `:root`（styles.css:230 的 `}` 之前——注意不是 236，236 在 `:focus-visible` 规则体内）。

```css
  /* ══ 四期·纸面分级：形状与层次的唯一判据 ══════════════════════════
     一级面（坐在页面底或遮罩上）= --r-0 + ink 边；
     二级面（装在别的面里）      = --r-card + line 边；
     控件（能点/能输/能选）      = --r-ctl，永远不是方角；深色面上例外。
     投影只有 4 个位置：印章按钮、弹窗、下拉浮层、通知浮层。其余全站为 0。
     判不出来的一律按二级面写。 */

  /* ── 形：4 档 + 2 个形状语义 ── */
  --r-0:     0;      /* 一级面 + 弹窗外壳。**从此必须被引用**，不再靠「没写
                        border-radius」表达 —— 那 10 个方角面 grep 不到，
                        任何一次性刷圆角的 sed 会静默改掉第一代语言 */
  --r-ctl:   9px;    /* 控件唯一档 */
  --r-card: 14px;    /* 二级面 + 浮层 B 唯一档 */
  --r-chip:  6px;
  --r-pill: 999px;
  --r-dot:   50%;
  --r-c10: var(--r-ctl);   /* @deprecated 步 4 末删，16 处 */
  --in-r:  var(--r-ctl);   /* @deprecated 步 4 末删，10 处 */
  /* 删 --r-panel(18px)：现有 9 处里 7 处是一级面（改 --r-0），
     .wizard(4930) 与 .renew-box(4804) 是弹窗（同样 --r-0） */

  /* ── 边：4 档，按「这是什么东西」命名，不按颜色命名（嫁接自 A 案）── */
  --bd-face-1: 1px solid var(--ink);          /* 一级面。A 的边一直是 1px 不是 2px */
  --bd-face-2: 1px solid var(--line);         /* 二级面 */
  --bd-ctl:    1px solid var(--line-strong);  /* 控件（= 旧 --in-bd） */
  --in-bd: var(--bd-ctl);                     /* 别名保留，14 处 */
  --rule-major: 2px solid var(--ink);         /* 页级标题线 + seg 基线 + ink 表头下沿 */
  --rule-minor: 1px solid var(--line);        /* 面内标题线 */

  /* ── 影：5 个名字，4 个位置。全站 35 条真实声明 → 目标 ~18 条 ── */
  --elev-0:        none;
  --elev-stamp:    5px 5px 0 var(--ink);            /* 印章静止（原 --elev-print-1） */
  --elev-stamp-up: 8px 8px 0 var(--ink);            /* 印章 hover（原 -print-2） */
  --elev-modal:   18px 18px 0 rgba(0, 0, 0, 0.22);  /* 弹窗：20px(4021) 与 18px(2866) 合一 */
  --elev-pop:      0 14px 34px rgba(0, 0, 0, 0.16); /* 下拉/通知/气泡/灯箱 */
  /* 删：--elev-print-card / -lift / -modal / --elev-flat / --scrim（全部 0 引用）
     --shadow 保留定义，消费点归零（现有 8 处：1256/4466/4484/4592/4644/4798/4804/5000） */

  /* ── 选中：全站唯一表达 ── */
  --sel-bg:  var(--ink);
  --sel-fg:  #fff;
  --sel-count-bg:    rgba(23, 22, 17, 0.08);
  --sel-count-bg-on: rgba(255, 255, 255, 0.26);
  --sel-count-attn:  var(--coral);   /* 需人工介入 */

  /* ── 分段控件 ── */
  --seg-h: var(--ctl-h-lg);   /* 42px：它是版面不是控件 */

  /* ── 按钮三档：.button 的 min-height:42px 已经吃掉 3 次「做个小按钮」的
     尝试（.ai-preset-row 4677 / .library-warn 5418 / .review-actions 4826
     全都只改了 padding，改不动 min-height，注释在 201-203）。
     缺的不是覆盖，是档位。 */
  --btn-h-sm: var(--ctl-h-sm);   /* 32px：行内、警告条、表格操作列 */
  --btn-h-md: var(--ctl-h-md);   /* 38px：默认 */
  --btn-h-lg: var(--ctl-h-lg);   /* 42px：印章白名单 */
```

**三条必须同时处理的地雷**，四份提案都漏了至少一条：

1. **`styles.css:5849`**：`@media (max-width: 680px) { .button.primary { box-shadow: 3px 3px 0 var(--ink); } }`。步 5 把 `.button.primary` 从印章降成普通按钮之后，这条会在 680px 以下**给全部 29 个 primary 重新装上硬投影**，白名单当场失效。而它不会出现在回代 diff 里——因为这条规则本身没被改。必须显式写进步骤。
2. **`.account-abnormal`(2842-2844)** 的珊瑚硬投影 `8px 8px 0 rgba(242,79,61,.16)` 不在任何 `--elev-*` 里。卡片去影后它会是账号总览里唯一还凸出来的卡——**这恰好是运营唯一会去找的卡态**。要么显式保留（我的推荐：保留，它是信号不是装饰，写进注释说明理由），要么一起归零。**绝不能让它默认漏网。**
3. **`.notice`(809-823)** 是 `position: fixed` 的全站吐司，带 `9px 9px 0`。按规则它是浮层，走 `--elev-pop`。它是每次发布都会弹在最上面的东西，漏掉它等于「两套语言」以最显眼的方式活下来。

另外两条硬投影 `890`(`.overview-strip`) 和 `1725`(`.phone-frame`) 都是**死代码**（tsx 零命中，我 grep 过），步 0 删掉，硬投影从 16 降到 14。**`.phone-frame` 不要写进规则书当「永久豁免」**——那是一个已经没人用的选择器。

---

## 4. 子页面 tab 改造规格

这是用户单独点名的一件事，也是全案业务代价最高的一处。

### 4.1 为什么它值一整步

设备详情的「内容发布 / 群消息」（DeviceTasksView.tsx:176-189）是全站**唯一还活着的真 tab**。它身上叠着四个漏斗：默认落「内容发布」→ 默认落 `platforms[0]` → 默认「全部」日期 → 四个轴上**没有一个告诉你另一边有多少条**。

而 `waiting_confirmation`（「需人工介入」）全前端只出现在 4 个文件：`DeviceTasksView.tsx:13/167`、`StatusBadge.tsx:7`、`TaskDetail.tsx:36/267/285`——全是「你已经点进那台设备、切对那个 tab、切对那个平台」之后才看得见的位置。`AlertBell.tsx:28-37` 的六种告警类型（device_offline / account_health / task_failed / task_failed_many / task_stuck / queue_backlog）**里面没有它**；侧栏 12 项无徽标。而 `backend/app/main.py:30` 的扫描循环每分钟把超时的 `waiting_confirmation` 自动判失败。

**翻译成运营的话：有人在等我点一下，我得先猜对设备、再猜对 tab、再猜对平台才看得见，猜慢了它自己失败。** tab 上那个珊瑚计数徽标是这条信号在界面上的第一个直接入口。

### 4.2 形态

tab 走**语言 A**——它不是控件（问 1 否）也不是面（问 2 否），是版面骨架（问 3）。三条现实理由：`.kind-tabs`(2148-2168) 今天已经是这个样子，推广它不用重画；2px ink 线是这套语言里唯一**帮**密度的品牌表达（靠对比不靠空间）；三期刚做完表头吸顶，tab 和表头必须是同一条线的语言。

⚠ 动手前先改一句注释：styles.css:109 把 `.kind-tabs` 列进「方角 + 实心 ink 边 + 硬偏移投影」，但它实际**没有投影、按钮也没有 ink 边**（2155-2168）。照那句注释「补齐」会给 tab 加上它从来没有过的硬投影。

```css
/* 分段 tab —— 全站唯一形态。用于：切换会换掉下面整块内容 */
.tabs {
  display: flex;
  align-items: flex-end;
  gap: 0;                            /* 相邻，靠基线连成一条 */
  margin-bottom: var(--sp-6);
  border-bottom: var(--rule-major);
  overflow-x: auto;                  /* 见 4.5：绝不 wrap */
  flex-wrap: nowrap;
  scrollbar-width: none;
}
.tabs::-webkit-scrollbar { display: none; }

.tabs button {
  flex: 0 0 auto;
  height: var(--seg-h);              /* 42px */
  padding: 0 var(--sp-6);
  border: 0;
  border-radius: var(--r-0);         /* 显式方角，挡 sed */
  background: transparent;
  color: var(--muted);
  font-size: var(--fs-read);         /* 14px —— 二期刚抬上去的，不许降到 --fs-body */
  font-weight: 700;
  white-space: nowrap;
  cursor: pointer;
  transition: background var(--dur-fast) var(--ease), color var(--dur-fast) var(--ease);
}
/* 今天完全没有 hover：非激活 tab 是透明底 + muted 灰字 + 零反馈，
   长得跟一个被禁用的标签一模一样。这条是修那个。 */
.tabs button:hover:not([aria-selected="true"]) {
  color: var(--ink);
  background: rgba(23, 22, 17, 0.05);
}
.tabs button[aria-selected="true"] { color: var(--sel-fg); background: var(--sel-bg); }
/* 焦点环沿用三期的全局 :focus-visible（styles.css:234-236，:where() 特异性 0）。
   这里不写 outline 去盖它；但 ink 实心块上 2px ink 环是隐形的，选中项单独反色： */
.tabs button[aria-selected="true"]:focus-visible {
  outline: var(--focus-ring-invert);   /* 197 已存在，侧栏在用 */
  outline-offset: -4px;
}

/* ── 计数徽标：必选，不是可选 ── */
.tabs button i {
  display: inline-block;
  min-width: 18px; height: 18px;
  margin-left: var(--sp-4);
  padding: 0 var(--sp-3);
  border-radius: var(--r-pill);
  background: var(--sel-count-bg);
  color: var(--ink-soft);
  font-family: "Oswald", "Noto Sans SC", "Microsoft YaHei", sans-serif;
  font-size: var(--fs-micro);
  font-style: normal; font-weight: 500; line-height: 18px; text-align: center;
}
.tabs button[aria-selected="true"] i { background: var(--sel-count-bg-on); color: var(--sel-fg); }
.tabs button i.zero { opacity: 0.45; }                              /* 0 条也显示，不许藏 */
.tabs button i.attn { background: var(--sel-count-attn); color: #fff; opacity: 1; }
```

### 4.3 计数徽标：强制，不是可选

`<Tabs>` 的 `items` 里 `count` 必填，不填开发期 `console.warn`。

- 反差最刺眼的证据：**同一个文件往下 60 行**，状态分组表头就是标准的「名字 + 数量」写法（DeviceTasksView.tsx:236-239 的 `<span>{group.label}</span><small>{items.length}</small>`，样式 styles.css:2124-2141），就在 `.kind-tabs`(2148) 上面 24 行。同一套模式隔着 24 行没用上。
- **数据不是「现成」的**：`deviceTasks`(DeviceTasksView.tsx:83-95) 已经**同时**按 kind 和 platform 过滤（`if (kind === "broadcast") return isBroadcast;` 否则再比 platform），拿不出另一个 tab 的数。两个数都要从 `tasks` 重算，约 8-10 行。不多，但别当成零。
- 全站计数写法同时收成一种。今天四种：下拉菜单项右侧 `<i>` 槽（收起后完全看不见，Overview/内容库的城市计数就这么丢的）、拼进 label 字符串（BroadcastView.tsx:423「自动推送 12」）、写进按钮文字（Overview.tsx:360）、死代码 `.city-chip`(2590-2600) 里那个**唯一做全了选中态反色、却没人用**的槽位。四期把 `.city-chip` 那套抄进 `.tabs button i` 然后删源。

**哪几个 tab 挂徽标**：全部。`.tabs` 组件不接受无计数的项。第一批 6 个调用点见 4.7。

### 4.4 位置

- 永远贴在它所控制的那块内容正上方，是那块的第一个子元素，左沿对齐内容左沿。
- 不进 hero，不和筛选条同行（今天 `.kind-tabs` 和 `.device-date-bar` 上下两行是对的，保持）。
- **一个视图只允许一排 tab。** 第二根轴（平台/时间/城市）降级成下拉，不许出现两条黑基线。
- 吸顶时 tab 留在吸顶区内，但吸顶区要瘦身：今天 `.device-tasks-sticky`(3900-3906) 从 `--topbar-h`(94px) 开始，底下压着 head（返回 + eyebrow + h2 设备名 + 设备码/在线 + 账号行）+ 可能的 offline-banner + tab + `.device-date-bar`，粗估固定占 350px，1280×800 上一半屏在讲「这是哪台手机」。**吸顶只留「返回 + 设备名 + tab + 日期」**，设备码/在线状态/账号昵称滚走。

### 4.5 窄屏

**tab 永不换行，改横向滚动**。`.device-date-bar` 今天是 `flex-wrap: wrap`(2170-2179)，一换行吸顶头就长高，直接吃掉本就不够的列表高度。

```css
@media (max-width: 900px) {
  .tabs button { height: var(--ctl-h-md); padding: 0 var(--sp-5); }
  /* 计数徽标不许在窄屏隐藏 —— 它才是窄屏最不该丢的东西 */
}
```

**但真正的窄屏事故不是 tab，是这个，必须单列一条排进步 1：**

`.app-shell` 侧栏 248px + `overflow: hidden`（styles.css:271-276）；`.device-tasks` 左右各 `--sp-page` 34px；`.workspace { minmax(330px,.72fr) minmax(580px,1.5fr); gap: var(--sp-c22)=24px }`(943-947) → 需 330+580+24+68+248 = **1250px**。`@media(max-width:1180px)` 降到 `330px minmax(500px,1fr)`(5607-5609) → 仍需 1170。`@media(max-width:900px)` 才单列(5685-5687)。

**所以 901–1169px 和 1181–1249px 两段宽度，右边详情栏是被 `overflow:hidden` 直接裁掉的——不是出滚动条，是够不着。** 1366 笔记本开 125% 缩放（有效 ~1093 CSS px）稳定落在第一段里。这是功能不可用，不是样式不好看，四份提案全把它写成 tab 规格的附录小点。

### 4.6 键盘可达 + 状态保留

```tsx
<div className="tabs" role="tablist" aria-label="任务类型">
  <button role="tab" aria-selected={on} aria-controls={panelId} id={tabId}
          tabIndex={on ? 0 : -1} onKeyDown={左右方向键 / Home / End}>
    内容发布<i className={cnt ? (attn ? "attn" : "") : "zero"}>{cnt}</i>
  </button>
</div>
<div role="tabpanel" id={panelId} aria-labelledby={tabId}>…</div>
```

全仓 `role="tablist" | aria-selected | role="radiogroup" | aria-pressed` 在 tsx 里 **0 命中**（我 grep 过）。唯一有 ARIA 的是 Select（`aria-haspopup`/`aria-expanded` 226-227 + `role="listbox"` 244，但菜单项缺 `role="option"`，连这处也不完整）。抽组件时一次做进去，边际成本近乎零；等 48 组切换各写各的就再也补不上。

**切换后筛选状态保留：`persistKey` 必填。** `lib/usePersistentState.ts` 已经在跑（Overview 3 个 key + ContentLibrary 4 个 key，sessionStorage，前缀 `ui:`）。`DeviceTasksView.tsx:72-77` 的 `kind`/`platform`/`dateFilter` 是全站唯一漏掉的三个裸 useState，而这个组件在 `App.tsx:674-678` 是条件渲染的——点侧栏任何一项就卸载，回来全部重置。3 行改动，key 带 `device.id`。

**说清边界**：这只解决「切页回来」。**四期不做路由**——全仓 `react-router` / `window.location` / `pushState` / `hashchange` 零命中，`App.tsx:154` 的 view 是裸 useState，刷新回账号总览、链接发不出去、浏览器后退等于退出应用。这是五期的事，但步 6 抽组件时把 value 的读写口子留出来，将来接路由不用再改一遍调用点。

### 4.7 第一批 6 个调用点

1. `DeviceTasksView.tsx:176` 内容发布 N / 群消息 N —— 带 `.attn` 徽标
2. `DeviceTasksView.tsx:193` 平台 —— **去掉 `kind === "post" && platforms.length > 1` 的条件渲染**，改成 `disabled`。今天点「群消息」的一瞬间平台下拉整个消失，右边时间下拉往左跳一整个控件宽，每天撞几十次
3. `BatchScheduleModal.tsx:208` `.batch-platform .chip` → tabs（coral 实心改 ink 实心），带「可排期账号数」计数
4. `ImagePickerModal.tsx:63` `.image-cat` —— **按判据它其实该留下拉**：`categories`(ImagePickerModal.tsx:37) 是运行时派生、长度未知，且它过滤的是同一张网格。判据是「≤4 且切换换掉整块内容 → tabs；≥5 或只是过滤当前列表 → 下拉」。这一处放进「先统一样式、形态待定」，不硬迁
5. `AutoBroadcastView.tsx:535` same / rotate —— **优先级最高**：两个选项要并排比较，今天做成 `variant="field"` 整宽下拉，下面还跟一段随值变的 `.field-note`(544-548) 解释差别
6. `Overview.tsx:354` `.active-toggle` → tabs 两项带计数。⚠ **同步保留 `Overview.tsx:363` 那句「另有 N 个账号最近两天没有发布，点击查看」的信息**——它说的是「我没看到什么」，tab 说的是「我正在看什么」，两者不能互相替代。默认 `activeOnly=true`(123)，一进首页就藏号，界面上必须一直有个地方写着藏了几个。

**不迁**：`CreateTaskModal.tsx:259` 与 `TokenManagementView.tsx:148`（表单枚举字段，在填值不是在切视图）；`PerformanceView.tsx:116-117` 效果榜并排双榜——并排是这个维度上唯一不需要运营做选择的答案，它是对的，不要为了统一收进来。

**顺手修的两条**：`DeviceTasksView.tsx:270` 的空态写「该设备暂无任务」，实际含义是「该设备 + 当前 tab + 当前平台 + 当前日期下无任务」，提示语里连「切 tab」都没提——tab 上有了数，这句自动不撒谎，两条是同一个修法。

---

## 5. 分期落地：8 步

照这个项目已有的节奏（一期只改名不改值、二期并档、三期换形态）。每步一个 commit，每步收尾跑：

```
git show HEAD~1:frontend/src/styles.css | python tools/resolve_css.py /dev/stdin > before.txt
python tools/resolve_css.py frontend/src/styles.css > after.txt
diff before.txt after.txt   # 把「本步应该出现几行差异、哪几行」写进 commit message
```

工具实测可用：`python tools/resolve_css.py frontend/src/styles.css` 跑通，输出 3840 条声明，`grep -c "var(--"` 回代后为 0。**但要知道它的边界**：它按 `}` 切块，`@media` 前缀只挂在每个 block 的第一条规则上，所以 5849 那条在输出里和基础层的 `.button.primary` 选择器字符串一样，diff 里分不出来——**5849 必须靠人工清单，不靠工具**。另外它会剥掉自定义属性声明本身并展开所有 `var()`，所以「只改名不改值」的正确产出是**空 diff**，不是「仅令牌名替换」。

---

**步 0 · 清场（半天，零像素）**
删 `components/TaskList.tsx` 整文件（102 行，全仓零 import）+ styles.css 的 `.queue-filter`(987-1007)、`.platform-tab(s)`(4409-4433)、`.city-chip`(2570-2600，先把 2594-2600 的徽标反色抄进 `.tabs button i`)、`.overview-strip`/`.overview-note`(**883**-935，不是 888)、`.date-quick`(**2181**-2202，不是 2188)、`.image-cell` 一族(**3415**-3460 左右，不是单行 3418)、`.phone-frame`(1713-1745)。
**合并 3 组重复选择器**：`.content-block`(3695 与 3836)、`.content-row`(3918 与 4994)、`.library-warn`(5219 与 5412)——四份提案没有一份发现这个，而步 2 的验收门恰恰是「计算值零差异」，同一个选择器被声明两遍会让 diff 读不了。顺手删 `.content-row` 4996/4997 那条自己盖自己的 `border-bottom`。
修 3 处过期注释（`--r-c10` 21→16、`--shadow` 次序、`--elev-flat` 70px）。
**必须第一步做**，不然后面会照着不存在的界面统一样式。删除区间按**选择器 grep 逐条进文件确认**，不许照行号做手术。
revert：`git revert`，界面零变化。

**步 1 · 运营红利（2 天，几乎不动 CSS）**
这一步是整份方案里唯一运营当天就能感到「变好用了」的部分，所以排在所有形状改动之前。
① tab 计数（含 `.attn`）+ 空态文案；② 平台下拉常驻改 `disabled`，横跳消失；③ `kind`/`platform`/`dateFilter` 换 `usePersistentState`；④ **901–1169px 内容被裁**：`.workspace` 的 900px 单列断点抬到 1250px（`@media(max-width:900px)` 是 5643-5722 共约 79 行，里面有 `.app-shell { grid-template-columns: 82px 1fr }`——**只抬 `.workspace` 那一条，不要整块抬**，否则 1093px 的机器上侧栏会塌成 82px 图标条）；⑤ 两个真 bug：`.schedule-pick`(2997-3006) 双层框、`AIStudioView.tsx:319` 的 `className="ai-field"` 挂在 `<input>` 自己身上而样式在后代选择器 `.ai-field input`(4503)；⑥ 删 `.button:hover`(653) 的 `transform: translateY(-2px)`——它是基类，66 处全吃，表格行里的小按钮也跟着跳。
revert：单 commit。这一步动 tsx，`resolve_css.py` 覆盖不到，**必须按 `verify-ui-locally` 那套本机起无鉴权环境把 6 个点点一遍**（tsc 通过 + 构建成功不算验证，这条已经吃过两次亏）。

**步 2 · 只改名不改值（1 天，零像素）**
新令牌全部落地；14 处硬投影字面量 → `--elev-stamp*`/`--elev-modal`；6 处手写柔投影 → `--elev-pop`；8 处 `var(--shadow)` 暂不动；26 处 `--r-c10`/`--in-r` 改写为 `--r-ctl`；**方角面显式写 `var(--r-0)`**。
方角清单**按选择器 grep 逐条进文件确认**，不预先写死数字——验收指标是「新增 N 行 `border-radius: 0`，N = 实际清点数」+「其余 diff 为 0」，不是「diff ≤ 4 行」。
⚠ 已知无法接令牌的 4 条，显式保留字面量并在旁边写理由：`.notice`(822，步 3 改 `--elev-pop`)、`.account-abnormal`(2844，告警信号)、`.image-lightbox img`(3384，0.5 alpha)、`.perf-rank.rank-1`(4608，琥珀辉光)。
**这一步不过关不许往下走**——后面每一步的 revert 都靠令牌，令牌没接上就没有 revert 机制。

**步 3 · 投影并档（1 天，第一次可见变化）**
`--elev-print-card`/`-lift` 归零（37 张卡 + hover）；8 处 `var(--shadow)` → 无影；`.notice` → `--elev-pop`；四种弹窗壳合到 `--elev-modal`(18px)；**5849 那条 media query 显式处理**。
`grep` 真实 box-shadow 声明应从 35 降到 ~18。
诚实告诉运营这一步换的是什么：**卡片变平、hover 不再胀，投影重新变成稀缺信号（只有弹窗和主按钮有），但一屏不会多放一张卡。**
revert：单 commit，改回几行令牌值。

**步 4 · B 自己收敛（3-4 天，A 一个字不动）**
先出 109 处细边的分类清单（按 2.3 规则跑一遍，标出「已合规 / 要改」），再动手；11 种容器配方 → 3 档；删 `--r-c10`/`--in-r`/`--r-panel` 三个令牌；补 4 处引用了 `--in-bd` 却漏写 `--in-r` 的输入框（2207/2926/3878/4342）。
**顺序硬约束：步 4 必须在步 5 之前。** 先把要迁进去的那套自己弄干净，再往里迁——反过来做的结果是把 A 的一致性换成 B 的混乱，而且到时候没人分得清一处不一致是「A 遗留」还是「B 本来就乱」。这和「先收间距再抬字号」是同一类纪律。
这是全案判断量最大、可验证性最低的一步（diff 会是几百行「预期内的改动」），排期给足。
revert：干净，全部改动在 B 的规则块内，与 A 无交集。

**步 5 · 面分两级 + 控件归一（3-4 天，拆 4 个 commit）**
- 5a 控件归一：`.button` 拆三档、`.icon-button`(700)/`.ghost-icon`(706)/`.notif-btn`(4858) 统一 38px、`.pager`/`.count-picker`/`.image-clear`/`.schedule-btn`/`.content-import-btn`/`.back-button`/`.detect-btn`/`.perf-refresh` 圆角化
- 5b 内容类面：`.content-block` / `.msg-card` / `.supply-block`
- 5c 数据卡：`.kpi` / `.device-card` / `.account-card`（⚠ 这三个**本来就是方角**，没写过 `border-radius`——5c 的实际改动只有边和投影，别拿「37 张卡变方角」去吓人）
- 5d AI / 效果榜 / 令牌页 / sheet 三兄弟 + `.login-card`（**唯一说不清属于哪派的面**：方角 + `--line-strong` 细边 + `0 18px 60px` 柔投影，必须显式拍板归哪边）
印章白名单落地：顶栏主操作(App.tsx:515) + 各弹窗底栏主操作，约 8 处，其余 58 处 `.button.primary/.danger` 走普通形态。⚠ `.button.stamp` 要在 tsx 调用点手打，**所以步 5 也动 tsx**。
revert：任一子 commit 可单独退。

**步 6 · Tabs 组件（2 天，动 tsx）**
新增 `components/Tabs.tsx`，把步 1 已经在跑的计数/persist/ARIA 收进组件，迁 5 个调用点，`.kind-tabs`(2148-2168) 退役。
因为步 1 已经把功能交付了，这一步纯粹是形态收口——**万一被打回，运营已经拿到的东西不用还回去**。这是步 1 前置的最大价值。

**步 7 · 真密度（独立，可延到五期）**
`.section-heading` min-height 62px（11 处）、`.history-table > article` 90px、`.device-card` 250px、`.button` 42→38、`--topbar-h` 94px。
`.history-table > article` 90→64 这一条：1080p 上一屏从 7 行到 10 行，是全案对运营价值最高的密度改动，**单独发、第一个发**。
⚠ 严格照「先收间距再抬字号」的顺序纪律，这一步不许和字号改动混在一个 commit。

---

## 6. 这个方案牺牲了什么

**1. 卡片的印刷感——这是最大的一笔付出。** 37 张卡（KPI 4 + 设备卡 + 账号卡）的 `8px 8px 0` 和 hover 的 `12px 12px 0` 全部删掉。首页会明显变平。品牌感从四簇压到三簇：侧栏品牌区(342-395/450-467)、印章按钮(约 8 处)、Oswald 排印(47 处声明)。**换回来的是 hover 不再胀、投影重新变成稀缺信号——不是密度。这笔交易是划算的，但它是「用品牌换秩序」，不要包装成双赢。**

**2. 「2px ink 版面线」这根柱子今天几乎是空的，四期把它救回来一半。** 全站 `2px solid var(--ink)` 只有两条：955(`.section-heading`) 和 2152(`.kind-tabs`)。而 `.section-heading` 的 11 个消费点里，**9 处在 `<section className="supply-block">`(白底 14px 圆角卡) 里、2 处在 `.broadcast-groups`/`.broadcast-records`(方角 paper 面板) 里，真正页级的只有 `Overview.tsx:315` 一处**。所以这根柱子不能当品牌资产卖。四期的处置：卡内 11 处降成 `--rule-minor`(1px)，页级保留 `--rule-major`，同时把 seg 基线接上去——消费者从 1 个变成 1 + 6 个调用点。**这是补救，不是「本来就很强」。**

**3. `.field` 稿纸式下划线输入框会消失（如果选做）。** styles.css:4222-4228，全站唯一显式 `border-radius: 0`，35 处消费。改完所有弹窗表单变成通用后台表单。成本已证实为零（高度不变），但排印上的第二个品牌表达没了。

**4. 规则管形状和层次，管不掉「同一件事四种长相」的全部。** 「抖音/小红书」四期后从四种收成两种（切内容用 tabs、填表用下拉），但城市筛选的 4 种选项算法（`r.account.city || "未分组"` / `i.city || "通用"` / 拆出 `value:"generic"` 分支 / 直接列）不动。

**5. 判定成本比白名单高一步。** 白名单是「Ctrl+F 一下，不在表上就是 B」；三问要先看这个元素坐在哪一层。换来的是新元素不用改白名单就能判、清单不会随时间膨胀成第二份混乱。

**6. 不解决的三件事，四期做完一条都还在**：无路由（48 处切换零 URL，刷新回账号总览、链接发不出去）；侧栏那个 coral 滑块（既不属 A 也不属 B 的第三种表达，本方案显式豁免它，等于承认它继续存在）；`.perf-boards` 并排双榜要不要改成切换（本方案的答案是不改）。

**7. 迁移期。** 步 3 到步 5 之间界面比今天更碎。生产是内网 8080 直连、运营整天开着。**步 5 的 4 个子 commit 必须同一天发完**，不要跨天。

---

## 7. 需要你拍的四件事

我给选项和推荐，不替你做审美判断。

**① 主按钮的 `5px 5px 0` 硬影，白名单内保不保？**
- A（推荐）：白名单 8 处保 5px，其余 58 处 `.button` 无影 + 三档高度。理由：它是运营每天真正摸到的品牌触点，也是唯一让「保存/发布/全部关闭」这类不可逆动作在视觉上有分量的手段；而这 8 处都不在网格里，不吃列表空间。
- B：全部降成 `--press: 0 2px 0 var(--ink)`（嫁接自「刻线」案）。保住一点压印感，只吃 2px 垂直。**如果你觉得 5px 太张扬，选 B 而不是选「全部无影」——两头空是最差的答案。**
- C：全部无影。最干净，但主操作和次操作只剩「coral 实底 + 4px 高度差」两个区分手段。

**② `.field` 下划线输入框改不改？**
- A（推荐）：改成框式。成本已证实为零（`box-sizing: border-box` + 高度已锁 38px），做完弹窗里「下划线 + 圆角框」相邻的问题才解决得掉。
- B：不改。那么四期的「表单一致」这一半交付不了，且这个问题永远解决不掉——没有中间答案。

**③ `.login-card`(4318-4327) 归哪边？**
方角（没写 radius）+ `--line-strong` 细边 + `0 18px 60px` 柔投影，全站唯一说不清属于哪派的面。它是新用户见到的第一屏。
- A（推荐）：归一级面（`--r-0` + `--bd-face-1` + `--elev-modal`），和弹窗同语言。
- B：归浮层 B（`--r-card` + `--elev-pop`）。
不能靠批量规则扫过去，必须显式定。

**④ 步 7（真密度）什么时候做？**
- A（推荐）：`.history-table > article` 90→64 单独抽出来，跟步 1 一起发。这是运营价值最高的单条改动（1080p 一屏从 7 行到 10 行），且和形状无关、零风险。其余 min-height 排到五期。
- B：整块留到五期。那么四期在运营眼里就是「换了个样子」——步 1 的功能修复能撑一部分，但撑不满。

---

## 8. 从落选方案嫁接了什么

| 嫁接自 | 嫁接了什么 | 为什么 |
|---|---|---|
| **A-印刷派** | 投影判据「这个元素一屏出现几次？≤2 且是不可逆动作或浮在别人上面 → 有影；>2 或在列表/网格里 → 零影」 | 把品味问题换成计数问题，新人能自己判。C 原案的「4 个位置白名单」判不了新元素 |
| **A-印刷派** | 选中态只有 ink 实心（选了哪一项）+ coral 实心（开着）两种颜色 | 绿/cyan/amber 三种选中表达退场；而且 ink 白字 18:1 vs coral 3.5:1，是这个项目自己的对比度纪律要求的 |
| **A-印刷派** | 边框令牌按「这是什么东西」命名（`--bd-face-1/-2/-ctl`），不按颜色命名 | 颜色名（`--line` vs `--line-strong`）永远吵不完，语义名一看就知道该用哪个 |
| **B-功能派** | 一句话分工：**围一个面用 `--line`，围一个能点的东西用 `--line-strong`** | 109 处细边今天五五开无规则。这是全案唯一一条能让 109 处争论一次性结束的话 |
| **B-功能派** | `.block-head`：卡内 11 处 2px ink 线降成 1px | 一条 2px 纯黑线横穿柔和圆角白卡，是自动发布/自动推送/自动生成三页最扎眼的一处，而这三页运营每天早晚各看一次 |
| **B-功能派** | tab 计数徽标的 `<i>` 槽 + 选中反色 + ARIA 完整规格 | 死代码 `.city-chip`(2590-2600) 是全站唯一把计数徽标做全了的实现，救活它、删掉源 |
| **刻线** | `--press: 0 2px 0 var(--ink)` 作为主按钮的中间答案（拍板选项 ①B） | 它是四份提案里唯一注意到「5px 硬影和无影之间还有第三个答案」的 |
| **刻线** | 「层级不能只靠底色」这个反面教训 | 它自己撞上了 1.105:1 的墙。C 之所以能去投影，正是因为一级面保留了 ink 边——**这是 C 和刻线的唯一结构差别，也是 C 能上而刻线不能上的原因**，值得写进注释 |

---

**一句话给排期**：步 0+1 一周（清场 + 运营红利，独立可发）→ 步 2+3 一周（令牌接线 + 投影并档）→ 步 4 一周（B 自己收敛，判断量最大）→ 步 5+6 一周半（面分级 + 组件）。步 7 单独排。全程改动面 100% 在 `styles.css` 一个文件加 6 个 tsx——30 个组件里 `borderRadius|boxShadow|border:` 内联样式实测为 **0**，7 处 `style={{}}` 全是布局与动画，`resolve_css.py` 的回代 diff 能覆盖除步 1/5a/6 之外的全部改动。

---

# 附：完整性批评（独立 agent，专职找这份方案漏了什么）

它抓到 20 个缺口，其中第 1 条是自指的 —— **方案自己定的「选中态只有两种
颜色」规则，八个步骤里没有任何一步实现它**。这类缺口比事实错误更危险：
读起来完整，做起来落空。

读代码核对完毕。方案覆盖面确实比四份提案都宽，但有实质缺口，按重要性排序：

---

**1. 「选中态只有两种颜色」这条规则没有任何一步实现——开关家族整个漏了。**
§2.4 宣布 绿/cyan/amber 退场，但步 5a 的控件清单是 `.button/.icon-button/.ghost-icon/.notif-btn/.pager/.count-picker/.image-clear/.schedule-btn/.content-import-btn/.back-button/.detect-btn/.perf-refresh` —— **`.auto-toggle`(styles.css:2763-2808，3 个调用点 AutoBroadcastView.tsx:618 / AutoPublishView.tsx:481 / SupplyView.tsx:277，每处 N 行)、`.supply-switch`(5316)、`.active-toggle`(2424)、`.content-publishing-note`(3700)、`.image-tile`(3287 coral 2px + 勾角标)、`.batch-platform .chip`(3048) 一个都不在任何一步里**。四期做完 cyan/绿/amber 原样活着，2.4 变成一句没人执行的话。补法：步 5 加一个 5e「二态开关归一」子 commit，明确列出这 6 个选择器 + 三处 `.auto-toggle` 调用点。

**2. 步 1⑥ + 步 3/5a 叠起来，58 个按钮 hover 零反馈。**
`.button` 全站唯一的 hover 就是 `styles.css:652-654` 那条 `transform: translateY(-2px)`——`.button.secondary`(667，36 处) 没有任何其它 hover 规则（grep `button:hover` 只有 652/662/678/712）。步 1⑥ 删 652，步 5a 把 58 个非印章按钮的 `box-shadow` 也拿掉，结果是这 58 个按钮鼠标划过去毫无反应。补法：删 652 的同一个 commit 里给 `.button:hover` 补底色/边色变化（secondary 用 `rgba(23,22,17,0.05)`，primary 用 coral-dark），否则步 1 是净负收益。

**3. `waiting_confirmation` 的入口只补了最后一跳。**
方案自己说这是「业务代价最高的一处」，但 tab 徽标只有在**已经进对设备**之后才看得见：`DeviceView.tsx:56-110` 的设备卡片没有待确认数、`App.tsx:451-468` 侧栏 12 项无徽标、`AlertBell.tsx:28-37` 的六种 kind 里仍然没有它，而 `backend/app/main.py:30` 会超时判失败。补法：步 1 里同时把 `waiting_confirmation` 计数挂到设备卡片和侧栏「设备」项上（后端 alerts 加一类是另一件事，可留五期，但设备卡片这个数前端现成数据就能算）。

**4. 步 0 的清场清单漏了 4 处死代码和一堆 media-query 孤儿。**
除方案列的之外，`.tasks-view`(879，tsx 零命中，还有 5855 的 680px 覆盖)、`.sidebar-index`(328 + 5657 + 5841)、`.form-modal`(仅出现在 5823 的媒体查询，FormModal.tsx:60 用的是 `schedule-modal`) 都是死的；删 `.overview-strip/.overview-note` 时必须连 **5598-5601 和 5862-5883** 那两段媒体查询里的 `.overview-strip / .overview-lead / .overview-stat.ink` 一起删，否则留下引用不存在选择器的 25 行。

**5. 「合并 3 组重复选择器」实际是 13 组，而且有两组不能合。**
基础层同名选择器实测 13 个：`.spin`(2453/4512)、`.filter-dd-menu`(2499/5064)、`.content-block`(3695/3836)、`.content-pub-filters`(3851/3870)、`.content-row`(3918/4994)、`.content-row:last-child`(3927/5004)、`.content-row-top`(3931/5007)、`.content-row-main strong`(3939/5013)、`.content-delete`(3989/5015)、`.notif-empty`(4909/4910)、`.filter-dd-search`(5021/5066)、`.library-warn`(5219/5412)、`.supply-table`(5249/5263)。**`.supply-table` 的第二份是三期吸顶的 `border-collapse: separate`（5263-5266，注释写明少一个条件就不生效），`.filter-dd-menu` 的第二份是 portal 后的 `position:fixed/z-index:90`（5056-5058）—— 这两组是有意的追加覆盖，合并等于三期返工。** 补法：步 0 只合并 `.content-*` 那一代际残留（3918-3989 vs 4977-5015），其余显式写进「不合并」清单。

**6. 步 0 和步 2 的「零像素 / 可 revert」不成立。**
① 合并 `.content-row` 两代规则牵连四对同名规则，其中 `.content-row-main p`(3968) 与 `.content-row-main > p`(4977) **特异性不同**，合并顺序错一次就是行内文字样式静默改变；而且 `resolve_css.py` 输出是有序行表，规则位置一动 diff 必然出现移动行，「界面零变化」这个验收门用它证明不了。② 步 2 声称零像素，却要把 `.modal` 的 `20px 20px 0`(4021) 映射到 `--elev-modal(18px)` —— 这是值变更不是改名。补法：步 2 先建 `--elev-modal-20` 保原值，20→18 的合并放进步 3；步 0 的验收门改成「删除类改动 diff 只减不增 + 合并类改动逐条人工比对」。

**7. tab 缺陷清单里的 dateFilter 双控件，8 个步骤没人认领。**
`DeviceTasksView.tsx:203-212`：`FilterDropdown`（三项：全部/今天/昨天）和裸 `<input type="date">` 写同一个 `dateFilter`，选了具体日期后下拉按钮显示 `2026-09-01` 而菜单里没有任何高亮项（Select.tsx:232 的兜底）。步 4 只给 2207 补了个 `--in-r`，语义问题和原生日期控件的浏览器 chrome 都没处理。同类漏网：`.dt24-time select`(styles.css:4847) 是原生 `<select>`，它展开的菜单永远不是 `.filter-dd-menu`。补法：步 1 里把下拉加一项「指定日期…」并把 date input 收进菜单，或反过来只留 date input + 两个快捷按钮。

**8. §4.4 写的「吸顶区瘦身」没有排进任何一步。**
`.device-tasks-sticky`(3900-3906) 顶着 head(2042-2047，返回+eyebrow+h2+设备码+`.dta-accounts`) + `.offline-banner`(2218) + tab + `.device-date-bar`(2170)，方案说「吸顶只留返回+设备名+tab+日期」，但步 1/6/7 的清单里都没有它。补法：并进步 6（Tabs 组件落地时一起改 sticky 的 DOM 分层），否则这条规格永远悬着。

**9. 「提示条 / 横幅」整类没有规则行。**
五种形态：`.error-banner`(735，方角 + coral 边，全站顶部)、`.offline-banner`(2218，方角 + amber 边)、`.schedule-warn`(2952，方角 + amber 边)、`.library-warn`(5219，**9px 圆角** + rgba amber)、`.notice`(810 toast)。§2.1 三问会把它们全判成「面」→ 一级面 → `--r-0` + ink 边，直接吃掉状态色语义。补法：§2.2 加一行「状态条：`--r-ctl` + 1px 状态色边 + 状态色底 + 无影」，并统一到 `--state-warn-bg/ink`（`.library-warn` 现在写的是 `rgba(255,176,32,0.14)` 字面量）。

**10. 「文字链按钮」整类没有规则行。**
`.linklike`(5474)、`.link-clear`(4853，两个调用点)、`.error-banner button`(747，border-bottom 下划线)、`.notice button`(870)、`.overview-hidden-note`、`.content-publishing-note` —— 六种低强度按钮。§3 只定义了三档按钮**高度**，没有「无框」档；机械套「控件 = `--r-ctl` + `--bd-ctl` + 高度」会给这六处纯文字加上框。补法：`--btn-h-*` 之外加一档 `.button.quiet`（无边无底、仅色彩与下划线），并把这 6 处收进去。

**11. 步 5 的四个子 commit 漏掉三个整页。**
5a 控件 / 5b 内容类面 / 5c 数据卡 / 5d AI+效果榜+令牌+sheet+login —— **归档记录 `.history-table`(2227，1px ink 边 + 2241 的 ink 实底表头)、群发页 `.broadcast-records`(3469) / `.record-item`(3482) / `.broadcast-compose`(3161) / `.broadcast-layout`(3154)、设备详情 `.device-tasks-sticky` / `.dta-account`(2096) / `.status-group-head`(2124) 一个都没出现**。这三页每天都在用。补法：5b 或新增 5e 显式列上，别靠「规则自己会盖到」。

**12. `.cover-shot` 被盘点点名为真混用，方案里一次都没出现。**
`styles.css:1400-1408`：`border: 3px solid var(--ink)` + `border-radius: var(--r-card)`，TaskDetail.tsx:163 在用（活的）。盘点明确说它没有 `.phone-frame` 那个「实物拟态」的理由，而方案反过来把已经死掉的 `.phone-frame` 写进了步 0。补法：拍板它归「二级面」（去掉 3px 改 `--bd-face-2`）还是「有意的相框」（写进豁免清单）。

**13. 遮罩三档没收，`--scrim` 选择了删而不是回代。**
`.modal-backdrop` = `rgba(17,16,12,0.68)` + `blur(8px)`(4009-4010)、`.renew-modal` = `rgba(23,22,17,0.45)` 无 blur(4803)、`.image-lightbox` = `rgba(0,0,0,0.82)`(3375)。`--scrim`(134) 的值恰好等于 `.renew-modal` 那个——它不是「没人用的死令牌」，是**回代漏了的令牌**。补法：步 2 把三处遮罩收进 `--scrim` + `--scrim-strong` 两档，别删。

**14. `.record-row` 展开态零视觉标记，方案没提。**
`styles.css:3486-3501` 只有 `:hover`；`BroadcastView.tsx:174-185` 的 `openRecord` 保证一次只展开一条，也就是它是一组互斥控件——盘点说「全站唯一没给选中态做样式的」，方案的 §2.4 定了 ink 实心是选中，但没人把它套到这里。补法：一行 `.record-row[aria-expanded="true"]` 底色。

**15. 48 组互斥控件里的 31 个下拉，a11y 和计数两件事都只在 tabs 里解决了。**
`Select.tsx:244` 的菜单有 `role="listbox"` 但菜单项没有 `role="option"`/`aria-selected`（226-227 只有 haspopup/expanded）；收起后计数完全不可见（`Select.tsx:291` 的 `<i>` 在浮层里）。方案 §4.6 把这两点当证据引用，却只给 6 个 tabs 做了规格。补法：步 6 抽 Tabs 时顺手给 Select 菜单项补 `role="option"`+`aria-selected`，并把 count 冒到 `.filter-dd-btn` 上（`.city-chip` 2590-2600 那套反色现成）。

**16. 拍板②「改框式成本为零」漏了两个附带项。**
`.field input:focus` 现在的焦点表达是 `border-bottom-color: var(--coral)`(4247-4251)，改四边框后要重写；`.field > small` 字数计数是 `position:absolute; right:7px; bottom:7px`(4255-4257)，四边框加上去后它会压在边框上。高度确实不变（4236-4239 + `* box-sizing`），但「半天的活」要加上这两处。

**17. 步 1④ 抬断点的两个实现细节。**
新的 `@media (max-width:1250px){ .workspace{grid-template-columns:1fr} }` 必须**排在 5598 那个 1180 块之后**，否则 1100px 时被后写的 `330px minmax(500px,1fr)`(5607-5609) 赢掉，改了等于没改；另外单列后 `.detail-column` 落到长列表下方，而全仓只有 `Select.tsx:117` 一处 `scrollIntoView`，点任务会像没反应。补法：同 commit 加一个选中后滚到详情的行为。

**18. 680px 媒体查询里还有第二、第三个雷。**
除方案点名的 5849 外：`5948 .modal { box-shadow: none }`（步 3 合并弹窗投影后仍然覆盖，无害但同类，应一并登记）；更麻烦的是 `5824 [class*="modal"]:not(.modal-backdrop)` —— 这个属性选择器会命中 `.modal-rail / .modal-header / .modal-footer / .modal-workspace / .modal-edition`，给它们每个都套上 `width: min(94vw,560px)!important; max-height:88vh; overflow-y:auto`。步 5 动弹窗时必然撞上。

**19. 加载态 / 空态整类没有规则行，也不在密度清单里。**
`.boot-state`(755) + `.boot-mark`(767，1px ink 边 + coral + acid，是方案自己那份「10 个方角面」里的两个)、`.empty-state`(1190，`min-height: 360px`，3 个调用点)、`.detail-placeholder`(1224，590px)。§2.2 没有任何一行能判这三个；步 7 的密度杠杆表列了 590 却漏了 360。补法：把它们显式归「二级面 + 无影」，360 一并进步 7。

**20. 数字校正（都不影响结论，但会被拿来当理由）。**
① `--line` / `--line-strong` 在 border 上下文实测 **65 / 46**（`grep -E "border(-top|-bottom|-left|-right)?: *[^;]*var\(--line(-strong)?\)"`），不是「五五开」，`--line` 是明显多数——§2.3 的规则落地后要改的主要是 `--line-strong` 那 46 处里的非控件用法。② `.section-heading` 调用点是 **12** 处不是 11（AutoBroadcast 3 + AutoPublish 3 + Broadcast 2 + Overview 1 + Supply 3），§0 密度表和 §6 的 9+2+1 分解才是对的。③ 印章白名单按「顶栏 + 各弹窗底栏主操作」数出来是 10-12 处（弹窗底栏 primary 至少 9 个，含 FirstRunWizard 3 个和 renew-box 1 个），不是 8——`.button.danger`(ConfirmModal) 算不算也没说。④ `.count-picker` tsx 零命中（已死），却出现在步 5a 的控件归一清单里，而步 0 没删它。