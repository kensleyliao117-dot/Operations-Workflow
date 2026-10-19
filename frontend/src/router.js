import { createRouter, createWebHistory } from 'vue-router'
import { session, validateSession } from './stores/session.js'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/products' },
    {
      path: '/login',
      component: () => import('./views/Login.vue'),
      meta: { public: true, title: '登录' },
    },
    {
      path: '/register',
      component: () => import('./views/Register.vue'),
      meta: { public: true, title: '注册' },
    },
    {
      path: '/products',
      component: () => import('./views/Products.vue'),
      meta: { title: '产品管理' },
    },
    {
      path: '/products/:id',
      component: () => import('./views/Products.vue'),
      meta: { title: '产品详情' },
    },
    { path: '/cases', component: () => import('./views/Cases.vue'), meta: { title: '案例库' } },
    {
      path: '/feedback',
      component: () => import('./views/Feedback.vue'),
      meta: { title: '数据反馈' },
    },
    { path: '/settings', component: () => import('./views/Settings.vue'), meta: { title: '设置' } },
    {
      path: '/generate',
      component: () => import('./views/Generate.vue'),
      meta: { title: '内容创作' },
    },
    { path: '/review', component: () => import('./views/Review.vue'), meta: { title: '审核发布' } },
    { path: '/:pathMatch(.*)*', redirect: '/products' },
  ],
})
router.beforeEach(async (to) => {
  if (to.meta.public) return
  if (!session.token) return { path: '/login', query: { redirect: to.fullPath } }
  try {
    if (await validateSession()) return
  } catch (error) {
    if (error.status !== 401)
      return { path: '/login', query: { reason: '无法验证会话，请检查后端服务' } }
  }
  return { path: '/login', query: { redirect: to.fullPath } }
})
window.addEventListener('session-expired', () =>
  router.replace({ path: '/login', query: { reason: '登录已失效，请重新登录' } }),
)
