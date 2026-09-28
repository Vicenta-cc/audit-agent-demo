import { useRef, useState } from "react";
import { X } from "lucide-react";

// Keep the existing resource editor's separators. Slashes and spaces can be
// part of a search term (for example 92/95/98) and must remain intact.
export const splitRecallVariants = (value: string) =>
  [...new Set(value.split(/[,，\n]/).map((item) => item.trim()).filter(Boolean))];

interface RecallVariantEditorProps {
  label: string;
  value: string;
  onChange: (value: string) => void;
}

export function RecallVariantEditor({ label, value, onChange }: RecallVariantEditorProps) {
  const [pending, setPending] = useState("");
  const [notice, setNotice] = useState("");
  const composing = useRef(false);
  const input = useRef<HTMLInputElement>(null);
  const variants = splitRecallVariants(value);

  const add = (text: string) => {
    const additions = splitRecallVariants(text);
    if (!additions.length) { setPending(""); return; }
    const merged = [...new Set([...variants, ...additions])];
    onChange(merged.join(", "));
    setPending("");
    setNotice(merged.length === variants.length ? "该变体已存在" : `已添加 ${merged.length - variants.length} 个变体`);
  };

  return (
    <div className="recall-variant-editor" role="group" aria-label={`${label}标签列表`}>
      {variants.map((variant) => (
        <span className="recall-variant-chip" key={variant}>
          <span>{variant}</span>
          <button type="button" aria-label={`删除变体 ${variant}`} onClick={() => {
            onChange(variants.filter((item) => item !== variant).join(", "));
            setNotice(`已删除 ${variant}`);
            input.current?.focus();
          }}>
            <X size={12} aria-hidden="true" />
          </button>
        </span>
      ))}
      <input
        ref={input}
        type="text"
        value={pending}
        aria-label={label}
        placeholder="+ 添加变体"
        onChange={(event) => setPending(event.target.value)}
        onCompositionStart={() => { composing.current = true; }}
        onCompositionEnd={() => { composing.current = false; }}
        onKeyDown={(event) => {
          if (event.key === "Enter" && !composing.current && !event.nativeEvent.isComposing && event.keyCode !== 229) {
            event.preventDefault();
            add(pending);
          }
        }}
        onBlur={() => add(pending)}
        onPaste={(event) => {
          const text = event.clipboardData.getData("text");
          if (!/[,，\n]/.test(text) || composing.current) return;
          event.preventDefault();
          const start = event.currentTarget.selectionStart ?? pending.length;
          const end = event.currentTarget.selectionEnd ?? start;
          add(pending.slice(0, start) + text + pending.slice(end));
        }}
      />
      <span className="recall-variant-announcement" role="status" aria-live="polite">{notice}</span>
    </div>
  );
}
