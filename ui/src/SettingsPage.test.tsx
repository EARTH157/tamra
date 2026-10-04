import { act, useState } from "react";
import { afterEach, describe, expect, it, type Mock, vi } from "vitest";
import { SettingsProvider } from "./settings";
import SettingsPage from "./SettingsPage";
import {
  buttonByText,
  type Call,
  click,
  json,
  type Mounted,
  mockFetch,
  mount,
  press,
  settle,
  typeInto,
} from "./test-utils";
import { localModel, modelsInfo, saved } from "./test-models";
import type { Collection, IndexStatus, ModelsInfo, Settings, SettingsTab } from "./types";

let view: Mounted | undefined;

afterEach(async () => {
  await view?.unmount();
  view = undefined;
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

const collection: Collection = {
  id: 1,
  name: "HR Documents",
  folder_path: "D:\\Work\\HR Documents",
  created_at: "t",
};
const index: IndexStatus = {
  stale: false,
  counts: { pending: 0, indexing: 0, indexed: 128, failed: 0, skipped: 0 },
  current: null,
  error: null,
  problems: [],
};

const noContent = () => new Response(null, { status: 204 });

type Options = {
  settings?: Partial<Settings>;
  models?: ModelsInfo;
  routes?: Record<string, () => Response>;
  tab?: SettingsTab;
  collection?: Collection | null;
  chatCount?: number;
  busy?: boolean;
};

type Harness = {
  calls: Call[];
  core: { settings: Settings; models: ModelsInfo };
  onChangeFolder: Mock<() => void>;
  onRebuild: Mock<() => void>;
  onChatsDeleted: Mock<() => Promise<void>>;
};

/** The page inside the settings provider, with a core that keeps the settings it is sent. */
async function mountPage(options: Options = {}): Promise<Harness> {
  const core = { settings: saved(options.settings), models: options.models ?? modelsInfo() };
  let calls: Call[] = []; // assigned below; the handlers only run once a request is made
  const body = () => calls[calls.length - 1].body as Record<string, unknown>;
  calls = mockFetch({
    "GET /api/settings": () => json(core.settings),
    "PUT /api/settings": () => {
      core.settings = { ...core.settings, ...body() };
      return json(core.settings);
    },
    "GET /api/models": () => json(core.models),
    "PUT /api/settings/api-key": () => {
      const key = String(body().key);
      core.settings = { ...core.settings, api_key_set: true, api_key_hint: key.slice(-4) };
      return noContent();
    },
    ...options.routes,
  });
  const harness: Harness = {
    calls,
    core,
    onChangeFolder: vi.fn<() => void>(),
    onRebuild: vi.fn<() => void>(),
    onChatsDeleted: vi.fn<() => Promise<void>>(async () => {}),
  };

  function Page() {
    const [tab, setTab] = useState<SettingsTab>(options.tab ?? "general");
    return (
      <SettingsPage
        tab={tab}
        onTabChange={setTab}
        collection={options.collection === undefined ? collection : options.collection}
        index={index}
        chatCount={options.chatCount ?? 4}
        busy={options.busy ?? false}
        onChangeFolder={harness.onChangeFolder}
        onRebuild={harness.onRebuild}
        onChatsDeleted={harness.onChatsDeleted}
      />
    );
  }
  view = await mount(
    <SettingsProvider>
      <Page />
    </SettingsProvider>,
  );
  return harness;
}

function text(): string {
  return view?.container.textContent ?? "";
}

function dialog(): HTMLElement | null {
  return document.querySelector('[role="dialog"]');
}

function keys(calls: Call[]): string[] {
  return calls.map((c) => c.key);
}

function find<T extends Element = HTMLElement>(selector: string): T {
  const found = view?.container.querySelector<T>(selector);
  if (!found) throw new Error(`nothing matches ${selector}`);
  return found;
}

function tab(name: string): HTMLButtonElement {
  const found = [...view!.container.querySelectorAll<HTMLButtonElement>('[role="tab"]')].find(
    (b) => b.textContent === name,
  );
  if (!found) throw new Error(`no tab ${name}`);
  return found;
}

/** The radio button named label: by its aria-label or text, or by the start of a card's text. */
function radio(label: string): HTMLButtonElement {
  const found = [...view!.container.querySelectorAll<HTMLButtonElement>('[role="radio"]')].find(
    (b) =>
      b.getAttribute("aria-label") === label ||
      b.textContent === label ||
      (b.matches(".mode-card, .model-pick") && b.textContent?.startsWith(label)),
  );
  if (!found) throw new Error(`no radio ${label}`);
  return found;
}

async function choose(select: HTMLSelectElement, value: string) {
  await act(async () => {
    select.value = value;
    select.dispatchEvent(new Event("change", { bubbles: true }));
  });
  await settle();
}

describe("Settings tabs", () => {
  it("shows the heading, the subtitle of the open tab, and switches with the arrow keys", async () => {
    await mountPage();
    expect(find("h1").textContent).toBe("Settings");
    expect(text()).toContain("Language, documents, and the data Tamra keeps on this computer.");
    expect(tab("General").getAttribute("aria-selected")).toBe("true");
    await press(tab("General"), "ArrowRight");
    expect(tab("AI model").getAttribute("aria-selected")).toBe("true");
    expect(document.activeElement).toBe(tab("AI model"));
    expect(text()).toContain("Choose where answers are generated.");
    await press(tab("AI model"), "ArrowRight");
    expect(text()).toContain("These choices change the app only, never your documents.");
    await press(tab("Style"), "ArrowRight"); // wraps around
    expect(tab("General").getAttribute("aria-selected")).toBe("true");
    await press(tab("General"), "End");
    expect(tab("Style").getAttribute("aria-selected")).toBe("true");
  });

  it("opens on the tab it is given", async () => {
    await mountPage({ tab: "style" });
    expect(tab("Style").getAttribute("aria-selected")).toBe("true");
    expect(text()).toContain("Accent colour");
  });
});

describe("General tab", () => {
  it("shows the settings, the folder, the index, the data and the chats", async () => {
    await mountPage();
    expect(find<HTMLSelectElement>("select").value).toBe("en");
    expect(find('button[role="switch"]').getAttribute("aria-checked")).toBe("true");
    expect(text()).toContain("D:\\Work\\HR Documents");
    expect(text()).toContain("128 files indexed · built with bge-m3");
    expect(text()).toContain("%LOCALAPPDATA%\\Tamra");
    expect(text()).toContain("4 chats saved on this computer");
    expect(text()).toContain("Tamra · pre-alpha · Apache-2.0 license");
  });

  it("saves the interface language and renders every string in Thai at once", async () => {
    const { calls } = await mountPage();
    await choose(find<HTMLSelectElement>("select"), "th");
    expect(calls).toContainEqual({ key: "PUT /api/settings", body: { language: "th" } });
    expect(find("h1").textContent).toBe("การตั้งค่า");
    expect(tab("ทั่วไป").getAttribute("aria-selected")).toBe("true");
    expect(text()).toContain("บันทึกแชทไว้ในเครื่องนี้ 4 แชท");
    expect(text()).toContain("สร้างดัชนีแล้ว 128 ไฟล์ · สร้างด้วย bge-m3");
    expect(document.documentElement.lang).toBe("th");
  });

  it("turns off asking before deleting a chat", async () => {
    const { calls } = await mountPage();
    await click(find('button[role="switch"]'));
    expect(calls).toContainEqual({ key: "PUT /api/settings", body: { ask_before_delete: false } });
    expect(find('button[role="switch"]').getAttribute("aria-checked")).toBe("false");
  });

  it("puts the old value back and shows the reason when a setting is refused", async () => {
    await mountPage({
      routes: { "PUT /api/settings": () => json({ detail: "invalid value for setting language" }, 400) },
    });
    await choose(find<HTMLSelectElement>("select"), "th");
    expect(find('[role="alert"]').textContent).toBe("invalid value for setting language");
    expect(find<HTMLSelectElement>("select").value).toBe("en");
    expect(find("h1").textContent).toBe("Settings");
  });

  it("changes the folder and rebuilds the index through the app", async () => {
    const page = await mountPage();
    await click(buttonByText(view!.container, "Change folder"));
    expect(page.onChangeFolder).toHaveBeenCalledTimes(1);
    await click(buttonByText(view!.container, "Rebuild index"));
    expect(page.onRebuild).toHaveBeenCalledTimes(1);
  });

  it("offers to choose a folder, and no index, before one is chosen", async () => {
    const page = await mountPage({ collection: null });
    expect(text()).toContain("No folder chosen yet");
    expect(text()).not.toContain("Search index");
    await click(buttonByText(view!.container, "Choose a folder"));
    expect(page.onChangeFolder).toHaveBeenCalledTimes(1);
  });

  it("will not rebuild the index or delete chats while an answer is written", async () => {
    await mountPage({ busy: true });
    expect(buttonByText(view!.container, "Rebuild index")?.disabled).toBe(true);
    expect(buttonByText(view!.container, "Change folder")?.disabled).toBe(true);
    expect(buttonByText(view!.container, "Delete all chats")?.disabled).toBe(true);
  });

  it("shows the data folder the core reports", async () => {
    await mountPage({ settings: { data_dir: "D:\\Tamra data" } });
    expect(text()).toContain("D:\\Tamra data");
    expect(text()).not.toContain("%LOCALAPPDATA%");
  });

  it("opens the data folder", async () => {
    const { calls } = await mountPage({
      routes: { "POST /api/open-data-folder": noContent },
    });
    await click(buttonByText(view!.container, "Open folder"));
    expect(keys(calls)).toContain("POST /api/open-data-folder");
    expect(buttonByText(view!.container, "Open folder")).toBeDefined();
  });

  it("hides Open folder where the core cannot open it (dev mode)", async () => {
    await mountPage({
      routes: {
        "POST /api/open-data-folder": () =>
          json({ detail: "Opening the data folder is only available in the Tamra window." }, 501),
      },
    });
    await click(buttonByText(view!.container, "Open folder"));
    expect(buttonByText(view!.container, "Open folder")).toBeUndefined();
    expect(text()).toContain("Only available in the Tamra window.");
    expect(view!.container.querySelector('[role="alert"]')).toBeNull();
  });

  it("deletes all chats only after a confirmation", async () => {
    const page = await mountPage({ routes: { "DELETE /api/chats": noContent } });
    await click(buttonByText(view!.container, "Delete all chats"));
    expect(dialog()?.textContent).toContain("Delete all chats?");
    expect(dialog()?.textContent).toContain("All 4 chats will be removed from this computer.");
    await click(buttonByText(dialog()!, "Cancel"));
    expect(dialog()).toBeNull();
    expect(keys(page.calls)).not.toContain("DELETE /api/chats");

    await click(buttonByText(view!.container, "Delete all chats"));
    await click(buttonByText(dialog()!, "Delete all chats"));
    expect(keys(page.calls)).toContain("DELETE /api/chats");
    expect(page.onChatsDeleted).toHaveBeenCalledTimes(1);
    expect(dialog()).toBeNull();
  });

  it("keeps the dialog open and says why when the chats cannot be deleted", async () => {
    const page = await mountPage({
      routes: {
        "DELETE /api/chats": () =>
          json({ detail: "An answer is being written. Wait for it to finish." }, 409),
      },
    });
    await click(buttonByText(view!.container, "Delete all chats"));
    await click(buttonByText(dialog()!, "Delete all chats"));
    expect(dialog()?.querySelector('[role="alert"]')?.textContent).toBe(
      "An answer is being written. Wait for it to finish.",
    );
    expect(page.onChatsDeleted).not.toHaveBeenCalled();
  });

  it("has nothing to delete without chats", async () => {
    await mountPage({ chatCount: 0 });
    expect(text()).toContain("No chats saved yet");
    expect(buttonByText(view!.container, "Delete all chats")?.disabled).toBe(true);
  });
});

describe("AI model tab: local", () => {
  it("shows the mode cards, the hardware and the models", async () => {
    await mountPage({ tab: "model" });
    expect(radio("Local model").getAttribute("aria-checked")).toBe("true");
    expect(radio("Cloud API").getAttribute("aria-checked")).toBe("false");
    expect(text()).toContain("Runs on this computer. Nothing leaves it.");
    expect(text()).toContain("Detected 16 GB RAM · NVIDIA RTX 3060, 12 GB VRAM");
    const rows = [...view!.container.querySelectorAll(".model-row")];
    expect(rows).toHaveLength(4); // three catalog models and one import
    expect(rows[0].textContent).toContain("Qwen3-4B");
    expect(rows[0].textContent).toContain("Small · 2.5 GB · for CPU-only machines · can think longer");
    expect(rows[0].textContent).toContain("Installed");
    expect(rows[0].querySelector('[role="radio"]')?.getAttribute("aria-checked")).toBe("true");
    expect(rows[0].textContent).toContain("Recommended");
    expect(rows[1].textContent).not.toContain("Recommended");
  });

  it("marks the recommended tier and shows the GPU a large model needs", async () => {
    await mountPage({
      tab: "model",
      models: modelsInfo({
        recommended_tier: "large",
        local: [
          localModel({ recommended: false }),
          localModel({
            id: "qwen3-14b",
            name: "Qwen3-14B",
            tier: "large",
            recommended: true,
            installed: false,
            min_vram_gb: 10,
          }),
        ],
        uncatalogued: [],
      }),
    });
    const row = [...view!.container.querySelectorAll(".model-row")][1];
    expect(row.textContent).toContain("Qwen3-14B");
    expect(row.textContent).toContain("Recommended");
    expect(row.textContent).toContain("Large · 2.5 GB · needs a GPU with 10 GB VRAM or more");
    expect(row.textContent).toContain("Download");
  });

  it("says so when there is no dedicated GPU", async () => {
    await mountPage({
      tab: "model",
      models: modelsInfo({ hardware: { ram_gb: 7.6, gpus: [] } }),
    });
    expect(text()).toContain("Detected 8 GB RAM · no dedicated GPU");
  });

  it("shows download progress with a bar and a Cancel", async () => {
    await mountPage({ tab: "model" });
    const row = [...view!.container.querySelectorAll(".model-row")][1];
    expect(row.textContent).toContain("Downloading 62%");
    expect(row.querySelector('[role="progressbar"]')?.getAttribute("aria-valuenow")).toBe("62");
    expect(row.querySelector('[role="radio"]')?.hasAttribute("disabled")).toBe(true);
  });

  it("cancels a download", async () => {
    const { calls } = await mountPage({
      tab: "model",
      routes: { "DELETE /api/models/qwen3-8b/download": noContent },
    });
    await click(find('button[aria-label="Cancel download: Qwen3-8B"]'));
    expect(keys(calls)).toContain("DELETE /api/models/qwen3-8b/download");
  });

  it("asks before downloading and shows the size", async () => {
    const { calls } = await mountPage({
      tab: "model",
      routes: { "POST /api/models/qwen3-14b/download": () => json({ status: "downloading" }, 202) },
    });
    await click(find('button[aria-label="Download Qwen3-14B"]'));
    expect(dialog()?.querySelector("h2")?.textContent).toBe("Download Qwen3-14B (2.5 GB)?");
    expect(keys(calls)).not.toContain("POST /api/models/qwen3-14b/download");
    await click(buttonByText(dialog()!, "Cancel"));
    expect(dialog()).toBeNull();
    expect(keys(calls)).not.toContain("POST /api/models/qwen3-14b/download");

    await click(find('button[aria-label="Download Qwen3-14B"]'));
    await click(buttonByText(dialog()!, "Download"));
    expect(keys(calls)).toContain("POST /api/models/qwen3-14b/download");
    expect(dialog()).toBeNull();
  });

  it("closes the dialog, reloads the list and shows the reason when a download is refused", async () => {
    const { calls } = await mountPage({
      tab: "model",
      routes: {
        "POST /api/models/qwen3-14b/download": () =>
          json({ detail: "Another download is already running." }, 409),
      },
    });
    await click(find('button[aria-label="Download Qwen3-14B"]'));
    const before = calls.filter((c) => c.key === "GET /api/models").length;
    await click(buttonByText(dialog()!, "Download"));
    expect(dialog()).toBeNull();
    expect(find('[role="alert"]').textContent).toBe("Another download is already running.");
    expect(calls.filter((c) => c.key === "GET /api/models").length).toBeGreaterThan(before);
  });

  it("explains in the user's language that a model is already installed", async () => {
    await mountPage({
      tab: "model",
      settings: { language: "th" },
      routes: {
        "POST /api/models/qwen3-14b/download": () =>
          json({ detail: "Already installed." }, 409),
      },
    });
    await click(find('button[aria-label="ดาวน์โหลด Qwen3-14B"]'));
    await click(buttonByText(dialog()!, "ดาวน์โหลด"));
    expect(dialog()).toBeNull();
    expect(find('[role="alert"]').textContent).toBe("ติดตั้งโมเดลนี้แล้ว");
  });

  it("shows the discrete GPU, not an integrated one that reports more memory", async () => {
    await mountPage({
      tab: "model",
      models: modelsInfo({
        hardware: {
          ram_gb: 15,
          gpus: [
            { name: "AMD Radeon(TM) 780M Graphics", vram_mb: 8094, integrated: true },
            { name: "NVIDIA GeForce RTX 5060 Laptop GPU", vram_mb: 7899, integrated: false },
          ],
        },
      }),
    });
    expect(text()).toContain("Detected 15 GB RAM · NVIDIA GeForce RTX 5060 Laptop GPU, 8 GB VRAM");
    expect(text()).not.toContain("Radeon");
  });

  it("falls back to the largest integrated GPU when there is no discrete one", async () => {
    await mountPage({
      tab: "model",
      models: modelsInfo({
        hardware: {
          ram_gb: 15,
          gpus: [
            { name: "Intel(R) UHD Graphics", vram_mb: 1024, integrated: true },
            { name: "AMD Radeon(TM) 780M Graphics", vram_mb: 8094, integrated: true },
          ],
        },
      }),
    });
    expect(text()).toContain("AMD Radeon(TM) 780M Graphics, 8 GB VRAM");
  });

  it("shows a failed download and lets it be tried again", async () => {
    await mountPage({
      tab: "model",
      models: modelsInfo({
        local: [
          localModel({}),
          localModel({
            id: "qwen3-8b",
            name: "Qwen3-8B",
            installed: false,
            state: "error",
            error: "The download was interrupted.",
          }),
        ],
        uncatalogued: [],
      }),
    });
    expect(text()).toContain("Download failed: The download was interrupted.");
    expect(find('button[aria-label="Download Qwen3-8B"]')).toBeDefined();
  });

  it("polls the models every second while a download runs, and stops when it is over", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    const page = await mountPage({ tab: "model" });
    const loads = () => page.calls.filter((c) => c.key === "GET /api/models").length;
    const first = loads();
    await vi.advanceTimersByTimeAsync(3000);
    await settle();
    expect(loads()).toBe(first + 3);

    // The download finishes: the next answer has nothing downloading, and polling stops.
    page.core.models = modelsInfo({ local: [localModel({})], uncatalogued: [] });
    await vi.advanceTimersByTimeAsync(1000);
    await settle();
    const done = loads();
    expect(done).toBe(first + 4);
    await vi.advanceTimersByTimeAsync(3000);
    expect(loads()).toBe(done);
  });

  it("stops polling when the tab is left", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    const page = await mountPage({ tab: "model" });
    await click(tab("Style"));
    const left = page.calls.filter((c) => c.key === "GET /api/models").length;
    await vi.advanceTimersByTimeAsync(3000);
    expect(page.calls.filter((c) => c.key === "GET /api/models").length).toBe(left);
  });

  it("chooses an installed model, but not one that is not installed", async () => {
    const { calls } = await mountPage({
      tab: "model",
      models: modelsInfo({
        local: [
          localModel({ id: "a", name: "Alpha" }),
          localModel({ id: "b", name: "Beta" }),
          localModel({ id: "c", name: "Gamma", installed: false }),
        ],
        uncatalogued: [],
        active: { mode: "local", label: "Alpha", id: "a" },
      }),
    });
    expect(radio("Gamma").disabled).toBe(true);
    await click(radio("Beta"));
    expect(calls).toContainEqual({ key: "PUT /api/settings", body: { local_model_id: "b" } });
    // The list is loaded again after the core has saved the choice, never before.
    const order = keys(calls);
    expect(order.lastIndexOf("GET /api/models")).toBeGreaterThan(order.indexOf("PUT /api/settings"));
  });

  it("lists an uncatalogued import with its warning, and lets it be chosen", async () => {
    const { calls } = await mountPage({ tab: "model" });
    const row = [...view!.container.querySelectorAll(".model-row")][3];
    expect(row.textContent).toContain("mine");
    expect(row.textContent).toContain("Imported model");
    expect(row.textContent).toContain("Not in Tamra's catalog; answer quality is unknown.");
    await click(row.querySelector('[role="radio"]'));
    expect(calls).toContainEqual({
      key: "PUT /api/settings",
      body: { local_model_id: "import:mine.gguf" },
    });
  });

  it("switches to the Cloud API", async () => {
    const { calls } = await mountPage({ tab: "model" });
    await click(radio("Cloud API"));
    expect(calls).toContainEqual({ key: "PUT /api/settings", body: { mode: "api" } });
    expect(radio("Cloud API").getAttribute("aria-checked")).toBe("true");
    expect(text()).toContain("the passages found for each question are sent to the provider");
    expect(text()).not.toContain("Detected");
  });
});

