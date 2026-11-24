<template>
  <section class="page" data-module="ingest_monitor">
    <header class="page-head">
      <div>
        <h2>接入监控</h2>
        <p class="page-desc">
          按班次核对落盘范围：关联两班实收与未齐区段，派发补采时同步核算仍缺观测与已核实收，
          已确认点不重复计入，打开时缺口快照随派发台账留存。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn" type="button" data-testid="reload-monitor" @click="reloadAll">刷新核对</button>
        <button class="btn ghost" type="button" data-testid="reset-monitor" @click="onReset">重置场景</button>
      </div>
    </header>

    <p v-if="message" class="status-legend" data-testid="monitor-message">
      <span class="legend-item">{{ message }}</span>
    </p>

    <!-- 班次看板 -->
    <h3 class="section-title">班次看板：实际观测 / 应收数与仍缺前后两段</h3>
    <div class="stat-row">
      <article v-for="item in summaryCards" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>
    <table class="data-table" data-testid="shift-board-table">
      <thead>
        <tr>
          <th>班次</th>
          <th>规则版本</th>
          <th>采样频率</th>
          <th>应收点数</th>
          <th>实收点数</th>
          <th>已接收连续区间</th>
          <th>边缘等待时刻（安全窗口内）</th>
          <th>确认缺口时刻（窗口外待补）</th>
          <th>下一次领取资格</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in board?.shifts ?? []" :key="row.shift_id" :data-shift="row.班次">
          <td>{{ row.班次 }}班</td>
          <td>{{ row.规则版本 }}</td>
          <td>{{ row.采样频率Hz }} Hz</td>
          <td>{{ row.应收点数 }}</td>
          <td>{{ row.实收点数 }}</td>
          <td>
            <span v-for="(interval, idx) in row.连续区间" :key="idx" class="range-chip">
              {{ shortTime(interval.start) }}~{{ shortTime(interval.end) }}（{{ interval.points }}点）
            </span>
            <span v-if="!row.连续区间.length" class="empty-state">—</span>
          </td>
          <td>
            <span v-for="ts in row.边缘等待时刻" :key="ts" class="range-chip waiting">{{ shortTime(ts) }}</span>
            <span v-if="!row.边缘等待时刻.length" class="empty-state">无</span>
          </td>
          <td>
            <span v-for="ts in row.确认缺口时刻" :key="ts" class="range-chip gap">{{ shortTime(ts) }}</span>
            <span v-if="!row.确认缺口时刻.length" class="empty-state">无</span>
          </td>
          <td>
            <span :class="['eligibility', row.下一次领取资格.eligible ? 'ok' : 'blocked']">
              {{ row.下一次领取资格.eligible ? '可领取' : '不可重复领取' }}
            </span>
            <small v-if="row.下一次领取资格.blocking_dispatch?.length">
              （被派发 #{{ row.下一次领取资格.blocking_dispatch.join(', #') }} 占用）
            </small>
          </td>
          <td class="row-actions">
            <button
              class="link"
              type="button"
              :data-testid="`open-request-${row.班次}`"
              @click="onOpenRequest(row.班次)"
            >
              打开补采申请
            </button>
          </td>
        </tr>
      </tbody>
    </table>

    <!-- 采样覆盖对照 -->
    <h3 class="section-title">采样覆盖对照（按规则版本）</h3>
    <table class="data-table" data-testid="coverage-table">
      <thead>
        <tr>
          <th>班次</th>
          <th>规则版本</th>
          <th>采样间隔</th>
          <th>应收点数</th>
          <th>实收点数</th>
          <th>仍缺点数</th>
          <th>边缘等待区间</th>
          <th>确认缺口区间</th>
          <th>安全接收窗口截止</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="seg in coverage?.segments ?? []" :key="seg.班次">
          <td>{{ seg.班次 }}班</td>
          <td>{{ seg.规则版本 }}</td>
          <td>{{ seg.采样间隔秒 }}s</td>
          <td>{{ seg.应收点数 }}</td>
          <td>{{ seg.实收点数 }}</td>
          <td>{{ seg.仍缺点数 }}</td>
          <td>
            <span v-for="(interval, idx) in seg.边缘等待区间" :key="idx" class="range-chip waiting">
              {{ shortTime(interval.start) }}~{{ shortTime(interval.end) }}（{{ interval.points }}点）
            </span>
            <span v-if="!seg.边缘等待区间.length" class="empty-state">无</span>
          </td>
          <td>
            <span v-for="(interval, idx) in seg.确认缺口区间" :key="idx" class="range-chip gap">
              {{ shortTime(interval.start) }}~{{ shortTime(interval.end) }}（{{ interval.points }}点）
            </span>
            <span v-if="!seg.确认缺口区间.length" class="empty-state">无</span>
          </td>
          <td>{{ shortTime(seg.安全接收窗口截止) }}</td>
        </tr>
      </tbody>
    </table>

    <!-- 补采申请与实收核对 -->
    <h3 class="section-title">补采申请（缺口快照 / 派发扣减 / 领取）</h3>
    <table class="data-table" data-testid="requests-table">
      <thead>
        <tr>
          <th>申请编号</th>
          <th>班次</th>
          <th>状态</th>
          <th>打开时缺口快照</th>
          <th>实际派发范围</th>
          <th>发出前已核扣减</th>
          <th>领取人</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="req in requests" :key="req.id" :data-status="req.status">
          <td>{{ req.申请编号 }}</td>
          <td>{{ req.shift }}班</td>
          <td>{{ req.status }}</td>
          <td>
            <span v-for="ts in req.缺口快照" :key="ts" class="range-chip">{{ shortTime(ts) }}</span>
          </td>
          <td>
            <span v-for="ts in req.派发范围" :key="ts" class="range-chip gap">{{ shortTime(ts) }}</span>
            <span v-if="!req.派发范围.length" class="empty-state">—</span>
          </td>
          <td>
            <span v-for="ts in req.已核扣减" :key="ts" class="range-chip received">{{ shortTime(ts) }}</span>
            <span v-if="!req.已核扣减.length" class="empty-state">—</span>
          </td>
          <td>{{ req.领取人 ?? '未领取' }}</td>
          <td class="row-actions">
            <button
              v-if="req.status === '待派发'"
              class="link"
              type="button"
              :data-testid="`issue-${req.id}`"
              @click="onIssue(req.id)"
            >
              发出捕获范围
            </button>
            <button
              v-if="req.status === '已派发' && !req.领取人"
              class="link"
              type="button"
              :data-testid="`claim-${req.id}`"
              @click="onClaim(req.id)"
            >
              领取
            </button>
          </td>
        </tr>
        <tr v-if="!requests.length">
          <td colspan="8" class="empty-state">暂无补采申请，可在班次看板对有缺口的班次「打开补采申请」</td>
        </tr>
      </tbody>
    </table>

    <!-- 派发台账 -->
    <h3 class="section-title">派发台账（留存当时所依据的缺口）</h3>
    <table class="data-table" data-testid="ledger-table">
      <thead>
        <tr>
          <th>派发号</th>
          <th>申请编号</th>
          <th>班次</th>
          <th>打开时缺口快照</th>
          <th>实际派发范围</th>
          <th>发出前已核扣减</th>
          <th>领取状态</th>
          <th>领取人</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="item in ledger" :key="item.dispatch_no">
          <td>#{{ item.dispatch_no }}</td>
          <td>{{ item.申请编号 }}</td>
          <td>{{ item.shift }}班</td>
          <td>
            <span v-for="ts in item.打开时缺口快照" :key="ts" class="range-chip">{{ shortTime(ts) }}</span>
          </td>
          <td>
            <span v-for="ts in item.实际派发范围" :key="ts" class="range-chip gap">{{ shortTime(ts) }}</span>
          </td>
          <td>
            <span v-for="ts in item.发出前已核扣减" :key="ts" class="range-chip received">{{ shortTime(ts) }}</span>
            <span v-if="!item.发出前已核扣减.length" class="empty-state">无</span>
          </td>
          <td>{{ item.claimed ? '已领取' : '待领取' }}</td>
          <td>{{ item.claimant ?? '—' }}</td>
        </tr>
        <tr v-if="!ledger.length">
          <td colspan="8" class="empty-state">暂无派发台账</td>
        </tr>
      </tbody>
    </table>

    <!-- 迟到旧包独立实收核对 -->
    <h3 class="section-title">迟到旧包实收核对（独立核对，重复自动去重）</h3>
    <form class="filter-bar" @submit.prevent="onIngest">
      <label class="filter-item">
        <span>观测时刻（取确认缺口时刻原样填入）</span>
        <input v-model="receiptTimestamp" data-testid="receipt-timestamp" placeholder="YYYY-MM-DDTHH:mm:ss.sss" />
      </label>
      <button class="btn primary" data-testid="submit-receipt" type="submit">提交实收核对</button>
    </form>

    <footer class="page-foot">
      <span>看板生成于 {{ board?.generated_at ?? '—' }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import {
  claimCaptureRequest,
  fetchCaptureRequests,
  fetchCoverageReport,
  fetchDispatchLedger,
  fetchShiftBoard,
  ingestReceipt,
  issueCaptureRequest,
  openCaptureRequest,
  resetMonitor,
  type CaptureRequest,
  type CoverageReport,
  type DispatchLedger,
  type ShiftBoard,
} from '@/api/backend-service'

const board = ref<ShiftBoard | null>(null)
const coverage = ref<CoverageReport | null>(null)
const requests = ref<CaptureRequest[]>([])
const ledger = ref<DispatchLedger[]>([])
const message = ref('')
const receiptTimestamp = ref('')

const summaryCards = computed(() => {
  const shifts = board.value?.shifts ?? []
  const expected = shifts.reduce((sum, item) => sum + item.应收点数, 0)
  const received = shifts.reduce((sum, item) => sum + item.实收点数, 0)
  const waiting = shifts.reduce((sum, item) => sum + item.边缘等待时刻.length, 0)
  const gaps = shifts.reduce((sum, item) => sum + item.确认缺口时刻.length, 0)
  return [
    { label: '应收点合计', value: expected },
    { label: '实收点合计', value: received },
    { label: '边缘等待点', value: waiting },
    { label: '确认缺口点', value: gaps },
  ]
})

function shortTime(ts: string): string {
  return ts.length >= 23 ? ts.slice(11, 23) : ts.slice(11)
}

async function reloadAll() {
  try {
    const [boardData, coverageData, requestData, ledgerData] = await Promise.all([
      fetchShiftBoard(),
      fetchCoverageReport(),
      fetchCaptureRequests(),
      fetchDispatchLedger(),
    ])
    board.value = boardData
    coverage.value = coverageData
    requests.value = requestData.items
    ledger.value = ledgerData.items
  } catch (error) {
    message.value = error instanceof Error ? error.message : '接入监控数据读取失败'
  }
}

async function runAction(action: () => Promise<{ ok: boolean; message: string }>) {
  message.value = ''
  try {
    const result = await action()
    message.value = result.message
    await reloadAll()
  } catch (error) {
    message.value = error instanceof Error ? error.message : '操作失败'
  }
}

function onOpenRequest(shift: string) {
  return runAction(() => openCaptureRequest(shift))
}

function onIssue(requestId: number) {
  return runAction(() => issueCaptureRequest(requestId))
}

function onClaim(requestId: number) {
  return runAction(() => claimCaptureRequest(requestId))
}

async function onIngest() {
  if (!receiptTimestamp.value.trim()) {
    message.value = '请先填入观测时刻'
    return
  }
  await runAction(async () => {
    const result = await ingestReceipt(receiptTimestamp.value.trim())
    return { ok: result.ok, message: result.reason ?? result.message }
  })
  receiptTimestamp.value = ''
}

async function onReset() {
  await runAction(() => resetMonitor())
}

onMounted(reloadAll)
</script>

<style scoped>
.section-title {
  margin: 24px 0 8px;
  font-size: 16px;
}
.range-chip {
  display: inline-block;
  margin: 2px 4px 2px 0;
  padding: 2px 8px;
  border-radius: 4px;
  background: #eef2ff;
  font-size: 12px;
  white-space: nowrap;
}
.range-chip.waiting {
  background: #fff7e6;
}
.range-chip.gap {
  background: #fff1f0;
  color: #cf1322;
}
.range-chip.received {
  background: #f6ffed;
  color: #389e0d;
}
.eligibility {
  font-weight: 600;
}
.eligibility.ok {
  color: #389e0d;
}
.eligibility.blocked {
  color: #cf1322;
}
</style>
