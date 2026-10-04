import { ArrowUp, Lightbulb, Square } from "lucide-react";
import { type FormEvent, type KeyboardEvent, useLayoutEffect, useRef } from "react";
import { useT } from "./i18n";
import ModeMenu from "./ModeMenu";
import ModelMenu from "./ModelMenu";
import type { AnswerMode, ModelsInfo, Settings, SettingsChanges } from "./types";

type Props = {
  question: string;
  onQuestionChange: (question: string) => void;
  onSubmit: () => void;
  onStop: () => void;
  /** The chat is not loaded yet, so nothing can be asked. */
  disabled: boolean;
  /** An answer is being written: the send button becomes Stop. */
  answering: boolean;
  /** The empty chat: the composer is centred and narrower. */
  narrow: boolean;
  mode: AnswerMode;
  onModeChange: (mode: AnswerMode) => void;
  think: boolean;
  onThinkChange: (think: boolean) => void;
  /** False when the model in use cannot think longer: the chip is disabled. */
  thinkAvailable: boolean;
  models: ModelsInfo | null;
  refreshModels: () => Promise<void>;
  settings: Settings;
  onChooseModel: (changes: SettingsChanges) => void;
  onManageModels: () => void;
};

/** The question box with its chips (mode, model, Think longer) and the send or stop button. */
export default function Composer({
  question,
  onQuestionChange,
  onSubmit,
  onStop,
  disabled,
  answering,
  narrow,
  mode,
  onModeChange,
  think,
  onThinkChange,
  thinkAvailable,
  models,
  refreshModels,
  settings,
  onChooseModel,
  onManageModels,
}: Props) {
  const t = useT();
  const box = useRef<HTMLTextAreaElement>(null);

  // Grow the question box with its text, up to the CSS max-height.
  useLayoutEffect(() => {
    const el = box.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  }, [question]);

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      onSubmit();
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    onSubmit();
  }

  const thinkOn = think && thinkAvailable;
  return (
    <form className={narrow ? "composer narrow" : "composer"} onSubmit={submit}>
      <div className="composer-card">
        <textarea
          ref={box}
          aria-label={t("chat.questionLabel")}
          placeholder={t("chat.placeholder")}
          rows={1}
          value={question}
          maxLength={4000}
          onChange={(e) => onQuestionChange(e.target.value)}
          onKeyDown={onKeyDown}
          disabled={disabled}
        />
        <div className="composer-actions">
          <div className="chips">
            <ModeMenu mode={mode} onChange={onModeChange} />
            <ModelMenu
              models={models}
              settings={settings}
              onChoose={onChooseModel}
              onManage={onManageModels}
              refresh={refreshModels}
            />
            <button
              type="button"
              className={thinkOn ? "chip on" : "chip"}
              aria-pressed={thinkOn}
              disabled={!thinkAvailable}
              title={thinkAvailable ? undefined : t("chat.thinkUnavailable")}
              onClick={() => onThinkChange(!think)}
            >
              <Lightbulb size={14} />
              {t("chat.think")}
            </button>
          </div>
          {answering ? (
            <button type="button" className="send" aria-label={t("chat.stop")} onClick={onStop}>
              <Square size={12} fill="currentColor" />
            </button>
          ) : (
            <button
              type="submit"
              className="send"
              aria-label={t("chat.send")}
              disabled={disabled || !question.trim()}
            >
              <ArrowUp size={18} />
            </button>
          )}
        </div>
      </div>
      <p className="composer-hint">{t("chat.composerHint")}</p>
    </form>
  );
}
