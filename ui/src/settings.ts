import {
  createContext,
  createElement,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { api } from "./api";
import { LanguageContext, setLanguage } from "./i18n";
import type { Settings, SettingsChanges } from "./types";

/** What the app shows until /api/settings answers; the same as the core's defaults. */
export const DEFAULT_SETTINGS: Settings = {
  mode: "local",
  local_model_id: null,
  api_provider: "anthropic",
  api_model: "claude-sonnet-5-5",
  api_base_url: "",
  language: "en",
  theme: "light",
  accent: "green",
  text_size: "default",
  spacing: "comfortable",
  ask_before_delete: true,
  api_key_set: false,
  api_key_hint: null,
};

const RETRY_MS = 3000; // wait before asking again when the core did not answer
const DARK_QUERY = "(prefers-color-scheme: dark)";

export type SettingsApi = {
  settings: Settings;
  /** True once the core has answered; before that `settings` holds the defaults. */
  loaded: boolean;
  /**
   * Save changes. The new values show at once; if the core refuses them they are put back and
   * the returned promise rejects with the ApiError, so the caller can show the message.
   */
  update: (changes: SettingsChanges) => Promise<void>;
  /** Load the settings again, e.g. after the API key changed (api_key_set is not a setting). */
  reload: () => Promise<void>;
};

const SettingsContext = createContext<SettingsApi>({
  settings: DEFAULT_SETTINGS,
  loaded: false,
  update: () => Promise.reject(new Error("useSettings needs a SettingsProvider.")),
  reload: () => Promise.reject(new Error("useSettings needs a SettingsProvider.")),
});

export function useSettings(): SettingsApi {
  return useContext(SettingsContext);
}

/** "system" follows the operating system's colour scheme. */
export function resolveTheme(theme: Settings["theme"], systemDark: boolean): "light" | "dark" {
  return theme === "system" ? (systemDark ? "dark" : "light") : theme;
}

/** Put the style settings on <html>: styles.css reacts to these attributes. */
export function applyAppearance(
  root: HTMLElement,
  settings: Pick<Settings, "theme" | "accent" | "text_size" | "spacing" | "language">,
  systemDark: boolean,
): void {
  root.dataset.theme = resolveTheme(settings.theme, systemDark);
  root.dataset.accent = settings.accent;
  root.dataset.textSize = settings.text_size;
  root.dataset.spacing = settings.spacing;
  root.lang = settings.language;
}

function prefersDark(): boolean {
  return typeof window.matchMedia === "function" && window.matchMedia(DARK_QUERY).matches;
}

/** Loads the settings once, applies the style settings, and provides the language. */
export function SettingsProvider({
  children,
  retryMs = RETRY_MS,
}: {
  children: ReactNode;
  retryMs?: number;
}) {
  const [settings, setSettings] = useState<Settings>(DEFAULT_SETTINGS);
  const [loaded, setLoaded] = useState(false);
  const [systemDark, setSystemDark] = useState(prefersDark);
  // What the core has confirmed, and the changes still waiting for its answer. The settings
  // shown are the confirmed ones with every pending change on top, so a failed or finished
  // update just leaves the list and nothing needs to be "put back" by hand.
  const confirmed = useRef(DEFAULT_SETTINGS);
  const pending = useRef<SettingsChanges[]>([]);

  const show = useCallback(() => {
    setSettings(Object.assign({}, confirmed.current, ...pending.current));
  }, []);

  const fetchSettings = useCallback(async () => {
    confirmed.current = await api<Settings>("GET", "/api/settings");
    setLoaded(true);
    show();
  }, [show]);

  // Load once; while the core is unreachable, try again until it answers.
  useEffect(() => {
    let alive = true;
    let timer: number | undefined;
    async function attempt() {
      try {
        await fetchSettings();
      } catch {
        if (alive) timer = window.setTimeout(() => void attempt(), retryMs);
      }
    }
    void attempt();
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, [fetchSettings, retryMs]);

  // Follow the operating system's colour scheme (it only matters for theme "system").
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const query = window.matchMedia(DARK_QUERY);
    const onChange = () => setSystemDark(query.matches);
    onChange();
    query.addEventListener("change", onChange);
    return () => query.removeEventListener("change", onChange);
  }, []);

  setLanguage(settings.language); // before the children render, so translateNow() agrees

  // Until the core answers, follow the operating system so a dark one does not flash light.
  useLayoutEffect(() => {
    const shown = loaded ? settings : { ...settings, theme: "system" as const };
    applyAppearance(document.documentElement, shown, systemDark);
  }, [settings, loaded, systemDark]);

  const update = useCallback(
    async (changes: SettingsChanges) => {
      const keys = Object.keys(changes) as (keyof SettingsChanges)[];
      if (keys.length === 0) return;
      const entry = { ...changes }; // its own identity, even if the caller reuses the object
      pending.current.push(entry);
      show();
      try {
        const saved = await api<Settings>("PUT", "/api/settings", changes);
        // Confirm the core's values for what changed, and the key state that follows the provider.
        const next = {
          ...confirmed.current,
          api_key_set: saved.api_key_set,
          api_key_hint: saved.api_key_hint,
        };
        for (const key of keys) Object.assign(next, { [key]: saved[key] });
        confirmed.current = next;
      } finally {
        // Done or refused: either way this change is no longer pending. A refused one disappears
        // and the confirmed value (or a newer pending change) shows again.
        pending.current = pending.current.filter((other) => other !== entry);
        show();
      }
    },
    [show],
  );

  const value = useMemo(
    () => ({ settings, loaded, update, reload: fetchSettings }),
    [settings, loaded, update, fetchSettings],
  );
  return createElement(
    SettingsContext.Provider,
    { value },
    createElement(LanguageContext.Provider, { value: settings.language }, children),
  );
}
