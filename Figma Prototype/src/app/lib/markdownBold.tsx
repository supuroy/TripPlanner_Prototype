import type { ReactNode } from "react";

/**
 * Renders `**segments**` as <strong>; leaves other text as spans.
 * Uses non-greedy matching so multiple bold spans per line work.
 */
export function renderInlineBold(text: string, strongClassName?: string): ReactNode[] {
  const strongCls = strongClassName ?? "font-semibold text-white/90";
  const parts = text.split(/(\*\*[\s\S]+?\*\*)/g);
  return parts.map((part, i) => {
    const m = part.match(/^\*\*([\s\S]+?)\*\*$/);
    if (m) {
      return (
        <strong key={i} className={strongCls}>
          {m[1]}
        </strong>
      );
    }
    return (
      <span key={i} className="text-inherit">
        {part}
      </span>
    );
  });
}
