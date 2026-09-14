<script setup lang="ts">
import { computed } from 'vue'
import { session } from '../stores/session'

const jsonText = computed(() => {
  if (session.lastResult === null || session.lastResult === undefined) {
    return '// 操作结果会显示在这里'
  }
  try {
    return JSON.stringify(session.lastResult, null, 2)
  } catch {
    return String(session.lastResult)
  }
})
</script>

<template>
  <section class="result-panel">
    <header class="result-panel__head">
      <h3>JSON 结果</h3>
      <span v-if="session.lastError" class="tag tag--warn">HTTP {{ session.lastError.status }}</span>
      <span v-else-if="session.lastResult !== null" class="tag tag--ok">OK</span>
    </header>
    <pre class="result-panel__body">{{ jsonText }}</pre>
  </section>
</template>
