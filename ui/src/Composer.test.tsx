import { afterEach, describe, expect, it, vi } from "vitest";
import ChatView from "./ChatView";
import { LanguageContext } from "./i18n";
import { DEFAULT_SETTINGS, SettingsProvider } from "./settings";
import Thinking from "./Thinking";
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
  sse,
  typeInto,
} from "./test-utils";
import type { LocalModel, ModelsInfo, Settings } from "./types";

let view: Mounted | undefined;

afterEach(async () => {
  await view?.unmount();
  view = undefined;
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

const chat = { id: 5, title: "", created_at: "t", updated_at: "t" };
const source = {
  n: 1,
  file: "HR/lease.pdf",
  label: "p. 2",
  text: "The lease term is three years.",
  location: { kind: "pdf" },
};

function localModel(over: Partial<LocalModel>): LocalModel {
  return {
    id: "qwen3-4b",
    name: "Qwen3-4B",
    size: 2_500_000_000,
    installed: true,
    state: "idle",
    done: 0,
    total: 0,
    error: null,
    tier: "small",
    recommended: true,
    license: "apache-2.0",
    languages: ["th", "en"],
    context_length: 32768,
    thinking: true,
    ...over,
  };
}

/** Qwen3-4B installed and active, Qwen3-8B downloading at 62%, and one imported file. */
function modelsInfo(over: Partial<ModelsInfo> = {}): ModelsInfo {
  return {
    recommended_tier: "small",
    active: { mode: "local", label: "Qwen3-4B-Q4_K_M", id: "qwen3-4b" },
    local: [
      localModel({}),
      localModel({
        id: "qwen3-8b",
        name: "Qwen3-8B",
        tier: "medium",
        recommended: false,
        installed: false,
        state: "downloading",
        done: 62,
        total: 100,
      }),
      localModel({ id: "qwen3-14b", name: "Qwen3-14B", tier: "large", installed: false }),
    ],
    uncatalogued: [{ id: "import:mine.gguf", name: "mine", file: "mine.gguf", size: 8 }],
    ...over,
  };
}

const saved = (over: Partial<Settings> = {}): Settings => ({ ...DEFAULT_SETTINGS, ...over });

type Setup = {
  settings?: Settings;
  models?: ModelsInfo;
  routes?: Record<string, () => Response>;
  language?: "en" | "th";
  onOpenSettings?: (tab: string) => void;
};

/** A chat inside the settings provider, with the settings and model routes answered. */
async function mountChat(setup: Setup = {}): Promise<Call[]> {
  const calls = mockFetch({
    "GET /api/settings": () => json(setup.settings ?? saved()),
    "GET /api/models": () => json(setup.models ?? modelsInfo()),
    "GET /api/chats/5": () => json({ ...chat, messages: [] }),
    ...setup.routes,
  });
  view = await mount(
    <SettingsProvider>
      <LanguageContext.Provider value={setup.language ?? "en"}>
        <ChatView
          chatId={5}
          createChat={vi.fn()}
          onBusyChange={vi.fn()}
          onAnswered={vi.fn()}
          onOpenSettings={setup.onOpenSettings ?? vi.fn()}
        />
      </LanguageContext.Provider>
    </SettingsProvider>,
  );
  return calls;
}

function chip(prefix: string): HTMLButtonElement {
  const found = view?.container.querySelector<HTMLButtonElement>(`button[aria-label^="${prefix}"]`);
  if (!found) throw new Error(`no chip ${prefix}`);
  return found;
}

function thinkChip(): HTMLButtonElement {
  const found = buttonByText(view!.container, "Think longer");
  if (!found) throw new Error("no Think longer chip");
  return found;
}

function menu(): HTMLElement | null {
  return view?.container.querySelector('[role="menu"]') ?? null;
}

function entry(text: string): HTMLButtonElement | undefined {
  return [...(menu()?.querySelectorAll<HTMLButtonElement>('[role^="menuitem"]') ?? [])].find(
    (item) => item.textContent?.includes(text),
  );
}

async function ask(text = "How long?") {
  await typeInto(view?.container.querySelector("textarea"), text);
  await click(view?.container.querySelector('button[type="submit"]'));
}

/** The JSON body of the question that was sent. */
function questionBody(calls: Call[]): unknown {
  return calls.find((c) => c.key === "POST /api/chats/5/messages")?.body;
}

/** A stream the test feeds event by event. */
function openStream() {
  const encoder = new TextEncoder();
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  const body = new ReadableStream<Uint8Array>({
    start(c) {
      controller = c;
    },
  });
  return {
    response: () => new Response(body, { headers: { "Content-Type": "text/event-stream" } }),
    push: (event: unknown) =>
      controller.enqueue(encoder.encode(`data: ${JSON.stringify(event)}\n\n`)),
    finish: () => controller.close(),
  };
}

describe("mode menu", () => {
  it("offers Answer and Search only, and no later ideas", async () => {
    await mountChat();
    expect(chip("How Tamra responds").textContent).toContain("Answer");
    await click(chip("How Tamra responds"));
    expect(menu()?.textContent).toContain("How Tamra responds");
    const items = [...menu()!.querySelectorAll('[role="menuitemradio"]')];
    expect(items.map((item) => item.querySelector(".entry-name")?.textContent)).toEqual([
      "Answer",
      "Search only",
    ]);
    expect(menu()?.textContent).toContain("Writes an answer and cites every passage it used.");
    expect(menu()?.textContent).toContain("Shows the matching passages. No AI-written text.");
    expect(menu()?.textContent).not.toContain("Summarise");
    expect(menu()?.textContent).not.toContain("Check my draft");
    expect(entry("Answer")?.getAttribute("aria-checked")).toBe("true");
  });

  it("switches to Search only and sends it with the question", async () => {
    const calls = await mountChat({
      routes: { "POST /api/chats/5/messages": () => sse([{ type: "sources", sources: [] }]) },
    });
    await click(chip("How Tamra responds"));
    await click(entry("Search only"));
    expect(menu()).toBeNull();
    expect(chip("How Tamra responds").textContent).toContain("Search only");
    await ask();
    expect(questionBody(calls)).toEqual({ content: "How long?", mode: "search", think: false });
  });

  it("closes on Escape and on a click outside", async () => {
    await mountChat();
    await click(chip("How Tamra responds"));
    expect(menu()).not.toBeNull();
    await press(menu(), "Escape");
    expect(menu()).toBeNull();
    await click(chip("How Tamra responds"));
    await click(chip("How Tamra responds")); // the chip toggles it
    expect(menu()).toBeNull();
  });
});

describe("model menu", () => {
  it("lists installed and imported models, a download, and the cloud model", async () => {
    await mountChat({ settings: saved({ api_key_set: true, api_key_hint: "1234" }) });
    expect(chip("Model").textContent).toContain("Qwen3-4B");
    await click(chip("Model"));
    const text = menu()?.textContent ?? "";
    expect(text).toContain("On this computer");
    expect(text).toContain("Small · can think longer");
    expect(text).toContain("Medium · downloading 62% · can think longer");
    expect(text).toContain("Imported model");
    expect(text).not.toContain("Qwen3-14B"); // not installed, not downloading
    expect(text).toContain("Cloud API");
    expect(text).toContain("claude-sonnet-5-5");
    expect(text).toContain("Anthropic · passages are sent to the provider");
    expect(entry("Qwen3-4B")?.getAttribute("aria-checked")).toBe("true");
    expect(entry("Qwen3-8B")?.disabled).toBe(true);
    expect(entry("mine")?.disabled).toBe(false);
    expect(entry("claude-sonnet-5-5")?.getAttribute("aria-checked")).toBe("false");
    expect(entry("Manage models…")).toBeDefined();
  });

  it("opens Settings on the AI model tab from Manage models", async () => {
    const onOpenSettings = vi.fn();
    await mountChat({ onOpenSettings });
    await click(chip("Model"));
    await click(entry("Manage models…"));
    expect(onOpenSettings).toHaveBeenCalledWith("model");
    expect(menu()).toBeNull();
  });

  it("saves the mode and model of a chosen local model", async () => {
    const calls = await mountChat({
      routes: {
        "PUT /api/settings": () => json(saved({ local_model_id: "import:mine.gguf" })),
      },
    });
    await click(chip("Model"));
    await click(entry("mine"));
    expect(calls.find((c) => c.key === "PUT /api/settings")?.body).toEqual({
      mode: "local",
      local_model_id: "import:mine.gguf",
    });
    expect(menu()).toBeNull();
  });

  it("chooses the cloud model", async () => {
    const calls = await mountChat({
      settings: saved({ api_key_set: true, api_key_hint: "1234" }),
      routes: {
        "PUT /api/settings": () => json(saved({ mode: "api", api_key_set: true })),
      },
    });
    await click(chip("Model"));
    await click(entry("claude-sonnet-5-5"));
    expect(calls.find((c) => c.key === "PUT /api/settings")?.body).toEqual({ mode: "api" });
    expect(chip("Model").textContent).toContain("claude-sonnet-5-5");
  });

  it("shows the cloud model as active, and no local one, in API mode", async () => {
    await mountChat({
      settings: saved({ mode: "api", api_key_set: true, api_key_hint: "1234" }),
      models: modelsInfo({ active: { mode: "api", label: "claude-sonnet-5-5", id: "qwen3-4b" } }),
    });
    expect(chip("Model").textContent).toContain("claude-sonnet-5-5");
    await click(chip("Model"));
    expect(entry("claude-sonnet-5-5")?.getAttribute("aria-checked")).toBe("true");
    expect(entry("Qwen3-4B")?.getAttribute("aria-checked")).toBe("false");
  });

  it("does not let a cloud model without a key be chosen", async () => {
    await mountChat();
    await click(chip("Model"));
    expect(entry("claude-sonnet-5-5")?.disabled).toBe(true);
    expect(menu()?.textContent).toContain("Anthropic · add an API key in Settings first");
  });

  it("says so when no model is installed", async () => {
    await mountChat({
      models: modelsInfo({
        active: { mode: "local", label: null, id: null },
        local: [localModel({ installed: false })],
        uncatalogued: [],
      }),
    });
    expect(chip("Model").textContent).toContain("No model");
    await click(chip("Model"));
    expect(menu()?.textContent).toContain("No model installed yet.");
  });

  it("refreshes download progress every second while the menu is open", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    const calls = await mountChat();
    const loads = () => calls.filter((c) => c.key === "GET /api/models").length;
    await click(chip("Model"));
    const opened = loads();
    expect(opened).toBeGreaterThanOrEqual(2); // on mount, and on opening
    await vi.advanceTimersByTimeAsync(3000);
    await settle();
    expect(loads()).toBe(opened + 3);
    await click(chip("Model")); // closed: no more polling
    const closed = loads();
    await vi.advanceTimersByTimeAsync(3000);
    expect(loads()).toBe(closed);
  });
});

describe("Think longer", () => {
  it("is off by default and sent as a toggle", async () => {
    const calls = await mountChat({
      routes: { "POST /api/chats/5/messages": () => sse([{ type: "sources", sources: [] }]) },
    });
    expect(thinkChip().getAttribute("aria-pressed")).toBe("false");
    await click(thinkChip());
    expect(thinkChip().getAttribute("aria-pressed")).toBe("true");
    await ask();
    expect(questionBody(calls)).toEqual({ content: "How long?", mode: "answer", think: true });
  });

  it("is disabled for a catalog model that cannot think, and never sent", async () => {
    const calls = await mountChat({
      models: modelsInfo({ local: [localModel({ thinking: false })] }),
      routes: { "POST /api/chats/5/messages": () => sse([{ type: "sources", sources: [] }]) },
    });
    expect(thinkChip().disabled).toBe(true);
    expect(thinkChip().title).toBe("This model cannot think longer.");
    await ask();
    expect(questionBody(calls)).toMatchObject({ think: false });
  });

  it("is enabled for an uncatalogued model", async () => {
    await mountChat({
      models: modelsInfo({ active: { mode: "local", label: "mine", id: "import:mine.gguf" } }),
    });
    expect(thinkChip().disabled).toBe(false);
  });

  it("is enabled in API mode, whatever the local model is", async () => {
    await mountChat({
      settings: saved({ mode: "api", api_key_set: true }),
      models: modelsInfo({
        active: { mode: "api", label: "claude-sonnet-5-5", id: "qwen3-4b" },
        local: [localModel({ thinking: false })],
      }),
    });
    expect(thinkChip().disabled).toBe(false);
  });
});

describe("thinking block", () => {
  it("streams the thinking, then collapses to Thought for N s above the answer", async () => {
    const stream = openStream();
    let done = false;
    await mountChat({
      routes: {
        "GET /api/chats/5": () =>
          json({
            ...chat,
            messages: done
              ? [
                  {
                    id: 2,
                    role: "assistant",
                    content: "Three years [1].",
                    provider: "local",
                    model: "Qwen3-4B",
                    created_at: "t",
                    sources: [source],
                  },
                ]
              : [],
          }),
        "POST /api/chats/5/messages": stream.response,
      },
    });
    await click(thinkChip());
    await ask();
    expect(view?.container.textContent).toContain("Searching your documents…");
    stream.push({ type: "sources", sources: [source] });
    stream.push({ type: "thinking", text: "Reading 3 passages. " });
    stream.push({ type: "thinking", text: "The lease says three years." });
    await settle();
    const block = view!.container.querySelector(".thinking");
    expect(block?.textContent).toContain("Thinking…");
    expect(block?.textContent).toMatch(/\d+ s/);
    expect(block?.textContent).toContain("Reading 3 passages. The lease says three years.");
    expect(view?.container.textContent).not.toContain("Searching your documents…");
    // Collapsible while it streams.
    await click(block?.querySelector("button"));
    expect(view?.container.querySelector(".thinking-body")).toBeNull();
    await click(view?.container.querySelector(".thinking button"));
    expect(view?.container.querySelector(".thinking-body")).not.toBeNull();

    stream.push({ type: "token", text: "Three years [1]." });
    await settle();
    expect(view?.container.querySelector(".thinking")?.textContent).toMatch(/Thought for \d+ s/);
    expect(view?.container.querySelector(".thinking-body")).toBeNull(); // collapsed by the answer

    done = true;
    stream.push({ type: "done", message_id: 2 });
    stream.finish();
    await settle();
    const answer = view!.container.querySelector(".message.assistant");
    expect(answer?.querySelector(".thinking")?.textContent).toMatch(/Thought for \d+ s/);
    expect(answer?.querySelector(".thinking-body")).toBeNull();
    expect(answer?.querySelector(".answer-meta")?.textContent).toMatch(
      /^Qwen3-4B · local · thought for \d+ s$/,
    );
    await click(answer?.querySelector(".thinking button")); // expand what it thought
    expect(view?.container.querySelector(".thinking-body")?.textContent).toContain(
      "The lease says three years.",
    );
  });

  it("shows no thinking block for an answer loaded from the core", async () => {
    await mountChat({
      routes: {
        "GET /api/chats/5": () =>
          json({
            ...chat,
            messages: [
              {
                id: 2,
                role: "assistant",
                content: "Three years [1].",
                provider: "local",
                model: "Qwen3-4B",
                created_at: "t",
                sources: [source],
              },
            ],
          }),
      },
    });
    expect(view?.container.querySelector(".thinking")).toBeNull();
    expect(view?.container.querySelector(".answer-meta")?.textContent).toBe("Qwen3-4B · local");
  });

  it("counts the seconds while it thinks and reports them when done", async () => {
    const live = { text: "hmm", startedAt: Date.now() - 12_000, endedAt: null };
    view = await mount(<Thinking thought={live} />);
    expect(view.container.textContent).toContain("12 s");
    await view.unmount();
    const finished = { text: "hmm", startedAt: 1000, endedAt: 19_000 };
    view = await mount(<Thinking thought={finished} />);
    expect(view.container.textContent).toContain("Thought for 18 s");
    expect(view.container.querySelector(".thinking-body")).toBeNull();
  });
});

describe("answer meta line", () => {
  it("names the cloud provider", async () => {
    await mountChat({
      routes: {
        "GET /api/chats/5": () =>
          json({
            ...chat,
            messages: [
              {
                id: 2,
                role: "assistant",
                content: "Three years [1].",
                provider: "anthropic",
                model: "claude-sonnet-5-5",
                created_at: "t",
                sources: [source],
              },
            ],
          }),
      },
    });
    expect(view?.container.querySelector(".answer-meta")?.textContent).toBe(
      "claude-sonnet-5-5 · Anthropic",
    );
  });
});

describe("search-only results", () => {
  it("shows the passages with their text and no answer", async () => {
    const second = { ...source, n: 2, file: "HR/notice.pdf", label: "p. 4", text: "Thirty days." };
    let asked = false;
    const reply = (sources: unknown[]) => ({
      id: 3,
      role: "assistant",
      content: "",
      provider: null,
      model: null,
      created_at: "t",
      sources,
    });
    const calls = await mountChat({
      routes: {
        "POST /api/chats/5/messages": () => {
          asked = true;
          return sse([
            { type: "sources", sources: [source, second] },
            { type: "done", message_id: 3 },
          ]);
        },
        "GET /api/chats/5": () =>
          json({ ...chat, messages: asked ? [reply([source, second])] : [] }),
      },
    });
    await click(chip("How Tamra responds"));
    await click(entry("Search only"));
    await ask();
    expect(questionBody(calls)).toMatchObject({ mode: "search" });
    const shown = view!.container.querySelector(".message.assistant");
    expect(shown?.querySelector(".passages-label")?.textContent).toBe("Passages found");
    const cards = [...shown!.querySelectorAll(".passage")];
    expect(cards).toHaveLength(2);
    expect(cards[0].textContent).toContain("lease.pdf");
    expect(cards[0].textContent).toContain("p. 2 · HR");
    expect(cards[0].textContent).toContain("The lease term is three years.");
    expect(cards[1].textContent).toContain("Thirty days.");
    expect(shown?.querySelector(".answer-text")).toBeNull();
    expect(shown?.querySelector(".cite")).toBeNull();
    await click(cards[1]);
    expect(view?.container.querySelector(".source-panel")?.textContent).toContain("Thirty days.");
  });

  it("shows a saved search-only reply as passages", async () => {
    await mountChat({
      routes: {
        "GET /api/chats/5": () =>
          json({
            ...chat,
            messages: [
              {
                id: 2,
                role: "assistant",
                content: "",
                provider: null,
                model: null,
                created_at: "t",
                sources: [source],
              },
            ],
          }),
      },
    });
    expect(view?.container.textContent).toContain("Passages found");
    expect(view?.container.querySelector(".passage")?.textContent).toContain("three years");
  });
});

describe("model errors", () => {
  it("shows a message for the reason, with a button to the model settings on model_missing", async () => {
    const onOpenSettings = vi.fn();
    await mountChat({
      onOpenSettings,
      routes: {
        "POST /api/chats/5/messages": () =>
          sse([
            { type: "sources", sources: [source] },
            { type: "error", message: "No local model is installed.", reason: "model_missing" },
          ]),
      },
    });
    await ask();
    const alert = view!.container.querySelector('[role="alert"]');
    expect(alert?.textContent).toContain(
      "No model is ready to answer. Choose or download one in Settings.",
    );
    expect(alert?.textContent).not.toContain("No local model is installed.");
    await click(buttonByText(alert!, "Open model settings"));
    expect(onOpenSettings).toHaveBeenCalledWith("model");
  });

  it.each([
    ["offline", "Could not reach the model. Check your internet connection and try again."],
    ["auth", "The API key is missing or was refused. Check it in Settings."],
    ["quota", "The provider's usage limit was reached. Try again later."],
  ] as const)("explains %s without a settings button", async (reason, message) => {
    await mountChat({
      routes: {
        "POST /api/chats/5/messages": () =>
          sse([{ type: "error", message: "raw provider text", reason }]),
      },
    });
    await ask();
    const alert = view!.container.querySelector('[role="alert"]');
    expect(alert?.textContent).toBe(message);
    expect(alert?.querySelector("button")).toBeNull();
  });

  it("shows the core's own message for other errors", async () => {
    await mountChat({
      routes: {
        "POST /api/chats/5/messages": () =>
          sse([{ type: "error", message: "The model returned no answer.", reason: "other" }]),
      },
    });
    await ask();
    expect(view?.container.querySelector('[role="alert"]')?.textContent).toBe(
      "The model returned no answer.",
    );
  });
});

describe("Thai", () => {
  it("translates the chips and the menus", async () => {
    await mountChat({ language: "th" });
    expect(chip("วิธีที่ Tamra ตอบ").textContent).toContain("ตอบคำถาม");
    expect(view?.container.textContent).toContain("คิดให้ลึกขึ้น");
    await click(chip("โมเดล"));
    expect(menu()?.textContent).toContain("ในเครื่องนี้");
    expect(menu()?.textContent).toContain("ขนาดกลาง · กำลังดาวน์โหลด 62% · คิดให้ลึกขึ้นได้");
    expect(menu()?.textContent).toContain("จัดการโมเดล…");
  });
});
