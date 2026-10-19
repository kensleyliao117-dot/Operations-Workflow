<script setup>
import { ref } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import AuthLayout from '../components/AuthLayout.vue'
import { login } from '../stores/session.js'
const route = useRoute(),
  router = useRouter()
const username = ref(String(route.query.registered || '')),
  password = ref(''),
  show = ref(false),
  pending = ref(false),
  error = ref('')
async function submit() {
  if (pending.value) return
  pending.value = true
  error.value = ''
  try {
    await login({ username: username.value.trim(), password: password.value })
    password.value = ''
    const target = String(route.query.redirect || '/products')
    await router.replace(
      target.startsWith('/') && !target.startsWith('//') && !target.startsWith('/login')
        ? target
        : '/products',
    )
  } catch (e) {
    error.value = e.message
  } finally {
    pending.value = false
  }
}
</script>
<template>
  <AuthLayout>
    <p class="eyebrow">WELCOME BACK</p>
    <h1>登录工作台</h1>
    <p class="muted">继续管理产品与内容任务</p>
    <p v-if="route.query.registered" class="notice info">注册成功，请使用新账号登录。</p>
    <p v-if="route.query.reason" class="notice">{{ route.query.reason }}</p>
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
          placeholder="输入用户名"
        />
      </label>
      <div class="field">
        <label for="login-password" class="field-label">密码</label>
        <span class="password-field">
          <input
            id="login-password"
            v-model="password"
            :type="show ? 'text' : 'password'"
            autocomplete="current-password"
            required
            maxlength="128"
            placeholder="输入密码"
          />
          <button type="button" class="button" :aria-pressed="show" @click="show = !show">
            {{ show ? '隐藏' : '显示' }}
          </button>
        </span>
      </div>
      <p v-if="error" class="error-text" role="alert">{{ error }}</p>
      <button class="button primary full-width" :disabled="pending">
        {{ pending ? '登录中…' : '登录' }}
      </button>
    </form>
    <p class="auth-footer">
      还没有账号？
      <RouterLink to="/register">创建账号</RouterLink>
    </p>
  </AuthLayout>
</template>
