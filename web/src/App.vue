<script setup lang="ts">
import { onMounted, onUnmounted, ref, watch } from 'vue'
import { NConfigProvider, zhCN, dateZhCN } from 'naive-ui'
import { themeOverrides } from './styles/theme'
import HomeView from './views/HomeView.vue'
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
  navigate('/')
}

onUnauthorized(syncRoute)
onMounted(() => window.addEventListener('popstate', syncRoute))
onUnmounted(() => window.removeEventListener('popstate', syncRoute))
</script>

<template>
  <NConfigProvider :theme-overrides="themeOverrides" :locale="zhCN" :date-locale="dateZhCN">
  <a class="skip-link" href="#main">跳到主要内容</a>
  <HomeView v-if="route === '/'" @navigate-auth="navigate(sessionToken ? '/app' : '/auth')" />
  <AuthView v-else-if="route === '/auth'" :error="authError" :loading="authLoading" @authenticated="login" @home="navigate('/')" />
  <WorkspaceView v-else-if="sessionToken" @logout="logout" />
  </NConfigProvider>
</template>
