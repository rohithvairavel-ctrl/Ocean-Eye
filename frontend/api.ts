// JSON contracts mirror backend OpenAPI. Scientific outputs carry their provenance.
export type Json = Record<string, any>;
const API_BASE = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '');

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

export async function api<T = Json>(path: string, options: RequestInit = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch(API_BASE + '/api/v1' + path, options);
  } catch {
    throw new ApiError(
      "OCEAN-EYE's local service is not responding. Start it with start.ps1 (or npm run dev), then retry — saved evidence is unaffected.",
      0,
    );
  }
  if (!response.ok) {
    const data = await response.json().catch(() => ({
      detail:
        response.status >= 500
          ? 'The analysis service hit an internal error. Saved evidence is unaffected; retrying is safe.'
          : response.statusText,
    }));
    throw new ApiError(
      typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail),
      response.status,
    );
  }
  return response.json();
}

export const post = <T = Json>(path: string, data: unknown = {}) =>
  api<T>(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });

export const patch = <T = Json>(path: string, data: unknown = {}) =>
  api<T>(path, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });

export const del = <T = Json>(path: string) => api<T>(path, { method: 'DELETE' });

export const time = (value: string) =>
  new Date(value).toLocaleString('en-GB', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    timeZone: 'UTC',
  }) + ' UTC';

export const shortTime = (value: string) =>
  new Date(value).toLocaleString('en-GB', {
    day: '2-digit',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
    timeZone: 'UTC',
  }) + ' UTC';

export const coordinate = (point: number[]) =>
  `${Math.abs(point[1]).toFixed(3)}°${point[1] >= 0 ? 'N' : 'S'}, ${Math.abs(point[0]).toFixed(3)}°${point[0] >= 0 ? 'E' : 'W'}`;

/** "07h 18m", "3d 04h", "42s" — compact durations for data-age clocks. */
export function duration(seconds: number | null | undefined) {
  if (seconds === null || seconds === undefined || !isFinite(seconds)) return '—';
  const s = Math.max(0, Math.floor(seconds));
  const d = Math.floor(s / 86400),
    h = Math.floor((s % 86400) / 3600),
    m = Math.floor((s % 3600) / 60),
    sec = s % 60;
  const pad = (n: number) => String(n).padStart(2, '0');
  if (d > 0) return `${d}d ${pad(h)}h`;
  if (h > 0) return `${pad(h)}h ${pad(m)}m`;
  if (m > 0) return `${m}m ${pad(sec)}s`;
  return `${sec}s`;
}

/** "00:08:42" countdown text. */
export function clock(seconds: number) {
  const s = Math.max(0, Math.floor(seconds));
  const pad = (n: number) => String(n).padStart(2, '0');
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const body = `${pad(h)}:${pad(Math.floor((s % 3600) / 60))}:${pad(s % 60)}`;
  return d > 0 ? `${d}d ${body}` : body;
}

export const label = (value: string | null | undefined) =>
  (value || '').replaceAll('_', ' ');

/** Sentence case for backend labels, keeping acronyms intact. */
export const sentence = (value: string) =>
  value
    .toLowerCase()
    .replace(/^\w/, (c) => c.toUpperCase())
    .replace(/\b(ais|sar|aoi|mmsi|utc)\b/gi, (m) => m.toUpperCase());

/** First potential-exposure time; runs from older builds stored only the hour offset. */
export const overlapTime = (a: Json, r: Json): string | null =>
  r.first_overlap_time ||
  (r.first_overlap_h !== null && r.first_overlap_h !== undefined
    ? new Date(Date.parse(a.observation_time) + r.first_overlap_h * 3600000).toISOString()
    : null);
