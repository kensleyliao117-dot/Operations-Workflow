<script setup>
import { computed, nextTick, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { onBeforeRouteLeave, useRoute, useRouter } from 'vue-router'
import { api, allPages } from '../api/index.js'
import { useTask } from '../composables/useTask.js'
import { action, busy, notify } from '../stores/ui.js'
import { session } from '../stores/session.js'
import {
  canEdit,
  canApprove,
  reviewSubmissionAction,
  ruleLabels,
  displayDate,
  splitTags,
} from '../utils/domain.js'
import { userKey, readLocal, writeLocal } from '../utils/storage.js'
import StatusBadge from '../components/StatusBadge.vue'
import WorkflowStatus from '../components/WorkflowStatus.vue'
import EmptyState from '../components/EmptyState.vue'
import AuthImage from '../components/AuthImage.vue'
import BaseDialog from '../components/BaseDialog.vue'
import ImageModelFields from '../components/ImageModelFields.vue'
import { platformAdapters } from '../config/platform_adapters.js'
import { selectedPlatform, currentPlatform, selectPlatform } from '../stores/platforms.js'
const route = useRoute(),
  router = useRouter(),
  tracking = useTask()
const { task, workflow, contents } = tracking
const tasks = ref([]),
  products = ref([]),
  status = ref(''),
  search = ref(''),
  current = ref(null),
  check = ref(null),
  loading = ref(true),
  reviewDecision = ref(null),
  reviewIntent = ref(null),
  comment = ref(''),
  reviewed = ref(false),
  histories = ref([]),
  draftRestored = ref(false),
  imageFailed = ref(false),
  imageConfirm = ref(false),
  showHistory = ref(false),
  imageEditorOpen = ref(false)
const imageIntent = ref(null)
function downloadText() {
  const text = [
    draft.title,
    draft.body,
    splitTags(draft.tags)
      .map((tag) => `#${tag}`)
      .join(' '),
  ]
    .filter(Boolean)
    .join('\n\n')
  const url = URL.createObjectURL(new Blob([text], { type: 'text/plain;charset=utf-8' }))
  const link = document.createElement('a')
  link.href = url
  link.download = `content-${current.value.id}-v${current.value.version}.txt`
  link.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
const draft = reactive({ title: '', body: '', tags: '' }),
  imageForm = reactive({
    style_requirement: '',
    count: 1,
    size: '1728*2368',
    replace_existing: false,
    image_model_id: null,
  })
const latest = computed(() => contents.value[0])
const dirty = computed(
  () =>
    !!current.value &&
    (draft.title !== current.value.title ||
      draft.body !== current.value.body ||
      JSON.stringify(splitTags(draft.tags)) !== JSON.stringify(current.value.tags || [])),
)
const editable = computed(() => canEdit(task.value, current.value, latest.value))
const submissionAction = computed(() =>
  reviewSubmissionAction(task.value, current.value, latest.value, workflow.value, dirty.value),
)
const submissionLabel = computed(() => {
  if (dirty.value) return '保存并提交审核'
  if (submissionAction.value === 'submit_changes') return '提交已保存版本审核'
  if (submissionAction.value === 'submit_review') return '提交已保存版本审核'
  if (submissionAction.value === 'recheck' || submissionAction.value === 'check')
    return '重新检查并提交审核'
  return task.value?.status === 'pending_review' ? '已提交审核' : '保存并提交审核'
})
const approvable = computed(() =>
  canApprove(task.value, current.value, latest.value, check.value, dirty.value),
)
const filtered = computed(() =>
  tasks.value.filter(
    (t) =>
      (!status.value || t.status === status.value) &&
      `${t.id} ${t.input_text}`.toLowerCase().includes(search.value.toLowerCase()),
  ),
)
const draftKey = (id) => userKey(session.user?.id, 'content-draft', id)
let hydrating = false,
  checkTurn = 0,
  closed = false
function setContent(content) {
  hydrating = true
  if (current.value?.id !== content.id) imageEditorOpen.value = false
  current.value = content
  draftRestored.value = false
  const saved = readLocal(draftKey(content.id))
  const values = saved?.version === content.version && editable.value ? saved : content
  Object.assign(draft, {
    title: values.title,
    body: values.body,
    tags: Array.isArray(values.tags) ? values.tags.join(' ') : values.tags,
  })
  draftRestored.value = values === saved
  hydrating = false
  loadChecks(content.id)
}
watch(
  draft,
  (value) => {
    if (hydrating || !current.value || !editable.value) return
    if (dirty.value) {
      if (!writeLocal(draftKey(current.value.id), { ...value, version: current.value.version }))
        notify('浏览器无法保存本机草稿，请及时保存内容版本。', 'error')
    } else localStorage.removeItem(draftKey(current.value.id))
  },
  { deep: true, flush: 'sync' },
)
async function loadChecks(id) {
  const turn = ++checkTurn
  check.value = null
  try {
    const rows = await api.rules.checks(id)
    if (turn === checkTurn && !closed && current.value?.id === id) check.value = rows[0] || null
  } catch (e) {
    if (!closed) notify(e.message, 'error')
  }
}
async function loadTasks() {
  const platform = selectedPlatform.value
  const rows = await allPages(api.tasks.list, { platform_name: platform })
  if (platform === selectedPlatform.value) tasks.value = rows
}
async function refresh() {
  if (!task.value) return
  const id = task.value.id
  await tracking.refresh(id)
  if (task.value?.id !== id) return
  histories.value = await api.reviews.list(id)
  if (!current.value || (!dirty.value && current.value.id !== latest.value?.id)) {
    if (latest.value) setContent(latest.value)
  }
  if (current.value) await loadChecks(current.value.id)
  await loadTasks()
}
async function selectTask(id) {
  tracking.reset()
  current.value = null
  check.value = null
  histories.value = []
  ++checkTurn
  await tracking.refresh(id)
  if (task.value) selectPlatform(task.value.platform_name)
  if (latest.value) setContent(latest.value)
  if (task.value) histories.value = await api.reviews.list(id)
}
function chooseTask(item) {
  if (!dirty.value || window.confirm('有未提交的修改，已尽力保存到本机草稿。确认切换任务？')) {
    showHistory.value = false
    router.replace({ path: '/review', query: { task_id: item.id } })
  }
}
function beforePlatformChange(event) {
  if (dirty.value && !window.confirm('当前修改尚未保存到后端，已尽力保留本机草稿。确认切换平台？'))
    event.preventDefault()
}
watch(selectedPlatform, () => {
  if (!session.user) return
  showHistory.value = false
  if (task.value?.platform_name !== selectedPlatform.value) {
    tracking.reset()
    current.value = null
    check.value = null
    histories.value = []
    ++checkTurn
  }
  tasks.value = []
  action('review-platform', loadTasks)
})
function selectVersion(event) {
  const id = Number(event.target.value)
  if (dirty.value && !window.confirm('当前修改尚未保存到后端，确认切换版本？')) {
    event.target.value = current.value.id
    return
  }
  const value = contents.value.find((c) => c.id === id)
  if (value) setContent(value)
}
async function saveContent() {
  if (!editable.value) throw new Error('只能修改允许编辑任务的最新版本，请先刷新状态')
  if (!dirty.value) return current.value
  if (!draft.title.trim() && !draft.body.trim() && !current.value.image_assets?.length)
    throw new Error('需要保留文案或图片素材')
  const previous = current.value.id
  const taskId = current.value.task_id
  const result = await api.content.update(previous, {
    title: draft.title,
    body: draft.body,
    tags: splitTags(draft.tags),
  })
  localStorage.removeItem(draftKey(previous))
  if (task.value?.id === taskId) {
    await tracking.refresh(taskId)
    if (task.value?.id === taskId) setContent(result)
  }
  await loadTasks()
  notify(`已保存新版本 v${result.version}，需要重新提交审核`)
  return result
}
async function submitChanges() {
  if (!submissionAction.value) return
  const taskId = current.value.task_id
  try {
    const content = await saveContent()
    // 保存后重新读取工作流，区分新版本提交与当前版本重新检查。
    const [savedTask, savedWorkflow] = await Promise.all([
      api.tasks.get(taskId),
      api.workflow.get(taskId).catch((e) => {
        if (e.status === 404) return null
        throw e
      }),
    ])
    const nextAction = reviewSubmissionAction(savedTask, content, content, savedWorkflow)
    if (savedWorkflow) {
      if (!nextAction) {
        if (
          savedTask.status === 'pending_review' &&
          savedWorkflow.status === 'waiting_review' &&
          savedWorkflow.content_id === content.id
        ) {
          notify('当前版本已在等待人工审核')
          return
        }
        throw new Error('任务或工作流状态已变化，请刷新后再提交')
      }
      const result = await api.workflow.resume(taskId, nextAction)
      if (result.task_status !== 'pending_review' || result.status !== 'waiting_review') {
        notify('内容已保存，请刷新查看实际工作流状态', 'info')
        return
      }
    } else {
      if (savedTask.status === 'rejected') await api.tasks.status(taskId, 'running')
      if (savedTask.status !== 'pending_review') await api.tasks.status(taskId, 'pending_review')
    }
    notify('最新版本已提交审核')
  } finally {
    if (task.value?.id === taskId) await refresh()
  }
}
async function recheck() {
  if (dirty.value) throw new Error('请先保存修改，再检查已保存版本')
  try {
    if (workflow.value)
      await api.workflow.resume(
        task.value.id,
        current.value.version > workflow.value.content_version ? 'submit_changes' : 'recheck',
      )
    else await api.rules.check(current.value.id, true)
  } finally {
    await refresh()
  }
}
function openReview(decision) {
  reviewIntent.value = {
    taskId: task.value.id,
    contentId: current.value.id,
    version: current.value.version,
    title: current.value.title,
    imageCount: current.value.image_assets?.length || 0,
    decision,
    hasWorkflow: !!workflow.value,
  }
  reviewDecision.value = decision
  comment.value = ''
  reviewed.value = false
}
async function review() {
  const intent = reviewIntent.value
  const decision = intent.decision
  await action('human-review', async () => {
    if (
      current.value?.id !== intent.contentId ||
      latest.value?.id !== intent.contentId ||
      dirty.value
    ) {
      throw new Error('确认期间内容版本已变化，请重新查看后审核')
    }
    await api.reviews.create({
      task_id: intent.taskId,
      content_id: intent.contentId,
      status: decision,
      comment: comment.value.trim() || null,
    })
    reviewDecision.value = null
    try {
      if (intent.hasWorkflow) await api.workflow.resume(intent.taskId, 'review')
      notify(decision === 'approved' ? '人工审核通过' : '已退回修改')
    } finally {
      await refresh()
    }
  })
}
async function generateImages() {
  imageConfirm.value = false
  const { contentId: id, taskId } = imageIntent.value
  await action(`images:${taskId}`, async () => {
    const timer = setTimeout(() => {
      if (task.value?.id === taskId) tracking.refresh(taskId)
    }, 2000)
    try {
      const value = await api.content.generateImages(id, {
        ...imageForm,
        count: Number(imageForm.count),
      })
      if (task.value?.id === taskId) {
        await tracking.refresh(taskId)
        if (task.value?.id === taskId) setContent(value.content)
      }
      imageFailed.value = value.review_status === 'submission_failed'
      notify(
        imageFailed.value
          ? '配图已保存，提交审核失败；可以单独重试提交。'
          : '配图与新内容版本已保存，请重新人工核对',
        imageFailed.value ? 'info' : 'success',
      )
    } finally {
      clearTimeout(timer)
      await refresh()
    }
  })
}
function askImages() {
  if (dirty.value) {
    notify('请先保存文字修改', 'error')
    return
  }
  imageIntent.value = { contentId: current.value.id, taskId: task.value.id }
  imageConfirm.value = true
}
function beforeUnload(event) {
  if (dirty.value) {
    event.preventDefault()
    event.returnValue = ''
  }
}
onBeforeRouteLeave(
  () =>
    !session.token ||
    !dirty.value ||
    window.confirm('文案修改尚未保存到后端，确认离开？本机草稿可在当前账号中恢复。'),
)
watch(
  () => route.query.task_id,
  (id) => {
    if (Number.isSafeInteger(Number(id)) && Number(id) > 0) selectTask(Number(id))
  },
)
watch(contents, (rows) => {
  if (!current.value && rows[0]) setContent(rows[0])
})
onMounted(async () => {
  window.addEventListener('beforeunload', beforeUnload)
  window.addEventListener('platform-before-change', beforePlatformChange)
  await action('review-load', async () => {
    ;[products.value] = await Promise.all([allPages(api.products.list), loadTasks()])
    const id = Number(route.query.task_id)
    if (id > 0) await selectTask(id)
  })
  loading.value = false
})
onUnmounted(() => {
  closed = true
  ++checkTurn
  window.removeEventListener('beforeunload', beforeUnload)
  window.removeEventListener('platform-before-change', beforePlatformChange)
})
</script>
<template>
  <div class="page-heading">
    <div>
      <p class="eyebrow">REVIEW & PUBLISH</p>
      <h1>审核发布</h1>
      <p>检查每一版文案与配图，确认之后再发布。</p>
    </div>
    <div class="row">
      <button class="button ghost" @click="showHistory = true">选择任务</button>
      <button
        class="button"
        @click="
          action('review-refresh', async () => {
            await loadTasks()
            await refresh()
          })
        "
      >
        刷新状态
      </button>
    </div>
  </div>
  <p v-if="!currentPlatform.workflow" class="notice info">
    {{ currentPlatform.name }} 的审核与发布适配待接入。
  </p>
  <BaseDialog :open="showHistory" title="内容任务" @close="showHistory = false">
    <section class="panel">
      <h3>内容任务</h3>
      <input v-model="search" class="search" placeholder="搜索任务" aria-label="搜索审核任务" />
      <select v-model="status" aria-label="筛选任务状态" class="search">
        <option value="">所有状态</option>
        <option value="pending_review">待审核</option>
        <option value="running">处理中 / 待修改</option>
        <option value="rejected">已退回</option>
        <option value="approved">审核通过</option>
        <option value="publishing">发布中</option>
        <option value="completed">已完成</option>
        <option value="failed">失败</option>
      </select>
      <p v-if="loading" class="loading-line">加载任务…</p>
      <div class="list scroll-list">
        <button
          v-for="item in filtered"
          :key="item.id"
          class="list-item"
          :class="{ active: task?.id === item.id }"
          @click="chooseTask(item)"
        >
          <strong>#{{ item.id }} {{ item.input_text.slice(0, 24) }}</strong>
          <p>
            {{ products.find((p) => p.id === item.product_id)?.name || '产品' }} ·
            {{ displayDate(item.created_at) }}
          </p>
          <StatusBadge :status="item.status" />
        </button>
      </div>
      <p v-if="!loading && !filtered.length" class="muted">暂无匹配任务</p>
    </section>
  </BaseDialog>
  <nav v-if="current" class="section-tabs" aria-label="审核步骤">
    <a href="#content-editor">01 文案与配图</a>
    <a href="#review-checks">02 风险与审核</a>
    <a href="#publish-section">03 发布与反馈</a>
  </nav>
  <details v-if="task" class="workflow-disclosure">
    <summary>任务 #{{ task.id }} · 查看工作流状态</summary>
    <WorkflowStatus
      v-if="task"
      :task="task"
      :workflow="workflow"
      :loading="tracking.loading.value"
      :error="tracking.error.value"
      @refresh="action('refresh-task', refresh)"
    />
  </details>
  <div class="review-workspace section-gap">
    <div class="stack">
      <section v-if="current" id="content-editor" class="panel">
        <div class="panel-header">
          <h3>内容预览与编辑</h3>
          <select
            class="version-select"
            :value="current.id"
            aria-label="内容版本"
            @change="selectVersion"
          >
            <option v-for="value in contents" :key="value.id" :value="value.id">
              v{{ value.version }}{{ value.id === latest?.id ? ' · 最新' : ' · 历史只读' }}
            </option>
          </select>
        </div>
        <p class="muted">
          {{ products.find((p) => p.id === task?.product_id)?.name }} · Content #{{ current.id }} ·
          {{ displayDate(current.created_at) }}
        </p>
        <p v-if="draftRestored" class="notice info">
          已恢复此版本的本机未提交草稿。点击保存后才会写入后端。
        </p>
        <p v-if="current.id !== latest?.id" class="notice">
          当前为历史版本，仅供查看；审核和发布始终针对最新版本。
        </p>
        <p v-if="dirty && current.id !== latest?.id" class="notice error">
          后端已有新版本，不能覆盖保存。请先复制需要保留的修改，再查看最新版本。
        </p>
        <label class="field">
          <span class="field-label">标题</span>
          <input v-model="draft.title" maxlength="300" :readonly="!editable" />
        </label>
        <label class="field">
          <span class="field-label">正文</span>
          <textarea v-model="draft.body" class="review-editor" :readonly="!editable" />
        </label>
        <label class="field">
          <span class="field-label">话题（空格或逗号分隔）</span>
          <input v-model="draft.tags" :readonly="!editable" />
        </label>
        <p class="muted">
          {{ dirty ? '有未提交修改 · 本机草稿不等于后端内容版本' : '当前显示数据库已保存版本' }}
        </p>
        <button class="button small" :disabled="!draft.title && !draft.body" @click="downloadText">
          下载文案
        </button>
        <p v-if="editable && !dirty" class="muted small-text">
          {{
            task.status === 'pending_review'
              ? '当前版本已提交，请在下方完成人工审核。'
              : task.status === 'rejected' && !submissionAction
                ? '当前版本已被退回，请先修改内容并保存新版本。'
                : '内容尚无修改，无需重复保存新版本；可直接提交已保存素材。风险检查仅作提示。'
          }}
        </p>
        <div v-if="editable" class="row">
          <button
            class="button"
            :disabled="!dirty || busy.has('save-content') || busy.has('submit-review')"
            @click="action('save-content', saveContent)"
          >
            保存修改 · 新版本
          </button>
          <button
            class="button primary"
            :disabled="busy.has('save-content') || busy.has('submit-review') || !submissionAction"
            @click="action('submit-review', submitChanges)"
          >
            {{ busy.has('submit-review') ? '保存并检查中…' : submissionLabel }}
          </button>
        </div>
        <hr class="section-divider" />
        <h3>
          配图
          <span class="muted">{{ current.image_assets?.length || 0 }} 张</span>
        </h3>
        <div class="images">
          <AuthImage
            v-for="(_, index) in current.image_assets"
            :key="`${current.id}:${index}`"
            :content-id="current.id"
            :index="index"
          />
        </div>
        <p v-if="!current.image_assets?.length" class="muted">
          尚未生成配图，已保存的文案不受生图失败影响。
        </p>
        <details v-if="editable" :open="imageEditorOpen">
          <summary @click.prevent="imageEditorOpen = !imageEditorOpen">生成 / 重新生成配图</summary>
          <label class="field section-gap">
            <span class="field-label">画面风格补充</span>
            <textarea
              v-model="imageForm.style_requirement"
              rows="3"
              maxlength="1000"
              placeholder="例如：自然光、简洁构图，留出标题空间。不要虚构产品外观。"
            />
          </label>
          <div class="row">
            <label class="field grow">
              <span class="field-label">数量</span>
              <select v-model="imageForm.count">
                <option v-for="n in 4" :key="n" :value="n">{{ n }} 张</option>
              </select>
            </label>
          </div>
          <ImageModelFields
            v-model="imageForm.image_model_id"
            v-model:size="imageForm.size"
            :disabled="busy.has(`images:${task?.id}`)"
          />
          <label v-if="current.image_assets?.length" class="check-row">
            <input v-model="imageForm.replace_existing" type="checkbox" />
            在新版本中替换现有图片（历史版本保留）
          </label>
          <button
            class="button section-gap"
            :disabled="dirty || busy.has(`images:${task?.id}`)"
            @click="askImages"
          >
            {{ busy.has(`images:${task?.id}`) ? '配图生成中…' : '生成配图' }}
          </button>
        </details>
        <button
          v-if="imageFailed"
          class="button section-gap"
          @click="
            action('resubmit-images', async () => {
              await api.content.submitImages(current.id)
              imageFailed = false
              await refresh()
            })
          "
        >
          重试提交已保存配图审核
        </button>
        <p class="notice section-gap">
          AI 配图可能与真实产品外观、结构或标识存在差异，请逐项核对。规则检查未分析实际图片像素。
        </p>
        <details>
          <summary>查看生成来源</summary>
          <pre>{{ JSON.stringify(current.generation_metadata, null, 2) }}</pre>
        </details>
      </section>
      <section v-else class="panel">
        <EmptyState
          title="选择一条内容任务"
          description="点击上方“选择任务”，查看文案、配图和历史版本。"
        >
          <RouterLink class="button" to="/generate">前往生成内容</RouterLink>
        </EmptyState>
      </section>
    </div>
    <aside class="side-details stack">
      <section v-if="current" id="review-checks" class="panel">
        <div class="panel-header">
          <h3>平台规则检查</h3>
          <button
            class="button small"
            :disabled="
              dirty ||
              current.id !== latest?.id ||
              busy.has('recheck') ||
              !['running', 'pending_review'].includes(task?.status)
            "
            @click="action('recheck', recheck)"
          >
            重新检查
          </button>
        </div>
        <StatusBadge v-if="check" :status="check.status" type="rule" />
        <p v-else class="muted">当前版本尚无检查结果。</p>
        <p v-if="dirty" class="notice section-gap">
          正在编辑，已保存版本的检查结果不能用于当前未保存修改。
        </p>
        <p v-if="check?.error_message" class="error-text">{{ check.error_message }}</p>
        <article v-for="(risk, index) in check?.risks" :key="index" class="risk">
          <strong>
            {{ risk.field }} · {{ { low: '低', medium: '中', high: '高' }[risk.severity] }}风险
          </strong>
          <p>{{ risk.reason }}</p>
          <p>原文：{{ risk.excerpt }}</p>
          <p>建议：{{ risk.suggestion }}</p>
        </article>
        <p v-for="warning in check?.coverage_warnings" :key="warning" class="muted section-gap">
          {{ warning }}
        </p>
        <p class="notice section-gap">
          规则检查仅提供风险提示，不阻止保存、人工审核或发布。是否采用建议由人工决定，不能保证平台审核通过或内容成为爆款。
        </p>
        <hr class="section-divider" />
        <h3>人工审核</h3>
        <div class="row">
          <button
            class="button primary"
            :disabled="!approvable || busy.has('human-review')"
            @click="openReview('approved')"
          >
            通过审核
          </button>
          <button
            class="button"
            :disabled="dirty || task?.status !== 'pending_review' || current.id !== latest?.id"
            @click="openReview('rejected')"
          >
            退回修改
          </button>
        </div>
        <button
          v-if="
            ['approved', 'rejected'].includes(task?.status) && workflow?.stage === 'waiting_review'
          "
          class="button section-gap"
          @click="
            action('resume-reviewed', async () => {
              await api.workflow.resume(task.id, 'review')
              await refresh()
            })
          "
        >
          同步已保存的审核结果
        </button>
        <details v-if="histories.length">
          <summary>审核记录</summary>
          <p v-for="history in histories" :key="history.id" class="section-gap">
            Content #{{ history.content_id }} ·
            {{ history.status === 'approved' ? '通过' : '退回' }} ·
            {{ displayDate(history.created_at) }}
            <br />
            <span class="muted">{{ history.comment }}</span>
          </p>
        </details>
      </section>
      <component
        id="publish-section"
        :is="platformAdapters[task.platform_name].publishing"
        v-if="task && current && platformAdapters[task.platform_name]?.publishing"
        :key="task.id"
        :task="task"
        :content="current"
        :latest="latest"
        :dirty="dirty"
        :workflow="workflow"
        @refresh="action('publish-refresh', refresh)"
      />
    </aside>
  </div>
  <BaseDialog
    :open="!!reviewDecision"
    :title="reviewDecision === 'approved' ? '确认人工审核通过' : '退回修改'"
    :busy="busy.has('human-review')"
    @close="reviewDecision = null"
  >
    <p>
      {{ reviewIntent?.title }} · v{{ reviewIntent?.version }} ·
      {{ reviewIntent?.imageCount || 0 }} 张配图
    </p>
    <form @submit.prevent="review">
      <label class="field">
        <span class="field-label">
          {{ reviewDecision === 'rejected' ? '修改意见（必填）' : '审核备注（选填）' }}
        </span>
        <textarea v-model="comment" rows="3" :required="reviewDecision === 'rejected'" />
      </label>
      <label class="check-row">
        <input v-model="reviewed" type="checkbox" required />
        已核对当前版本的文案、图片和风险提示
      </label>
      <div class="row section-gap">
        <button class="button primary" :disabled="!reviewed || busy.has('human-review')">
          {{ reviewDecision === 'approved' ? '确认通过' : '确认退回' }}
        </button>
        <button
          class="button"
          type="button"
          :disabled="busy.has('human-review')"
          @click="reviewDecision = null"
        >
          取消
        </button>
      </div>
    </form>
  </BaseDialog>
  <BaseDialog :open="imageConfirm" title="确认生成配图" @close="imageConfirm = false">
    <p>
      将生成
      {{ imageForm.count }} 张配图并保存为新的内容版本，需要重新人工审核。模型调用可能产生费用。
    </p>
    <p v-if="imageForm.replace_existing" class="notice">新版本将替换已选图片，历史版本仍可查询。</p>
    <div class="row">
      <button class="button primary" @click="generateImages">确认生图</button>
      <button class="button" @click="imageConfirm = false">取消</button>
    </div>
  </BaseDialog>
</template>
