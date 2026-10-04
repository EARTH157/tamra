import { AlignLeft, BookOpen, Check, Folder, Lock } from "lucide-react";

/** First run: no folder of documents has been chosen yet. */
export default function Welcome({ onChoose }: { onChoose: () => void }) {
  return (
    <div className="hero">
      <span className="hero-tile">
        <BookOpen size={26} />
      </span>
      <h1>Welcome to Tamra</h1>
      <p className="hero-sub">
        Ask questions about your own documents and check every answer against its source.
      </p>
      <div className="add-card">
        <Folder size={28} strokeWidth={1.75} />
        <h2>Add a folder of documents</h2>
        <p>Choose a folder of PDF, Word, text, or Markdown files.</p>
        <button type="button" className="btn primary" onClick={onChoose}>
          Choose a folder
        </button>
      </div>
      <div className="badges">
        <span>
          <Lock size={13} />
          Works offline
        </span>
        <span>
          <Check size={13} />
          Answers cite their source
        </span>
        <span>
          <AlignLeft size={13} />
          Thai, English, and Chinese
        </span>
      </div>
    </div>
  );
}
