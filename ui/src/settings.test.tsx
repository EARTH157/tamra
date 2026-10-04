import { act } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "./api";
import { setLanguage, translateNow } from "./i18n";
import { applyAppearance, DEFAULT_SETTINGS, resolveTheme, SettingsProvider, useSettings, type SettingsApi } from "./settings";
import { json, type Mounted, mockFetch, mount, settle } from "./test-utils";
import type { Settings } from "./types";
import Welcome from "./Welcome";

let view: Mounted | undefined;
let current: SettingsApi;

function Probe() {
  current = useSettings();
  return <span id="probe">{current.settings.theme}</span>;
}

const html = document.documentElement;
const saved = (changes: Partial<Settings> = {}): Settings => ({ ...DEFAULT_SETTINGS, ...changes });

beforeEach(() => {
  for (const name of ["theme", "accent", "textSize", "spacing"]) delete html.dataset[name];
  html.lang = "";
});

afterEach(async () => {
  await view?.unmount();
  view = undefined;
  setLanguage("en");
  vi.unstubAllGlobals();
});

describe("SettingsProvider", () => {
  it("loads /api/settings once and provides it", async () => {
    const calls = mockFetch({ "GET /api/settings": () => json(saved({ theme: "dark" })) });
    view = await mount(
      <SettingsProvider>
        <Probe />
      </SettingsProvider>,
    );
    expect(calls.map((c) => c.key)).toEqual(["GET /api/settings"]);
    expect(current.loaded).toBe(true);
    expect(current.settings.theme).toBe("dark");
  });

  it("uses English and the light green style until the settings arrive", async () => {
    vi.stubGlobal("fetch", () => new Promise<Response>(() => {}));
    view = await mount(
      <SettingsProvider>
        <Welcome onChoose={() => {}} />
      </SettingsProvider>,
    );
    expect(view.container.querySelector("h1")?.textContent).toBe("Welcome to Tamra");
    expect(html.lang).toBe("en");
    expect(html.dataset.theme).toBe("light");
    expect(html.dataset.accent).toBe("green");
  });

  it("asks again when the core did not answer", async () => {
    let attempts = 0;
    mockFetch({
      "GET /api/settings": () => (++attempts < 3 ? json({ detail: "starting" }, 503) : json(saved())),
    });
    view = await mount(
      <SettingsProvider retryMs={5}>
        <Probe />
      </SettingsProvider>,
    );
    // Wait for the retries by what they do, not for a fixed time.
    await vi.waitFor(async () => {
      await settle();
      expect(attempts).toBe(3);
    });
    expect(current.loaded).toBe(true);
  });

  it("applies theme, accent, text size, spacing and language to <html>", async () => {
    mockFetch({
      "GET /api/settings": () =>
        json(saved({ theme: "dark", accent: "purple", text_size: "large", spacing: "compact", language: "th" })),
    });
    view = await mount(
      <SettingsProvider>
        <Probe />
      </SettingsProvider>,
    );
    expect(html.dataset.theme).toBe("dark");
    expect(html.dataset.accent).toBe("purple");
    expect(html.dataset.textSize).toBe("large");
    expect(html.dataset.spacing).toBe("compact");
    expect(html.lang).toBe("th");
  });

  it("follows the system colour scheme for theme 'system'", async () => {
    let listener: (() => void) | undefined;
    const query = {
      matches: true,
      addEventListener: (_: string, fn: () => void) => {
        listener = fn;
      },
      removeEventListener: vi.fn(),
    };
    vi.stubGlobal("matchMedia", () => query);
    mockFetch({ "GET /api/settings": () => json(saved({ theme: "system" })) });
    view = await mount(
      <SettingsProvider>
        <Probe />
      </SettingsProvider>,
    );
    expect(html.dataset.theme).toBe("dark");
    query.matches = false;
    await act(async () => listener?.());
    expect(html.dataset.theme).toBe("light");
    await view.unmount();
    view = undefined;
    expect(query.removeEventListener).toHaveBeenCalledTimes(1);
  });

  it("follows a dark operating system until the settings arrive", async () => {
    const query = { matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() };
    vi.stubGlobal("matchMedia", () => query);
    vi.stubGlobal("fetch", () => new Promise<Response>(() => {}));
    view = await mount(
      <SettingsProvider>
        <Probe />
      </SettingsProvider>,
    );
    expect(current.loaded).toBe(false);
    expect(html.dataset.theme).toBe("dark");
  });

  it("translates through translateNow() and useT() in the saved language", async () => {
    mockFetch({ "GET /api/settings": () => json(saved({ language: "th" })) });
    view = await mount(
      <SettingsProvider>
        <Welcome onChoose={() => {}} />
      </SettingsProvider>,
    );
    expect(view.container.querySelector("h1")?.textContent).toBe("ยินดีต้อนรับสู่ Tamra");
    expect(translateNow("common.cancel")).toBe("ยกเลิก");
  });

  it("switches language for components without re-mounting them", async () => {
    mockFetch({
      "GET /api/settings": () => json(saved()),
      "PUT /api/settings": () => json(saved({ language: "th" })),
    });
    view = await mount(
      <SettingsProvider>
        <Probe />
        <Welcome onChoose={() => {}} />
      </SettingsProvider>,
    );
    expect(view.container.querySelector("h1")?.textContent).toBe("Welcome to Tamra");
    await act(async () => {
      await current.update({ language: "th" });
    });
    expect(view.container.querySelector("h1")?.textContent).toBe("ยินดีต้อนรับสู่ Tamra");
    expect(html.lang).toBe("th");
  });
});

