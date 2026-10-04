import { act, type ReactElement } from "react";
import { createRoot } from "react-dom/client";
import { vi } from "vitest";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

export type Mounted = { container: HTMLElement; unmount: () => Promise<void> };

/** Render into a fresh container and let effects and mocked requests settle. */
export async function mount(element: ReactElement): Promise<Mounted> {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  await act(async () => {
    root.render(element);
  });
  await settle();
  return {
    container,
    unmount: async () => {
      await act(async () => {
        root.unmount();
      });
      container.remove();
    },
  };
}

/** Let pending promises (mocked fetches, streamed bodies) and the renders they cause finish. */
export async function settle(): Promise<void> {
  for (let i = 0; i < 10; i++) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }
}

export async function click(element: Element | null | undefined): Promise<void> {
  if (!(element instanceof HTMLElement)) throw new Error("nothing to click");
  await act(async () => {
    element.click();
  });
  await settle();
}

/** Type into a React-controlled input or textarea. */
export async function typeInto(element: Element | null | undefined, value: string): Promise<void> {
  if (!(element instanceof HTMLInputElement || element instanceof HTMLTextAreaElement)) {
    throw new Error("not a text field");
  }
  const setter = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(element), "value")?.set;
  await act(async () => {
    setter?.call(element, value);
    element.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

export function json(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

export function sse(events: unknown[]): Response {
  const body = events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join("");
  return new Response(body, { headers: { "Content-Type": "text/event-stream" } });
}

export type Call = { key: string; body: unknown };

/** Replace fetch with routes keyed by "METHOD /path"; an unknown route fails the test. */
export function mockFetch(routes: Record<string, () => Response>): Call[] {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const key = `${init?.method ?? "GET"} ${String(input)}`;
      calls.push({ key, body: init?.body ? JSON.parse(String(init.body)) : undefined });
      const route = routes[key];
      if (!route) throw new Error(`unexpected request: ${key}`);
      return route();
    }),
  );
  return calls;
}

/** Press a key on an element (keydown, bubbling, as React listens for it). */
export async function press(element: Element | null | undefined, key: string): Promise<void> {
  if (!(element instanceof HTMLElement)) throw new Error("nothing to press a key on");
  await act(async () => {
    element.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true }));
  });
  await settle();
}

/** The first button (or menu item) inside root whose visible text is exactly text. */
export function buttonByText(root: ParentNode, text: string): HTMLButtonElement | undefined {
  return [...root.querySelectorAll("button")].find((b) => b.textContent?.trim() === text);
}
