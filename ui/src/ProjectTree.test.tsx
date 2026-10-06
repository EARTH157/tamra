import { afterEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import type { Chat, Project } from "./types";
import {
  buttonByText,
  type Call,
  click,
  json,
  type Mounted,
  mockFetch,
  mount,
  press,
  sse,
  typeInto,
} from "./test-utils";

let view: Mounted | undefined;

afterEach(async () => {
  await view?.unmount();
  view = undefined;
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  localStorage.clear();
});

const idle = {
  stale: false,
  counts: { pending: 0, indexing: 0, indexed: 2, failed: 0, skipped: 0 },
  current: null,
  error: null,
  problems: [],
};
const contracts: Project = { id: 1, name: "Contracts", folder_path: "C:/c", index: idle };
const hr: Project = {
  id: 2,
  name: "HR Documents",
  folder_path: "D:/hr",
  index: { ...idle, counts: { ...idle.counts, indexed: 128 } },
};
const chat = (id: number, title: string, project_id: number): Chat => ({
  id,
  title,
  created_at: "t",
  updated_at: "t",
  project_id,
  scope: [project_id],
});
const lease = chat(3, "Lease length", 1);
const notice = chat(4, "Notice period", 1);
const leave = chat(5, "Leave policy", 2);

type Core = { projects: Project[]; chats: Chat[] };

/** A core that keeps the projects and chats it is given; `extra` adds or replaces routes. */
function boot(core: Core, extra: Record<string, () => Response> = {}): Call[] {
  return mockFetch({
    "GET /api/projects": () => json({ projects: core.projects }),
    "GET /api/chats": () => json({ chats: core.chats }),
    ...extra,
  });
}

const tree = () => view!.container.querySelector(".project-tree") as HTMLElement;
const toggle = (name: string) =>
  tree().querySelector<HTMLButtonElement>(`.project-toggle[title="${name}"]`);
const projectMenuButton = (name: string) =>
  view!.container.querySelector(`button[aria-label="Options for project ${name}"]`);
const chatMenuButton = (title: string) =>
  view!.container.querySelector(`button[aria-label="Options for ${title}"]`);
const menu = () => document.querySelector<HTMLElement>('[role="menu"]');
const dialog = () => document.querySelector<HTMLElement>('[role="dialog"]');
const items = (root: ParentNode | null) =>
  [...(root?.querySelectorAll('[role="menuitem"]') ?? [])].map((item) => item.textContent?.trim());
const card = () => view!.container.querySelector(".collection-card") as HTMLElement;
const cardName = () => card().querySelector(".collection-name")?.textContent;
const names = () => [...tree().querySelectorAll(".project-name")].map((n) => n.textContent);
const chatTitles = (project: string) =>
  [...(toggle(project)?.closest("li")?.querySelectorAll(".chat-title") ?? [])].map((n) =>
    n.textContent?.trim(),
  );

describe("The project tree", () => {
  it("lists every project with its indexed count and opens the first one", async () => {
    boot({ projects: [contracts, hr], chats: [lease, notice, leave] });
    view = await mount(<App />);
    expect(names()).toEqual(["Contracts", "HR Documents"]);
    expect([...tree().querySelectorAll(".project-count")].map((n) => n.textContent)).toEqual([
      "2",
      "128",
    ]);
    expect(toggle("Contracts")?.getAttribute("aria-expanded")).toBe("true");
    expect(toggle("HR Documents")?.getAttribute("aria-expanded")).toBe("false");
    expect(chatTitles("Contracts")).toEqual(["Lease length", "Notice period"]);
    expect(view.container.textContent).not.toContain("Leave policy"); // under the closed project
    expect(view.container.textContent).not.toContain("CHATS");
    expect(view.container.querySelector('button[aria-label="New project"]')).not.toBeNull();
  });

  it("opens and closes a project, and remembers which are open", async () => {
    boot({ projects: [contracts, hr], chats: [lease, leave] });
    view = await mount(<App />);
    await click(toggle("HR Documents"));
    expect(toggle("HR Documents")?.getAttribute("aria-expanded")).toBe("true");
    expect(chatTitles("HR Documents")).toEqual(["Leave policy"]);
    expect(JSON.parse(localStorage.getItem("tamra.expandedProjects") ?? "[]")).toEqual([1, 2]);
    await click(toggle("Contracts"));
    expect(toggle("Contracts")?.getAttribute("aria-expanded")).toBe("false");
    expect(chatTitles("Contracts")).toEqual([]);
    expect(JSON.parse(localStorage.getItem("tamra.expandedProjects") ?? "[]")).toEqual([2]);
  });

  it("starts with the projects that were left open", async () => {
    localStorage.setItem("tamra.expandedProjects", "[2]");
    boot({ projects: [contracts, hr], chats: [lease, leave] });
    view = await mount(<App />);
    expect(toggle("HR Documents")?.getAttribute("aria-expanded")).toBe("true");
  });

  it("works when the browser's storage is unavailable", async () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    boot({ projects: [contracts, hr], chats: [lease, leave] });
    view = await mount(<App />);
    await click(toggle("HR Documents"));
    expect(toggle("HR Documents")?.getAttribute("aria-expanded")).toBe("true");
  });

  it("opens the project of the chat you pick and shows its card", async () => {
    boot(
      { projects: [contracts, hr], chats: [lease, leave] },
      { "GET /api/chats/5": () => json({ ...leave, messages: [] }) },
    );
    view = await mount(<App />);
    expect(cardName()).toBe("Contracts");
    await click(toggle("HR Documents")); // opens it
    await click(view.container.querySelector('button[title="Leave policy"]'));
    expect(cardName()).toBe("HR Documents");
    expect(card().querySelector(".collection-path")?.textContent).toBe("D:/hr");
    expect(card().textContent).toContain("128 files indexed");
    expect(buttonByText(card(), "Change folder")).toBeDefined();
    expect(view.container.querySelector(".chat-row.active .chat-title")?.textContent).toBe(
      "Leave policy",
    );
  });

  it("explains an empty project", async () => {
    boot({ projects: [contracts], chats: [] });
    view = await mount(<App />);
    expect(view.container.querySelector(".project-empty")?.textContent).toBe(
      "No chats yet. Ask a question to start one.",
    );
  });
});

