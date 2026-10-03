import { type FormEvent, useState } from "react";
import { api } from "./api";
import type { CollectionState } from "./types";

type Props = { onDone: (state: CollectionState) => void; onCancel?: () => void };

/** Choose the folder of documents to index. */
export default function Setup({ onDone, onCancel }: Props) {
  const [folder, setFolder] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [working, setWorking] = useState(false);

  async function browse() {
    setError(null);
    try {
      const picked = await api<{ folder_path: string | null }>("POST", "/api/pick-folder");
      if (picked.folder_path) setFolder(picked.folder_path);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setWorking(true);
    setError(null);
    try {
      const state = await api<CollectionState>("PUT", "/api/collection", {
        name: "",
        folder_path: folder.trim(),
      });
      onDone(state);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setWorking(false);
    }
  }

  return (
    <form className="setup" onSubmit={submit}>
      <h2>Choose a folder of documents</h2>
      <p className="muted">
        Tamra indexes the PDF, Word, text, and Markdown files in this folder and its subfolders.
        It never changes them.
      </p>
      <div className="setup-row">
        <input
          aria-label="Folder path"
          placeholder="Full folder path"
          value={folder}
          onChange={(e) => setFolder(e.target.value)}
        />
        <button type="button" onClick={browse}>
          Browse…
        </button>
      </div>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      <div className="setup-row">
        <button type="submit" disabled={working || !folder.trim()}>
          Use this folder
        </button>
        {onCancel && (
          <button type="button" onClick={onCancel}>
            Cancel
          </button>
        )}
      </div>
    </form>
  );
}
