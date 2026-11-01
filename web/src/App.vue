<script setup lang="ts">
import { onMounted, onUnmounted, ref, watch } from 'vue'
import { NConfigProvider, darkTheme, zhCN, dateZhCN } from 'naive-ui'
import { colorScheme, themeForScheme } from './styles/scheme'
import AuthView from './views/AuthView.vue'
import WorkspaceView from './views/WorkspaceView.vue'
import { authenticate } from './api/auth'
import { onUnauthorized } from './api/client'
import { clearSession, establishSession, sessionToken } from './stores/session'
import { enterWorkspace, navigate, route, syncRoute } from './router'

const authError = ref('')
const authLoading = ref(false)
let authAttempt = 0
syncRoute()
watch(route, () => {
  authAttempt += 1
  authLoading.value = false
  authError.value = ''
}, { flush: 'sync' })

async function login(token: string): Promise<void> {
  const attempt = ++authAttempt
  authLoading.value = true
  authError.value = ''
  try {
    const result = await authenticate(token)
    if (attempt !== authAttempt || route.value !== '/auth') return
    if (result?.authenticated !== true) throw new Error('暂时无法确认登录结果，请重试')
    establishSession(token)
    enterWorkspace()
  } catch (cause) {
    if (attempt === authAttempt && route.value === '/auth') authError.value = cause instanceof Error ? cause.message : '访问密钥验证失败'
  } finally {
    if (attempt === authAttempt) authLoading.value = false
  }
}

function logout(): void {
  authAttempt += 1
  clearSession()
  authError.value = ''
  authLoading.value = false
  navigate('/auth')
}

onUnauthorized(syncRoute)
onMounted(() => window.addEventListener('popstate', syncRoute))
onUnmounted(() => window.removeEventListener('popstate', syncRoute))
</script>

<template>
  <NConfigProvider :theme="colorScheme === 'dark' ? darkTheme : null" :theme-overrides="themeForScheme" :locale="zhCN" :date-locale="dateZhCN">
  <a class="skip-link" href="#main">跳到主要内容</a>
  <WorkspaceView v-if="sessionToken && route !== '/auth'" @logout="logout" />
  <AuthView v-else :error="authError" :loading="authLoading" @authenticated="login" />
  </NConfigProvider>
</template>