describe("The project menu", () => {
  it("lists Project settings, Rename, Change folder, Rebuild index and Delete project", async () => {
    boot({ projects: [contracts], chats: [] });
    view = await mount(<App />);
    await click(projectMenuButton("Contracts"));
    expect(menu()?.getAttribute("aria-label")).toBe("Project options");
    expect(items(menu())).toEqual([
      "Project settings",
      "Rename",
      "Change folder",
      "Rebuild index",
      "Delete project",
    ]);
    expect(menu()?.textContent).toContain("Contracts"); // headed with the project's name
  });

  it("closes with Esc and returns to the ⋯ button", async () => {
    boot({ projects: [contracts], chats: [] });
    view = await mount(<App />);
    await click(projectMenuButton("Contracts"));
    await press(menu(), "Escape");
    expect(menu()).toBeNull();
    expect(document.activeElement).toBe(projectMenuButton("Contracts"));
  });

  it("moves between items with the arrow keys, wrapping round", async () => {
    boot({ projects: [contracts], chats: [] });
    view = await mount(<App />);
    await click(projectMenuButton("Contracts"));
    expect(document.activeElement?.textContent).toBe("Project settings");
    await press(document.activeElement, "ArrowUp");
    expect(document.activeElement?.textContent).toBe("Delete project");
    await press(document.activeElement, "ArrowDown");
    expect(document.activeElement?.textContent).toBe("Project settings");
  });

  it("renames a project inline", async () => {
    let core: Core = { projects: [contracts], chats: [] };
    const calls = boot(core, {
      "PATCH /api/projects/1": () => {
        core = { ...core, projects: [{ ...contracts, name: "Leases" }] };
        return json(core.projects[0]);
      },
      "GET /api/projects": () => json({ projects: core.projects }),
    });
    view = await mount(<App />);
    await click(projectMenuButton("Contracts"));
    await click(buttonByText(menu()!, "Rename"));
    const input = view.container.querySelector<HTMLInputElement>(
      'input[aria-label="Project name"]',
    );
    expect(input?.value).toBe("Contracts");
    await typeInto(input, "Leases");
    await press(input, "Enter");
    expect(calls).toContainEqual({ key: "PATCH /api/projects/1", body: { name: "Leases" } });
    expect(view.container.querySelector('input[aria-label="Project name"]')).toBeNull();
    expect(names()).toEqual(["Leases"]);
  });

  it("cancels a project rename with Esc", async () => {
    const calls = boot({ projects: [contracts], chats: [] });
    view = await mount(<App />);
    await click(projectMenuButton("Contracts"));
    await click(buttonByText(menu()!, "Rename"));
    const input = view.container.querySelector('input[aria-label="Project name"]');
    await typeInto(input, "Other");
    await press(input, "Escape");
    expect(names()).toEqual(["Contracts"]);
    expect(calls.some((c) => c.key.startsWith("PATCH"))).toBe(false);
  });

  it("shows a refused rename in the banner, in the user's words", async () => {
    boot(
      { projects: [contracts, hr], chats: [] },
      {
        "PATCH /api/projects/1": () =>
          json({ detail: "Another project already has this name." }, 409),
      },
    );
    view = await mount(<App />);
    await click(projectMenuButton("Contracts"));
    await click(buttonByText(menu()!, "Rename"));
    const input = view.container.querySelector('input[aria-label="Project name"]');
    await typeInto(input, "HR Documents");
    await press(input, "Enter");
    expect(view.container.querySelector('[role="alert"]')?.textContent).toContain(
      "A project with this name already exists.",
    );
    expect(names()).toEqual(["Contracts", "HR Documents"]);
  });

  it("rebuilds the index", async () => {
    const calls = boot(
      { projects: [contracts], chats: [] },
      { "POST /api/projects/1/rebuild": () => json({ status: "rebuilding" }, 202) },
    );
    view = await mount(<App />);
    await click(projectMenuButton("Contracts"));
    await click(buttonByText(menu()!, "Rebuild index"));
    expect(calls.map((c) => c.key)).toContain("POST /api/projects/1/rebuild");
    expect(menu()).toBeNull();
  });

  it("changes the folder of that project", async () => {
    const calls = boot(
      { projects: [contracts, hr], chats: [] },
      { "PUT /api/projects/2/folder": () => json(hr) },
    );
    view = await mount(<App />);
    await click(projectMenuButton("HR Documents"));
    await click(buttonByText(menu()!, "Change folder"));
    expect(dialog()?.textContent).toContain("Change documents folder");
    const input = dialog()?.querySelector<HTMLInputElement>('input[aria-label="Folder path"]');
    expect(input?.value).toBe("D:/hr");
    await typeInto(input, "E:/new");
    await click(buttonByText(dialog()!, "Use this folder"));
    expect(calls).toContainEqual({
      key: "PUT /api/projects/2/folder",
      body: { folder_path: "E:/new" },
    });
    expect(dialog()).toBeNull();
  });

  it("opens the project settings: name, folder, index status and Rebuild index", async () => {
    const calls = boot(
      { projects: [contracts, hr], chats: [] },
      { "POST /api/projects/2/rebuild": () => json({ status: "rebuilding" }, 202) },
    );
    view = await mount(<App />);
    await click(projectMenuButton("HR Documents"));
    await click(buttonByText(menu()!, "Project settings"));
    const box = dialog()!;
    expect(box.querySelector("h2")?.textContent).toBe("Project settings");
    expect(box.querySelector<HTMLInputElement>("input.field-input")?.value).toBe("HR Documents");
    expect(box.querySelector(".path-text")?.textContent).toBe("D:/hr");
    expect(box.querySelector(".index-panel")?.textContent).toContain("128 files indexed");
    expect(buttonByText(box, "Save")?.disabled).toBe(true); // nothing changed yet
    await click(buttonByText(box, "Rebuild index"));
    expect(calls.map((c) => c.key)).toContain("POST /api/projects/2/rebuild");
    expect(dialog()).not.toBeNull(); // rebuilding does not close it
  });

  it("renames from the project settings and closes", async () => {
    let core: Core = { projects: [contracts], chats: [] };
    const calls = boot(core, {
      "GET /api/projects": () => json({ projects: core.projects }),
      "PATCH /api/projects/1": () => {
        core = { ...core, projects: [{ ...contracts, name: "Leases" }] };
        return json(core.projects[0]);
      },
    });
    view = await mount(<App />);
    await click(projectMenuButton("Contracts"));
    await click(buttonByText(menu()!, "Project settings"));
    await typeInto(dialog()?.querySelector("input.field-input"), "  Leases ");
    await click(buttonByText(dialog()!, "Save"));
    expect(calls).toContainEqual({ key: "PATCH /api/projects/1", body: { name: "Leases" } });
    expect(dialog()).toBeNull();
    expect(names()).toEqual(["Leases"]);
  });

  it("shows a refused rename inside the project settings", async () => {
    boot(
      { projects: [contracts, hr], chats: [] },
      {
        "PATCH /api/projects/1": () =>
          json({ detail: "Another project already has this name." }, 409),
      },
    );
    view = await mount(<App />);
    await click(projectMenuButton("Contracts"));
    await click(buttonByText(menu()!, "Project settings"));
    await typeInto(dialog()?.querySelector("input.field-input"), "HR Documents");
    await click(buttonByText(dialog()!, "Save"));
    expect(dialog()?.querySelector('[role="alert"]')?.textContent).toBe(
      "A project with this name already exists.",
    );
  });

  it("goes back to the project settings after Change folder", async () => {
    boot({ projects: [contracts], chats: [] });
    view = await mount(<App />);
    await click(projectMenuButton("Contracts"));
    await click(buttonByText(menu()!, "Project settings"));
    await click(buttonByText(dialog()!, "Change folder"));
    expect(dialog()?.querySelector("h2")?.textContent).toBe("Change documents folder");
    await click(buttonByText(dialog()!, "Cancel"));
    expect(dialog()?.querySelector("h2")?.textContent).toBe("Project settings");
  });

  it("deletes a project after a confirmation that names its chats", async () => {
    let core: Core = { projects: [contracts, hr], chats: [lease, notice, leave] };
    const calls = boot(core, {
      "GET /api/projects": () => json({ projects: core.projects }),
      "GET /api/chats": () => json({ chats: core.chats }),
      "DELETE /api/projects/1": () => {
        core = { projects: [hr], chats: [leave] };
        return json({ chats_deleted: 2 });
      },
    });
    view = await mount(<App />);
    await click(projectMenuButton("Contracts"));
    await click(buttonByText(menu()!, "Delete project"));
    expect(dialog()?.querySelector("h2")?.textContent).toBe("Delete this project?");
    expect(dialog()?.textContent).toContain(
      '"Contracts", its search index, and its 2 chats will be removed from this computer.',
    );
    expect(dialog()?.textContent).toContain("Your folder and its files are not touched.");
    await click(buttonByText(dialog()!, "Cancel"));
    expect(calls.some((c) => c.key.startsWith("DELETE"))).toBe(false);

    await click(projectMenuButton("Contracts"));
    await click(buttonByText(menu()!, "Delete project"));
    await click(buttonByText(dialog()!, "Delete project"));
    expect(calls.map((c) => c.key)).toContain("DELETE /api/projects/1");
    expect(dialog()).toBeNull();
    expect(names()).toEqual(["HR Documents"]);
    expect(view.container.textContent).not.toContain("Lease length");
  });

  it("words the confirmation for one chat and for none", async () => {
    boot({ projects: [contracts, hr], chats: [leave] });
    view = await mount(<App />);
    await click(projectMenuButton("HR Documents"));
    await click(buttonByText(menu()!, "Delete project"));
    expect(dialog()?.textContent).toContain("its search index, and its 1 chat will be removed");
    await click(buttonByText(dialog()!, "Cancel"));
    await click(projectMenuButton("Contracts"));
    await click(buttonByText(menu()!, "Delete project"));
    expect(dialog()?.textContent).toContain(
      '"Contracts" and its search index will be removed from this computer.',
    );
    expect(dialog()?.textContent).not.toContain("chat");
  });

  it("leaves the open chat when its project is deleted", async () => {
    let core: Core = { projects: [contracts, hr], chats: [lease, leave] };
    boot(core, {
      "GET /api/projects": () => json({ projects: core.projects }),
      "GET /api/chats": () => json({ chats: core.chats }),
      "GET /api/chats/3": () => json({ ...lease, messages: [] }),
      "DELETE /api/projects/1": () => {
        core = { projects: [hr], chats: [leave] };
        return json({ chats_deleted: 1 });
      },
    });
    view = await mount(<App />);
    await click(view.container.querySelector('button[title="Lease length"]'));
    expect(view.container.querySelector(".chat-row.active")).not.toBeNull();
    await click(projectMenuButton("Contracts"));
    await click(buttonByText(menu()!, "Delete project"));
    await click(buttonByText(dialog()!, "Delete project"));
    expect(view.container.querySelector(".chat-row.active")).toBeNull();
    expect(cardName()).toBe("HR Documents");
  });

  it("keeps the dialog open and says why when the delete is refused", async () => {
    boot(
      { projects: [contracts], chats: [] },
      {
        "DELETE /api/projects/1": () =>
          json({ detail: "An answer is being written. Wait for it to finish." }, 409),
      },
    );
    view = await mount(<App />);
    await click(projectMenuButton("Contracts"));
    await click(buttonByText(menu()!, "Delete project"));
    await click(buttonByText(dialog()!, "Delete project"));
    expect(dialog()?.querySelector('[role="alert"]')?.textContent).toBe(
      "An answer is being written. Wait for it to finish.",
    );
    expect(names()).toEqual(["Contracts"]);
  });
});

