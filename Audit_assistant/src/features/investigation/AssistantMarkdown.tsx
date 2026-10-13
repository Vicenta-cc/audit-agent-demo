import { createElement } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { formatHistoricalReportText, formatReportVocabulary } from "./historicalReportPresentation";

import { ManualImage } from "./ManualImage";

interface MarkdownNode {
  type: string;
  value?: string;
  url?: string;
  position?: { start: { offset?: number }; end: { offset?: number } };
  children?: MarkdownNode[];
}

const PRESERVED_NODE_TYPES = new Set([
  "blockquote",
  "code",
  "inlineCode",
  "link",
  "linkReference"
]);

const ENUM_CONTEXT_NODE_TYPES = new Set(["listItem", "paragraph", "tableCell"]);
const ENUM_CONTEXT_PATTERN = /审核(?:决定|结论|状态|资料)|被审核为|决定\s*[：:]|风险(?:等级|分布)|(?:none|low|medium|high)\s*风险|risk[_ ]?level|decision/i;
const DETAIL_LINK_PATTERN = /^(?:(?:https?:\/\/)[^\s]+|\/(?:investigation|tasks)\/[^\s]+)$/;

function codeLinkTarget(children: unknown): string {
  const value = String(children ?? "").replace(/\n$/, "").trim();
  return DETAIL_LINK_PATTERN.test(value) ? value : "";
}

function textContent(node: MarkdownNode): string {
  if (PRESERVED_NODE_TYPES.has(node.type)) return "";
  if (node.type === "text") return node.value || "";
  return node.children?.map(textContent).join("") || "";
}

function transformDisplayTerms(
  node: MarkdownNode,
  tableCell = false,
  enumContext = false
) {
  if (node.type === "inlineCode" && typeof node.value === "string") {
    node.value = formatReportVocabulary(node.value);
    return;
  }
  if (PRESERVED_NODE_TYPES.has(node.type)) return;
  const isTableCell = tableCell || node.type === "tableCell";
  const isEnumContext = enumContext || isTableCell || (
    ENUM_CONTEXT_NODE_TYPES.has(node.type) && ENUM_CONTEXT_PATTERN.test(textContent(node))
  );
  if (node.type === "text" && typeof node.value === "string") {
    node.value = formatHistoricalReportText(node.value, {
      tableCell: isTableCell,
      enumContext: isEnumContext
    });
  }
  node.children?.forEach((child) => transformDisplayTerms(
    child,
    isTableCell,
    isEnumContext
  ));
}

function remarkHistoricalReportTerms() {
  return (tree: MarkdownNode) => transformDisplayTerms(tree);
}

// GFM can consume the closing ** and adjacent Chinese prose as part of a
// literal URL. Repair only a matching opening marker, never explicit links.
function remarkChineseAutolinks() {
  return (tree: MarkdownNode, file: { value: unknown }) => {
    const source = String(file.value);
    const visit = (node: MarkdownNode) => {
      if (!node.children) return;
      const children: MarkdownNode[] = [];
      for (const child of node.children) {
        const start = child.position?.start.offset;
        const end = child.position?.end.offset;
        const raw = start !== undefined && end !== undefined ? source.slice(start, end) : "";
        const text = child.children?.length === 1 ? child.children[0].value : undefined;
        if (child.type === "link" && /^https?:\/\//.test(raw) && text && child.url === text) {
          const previous = children[children.length - 1];
          const closing = text.search(/\*\*(?=[，。；：！？、（）【】《》“”‘’]|$)/);
          if (closing > 0 && previous?.type === "text" && previous.value?.endsWith("**") &&
              start !== undefined && source.slice(start - 2, start) === "**") {
            previous.value = previous.value.slice(0, -2);
            const url = text.slice(0, closing);
            children.push({ type: "strong", children: [
              { ...child, url, children: [{ type: "text", value: url }] }
            ] });
            const suffix = text.slice(closing + 2);
            if (suffix) children.push({ type: "text", value: suffix });
            continue;
          }
          const cut = text.search(/[，。；：！？、（）【】《》“”‘’]/);
          const queryOrFragment = text.search(/[?#]/);
          // Query/fragment punctuation can be real data (e.g. q=上海（旅游）).
          // Its boundary is ambiguous, so do not truncate it as Chinese prose.
          if (cut > 0 && (queryOrFragment < 0 || cut < queryOrFragment)) {
            children.push(
              { ...child, url: text.slice(0, cut), children: [{ type: "text", value: text.slice(0, cut) }] },
              { type: "text", value: text.slice(cut) }
            );
            continue;
          }
        }
        visit(child);
        children.push(child);
      }
      node.children = children;
    };
    visit(tree);
  };
}

const components: Components = {
  img: ({ src, alt }) => src?.startsWith("/api/user-manual/images/")
    ? createElement(ManualImage, { src, alt }) : createElement("img", { src, alt }),
  table: ({ children, node: _node, ...props }) => createElement(
    "div",
    { className: "inv-markdown-table-scroll", tabIndex: 0, "aria-label": "回答数据表格" },
    createElement("table", props, children)
  ),
  code: ({ children, className, node: _node, ...props }) => {
    const target = codeLinkTarget(children);
    if (target) {
      return createElement(
        "a",
        {
          ...props,
          className: "inv-assistant-detail-link",
          href: target,
          title: "打开审核详情"
        },
        "查看审核详情"
      );
    }
    return createElement("code", { ...props, className }, children);
  },
  a: ({ children, href, node: _node, ...props }) => {
    const target = typeof href === "string" ? href : "";
    const isDetailLink = DETAIL_LINK_PATTERN.test(target);
    return createElement(
      "a",
      {
        ...props,
        href,
        className: isDetailLink ? "inv-assistant-detail-link" : props.className
      },
      children
    );
  }
};

interface AssistantMarkdownProps {
  content: string;
  className?: string;
}

export function AssistantMarkdown({ content, className = "" }: AssistantMarkdownProps) {
  return createElement(
    "div",
    { className: `inv-assistant-markdown ${className}`.trim() },
    createElement(
      ReactMarkdown,
      {
        remarkPlugins: [remarkGfm, remarkChineseAutolinks, remarkHistoricalReportTerms],
        components,
        skipHtml: true
      },
      content
    )
  );
}
