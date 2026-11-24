import { request } from './client'
import { moduleMeta } from '@/data/modules'
import type { ActionResult, EntryRow, OverviewResult, PageResult } from '@/data/types'

export { moduleMeta }

export async function listEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<PageResult> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}?${query}`)
  if (!response.ok) throw new Error('业务数据读取失败')
  return await response.json()
}

export async function createEntry(
  key: string,
  values: Record<string, string>,
): Promise<ActionResult> {
  const response = await request(`/api/${key}`, {
    method: 'POST',
    body: JSON.stringify({ values }),
  })
  return await response.json()
}

export async function runAction(
  key: string,
  id: number,
  action: string,
): Promise<ActionResult> {
  const response = await request(`/api/${key}/${id}/actions`, {
    method: 'POST',
    body: JSON.stringify({ action }),
  })
  return await response.json()
}

export async function downloadEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<void> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}/export?${query}`)
  if (!response.ok) throw new Error('业务数据导出失败')
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `${key}.csv`
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(url)
}

export async function loadOverview(): Promise<OverviewResult> {
  const response = await request('/api/overview')
  if (!response.ok) throw new Error('运营概览读取失败')
  return await response.json()
}

// ---- 接入监控专用接口 ----

export type ShiftBoard = {
  point: string
  generated_at: string
  shifts: Array<{
    shift_id: string
    班次: string
    规则版本: string
    采样频率Hz: number
    起始时刻: string
    截止时刻: string
    应收点数: number
    实收点数: number
    连续区间: Array<{ start: string; end: string; points: number }>
    边缘等待时刻: string[]
    确认缺口时刻: string[]
    仍缺时刻: string[]
    安全接收窗口截止: string
    在途补采申请: string | null
    下一次领取资格: { eligible: boolean; reason: string; blocking_dispatch?: number[] }
  }>
}

export type CoverageReport = {
  point: string
  rule_versions: string[]
  generated_at: string
  segments: Array<{
    班次: string
    规则版本: string
    采样间隔秒: number
    应收点数: number
    实收点数: number
    仍缺点数: number
    边缘等待区间: Array<{ start: string; end: string; points: number }>
    确认缺口区间: Array<{ start: string; end: string; points: number }>
    安全接收窗口截止: string
  }>
}

export type CaptureRequest = {
  id: number
  申请编号: string
  point: string
  shift: string
  status: string
  opened_at: string
  issued_at: string | null
  缺口快照: string[]
  派发范围: string[]
  已核扣减: string[]
  dispatch_no: number | null
  领取人: string | null
  claimed_at: string | null
}

export type DispatchLedger = {
  dispatch_no: number
  request_id: number
  申请编号: string
  point: string
  shift: string
  issued_at: string
  打开时缺口快照: string[]
  实际派发范围: string[]
  发出前已核扣减: string[]
  claimed: boolean
  claimant: string | null
  claimed_at: string | null
}

async function postJson<T>(path: string, body: Record<string, unknown> = {}): Promise<T> {
  const response = await request(path, { method: 'POST', body: JSON.stringify(body) })
  if (!response.ok) throw new Error('接入监控操作失败')
  return await response.json()
}

export async function fetchShiftBoard(): Promise<ShiftBoard> {
  const response = await request('/api/ingest_monitor/board')
  if (!response.ok) throw new Error('班次看板读取失败')
  return await response.json()
}

export async function fetchCoverageReport(): Promise<CoverageReport> {
  const response = await request('/api/ingest_monitor/coverage')
  if (!response.ok) throw new Error('采样覆盖对照读取失败')
  return await response.json()
}

export async function fetchCaptureRequests(): Promise<{ items: CaptureRequest[] }> {
  const response = await request('/api/ingest_monitor/requests')
  if (!response.ok) throw new Error('补采申请读取失败')
  return await response.json()
}

export async function fetchDispatchLedger(): Promise<{ items: DispatchLedger[] }> {
  const response = await request('/api/ingest_monitor/ledger')
  if (!response.ok) throw new Error('派发台账读取失败')
  return await response.json()
}

export async function openCaptureRequest(shift: string): Promise<{ ok: boolean; message: string }> {
  return postJson('/api/ingest_monitor/requests/open', { shift })
}

export async function issueCaptureRequest(requestId: number): Promise<{ ok: boolean; message: string }> {
  return postJson(`/api/ingest_monitor/requests/${requestId}/issue`)
}

export async function claimCaptureRequest(requestId: number): Promise<{ ok: boolean; message: string }> {
  return postJson(`/api/ingest_monitor/requests/${requestId}/claim`, { claimant: '值班员' })
}

export async function ingestReceipt(timestamp: string): Promise<{ ok: boolean; message: string; reason?: string }> {
  return postJson('/api/ingest_monitor/receipts', { timestamp, source: 'edge-late' })
}

export async function resetMonitor(): Promise<{ ok: boolean; message: string }> {
  return postJson('/api/ingest_monitor/reset')
}
