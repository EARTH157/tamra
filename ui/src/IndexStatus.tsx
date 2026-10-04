import { AlertTriangle, Check, Folder } from "lucide-react";
import { useState } from "react";
import type { Collection, IndexStatus as IndexState, ProblemFile } from "./types";

type Props = {
  collection: Collection;
  index: IndexState | null;
  disabled: boolean;
  onChangeFolder: () => void;
  onRebuild: () => void;
};

const files = (n: number) => (n === 1 ? "1 file" : `${n} files`);

/** "Failed: <error>", "Skipped: <error>", or the note of a file indexed with missing parts. */
function problemText(problem: ProblemFile): string {
  const prefix = problem.status === "failed" ? "Failed" : problem.status === "skipped" ? "Skipped" : "";
  const error = problem.error ?? "";
  return prefix && error ? `${prefix}: ${error}` : prefix || error;
}

/** The sidebar card: the chosen folder, indexing progress, and files that need attention. */
export default function IndexStatus({
  collection,
  index,
  disabled,
  onChangeFolder,
  onRebuild,
}: Props) {
  const [showProblems, setShowProblems] = useState(false);
  const counts = index?.counts;
  const total = counts ? Object.values(counts).reduce((sum, n) => sum + n, 0) : 0;
  const waiting = counts ? counts.pending + counts.indexing : 0;
  const problems = index?.problems ?? [];

  return (
    <section className="collection-card" aria-label="Documents folder">
      <div className="collection-name">
        <Folder size={16} />
        <span>{collection.name}</span>
      </div>
      <div className="collection-path" title={collection.folder_path}>
        {collection.folder_path}
      </div>
      {index?.stale ? (
        <p className="stale-note">
          This index was built with a different embedding model. Rebuild it to search again.
          <button type="button" className="link-button" onClick={onRebuild} disabled={disabled}>
            Rebuild index
          </button>
        </p>
      ) : !counts ? (
        <div className="collection-count done">Checking files…</div>
      ) : total === 0 ? (
        <div className="collection-count done">No documents found yet</div>
      ) : waiting > 0 ? (
        <>
          <div
            className="progress"
            role="progressbar"
            aria-label="Indexing progress"
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
            {counts.indexed} of {total} files indexed
          </div>
          {index?.current && (
            <div className="collection-current" title={index.current}>
              Indexing {index.current}
            </div>
          )}
        </>
      ) : (
        <div className="collection-count done">
          <Check size={14} />
          {counts.indexed === total
            ? `${files(total)} indexed`
            : `${counts.indexed} of ${total} files indexed`}
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
            {problems.length === 1
              ? "1 file needs attention"
              : `${problems.length} files need attention`}
          </button>
          {showProblems && (
            <ul className="problems">
              {problems.map((problem) => (
                <li key={problem.rel_path}>
                  {problem.rel_path}
                  <span className="problem-error" title={problemText(problem)}>
                    {problemText(problem)}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
      <button type="button" className="link-button" onClick={onChangeFolder} disabled={disabled}>
        Change folder
      </button>
    </section>
  );
}
