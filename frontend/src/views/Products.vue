<script setup>
import { ref, computed, reactive, onMounted, watch } from 'vue'
import { onBeforeRouteLeave, onBeforeRouteUpdate, useRoute, useRouter } from 'vue-router'
import { api, allPages } from '../api/index.js'
import { action, busy, notify } from '../stores/ui.js'
import { formatSize, displayDate } from '../utils/domain.js'
import { download } from '../utils/storage.js'
import BaseDialog from '../components/BaseDialog.vue'
import EmptyState from '../components/EmptyState.vue'
import StatusBadge from '../components/StatusBadge.vue'
import { session } from '../stores/session.js'
const route = useRoute(),
  router = useRouter()
let internalNavigation = false
async function navigate(path) {
  internalNavigation = true
  try {
    await router.replace(path)
  } finally {
    internalNavigation = false
  }
}
const products = ref([]),
  selected = ref(Number(route.params.id) || null),
  search = ref(''),
  loading = ref(true),
  error = ref(''),
  documentLoading = ref(false)
const documents = reactive({}),
  documentErrors = reactive({}),
  queue = ref([])
const editor = ref(false),
  editing = ref(null),
  form = reactive({ name: '', description: '' }),
  confirmation = ref(null)
const current = computed(() => products.value.find((p) => p.id === selected.value))
const filtered = computed(() =>
  products.value.filter((p) =>
    (p.name + ' ' + (p.description || '')).toLowerCase().includes(search.value.toLowerCase()),
  ),
)
const currentDocuments = computed(() => documents[selected.value] || [])
async function loadDocuments(id) {
  try {
    documents[id] = await allPages((params) => api.products.documents(id, params))
    delete documentErrors[`list:${id}`]
  } catch (e) {
    documentErrors[`list:${id}`] = e.message
    throw e
  }
}
async function reload() {
  loading.value = true
  error.value = ''
  try {
    products.value = await allPages(api.products.list)
    selected.value = Number(route.params.id) || null
    if (route.params.id && !products.value.some((p) => p.id === selected.value))
      error.value = '产品不存在或无权访问，可以返回产品列表重新选择。'
    await Promise.allSettled(products.value.map((p) => loadDocuments(p.id)))
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}
async function selectProduct(id) {
  if (busy.has('upload') || busy.has('delete-product')) return
  await router.push(`/products/${id}`)
  documentLoading.value = true
  try {
    await loadDocuments(id)
  } catch (e) {
    notify(e.message, 'error')
  } finally {
    documentLoading.value = false
  }
}
function openEditor(product = null) {
  editing.value = product
  form.name = product?.name || ''
  form.description = product?.description || ''
  editor.value = true
}
async function saveProduct() {
  await action('save-product', async () => {
    const result = editing.value
      ? await api.products.update(editing.value.id, {
          name: form.name.trim(),
          description: form.description,
        })
      : await api.products.create({ name: form.name.trim(), description: form.description })
    editor.value = false
    await navigate(`/products/${result.id}`)
    await reload()
    notify('产品已保存')
  })
}
function chooseFiles(event) {
  for (const file of event.target.files) {
    const invalid = !/\.(pdf|txt|md|docx)$/i.test(file.name)
      ? '仅支持 PDF、TXT、MD、DOCX'
      : !file.size
        ? '不能上传空文件'
        : file.size > 20 * 1024 * 1024
          ? '文件超过 20 MB'
          : ''
    queue.value.push({
      id: crypto.randomUUID(),
      file,
      state: invalid ? 'invalid' : 'queued',
      error: invalid,
    })
  }
  event.target.value = ''
}
async function upload() {
  const id = selected.value
  await action('upload', async () => {
    for (const item of queue.value.filter((i) => ['queued', 'error'].includes(i.state))) {
      item.state = 'uploading'
      item.error = ''
      try {
        await api.products.upload(id, item.file)
        item.state = 'done'
      } catch (e) {
        item.state = 'error'
        item.error = e.message
      }
    }
    await loadDocuments(id)
    notify('上传处理已结束，请查看每个文件的状态。建立知识库需另行执行。', 'info')
  })
}
async function indexDocument(document) {
  if (
    document.status === 'indexed' &&
    !window.confirm('重新入库会调用 Embedding 服务并更新该资料的知识片段，是否继续？')
  )
    return
  const id = selected.value
  await action(`index:${document.id}`, async () => {
    delete documentErrors[document.id]
    try {
      await api.products.index(id, document.id)
      notify(`${document.filename} 已完成知识入库`)
    } catch (e) {
      documentErrors[document.id] = e.message
      throw e
    } finally {
      await loadDocuments(id)
    }
  })
}
async function remove() {
  const target = confirmation.value
  await action('delete-product', async () => {
    if (target.document) await api.products.removeDocument(target.product.id, target.document.id)
    else await api.products.remove(target.product.id)
    confirmation.value = null
    if (!target.document) await navigate('/products')
    await reload()
    notify('已删除')
  })
}
function canLeave() {
  if (!session.token) return true
  if (internalNavigation) return true
  if (busy.has('upload') || busy.has('delete-product')) {
    notify('正在处理文件，请等待操作结束')
    return false
  }
  return (
    !queue.value.some((i) => !['done'].includes(i.state)) ||
    window.confirm('尚有未上传的文件选择，离开后需要重新选择，是否继续？')
  )
}
onBeforeRouteLeave(canLeave)
onBeforeRouteUpdate((to, from) => to.params.id === from.params.id || canLeave())
watch(
  () => route.params.id,
  (id) => {
    selected.value = Number(id) || null
    queue.value = []
  },
)
onMounted(reload)
</script>
<template>
  <div class="page-heading">
    <div>
      <p class="eyebrow">PRODUCT LIBRARY</p>
      <h1>{{ current ? '产品资料' : '产品管理' }}</h1>
      <p>
        {{
          current
            ? '产品事实、资料与知识库，在这里保持一致。'
            : '从真实产品出发，让每次创作都有据可依。'
        }}
      </p>
    </div>
    <div class="row">
      <RouterLink v-if="route.params.id" class="button ghost" to="/products">
        ← 返回产品列表
      </RouterLink>
      <button v-else class="button primary" @click="openEditor()">＋ 新建产品</button>
      <button v-if="route.params.id" class="button" :disabled="loading" @click="reload">
        刷新
      </button>
    </div>
  </div>
  <div v-if="error" class="inline-error" role="alert">
    {{ error }}
    <button class="button small" @click="reload">重试</button>
  </div>
  <p v-if="loading" class="loading-line">
    <span class="spinner"></span>
    加载产品与资料…
  </p>
  <div class="product-workspace">
    <section v-if="!route.params.id" class="panel product-library">
      <div class="panel-header">
        <h3>
          产品库
          <span class="muted">{{ products.length }}</span>
        </h3>
        <button class="button ghost small" :disabled="loading" @click="reload">刷新</button>
      </div>
      <input
        v-model="search"
        class="search"
        aria-label="搜索产品"
        placeholder="搜索产品名称或简介"
      />
      <div class="list product-list">
        <button
          v-for="product in filtered"
          :key="product.id"
          class="list-item"
          :class="{ active: selected === product.id }"
          :disabled="busy.has('upload')"
          @click="selectProduct(product.id)"
        >
          <strong>{{ product.name }}</strong>
          <p>{{ product.description || '尚未填写简介' }}</p>
          <p v-if="documents[product.id]">
            {{ documents[product.id].length }} 份资料 ·
            {{ documents[product.id].filter((d) => d.status === 'indexed').length }} 份已入库
          </p>
          <p v-else>
            {{ documentErrors[`list:${product.id}`] ? '资料状态读取失败' : '读取资料状态…' }}
          </p>
        </button>
      </div>
      <EmptyState
        v-if="!loading && !filtered.length"
        image="products"
        title="暂无产品"
        description="创建产品后即可上传资料并建立知识库。"
      >
        <button class="button" @click="openEditor()">新建产品</button>
      </EmptyState>
    </section>
    <div v-if="current" class="stack">
      <section class="panel">
        <div class="panel-header">
          <div>
            <h2>{{ current.name }}</h2>
            <small class="muted">创建于 {{ displayDate(current.created_at) }}</small>
          </div>
          <div class="row">
            <button class="button small" @click="openEditor(current)">编辑</button>
            <button class="button danger small" @click="confirmation = { product: current }">
              删除产品
            </button>
          </div>
        </div>
        <p class="preserve muted">
          {{ current.description || '添加简介，让产品更容易辨识。详细参数与卖点可通过资料上传。' }}
        </p>
        <RouterLink
          class="button primary"
          :to="{ path: '/generate', query: { product_id: current.id } }"
        >
          为此产品生成内容 →
        </RouterLink>
      </section>
      <section class="panel">
        <div class="panel-header">
          <h3>产品资料</h3>
          <span class="muted">{{ currentDocuments.length }} 份</span>
        </div>
        <p class="notice info">上传成功后，需完成知识入库，资料才能用于 RAG 检索。</p>
        <div class="upload-zone">
          <h3>上传产品资料</h3>
          <p class="muted">PDF / TXT / MD / DOCX，单个文件不超过 20 MB。可一次选择多个文件。</p>
          <input
            type="file"
            multiple
            accept=".pdf,.txt,.md,.docx"
            aria-label="选择产品资料"
            :disabled="busy.has('upload')"
            @change="chooseFiles"
          />
          <div v-for="item in queue" :key="item.id" class="file-row">
            <div class="row between">
              <span>
                {{ item.file.name }}
                <small class="muted">{{ formatSize(item.file.size) }}</small>
              </span>
              <span>
                {{
                  {
                    queued: '等待上传',
                    uploading: '上传中…',
                    done: '上传成功',
                    error: '上传失败',
                    invalid: '文件无效',
                  }[item.state]
                }}
              </span>
            </div>
            <progress v-if="item.state === 'uploading'" aria-label="文件上传处理中" />
            <p v-if="item.error" class="error-text">{{ item.error }}</p>
            <button
              v-if="item.state !== 'uploading'"
              class="button ghost small"
              @click="queue = queue.filter((i) => i.id !== item.id)"
            >
              移除选择
            </button>
          </div>
          <button
            class="button section-gap"
            :disabled="
              busy.has('upload') || !queue.some((i) => ['queued', 'error'].includes(i.state))
            "
            @click="upload"
          >
            {{ busy.has('upload') ? '上传处理中…' : '上传所选文件 / 重试失败项' }}
          </button>
        </div>
        <p v-if="documentLoading" class="loading-line">读取资料…</p>
        <p v-if="documentErrors[`list:${selected}`]" class="error-text">
          {{ documentErrors[`list:${selected}`] }}
        </p>
        <div v-for="document in currentDocuments" :key="document.id" class="file-row">
          <div class="row between">
            <div>
              <strong>{{ document.filename }}</strong>
              <p class="muted">
                {{ formatSize(document.size_bytes) }} · {{ displayDate(document.created_at) }}
              </p>
            </div>
            <StatusBadge :status="document.status" type="document" />
          </div>
          <p v-if="busy.has(`index:${document.id}`)" class="loading-line">
            <span class="spinner"></span>
            正在解析、切分和建立向量索引…
          </p>
          <p v-if="documentErrors[document.id]" class="error-text">
            {{ documentErrors[document.id] }}
          </p>
          <div class="row">
            <button
              class="button small"
              :disabled="busy.has(`index:${document.id}`)"
              @click="indexDocument(document)"
            >
              {{ document.status === 'indexed' ? '重新入库' : '建立知识库 / 重试' }}
            </button>
            <button
              class="button ghost small"
              @click="
                action(`download:${document.id}`, async () =>
                  download(await api.products.download(selected, document.id), document.filename),
                )
              "
            >
              下载
            </button>
            <button
              class="button ghost danger small"
              :disabled="busy.has(`index:${document.id}`)"
              @click="confirmation = { product: current, document }"
            >
              删除资料
            </button>
          </div>
        </div>
        <EmptyState
          v-if="!currentDocuments.length && !documentLoading"
          title="尚未上传资料"
          description="产品手册、品牌介绍和使用说明都可以作为知识来源。"
        />
      </section>
    </div>
    <section v-else class="panel">
      <EmptyState
        image="products"
        title="选择一个产品"
        description="在左侧选择产品，或创建第一个产品。"
      />
    </section>
  </div>
  <BaseDialog
    :open="editor"
    :title="editing ? '编辑产品' : '新建产品'"
    :busy="busy.has('save-product')"
    @close="editor = false"
  >
    <form @submit.prevent="saveProduct">
      <label class="field">
        <span class="field-label">产品名称</span>
        <input v-model="form.name" required maxlength="100" />
      </label>
      <label class="field">
        <span class="field-label">产品简介</span>
        <textarea
          v-model="form.description"
          rows="5"
          placeholder="简要描述产品及主要使用场景；详细事实请上传产品资料。"
        />
      </label>
      <div class="row">
        <button class="button primary" :disabled="busy.has('save-product')">
          {{ busy.has('save-product') ? '保存中…' : '保存产品' }}
        </button>
        <button
          class="button"
          type="button"
          :disabled="busy.has('save-product')"
          @click="editor = false"
        >
          取消
        </button>
      </div>
    </form>
  </BaseDialog>
  <BaseDialog
    :open="!!confirmation"
    :title="confirmation?.document ? '删除产品资料' : '删除产品'"
    :busy="busy.has('delete-product')"
    @close="confirmation = null"
  >
    <p v-if="confirmation?.document">
      确认删除资料「{{ confirmation.document.filename }}」及其文件？删除结果以服务端确认的信息为准。
    </p>
    <p v-else>
      确认删除产品「{{
        confirmation?.product.name
      }}」？已有任务关联时不能删除；产品仍有资料时需先删除资料。
    </p>
    <div class="row">
      <button class="button danger" :disabled="busy.has('delete-product')" @click="remove">
        确认删除
      </button>
      <button class="button" :disabled="busy.has('delete-product')" @click="confirmation = null">
        取消
      </button>
    </div>
  </BaseDialog>
</template>
