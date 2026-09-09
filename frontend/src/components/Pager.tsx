import { useState } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";

/** Slice an array to the current page and remember the page index. */
export function usePaged<T>(items: T[], perPage = 10) {
  const [page, setPage] = useState(0);
  const pageCount = Math.max(1, Math.ceil(items.length / perPage));
  const safePage = Math.min(page, pageCount - 1);
  const slice = items.slice(safePage * perPage, safePage * perPage + perPage);
  return { slice, page: safePage, pageCount, setPage, total: items.length };
}

interface Props {
  page: number;
  pageCount: number;
  total: number;
  onChange: (page: number) => void;
}

export function Pager({ page, pageCount, total, onChange }: Props) {
  if (pageCount <= 1) return null;
  return (
    <div className="pager">
      <button
        aria-label="上一页"
        disabled={page <= 0}
        onClick={() => onChange(page - 1)}
      >
        <ChevronLeft size={15} />
      </button>
      <span>
        {page + 1} / {pageCount}
        <i>共 {total}</i>
      </span>
      <button
        aria-label="下一页"
        disabled={page >= pageCount - 1}
        onClick={() => onChange(page + 1)}
      >
        <ChevronRight size={15} />
      </button>
    </div>
  );
}