describe("The chat menu: Move to project", () => {
  it("is disabled when there is no other project", async () => {
    boot({ projects: [contracts], chats: [lease] });
    view = await mount(<App />);
    await click(chatMenuButton("Lease length"));
    expect(buttonByText(menu()!, "Move to project")?.disabled).toBe(true);
  });

  it("opens a flyout of the other projects from the keyboard", async () => {
    boot({ projects: [contracts, hr], chats: [lease] });
    view = await mount(<App />);
    await click(chatMenuButton("Lease length"));
    await press(document.activeElement, "ArrowDown"); // Rename -> Move to project
    const move = buttonByText(menu()!, "Move to project");
    expect(document.activeElement).toBe(move);
    expect(move?.getAttribute("aria-haspopup")).toBe("menu");
    expect(move?.getAttribute("aria-expanded")).toBe("false");
    await press(move, "ArrowRight");
    expect(move?.getAttribute("aria-expanded")).toBe("true");
    const flyout = menu()!.querySelector<HTMLElement>('[role="menu"]');
    expect(flyout?.getAttribute("aria-label")).toBe("Move to project");
    expect(items(flyout)).toEqual(["HR Documents"]); // not the chat's own project
    expect(document.activeElement?.textContent).toBe("HR Documents");
    await press(document.activeElement, "ArrowLeft");
    expect(menu()!.querySelector('[role="menu"]')).toBeNull();
    expect(document.activeElement).toBe(move);
  });

  it("closes only the flyout on the first Esc, then the menu", async () => {
    boot({ projects: [contracts, hr], chats: [lease] });
    view = await mount(<App />);
    await click(chatMenuButton("Lease length"));
    await press(document.activeElement, "ArrowDown");
    await press(document.activeElement, "ArrowRight");
    await press(document.activeElement, "Escape");
    expect(menu()).not.toBeNull();
    expect(menu()!.querySelector('[role="menu"]')).toBeNull();
    await press(document.activeElement, "Escape");
    expect(menu()).toBeNull();
    expect(document.activeElement).toBe(chatMenuButton("Lease length"));
  });

  it("moves the chat to the chosen project", async () => {
    let core: Core = { projects: [contracts, hr], chats: [lease, leave] };
    const calls = boot(core, {
      "GET /api/chats": () => json({ chats: core.chats }),
      "PATCH /api/chats/3": () => {
        const moved = { ...lease, project_id: 2, scope: [2] };
        core = { ...core, chats: [moved, leave] };
        return json(moved);
      },
    });
    view = await mount(<App />);
    expect(chatTitles("Contracts")).toEqual(["Lease length"]);
    await click(chatMenuButton("Lease length"));
    await click(buttonByText(menu()!, "Move to project"));
    const target = menu()!.querySelector('[role="menu"]')!;
    await click(buttonByText(target, "HR Documents"));
    expect(calls).toContainEqual({ key: "PATCH /api/chats/3", body: { project_id: 2 } });
    expect(menu()).toBeNull();
    expect(chatTitles("Contracts")).toEqual([]);
    expect(toggle("HR Documents")?.getAttribute("aria-expanded")).toBe("true"); // shows where it went
    expect(chatTitles("HR Documents")).toEqual(["Lease length", "Leave policy"]);
  });

  it("makes the new project the current one when the open chat is moved", async () => {
    let core: Core = { projects: [contracts, hr], chats: [lease] };
    boot(core, {
      "GET /api/chats": () => json({ chats: core.chats }),
      "GET /api/chats/3": () => json({ ...lease, messages: [] }),
      "PATCH /api/chats/3": () => {
        const moved = { ...lease, project_id: 2, scope: [2] };
        core = { ...core, chats: [moved] };
        return json(moved);
      },
    });
    view = await mount(<App />);
    await click(view.container.querySelector('button[title="Lease length"]'));
    expect(cardName()).toBe("Contracts");
    await click(chatMenuButton("Lease length"));
    await click(buttonByText(menu()!, "Move to project"));
    await click(buttonByText(menu()!.querySelector('[role="menu"]')!, "HR Documents"));
    expect(cardName()).toBe("HR Documents");
  });
});

