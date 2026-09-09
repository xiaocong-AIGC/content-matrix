import {
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";
import { ChevronDown, Plus, Search } from "lucide-react";

export interface SelectOption {
  value: string;
  label: string;
  count?: number;
  badge?: ReactNode; // 例如平台徽章，渲染在文字前面
}

/**
 * 全站唯一的下拉。
 *
 * 在它之前这里有**三套**并存：12 处原生 `<select>`（7 个文件）、3 处 `datalist`、
 * 以及自研的 `FilterDropdown`。同一个「选城市」的动作在不同页面长得不一样、
 * 行为也不一样 —— 有的能搜、有的不能，有的能填新值、有的只能选。
 *
 * 两个刻意的取舍：
 *
 * **① 沿用 `filter-dd-*` 的全套类名，只加两个修饰类**（`.as-field` 和
 * `.filter-dd-create`）。不新造视觉语言，也意味着 `FilterDropdown` 能原地变成
 * 它的一层壳、4 个现有调用点一行不用改 —— 收口可以分批做，不必一次性重构。
 *
 * **② `creatable` 是给「城市」这类字段的**：用户明确要过「既能选已有也能新增」。
 * 城市是个开放集合（今天三个城市，明天可能加杭州），做成纯枚举等于每加一个城市
 * 就要改代码；做成纯输入框又会让人打出「上海市」「上海 」这种和已有值差一点的脏值，
 * 而城市是内容分发的键，一个脏值就意味着那条内容谁都取不到。
 * 所以：先给候选、允许新建、新建走同一个 onChange。
 */
export function Select({
  label,
  value,
  options,
  onChange,
  icon,
  variant = "filter",
  creatable = false,
  searchable,
  placeholder = "请选择",
  disabled = false,
}: {
  label?: string;
  value: string;
  options: SelectOption[];
  onChange: (value: string) => void;
  icon?: ReactNode;
  /** filter = 页面顶部的筛选条；field = 表单里的一个字段（占满宽度） */
  variant?: "filter" | "field";
  /** 允许输入一个候选里没有的新值（城市这类开放集合） */
  creatable?: boolean;
  /** 不传时按候选个数自动决定：超过 8 个才给搜索框 */
  searchable?: boolean;
  placeholder?: string;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [keyword, setKeyword] = useState("");
  const [box, setBox] = useState<{
    left: number; top: number; width: number; up: boolean; maxHeight: number;
  } | null>(null);
  const ref = useRef<HTMLDivElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const btnRef = useRef<HTMLButtonElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const withSearch = searchable ?? (creatable || options.length > 8);

  useEffect(() => {
    if (!open) return;
    const onDoc = (event: MouseEvent) => {
      const t = event.target as Node;
      // ⚠ 菜单是 portal 到 body 的，**不在** ref 这棵 DOM 子树里 ——
      // 只判 ref 的话，点菜单里的选项会被当成"点了外面"，下拉在 click 生效前就关了，
      // 表现是"这个下拉点不中"。所以两个都要判。
      const inTrigger = ref.current?.contains(t);
      const inMenu = menuRef.current?.contains(t);
      if (!inTrigger && !inMenu) setOpen(false);
    };
    // Esc 关掉：弹出层不给键盘出口，是每个自研下拉都会踩的一条
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  // 「已经为这次打开做过初始化了吗」。菜单是 portal 的、位置要等量完才有，
  // 所以初始化必须等 box 就绪；但**只能做一次**。
  const readyRef = useRef(false);
  useEffect(() => {
    if (!open) {
      readyRef.current = false;
      setKeyword("");
      return;
    }
    if (!box || readyRef.current) return;
    readyRef.current = true;
    if (withSearch) inputRef.current?.focus();
    // 把当前选中项滚进视野：24 个小时里选中的是 19，不滚的话打开看到的是 00。
    // ⚠ 这一句**只能在打开时跑一次**。之前它跟着 box 变化重跑，而 box 每次滚动
    // 都会被重新计算 —— 于是用户往下滑，列表立刻被弹回选中项，
    // 表现就是"滑不动、也滑不到后面的选项"。
    menuRef.current
      ?.querySelector(".filter-dd-item.on")
      ?.scrollIntoView({ block: "nearest" });
  }, [open, box, withSearch]);

  // 菜单挂到 body 上、用 fixed 定位。
  // ⚠ 这不是为了好看：下拉长在弹窗里时，弹窗的 `overflow-y: auto` 会把菜单**裁掉** ——
  // 实测「城市」下拉里输入「上」，DOM 里明明有「上海」这一项，屏幕上却只看得到
  // 「新增」那一行，因为菜单底边（650）已经超出了弹窗可视区（601）。
  // 用户会以为"这个下拉搜不出东西"，而它其实搜到了。
  useLayoutEffect(() => {
    if (!open) {
      setBox(null);
      return;
    }
    const place = () => {
      const el = btnRef.current;
      if (!el) return;
      const r = el.getBoundingClientRect();
      const GAP = 12;
      const below = window.innerHeight - r.bottom - GAP;
      const above = r.top - GAP;
      // 往空间大的那边开
      const up = above > below;
      // ⚠ 高度必须按**可用空间**算，不能只写死一个 max-height：
      // 时间选择器有 24 个小时、60 个分钟，写死高度换个位置照样顶出屏幕，
      // 而菜单一旦超出视口就既滚不动也点不到 —— 用户连"选晚上的时间"都做不到。
      //
      // ⚠ 下限 140 不能再往上抬。以前这里是 `Math.max(200, ...)`，那是个
      // **保证溢出**的写法：可用空间只剩 120px 时它照样要 200px，多出来的
      // 80px 连同里面的选项一起留在视口外 —— 而菜单是 position:fixed，
      // 页面怎么滚都够不着。宁可矮一点、内部滚动，也不能有点不到的选项。
      const room = Math.max(0, up ? above : below);
      const maxHeight = Math.min(360, Math.max(140, room));
      const next = {
        left: r.left,
        top: up ? r.top : r.bottom,
        width: r.width,
        up,
        maxHeight,
      };
      // ⚠ 值没变就复用旧对象。否则每个滚动事件都产生一个新 box → 重渲染 →
      // 依赖 box 的 effect 重跑。这是"滑不动"的根源。
      setBox((prev) =>
        prev &&
        prev.left === next.left &&
        prev.top === next.top &&
        prev.width === next.width &&
        prev.up === next.up &&
        prev.maxHeight === next.maxHeight
          ? prev
          : next,
      );
    };
    place();
    // ⚠ 再量一帧。`useLayoutEffect` 是在这一次渲染提交后立刻跑的，如果这次
    // 渲染**本身**改变了布局（典型场景：点「加一个时间段」插入一行，紧接着
    // 就点这一行里的小时下拉），量到的是旧位置 —— 菜单会照着旧坐标往下开，
    // 而它 position:fixed，之后没有任何东西会再纠正它。
    const raf = requestAnimationFrame(place);
    // 祖先容器滚动时菜单要跟着走；用捕获才能收到内层滚动。
    // 但**菜单自己滚不算** —— 那是用户在翻选项，重新定位纯属添乱。
    const onScroll = (event: Event) => {
      if (menuRef.current?.contains(event.target as Node)) return;
      place();
    };
    window.addEventListener("scroll", onScroll, true);
    window.addEventListener("resize", place);
    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener("scroll", onScroll, true);
      window.removeEventListener("resize", place);
    };
  }, [open]);

  const current = options.find((o) => o.value === value);
  const shown = useMemo(() => {
    const key = keyword.trim().toLowerCase();
    if (!key) return options;
    return options.filter(
      (o) =>
        o.label.toLowerCase().includes(key) ||
        o.value.toLowerCase().includes(key),
    );
  }, [options, keyword]);

  const typed = keyword.trim();
  // 打的词和任何候选都不一样时，才提供「新建」——完全相同就是在选已有的那个
  const canCreate =
    creatable &&
    !!typed &&
    !options.some((o) => o.value === typed || o.label === typed);

  function pick(next: string) {
    onChange(next);
    setOpen(false);
  }

  return (
    <div
      className={`filter-dd ${variant === "field" ? "as-field" : ""} ${
        open ? "open" : ""
      }`}
      ref={ref}
    >
      <button
        ref={btnRef}
        type="button"
        className="filter-dd-btn"
        onClick={() => !disabled && setOpen((v) => !v)}
        disabled={disabled}
        aria-haspopup="listbox"
        aria-expanded={open}
      >
        {label ? (
          <span className="filter-dd-tag">
            {icon}
            {label}
          </span>
        ) : null}
        {current?.badge}
        <strong>{current?.label ?? value ?? placeholder}</strong>
        <ChevronDown size={14} className="filter-dd-caret" />
      </button>
      {open && box
        ? createPortal(
        <div
          ref={menuRef}
          className={`filter-dd-menu is-floating ${box.up ? "up" : ""}`}
          role="listbox"
          style={{
            left: box.left,
            top: box.top,
            minWidth: Math.max(box.width, 150),
            maxHeight: box.maxHeight,
          }}
        >
          {withSearch ? (
            <div className="filter-dd-search">
              <Search size={13} />
              <input
                ref={inputRef}
                value={keyword}
                onChange={(e) => setKeyword(e.target.value)}
                placeholder={creatable ? "搜索，或输入新选项" : "搜索"}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && canCreate) pick(typed);
                  if (e.key === "Enter" && !canCreate && shown[0]) {
                    pick(shown[0].value);
                  }
                }}
              />
            </div>
          ) : null}
          {canCreate ? (
            <button
              type="button"
              className="filter-dd-item filter-dd-create"
              onClick={() => pick(typed)}
            >
              <span className="filter-dd-itemlabel">
                <Plus size={13} /> 新增「{typed}」
              </span>
            </button>
          ) : null}
          {shown.map((o) => (
            <button
              key={o.value}
              type="button"
              className={`filter-dd-item ${o.value === value ? "on" : ""}`}
              onClick={() => pick(o.value)}
            >
              <span className="filter-dd-itemlabel">
                {o.badge}
                {o.label}
              </span>
              {o.count !== undefined ? <i>{o.count}</i> : null}
            </button>
          ))}
          {!shown.length && !canCreate ? (
            <div className="filter-dd-empty">没有匹配的选项</div>
          ) : null}
        </div>,
        document.body,
          )
        : null}
    </div>
  );
}
