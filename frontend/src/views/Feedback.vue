<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, allPages } from '../api/index.js'
import { currentPlatform, selectedPlatform } from '../stores/platforms.js'
import { displayDate, safeLink } from '../utils/domain.js'
import MemoryPanel from '../components/MemoryPanel.vue'
import EmptyState from '../components/EmptyState.vue'
const route = useRoute(),
  router = useRouter()
const records = ref([]),
  products = ref([]),
  productId = ref(''),
  query = ref(''),
  loading = ref(true),
  error = ref('')
const selected = computed(() => records.value.find((r) => r.id === Number(route.query.record_id)))
const filtered = computed(() =>
  records.value.filter(
    (r) =>
      (!productId.value || r.product_id === Number(productId.value)) &&
      `${r.title} ${r.product_name}`.toLowerCase().includes(query.value.toLowerCase()),
  ),
)
let turn = 0
async function reload() {
  const request = ++turn
  const platform = selectedPlatform.value
  loading.value = true
  error.value = ''
  try {
    const values = await Promise.all([
      allPages(api.memory.publications, { platform }),
      allPages(api.products.list),
    ])
    if (request === turn) {
      records.value = values[0]
      products.value = values[1]
    }
  } catch (e) {
    if (request === turn) error.value = e.message
  } finally {
    if (request === turn) loading.value = false
  }
}
watch(selectedPlatform, () => {
  records.value = []
  productId.value = ''
  query.value = ''
  router.replace('/feedback')
  reload()
})
onMounted(reload)
onUnmounted(() => {
  ++turn
})
</script>
<template>
  <header class="page-heading">
    <div>
      <p class="eyebrow">FEEDBACK & LEARNING</p>
      <h1>数据反馈</h1>
      <p>持续观察真实表现，把每次发布积累为下一轮创作的参考。</p>
    </div>
    <div class="row">
      <RouterLink v-if="route.query.record_id" class="button ghost" to="/feedback">
        ← 返回作品列表
      </RouterLink>
      <button class="button" :disabled="loading" @click="reload">刷新作品</button>
    </div>
  </header>
  <p class="notice info">运营经验通过经验库供后续生成检索参考，不会直接训练或改变大模型参数。</p>
  <p v-if="!currentPlatform.workflow" class="muted">{{ currentPlatform.name }} · 待接入</p>
  <p v-if="error" class="error-text" role="alert">{{ error }}</p>
  <p v-if="loading" class="loading-line">读取已发布作品…</p>
  <template v-else-if="selected">
    <section class="panel feedback-publication section-gap">
      <p class="eyebrow">{{ selected.product_name }} · {{ currentPlatform.name }}</p>
      <h2>{{ selected.title }}</h2>
      <p class="muted">
        内容 v{{ selected.content_version || '—' }} · 发布记录 #{{ selected.id }} ·
        {{ selected.visibility === 'private' ? '仅自己可见' : '公开作品' }}
      </p>
      <p class="muted">记录的发布时间：{{ displayDate(selected.published_at, true) }}</p>
      <div class="row">
        <a
          v-if="safeLink(selected.platform_post_url)"
          class="button small"
          :href="safeLink(selected.platform_post_url)"
          target="_blank"
          rel="noopener noreferrer"
        >
          查看作品 ↗
        </a>
        <RouterLink
          class="button ghost small"
          :to="{ path: '/review', query: { task_id: selected.task_id } }"
        >
          查看内容与发布记录
        </RouterLink>
      </div>
    </section>
    <MemoryPanel :key="selected.id" :record="selected" :product-id="selected.product_id" />
  </template>
  <section v-else-if="route.query.record_id && !loading" class="panel empty-page section-gap">
    <h2>未找到该已发布作品</h2>
    <p class="muted">作品可能不属于当前账号或当前平台。</p>
    <RouterLink class="button" to="/feedback">返回作品列表</RouterLink>
  </section>
  <section v-else class="panel section-gap">
    <div class="panel-header">
      <h2>已发布作品</h2>
      <span class="muted">{{ records.length }} 篇</span>
    </div>
    <div class="row feedback-filters">
      <input
        v-model="query"
        class="grow"
        aria-label="搜索反馈作品"
        placeholder="搜索作品标题或产品"
      />
      <select v-model="productId" aria-label="筛选反馈产品">
        <option value="">所有产品</option>
        <option v-for="product in products" :key="product.id" :value="product.id">
          {{ product.name }}
        </option>
      </select>
    </div>
    <div class="list product-list">
      <button
        v-for="record in filtered"
        :key="record.id"
        class="list-item"
        @click="router.push({ path: '/feedback', query: { record_id: record.id } })"
      >
        <strong>{{ record.title }}</strong>
        <p>
          {{ record.product_name }} · {{ displayDate(record.published_at, true) }} ·
          {{ record.visibility === 'private' ? '仅自己可见' : '公开作品' }}
        </p>
        <p>查看多次观测记录与运营经验</p>
      </button>
    </div>
    <EmptyState
      v-if="!filtered.length"
      title="暂无已发布作品"
      description="完成审核并登记发布成功后，可以在这里采集数据、记录观察窗口并提炼运营经验。"
    >
      <RouterLink class="button" to="/review">前往审核发布</RouterLink>
    </EmptyState>
  </section>
</template>