describe("New project", () => {
  const fill = async (name: string, folder: string) => {
    await typeInto(dialog()?.querySelector('input:not([aria-label="Folder path"])'), name);
    await typeInto(dialog()?.querySelector('input[aria-label="Folder path"]'), folder);
  };

  it("opens from the + button with a name, a folder and a hint", async () => {
    boot({ projects: [contracts], chats: [] });
    view = await mount(<App />);
    await click(view.container.querySelector('button[aria-label="New project"]'));
    const box = dialog()!;
    expect(box.querySelector("h2")?.textContent).toBe("New project");
    expect(box.textContent).toContain("A project keeps one folder of documents");
    expect(box.textContent).toContain("Your files are never changed.");
    expect(document.activeElement?.getAttribute("class")).toBe("field-input"); // the name
    expect(buttonByText(box, "Browse…")).toBeDefined();
    expect(buttonByText(box, "Create project")?.disabled).toBe(true); // no folder yet
    await press(document.activeElement, "Escape");
    expect(dialog()).toBeNull();
  });

  it("creates the project, lists it and makes it the current one", async () => {
    let core: Core = { projects: [contracts], chats: [] };
    const suppliers: Project = {
      id: 7,
      name: "Supplier agreements",
      folder_path: "D:/s",
      index: idle,
    };
    const calls = boot(core, {
      "GET /api/projects": () => json({ projects: core.projects }),
      "POST /api/projects": () => {
        core = { ...core, projects: [contracts, suppliers] };
        return json(suppliers, 201);
      },
    });
    view = await mount(<App />);
    await click(view.container.querySelector('button[aria-label="New project"]'));
    await fill("  Supplier agreements ", " D:/s ");
    await click(buttonByText(dialog()!, "Create project"));
    expect(calls).toContainEqual({
      key: "POST /api/projects",
      body: { name: "Supplier agreements", folder_path: "D:/s" },
    });
    expect(dialog()).toBeNull();
    expect(names()).toEqual(["Contracts", "Supplier agreements"]);
    expect(cardName()).toBe("Supplier agreements");
  });

  it("leaves the name out when it is blank, so the core uses the folder's name", async () => {
    const calls = boot(
      { projects: [], chats: [] },
      { "POST /api/projects": () => json({ ...contracts, name: "c" }, 201) },
    );
    view = await mount(<App />);
    await click(buttonByText(view.container, "Choose a folder"));
    await fill("", "C:/c");
    await click(buttonByText(dialog()!, "Create project"));
    expect(calls).toContainEqual({ key: "POST /api/projects", body: { folder_path: "C:/c" } });
  });

  it("says when the name is taken, and stays open to fix it", async () => {
    boot(
      { projects: [contracts], chats: [] },
      {
        "POST /api/projects": () =>
          json({ detail: "Another project already has this name." }, 409),
      },
    );
    view = await mount(<App />);
    await click(view.container.querySelector('button[aria-label="New project"]'));
    await fill("contracts", "D:/x");
    await click(buttonByText(dialog()!, "Create project"));
    expect(dialog()?.querySelector('[role="alert"]')?.textContent).toBe(
      "A project with this name already exists.",
    );
    expect(buttonByText(dialog()!, "Create project")?.disabled).toBe(false);
    expect(names()).toEqual(["Contracts"]);
  });

  it("says when the folder does not exist", async () => {
    boot(
      { projects: [contracts], chats: [] },
      { "POST /api/projects": () => json({ detail: "Folder not found." }, 400) },
    );
    view = await mount(<App />);
    await click(view.container.querySelector('button[aria-label="New project"]'));
    await fill("Anything", "D:/nope");
    await click(buttonByText(dialog()!, "Create project"));
    expect(dialog()?.querySelector('[role="alert"]')?.textContent).toBe(
      "That folder does not exist.",
    );
  });

  it("fills the folder from Browse…", async () => {
    boot(
      { projects: [], chats: [] },
      { "POST /api/pick-folder": () => json({ folder_path: "D:/picked" }) },
    );
    view = await mount(<App />);
    await click(buttonByText(view.container, "Choose a folder"));
    await click(buttonByText(dialog()!, "Browse…"));
    expect(
      dialog()?.querySelector<HTMLInputElement>('input[aria-label="Folder path"]')?.value,
    ).toBe("D:/picked");
  });
});

