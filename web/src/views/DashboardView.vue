<script setup lang="ts">
import { computed, ref } from 'vue'
import { api } from '../api/client'
import { session, runAction, refreshSnapshot } from '../stores/session'

const seedSummary = ref<Record<string, unknown> | null>(null)
const snapshotNote = ref('')

const inboundList = computed(() => {
  const ib = session.snapshot?.inbounds
  if (!ib || typeof ib !== 'object') return [] as { id: string; status: string; warehouse: string }[]
  return Object.entries(ib as Record<string, Record<string, unknown>>).map(([id, v]) => ({
    id,
    status: String(v.status ?? ''),
    warehouse: String(v.warehouse ?? ''),
  }))
})

const outboundList = computed(() => {
  const ob = session.snapshot?.outbounds
  if (!ob || typeof ob !== 'object') return [] as { id: string; status: string; warehouse: string }[]
  return Object.entries(ob as Record<string, Record<string, unknown>>).map(([id, v]) => ({
    id,
    status: String(v.status ?? ''),
    warehouse: String(v.warehouse ?? ''),
  }))
})

const waveList = computed(() => {
  const wv = session.snapshot?.waves
  if (!wv || typeof wv !== 'object') return [] as { id: string; status: string }[]
  return Object.entries(wv as Record<string, Record<string, unknown>>).map(([id, v]) => ({
    id,
    status: String(v.status ?? ''),
  }))
})

const stocktakeList = computed(() => {
  const st = session.snapshot?.stocktakes
  if (!st || typeof st !== 'object') return [] as { id: string; status: string; warehouse: string }[]
  return Object.entries(st as Record<string, Record<string, unknown>>).map(([id, v]) => ({
    id,
    status: String(v.status ?? ''),
    warehouse: String(v.warehouse ?? ''),
  }))
})

async function seed() {
  const data = await runAction('Seed demo', () => api.seedDemo(session.baseUrl, session.user))
  if (data) seedSummary.value = data as Record<string, unknown>
}

async function trySnapshot() {
  snapshotNote.value = '请求 GET /snapshot…'
  const snap = await refreshSnapshot()
  if (!snap) {
    snapshotNote.value = '加载失败，当前没有可显示的库存数据。'
    return
  }
  session.lastResult = snap
  snapshotNote.value = `已加载 snapshot（仓 ${session.warehouseOptions.join(', ')}）。`
}
</script>

<template>
  <div class="page">
    <p class="lede">
      一键加载多仓演示数据（批次效期、入库 IN-1001、出库 OUT-2001、盘点 ST-3001）。之后可在各模块走状态机；列表来自 GET /snapshot。
    </p>

    <div class="card-grid">
      <div class="card">
        <h2>种子 / Seed</h2>
        <p class="muted">POST /seed/demo</p>
        <button type="button" class="btn btn--primary" :disabled="session.busy" @click="seed">
          一键 Seed Demo
        </button>
        <dl v-if="seedSummary" class="summary">
          <div v-for="(v, k) in seedSummary" :key="String(k)">
            <dt>{{ k }}</dt>
            <dd>{{ Array.isArray(v) ? v.join(', ') : v }}</dd>
          </div>
        </dl>
      </div>

      <div class="card">
        <h2>内核快照</h2>
        <p class="muted">GET /snapshot → 多仓 / 单据 / 库存</p>
        <button type="button" class="btn" :disabled="session.busy" @click="trySnapshot">
          刷新 snapshot
        </button>
        <p v-if="snapshotNote" class="note">{{ snapshotNote }}</p>
        <p class="muted" v-if="session.snapshot">来源：{{ session.snapshot.source }}</p>
      </div>

      <div class="card">
        <h2>单据一览</h2>
        <p class="muted">入库 / 出库 / 波次 / 盘点（来自 snapshot）</p>
        <ul class="id-list" v-if="inboundList.length || outboundList.length || waveList.length || stocktakeList.length">
          <li v-for="d in inboundList" :key="'in-'+d.id">入库 <code>{{ d.id }}</code> · {{ d.status }} · {{ d.warehouse }}</li>
          <li v-for="d in outboundList" :key="'out-'+d.id">出库 <code>{{ d.id }}</code> · {{ d.status }} · {{ d.warehouse }}</li>
          <li v-for="d in waveList" :key="'wv-'+d.id">波次 <code>{{ d.id }}</code> · {{ d.status }}</li>
          <li v-for="d in stocktakeList" :key="'st-'+d.id">盘点 <code>{{ d.id }}</code> · {{ d.status }} · {{ d.warehouse }}</li>
        </ul>
        <p v-else class="muted">先 Seed 或刷新 snapshot。</p>
      </div>
    </div>
  </div>
</template>
