<script setup lang="ts">
import { ref } from 'vue'
import { api } from '../api/client'
import { session, runAction } from '../stores/session'

const outboundId = ref('OUT-2001')
const strategy = ref<'fifo' | 'fefo'>('fefo')

async function allocate() {
  await runAction('出库分配', () =>
    api.outboundAllocate(session.baseUrl, outboundId.value.trim(), strategy.value, session.user),
  )
}
</script>

<template>
  <div class="page">
    <p class="lede">
      出库分配使用 FIFO / FEFO；乐观版本冲突时返回 409 StockConflict。先 Seed 再分配 OUT-2001。
    </p>

    <div class="card">
      <div class="form-row">
        <label class="field">
          <span>出库单 ID</span>
          <input v-model="outboundId" class="input" />
        </label>
        <label class="field">
          <span>策略</span>
          <select v-model="strategy" class="select">
            <option value="fefo">FEFO（近效期优先）</option>
            <option value="fifo">FIFO（先入先出）</option>
          </select>
        </label>
      </div>
      <div class="btn-row">
        <button type="button" class="btn btn--primary" :disabled="session.busy" @click="allocate">
          Allocate 分配
        </button>
      </div>
    </div>
  </div>
</template>