describe("New chat", () => {
  /** Ask a question in the empty chat that "New chat" shows, and return the chat it created. */
  async function ask(calls: Call[]) {
    await typeInto(view!.container.querySelector("textarea"), "How long?");
    await click(view!.container.querySelector('button[type="submit"]'));
    return calls.find((call) => call.key === "POST /api/chats");
  }
  const routes = (project: number) => ({
    "POST /api/chats": () => json(chat(9, "", project), 201),
    "GET /api/chats/9": () => json({ ...chat(9, "", project), messages: [] }),
    "POST /api/chats/9/messages": () => sse([{ type: "done", message_id: 1 }]),
  });

  it("starts in the first project when none was chosen", async () => {
    const calls = boot({ projects: [contracts, hr], chats: [] }, routes(1));
    view = await mount(<App />);
    expect((await ask(calls))?.body).toEqual({ project_id: 1 });
  });

  it("starts in the project of the open chat", async () => {
    const calls = boot(
      { projects: [contracts, hr], chats: [leave] },
      { ...routes(2), "GET /api/chats/5": () => json({ ...leave, messages: [] }) },
    );
    view = await mount(<App />);
    await click(toggle("HR Documents"));
    await click(view.container.querySelector('button[title="Leave policy"]'));
    await click(buttonByText(view.container, "New chat"));
    expect((await ask(calls))?.body).toEqual({ project_id: 2 });
  });

  it("starts in the project that was opened in the tree", async () => {
    const calls = boot({ projects: [contracts, hr], chats: [lease] }, routes(2));
    view = await mount(<App />);
    await click(toggle("HR Documents"));
    expect((await ask(calls))?.body).toEqual({ project_id: 2 });
  });

  it("is disabled until a project exists", async () => {
    boot({ projects: [], chats: [] });
    view = await mount(<App />);
    expect(buttonByText(view.container, "New chat")?.disabled).toBe(true);
  });
});
