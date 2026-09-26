import { computed, reactive } from 'vue'
import { api, getSnapshot, getStoredBaseUrl, isApiError, setStoredBaseUrl, type ApiError, type Snapshot } from '../api/client'

export type Role = 'operator' | 'supervisor'

export const session = reactive({
  baseUrl: getStoredBaseUrl(),
  user: 'operator' as Role,
  warehouse: 'WH-EAST',
  health: 'unknown' as 'ok' | 'down' | 'unknown' | 'checking',
  lastResult: null as unknown,
  lastError: null as ApiError | null,
  banner: null as null | { kind: 'error' | 'warn' | 'ok' | 'info'; text: string },
  busy: false,
  snapshot: null as Snapshot | null,
  warehouseOptions: ['WH-EAST', 'WH-WEST'] as string[],
})

export const warehouses = computed(() => session.warehouseOptions)
export const roles = ['operator', 'supervisor'] as const

export function setBaseUrl(url: string) {
  session.baseUrl = url.replace(/\/$/, '')
  setStoredBaseUrl(session.baseUrl)
}

export function clearBanner() {
  session.banner = null
}

export function showBanner(kind: 'error' | 'warn' | 'ok' | 'info', text: string) {
  session.banner = { kind, text }
}

export function setResult(data: unknown) {
  session.lastResult = data
  session.lastError = null
}

export async function checkHealth() {
  session.health = 'checking'
  try {
    const h = await api.health(session.baseUrl)
    session.health = h.status === 'ok' ? 'ok' : 'down'
  } catch {
    session.health = 'down'
  }
}

/** Pull GET /snapshot and refresh warehouse switcher + cached snapshot. */
export async function refreshSnapshot(): Promise<Snapshot | null> {
  let snap: Snapshot
  try {
    snap = await getSnapshot(session.baseUrl)
  } catch {
    session.snapshot = null
    showBanner('warn', '库存数据加载失败，请检查连接后重试。')
    return null
  }
  session.snapshot = snap
  const wh = snap.warehouses
  if (wh && typeof wh === 'object') {
    const keys = Object.keys(wh as Record<string, unknown>)
    if (keys.length) {
      session.warehouseOptions = keys
      if (!keys.includes(session.warehouse)) {
        session.warehouse = keys[0]
      }
    }
  }
  return snap
}

/**
 * Run an API action: stores JSON result, surfaces 403/409 as banner/toast.
 * On success, best-effort refresh snapshot for list UIs.
 */
export async function runAction<T>(label: string, fn: () => Promise<T>): Promise<T | null> {
  session.busy = true
  session.lastError = null
  clearBanner()
  try {
    const data = await fn()
    setResult(data)
    showBanner('ok', `${label} 成功`)
    void refreshSnapshot()
    return data
  } catch (e) {
    if (isApiError(e)) {
      session.lastError = e
      session.lastResult = e.body
      const code = e.status
      const detail = e.detail || e.error || '请求失败'
      if (code === 403) {
        showBanner('error', `403 权限不足：${detail}`)
      } else if (code === 409) {
        showBanner('warn', `409 冲突：${detail}`)
      } else {
        showBanner('error', `${code}：${detail}`)
      }
    } else {
      const msg = e instanceof Error ? e.message : String(e)
      showBanner('error', `网络错误：${msg}`)
      session.lastResult = { error: msg }
    }
    return null
  } finally {
    session.busy = false
  }
}

export const healthLabel = computed(() => {
  switch (session.health) {
    case 'ok':
      return '已连接'
    case 'down':
      return '不可达'
    case 'checking':
      return '检测中…'
    default:
      return '未知'
  }
})
