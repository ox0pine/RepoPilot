<script setup lang="ts">
import { computed, ref } from 'vue'
import { NAlert, NButton, NInput } from 'naive-ui'
import { Eye, EyeOff, KeyRound } from '@lucide/vue'
import BrandMark from '../components/BrandMark.vue'

const props = defineProps<{ error?: string; loading?: boolean }>()
const emit = defineEmits<{ authenticated: [token: string] }>()
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
    <main id="main" class="auth-layout" tabindex="-1">
      <div class="auth-brand"><BrandMark /></div>
      <h1>登录工作台</h1>
      <p class="intro-description">使用管理员提供的访问密钥进入工作台。模型服务与 GitHub 授权可在登录后配置。</p>
      <form class="auth-form" @submit.prevent="submit">
        <label class="token-label" for="access-token">访问密钥</label>
        <div class="token-field">
          <NInput
            v-model:value="token"
            :type="inputType"
            :disabled="props.loading"
            placeholder="输入访问密钥"
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
      <p id="session-note" class="session-note">
        <KeyRound :size="18" :stroke-width="1.75" aria-hidden="true" />
        <span>访问密钥仅在本次登录期间保留，刷新页面后需要重新登录。</span>
      </p>
    </main>
  </div>
</template>

<style scoped>
.auth-page { display: flex; flex-direction: column; justify-content: center; min-height: 100dvh; padding: 48px 24px; }
.auth-layout { width: 100%; max-width: 400px; margin: 0 auto; }
.auth-brand { margin-bottom: 36px; }
h1 { margin: 0 0 12px; font-size: 28px; line-height: 1.35; }
.intro-description { margin: 0 0 28px; color: var(--text-secondary); font-size: 15px; line-height: 1.7; }
.auth-form { padding: 20px; border: 1px solid var(--border); border-radius: 12px; background: var(--surface); }
.token-label { display: block; margin-bottom: 8px; font-size: 13px; font-weight: 600; }
.token-field { display: grid; grid-template-columns: minmax(0, 1fr) 44px; align-items: center; gap: 8px; }
.reveal-button { width: 44px; min-height: 44px; }
.auth-error { margin-top: 12px; }
.submit-button { width: 100%; min-height: 42px; margin-top: 16px; }
.session-note { display: flex; align-items: flex-start; gap: 8px; margin: 20px 0 0; color: var(--text-muted); font-size: 12px; line-height: 1.7; }
.session-note svg { flex-shrink: 0; margin-top: 1px; }
@media (max-width: 768px) {
  .auth-page { padding: 32px 16px; }
  .auth-brand { margin-bottom: 28px; }
  h1 { font-size: 24px; }
}
</style>
