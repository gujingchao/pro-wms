/**
 * pro-wms HTTP client — talks to FastAPI at configured base URL.
 * Sends X-User on mutating calls when a role is selected.
 */

export type ApiError = {
  status: number
  error?: string
  detail?: string
  body: unknown
}

export type RequestOptions = {
  method?: string
  body?: unknown
  user?: string | null
  query?: Record<string, string | number | undefined | null>
}

const STORAGE_KEY = 'pro-wms-api-base'

export function getStoredBaseUrl(): string {
  try {
    return localStorage.getItem(STORAGE_KEY) || 'http://127.0.0.1:8080'
  } catch {
    return 'http://127.0.0.1:8080'
  }
}

export function setStoredBaseUrl(url: string): void {
  try {
    localStorage.setItem(STORAGE_KEY, url.replace(/\/$/, ''))
  } catch {
    /* ignore */
  }
}

function buildUrl(base: string, path: string, query?: RequestOptions['query']): string {
  const root = base.replace(/\/$/, '')
  const u = new URL(path.startsWith('http') ? path : `${root}${path.startsWith('/') ? path : `/${path}`}`)
  if (query) {
    for (const [k, v] of Object.entries(query)) {
      if (v !== undefined && v !== null && v !== '') u.searchParams.set(k, String(v))
    }
  }
  return u.toString()
}

export async function apiRequest<T = unknown>(
  baseUrl: string,
  path: string,
  options: RequestOptions = {},
): Promise<T> {
  const headers: Record<string, string> = {
    Accept: 'application/json',
  }
  if (options.body !== undefined) {
    headers['Content-Type'] = 'application/json'
  }
  if (options.user) {
    headers['X-User'] = options.user
  }

  const res = await fetch(buildUrl(baseUrl, path, options.query), {
    method: options.method || (options.body !== undefined ? 'POST' : 'GET'),
    headers,
    body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
  })

  let parsed: unknown = null
  const text = await res.text()
  if (text) {
    try {
      parsed = JSON.parse(text)
    } catch {
      parsed = { raw: text }
    }
  }

  if (!res.ok) {
    const errBody = (parsed && typeof parsed === 'object' ? parsed : {}) as {
      error?: string
      detail?: string
    }
    const err: ApiError = {
      status: res.status,
      error: errBody.error,
      detail: errBody.detail ?? (typeof parsed === 'string' ? parsed : res.statusText),
      body: parsed,
    }
    throw err
  }

  return parsed as T
}

export function isApiError(e: unknown): e is ApiError {
  return (
    typeof e === 'object' &&
    e !== null &&
    'status' in e &&
    typeof (e as ApiError).status === 'number' &&
    'body' in e
  )
}

/** Demo / local fallback when GET /snapshot is not available. */
export const DEMO_SNAPSHOT = {
  warehouses: { 'WH-EAST': 'East DC', 'WH-WEST': 'West DC' },
  locations: {
    'EAST-DOCK': 'WH-EAST',
    'EAST-A-01-01': 'WH-EAST',
    'EAST-A-01-02': 'WH-EAST',
    'WEST-A-01-01': 'WH-WEST',
  },
  skus: {
    'SKU-MILK': { name: 'Fresh milk', shelf_life_days: 14 },
    'SKU-BOLT': { name: 'M8 bolt', shelf_life_days: null },
  },
  inbounds: { 'IN-1001': { id: 'IN-1001', status: 'draft', warehouse: 'WH-EAST' } },
  outbounds: { 'OUT-2001': { id: 'OUT-2001', status: 'draft', warehouse: 'WH-EAST' } },
  waves: {} as Record<string, unknown>,
  stocktakes: { 'ST-3001': { id: 'ST-3001', status: 'submitted', warehouse: 'WH-EAST' } },
  stock: [] as unknown[],
  ledger: [] as unknown[],
  source: 'local-demo' as 'local-demo' | 'api',
}

/**
 * Live kernel snapshot via GET /snapshot (kernel.to_dict()).
 * Falls back to DEMO_SNAPSHOT on 404/network failure so the shell still renders.
 */
export type Snapshot = typeof DEMO_SNAPSHOT & Record<string, unknown>

export async function getSnapshot(baseUrl: string): Promise<Snapshot> {
  try {
    const data = await apiRequest<Record<string, unknown>>(baseUrl, '/snapshot')
    return { ...DEMO_SNAPSHOT, ...data, source: 'api' }
  } catch (e) {
    if (isApiError(e) && (e.status === 404 || e.status === 405)) {
      return { ...DEMO_SNAPSHOT, source: 'local-demo' }
    }
    // Network / other: still fall back so shell can render
    return { ...DEMO_SNAPSHOT, source: 'local-demo' }
  }
}

export const api = {
  health: (base: string) => apiRequest<{ status: string }>(base, '/health'),
  seedDemo: (base: string, user?: string | null) =>
    apiRequest<{ warehouses: string[]; inbounds: string[]; outbounds: string[] }>(base, '/seed/demo', {
      method: 'POST',
      user,
    }),
  inboundReceive: (base: string, id: string, user?: string | null) =>
    apiRequest(base, `/inbounds/${encodeURIComponent(id)}/receive`, { method: 'POST', user }),
  inboundPutaway: (base: string, id: string, to: string, user?: string | null) =>
    apiRequest(base, `/inbounds/${encodeURIComponent(id)}/putaway`, {
      method: 'POST',
      body: { to },
      user,
    }),
  outboundAllocate: (base: string, id: string, strategy: 'fifo' | 'fefo', user?: string | null) =>
    apiRequest(base, `/outbounds/${encodeURIComponent(id)}/allocate`, {
      method: 'POST',
      body: { strategy },
      user,
    }),
  waveCreate: (base: string, outbounds: string[], user?: string | null) =>
    apiRequest(base, '/waves', { method: 'POST', body: { outbounds }, user }),
  wavePick: (base: string, id: string, user?: string | null) =>
    apiRequest(base, `/waves/${encodeURIComponent(id)}/pick`, { method: 'POST', user }),
  stocktakeApprove: (base: string, id: string, user?: string | null) =>
    apiRequest(base, `/stocktakes/${encodeURIComponent(id)}/approve`, { method: 'POST', user }),
  race: (
    base: string,
    body: { sku: string; warehouse: string; workers: number },
    user?: string | null,
  ) => apiRequest(base, '/race', { method: 'POST', body, user }),
  ledger: (base: string, sku: string, warehouse?: string | null) =>
    apiRequest<{ on_hand: number; ledger: unknown[] }>(base, '/ledger', {
      method: 'GET',
      query: { sku, warehouse: warehouse || undefined },
    }),
  getSnapshot,
}
