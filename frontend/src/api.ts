import type { Catalog, Experiment, RunDetail, RunSummary } from './types'

export class ApiError extends Error {
  constructor(message: string, public status: number) { super(message); this.name = 'ApiError' }
}

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const controller = new AbortController()
  const timeout = window.setTimeout(() => controller.abort(), 15000)
  try {
    const response = await fetch(`/api${path}`, { ...init, signal: controller.signal, headers: { 'Content-Type': 'application/json', ...init?.headers } })
    const body: unknown = await response.json().catch(() => null)
    if (!response.ok) {
      const detail = body && typeof body === 'object' && 'detail' in body ? (body as { detail: unknown }).detail : null
      const validation = Array.isArray(detail) ? detail.map(item => item && typeof item === 'object' ? `${Array.isArray(item.loc) ? item.loc.filter((part: unknown) => part !== 'body').join('.') : 'Plan'}: ${String(item.msg ?? 'Invalid value')}` : String(item)).join('; ') : null
      const message = typeof detail === 'string' ? detail : validation || (response.status === 409 ? 'The local worker is busy. Wait for the current run, campaign or experiment to finish.' : `Request failed (${response.status}). Please try again.`)
      throw new ApiError(message, response.status)
    }
    return body as T
  } catch (error) {
    if (error instanceof ApiError) throw error
    throw new ApiError(error instanceof DOMException && error.name === 'AbortError' ? 'The local API did not respond within 15 seconds. Check that the server is running.' : 'Cannot reach the local API. Start the Device Test Lab server, then reconnect.', 0)
  } finally { window.clearTimeout(timeout) }
}

export const api = {
  health: () => request<{ status: string; version: string }>('/health'),
  catalog: () => request<Catalog>('/catalog'),
  runs: () => request<{ runs: RunSummary[] }>('/runs'),
  run: (id: string) => request<RunDetail>(`/runs/${encodeURIComponent(id)}`),
  startRun: (scenario: string, strategy: string, seed: number) => request<RunDetail>('/runs', { method: 'POST', body: JSON.stringify({ scenario, strategy, seed }) }),
  experiment: () => request<Experiment>('/experiment'),
  train: () => request<{ state: 'running' }>('/experiment', { method: 'POST' }),
}
