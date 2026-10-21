import { AlertTriangle, Check, Folder } from "lucide-react";
import { useState } from "react";
import { useT } from "./i18n";
import type { IndexStatus as IndexState, Project, ProblemFile } from "./types";

type Translate = ReturnType<typeof useT>;

/** "Failed: <error>", "Skipped: <error>", or the note of a file indexed with missing parts. */
function problemText(problem: ProblemFile, t: Translate): string {
  if (problem.needs_ocr) return t("index.needsOcr");
  const error = problem.error ?? "";
  if (problem.status === "failed") {
    return error ? t("index.failed", { error }) : t("index.failedBare");
  }
  if (problem.status === "skipped") {
    return error ? t("index.skipped", { error }) : t("index.skippedBare");
  }
  return error;
}

/** How the indexer words a pause for the missing search model (src/tamra/ingest/indexer.py). */
const EMBEDDING_UNAVAILABLE = "Embedding model unavailable";

type BodyProps = {
  index: IndexState | null;
  /** Shows a Rebuild link in the note about a stale index. */
  onRebuild?: () => void;
  /** Shows a button to the AI model settings when indexing waits for the search model. */
  onOpenModelSettings?: () => void;
  disabled?: boolean;
};

/** A project's index: progress or the file count, a stale or error note, and files that need attention. */
export function IndexBody({ index, onRebuild, onOpenModelSettings, disabled }: BodyProps) {
  const t = useT();
  const [showProblems, setShowProblems] = useState(false);
  const counts = index?.counts;
  const total = counts ? Object.values(counts).reduce((sum, n) => sum + n, 0) : 0;
  const waiting = counts ? counts.pending + counts.indexing : 0;
  const problems = index?.problems ?? [];

  return (
    <>
      {index?.stale ? (
        <p className="stale-note">
          {t("index.stale")}
          {onRebuild && (
            <button type="button" className="link-button" onClick={onRebuild} disabled={disabled}>
              {t("index.rebuild")}
            </button>
          )}
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
          {index?.reading && (
            <div className="collection-current">
              {t("index.reading", { page: index.reading.page, pages: index.reading.pages })}
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
      {index?.error &&
        (index.error.startsWith(EMBEDDING_UNAVAILABLE) ? (
          <p className="index-error">
            {t("error.embeddingMissing")}
            {onOpenModelSettings && (
              <button type="button" className="link-button" onClick={onOpenModelSettings}>
                {t("chat.openModelSettings")}
              </button>
            )}
          </p>
        ) : (
          <p className="index-error">{index.error}</p>
        ))}
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
                  {problem.needs_ocr && onOpenModelSettings && (
                    <button type="button" className="link-button" onClick={onOpenModelSettings}>
                      {t("chat.openModelSettings")}
                    </button>
                  )}
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </>
  );
}

type Props = {
  project: Project;
  disabled: boolean;
  onChangeFolder: () => void;
  onRebuild: () => void;
  onOpenModelSettings?: () => void;
};

/** The sidebar card for the active project: its folder, indexing progress, and files that need attention. */
export default function IndexStatus({
  project,
  disabled,
  onChangeFolder,
  onRebuild,
  onOpenModelSettings,
}: Props) {
  const t = useT();
  return (
    <section className="collection-card" aria-label={t("index.cardLabel")}>
      <div className="collection-name">
        <Folder size={16} />
        <span>{project.name}</span>
      </div>
      <div className="collection-path" title={project.folder_path}>
        {project.folder_path}
      </div>
      <IndexBody
        index={project.index}
        onRebuild={onRebuild}
        onOpenModelSettings={onOpenModelSettings}
        disabled={disabled}
      />
      <button type="button" className="link-button" onClick={onChangeFolder} disabled={disabled}>
        {t("index.changeFolder")}
      </button>
    </section>
  );
}
