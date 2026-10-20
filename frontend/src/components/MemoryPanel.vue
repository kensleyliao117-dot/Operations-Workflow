<script setup>
import { ref, computed, reactive, onMounted, onUnmounted } from 'vue'
import { api, allPages } from '../api/index.js'
import { action, busy, notify } from '../stores/ui.js'
import { session } from '../stores/session.js'
import { displayDate } from '../utils/domain.js'
import {
  metricFields,
  metricSources,
  formatObservationAge,
  localDateTime,
} from '../utils/metrics.js'
import { userKey, readLocal, writeLocal } from '../utils/storage.js'
import BaseDialog from './BaseDialog.vue'
const props = defineProps({ record: Object, productId: Number })
const emit = defineEmits(['deleted'])
const deleteTarget = ref(null)
const deleting = computed(() => busy.has('delete-feedback:' + props.record.id))
function askDelete(kind, id) {
  deleteTarget.value = { kind, id, recordId: props.record.id }
}
async function deleteFeedback() {
  const target = deleteTarget.value
  if (!target) return
  await action('delete-feedback:' + target.recordId, async () => {
    if (target.kind === 'all') await api.memory.remove(target.recordId)
    else if (target.kind === 'metric') await api.memory.removeMetric(target.recordId, target.id)
    else await api.memory.removeExperience(target.id)
    if (closed) return
    deleteTarget.value = null
    evidence.value = null
    experienceId.value = ''
    await reload()
    notify(
      target.kind === 'all'
        ? '该作品的反馈与经验已删除，发布记录和平台作品保留'
        : '记录及相关派生经验已删除',
    )
    if (target.kind === 'all') emit('deleted')
  })
}
const sync = ref(null),
  archive = ref(null),
  snapshots = ref([]),
  experiences = ref([]),
  selected = ref([]),
  note = ref(''),
  confirmAnalysis = ref(false),
  analysisIntent = ref(null),
  error = ref(''),
  source = ref('rednote_creator'),
  shown = ref(20),
  experienceId = ref(''),
  evidence = ref(null)
const manual = reactive({
  ...Object.fromEntries(metricFields.map(([key]) => [key, ''])),
  observed_at: localDateTime(),
  confirmed: false,
})
let closed = false,
  reloadTurn = 0,
  initialized = false
