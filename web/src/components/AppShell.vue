<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import ToastBanner from './ToastBanner.vue'
import ResultPanel from './ResultPanel.vue'
import {
  session,
  setBaseUrl,
  checkHealth,
  refreshSnapshot,
  warehouses,
  roles,
  healthLabel
} from '../stores/session'

const route = useRoute()
const baseDraft = ref(session.baseUrl)
let timer: ReturnType<typeof setInterval> | null = null

const nav = [
  { to: '/', label: '仪表盘 / 种子', icon: '◈' },
  { to: '/inbound', label: '入库', icon: '⬇' },
  { to: '/outbound', label: '出库分配', icon: '⬆' },
  { to: '/waves', label: '波次看板', icon: '≋' },
  { to: '/stocktake', label: '盘点审批', icon: '☑' },
  { to: '/ledger', label: '库存流水', icon: '☰' },
  { to: '/race', label: '并发竞态', icon: '⚡' },
]

function applyBase() {
  setBaseUrl(baseDraft.value.trim() || 'http://127.0.0.1:8080')
  baseDraft.value = session.baseUrl
  void checkHealth()
}

onMounted(() => {
  void checkHealth()
  void refreshSnapshot()
  timer = setInterval(() => void checkHealth(), 15000)
})

onUnmounted(() => {
  if (timer) clearInterval(timer)
})

</script>

<template>
  <div class="shell">
    <aside class="sidebar">
      <div class="brand">
        <div class="brand__mark">WMS</div>
        <div>
          <div class="brand__title">pro-wms</div>
          <div class="brand__sub">admin console</div>
        </div>
      </div>
      <nav class="nav">
        <RouterLink
          v-for="item in nav"
          :key="item.to"
          :to="item.to"
          class="nav__item"
          :class="{ 'nav__item--active': route.path === item.to }"
        >
          <span class="nav__icon">{{ item.icon }}</span>
          {{ item.label }}
        </RouterLink>
      </nav>
      <div class="sidebar__foot">
        <div class="health" :class="`health--${session.health}`">
          <span class="health__dot" />
          {{ healthLabel }}
        </div>
      </div>
    </aside>

    <div class="main">
      <header class="topbar">
        <div class="topbar__title">
          {{ (route.meta.title as string) || 'pro-wms' }}
        </div>
        <div class="topbar__controls">
          <label class="field field--inline">
            <span>API</span>
            <input v-model="baseDraft" class="input input--url" @keydown.enter="applyBase" />
            <button type="button" class="btn btn--ghost" @click="applyBase">应用</button>
          </label>
          <label class="field field--inline">
            <span>角色</span>
            <select v-model="session.user" class="select">
              <option v-for="r in roles" :key="r" :value="r">{{ r }}</option>
            </select>
          </label>
          <label class="field field--inline">
            <span>仓库</span>
            <select v-model="session.warehouse" class="select">
              <option v-for="w in warehouses" :key="w" :value="w">{{ w }}</option>
            </select>
          </label>
          <button type="button" class="btn btn--ghost" :disabled="session.busy" @click="checkHealth">
            刷新连接
          </button>
        </div>
      </header>

      <ToastBanner />

      <div class="content">
        <div class="content__page">
          <slot />
        </div>
        <ResultPanel />
      </div>
    </div>
  </div>
</template>
