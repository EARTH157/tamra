import { useT } from "./i18n";

/** How full a chat's last prompt made the model's context window (spec §6). */
export type ContextUse = {
  /** Tokens in use: the core's estimate of the prompt, then the local model's own count. */
  used: number;
  window: number;
  /** True once the local model reported the real count (prompt plus what it has written). */
  measured: boolean;
  /** Earlier exchanges in the prompt, of the chat's answered ones. */
  kept: number;
  total: number;
};

/** 950, 2.1k, 32k */
export function compactTokens(n: number): string {
  if (n < 1000) return String(n);
  const k = n / 1000;
  return `${k >= 10 ? Math.round(k) : Math.round(k * 10) / 10}k`;
}

/** "Context ≈2.1k / 8.2k tokens" with a thin bar; says when older exchanges were left out. */
export default function ContextMeter({ use }: { use: ContextUse }) {
  const t = useT();
  const share = Math.min(1, use.used / use.window);
  const dropped = use.total - use.kept;
  const label = t(use.measured ? "chat.context" : "chat.contextEstimate", {
    used: compactTokens(use.used),
    window: compactTokens(use.window),
  });
  const memory =
    dropped > 0
      ? t("chat.contextDropped", { kept: use.kept, total: use.total })
      : t("chat.contextAll", { kept: use.kept });
  return (
    <span
      className={share >= 0.9 ? "context-meter full" : "context-meter"}
      role="meter"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={use.window}
      aria-valuenow={Math.min(use.used, use.window)}
      title={memory}
    >
      <span className="context-bar">
        <span style={{ width: `${share * 100}%` }} />
      </span>
      {label}
      {dropped > 0 && <span className="context-dropped">{t("chat.contextLeftOut", { count: dropped })}</span>}
    </span>
  );
}
