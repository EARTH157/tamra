import { describe, expect, it } from "vitest";
import { ApiError } from "./api";
import { attributionErrorText, errorText, viewerErrorText } from "./apiErrors";
import { type Params, translate, type TranslationKey } from "./i18n";

const en = (key: TranslationKey, params?: Params) => translate("en", key, params);
const th = (key: TranslationKey, params?: Params) => translate("th", key, params);

describe("errorText", () => {
  it("words the Base URL refusal in the user's language", () => {
    const refused = new ApiError(400, "invalid value for setting api_base_url: 'ftp://x'");
    expect(errorText(refused, en)).toContain("must start with http://");
    expect(errorText(refused, th)).toContain("http://");
    expect(errorText(refused, th)).not.toContain("invalid value");
  });

  it("words the busy download refusal in the user's language", () => {
    const busy = new ApiError(409, "Another download is running.");
    expect(errorText(busy, en)).toBe(
      "Another model is downloading. Wait for it to finish, then try again.",
    );
    expect(errorText(busy, th)).not.toBe(errorText(busy, en));
  });

  it("keeps the core's text for anything unknown", () => {
    expect(errorText(new ApiError(400, "invalid value for setting api_model: ''"), en)).toBe(
      "invalid value for setting api_model: ''",
    );
    expect(errorText(new ApiError(409, "Already installed."), en)).toBe("Already installed.");
    expect(errorText(new Error("network down"), en)).toBe("network down");
  });
});

describe("attributionErrorText", () => {
  it("says the search model is not available for a 503", () => {
    const error = new ApiError(503, "The embedding model is unavailable.");
    expect(attributionErrorText(error, en)).toBe(
      "The search model is not available, so the source cannot be checked.",
    );
    expect(attributionErrorText(error, th)).not.toBe(attributionErrorText(error, en));
  });

  it("says the answer is no longer saved for a 404", () => {
    const error = new ApiError(404, "Message not found.");
    expect(attributionErrorText(error, en)).toBe("This answer is no longer saved.");
    expect(attributionErrorText(error, th)).not.toBe(attributionErrorText(error, en));
  });

  it("says the core cannot be reached when there was no answer", () => {
    expect(attributionErrorText(new TypeError("Failed to fetch"), en)).toBe(
      "Tamra could not reach its core.",
    );
  });

  it("keeps the core's text for anything else", () => {
    expect(attributionErrorText(new ApiError(400, "Only an answer has sources."), en)).toBe(
      "Only an answer has sources.",
    );
    expect(attributionErrorText(new ApiError(500, "HTTP 500"), en)).toBe("HTTP 500");
  });
});

describe("viewerErrorText", () => {
  it("separates the 404s of the viewer routes by what is missing", () => {
    expect(viewerErrorText(new ApiError(404, "File not found."), en)).toBe(
      "This file was moved or deleted.",
    );
    expect(viewerErrorText(new ApiError(404, "Page not found."), en)).toBe(
      "That page is not in this file.",
    );
    expect(viewerErrorText(new ApiError(404, "Source not found."), en)).toBe(
      "This answer is no longer saved.",
    );
    expect(viewerErrorText(new ApiError(404, "This file has no pages."), en)).toBe(
      "This file was moved or deleted.",
    );
    expect(viewerErrorText(new ApiError(404, "Page not found."), th)).not.toBe(
      viewerErrorText(new ApiError(404, "File not found."), th),
    );
  });

  it("keeps the wording of the other statuses", () => {
    expect(viewerErrorText(new ApiError(422, "x"), en)).toBe("This file could not be read.");
    expect(viewerErrorText(new ApiError(400, "x"), en)).toBe("This file cannot be shown.");
    expect(viewerErrorText(new TypeError("Failed to fetch"), en)).toBe(
      "Tamra could not reach its core.",
    );
  });
});
