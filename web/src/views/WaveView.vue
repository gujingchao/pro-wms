<script setup lang="ts">
import { ref } from 'vue'
import { api } from '../api/client'
import { session, runAction } from '../stores/session'

const outboundIds = ref('OUT-2001')
const waveId = ref('')

async function createWave() {
  const ids = outboundIds.value
    .split(/[\s,]+/)
    .map((s) => s.trim())
    .filter(Boolean)
  // wave create defaults to supervisor on API; send current role so 403 can be demo'd
  const data = await runAction('创建波次', () => api.waveCreate(session.baseUrl, ids, session.user))
  if (data && typeof data === 'object' && data !== null && 'id' in data) {
    waveId.value = String((data as { id: string }).id)
  }
}

async function pick() {
  if (!waveId.value.trim()) return
  await runAction('波次拣货', () =>
    api.wavePick(session.baseUrl, waveId.value.trim(), session.user),
  )
}
</script>

<template>
  <div class="page">
    <p class="lede">
      创建波次需要 supervisor（403 可切角色验证）。Pick 后出库单进入 shipped。波次 ID 由服务返回（如 WV-2001）。
    </p>

    <div class="card">
      <div class="form-row">
        <label class="field grow">
          <span>出库单（逗号分隔）</span>
          <input v-model="outboundIds" class="input" />
        </label>
        <label class="field">
          <span>波次 ID</span>
          <input v-model="waveId" class="input" placeholder="创建后自动回填" />
        </label>
      </div>
      <div class="btn-row">
        <button type="button" class="btn btn--primary" :disabled="session.busy" @click="createWave">
          创建波次
        </button>
        <button
          type="button"
          class="btn"
          :disabled="session.busy || !waveId.trim()"
          @click="pick"
        >
          Pick 拣货
        </button>
      </div>
      <p class="muted">提示：创建波次建议切换角色为 supervisor。</p>
    </div>
  </div>
</template>
