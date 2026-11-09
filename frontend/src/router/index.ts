import { createRouter, createWebHistory } from 'vue-router'

import AuditView from '../views/AuditView.vue'
import CallView from '../views/CallView.vue'
import CorrectView from '../views/CorrectView.vue'
import DevicesView from '../views/DevicesView.vue'
import LearnView from '../views/LearnView.vue'
import StateView from '../views/StateView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/call' },
    { path: '/call', name: 'call', component: CallView, meta: { title: '调用' } },
    { path: '/devices', name: 'devices', component: DevicesView, meta: { title: '设备' } },
    { path: '/learn', name: 'learn', component: LearnView, meta: { title: '学码' } },
    { path: '/state', name: 'state', component: StateView, meta: { title: '状态' } },
    { path: '/correct', name: 'correct', component: CorrectView, meta: { title: '校正' } },
    { path: '/audit', name: 'audit', component: AuditView, meta: { title: '审计' } },
    { path: '/:pathMatch(.*)*', redirect: '/call' },
  ],
  scrollBehavior: () => ({ top: 0 }),
})

router.afterEach((route) => {
  const title = typeof route.meta.title === 'string' ? route.meta.title : ''
  document.title = title ? `${title} · HomeLink` : 'HomeLink · 红外控制台'
})

export default router