describe("update", () => {
  it("shows the change at once, sends only the change, and keeps what the core returns", async () => {
    let finish: (response: Response) => void = () => {};
    const calls: { method: string; body: unknown }[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_input: string, init?: RequestInit) => {
        calls.push({ method: init?.method ?? "GET", body: init?.body && JSON.parse(String(init.body)) });
        if (init?.method === "PUT") return new Promise<Response>((resolve) => (finish = resolve));
        return json(saved());
      }),
    );
    view = await mount(
      <SettingsProvider>
        <Probe />
      </SettingsProvider>,
    );
    let done: Promise<void> = Promise.resolve();
    await act(async () => {
      done = current.update({ accent: "blue", theme: "dark" });
    });
    expect(current.settings.accent).toBe("blue"); // before the core has answered
    expect(html.dataset.theme).toBe("dark");
    await act(async () => {
      finish(json(saved({ accent: "blue", theme: "dark", api_key_set: true, api_key_hint: "wxyz" })));
      await done;
    });
    expect(calls[1]).toEqual({ method: "PUT", body: { accent: "blue", theme: "dark" } });
    expect(current.settings).toMatchObject({ accent: "blue", theme: "dark", api_key_set: true, api_key_hint: "wxyz" });
  });

  it("rolls back and rejects when the core refuses", async () => {
    mockFetch({
      "GET /api/settings": () => json(saved({ accent: "orange" })),
      "PUT /api/settings": () => json({ detail: "invalid value for setting accent: 'pink'" }, 400),
    });
    view = await mount(
      <SettingsProvider>
        <Probe />
      </SettingsProvider>,
    );
    await act(async () => {
      const failure = await current.update({ accent: "blue", spacing: "compact" }).catch((e: unknown) => e);
      expect(failure).toBeInstanceOf(ApiError);
      expect((failure as ApiError).message).toBe("invalid value for setting accent: 'pink'");
    });
    expect(current.settings.accent).toBe("orange");
    expect(current.settings.spacing).toBe("comfortable");
    expect(html.dataset.accent).toBe("orange");
    expect(html.dataset.spacing).toBe("comfortable");
  });

  it("rolls back when the core cannot be reached", async () => {
    let reachable = true;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_input: string, init?: RequestInit) => {
        if (init?.method === "PUT" && !reachable) throw new TypeError("Failed to fetch");
        return json(saved());
      }),
    );
    view = await mount(
      <SettingsProvider>
        <Probe />
      </SettingsProvider>,
    );
    reachable = false;
    await act(async () => {
      await expect(current.update({ theme: "dark" })).rejects.toThrow("Failed to fetch");
    });
    expect(current.settings.theme).toBe("light");
  });

  it("does not undo a newer change when an older one fails", async () => {
    const pending: ((r: Response) => void)[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_input: string, init?: RequestInit) => {
        if (init?.method === "PUT") return new Promise<Response>((resolve) => pending.push(resolve));
        return json(saved());
      }),
    );
    view = await mount(
      <SettingsProvider>
        <Probe />
      </SettingsProvider>,
    );
    let first: Promise<unknown> = Promise.resolve();
    let second: Promise<unknown> = Promise.resolve();
    await act(async () => {
      first = current.update({ accent: "blue" }).catch(() => "failed");
      second = current.update({ accent: "slate", theme: "dark" });
    });
    await act(async () => {
      pending[0](json({ detail: "no" }, 400));
      await first;
    });
    expect(current.settings.accent).toBe("slate"); // the newer value stays
    await act(async () => {
      pending[1](json(saved({ accent: "slate", theme: "dark" })));
      await second;
    });
    expect(current.settings).toMatchObject({ accent: "slate", theme: "dark" });
  });

  it("shows the confirmed value when two overlapping updates both fail", async () => {
    const pending: ((r: Response) => void)[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_input: string, init?: RequestInit) => {
        if (init?.method === "PUT") return new Promise<Response>((resolve) => pending.push(resolve));
        return json(saved());
      }),
    );
    view = await mount(
      <SettingsProvider>
        <Probe />
      </SettingsProvider>,
    );
    let first: Promise<unknown> = Promise.resolve();
    let second: Promise<unknown> = Promise.resolve();
    await act(async () => {
      first = current.update({ theme: "dark" }).catch(() => "failed");
      second = current.update({ theme: "system" }).catch(() => "failed");
    });
    expect(current.settings.theme).toBe("system");
    await act(async () => {
      pending[0](json({ detail: "no" }, 400));
      await first;
    });
    expect(current.settings.theme).toBe("system"); // the newer change is still pending
    await act(async () => {
      pending[1](json({ detail: "no" }, 400));
      await second;
    });
    expect(current.settings.theme).toBe("light"); // what the core has
    expect(html.dataset.theme).toBe("light");
  });

  it("keeps a pending change visible when a reload arrives, and confirms it afterwards", async () => {
    let finish: (r: Response) => void = () => {};
    let server = saved({ theme: "light" });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_input: string, init?: RequestInit) => {
        if (init?.method === "PUT") return new Promise<Response>((resolve) => (finish = resolve));
        return json(server);
      }),
    );
    view = await mount(
      <SettingsProvider>
        <Probe />
      </SettingsProvider>,
    );
    let done: Promise<void> = Promise.resolve();
    await act(async () => {
      done = current.update({ theme: "dark" });
    });
    server = saved({ theme: "light", api_key_set: true, api_key_hint: "9999" });
    await act(async () => {
      await current.reload();
    });
    expect(current.settings).toMatchObject({ theme: "dark", api_key_set: true }); // not clobbered
    await act(async () => {
      server = saved({ theme: "dark", api_key_set: true, api_key_hint: "9999" });
      finish(json(server));
      await done;
    });
    expect(current.settings.theme).toBe("dark");
  });

  it("sends nothing for an empty change", async () => {
    const calls = mockFetch({ "GET /api/settings": () => json(saved()) });
    view = await mount(
      <SettingsProvider>
        <Probe />
      </SettingsProvider>,
    );
    await act(async () => {
      await current.update({});
    });
    expect(calls).toHaveLength(1);
  });
});

