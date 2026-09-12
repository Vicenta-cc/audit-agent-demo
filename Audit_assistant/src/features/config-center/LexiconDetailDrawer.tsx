import { Edit3, X } from "lucide-react";
import { Button } from "../../components/common/Button";
import { IconButton } from "../../components/common/IconButton";
import { formatFullDateTime, formatNumber } from "../../services/configCenter";
import type { RiskLexicon } from "../../types/configCenter";

interface LexiconDetailDrawerProps {
  lexicon: RiskLexicon | null;
  onClose: () => void;
  onEdit: (lexicon: RiskLexicon) => void;
}

export function LexiconDetailDrawer({ lexicon, onClose, onEdit }: LexiconDetailDrawerProps) {
  if (!lexicon) {
    return null;
  }

  return (
    <div className="config-drawer-backdrop" role="presentation" onClick={onClose}>
      <aside
        className="config-detail-drawer lexicon-detail-drawer"
        role="dialog"
        aria-modal="true"
        aria-labelledby="lexicon-detail-title"
        onClick={(event) => event.stopPropagation()}
      >
        <header className="config-drawer-header">
          <div>
            <span className="config-drawer-kicker">黑话库详情</span>
            <h2 id="lexicon-detail-title">{lexicon.name}</h2>
            <div className="config-drawer-meta">
              {formatNumber(lexicon.entryCount)} 个词条 · 最近更新：{formatFullDateTime(lexicon.updatedAt)}
            </div>
          </div>
          <IconButton type="button" aria-label="关闭黑话库详情" onClick={onClose}>
            <X size={20} />
          </IconButton>
        </header>

        <div className="config-drawer-body">
          <section className="config-drawer-section">
            <h3>词库说明</h3>
            <p style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{lexicon.description || "暂无说明"}</p>
          </section>
          <section className="config-drawer-section">
            <h3>词条</h3>
            {lexicon.terms.length ? (
              <div className="drawer-term-list">
                {lexicon.terms.map((term) => (
                  <div className="drawer-term-item" key={term.id}>
                    <div className="drawer-term-title">
                      <strong>{term.mainTerm}</strong>
                      <span>{term.queryType === "tag" ? "Tag 查询" : "关键词查询"}</span>
                    </div>
                    {term.variants.length ? (
                      <p>变体：{term.variants.join("、")}</p>
                    ) : <p>暂无变体</p>}
                  </div>
                ))}
              </div>
            ) : <p className="drawer-empty-copy">当前暂无词条</p>}
          </section>

          <section className="config-drawer-section">
            <h3>引用方案</h3>
            {lexicon.references.length ? (
              <div className="drawer-reference-list">
                {lexicon.references.map((reference) => (
                  <div className="drawer-reference-item" key={reference.id}>
                    <strong>{reference.name}</strong>
                  </div>
                ))}
              </div>
            ) : <p className="drawer-empty-copy">当前暂无研判方案引用该黑话库</p>}
          </section>
        </div>

        <footer className="config-drawer-footer">
          <Button type="button" variant="primary" onClick={() => onEdit(lexicon)}>
            <Edit3 size={16} />
            编辑黑话库
          </Button>
        </footer>
      </aside>
    </div>
  );
}
