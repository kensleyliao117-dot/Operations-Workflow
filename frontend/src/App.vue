<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { session, clearSession } from './stores/session.js'
import { messages, dismiss, action, busy, notify } from './stores/ui.js'
import { api } from './api/index.js'
import { ui } from './config/ui.js'
import AppIcon from './components/AppIcon.vue'
import { readLocal, writeLocal, userKey } from './utils/storage.js'
import {
  platforms,
  selectedPlatform,
  currentPlatform,
  loadPlatforms,
  selectPlatform,
} from './stores/platforms.js'
const sidebarOpen = ref(window.innerWidth > 850),
  recentTasks = ref([])
const navigation = [
  { path: '/products', label: '产品资料', icon: 'products' },
  { path: '/cases', label: '案例库', icon: 'cases' },
  { path: '/generate', label: '内容创作', icon: 'create' },
  { path: '/review', label: '审核发布', icon: 'review' },
  { path: '/feedback', label: '数据反馈', icon: 'feedback' },
]
let smallScreen = window.innerWidth <= 850
let recentTurn = 0
function resize() {
  const next = window.innerWidth <= 850
  if (next !== smallScreen)
    sidebarOpen.value = next
      ? false
      : readLocal(userKey(session.user?.id, 'sidebar-open')) !== false
  smallScreen = next
}
function keyboard(event) {
  if (event.key === 'Escape' && smallScreen) sidebarOpen.value = false
}
onMounted(() => window.addEventListener('resize', resize))
onUnmounted(() => window.removeEventListener('resize', resize))
onMounted(() => window.addEventListener('keydown', keyboard))
onUnmounted(() => window.removeEventListener('keydown', keyboard))
watch(sidebarOpen, (value) => {
  if (!smallScreen && session.user) writeLocal(userKey(session.user.id, 'sidebar-open'), value)
})
const route = useRoute(),
  router = useRouter()
const isPublic = computed(() => route.meta.public)
watch(
  () => session.user?.id,
  (id) => {
    recentTasks.value = []
    sidebarOpen.value = !smallScreen && readLocal(userKey(id, 'sidebar-open')) !== false
    if (id) action('platform-catalog', loadPlatforms)
  },
  { immediate: true },
)
watch(
  () => route.fullPath,
  () => {
    if (smallScreen) sidebarOpen.value = false
  },
)
watch(
  [selectedPlatform, () => session.user?.id, () => route.query.task_id],
  async () => {
    const turn = ++recentTurn
    if (!session.user) return
    try {
      const rows = await api.tasks.list({ platform_name: selectedPlatform.value, limit: 20 })
      if (turn === recentTurn) recentTasks.value = rows
    } catch (e) {
      if (turn === recentTurn) notify(e.message, 'error')
    }
  },
  { immediate: true },
)
async function changePlatform(event) {
  if (busy.size) {
    event.target.value = selectedPlatform.value
    notify('当前操作尚未完成，请稍后切换平台')
    return
  }
  const request = new CustomEvent('platform-before-change', { cancelable: true })
  if (event.target.value === selectedPlatform.value || !window.dispatchEvent(request)) {
    event.target.value = selectedPlatform.value
    return
  }
  if (selectPlatform(event.target.value) && ['/generate', '/review'].includes(route.path))
    await router.replace(route.path)
}
function startNew() {
  router.push({ path: '/generate', query: { new: Date.now() } })
}
async function logout() {
  await action('logout', async () => {
    try {
      await api.auth.logout()
    } catch (e) {
      if (e.status !== 401) notify('已清除本地会话，服务端退出未确认；请检查后端连接。', 'error')
    } finally {
      clearSession()
      await router.replace('/login')
    }
  })
}
</script>

<template>
  <div class="app" :class="{ 'app-public': isPublic, 'sidebar-collapsed': !sidebarOpen }">
    <aside
      v-if="!isPublic"
      id="workspace-sidebar"
      class="sidebar"
      :inert="!sidebarOpen"
      :aria-hidden="!sidebarOpen"
    >
      <RouterLink class="brand" to="/products">
        <img :src="ui.assets.logo" alt="" />
        <span>
          {{ ui.appName }}
          <small>{{ ui.appSubtitle }}</small>
        </span>
      </RouterLink>
      <button class="button new-conversation" @click="startNew">
        <AppIcon name="plus" :size="18" />
        新建内容
      </button>
      <nav aria-label="主导航">
        <RouterLink
          v-for="item in navigation"
          :key="item.path"
          :to="item.path"
          :class="{ selected: route.path.startsWith(item.path) }"
        >
          <AppIcon :name="item.icon" />
          {{ item.label }}
        </RouterLink>
      </nav>
      <div class="sidebar-history">
        <p class="nav-caption">最近任务 · {{ currentPlatform.name }}</p>
        <RouterLink
          v-for="item in recentTasks"
          :key="item.id"
          :to="{ path: '/generate', query: { task_id: item.id } }"
          class="history-link"
          :title="item.input_text"
        >
          {{ item.input_text || `任务 #${item.id}` }}
        </RouterLink>
        <p v-if="!recentTasks.length" class="muted small-text">暂无任务</p>
      </div>
      <div class="sidebar-foot">
        <RouterLink
          class="settings-link"
          to="/settings"
          :class="{ selected: route.path === '/settings' }"
        >
          <AppIcon name="settings" />
          设置
        </RouterLink>
        <p>独立资料 · 持续创作</p>
      </div>
    </aside>
    <button
      v-if="sidebarOpen && !isPublic"
      class="sidebar-backdrop"
      aria-label="关闭侧栏"
      @click="sidebarOpen = false"
    ></button>
    <div class="app-body">
      <header v-if="!isPublic" class="topbar">
        <div class="row grow">
          <button
            class="button ghost sidebar-toggle"
            aria-label="切换侧栏"
            :aria-expanded="sidebarOpen"
            aria-controls="workspace-sidebar"
            @click="sidebarOpen = !sidebarOpen"
          >
            <AppIcon name="sidebar" />
          </button>
          <label class="platform-picker">
            <span class="sr-only">目标平台</span>
            <select :value="selectedPlatform" aria-label="目标平台" @change="changePlatform">
              <option v-for="item in platforms" :key="item.id" :value="item.id">
                {{ item.name }}{{ item.workflow ? '' : ' · 待接入' }}
              </option>
            </select>
          </label>
          <span class="header-page">{{ route.meta.title }}</span>
        </div>
        <div class="row">
          <span class="avatar">{{ session.user?.username?.[0]?.toUpperCase() }}</span>
          <span class="header-username">{{ session.user?.username }}</span>
          <button class="button ghost small" :disabled="busy.has('logout')" @click="logout">
            退出登录
          </button>
        </div>
      </header>
      <main :class="isPublic ? 'auth-main' : 'main'"><RouterView /></main>
    </div>
    <div class="toasts" aria-live="polite">
      <div
        v-for="message in messages"
        :key="message.id"
        class="toast"
        :class="message.type"
        role="status"
      >
        <span>{{ message.text }}</span>
        <button aria-label="关闭消息" @click="dismiss(message.id)">×</button>
      </div>
    </div>
  </div>
</template>
