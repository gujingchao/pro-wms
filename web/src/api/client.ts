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
  idempotencyKey?: string
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
  if (options.idempotencyKey) headers['Idempotency-Key'] = options.idempotencyKey

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

export type Snapshot = {
  warehouses: Record<string, string>
  locations: Record<string, string>
  skus: Record<string, unknown>
  inbounds: Record<string, unknown>
  outbounds: Record<string, unknown>
  waves: Record<string, unknown>
  stocktakes: Record<string, unknown>
  stock: unknown[]
  ledger: unknown[]
  source: 'api'
}

/** Failed reads remain errors; never substitute demo stock for server data. */
export async function getSnapshot(baseUrl: string): Promise<Snapshot> {
  const data = await apiRequest<Record<string, unknown>>(baseUrl, '/snapshot')
  return { ...data, source: 'api' } as Snapshot
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
  outboundCancel: (base: string, id: string, user?: string | null) =>
    apiRequest(base, `/outbounds/${encodeURIComponent(id)}/cancel`, { method: 'POST', user }),
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
    apiRequest<{ on_hand: number; reserved: number; available: number; ledger: unknown[] }>(base, '/ledger', {
      method: 'GET',
      query: { sku, warehouse: warehouse || undefined },
    }),
  getSnapshot,
}
