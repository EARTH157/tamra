import { AlignLeft, BookOpen, Check, Folder, Lock } from "lucide-react";
import { useT } from "./i18n";

/** First run: no folder of documents has been chosen yet. */
export default function Welcome({ onChoose }: { onChoose: () => void }) {
  const t = useT();
  return (
    <div className="hero">
      <span className="hero-tile">
        <BookOpen size={26} />
      </span>
      <h1>{t("welcome.title")}</h1>
      <p className="hero-sub">
        {t("welcome.subtitle")}
      </p>
      <div className="add-card">
        <Folder size={28} strokeWidth={1.75} />
        <h2>{t("welcome.addTitle")}</h2>
        <p>{t("welcome.addText")}</p>
        <button type="button" className="btn primary" onClick={onChoose}>
          {t("welcome.choose")}
        </button>
      </div>
      <div className="badges">
        <span>
          <Lock size={13} />
          {t("welcome.offline")}
        </span>
        <span>
          <Check size={13} />
          {t("welcome.cites")}
        </span>
        <span>
          <AlignLeft size={13} />
          {t("welcome.languages")}
        </span>
      </div>
    </div>
  );
}
