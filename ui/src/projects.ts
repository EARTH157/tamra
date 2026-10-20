import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";
import type { Project } from "./types";

/** The longest project name the core accepts. */
export const NAME_MAX = 80;

/** How often the projects are reloaded while a project is indexing, or while the core is unreachable. */
export const FAST_POLL_MS = 2000;
/** How often they are reloaded otherwise, to notice files the folder watchers found. */
export const IDLE_POLL_MS = 10000;

/** True while any project has files waiting to be indexed or being indexed. */
export function anyIndexing(projects: Project[]): boolean {
  return projects.some(({ index }) => index.counts.pending + index.counts.indexing > 0);
}

/**
 * The projects, reloaded on a timer: quickly while one is indexing or the core is unreachable,
 * slowly otherwise. `onReconnect` runs when the core answers again after being unreachable, so the
 * caller can reload what else it shows. The mutations call the core and then reload the projects;
 * a refused call throws its ApiError.
 */
export function useProjects(onReconnect?: () => void) {
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [offline, setOffline] = useState<string | null>(null);
  const [ticks, setTicks] = useState(0); // one per reload, so the timer is set again after each
  const wasOffline = useRef(false);
  const requests = useRef(0); // the newest reload; an older one that answers late is ignored
  const reconnect = useRef(onReconnect);
  useEffect(() => {
    reconnect.current = onReconnect;
  });

  const refresh = useCallback(async () => {
    const mine = ++requests.current;
    try {
      const loaded = (await api<{ projects: Project[] }>("GET", "/api/projects")).projects;
      if (mine !== requests.current) return; // a newer reload (after a change) has the truth
      setProjects(loaded);
      setOffline(null);
      if (wasOffline.current) {
        wasOffline.current = false;
        reconnect.current?.();
      }
    } catch (e) {
      if (mine !== requests.current) return;
      wasOffline.current = true;
      setOffline((e as Error).message);
    } finally {
      setTicks((n) => n + 1);
    }
  }, []);

  const quick = projects === null || offline !== null || anyIndexing(projects);
  useEffect(() => {
    const timer = window.setTimeout(() => void refresh(), quick ? FAST_POLL_MS : IDLE_POLL_MS);
    return () => window.clearTimeout(timer);
  }, [refresh, quick, ticks]);

  // The first load.
  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function create(name: string, folderPath: string): Promise<Project> {
    const project = await api<Project>("POST", "/api/projects", {
      ...(name ? { name } : {}), // no name: the core uses the folder's name
      folder_path: folderPath,
    });
    await refresh();
    return project;
  }

  async function rename(id: number, name: string): Promise<void> {
    await api<Project>("PATCH", `/api/projects/${id}`, { name });
    await refresh();
  }

  async function changeFolder(id: number, folderPath: string): Promise<void> {
    await api<Project>("PUT", `/api/projects/${id}/folder`, { folder_path: folderPath });
    await refresh();
  }

  async function rebuild(id: number): Promise<void> {
    await api("POST", `/api/projects/${id}/rebuild`);
    await refresh();
  }

  /** Delete a project and its chats; resolves to how many chats went with it. */
  async function remove(id: number): Promise<number> {
    const { chats_deleted } = await api<{ chats_deleted: number }>("DELETE", `/api/projects/${id}`);
    await refresh();
    return chats_deleted;
  }

  return { projects, offline, refresh, create, rename, changeFolder, rebuild, remove };
}
