import { Check, ChevronDown, Folder } from "lucide-react";
import { useState } from "react";
import { type TranslationKey, useT } from "./i18n";
import PopMenu from "./PopMenu";
import type { Project } from "./types";

type Translate = (key: TranslationKey, params?: Record<string, string | number>) => string;

type Props = {
  projects: Project[];
  /** The ids of the projects searched; never empty. */
  scope: number[];
  /** The chat's home project: listed first in the menu and named first on the chip. */
  homeId: number | null;
  /** An answer is being written, or the chat is not loaded: the scope cannot change. */
  disabled: boolean;
  onChange: (scope: number[]) => void;
};

/** The projects searched, home first. */
function searched(projects: Project[], scope: number[], homeId: number | null): Project[] {
  const chosen = projects.filter((project) => scope.includes(project.id));
  return [...chosen.filter((p) => p.id === homeId), ...chosen.filter((p) => p.id !== homeId)];
}

/** "HR Documents" for one project, "HR Documents + 1" when the scope is wider. */
export function scopeLabel(
  projects: Project[],
  scope: number[],
  homeId: number | null,
  t: Translate,
): string {
  const [first, ...rest] = searched(projects, scope, homeId);
  if (!first) return "";
  return rest.length === 0 ? first.name : t("chat.scope.more", { name: first.name, count: rest.length });
}

/**
 * The "Search in" chip and its menu: a checkbox per project, home first. The menu stays open while
 * projects are ticked; the last ticked project cannot be unticked.
 */
export default function ScopeMenu({ projects, scope, homeId, disabled, onChange }: Props) {
  const t = useT();
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  const label = scopeLabel(projects, scope, homeId, t);
  const home = projects.find((project) => project.id === homeId);
  const ordered = home ? [home, ...projects.filter((project) => project !== home)] : projects;

  function toggle(id: number) {
    if (scope.includes(id)) {
      if (scope.length > 1) onChange(scope.filter((other) => other !== id));
    } else {
      onChange([...scope, id]);
    }
  }

  return (
    <>
      <button
        type="button"
        className={anchor ? "chip open" : "chip"}
        aria-haspopup="menu"
        aria-expanded={anchor !== null}
        aria-label={`${t("chat.scope.menu")}: ${label}`}
        title={label}
        disabled={disabled}
        onClick={(event) => setAnchor(anchor ? null : event.currentTarget)}
      >
        <Folder size={14} />
        <span className="chip-text">{label}</span>
        <ChevronDown size={13} className="chip-chevron" />
      </button>
      {anchor && !disabled && (
        <PopMenu
          anchor={anchor}
          label={t("chat.scope.menu")}
          onClose={(refocus) => {
            setAnchor(null);
            if (refocus) anchor.focus();
          }}
        >
          <div className="menu-heading">{t("chat.scope.menu")}</div>
          {ordered.map((project) => {
            const on = scope.includes(project.id);
            const last = on && scope.length === 1;
            return (
              <button
                key={project.id}
                type="button"
                role="menuitemcheckbox"
                aria-checked={on}
                aria-disabled={last || undefined}
                title={last ? t("chat.scope.last") : undefined}
                className="menu-entry"
                onClick={() => toggle(project.id)}
              >
                <Folder size={16} className="entry-icon" />
                <span className="entry-text">
                  <span className="entry-name">{project.name}</span>
                  {project.id === homeId && (
                    <span className="entry-note">{t("chat.scope.home")}</span>
                  )}
                </span>
                {on && <Check size={16} className="entry-check" />}
              </button>
            );
          })}
        </PopMenu>
      )}
    </>
  );
}
