import { ChevronLeft, ChevronRight } from "lucide-react";
import { IconButton } from "../../components/common/IconButton";

interface TaskPaginationProps {
  total: number;
  page: number;
  pageSize: number;
  onPageChange: (page: number) => void;
  onPageSizeChange: (pageSize: number) => void;
}

export function TaskPagination({ total, page, pageSize, onPageChange, onPageSizeChange }: TaskPaginationProps) {
  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  const pages = Array.from({ length: pageCount }, (_, index) => index + 1).slice(0, 5);

  return (
    <div className="task-pagination">
      <span>共 {total} 条</span>
      <div className="task-pagination-controls">
        <select value={pageSize} onChange={(event) => onPageSizeChange(Number(event.target.value))}>
          {[10, 20, 50].map((size) => (
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
            className={`page-number${item === page ? " is-active" : ""}`}
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
