<script setup lang="ts">
import { ref } from 'vue'
import { api } from '../api/client'
import { session, runAction } from '../stores/session'

const sku = ref('SKU-MILK')
const useWarehouse = ref(true)
const balance = ref<{ on_hand: number; reserved: number; available: number } | null>(null)

async function query() {
  const wh = useWarehouse.value ? session.warehouse : null
  balance.value = null
  const result = await runAction('查询流水', () => api.ledger(session.baseUrl, sku.value.trim(), wh))
  if (result) balance.value = result
}
</script>

<template>
  <div class="page">
    <p class="lede">实物库存包含已预占、尚未发运的货物；可用库存等于实物减去预占。</p>

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
      <dl v-if="balance" class="summary">
        <div><dt>实物库存</dt><dd>{{ balance.on_hand }}</dd></div>
        <div><dt>预占库存</dt><dd>{{ balance.reserved }}</dd></div>
        <div><dt>可用库存</dt><dd>{{ balance.available }}</dd></div>
      </dl>
    </div>
  </div>
</template>
