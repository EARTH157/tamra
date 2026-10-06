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

/** The core's fixed refusals about projects (src/tamra/server.py), by their English text. */
const PROJECT_ERRORS: Record<string, TranslationKey> = {
  "Another project already has this name.": "project.err.nameTaken",
  "This folder is already used by another project.": "project.err.folderUsed",
  "Folder not found.": "project.err.folderNotFound",
  "The project name must be 1 to 80 characters, without control or format characters.":
    "project.err.nameLength",
  "Choose a folder of documents, not a whole drive or Tamra's own folder.":
    "project.err.folderNotAllowed",
  "The project could not be changed.": "project.err.generic",
  "The chat title must not be empty.": "project.err.titleEmpty",
  "Project not found.": "project.err.notFound",
  "Unknown project.": "project.err.notFound",
  "An answer is being written. Wait for it to finish.": "project.err.busy",
};

/** The text to show when a project or chat call was refused: the user's language for the core's fixed refusals. */
export function projectErrorText(error: Error, t: Translate): string {
  if (error instanceof ApiError) {
    const key = PROJECT_ERRORS[error.message];
    if (key) return t(key);
  }
  return errorText(error, t);
}

/**
 * The text to show when checking a selection failed: the search model is unavailable (503), the
 * answer is no longer saved (404), or the core could not be reached; anything else is worded as
 * for any other call.
 */
export function attributionErrorText(error: Error, t: Translate): string {
  if (!(error instanceof ApiError)) return t("viewer.err.network");
  if (error.status === 503) return t("check.err.model");
  if (error.status === 404) return t("check.err.gone");
  return errorText(error, t);
}

/**
 * The text to show when the viewer cannot show a file or a page: a sentence of our own for the
 * statuses the viewer routes answer with, one for a call that never got an answer, and the core's
 * own words only for anything else.
 */
export function viewerErrorText(error: Error, t: Translate): string {
  if (!(error instanceof ApiError)) return t("viewer.err.network");
  if (error.status === 404) {
    // The viewer routes word their 404s differently: only an unknown file is "moved or deleted".
    if (error.message === "Page not found.") return t("viewer.err.page");
    if (error.message === "Source not found.") return t("check.err.gone");
    return t("viewer.err.notFound");
  }
  if (error.status === 422) return t("viewer.err.unreadable");
  if (error.status === 400) return t("viewer.err.cannotShow");
  return `${t("viewer.failed")} ${error.message}`;
}
