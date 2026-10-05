import { describe, expect, it } from "vitest";
import { ApiError } from "./api";
import { errorText } from "./apiErrors";
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
