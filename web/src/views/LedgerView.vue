<script setup lang="ts">
import { ref } from 'vue'
import { api } from '../api/client'
import { session, runAction } from '../stores/session'

const sku = ref('SKU-MILK')
const useWarehouse = ref(true)

async function query() {
  const wh = useWarehouse.value ? session.warehouse : null
  await runAction('查询流水', () => api.ledger(session.baseUrl, sku.value.trim(), wh))
}
</script>

<template>
  <div class="page">
    <p class="lede">GET /ledger?sku=&amp;warehouse= — 返回 on_hand 与 ledger 行。</p>

    <div class="card">
      <div class="form-row">
        <label class="field">
          <span>SKU</span>
          <input v-model="sku" class="input" />
        </label>
        <label class="field field--check">
          <input v-model="useWarehouse" type="checkbox" />
          <span>限定仓库 {{ session.warehouse }}</span>
        </label>
      </div>
      <div class="btn-row">
        <button type="button" class="btn btn--primary" :disabled="session.busy" @click="query">
          查询 Ledger
        </button>
      </div>
    </div>
  </div>
</template>
