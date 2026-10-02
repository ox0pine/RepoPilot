<script setup lang="ts">
import { computed, ref } from 'vue'
import { NAlert, NButton, NCard, NInput } from 'naive-ui'
import { ArrowLeft, Eye, EyeOff, KeyRound } from '@lucide/vue'
import BrandMark from '../components/BrandMark.vue'

const props = defineProps<{ error?: string; loading?: boolean }>()
const emit = defineEmits<{ authenticated: [token: string]; home: [] }>()
const token = ref('')
const reveal = ref(false)
const inputType = computed(() => reveal.value ? 'text' : 'password')

function submit(): void {
  if (props.loading) return
  const value = token.value.trim()
  if (value) emit('authenticated', value)
}
</script>

<template>
  <div class="auth-page">
    <header class="auth-header">
      <NButton attr-type="button" class="back-button" @click="emit('home')">
        <template #icon><ArrowLeft :size="18" :stroke-width="1.75" aria-hidden="true" /></template>
        返回首页
      </NButton>
    </header>
    <main id="main" class="auth-layout" tabindex="-1">
      <section class="auth-intro" aria-labelledby="auth-title">
        <a class="brand-link" href="/" aria-label="返回 RepoPilot 首页" @click.prevent="emit('home')"><BrandMark /></a>
        <p class="eyebrow">你的代码任务，从这里开始</p>
        <h1 id="auth-title">连接你的工作台</h1>
        <p class="intro-description">输入管理员提供的访问密钥，进入工作台并配置模型服务与 GitHub 授权。</p>
        <p id="session-note" class="session-note">
          <KeyRound :size="20" :stroke-width="1.75" aria-hidden="true" />
          <span>访问密钥仅用于当前会话，刷新后需重新登录</span>
        </p>
      </section>
      <NCard title="工作台登录" class="auth-card">
        <p class="card-description">使用工作台访问密钥验证身份。</p>
        <form @submit.prevent="submit">
          <label class="token-label" for="access-token">工作台访问密钥</label>
          <div class="token-field">
            <NInput
              v-model:value="token"
              :type="inputType"
              :disabled="props.loading"
              placeholder="粘贴工作台访问密钥"
              :input-props="{ id: 'access-token', autocomplete: 'current-password', spellcheck: false, 'aria-describedby': 'session-note' }"
            />
            <NButton
              attr-type="button"
              class="reveal-button"
              :disabled="props.loading"
              :aria-label="reveal ? '隐藏密钥' : '显示密钥'"
              :title="reveal ? '隐藏密钥' : '显示密钥'"
              :aria-pressed="reveal"
              @click="reveal = !reveal"
            >
              <template #icon>
                <EyeOff v-if="reveal" :size="18" :stroke-width="1.75" aria-hidden="true" />
                <Eye v-else :size="18" :stroke-width="1.75" aria-hidden="true" />
              </template>
            </NButton>
          </div>
          <NAlert v-if="props.error" type="error" class="auth-error" role="alert" :show-icon="false">{{ props.error }}</NAlert>
          <NButton type="primary" attr-type="submit" class="submit-button" :loading="props.loading" :disabled="!token.trim() || props.loading">
            {{ props.loading ? '验证中…' : '进入工作台' }}
          </NButton>
        </form>
      </NCard>
    </main>
  </div>
</template>

<style scoped>
.auth-page { max-width: 1008px; margin: 0 auto; padding: 0 24px; }
.auth-header { display: flex; align-items: center; height: 64px; }
.auth-layout { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 420px); align-items: center; gap: 64px; min-height: calc(100dvh - 64px); padding: 48px 0 96px; }
.auth-intro { min-width: 0; }
.brand-link { display: inline-flex; margin-bottom: 48px; }
.eyebrow { margin: 0 0 12px; color: var(--accent); font-size: 13px; font-weight: 600; }
h1 { margin: 0 0 24px; font-size: clamp(28px, 3.5vw, 40px); line-height: 1.3; letter-spacing: -0.025em; font-weight: 650; }
.intro-description { max-width: 400px; margin: 0; color: var(--text-secondary); font-size: 16px; }
.session-note { display: flex; align-items: flex-start; gap: 12px; max-width: 400px; margin: 32px 0 0; color: var(--text-secondary); }
.session-note svg { flex-shrink: 0; margin-top: 2px; color: var(--text-muted); }
.auth-card { width: 100%; max-width: 420px; justify-self: end; }
.card-description { margin: 0 0 24px; color: var(--text-secondary); }
.token-label { display: block; margin-bottom: 8px; font-weight: 500; }
.token-field { display: grid; grid-template-columns: minmax(0, 1fr) 44px; align-items: center; gap: 8px; }
.reveal-button { width: 44px; min-height: 44px; }
.auth-error { margin-top: 16px; }
.submit-button { width: 100%; min-height: 44px; margin-top: 24px; }
@media (max-width: 768px) {
  .auth-page { padding: 0 16px; }
  .auth-layout { grid-template-columns: minmax(0, 1fr); gap: 32px; min-height: auto; padding: 32px 0 48px; }
  .brand-link { margin-bottom: 32px; }
  h1 { margin-bottom: 16px; }
  .session-note { margin-top: 24px; }
  .auth-card { justify-self: center; }
  .back-button { min-height: 44px; }
}
</style>
