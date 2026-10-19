<script setup>
import { ref, reactive, watch, computed, onUnmounted } from 'vue'
import BaseDialog from './BaseDialog.vue'
import StatusBadge from './StatusBadge.vue'
import { api } from '../api/index.js'
import { session } from '../stores/session.js'
import { action, busy, notify } from '../stores/ui.js'
import { canPublish, safeLink, displayDate } from '../utils/domain.js'
import { publishKey, readLocal, writeLocal, userKey } from '../utils/storage.js'
const props = defineProps({
  task: Object,
  content: Object,
  latest: Object,
  dirty: Boolean,
  workflow: Object,
})
const emit = defineEmits(['refresh'])
const records = ref([]),
  accounts = ref([]),
  account = ref(''),
  visibility = ref('private'),
  confirm = ref(null),
  accountConfirmed = ref(false),
  resultRecord = ref(null),
  resultForm = reactive({
    publish_status: 'published',
    platform_post_url: '',
    error_message: '',
    confirmed: false,
    confirm_not_published: false,
    published_at: '',
    final_title: '',
    final_body: '',
    external_materials_note: '',
  })
const accountForm = reactive({ platform_user_id: '', display_name: '' })
const enabled = computed(() =>
  canPublish(props.task, props.content, props.latest, records.value, props.dirty, props.workflow),
)
const success = computed(() => records.value.find((r) => r.publish_status === 'published'))
let refreshTimer,
  turn = 0,
  closed = false
