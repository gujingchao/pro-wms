<script setup lang="ts">
import { ref } from 'vue'
import { api } from '../api/client'
import { session, runAction } from '../stores/session'

const inboundId = ref('IN-1001')
const toLocation = ref('EAST-A-01-01')

async function receive() {
  await runAction('入库收货', () =>
    api.inboundReceive(session.baseUrl, inboundId.value.trim(), session.user),
  )
}

async function putaway() {
  await runAction('入库上架', () =>
    api.inboundPutaway(session.baseUrl, inboundId.value.trim(), toLocation.value.trim(), session.user),
  )
}
</script>

<template>
  <div class="page">
    <p class="lede">
      入库状态机：draft → receive → receiving → putaway → closed。非法跳转返回 409 IllegalTransition。
    </p>

    <div class="card">
      <div class="form-row">
        <label class="field">
          <span>入库单 ID</span>
          <input v-model="inboundId" class="input" />
        </label>
        <label class="field">
          <span>上架库位</span>
          <input v-model="toLocation" class="input" placeholder="EAST-A-01-01" />
        </label>
      </div>
      <div class="btn-row">
        <button type="button" class="btn btn--primary" :disabled="session.busy" @click="receive">
          1. Receive 收货
        </button>
        <button type="button" class="btn" :disabled="session.busy" @click="putaway">
          2. Putaway 上架
        </button>
      </div>
      <p class="muted">当前角色 X-User={{ session.user }} · 仓库上下文 {{ session.warehouse }}</p>
    </div>
  </div>
</template>
