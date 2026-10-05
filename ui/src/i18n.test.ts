import { afterEach, describe, expect, it } from "vitest";
import { getLanguage, setLanguage, translate, translateNow } from "./i18n";
import { en } from "./strings/en";
import { th } from "./strings/th";

afterEach(() => setLanguage("en"));

const placeholders = (text: string) => [...text.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort();

describe("string tables", () => {
  it("have the same keys in English and Thai", () => {
    expect(Object.keys(th).sort()).toEqual(Object.keys(en).sort());
  });

  it("have no empty text", () => {
    for (const table of [en, th]) {
      for (const [key, text] of Object.entries(table)) expect(text.trim(), key).not.toBe("");
    }
  });

  it("use the same placeholders in both languages", () => {
    for (const key of Object.keys(en) as (keyof typeof en)[]) {
      expect(placeholders(th[key]), key).toEqual(placeholders(en[key]));
    }
  });

  it("keep the backslashes of the Windows path example", () => {
    for (const language of ["en", "th"] as const) {
      expect(translate(language, "folder.pathPlaceholder"), language).toContain("D:\\Work\\Documents");
    }
  });

  it("give every .one text a base key", () => {
    for (const key of Object.keys(en).filter((k) => k.endsWith(".one"))) {
      expect(Object.keys(en), key).toContain(key.slice(0, -4));
    }
  });
});

describe("translate", () => {
  it("returns the text in the language asked for", () => {
    expect(translate("en", "common.cancel")).toBe("Cancel");
    expect(translate("th", "common.cancel")).toBe("ยกเลิก");
  });

  it("falls back to English, then to the key", () => {
    const table = th as Record<string, string>;
    const saved = table["common.cancel"];
    delete table["common.cancel"];
    try {
      expect(translate("th", "common.cancel")).toBe("Cancel");
    } finally {
      table["common.cancel"] = saved;
    }
    expect(translate("th", "no.such.key")).toBe("no.such.key");
  });

  it("fills {placeholders} and leaves unknown ones as written", () => {
    expect(translate("en", "chatList.options", { title: "Leave policy" })).toBe(
      "Options for Leave policy",
    );
    expect(translate("en", "chatList.options", { other: 1 })).toBe("Options for {title}");
    expect(translate("en", "index.partial", { indexed: 3, total: 9 })).toBe("3 of 9 files indexed");
  });

  it("uses the .one text for a count of 1 only", () => {
    expect(translate("en", "index.needAttention", { count: 1 })).toBe("1 file needs attention");
    expect(translate("en", "index.needAttention", { count: 2 })).toBe("2 files need attention");
    expect(translate("en", "index.needAttention", { count: 0 })).toBe("0 files need attention");
    expect(translate("th", "index.needAttention", { count: 2 })).toBe("2 ไฟล์ต้องตรวจสอบ");
  });
});

describe("translateNow", () => {
  it("is English until a language is set", () => {
    expect(getLanguage()).toBe("en");
    expect(translateNow("common.close")).toBe("Close");
  });

  it("follows setLanguage", () => {
    setLanguage("th");
    expect(translateNow("common.close")).toBe("ปิด");
    expect(translateNow("chat.notFoundTitle", { name: "HR Documents" })).toBe("ไม่พบใน HR Documents");
  });
});
