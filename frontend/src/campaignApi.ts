import { request } from './api'
import type { Campaign, CampaignComparison, PlanValidation, RunComparison, TestPlan } from './types'

export const campaignApi = {
  validate: (plan: unknown) => request<PlanValidation>('/plans/validate', { method: 'POST', body: JSON.stringify(plan) }),
  list: () => request<{ campaigns: Campaign[] }>('/campaigns'),
  get: (id: string) => request<Campaign>(`/campaigns/${encodeURIComponent(id)}`),
  create: (name: string, scenarios: string[], seeds: number[], plan: TestPlan | null) => request<Campaign>('/campaigns', { method: 'POST', body: JSON.stringify({ name, scenarios, seeds, plan }) }),
  pause: (id: string) => request<Campaign>(`/campaigns/${encodeURIComponent(id)}/pause`, { method: 'POST' }),
  resume: (id: string) => request<Campaign>(`/campaigns/${encodeURIComponent(id)}/resume`, { method: 'POST' }),
  compare: (left: string, right: string) => request<RunComparison>(`/compare?left=${encodeURIComponent(left)}&right=${encodeURIComponent(right)}`),
  compareCampaigns: (left: string, right: string) => request<CampaignComparison>(`/campaign-compare?left=${encodeURIComponent(left)}&right=${encodeURIComponent(right)}`),
}
