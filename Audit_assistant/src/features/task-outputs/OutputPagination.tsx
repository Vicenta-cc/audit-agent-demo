import { ChevronLeft, ChevronRight } from "lucide-react";
import { useEffect, useState } from "react";

interface OutputPaginationProps {
  total: number;
  page: number;
  pageSize: number;
  onPageChange: (value: number) => void;
  onPageSizeChange: (value: number) => void;
}

export function OutputPagination({ total, page, pageSize, onPageChange, onPageSizeChange }: OutputPaginationProps) {
  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  const safePage = Math.min(page, pageCount);
  const [jumpPage, setJumpPage] = useState(String(safePage));
  useEffect(() => setJumpPage(String(safePage)), [safePage]);

  const jump = () => {
    const nextPage = Math.max(1, Math.min(pageCount, Number(jumpPage) || 1));
    setJumpPage(String(nextPage));
    onPageChange(nextPage);
  };

  return (
    <div className="output-pagination" aria-label="产出分页">
      <span className="pagination-total">总数 {total} 条</span>
      <select value={pageSize} onChange={(event) => onPageSizeChange(Number(event.target.value))} aria-label="每页条数">
        <option value={10}>10 条/页</option>
        <option value={20}>20 条/页</option>
        <option value={50}>50 条/页</option>
      </select>
      <div className="pagination-pages">
        <button type="button" className="pagination-step" disabled={safePage <= 1} onClick={() => onPageChange(safePage - 1)}>
          <ChevronLeft size={15} />上一页
        </button>
        {getVisiblePages(safePage, pageCount).map((item, index) => item === "ellipsis" ? (
          <span className="pagination-ellipsis" key={`ellipsis-${index}`}>...</span>
        ) : (
          <button
            type="button"
            className={`page-number${item === safePage ? " is-active" : ""}`}
            aria-current={item === safePage ? "page" : undefined}
            onClick={() => onPageChange(item)}
            key={item}
          >
            {item}
          </button>
        ))}
        <button type="button" className="pagination-step" disabled={safePage >= pageCount} onClick={() => onPageChange(safePage + 1)}>
          下一页<ChevronRight size={15} />
        </button>
      </div>
      <div className="pagination-jump">
        <span>前往</span>
        <input
          value={jumpPage}
          inputMode="numeric"
          aria-label="跳转页码"
          onChange={(event) => setJumpPage(event.target.value.replace(/\D/g, ""))}
          onKeyDown={(event) => { if (event.key === "Enter") jump(); }}
        />
        <span>页</span>
        <button type="button" onClick={jump}>跳转</button>
      </div>
    </div>
  );
}

function getVisiblePages(current: number, total: number): Array<number | "ellipsis"> {
  if (total <= 7) return Array.from({ length: total }, (_, index) => index + 1);
  if (current <= 4) return [1, 2, 3, 4, 5, "ellipsis", total];
  if (current >= total - 3) return [1, "ellipsis", total - 4, total - 3, total - 2, total - 1, total];
  return [1, "ellipsis", current - 1, current, current + 1, "ellipsis", total];
}