describe("AI model tab: import", () => {
  const imported = {
    id: "import:mine.gguf",
    path: "C:\\Users\\me\\AppData\\Local\\Tamra\\models\\mine.gguf",
    catalogued: false,
    warning: "This model is not in Tamra's catalog; answer quality is unknown.",
  };

  it("imports the file the picker returns and shows the warning for an unknown model", async () => {
    const { calls } = await mountPage({
      tab: "model",
      routes: {
        "POST /api/pick-file": () => json({ file_path: "D:\\Models\\mine.gguf" }),
        "POST /api/models/import": () => json(imported),
      },
    });
    await click(buttonByText(view!.container, "Import model file…"));
    expect(calls).toContainEqual({
      key: "POST /api/models/import",
      body: { path: "D:\\Models\\mine.gguf" },
    });
    expect(find('[role="status"]').textContent).toBe(
      "Imported mine.gguf. Not in Tamra's catalog; answer quality is unknown.",
    );
    expect(calls.filter((c) => c.key === "GET /api/models").length).toBeGreaterThan(1);
  });

  it("says a catalogued file matches the catalog", async () => {
    await mountPage({
      tab: "model",
      routes: {
        "POST /api/pick-file": () => json({ file_path: "D:\\Models\\q.gguf" }),
        "POST /api/models/import": () =>
          json({ ...imported, id: "small", path: "C:\\m\\Qwen3-4B-Q4_K_M.gguf", catalogued: true, warning: null }),
      },
    });
    await click(buttonByText(view!.container, "Import model file…"));
    expect(find('[role="status"]').textContent).toBe(
      "Imported Qwen3-4B-Q4_K_M.gguf. It matches the model catalog.",
    );
  });

  it("does nothing when the picker is cancelled", async () => {
    const { calls } = await mountPage({
      tab: "model",
      routes: { "POST /api/pick-file": () => json({ file_path: null }) },
    });
    await click(buttonByText(view!.container, "Import model file…"));
    expect(keys(calls)).not.toContain("POST /api/models/import");
    expect(view!.container.querySelector('[role="status"], [role="alert"]')).toBeNull();
  });

  it("asks for a typed path where there is no file picker, and shows the core's error", async () => {
    let refuse = true;
    const { calls } = await mountPage({
      tab: "model",
      routes: {
        "POST /api/pick-file": () =>
          json({ detail: "The file picker is only available in the Tamra window." }, 501),
        "POST /api/models/import": () =>
          refuse ? json({ detail: "File not found: D:\\nope.gguf" }, 400) : json(imported),
      },
    });
    await click(buttonByText(view!.container, "Import model file…"));
    const input = find<HTMLInputElement>('input[aria-label="Model file path"]');
    await typeInto(input, "D:\\nope.gguf");
    await click(buttonByText(view!.container, "Import"));
    expect(find('[role="alert"]').textContent).toBe("File not found: D:\\nope.gguf");
    refuse = false;
    await typeInto(find('input[aria-label="Model file path"]'), "D:\\Models\\mine.gguf");
    await click(buttonByText(view!.container, "Import"));
    expect(calls).toContainEqual({
      key: "POST /api/models/import",
      body: { path: "D:\\Models\\mine.gguf" },
    });
    expect(view!.container.querySelector('input[aria-label="Model file path"]')).toBeNull();
    expect(find('[role="status"]').textContent).toContain("Imported mine.gguf.");
  });
});

