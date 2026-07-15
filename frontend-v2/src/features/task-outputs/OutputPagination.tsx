import { ChevronLeft, ChevronRight } from "lucide-react";

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

  return (
    <div className="output-pagination" aria-label="产出分页">
      <button
        className="mt-icon-button small"
        type="button"
        aria-label="上一页"
        disabled={safePage <= 1}
        onClick={() => onPageChange(Math.max(1, safePage - 1))}
      >
        <ChevronLeft size={17} />
      </button>
      <button className="page-number is-active" type="button" aria-current="page">
        {safePage}
      </button>
      <button
        className="mt-icon-button small"
        type="button"
        aria-label="下一页"
        disabled={safePage >= pageCount}
        onClick={() => onPageChange(Math.min(pageCount, safePage + 1))}
      >
        <ChevronRight size={17} />
      </button>

      <select value={pageSize} onChange={(event) => onPageSizeChange(Number(event.target.value))} aria-label="每页条数">
        <option value={10}>10 条/页</option>
        <option value={20}>20 条/页</option>
        <option value={50}>50 条/页</option>
      </select>
    </div>
  );
}
