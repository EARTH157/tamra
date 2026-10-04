import { ChevronDown } from "lucide-react";
import type { ReactNode } from "react";

/** A titled group of rows on the Settings page: a heading above a white card. */
export function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="settings-section">
      <h2 className="settings-heading">{title}</h2>
      {children}
    </section>
  );
}

/** One setting: its name and an optional hint on the left, the control on the right. */
export function Row({
  title,
  hint,
  titleId,
  children,
}: {
  title: string;
  hint?: string;
  titleId?: string;
  children?: ReactNode;
}) {
  return (
    <div className="settings-row">
      <div className="settings-row-text">
        <div className="settings-row-title" id={titleId}>
          {title}
        </div>
        {hint && <div className="settings-row-hint">{hint}</div>}
      </div>
      {children && <div className="settings-row-control">{children}</div>}
    </div>
  );
}

/** An on/off switch. */
export function Toggle({
  checked,
  label,
  onChange,
}: {
  checked: boolean;
  label: string;
  onChange: (next: boolean) => void;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      className="toggle"
      onClick={() => onChange(!checked)}
    >
      <span className="toggle-thumb" />
    </button>
  );
}

/** A native select with the app's look. */
export function Select<T extends string>({
  value,
  label,
  options,
  onChange,
}: {
  value: T;
  label: string;
  options: { value: T; label: string }[];
  onChange: (next: T) => void;
}) {
  return (
    <span className="select">
      <select aria-label={label} value={value} onChange={(e) => onChange(e.target.value as T)}>
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
      <ChevronDown size={14} />
    </span>
  );
}

/** A row of buttons of which exactly one is chosen (radio semantics). */
export function Segmented<T extends string>({
  value,
  label,
  options,
  onChange,
}: {
  value: T;
  label: string;
  options: { value: T; label: string }[];
  onChange: (next: T) => void;
}) {
  return (
    <div className="segmented" role="radiogroup" aria-label={label}>
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          role="radio"
          aria-checked={option.value === value}
          onClick={() => onChange(option.value)}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

/** The round radio mark of a card or a model row. */
export function RadioMark({ on }: { on: boolean }) {
  return <span className={on ? "radio-mark on" : "radio-mark"} aria-hidden="true" />;
}
