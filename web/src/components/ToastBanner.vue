<script setup lang="ts">
import { session, clearBanner } from '../stores/session'
</script>

<template>
  <div v-if="session.banner" class="banner" :class="`banner--${session.banner.kind}`" role="alert">
    <span>{{ session.banner.text }}</span>
    <button type="button" class="banner__close" @click="clearBanner" aria-label="关闭">×</button>
  </div>
</template>