async function reload() {
  const id = props.task?.id,
    ticket = ++turn
  if (!id) {
    records.value = []
    return
  }
  try {
    const values = await api.publishing.list(id)
    if (ticket === turn && !closed) records.value = values
  } catch (e) {
    notify(e.message, 'error')
  } finally {
    clearTimeout(refreshTimer)
    if (
      !closed &&
      props.task?.id === id &&
      records.value.some((r) => r.publish_status === 'publishing')
    )
      refreshTimer = setTimeout(reload, 6000)
  }
}
watch(
  () => props.task?.id,
  async () => {
    records.value = []
    account.value = ''
    confirm.value = null
    resultRecord.value = null
    await reload()
    await action('accounts-load', async () => (accounts.value = await api.accounts.list()))
  },
  { immediate: true },
)
watch(() => props.task?.status, reload)
onUnmounted(() => {
  closed = true
  ++turn
  clearTimeout(refreshTimer)
})
function ask(kind, record = null) {
  if (
    !props.content ||
    props.content.id !== props.latest?.id ||
    (record &&
      (record.content_id !== props.content.id || record.content_version !== props.content.version))
  ) {
    notify('请先查看发布记录对应的最新已审核版本，再确认发布或核实结果', 'error')
    return
  }
  if (props.dirty) {
    notify('请先保存修改并重新审核', 'error')
    return
  }
  accountConfirmed.value = false
  confirm.value = {
    kind,
    record,
    taskId: props.task.id,
    contentId: props.content.id,
    version: props.content.version,
    title: props.content.title,
    accountId: record?.account_id || Number(account.value),
    visibility: record?.visibility || visibility.value,
  }
}
async function execute() {
  const intent = confirm.value
  if (!accountConfirmed.value || !intent?.accountId) return
  const owner = session.user.id
  await action(`publish:${intent.taskId}`, async () => {
    let record = intent.record
    try {
      if (!record)
        record = await api.publishing.create({
          task_id: intent.taskId,
          content_id: intent.contentId,
          platform: 'rednote',
          account_id: intent.accountId,
          publisher_backend: intent.kind === 'manual_new' ? 'manual' : 'chrome',
          visibility: intent.visibility,
          idempotency_key:
            (intent.kind === 'manual_new' ? 'manual-' : '') +
            publishKey(owner, intent.taskId, intent.contentId, intent.accountId, intent.visibility),
        })
      else if (intent.kind === 'retry' || intent.kind === 'manual') {
        const storage = userKey(owner, 'retry', record.id)
        const key = readLocal(storage) || `retry-${crypto.randomUUID()}`
        writeLocal(storage, key)
        record = await api.publishing.retry(record.id, key)
      }
      if (intent.kind === 'manual' || intent.kind === 'manual_new') {
        openResult(record)
        confirm.value = null
        return
      }
      const result = await api.publishing.execute(record.id)
      notify(
        result.publish_status === 'published'
          ? '后端已确认发布成功'
          : result.error_message || '发布结果已返回，请查看记录',
        result.publish_status === 'published' ? 'success' : 'info',
      )
      confirm.value = null
    } finally {
      await reload()
      emit('refresh')
    }
  })
}
function openResult(record) {
  resultRecord.value = record
  Object.assign(resultForm, {
    publish_status: 'published',
    platform_post_url: '',
    error_message: '',
    confirmed: false,
    confirm_not_published: false,
    published_at: '',
    final_title: '',
    final_body: '',
    external_materials_note: '',
  })
}
async function saveResult() {
  if (!resultForm.confirmed) return
  const record = resultRecord.value
  await action(`reconcile:${record.id}`, async () => {
    const data = { publish_status: resultForm.publish_status, account_id: record.account_id }
    if (resultForm.publish_status === 'published') {
      data.platform_post_url = resultForm.platform_post_url
      if (resultForm.published_at)
        data.published_at = new Date(resultForm.published_at).toISOString()
      if (resultForm.final_title || resultForm.final_body)
        data.final_content = {
          ...(resultForm.final_title ? { title: resultForm.final_title } : {}),
          ...(resultForm.final_body ? { body: resultForm.final_body } : {}),
        }
      if (resultForm.external_materials_note.trim())
        data.external_materials_note = resultForm.external_materials_note.trim()
    } else {
      data.error_message = resultForm.error_message
      data.confirm_not_published = resultForm.confirm_not_published
    }
    await api.publishing.result(record.id, data)
    resultRecord.value = null
    notify('已保存人工核实结果')
    await reload()
    emit('refresh')
  })
}
async function registerAccount() {
  await action('register-account', async () => {
    const result = await api.accounts.create({
      platform: 'rednote',
      platform_user_id: accountForm.platform_user_id.trim(),
      display_name: accountForm.display_name.trim() || null,
    })
    accounts.value = await api.accounts.list()
    account.value = result.id
    notify('账号已登记。发布时还会核对 Chrome 的实际登录账号。')
  })
}
</script>
<template>
  <section class="panel">
    <div class="panel-header">
      <h3>发布到小红书</h3>
      <button class="button ghost small" @click="reload">刷新记录</button>
    </div>
    <p class="muted">仅发布已保存且审核通过的最新版本。发布前需再次确认账号与可见范围。</p>
    <p v-if="records.some((record) => record.publish_status === 'unknown')" class="notice">
      任务和素材已保存，当前发布结果待确认。请核实创作中心后，在下方登记实际结果；不要重复发布。
    </p>
    <template v-if="task?.status === 'approved'">
      <label class="field">
        <span class="field-label">发布账号</span>
        <select v-model="account" aria-label="发布账号">
          <option value="">明确选择一个发布账号</option>
          <option
            v-for="item in accounts.filter((a) => a.is_active && a.platform === 'rednote')"
            :key="item.id"
            :value="item.id"
          >
            {{ item.display_name || item.platform_user_id }}
          </option>
        </select>
      </label>
      <label class="field">
        <span class="field-label">可见范围</span>
        <select v-model="visibility" aria-label="可见范围">
          <option value="private">仅自己可见</option>
          <option value="public">公开可见</option>
        </select>
      </label>
      <button
        class="button primary full-width"
        :disabled="
          !enabled ||
          !account ||
          !content.image_assets?.length ||
          (!content.title && !content.body) ||
          busy.has(`publish:${task.id}`)
        "
        @click="ask('new')"
      >
        确认内容并发布
      </button>
      <button
        class="button full-width section-gap"
        :disabled="!enabled || !account || busy.has(`publish:${task.id}`)"
        @click="ask('manual_new')"
      >
        自行发布并登记结果
      </button>
      <p class="muted">
        自动发布使用已保存的文案和图片。也可下载现有素材，在平台补充另一部分后自行发布，再登记最终作品链接。
      </p>
    </template>
    <p v-else-if="!records.length" class="muted">
      完成人工审核后可自动发布或登记人工发布结果。风险检查仅作提示。
    </p>
    <details>
      <summary>登记发布账号</summary>
      <p class="muted section-gap">
        可读取 Chrome 中的账号 ID，确认后登记；账号归属由当前项目登录身份确定。
      </p>
      <button
        class="button small"
        :disabled="busy.has('read-account')"
        @click="
          action('read-account', async () => {
            const value = await api.accounts.current()
            accountForm.platform_user_id = value.platform_user_id
            notify('已读取 Chrome 当前账号 ID，请确认后登记')
          })
        "
      >
        读取 Chrome 登录账号
      </button>
      <form class="section-gap" @submit.prevent="registerAccount">
        <label class="field">
          <span class="field-label">小红书个人主页 ID（24 位）</span>
          <input
            v-model="accountForm.platform_user_id"
            required
            pattern="[0-9a-f]{24}"
            placeholder="个人主页 /user/profile/ 后的 ID"
          />
        </label>
        <label class="field">
          <span class="field-label">账号备注</span>
          <input
            v-model="accountForm.display_name"
            maxlength="100"
            placeholder="便于识别的账号名称"
          />
        </label>
        <button class="button small" :disabled="busy.has('register-account')">
          确认并登记账号
        </button>
      </form>
    </details>
    <div v-for="record in records" :key="record.id" class="file-row">
      <div class="row between">
        <strong>发布记录 #{{ record.id }}</strong>
        <StatusBadge type="publish" :status="record.publish_status" />
      </div>
      <small class="muted">
        内容 v{{ record.content_version }} ·
        {{ record.visibility === 'private' ? '仅自己可见' : '公开可见' }} ·
        {{
          accounts.find((a) => a.id === record.account_id)?.display_name ||
          `账号 #${record.account_id}`
        }}
      </small>
      <p v-if="record.error_message" class="error-text">{{ record.error_message }}</p>
      <a
        v-if="safeLink(record.platform_post_url)"
        :href="safeLink(record.platform_post_url)"
        target="_blank"
        rel="noopener noreferrer"
      >
        查看作品 ↗
      </a>
      <small v-if="record.published_at" class="muted">
        发布于 {{ displayDate(record.published_at, true) }}
      </small>
      <div class="row">
        <button
          v-if="
            record.publisher_backend === 'chrome' &&
            record.publish_status === 'publishing' &&
            record.delivery_phase === 'prepared'
          "
          class="button small"
          :disabled="busy.has(`publish:${task.id}`)"
          @click="ask('continue', record)"
        >
          继续已登记发布
        </button>
        <button
          v-if="
            record.publish_status === 'failed' &&
            record.id === records[0]?.id &&
            task?.status === 'failed'
          "
          class="button small"
          @click="ask('retry', record)"
        >
          确认后新建重试
        </button>
        <button
          v-if="
            record.publish_status === 'failed' &&
            record.id === records[0]?.id &&
            task?.status === 'failed'
          "
          class="button ghost small"
          @click="ask('manual', record)"
        >
          已手动发布？登记结果
        </button>
        <button
          v-if="['unknown', 'publishing'].includes(record.publish_status)"
          class="button small"
          @click="openResult(record)"
        >
          核实并登记实际结果
        </button>
      </div>
      <p v-if="record.publish_status === 'unknown'" class="notice">
        请先在创作中心核对实际作品，不要直接重新发布。查询记录本身不会替代平台核实。
      </p>
    </div>
  </section>
  <section v-if="success" class="panel">
    <h3>发布后的数据反馈</h3>
    <p class="muted">记录不同时间的作品表现，提炼运营经验，供下一轮创作参考。</p>
    <RouterLink class="button" :to="{ path: '/feedback', query: { record_id: success.id } }">
      数据反馈与运营经验 →
    </RouterLink>
  </section>
  <BaseDialog
    :open="!!confirm"
    :title="confirm?.kind?.startsWith('manual') ? '登记人工发布结果' : '最终发布确认'"
    :busy="busy.has(`publish:${confirm?.taskId}`)"
    @close="confirm = null"
  >
    <p class="notice" v-if="confirm?.kind?.startsWith('manual')">
      仅新建结果核实记录，不点击平台发布按钮。稍后需要提供实际作品链接。
    </p>
    <p>
      <strong>{{ confirm?.title }}</strong>
    </p>
    <p>任务 #{{ confirm?.taskId }} · Content #{{ confirm?.contentId }} · v{{ confirm?.version }}</p>
    <p>
      账号：{{
        accounts.find((a) => a.id === confirm?.accountId)?.display_name || confirm?.accountId
      }}
    </p>
    <p>
      范围：
      <strong>{{ confirm?.visibility === 'private' ? '仅自己可见' : '公开可见' }}</strong>
    </p>
    <p class="muted">
      将由后端再次核对实际账号、审核版本、图片和可见范围。网络中断后需先查看发布记录。
    </p>
    <p v-if="!confirm?.kind?.startsWith('manual')" class="notice">
      自动发布期间请勿修改发布页。需要在小红书编辑图片或补充素材时，请选择“自行发布并登记结果”。
    </p>
    <label class="check-row">
      <input v-model="accountConfirmed" type="checkbox" />
      已核对内容版本、目标账号及可见范围
    </label>
    <div class="row section-gap">
      <button
        class="button primary"
        :disabled="!accountConfirmed || busy.has(`publish:${confirm?.taskId}`)"
        @click="execute"
      >
        {{
          confirm?.kind?.startsWith('manual')
            ? '创建结果核实记录'
            : busy.has(`publish:${confirm?.taskId}`)
              ? '发布处理中…'
              : '确认发布'
        }}
      </button>
      <button
        class="button"
        :disabled="busy.has(`publish:${confirm?.taskId}`)"
        @click="confirm = null"
      >
        取消
      </button>
    </div>
  </BaseDialog>
  <BaseDialog
    :open="!!resultRecord"
    title="登记已核实的发布结果"
    :busy="busy.has(`reconcile:${resultRecord?.id}`)"
    @close="resultRecord = null"
  >
    <p class="notice">先在指定账号的创作中心核实，再填写实际结果。不会自动发送或删除作品。</p>
    <form @submit.prevent="saveResult">
      <label class="field">
        <span class="field-label">核实结果</span>
        <select v-model="resultForm.publish_status">
          <option value="published">确认已发布</option>
          <option value="failed">确认未发布</option>
        </select>
      </label>
      <template v-if="resultForm.publish_status === 'published'">
        <label class="field">
          <span class="field-label">完整作品链接</span>
          <input
            v-model="resultForm.platform_post_url"
            type="url"
            required
            placeholder="https://www.xiaohongshu.com/explore/..."
          />
        </label>
        <label class="field">
          <span class="field-label">实际发布时间（选填）</span>
          <input v-model="resultForm.published_at" type="datetime-local" />
          <small class="muted">使用本机时区，未填写时以登记时刻保存。</small>
        </label>
        <details>
          <summary>平台补充或修改的素材（选填）</summary>
          <label class="field">
            <span class="field-label">最终发布标题</span>
            <input v-model="resultForm.final_title" maxlength="300" />
          </label>
          <label class="field">
            <span class="field-label">最终发布正文</span>
            <textarea v-model="resultForm.final_body" maxlength="50000" rows="5" />
          </label>
          <label class="field">
            <span class="field-label">平台补充素材说明</span>
            <textarea
              v-model="resultForm.external_materials_note"
              maxlength="2000"
              placeholder="例如：图片在工作流生成，文案在平台填写；或补充了实拍产品图。"
            />
          </label>
        </details>
      </template>
      <template v-else>
        <label class="field">
          <span class="field-label">核实说明</span>
          <textarea v-model="resultForm.error_message" required rows="3" />
        </label>
        <label class="check-row">
          <input v-model="resultForm.confirm_not_published" type="checkbox" required />
          已核实该账号确实没有生成本次作品
        </label>
      </template>
      <label class="check-row section-gap">
        <input v-model="resultForm.confirmed" type="checkbox" required />
        确认实际账号与发布记录绑定账号一致
      </label>
      <button
        class="button primary section-gap"
        :disabled="!resultForm.confirmed || busy.has(`reconcile:${resultRecord?.id}`)"
      >
        保存核实结果
      </button>
    </form>
  </BaseDialog>
</template>
