import { createElement as h, Fragment, useEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type MouseEvent } from "react";
import { createPortal } from "react-dom";

export function ManualImage({ src, alt = "操作手册配图" }: { src?: string; alt?: string }) {
  const trigger = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    if (!open) return;
    const close = (event: KeyboardEvent) => { if (event.key === "Escape") setOpen(false); };
    document.addEventListener("keydown", close);
    return () => { document.removeEventListener("keydown", close); trigger.current?.focus(); };
  }, [open]);
  if (!src || !/^\/api\/user-manual\/images\/[a-f0-9]{64}$/.test(src)) return h("span", null, alt);
  if (failed) return h("a", { href: "/manual.html", target: "_blank", rel: "noopener noreferrer" }, "配图暂不可用，查看操作手册");
  return h(Fragment, null,
    h("button", { ref: trigger, type: "button", className: "inv-manual-image", "aria-label": `放大：${alt}`, onClick: () => setOpen(true) },
      h("img", { src, alt, loading: "lazy", onError: () => setFailed(true) }),
      h("span", null, `${alt} · 点击放大`)),
    open && createPortal(h("div", { className: "inv-manual-lightbox", role: "dialog", "aria-modal": true, "aria-label": alt,
      onKeyDown: (event: ReactKeyboardEvent<HTMLDivElement>) => { if (event.key === "Tab") event.preventDefault(); }, onClick: () => setOpen(false) },
      h("button", { type: "button", autoFocus: true, "aria-label": "关闭图片", onClick: () => setOpen(false) }, "关闭"),
      h("img", { src, alt, onClick: (event: MouseEvent<HTMLImageElement>) => event.stopPropagation() })), document.body));
}
