export function tokenFromHash(hash: string): string | null {
  const match = /(?:^#|&)token=([^&]+)/.exec(hash);
  return match ? decodeURIComponent(match[1]) : null;
}

export async function apiGet<T>(path: string, fetchImpl: typeof fetch = fetch): Promise<T> {
  const token = tokenFromHash(window.location.hash) ?? "";
  const response = await fetchImpl(path, { headers: { "X-Tamra-Token": token } });
  if (!response.ok) throw new Error(`${path}: HTTP ${response.status}`);
  return (await response.json()) as T;
}
