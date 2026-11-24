import { createRouter, createWebHistory } from 'vue-router'

import IngestMonitorView from '@/views/IngestMonitorView.vue'
import Overview from '@/views/Overview.vue'
import WorkbenchView from '@/views/WorkbenchView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'overview', component: Overview },
    { path: '/ingest_monitor', name: 'ingest-monitor', component: IngestMonitorView },
    { path: '/:moduleKey', name: 'module', component: WorkbenchView, props: true },
  ],
})

export default router
