<script setup lang="ts">
import { ref } from 'vue'
import { api } from '../api/client'
import { session, runAction } from '../stores/session'

const stocktakeId = ref('ST-3001')

async function approve() {
  await runAction('盘点审批', () =>
    api.stocktakeApprove(session.baseUrl, stocktakeId.value.trim(), session.user),
  )
}
</script>

<template>
  <div class="page">
    <p class="lede">
      盘点审批仅 supervisor / admin。operator 调用会得到 403 PermissionDenied。Seed 后 ST-3001 状态为 submitted。
    </p>

    <div class="card">
      <div class="form-row">
        <label class="field">
          <span>盘点单 ID</span>
          <input v-model="stocktakeId" class="input" />
        </label>
      </div>
      <div class="btn-row">
        <button type="button" class="btn btn--primary" :disabled="session.busy" @click="approve">
          Approve 审批通过
        </button>
      </div>
      <p class="muted">当前 X-User={{ session.user }}</p>
    </div>
  </div>
</template>
