import type { Collection, IndexStatus as IndexState } from "./types";

type Props = {
  collection: Collection;
  index: IndexState | null;
  disabled: boolean;
  onChangeFolder: () => void;
  onRebuild: () => void;
};

/** The chosen folder, indexing progress, and files that could not be indexed fully. */
export default function IndexStatus({
  collection,
  index,
  disabled,
  onChangeFolder,
  onRebuild,
}: Props) {
  const counts = index?.counts;
  const total = counts ? Object.values(counts).reduce((sum, n) => sum + n, 0) : 0;
  const waiting = counts ? counts.pending + counts.indexing : 0;
  return (
    <section className="index-status">
      <div className="index-folder">
        <strong>{collection.name}</strong>
        <span className="muted">{collection.folder_path}</span>
        <button type="button" onClick={onChangeFolder} disabled={disabled}>
          Change folder
        </button>
      </div>
      {index?.stale ? (
        <p className="warning">
          This index was built with a different embedding model. Rebuild it to search again.{" "}
          <button type="button" onClick={onRebuild} disabled={disabled}>
            Rebuild index
          </button>
        </p>
      ) : (
        <p className="muted">
          {counts ? `${counts.indexed} of ${total} files indexed` : "Checking files…"}
          {waiting > 0 && index?.current ? ` · indexing ${index.current}` : ""}
        </p>
      )}
      {index?.error && <p className="error">{index.error}</p>}
      {index && index.problems.length > 0 && (
        <details className="problems">
          <summary>{index.problems.length} file(s) need attention</summary>
          <ul>
            {index.problems.map((problem) => (
              <li key={problem.rel_path}>
                {problem.rel_path}{" "}
                <span className="muted">
                  ({problem.status}) {problem.error}
                </span>
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}