describe("AI model tab: Cloud API", () => {
  const cloud = { mode: "api", api_key_set: true, api_key_hint: "3f9a" } as const;

  function input(label: string): HTMLInputElement {
    const found = [...view!.container.querySelectorAll<HTMLLabelElement>("label.field")].find((l) =>
      l.textContent?.startsWith(label),
    );
    const field = found?.querySelector("input");
    if (!field) throw new Error(`no field ${label}`);
    return field;
  }

  it("warns that the passages are sent, and shows the provider, model and key", async () => {
    await mountPage({ tab: "model", settings: cloud });
    expect(text()).toContain(
      "In API mode, the passages found for each question are sent to the provider. Your files and the search index stay on this computer.",
    );
    expect(radio("Anthropic").getAttribute("aria-checked")).toBe("true");
    expect(input("Model").value).toBe("claude-sonnet-5-5");
    expect(input("Base URL").disabled).toBe(true);
    expect(text()).toContain("Stored in Windows Credential Manager, never in Tamra's files.");
  });

  it("lets the base URL be edited for an OpenAI-compatible server", async () => {
    const { calls } = await mountPage({ tab: "model", settings: cloud });
    await click(radio("OpenAI-compatible"));
    expect(calls).toContainEqual({ key: "PUT /api/settings", body: { api_provider: "openai" } });
    expect(input("Base URL").disabled).toBe(false);
    expect(input("Base URL").placeholder).toBe("Only for OpenAI-compatible servers");
  });

  it("shows a saved key masked with its last 4 characters, and never as text", async () => {
    await mountPage({ tab: "model", settings: cloud });
    const key = input("API key");
    expect(key.type).toBe("password");
    expect(key.autocomplete).toBe("off");
    expect(key.value).toBe("");
    expect(key.placeholder).toBe(`${"•".repeat(20)}3f9a`);
  });

  it("asks for a key when none is saved", async () => {
    await mountPage({ tab: "model", settings: { mode: "api" } });
    expect(input("API key").placeholder).toBe("Paste your API key");
  });

  it("sends a typed key once, clears the field and shows the new hint", async () => {
    const { calls } = await mountPage({ tab: "model", settings: cloud });
    await typeInto(input("API key"), "test-key-1234");
    await click(buttonByText(view!.container, "Save"));
    expect(calls).toContainEqual({
      key: "PUT /api/settings/api-key",
      body: { provider: "anthropic", key: "test-key-1234" },
    });
    expect(keys(calls)).toContain("GET /api/settings"); // reloaded for the hint
    expect(input("API key").value).toBe("");
    expect(input("API key").placeholder.endsWith("1234")).toBe(true);
    expect(find('[role="status"]').textContent).toBe("Saved");
    expect(document.body.innerHTML).not.toContain("test-key-1234");
  });

  it("saves the model and base URL as settings, and sends no key when none was typed", async () => {
    const { calls } = await mountPage({
      tab: "model",
      settings: { ...cloud, api_provider: "openai" },
    });
    await typeInto(input("Model"), "gpt-test");
    await typeInto(input("Base URL"), "https://llm.example/v1");
    await click(buttonByText(view!.container, "Save"));
    expect(calls).toContainEqual({
      key: "PUT /api/settings",
      body: { api_model: "gpt-test", api_base_url: "https://llm.example/v1" },
    });
    expect(keys(calls)).not.toContain("PUT /api/settings/api-key");
  });

  it("shows the core's reason when the key cannot be saved", async () => {
    await mountPage({
      tab: "model",
      settings: cloud,
      routes: {
        "PUT /api/settings/api-key": () =>
          json({ detail: "The API key could not be saved to the system." }, 500),
      },
    });
    await typeInto(input("API key"), "test-key-1234");
    await click(buttonByText(view!.container, "Save"));
    expect(find('[role="alert"]').textContent).toBe(
      "The API key could not be saved to the system.",
    );
    expect(input("API key").value).toBe("test-key-1234"); // kept, so it can be tried again
  });

  it("says that Test connection saves first", async () => {
    await mountPage({ tab: "model", settings: cloud });
    expect(text()).toContain("Test connection saves your changes, then tests them.");
  });

  it("tests the connection after saving what was typed", async () => {
    const { calls } = await mountPage({
      tab: "model",
      settings: cloud,
      routes: {
        "POST /api/settings/test-connection": () =>
          json({ ok: true, reason: null, message: "Connected." }),
      },
    });
    await typeInto(input("API key"), "test-key-1234");
    await click(buttonByText(view!.container, "Test connection"));
    const order = keys(calls).filter((k) => k.startsWith("PUT") || k.startsWith("POST"));
    expect(order).toEqual(["PUT /api/settings/api-key", "POST /api/settings/test-connection"]);
    expect(find('[role="status"]').textContent).toBe("Connected");
  });

  it("explains a failed test in the user's language", async () => {
    await mountPage({
      tab: "model",
      settings: cloud,
      routes: {
        "POST /api/settings/test-connection": () =>
          json({ ok: false, reason: "auth", message: "No API key is set." }),
      },
    });
    await click(buttonByText(view!.container, "Test connection"));
    expect(find('[role="alert"]').textContent).toBe("The API key is missing or was refused.");
  });

  it("shows the core's message when a failed test has no known reason", async () => {
    await mountPage({
      tab: "model",
      settings: cloud,
      routes: {
        "POST /api/settings/test-connection": () =>
          json({ ok: false, reason: "other", message: "The server answered 500." }),
      },
    });
    await click(buttonByText(view!.container, "Test connection"));
    expect(find('[role="alert"]').textContent).toBe("The server answered 500.");
  });
});

