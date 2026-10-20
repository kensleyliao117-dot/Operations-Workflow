<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, allPages } from '../api/index.js'
import { useTask } from '../composables/useTask.js'
import { action, busy, notify } from '../stores/ui.js'
import { session } from '../stores/session.js'
import { readLocal, writeLocal, userKey } from '../utils/storage.js'
import { displayDate } from '../utils/domain.js'
import CaseSelector from '../components/CaseSelector.vue'
import PromptManager from '../components/PromptManager.vue'
import RuleManager from '../components/RuleManager.vue'
import StatusBadge from '../components/StatusBadge.vue'
import WorkflowStatus from '../components/WorkflowStatus.vue'
import AuthImage from '../components/AuthImage.vue'
import EmptyState from '../components/EmptyState.vue'
import BaseDialog from '../components/BaseDialog.vue'
import ImageModelFields from '../components/ImageModelFields.vue'
import { selectedPlatform, currentPlatform, selectPlatform } from '../stores/platforms.js'
import { analysisModels, loadModels } from '../stores/models.js'
import { defaultImageSize } from '../utils/image_sizes.js'
const route = useRoute(),
  router = useRouter(),
  tracking = useTask()
const { task, workflow, contents } = tracking
const products = ref([]),
  tasks = ref([]),
  prompts = ref([]),
  documents = ref([]),
  loading = ref(true),
  search = ref(''),
  showPrompts = ref(false),
  showRules = ref(false),
  showHistory = ref(false)
