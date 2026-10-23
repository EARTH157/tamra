import { ChevronDown, ChevronUp } from "lucide-react";
import { useState } from "react";
import { useT } from "./i18n";

/** What a chat's last prompt put in the model's context window (spec §6). */
export type ContextUse = {
  window: number;
  /** Kept for the reply: its max tokens, plus the thinking headroom where the provider adds it. */
  reply: number;
  /** The prompt's parts, estimated by the core. */
  parts: { instructions: number; sources: number; history: number; question: number };
  sourceCount: number;
  /** Earlier exchanges in the prompt, of the chat's answered ones. */
  kept: number;
  total: number;
  /** The local model's own count, once it reports: the whole prompt and what it has generated. */
  measured: { prompt: number; generated: number } | null;
};

/** 950, 2.1k, 32k */
export function compactTokens(n: number): string {
  if (n < 1000) return String(n);
  const k = n / 1000;
  return `${k >= 10 ? Math.round(k) : Math.round(k * 10) / 10}k`;
}

/** Tokens in use: the model's own count once it has one, else the estimate of the prompt. */
export function usedTokens(use: ContextUse): number {
  if (use.measured) return use.measured.prompt + use.measured.generated;
  const { instructions, sources, history, question } = use.parts;
  return instructions + sources + history + question;
}

type Segment = { key: string; label: string; tokens: number; note?: string; estimated: boolean };

/** The window, part by part: the prompt's parts, what the model wrote, the reply room, and free. */
function segments(use: ContextUse, t: ReturnType<typeof useT>): Segment[] {
  // Once the model has counted its prompt, the estimated parts are scaled to that count.
  const estimate = use.parts.instructions + use.parts.sources + use.parts.history + use.parts.question;
  const scale = use.measured && estimate > 0 ? use.measured.prompt / estimate : 1;
  const [instructions, sources, history, question] = [
    use.parts.instructions,
    use.parts.sources,
    use.parts.history,
    use.parts.question,
  ].map((n) => Math.round(n * scale));
  const generated = use.measured?.generated ?? 0;
  const list: Segment[] = [
    { key: "instructions", label: t("context.instructions"), tokens: instructions, estimated: true },
    {
      key: "sources",
      label: t("context.sources"),
      tokens: sources,
      note: t("context.sourceCount", { count: use.sourceCount }),
      estimated: true,
    },
    {
      key: "history",
      label: t("context.history"),
      tokens: history,
      note: t("context.historyCount", { kept: use.kept, total: use.total }),
      estimated: true,
    },
    { key: "question", label: t("context.question"), tokens: question, estimated: true },
    { key: "generated", label: t("context.generated"), tokens: generated, estimated: false },
  ];
  // The reply room shrinks as the model writes into it; what it has written is shown apart.
  const reply = Math.max(0, use.reply - generated);
  const taken = instructions + sources + history + question + generated + reply;
  list.push({ key: "reply", label: t("context.reply"), tokens: reply, estimated: false });
  list.push({
    key: "free",
    label: t("context.free"),
    tokens: Math.max(0, use.window - taken),
    estimated: true,
  });
  return list.filter((s) => s.tokens > 0 || s.key === "history");
}

/**
 * The chat's context window above the question box: a bar and "Context ≈2.1k / 8.2k tokens",
 * with a Details button that lists what fills it, part by part.
 */
export default function ContextMeter({ use }: { use: ContextUse }) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const used = usedTokens(use);
  const share = Math.min(1, used / use.window);
  const dropped = use.total - use.kept;
  const label = t(use.measured ? "chat.context" : "chat.contextEstimate", {
    used: compactTokens(used),
    window: compactTokens(use.window),
  });
  const parts = segments(use, t);
  return (
    <div className={open ? "context-panel open" : "context-panel"}>
      <div className="context-summary">
        <span
          className={share >= 0.9 ? "context-bar full" : "context-bar"}
          role="meter"
          aria-label={label}
          aria-valuemin={0}
          aria-valuemax={use.window}
          aria-valuenow={Math.min(used, use.window)}
        >
          <span style={{ width: `${share * 100}%` }} />
        </span>
        <span className="context-label">{label}</span>
        <span className="context-memory">
          {dropped > 0
            ? t("chat.contextLeftOut", { count: dropped })
            : t("context.memory", { kept: use.kept })}
        </span>
        <button
          type="button"
          className="context-toggle"
          aria-expanded={open}
          onClick={() => setOpen(!open)}
        >
          {t("context.details")}
          {open ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
        </button>
      </div>
      {open && (
        <div className="context-details">
          <div className="context-stack" aria-hidden="true">
            {parts.map((s) => (
              <span
                key={s.key}
                className={`seg seg-${s.key}`}
                style={{ flexGrow: s.tokens }}
                title={`${s.label}: ${s.tokens}`}
              />
            ))}
          </div>
          <ul className="context-parts">
            {parts.map((s) => (
              <li key={s.key}>
                <span className={`swatch seg-${s.key}`} />
                <span className="part-name">
                  {s.label}
                  {s.note && <span className="part-note">{s.note}</span>}
                </span>
                <span className="part-tokens">
                  {s.estimated && s.key !== "free" ? "≈" : ""}
                  {s.tokens.toLocaleString()}
                </span>
              </li>
            ))}
          </ul>
          <p className="context-foot">
            {use.measured
              ? t("context.measured", {
                  prompt: use.measured.prompt.toLocaleString(),
                  generated: use.measured.generated.toLocaleString(),
                })
              : t("context.estimated")}
            {dropped > 0 && ` ${t("chat.contextDropped", { kept: use.kept, total: use.total })}`}
          </p>
        </div>
      )}
    </div>
  );
}
