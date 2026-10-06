import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { anyIndexing, FAST_POLL_MS, IDLE_POLL_MS, useProjects } from "./projects";
import { json, mockFetch } from "./test-utils";
import type { Project } from "./types";

const counts = { pending: 0, indexing: 0, indexed: 3, failed: 0, skipped: 0 };
const project = (indexing: number, id = 1): Project => ({
  id,
  name: `P${id}`,
  folder_path: "C:/p",
  index: { stale: false, counts: { ...counts, indexing }, current: null, error: null, problems: [] },
});

describe("anyIndexing", () => {
  it("is true while any project has files pending or being indexed", () => {
    expect(anyIndexing([project(0), project(0, 2)])).toBe(false);
    expect(anyIndexing([project(0), project(1, 2)])).toBe(true);
    const waiting = project(0);
    waiting.index.counts.pending = 2;
    expect(anyIndexing([waiting])).toBe(true);
  });
});

describe("useProjects polling", () => {
  let latest: ReturnType<typeof useProjects> | undefined;
  let host: HTMLElement | undefined;
  let unmount: (() => Promise<void>) | undefined;

  function Probe({ onReconnect }: { onReconnect?: () => void }) {
    latest = useProjects(onReconnect);
    return null;
  }

  async function mountProbe(onReconnect?: () => void) {
    host = document.createElement("div");
    document.body.appendChild(host);
    const root = createRoot(host);
    await act(async () => {
      root.render(<Probe onReconnect={onReconnect} />);
    });
    unmount = async () => {
      await act(async () => root.unmount());
    };
  }

  async function advance(ms: number) {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(ms);
    });
  }

  beforeEach(() => {
    // Only the timer is faked: the mocked requests settle on their own.
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
  });

  afterEach(async () => {
    await unmount?.();
    host?.remove();
    unmount = undefined;
    latest = undefined;
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("reloads quickly while a project is indexing, and slowly once all are done", async () => {
    let indexing = 2;
    const calls = mockFetch({ "GET /api/projects": () => json({ projects: [project(indexing)] }) });
    const loads = () => calls.filter((c) => c.key === "GET /api/projects").length;
    await mountProbe();
    expect(loads()).toBe(1);
    expect(latest?.projects).toHaveLength(1);

    await advance(FAST_POLL_MS);
    expect(loads()).toBe(2); // still indexing: the next look is soon
    await advance(FAST_POLL_MS);
    expect(loads()).toBe(3);

    indexing = 0; // the next answer says it is done
    await advance(FAST_POLL_MS);
    expect(loads()).toBe(4);
    await advance(FAST_POLL_MS * 2);
    expect(loads()).toBe(4); // idle now: no quick polling
    await advance(IDLE_POLL_MS);
    expect(loads()).toBe(5);
  });

  it("keeps trying quickly while the core is unreachable, then reports the reconnect once", async () => {
    let reachable = false;
    const calls = mockFetch({
      "GET /api/projects": () => {
        if (!reachable) throw new TypeError("Failed to fetch");
        return json({ projects: [project(0)] });
      },
    });
    const loads = () => calls.filter((c) => c.key === "GET /api/projects").length;
    const onReconnect = vi.fn();
    await mountProbe(onReconnect);
    expect(latest?.offline).toBe("Failed to fetch");
    await advance(FAST_POLL_MS);
    await advance(FAST_POLL_MS);
    expect(loads()).toBe(3);
    expect(onReconnect).not.toHaveBeenCalled();

    reachable = true;
    await advance(FAST_POLL_MS);
    expect(latest?.offline).toBeNull();
    expect(latest?.projects).toHaveLength(1);
    expect(onReconnect).toHaveBeenCalledTimes(1);
    await advance(IDLE_POLL_MS);
    expect(onReconnect).toHaveBeenCalledTimes(1);
  });

  it("calls the project routes, and reloads after each", async () => {
    const calls = mockFetch({
      "GET /api/projects": () => json({ projects: [project(0)] }),
      "POST /api/projects": () => json(project(0, 5), 201),
      "PATCH /api/projects/5": () => json(project(0, 5)),
      "PUT /api/projects/5/folder": () => json(project(0, 5)),
      "POST /api/projects/5/rebuild": () => json({ status: "rebuilding" }, 202),
      "DELETE /api/projects/5": () => json({ chats_deleted: 4 }),
    });
    await mountProbe();
    const loads = () => calls.filter((c) => c.key === "GET /api/projects").length;
    const before = loads();

    await act(async () => {
      await latest!.create("", "D:/new");
      await latest!.rename(5, "Renamed");
      await latest!.changeFolder(5, "D:/other");
      await latest!.rebuild(5);
      expect(await latest!.remove(5)).toBe(4);
    });
    expect(calls.filter((c) => c.key !== "GET /api/projects")).toEqual([
      { key: "POST /api/projects", body: { folder_path: "D:/new" } }, // no name: the folder's
      { key: "PATCH /api/projects/5", body: { name: "Renamed" } },
      { key: "PUT /api/projects/5/folder", body: { folder_path: "D:/other" } },
      { key: "POST /api/projects/5/rebuild", body: undefined },
      { key: "DELETE /api/projects/5", body: undefined },
    ]);
    expect(loads()).toBe(before + 5);
  });
});
