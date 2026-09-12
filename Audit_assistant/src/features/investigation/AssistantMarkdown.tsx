import { createElement } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { formatHistoricalReportText, formatReportVocabulary } from "./historicalReportPresentation";

interface MarkdownNode {
  type: string;
  value?: string;
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

const components: Components = {
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
        remarkPlugins: [remarkGfm, remarkHistoricalReportTerms],
        components,
        skipHtml: true
      },
      content
    )
  );
}
