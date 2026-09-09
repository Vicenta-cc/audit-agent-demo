import type { ReportPostDetail } from "../../types/reports";

/** Frozen source fields shared by the report and appendix detail drawers. */
export function ReportPostSourceDetails({ detail }: { detail: ReportPostDetail }) {
  const published = detail.published_at ? new Date(detail.published_at) : null;
  const publishedLabel = published && !Number.isNaN(published.getTime())
    ? new Intl.DateTimeFormat("zh-CN", {
      year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit"
    }).format(published)
    : detail.published_at;

  return (
    <>
      {publishedLabel ? <p>发布时间：{publishedLabel}</p> : null}
      {detail.source_url && /^https?:\/\//i.test(detail.source_url) ? (
        <a href={detail.source_url} target="_blank" rel="noopener noreferrer">打开原帖</a>
      ) : null}
      {detail.original_text ? (
        <section className="r31-drawer-section"><h3>原文</h3><p>{detail.original_text}</p></section>
      ) : null}
      {detail.transcripts?.length ? (
        <section className="r31-drawer-section">
          <h3>已有语音转写</h3>
          {detail.transcripts.map((text, index) => <p key={index}>{text}</p>)}
        </section>
      ) : null}
    </>
  );
}
