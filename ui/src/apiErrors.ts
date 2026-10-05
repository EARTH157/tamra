import { ApiError } from "./api";
import type { Params, TranslationKey } from "./i18n";

type Translate = (key: TranslationKey, params?: Params) => string;

/**
 * The text to show for a failed call. Refusals the core words in English for developers are
 * shown in the user's language; anything unknown keeps the core's own text.
 */
export function errorText(error: Error, t: Translate): string {
  if (error instanceof ApiError) {
    if (error.status === 400 && error.message.startsWith("invalid value for setting api_base_url")) {
      return t("settings.baseUrlInvalid");
    }
    if (error.status === 409 && error.message === "Another download is running.") {
      return t("settings.downloadBusy");
    }
  }
  return error.message;
}

/**
 * The text to show when the viewer cannot show a file or a page: a sentence of our own for the
 * statuses the viewer routes answer with, one for a call that never got an answer, and the core's
 * own words only for anything else.
 */
export function viewerErrorText(error: Error, t: Translate): string {
  if (!(error instanceof ApiError)) return t("viewer.err.network");
  if (error.status === 404) return t("viewer.err.notFound");
  if (error.status === 422) return t("viewer.err.unreadable");
  if (error.status === 400) return t("viewer.err.cannotShow");
  return `${t("viewer.failed")} ${error.message}`;
}
