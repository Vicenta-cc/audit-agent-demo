import { formatCreationErrorMessage } from "./confirmationView";

const technicalContent = /(schema_version|draft[_\s-]?id|toolresult|\[object object\]|\/rule-assistant\/|[A-Z]{3,}(?:_[A-Z0-9]+)+|\{\s*"|database|revision|(?:规则|词库|修订|编辑|操作)\s*ID|内容(?:哈希|指纹)|(?:ruleset-proposal|lexicon-edit|ruleset-presentation):)/i;
const technicalColumn = /(?:\bID\b|_id\b|哈希|指纹|schema_version|revision)/i;

function publicTerms(text: string): string {
  return text
    .replace(/\bHermes(?:\s+Agent|助手)?(?:\s+v?\d+(?:\.\d+)*)?\b/gi, "研判助手")
    .replace(/我是\s+研判助手/g, "我是研判助手")
    .replace(/[（(]id=[^）)]*[）)]/gi, (value) => value.includes("归属主词") ? "（归属主词）" : "")
    .replace(/[（(](?:adjudication_notes|recall_plan\.search_terms)[）)]/g, "")
    .replace(/term\s*(?:\+|\/)\s*platform\s*(?:\+|\/)\s*match_type/g, "词条、适用平台和匹配方式")
    .replace(/\bterm\b/g, "词条")
    .replace(/主词\s+词条/g, "主词")
    .replace(/([（(])(?:main|variant|tag)[，,]\s*/g, "$1")
    .replace(/[（(](?:main|variant|tag)[）)]/g, "")
    .replace(/\bcomment_audit\b/g, "评论研判")
    .replace(/\bfusion_audit\b/g, "融合研判")
    .replace(/\bimage_evidence\b/g, "图片证据提取")
    .replace(/\bvideo_frame_evidence\b/g, "视频关键帧提取");
}

function tableCells(line: string): string[] {
  return line.trim().replace(/^\|/, "").replace(/(?<!\\)\|$/, "")
    .split(/(?<!\\)\|/).map(cell => cell.trim());
}

const separator = (line: string) => /^\s*\|?\s*:?-+:?\s*(?:\|\s*:?-+:?\s*)+\|?\s*$/.test(line);

/** Project metadata out of whole tables, never delete a table header or a business row in isolation. */
export function presentCreationAssistantContent(content: string): string {
  const trimmed = content.trim();
  if (!trimmed) return "正在整理本次调查建议。";
  const lines = trimmed.split("\n");
  const output: string[] = [];
  let fenced = false;
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    // Keep ordinary fenced lists and quotations intact. They may contain literal search terms.
    if (/^\s*```/.test(line)) {
      fenced = !fenced;
      output.push(line);
      continue;
    }
    if (fenced || /^\s*>/.test(line)) {
      output.push(line);
      continue;
    }
    if (line.includes("|") && i + 1 < lines.length && separator(lines[i + 1])) {
      const headers = tableCells(line);
      const alignment = tableCells(lines[i + 1]);
      const keep = headers.map((cell, index) => technicalColumn.test(cell) ? -1 : index).filter(index => index >= 0);
      const rows: string[][] = [];
      i += 2;
      while (i < lines.length && lines[i].includes("|")) {
        const cells = tableCells(lines[i]);
        // A two-column field/value table may have metadata rows rather than metadata columns.
        if (!technicalContent.test(cells[0] || "") && !technicalColumn.test(cells[0] || "")) rows.push(cells);
        i++;
      }
      i--;
      if (keep.length && rows.length) {
        const row = (cells: string[]) => `| ${keep.map(index => publicTerms(cells[index] || "—")).join(" | ")} |`;
        output.push(row(headers), row(alignment), ...rows.map(row));
      }
      continue;
    }
    const shown = publicTerms(line);
    if (!technicalContent.test(shown)) output.push(shown);
  }
  const shown = output.join("\n").trim();
  // Preserve the original text when projection made no substantive change.
  if (shown === trimmed || (!technicalContent.test(trimmed) && publicTerms(trimmed) === trimmed && !technicalColumn.test(trimmed))) return trimmed;
  const hasBusinessText = output.some(line => !separator(line) && (line.match(/[\u3400-\u9fff]/g)?.length || 0) >= 2);
  return hasBusinessText ? shown : formatCreationErrorMessage(trimmed);
}
