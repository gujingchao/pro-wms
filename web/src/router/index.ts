import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    { path: '/', name: 'dashboard', component: () => import('../views/DashboardView.vue'), meta: { title: '仪表盘 / 种子' } },
    { path: '/inbound', name: 'inbound', component: () => import('../views/InboundView.vue'), meta: { title: '入库' } },
    { path: '/outbound', name: 'outbound', component: () => import('../views/OutboundView.vue'), meta: { title: '出库分配' } },
    { path: '/waves', name: 'waves', component: () => import('../views/WaveView.vue'), meta: { title: '波次看板' } },
    { path: '/stocktake', name: 'stocktake', component: () => import('../views/StocktakeView.vue'), meta: { title: '盘点审批' } },
    { path: '/ledger', name: 'ledger', component: () => import('../views/LedgerView.vue'), meta: { title: '库存流水' } },
    { path: '/race', name: 'race', component: () => import('../views/RaceView.vue'), meta: { title: '并发竞态' } },
  ],
})

export default router