describe("Style tab", () => {
  it("shows the current choices", async () => {
    await mountPage({
      tab: "style",
      settings: { theme: "dark", accent: "blue", text_size: "large", spacing: "compact" },
    });
    expect(radio("Dark").getAttribute("aria-checked")).toBe("true");
    expect(radio("Light").getAttribute("aria-checked")).toBe("false");
    expect(radio("Blue").getAttribute("aria-checked")).toBe("true");
    expect(radio("Large").getAttribute("aria-checked")).toBe("true");
    expect(radio("Compact").getAttribute("aria-checked")).toBe("true");
  });

  it("saves the theme, the accent, the text size and the spacing", async () => {
    const { calls } = await mountPage({ tab: "style" });
    await click(radio("Dark"));
    await click(radio("Match Windows"));
    await click(radio("Orange"));
    await click(radio("Small"));
    await click(radio("Compact"));
    const puts = calls.filter((c) => c.key === "PUT /api/settings").map((c) => c.body);
    expect(puts).toEqual([
      { theme: "dark" },
      { theme: "system" },
      { accent: "orange" },
      { text_size: "small" },
      { spacing: "compact" },
    ]);
    expect(document.documentElement.dataset.accent).toBe("orange");
    expect(document.documentElement.dataset.spacing).toBe("compact");
  });

  it("shows a live preview of a question and an answer with a citation", async () => {
    await mountPage({ tab: "style" });
    const preview = find(".preview");
    expect(preview.querySelector(".message.user")?.textContent).toBe(
      "How many days of leave does a new employee get?",
    );
    expect(preview.querySelector(".answer-text")?.textContent).toContain("6 days of leave a year");
    expect(preview.querySelector(".cite")?.textContent).toBe("1");
  });

  it("is in Thai when the language is Thai", async () => {
    await mountPage({ tab: "style", settings: { language: "th" } });
    expect(find(".preview .message.user").textContent).toBe("พนักงานใหม่ลาพักร้อนได้กี่วัน");
    expect(radio("ตาม Windows")).toBeDefined();
  });
});
