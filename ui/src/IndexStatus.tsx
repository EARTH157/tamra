import { AlertTriangle, Check, Folder } from "lucide-react";
import { useState } from "react";
import { useT } from "./i18n";
import type { Collection, IndexStatus as IndexState, ProblemFile } from "./types";

type Props = {
  collection: Collection;
  index: IndexState | null;
  disabled: boolean;
  onChangeFolder: () => void;
  onRebuild: () => void;
};

type Translate = ReturnType<typeof useT>;

/** "Failed: <error>", "Skipped: <error>", or the note of a file indexed with missing parts. */
function problemText(problem: ProblemFile, t: Translate): string {
  const error = problem.error ?? "";
  if (problem.status === "failed") {
    return error ? t("index.failed", { error }) : t("index.failedBare");
  }
  if (problem.status === "skipped") {
    return error ? t("index.skipped", { error }) : t("index.skippedBare");
  }
  return error;
}

/** The sidebar card: the chosen folder, indexing progress, and files that need attention. */
export default function IndexStatus({
  collection,
  index,
  disabled,
  onChangeFolder,
  onRebuild,
}: Props) {
  const t = useT();
  const [showProblems, setShowProblems] = useState(false);
  const counts = index?.counts;
  const total = counts ? Object.values(counts).reduce((sum, n) => sum + n, 0) : 0;
  const waiting = counts ? counts.pending + counts.indexing : 0;
  const problems = index?.problems ?? [];

  return (
    <section className="collection-card" aria-label={t("index.cardLabel")}>
      <div className="collection-name">
        <Folder size={16} />
        <span>{collection.name}</span>
      </div>
      <div className="collection-path" title={collection.folder_path}>
        {collection.folder_path}
      </div>
      {index?.stale ? (
        <p className="stale-note">
          {t("index.stale")}
          <button type="button" className="link-button" onClick={onRebuild} disabled={disabled}>
            {t("index.rebuild")}
          </button>
        </p>
      ) : !counts ? (
        <div className="collection-count done">{t("index.checking")}</div>
      ) : total === 0 ? (
        <div className="collection-count done">{t("index.none")}</div>
      ) : waiting > 0 ? (
        <>
          <div
            className="progress"
            role="progressbar"
            aria-label={t("index.progress")}
            aria-valuemin={0}
            aria-valuemax={total}
            aria-valuenow={counts.indexed}
          >
            <div
              className="progress-fill"
              style={{ width: `${total ? (counts.indexed / total) * 100 : 0}%` }}
            />
          </div>
          <div className="collection-count busy">
            {t("index.partial", { indexed: counts.indexed, total })}
          </div>
          {index?.current && (
            <div className="collection-current" title={index.current}>
              {t("index.current", { file: index.current })}
            </div>
          )}
        </>
      ) : (
        <div className="collection-count done">
          <Check size={14} />
          {counts.indexed === total
            ? t("index.filesIndexed", { count: total })
            : t("index.partial", { indexed: counts.indexed, total })}
        </div>
      )}
      {index?.error && <p className="index-error">{index.error}</p>}
      {problems.length > 0 && (
        <>
          <button
            type="button"
            className="attention"
            aria-expanded={showProblems}
            onClick={() => setShowProblems((shown) => !shown)}
          >
            <AlertTriangle size={14} />
            {t("index.needAttention", { count: problems.length })}
          </button>
          {showProblems && (
            <ul className="problems">
              {problems.map((problem) => (
                <li key={problem.rel_path}>
                  {problem.rel_path}
                  <span className="problem-error" title={problemText(problem, t)}>
                    {problemText(problem, t)}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
      <button type="button" className="link-button" onClick={onChangeFolder} disabled={disabled}>
        {t("index.changeFolder")}
      </button>
    </section>
  );
}