const latest = computed(() => snapshots.value[0])
const versions = computed(() =>
  experiences.value
    .filter((e) => e.post_id === archive.value?.id)
    .sort((a, b) => b.version - a.version),
)
const experience = computed(
  () => versions.value.find((e) => e.id === Number(experienceId.value)) || versions.value[0],
)
async function reload() {
  if (closed) return
  const turn = ++reloadTurn
  error.value = ''
  try {
    const status = await api.memory.syncStatus(props.record.id)
    const post = await api.memory.publication(props.record.id).catch((e) => {
      if (e.status === 404) return null
      throw e
    })
    const values = post
      ? await Promise.all([
          allPages((params) => api.memory.metrics(props.record.id, params)),
          allPages((params) => api.memory.experiences(props.productId, params)),
        ])
      : [[], []]
    if (closed || turn !== reloadTurn) return
    sync.value = status
    archive.value = post
    snapshots.value = values[0].sort(
      (a, b) => new Date(b.observed_at) - new Date(a.observed_at) || b.id - a.id,
    )
    experiences.value = values[1]
    selected.value = selected.value.filter((id) => snapshots.value.some((s) => s.id === id))
    if (!initialized && latest.value) selected.value = [latest.value.id]
    initialized = true
  } catch (e) {
    if (!closed && turn === reloadTurn) error.value = e.message
  }
}
async function collect() {
  const id = props.record.id,
    chosenSource = source.value
  const storage = userKey(session.user.id, 'metric-intent', id + ':' + chosenSource)
  const key = readLocal(storage) || 'metric-' + crypto.randomUUID()
  writeLocal(storage, key)
  await action('metrics:' + id, async () => {
    const value = await api.memory.collect(id, key, chosenSource)
    localStorage.removeItem(storage)
    await reload()
    if (!closed) {
      if (!selected.value.includes(value.id) && selected.value.length < 20)
        selected.value.push(value.id)
      notify('真实指标快照已保存，历史观测不会覆盖')
    }
  })
}
function askAnalysis() {
  if (!selected.value.length || selected.value.length > 20) return
  analysisIntent.value = {
    recordId: props.record.id,
    metrics_ids: [...selected.value],
    user_note: note.value.trim() || null,
  }
  confirmAnalysis.value = true
}
async function analyze() {
  const intent = analysisIntent.value
  await action('experience:' + intent.recordId, async () => {
    await api.memory.analyze(intent.recordId, {
      metrics_ids: intent.metrics_ids,
      user_note: intent.user_note,
    })
    if (closed) return
    confirmAnalysis.value = false
    experienceId.value = ''
    evidence.value = null
    notify('运营经验已保存；相同证据和说明会复用对应版本')
    await reload()
  })
}
async function saveManual() {
  const id = props.record.id
  await action('manual-metrics:' + id, async () => {
    if (!manual.confirmed) throw new Error('请先核对实际账号及后台数值')
    const values = Object.fromEntries(
      metricFields.filter(([key]) => manual[key] !== '').map(([key]) => [key, Number(manual[key])]),
    )
    if (!Object.keys(values).length) throw new Error('至少填写一项真实指标，未取得的数据请留空')
    if (Object.values(values).some((value) => !Number.isSafeInteger(value) || value < 0))
      throw new Error('指标必须是有效的非负整数')
    const observed = new Date(manual.observed_at)
    if (!Number.isFinite(observed.getTime())) throw new Error('请填写实际观测时间')
    const data = {
      account_id: props.record.account_id,
      observed_at: observed.toISOString(),
      metrics: values,
    }
    const storage = userKey(session.user.id, 'manual-metric-intent', id)
    const saved = readLocal(storage),
      signature = JSON.stringify(data)
    const key = saved?.signature === signature ? saved.key : 'manual-' + crypto.randomUUID()
    writeLocal(storage, { signature, key })
    const value = await api.memory.manual(id, { ...data, idempotency_key: key })
    localStorage.removeItem(storage)
    await reload()
    if (closed) return
    if (!selected.value.includes(value.id) && selected.value.length < 20)
      selected.value.push(value.id)
    for (const [key] of metricFields) manual[key] = ''
    manual.confirmed = false
    manual.observed_at = localDateTime()
    notify('后台观测已保存，来源标记为人工录入')
  })
}
function observationTime(value) {
  if (!value) return '—'
  const normalized = /(?:Z|[+-]\d{2}:?\d{2})$/.test(value) ? value : value + 'Z'
  return new Date(normalized).toLocaleString('zh-CN', { hour12: false })
}
async function showEvidence() {
  await action('experience-evidence', async () => {
    const id = experience.value.id
    const value = await api.memory.feedback(experience.value.feedback_id)
    if (!closed && experience.value?.id === id) evidence.value = value
  })
}
onMounted(reload)
onUnmounted(() => {
  closed = true
  ++reloadTurn
})
</script>
<template>
  <section class="panel memory-panel section-gap">
    <div class="panel-header">
      <h3>数据反馈与运营经验</h3>
      <div class="row">
        <button class="button ghost small" @click="reload">刷新反馈</button>
        <button
          class="button ghost small"
          :disabled="deleting"
          @click="askDelete('all', record.id)"
        >
          {{ sync?.status === 'deleting' ? '重试删除反馈' : '删除全部反馈' }}
        </button>
      </div>
    </div>
    <p v-if="error" class="error-text" role="alert">{{ error }}</p>
    <p class="muted small-text">
      经验库同步：{{
        {
          synced: '已同步',
          pending: '待同步',
          failed: '同步失败',
          deleting: '删除未完成，可重试',
          deleted: '反馈已删除',
        }[sync?.status] || '读取中'
      }}
    </p>
    <p v-if="sync?.error_message" class="error-text">{{ sync.error_message }}</p>
    <button
      v-if="!archive && sync?.status !== 'deleting'"
      class="button"
      :disabled="busy.has('sync-memory')"
      @click="
        action('sync-memory', async () => {
          await api.memory.sync(record.id)
          await reload()
        })
      "
    >
      {{ sync?.status === 'deleted' ? '重新启用反馈（不恢复已删除数据）' : '同步作品到经验库' }}
    </button>
    <template v-if="archive">
      <div class="metrics feedback-metrics">
        <div v-for="[key, label] in metricFields" :key="key" class="metric">
          <strong>{{ latest?.metrics[key] ?? '—' }}</strong>
          <span>{{ label }}</span>
        </div>
      </div>
      <div v-if="latest" class="observation-heading section-gap">
        <strong>{{ formatObservationAge(latest.elapsed_since_publish_seconds) }}</strong>
        <p class="muted">
          观测于 {{ observationTime(latest.observed_at) }} · {{ metricSources[latest.source] }}
        </p>
      </div>
      <p v-else class="muted section-gap">尚未采集指标。“—”表示未取得的数据，不作为零值处理。</p>
      <div v-if="record.platform === 'rednote'" class="row feedback-collection section-gap">
        <label class="field grow">
          <span class="field-label">数据来源</span>
          <select
            v-model="source"
            aria-label="反馈数据来源"
            :disabled="busy.has('metrics:' + record.id)"
          >
            <option value="rednote_creator">创作中心 · 当前账号后台</option>
            <option value="rednote_public">公开详情页 · 公开互动数据</option>
          </select>
        </label>
        <button class="button" :disabled="busy.has('metrics:' + record.id)" @click="collect">
          {{ busy.has('metrics:' + record.id) ? '采集中…' : '采集当前真实指标' }}
        </button>
      </div>
      <p class="muted small-text">
        每次成功采集保存一个新快照；失败重试复用同次请求。采集前须登录作品绑定的实际平台账号。创作中心当前读取作品列表提供的指标，未取得的曝光、点击、转化可据后台真实值补录。浏览量不等于曝光量。
      </p>
      <p v-if="record.visibility === 'private'" class="notice">
        此作品仅自己可见，互动数据不能用于判断公开传播效果。
      </p>
      <details class="manual-observation">
        <summary>录入真实后台数据</summary>
        <form class="section-gap" @submit.prevent="saveManual">
          <fieldset :disabled="busy.has('manual-metrics:' + record.id)">
            <div class="manual-fields">
              <label v-for="[key, label] in metricFields" :key="key" class="field">
                <span class="field-label">{{ label }}（未知留空）</span>
                <input
                  v-model="manual[key]"
                  type="number"
                  min="0"
                  :max="Number.MAX_SAFE_INTEGER"
                  step="1"
                />
              </label>
            </div>
            <label class="field">
              <span class="field-label">实际观测时间</span>
              <input v-model="manual.observed_at" type="datetime-local" step="1" required />
            </label>
            <label class="check-row">
              <input v-model="manual.confirmed" type="checkbox" required />
              已核对作品、实际账号及后台数值
            </label>
            <button class="button section-gap" :disabled="!manual.confirmed">保存人工观测</button>
          </fieldset>
        </form>
      </details>
      <hr class="section-divider" />
      <div class="panel-header">
        <h3>观测历史 · {{ snapshots.length }} 次</h3>
        <span class="muted small-text">已选 {{ selected.length }}/20 个证据快照</span>
      </div>
      <p class="muted small-text">
        同一篇作品可在不同时间反复观测。以下均为当时的累计值，不应相加；不同来源和观察窗口需分别比较。
      </p>
      <article
        v-for="snapshot in snapshots.slice(0, shown)"
        :key="snapshot.id"
        class="snapshot-card"
      >
        <div class="row between">
          <label class="check-row">
            <input
              v-model="selected"
              type="checkbox"
              :value="snapshot.id"
              :aria-label="'选择快照 #' + snapshot.id"
              :disabled="!selected.includes(snapshot.id) && selected.length >= 20"
            />
            <span>
              <strong>{{ formatObservationAge(snapshot.elapsed_since_publish_seconds) }}</strong>
              <small class="muted">
                #{{ snapshot.id }} · {{ observationTime(snapshot.observed_at) }}
              </small>
            </span>
          </label>
          <span class="chip">{{ metricSources[snapshot.source] }}</span>
          <button
            class="button ghost small"
            :aria-label="`删除观测 #${snapshot.id}`"
            :disabled="deleting"
            @click="askDelete('metric', snapshot.id)"
          >
            删除观测
          </button>
        </div>
        <dl class="snapshot-values">
          <div v-for="[key, label] in metricFields" :key="key">
            <dt>{{ label }}</dt>
            <dd>{{ snapshot.metrics[key] ?? '—' }}</dd>
          </div>
        </dl>
      </article>
      <p v-if="!snapshots.length" class="muted">采集或录入后，会在这里保留每次观测的时间和来源。</p>
      <button v-if="snapshots.length > shown" class="button small" @click="shown += 20">
        查看更多历史观测
      </button>
      <hr class="section-divider" />
      <h3>提炼运营经验</h3>
      <p class="muted small-text">
        选择一个或多个观测快照，结合已发布内容生成待验证的运营假设；经验用于后续生成检索，不会改变模型参数。
      </p>
      <label class="field">
        <span class="field-label">本轮补充说明</span>
        <textarea
          v-model="note"
          rows="3"
          maxlength="2000"
          placeholder="补充运营背景、人工调整、评论反馈或下一轮希望验证的问题。说明不等同于指标证据。"
        />
      </label>
      <button
        class="button primary"
        :disabled="!selected.length || selected.length > 20 || busy.has('experience:' + record.id)"
        @click="askAnalysis"
      >
        AI 分析并积累经验
      </button>
      <template v-if="experience">
        <div class="row between section-gap">
          <strong>
            {{ experience.id === versions[0]?.id ? '最新经验' : '历史经验' }} v{{
              experience.version
            }}
          </strong>
          <label class="field">
            <span class="sr-only">运营经验版本</span>
            <select v-model="experienceId" aria-label="运营经验版本" @change="evidence = null">
              <option value="">最新版本 v{{ versions[0]?.version }}</option>
              <option v-for="version in versions" :key="version.id" :value="version.id">
                v{{ version.version }} · {{ displayDate(version.created_at, true) }}
              </option>
            </select>
          </label>
        </div>
        <span class="chip">{{ experience.is_active ? '可供检索' : '已停用' }}</span>
        <button
          class="button ghost small"
          :disabled="deleting"
          @click="askDelete('experience', experience.id)"
        >
          删除此经验
        </button>
        <p class="preserve section-gap">{{ experience.result.summary }}</p>
        <article v-for="(lesson, index) in experience.result.lessons" :key="index" class="risk">
          <strong>
            {{
              {
                hook: '开头',
                title: '标题',
                body: '正文',
                image: '配图',
                cta: '行动引导',
                audience: '受众',
              }[lesson.category] || lesson.category
            }}
            · {{ lesson.confidence === 'medium' ? '中等' : '较低' }}信心 · 待验证假设
          </strong>
          <p>{{ lesson.observation }}</p>
          <p>下一轮实验：{{ lesson.next_experiment }}</p>
          <small>证据快照：{{ lesson.evidence_metric_ids.join('、') }}</small>
        </article>
        <details>
          <summary>分析限制</summary>
          <ul>
            <li v-for="limitation in experience.result.limitations" :key="limitation">
              {{ limitation }}
            </li>
          </ul>
        </details>
        <div class="row section-gap">
          <button class="button ghost small" @click="showEvidence">查看本版分析证据</button>
          <button
            class="button ghost small"
            @click="
              action('toggle-experience', async () => {
                await api.memory.setActive(experience.id, !experience.is_active)
                await reload()
              })
            "
          >
            {{ experience.is_active ? '停用此经验' : '重新启用此经验' }}
          </button>
        </div>
        <div v-if="evidence" class="notice info section-gap">
          <p>分析模型：{{ evidence.model }} · {{ displayDate(evidence.created_at, true) }}</p>
          <p v-for="snapshot in evidence.evidence_snapshot.metrics_snapshots" :key="snapshot.id">
            证据 #{{ snapshot.id }} ·
            {{ formatObservationAge(snapshot.elapsed_since_publish_seconds) }} ·
            {{ observationTime(snapshot.observed_at) }}
          </p>
        </div>
      </template>
      <p v-else class="muted section-gap">
        尚无该作品的运营经验。采集指标后，明确触发 AI 分析并保存经验版本。
      </p>
    </template>
  </section>
  <BaseDialog
    :open="!!deleteTarget"
    title="确认删除反馈数据"
    :busy="deleting"
    @close="!deleting && (deleteTarget = null)"
  >
    <p v-if="deleteTarget?.kind === 'all'">
      将彻底删除这篇作品的全部观测、分析和运营经验，并从反馈列表移除。发布记录与平台真实帖子保留，已删除数据无法恢复。
    </p>
    <p v-else-if="deleteTarget?.kind === 'metric'">
      将删除这次观测，以及使用该观测生成的分析和经验，避免继续引用已删除证据。无法恢复。
    </p>
    <p v-else>
      将删除当前经验版本及其分析记录，后续生成不再检索此经验。观测数据保留，删除后无法恢复。
    </p>
    <div class="row section-gap">
      <button class="button" :disabled="deleting" @click="deleteTarget = null">取消</button>
      <button class="button danger" :disabled="deleting" @click="deleteFeedback">
        {{ deleting ? '删除中…' : '确认删除反馈' }}
      </button>
    </div>
  </BaseDialog>
  <BaseDialog
    :open="confirmAnalysis"
    title="确认提炼运营经验"
    :busy="busy.has('experience:' + record.id)"
    @close="confirmAnalysis = false"
  >
    <p>
      将使用选中的
      {{ analysisIntent?.metrics_ids.length }}
      个真实观测快照、发布内容和补充说明调用模型，并保存运营经验。模型调用可能产生费用。
    </p>
    <p class="notice">
      同一作品的多次观测不是多个独立样本。分析仅提出待验证假设，不保证效果，也不会直接训练大模型。
    </p>
    <div class="row section-gap">
      <button
        class="button primary"
        :disabled="busy.has('experience:' + record.id)"
        @click="analyze"
      >
        {{ busy.has('experience:' + record.id) ? '分析中…' : '确认分析' }}
      </button>
      <button
        class="button"
        :disabled="busy.has('experience:' + record.id)"
        @click="confirmAnalysis = false"
      >
        取消
      </button>
    </div>
  </BaseDialog>
</template>
