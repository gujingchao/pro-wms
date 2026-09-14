<script setup lang="ts">
import { ref } from 'vue'
import { api } from '../api/client'
import { session, runAction } from '../stores/session'

const sku = ref('SKU-MILK')
const workers = ref(8)

async function race() {
  await runAction('并发竞态', () =>
    api.race(
      session.baseUrl,
      {
        sku: sku.value.trim(),
        warehouse: session.warehouse,
        workers: Number(workers.value) || 8,
      },
      session.user,
    ),
  )
}
</script>

<template>
  <div class="page">
    <p class="lede">
      多 worker 并发分配同一 SKU，乐观版本号只允许一人成功，其余 409 conflict。结果含 ok / conflict /
      on_hand。
    </p>

    <div class="card">
      <div class="form-row">
        <label class="field">
          <span>SKU</span>
          <input v-model="sku" class="input" />
        </label>
        <label class="field">
          <span>Workers</span>
          <input v-model.number="workers" class="input" type="number" min="2" max="32" />
        </label>
        <label class="field">
          <span>仓库</span>
          <input :value="session.warehouse" class="input" disabled />
        </label>
      </div>
      <div class="btn-row">
        <button type="button" class="btn btn--danger" :disabled="session.busy" @click="race">
          跑 Race
        </button>
      </div>
    </div>
  </div>
</template>
