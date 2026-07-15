import { ChevronLeft, ChevronRight } from "lucide-react";
import { IconButton } from "../../components/common/IconButton";

interface ConfigPaginationProps {
  total: number;
  page: number;
  pageSize: number;
  onPageChange: (page: number) => void;
  onPageSizeChange: (pageSize: number) => void;
}

export function ConfigPagination({ total, page, pageSize, onPageChange, onPageSizeChange }: ConfigPaginationProps) {
  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  const start = Math.max(1, Math.min(page - 2, pageCount - 4));
  const pages = Array.from({ length: Math.min(5, pageCount) }, (_, index) => start + index).filter(
    (item) => item <= pageCount
  );

  return (
    <div className="config-pagination">
      <span>共 {total} 条</span>
      <div className="config-pagination-controls">
        <select value={pageSize} onChange={(event) => onPageSizeChange(Number(event.target.value))}>
          {[5, 10, 20, 50].map((size) => (
            <option key={size} value={size}>
              {size} 条/页
            </option>
          ))}
        </select>
        <IconButton type="button" aria-label="上一页" disabled={page <= 1} onClick={() => onPageChange(page - 1)}>
          <ChevronLeft size={18} />
        </IconButton>
        {pages.map((item) => (
          <button
            key={item}
            type="button"
            className={`config-page-number${item === page ? " is-active" : ""}`}
            onClick={() => onPageChange(item)}
          >
            {item}
          </button>
        ))}
        <IconButton
          type="button"
          aria-label="下一页"
          disabled={page >= pageCount}
          onClick={() => onPageChange(page + 1)}
        >
          <ChevronRight size={18} />
        </IconButton>
      </div>
    </div>
  );
}
