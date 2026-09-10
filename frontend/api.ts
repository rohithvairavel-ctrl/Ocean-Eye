// JSON contracts mirror backend OpenAPI. Scientific outputs carry their provenance.
export type Json = Record<string, any>;
export async function api<T = Json>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch('/api/v1' + path, options);
  if (!response.ok) {
    const data = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail));
  }
  return response.json();
}
export const post = <T = Json>(path: string, data: unknown = {}) =>
  api<T>(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
export const time = (value: string) =>
  new Date(value).toLocaleString('en-GB', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    timeZone: 'UTC',
  }) + ' UTC';
export const coordinate = (point: number[]) => `${point[1].toFixed(3)}°, ${point[0].toFixed(3)}°`;
