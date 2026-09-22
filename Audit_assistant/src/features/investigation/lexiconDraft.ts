import type {
  DraftLexiconContent,
  DraftLexiconEntry
} from "../../types/investigationCreation";

const defaults = {
  platform: "全平台",
  match_type: "黑话词",
  risk_level: "中",
  note: ""
};

export function createLexiconEntry(
  kind: DraftLexiconEntry["kind"],
  parentId = "",
  term = ""
): DraftLexiconEntry {
  return {
    id: `entry:${crypto.randomUUID()}`,
    term,
    kind,
    parent_id: kind === "variant" ? parentId : "",
    enabled: true,
    ...defaults
  };
}

export function normalizeLexiconContent(content: DraftLexiconContent): DraftLexiconContent {
  return {
    ...content,
    title: content.title.trim(),
    risk_label: content.risk_label.trim(),
    description: content.description?.trim() || "",
    entries: content.entries.map((entry) => ({
      ...entry,
      term: entry.term.trim(),
      parent_id: entry.kind === "variant" ? entry.parent_id : "",
      platform: entry.platform || defaults.platform,
      match_type: entry.match_type || defaults.match_type,
      risk_level: entry.risk_level || defaults.risk_level,
      note: entry.note || ""
    }))
  };
}

export function projectLexiconSearchTerms(content: DraftLexiconContent): string[] {
  const enabledVariants = new Map<string, DraftLexiconEntry[]>();
  content.entries.forEach((entry) => {
    if (entry.kind !== "variant" || !entry.enabled) return;
    const values = enabledVariants.get(entry.parent_id) || [];
    values.push(entry);
    enabledVariants.set(entry.parent_id, values);
  });
  const terms: string[] = [];
  const seen = new Set<string>();
  content.entries.forEach((entry) => {
    if (entry.kind !== "main" || !entry.enabled) return;
    (enabledVariants.get(entry.id) || []).forEach((variant) => {
      const term = variant.term.trim();
      if (term && !seen.has(term)) {
        seen.add(term);
        terms.push(term);
      }
    });
  });
  return terms;
}

export function validateLexiconDraft(content: DraftLexiconContent): string[] {
  const errors: string[] = [];
  if (!content.title.trim()) errors.push("请填写词库名称");
  const mains = content.entries.filter((entry) => entry.kind === "main");
  if (!mains.length) errors.push("至少需要一个主题主词");
  const mainIds = new Set(mains.map((entry) => entry.id));
  const enabledVariantParents = new Set(
    content.entries
      .filter((entry) => entry.kind === "variant" && entry.enabled && entry.term.trim())
      .map((entry) => entry.parent_id)
  );
  for (const main of mains) {
    if (!main.term.trim()) errors.push("主题名称不能为空");
    if (main.enabled && !enabledVariantParents.has(main.id)) {
      errors.push(`主题“${main.term.trim() || "未命名主题"}”至少需要一个启用的变体词`);
    }
  }
  for (const entry of content.entries) {
    if (!entry.term.trim()) errors.push("词条不能为空");
    if (entry.kind === "variant" && !mainIds.has(entry.parent_id)) {
      errors.push(`变体词“${entry.term.trim() || "未命名词条"}”尚未归入有效主题`);
    }
  }
  const seen = new Set<string>();
  for (const entry of content.entries) {
    const key = `${entry.term.trim()}\u0000${entry.platform}\u0000${entry.match_type}`;
    if (entry.term.trim() && seen.has(key)) errors.push(`词条“${entry.term.trim()}”重复`);
    seen.add(key);
  }
  if (!projectLexiconSearchTerms(content).length) errors.push("至少需要一个可用于搜索的启用变体词");
  return [...new Set(errors)];
}

export function buildLegacyStructuredLexicon(
  title: string,
  subject: string,
  terms: string[]
): DraftLexiconContent {
  const main = createLexiconEntry("main", "", subject.trim() || "待确认主题");
  return {
    title: `${title.trim() || subject.trim() || "本次调查"}黑话库`,
    risk_label: subject.trim(),
    description: "由旧版扁平搜索词转换，请在应用前确认主题归属。",
    entries: [
      main,
      ...terms.map((term) => createLexiconEntry("variant", main.id, term))
    ]
  };
}