describe("reload", () => {
  it("reads the settings again, for example to see the API key state", async () => {
    let keySet = false;
    mockFetch({ "GET /api/settings": () => json(saved({ api_key_set: keySet, api_key_hint: keySet ? "1234" : null })) });
    view = await mount(
      <SettingsProvider>
        <Probe />
      </SettingsProvider>,
    );
    expect(current.settings.api_key_set).toBe(false);
    keySet = true;
    await act(async () => {
      await current.reload();
    });
    expect(current.settings).toMatchObject({ api_key_set: true, api_key_hint: "1234" });
  });
});

describe("appearance helpers", () => {
  it("resolve 'system' from the operating system", () => {
    expect(resolveTheme("system", true)).toBe("dark");
    expect(resolveTheme("system", false)).toBe("light");
    expect(resolveTheme("dark", false)).toBe("dark");
    expect(resolveTheme("light", true)).toBe("light");
  });

  it("set the attributes on an element", () => {
    const root = document.createElement("html");
    applyAppearance(root, { ...DEFAULT_SETTINGS, accent: "slate", text_size: "small" }, false);
    expect(root.dataset).toMatchObject({ theme: "light", accent: "slate", textSize: "small", spacing: "comfortable" });
    expect(root.lang).toBe("en");
  });
});
