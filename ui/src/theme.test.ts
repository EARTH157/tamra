import { describe, expect, it } from "vitest";
import css from "./styles.css?raw";

type Tokens = Record<string, string>;

/** The custom properties each :root rule of styles.css sets, by selector. */
function rootRules(): Map<string, Tokens> {
  const rules = new Map<string, Tokens>();
  for (const match of css.matchAll(/(:root(?:\[[^\]]+\])*)\s*\{([^}]*)\}/g)) {
    const tokens: Tokens = rules.get(match[1]) ?? {};
    for (const decl of match[2].matchAll(/(--[\w-]+)\s*:\s*([^;]+);/g)) {
      tokens[decl[1]] = decl[2].replace(/\/\*.*?\*\//g, "").trim();
    }
    rules.set(match[1], tokens);
  }
  return rules;
}

/** The tokens in force for a theme and accent, merged in the cascade's order. */
function effective(theme: "light" | "dark", accent: string): Tokens {
  const rules = rootRules();
  const order = [":root", `:root[data-accent="${accent}"]`];
  if (theme === "dark") {
    // The dark rule outranks the light accent rule, and the dark accent rule outranks both.
    order.push(':root[data-theme="dark"]', `:root[data-theme="dark"][data-accent="${accent}"]`);
  }
  return Object.assign({}, ...order.map((selector) => rules.get(selector) ?? {}));
}

function channel(value: number): number {
  const v = value / 255;
  return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
}

function luminance(hex: string): number {
  const n = Number.parseInt(hex.slice(1), 16);
  return (
    0.2126 * channel((n >> 16) & 255) +
    0.7152 * channel((n >> 8) & 255) +
    0.0722 * channel(n & 255)
  );
}

function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

const ACCENTS = ["green", "blue", "orange", "purple", "slate"];
const MIN = 4.5;

describe("theme tokens", () => {
  it("define every accent in both themes", () => {
    const rules = rootRules();
    for (const accent of ACCENTS.filter((a) => a !== "green")) {
      expect(rules.has(`:root[data-accent="${accent}"]`), accent).toBe(true);
      expect(rules.has(`:root[data-theme="dark"][data-accent="${accent}"]`), accent).toBe(true);
    }
    expect(rules.has(':root[data-theme="dark"]')).toBe(true);
  });

  it("keep the colours sampled from the Style frame", () => {
    const fills = ACCENTS.map((accent) => effective("light", accent)["--primary"]);
    expect(fills).toEqual(["#0f5c56", "#2b3fd1", "#a8502a", "#7a3e8e", "#3f4a5a"]);
    const dark = effective("dark", "green");
    expect(dark["--bg"]).toBe("#1c1b19");
    expect(dark["--sidebar"]).toBe("#262421");
    expect(dark["--primary-soft"]).toBe("#1f4a45");
  });

  for (const theme of ["light", "dark"] as const) {
    for (const accent of ACCENTS) {
      it(`keep accent text readable: ${theme} ${accent}`, () => {
        const t = effective(theme, accent);
        const ratio = (a: string, b: string) => contrast(t[a], t[b]);
        expect(ratio("--on-primary", "--primary"), "on primary").toBeGreaterThanOrEqual(MIN);
        expect(ratio("--on-primary", "--primary-hover"), "on hover").toBeGreaterThanOrEqual(MIN);
        expect(ratio("--primary-ink", "--primary-soft"), "ink on soft").toBeGreaterThanOrEqual(MIN);
        expect(ratio("--primary-ink", "--primary-soft-hover"), "ink on soft hover").toBeGreaterThanOrEqual(MIN);
        expect(ratio("--text", "--primary-soft"), "text on soft").toBeGreaterThanOrEqual(MIN);
        for (const ground of ["--bg", "--sidebar", "--surface"]) {
          expect(ratio("--primary-ink", ground), `ink on ${ground}`).toBeGreaterThanOrEqual(MIN);
        }
      });
    }
  }

  it("keep the dark palette's text, errors and warnings readable", () => {
    const t = effective("dark", "green");
    for (const ground of ["--bg", "--sidebar", "--surface"]) {
      expect(contrast(t["--text"], t[ground]), `text on ${ground}`).toBeGreaterThanOrEqual(MIN);
      expect(contrast(t["--muted"], t[ground]), `muted on ${ground}`).toBeGreaterThanOrEqual(MIN);
      expect(contrast(t["--danger-ink"], t[ground]), `danger on ${ground}`).toBeGreaterThanOrEqual(MIN);
    }
    expect(contrast(t["--error-text"], t["--error-bg"])).toBeGreaterThanOrEqual(MIN);
    expect(contrast(t["--warning-text"], t["--warning-bg"])).toBeGreaterThanOrEqual(MIN);
    expect(contrast(t["--on-primary"], t["--danger"])).toBeGreaterThanOrEqual(MIN);
  });

  it("scale the chat text and tighten the spacing", () => {
    const rules = rootRules();
    const base = rules.get(":root")!;
    const size = (name: string) =>
      Number.parseFloat(rules.get(`:root[data-text-size="${name}"]`)!["--chat-size"]);
    expect(size("small")).toBeLessThan(Number.parseFloat(base["--chat-size"]));
    expect(size("large")).toBeGreaterThan(Number.parseFloat(base["--chat-size"]));
    const compact = rules.get(':root[data-spacing="compact"]')!;
    for (const token of ["--gap-answer", "--gap-user", "--gap-para", "--row-height"]) {
      expect(Number.parseFloat(compact[token]), token).toBeLessThan(Number.parseFloat(base[token]));
    }
  });
});
