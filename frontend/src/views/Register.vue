<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api/index.js'
import AuthLayout from '../components/AuthLayout.vue'
const router = useRouter()
const username = ref(''),
  email = ref(''),
  password = ref(''),
  confirmation = ref(''),
  pending = ref(false),
  error = ref('')
async function submit() {
  error.value = ''
  if (password.value !== confirmation.value) {
    error.value = '两次输入的密码不一致'
    return
  }
  if (pending.value) return
  pending.value = true
  try {
    const data = { username: username.value.trim(), password: password.value }
    if (email.value.trim()) data.email = email.value.trim()
    await api.auth.register(data)
    password.value = ''
    confirmation.value = ''
    await router.push({ path: '/login', query: { registered: username.value.trim() } })
  } catch (e) {
    error.value = e.message
  } finally {
    pending.value = false
  }
}
</script>
<template>
  <AuthLayout>
    <p class="eyebrow">YOUR WORKSPACE</p>
    <h1>创建账号</h1>
    <p class="muted">产品资料与生成内容按账号独立保存</p>
    <form @submit.prevent="submit">
      <label class="field">
        <span class="field-label">用户名</span>
        <input
          v-model="username"
          autocomplete="username"
          required
          minlength="3"
          maxlength="100"
          pattern="[a-zA-Z0-9_.\-]+"
          placeholder="字母、数字、下划线、点或短横线"
        />
      </label>
      <label class="field">
        <span class="field-label">
          邮箱
          <span class="muted">（选填）</span>
        </span>
        <input
          v-model="email"
          type="email"
          autocomplete="email"
          maxlength="200"
          placeholder="name@example.com"
        />
      </label>
      <label class="field">
        <span class="field-label">密码</span>
        <input
          v-model="password"
          type="password"
          autocomplete="new-password"
          required
          minlength="12"
          maxlength="128"
          placeholder="12–128 个字符"
        />
      </label>
      <label class="field">
        <span class="field-label">确认密码</span>
        <input
          v-model="confirmation"
          type="password"
          autocomplete="new-password"
          required
          minlength="12"
          maxlength="128"
          placeholder="再次输入密码"
        />
      </label>
      <p v-if="error" class="error-text" role="alert">{{ error }}</p>
      <button class="button primary full-width" :disabled="pending">
        {{ pending ? '创建中…' : '注册账号' }}
      </button>
    </form>
    <p class="auth-footer">
      已有账号？
      <RouterLink to="/login">返回登录</RouterLink>
    </p>
  </AuthLayout>
</template>