const draftKey = () => userKey(session.user?.id, 'generation-config', selectedPlatform.value)
const saved = readLocal(draftKey())
const form = reactive({
  product_id: Number(route.query.product_id) || saved?.product_id || '',
  input_text: route.query.new ? '' : saved?.input_text || '',
  prompt_id: saved?.prompt_id || '',
  analysis_ids: route.query.new ? [] : saved?.analysis_ids || [],
  use_memory: saved?.use_memory ?? true,
  model_id: saved?.model_id || '',
  output_mode: saved?.output_mode || 'text',
  image_style_requirement: saved?.image_style_requirement || '',
  image_count: saved?.image_count || 1,
  image_size: saved?.image_size || defaultImageSize,
  image_model_id: saved?.image_model_id || null,
})
const latest = computed(() => contents.value[0])
const deleteTarget = ref(null)
const deleteBusy = computed(
  () => deleteTarget.value && busy.has(`delete-task:${deleteTarget.value.id}`),
)
function deletionBlocked(item) {
  return ['running', 'publishing'].includes(item.status) || busy.has(`workflow:${item.id}`)
}
async function deleteTask() {
  const item = deleteTarget.value
  if (!item) return
  await action(`delete-task:${item.id}`, async () => {
    try {
      await api.tasks.remove(item.id)
      tasks.value = tasks.value.filter((row) => row.id !== item.id)
      deleteTarget.value = null
      if (task.value?.id === item.id || Number(route.query.task_id) === item.id) await newTask()
      notify('任务及其关联内容、图片、审核、发布和反馈记录已彻底删除')
    } finally {
      await loadTasks()
    }
  })
}
const filteredTasks = computed(() =>
  tasks.value.filter((t) =>
    `${t.id} ${t.input_text}`.toLowerCase().includes(search.value.toLowerCase()),
  ),
)
const product = computed(() => products.value.find((p) => p.id === Number(form.product_id)))
const locked = computed(() => !!task.value && task.value.status !== 'pending')
const running = computed(() => !!task.value && busy.has(`workflow:${task.value.id}`))
watch(analysisModels, (rows) => {
  if (
    form.model_id &&
    !rows.some((model) => model.id === form.model_id && model.is_active !== false)
  )
    form.model_id = ''
})
watch(
  form,
  (value) => {
    if (session.user) writeLocal(draftKey(), JSON.parse(JSON.stringify(value)))
  },
  {
    deep: true,
    flush: 'sync',
  },
)
async function loadPrompts() {
  const platform = selectedPlatform.value
  const values = await allPages(api.prompts.list, { platform, is_active: true })
  if (platform === selectedPlatform.value) prompts.value = values
}
async function loadTasks() {
  const platform = selectedPlatform.value
  const rows = await allPages(api.tasks.list, { platform_name: platform, include_deleted: true })
  if (platform === selectedPlatform.value) tasks.value = rows
}
async function chooseTask(id) {
  tracking.reset()
  await tracking.refresh(id)
  if (task.value) {
    selectPlatform(task.value.platform_name)
    form.product_id = task.value.product_id
    form.input_text = task.value.input_text
    const metadata = latest.value?.generation_metadata
    if (metadata) {
      form.prompt_id = metadata.prompt_id || ''
      form.analysis_ids = (metadata.analysis_references || []).map((a) => a.analysis_id)
      form.use_memory = metadata.memory_status !== 'disabled'
      form.model_id = metadata.generation_model_profile?.id || ''
      form.output_mode = metadata.output_mode || 'text'
    }
  }
}
async function createTask() {
  if (!currentPlatform.value.workflow || !currentPlatform.value.generation) {
    notify('当前平台的生成流程尚未接入，可先保存 Prompt 和规则配置', 'error')
    return
  }
  await action('create-task', async () => {
    const value = await api.tasks.create({
      product_id: Number(form.product_id),
      platform_name: selectedPlatform.value,
      task_type: 'generate',
      input_text: form.input_text.trim(),
    })
    await router.replace({ path: '/generate', query: { task_id: value.id } })
    await loadTasks()
    notify('任务已创建，确认配置后可启动生成')
  })
}
async function generate(retry = false) {
  if (!currentPlatform.value.workflow || task.value?.platform_name !== selectedPlatform.value)
    return
  const id = task.value?.id
  if (!id) return
  await action(`workflow:${id}`, async () => {
    const timer = setTimeout(() => {
      if (task.value?.id === id) tracking.refresh(id)
    }, 2000)
    try {
      if (retry) await api.workflow.retry(id)
      else
        await api.workflow.start(id, {
          analysis_ids: [...form.analysis_ids],
          prompt_id: form.prompt_id ? Number(form.prompt_id) : null,
          use_memory: form.use_memory,
          model_id: form.model_id || null,
          output_mode: form.output_mode,
          image_style_requirement: form.image_style_requirement,
          image_count: Number(form.image_count),
          image_size: form.image_size,
          image_model_id: form.image_model_id || null,
        })
      notify(`任务 #${id} 工作流已返回，请查看实际任务状态和审核结果`)
    } finally {
      clearTimeout(timer)
      if (task.value?.id === id) await tracking.refresh(id)
      await loadTasks()
    }
  })
}
async function newTask() {
  showHistory.value = false
  tracking.reset()
  form.input_text = ''
  form.analysis_ids = []
  await router.replace('/generate')
}
function openTask(item) {
  showHistory.value = false
  router.replace({ path: '/generate', query: { task_id: item.id } })
}
watch(selectedPlatform, async () => {
  showPrompts.value = false
  showRules.value = false
  showHistory.value = false
  if (!session.user) return
  if (task.value?.platform_name !== selectedPlatform.value) {
    tracking.reset()
    const saved = readLocal(draftKey())
    Object.assign(form, {
      product_id: saved?.product_id || '',
      input_text: saved?.input_text || '',
      prompt_id: saved?.prompt_id || '',
      analysis_ids: saved?.analysis_ids || [],
      use_memory: saved?.use_memory ?? true,
      model_id: saved?.model_id || '',
      output_mode: saved?.output_mode || 'text',
      image_style_requirement: saved?.image_style_requirement || '',
      image_count: saved?.image_count || 1,
      image_size: saved?.image_size || defaultImageSize,
      image_model_id: saved?.image_model_id || null,
    })
  }
  prompts.value = []
  tasks.value = []
  await action('platform-generation', async () => {
    await Promise.all([loadTasks(), loadPrompts(), loadModels()])
  })
})
watch(
  () => route.query.new,
  (value) => {
    if (value) newTask()
  },
)
watch(
  () => route.query.task_id,
  (value) => {
    if (Number.isSafeInteger(Number(value)) && Number(value) > 0) chooseTask(Number(value))
  },
)
watch(
  () => form.product_id,
  async (value) => {
    const id = Number(value)
    documents.value = []
    if (id)
      try {
        const rows = await allPages((params) => api.products.documents(id, params))
        if (id === Number(form.product_id)) documents.value = rows
      } catch (e) {
        notify(e.message, 'error')
      }
  },
)
onMounted(async () => {
  loading.value = true
  if (route.query.new) await newTask()
  await action('generate-load', async () => {
    products.value = await allPages(api.products.list)
    await Promise.all([loadTasks(), loadPrompts(), loadModels()])
    if (Number(route.query.task_id) > 0) await chooseTask(Number(route.query.task_id))
    else if (form.product_id)
      documents.value = await allPages((params) => api.products.documents(form.product_id, params))
  })
  loading.value = false
})
</script>
<template>
  <div class="page-heading">
    <div>
      <p class="eyebrow">CONTENT STUDIO</p>
      <h1>让想法，成为内容。</h1>
      <p>选择产品，写下需求。从一份有据可依的草稿开始。</p>
    </div>
    <div class="row">
      <button class="button" @click="showPrompts = true">Prompt 模板</button>
      <RouterLink class="button ghost" :to="{ path: '/settings', query: { section: 'rules' } }">
        规则设置
      </RouterLink>
      <button class="button ghost" @click="showHistory = true">任务历史</button>
    </div>
  </div>
  <p v-if="!currentPlatform.workflow" class="notice info" role="status">
    {{ currentPlatform.name }} 已预留平台入口，采集、生成、审核和发布流程待接入。可以先管理此平台的
    Prompt 和规则；不会调用小红书流程。
  </p>
  <p class="muted small-text">
    基于爆款案例生成的文案和配图不保证成为爆款，仍需人工调整。产品事实以产品知识资料为准。
  </p>
  <p v-if="loading" class="loading-line">
    <span class="spinner"></span>
    加载工作空间…
  </p>
  <BaseDialog :open="showHistory" title="任务历史" @close="showHistory = false">
    <section class="panel">
      <div class="panel-header">
        <h3>任务历史</h3>
        <button class="button ghost small" @click="newTask">新建</button>
      </div>
      <input v-model="search" placeholder="搜索任务" aria-label="搜索任务" class="search" />
      <div class="list scroll-list">
        <div v-for="item in filteredTasks" :key="item.id" class="row">
          <button
            class="list-item"
            style="flex: 1; min-width: 0"
            :class="{ active: item.id === task?.id }"
            :disabled="item.is_deleted"
            @click="openTask(item)"
          >
            <strong>#{{ item.id }} {{ item.input_text.slice(0, 28) }}</strong>
            <p>
              {{ products.find((p) => p.id === item.product_id)?.name || '产品' }} ·
              {{ displayDate(item.created_at) }}
            </p>
            <span v-if="item.is_deleted" class="chip">已移除 · 可继续彻底删除</span>
            <StatusBadge v-else :status="item.status" />
          </button>
          <button
            class="button ghost small"
            :aria-label="`删除任务 #${item.id}`"
            :disabled="deletionBlocked(item)"
            :title="deletionBlocked(item) ? '生成或发布完成后可删除' : '删除任务'"
            @click="deleteTarget = item"
          >
            删除
          </button>
        </div>
      </div>
      <p v-if="!tasks.length" class="muted">尚无任务，先填写运营需求。</p>
    </section>
  </BaseDialog>
  <BaseDialog
    :open="!!deleteTarget"
    title="删除任务"
    :busy="!!deleteBusy"
    @close="!deleteBusy && (deleteTarget = null)"
  >
    <p>
      确认删除任务 #{{
        deleteTarget?.id
      }}？将彻底删除所有内容版本、生成图片、审核、发布记录、工作流进度及关联反馈与经验，无法恢复。产品资料和共用案例、Prompt、模型配置保留。不会删除小红书平台上的真实帖子。
    </p>
    <div class="row section-gap">
      <button class="button" :disabled="deleteBusy" @click="deleteTarget = null">取消</button>
      <button class="button danger" :disabled="deleteBusy" @click="deleteTask">
        {{ deleteBusy ? '删除中…' : '确认删除' }}
      </button>
    </div>
  </BaseDialog>
  <div class="generation-workspace section-gap">
    <div class="stack">
      <WorkflowStatus
        v-if="task"
        :task="task"
        :workflow="workflow"
        :loading="tracking.loading.value"
        :error="tracking.error.value"
        @refresh="tracking.refresh()"
      />
      <details class="panel composer-panel" :open="!latest">
        <summary class="composer-summary">{{ latest ? '查看本次生成配置' : '本次创作' }}</summary>
        <div class="panel-header">
          <h3>{{ task ? `任务 #${task.id}` : '想为产品创作什么内容？' }}</h3>
          <span class="chip">{{ currentPlatform.name }}</span>
        </div>
        <form @submit.prevent="createTask">
          <label class="field">
            <span class="field-label">选择产品</span>
            <select v-model="form.product_id" aria-label="选择产品" required :disabled="!!task">
              <option value="">选择本次运营的产品</option>
              <option v-for="item in products" :key="item.id" :value="item.id">
                {{ item.name }}
              </option>
            </select>
          </label>
          <template v-if="product">
            <p class="muted">{{ product.description }}</p>
            <p class="notice info">
              {{ documents.length }} 份资料，{{
                documents.filter((d) => d.status === 'indexed').length
              }}
              份已入库。
              <RouterLink :to="{ path: '/products' }">管理资料</RouterLink>
            </p>
          </template>
          <p v-if="!products.length && !loading" class="notice">
            请先到
            <RouterLink to="/products">产品管理</RouterLink>
            创建产品并上传资料。
          </p>
          <label class="field">
            <span class="field-label">本次运营需求</span>
            <textarea
              v-model="form.input_text"
              required
              maxlength="2000"
              rows="5"
              :disabled="!!task"
              placeholder="例如：为通勤人群介绍这款产品，语气自然，突出适用场景，正文约300字，不编造体验。"
            />
            <small class="muted">{{ form.input_text.length }}/2000 · 任务创建后保留原始需求</small>
          </label>
          <label class="field">
            <span class="field-label">生成内容</span>
            <select v-model="form.output_mode" aria-label="生成内容" :disabled="locked || running">
              <option value="text">仅生成文案</option>
              <option value="images">仅生成图片</option>
              <option value="text_images">生成文案和图片</option>
            </select>
            <small class="muted">
              文案与图片可分别生成、审核。另一部分可在平台手工补充，再登记实际发布结果。
            </small>
          </label>
          <div v-if="form.output_mode !== 'text'" class="stack">
            <label class="field">
              <span class="field-label">图片风格补充</span>
              <textarea
                v-model="form.image_style_requirement"
                maxlength="1000"
                :disabled="locked || running"
              />
            </label>
            <label class="field">
              <span class="field-label">图片数量</span>
              <select v-model="form.image_count" :disabled="locked || running">
                <option v-for="n in 4" :key="n" :value="n">{{ n }} 张</option>
              </select>
            </label>
            <ImageModelFields
              v-model="form.image_model_id"
              v-model:size="form.image_size"
              :disabled="locked || running"
            />
          </div>
          <CaseSelector
            v-if="currentPlatform.collection"
            v-model="form.analysis_ids"
            :disabled="locked || running"
          />
          <details class="advanced-options" :open="!!form.prompt_id">
            <summary>风格与生成偏好</summary>
            <label class="field section-gap">
              <span class="field-label">文案生成模型</span>
              <select
                v-model="form.model_id"
                aria-label="文案生成模型"
                :disabled="locked || running"
              >
                <option value="">使用账号默认模型</option>
                <option
                  v-for="model in analysisModels"
                  :key="model.id"
                  :value="model.id"
                  :disabled="!model.ready || model.is_active === false"
                >
                  {{ model.name || model.model }} · {{ model.provider
                  }}{{ model.is_default ? ' · 默认' : '' }}
                </option>
              </select>
              <small class="muted">可在设置 → 模型连接中新增供应商或更换默认模型。</small>
            </label>
            <label class="field section-gap">
              <span class="field-label">生成 Prompt</span>
              <select v-model="form.prompt_id" aria-label="生成 Prompt" :disabled="locked">
                <option value="">不应用额外模板</option>
                <option v-for="prompt in prompts" :key="prompt.id" :value="prompt.id">
                  {{ prompt.name }} · v{{ prompt.version }}
                </option>
              </select>
            </label>
            <p v-if="form.prompt_id" class="preserve notice info">
              {{ prompts.find((p) => p.id === Number(form.prompt_id))?.prompt_content }}
            </p>
            <label class="check-row">
              <input v-model="form.use_memory" type="checkbox" :disabled="locked" />
              检索当前产品的历史运营经验
            </label>
          </details>
          <div class="row section-gap">
            <button
              v-if="!task"
              class="button primary"
              :disabled="busy.has('create-task') || !products.length || !currentPlatform.workflow"
            >
              {{ busy.has('create-task') ? '创建中…' : '创建任务' }}
            </button>
            <button
              v-if="task?.status === 'pending' && !workflow"
              type="button"
              class="button primary"
              :disabled="running"
              @click="generate()"
            >
              {{
                running
                  ? '工作流执行中…'
                  : form.output_mode === 'images'
                    ? '启动图片生成'
                    : form.output_mode === 'text_images'
                      ? '启动文案和图片生成'
                      : '启动文案生成'
              }}
            </button>
            <button
              v-if="
                task?.status === 'failed' && task.failed_from_status !== 'publishing' && workflow
              "
              type="button"
              class="button"
              :disabled="running"
              @click="generate(true)"
            >
              重试工作流
            </button>
          </div>
        </form>
      </details>
    </div>
    <aside v-if="latest" class="generated-result stack">
      <section class="panel">
        <div class="panel-header">
          <h3>最新内容</h3>
          <span v-if="latest" class="chip">v{{ latest.version }}</span>
        </div>
        <template v-if="latest">
          <div class="copy-preview">
            <h3>{{ latest.title }}</h3>
            <p>{{ latest.body }}</p>
            <div class="row">
              <span v-for="tag in latest.tags" :key="tag" class="chip">#{{ tag }}</span>
            </div>
          </div>
          <div v-if="latest.image_assets?.length" class="images section-gap">
            <AuthImage
              v-for="(_, index) in latest.image_assets"
              :key="`${latest.id}:${index}`"
              :content-id="latest.id"
              :index="index"
            />
          </div>
          <p v-else class="muted section-gap">
            素材已保存，可分别审核、下载，或继续补充文案和配图。
          </p>
          <p class="muted section-gap">
            运营经验：{{
              {
                used: '已引用',
                no_matches: '暂无相关经验',
                unavailable: '经验库暂不可用',
                disabled: '未启用',
              }[latest.generation_metadata?.memory_status] || '查看生成依据'
            }}
          </p>
          <details>
            <summary>生成依据与资料引用</summary>
            <p v-for="source in latest.generation_metadata?.sources" :key="source.chunk_id">
              {{ source.citation }} {{ source.filename }}
            </p>
            <pre>{{ JSON.stringify(latest.generation_metadata, null, 2) }}</pre>
          </details>
          <RouterLink
            class="button primary section-gap"
            :to="{ path: '/review', query: { task_id: task.id } }"
          >
            前往审核 →
          </RouterLink>
        </template>
        <EmptyState
          v-else
          title="草稿将在这里出现"
          description="创建任务并启动工作流后，可查看实际生成结果。"
        />
      </section>
    </aside>
  </div>
  <PromptManager
    :platform="selectedPlatform"
    :open="showPrompts"
    @close="showPrompts = false"
    @saved="
      async (value) => {
        await loadPrompts()
        if (value?.is_active && !locked) form.prompt_id = value.id
        else if (!locked && !prompts.some((p) => p.id === Number(form.prompt_id)))
          form.prompt_id = ''
      }
    "
  />
  <RuleManager :platform="selectedPlatform" :open="showRules" @close="showRules = false" />
</template>
