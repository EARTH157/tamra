import { createContext, useCallback, useContext } from "react";
import { en, type TranslationKey } from "./strings/en";
import { th } from "./strings/th";

export type Language = "en" | "th";
export type { TranslationKey };
export type Params = Record<string, string | number>;

const en_: Record<string, string> = en;
const tables: Record<Language, Record<string, string>> = { en: en_, th };

/** The language `t()` uses; the settings provider keeps it in step with the saved setting. */
let current: Language = "en";

export function setLanguage(language: Language): void {
  current = language;
}

export function getLanguage(): Language {
  return current;
}

/**
 * The text of a key in a language. A key missing in Thai falls back to English; a key missing
 * everywhere returns the key itself. With `count: 1` a "<key>.one" entry is used when it exists.
 * `{name}` placeholders are replaced from params and left as written when no param matches.
 */
export function translate(language: Language, key: string, params?: Params): string {
  const table = tables[language];
  let text = table[key] ?? en_[key] ?? key;
  if (params?.count === 1) text = table[`${key}.one`] ?? en_[`${key}.one`] ?? text;
  if (!params) return text;
  return text.replace(/\{(\w+)\}/g, (placeholder, name: string) =>
    name in params ? String(params[name]) : placeholder,
  );
}

/** Translate with the current language. Components should prefer useT(), which re-renders them. */
export function t(key: TranslationKey, params?: Params): string {
  return translate(current, key, params);
}

/** The language of the nearest settings provider ("en" without one). */
export const LanguageContext = createContext<Language>("en");

/** `t` bound to the language in React context, so a component re-renders when it changes. */
export function useT(): (key: TranslationKey, params?: Params) => string {
  const language = useContext(LanguageContext);
  return useCallback(
    (key: TranslationKey, params?: Params) => translate(language, key, params),
    [language],
  );
}
