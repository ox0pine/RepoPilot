<script setup lang="ts">
import { computed, ref } from 'vue'
import BrandMark from '../components/BrandMark.vue'

const props = defineProps<{ error?: string; loading?: boolean }>()
const emit = defineEmits<{ authenticated: [token: string]; home: [] }>()
const token = ref('')
const reveal = ref(false)
const inputType = computed(() => reveal.value ? 'text' : 'password')

function submit(): void {
  const value = token.value.trim()
  if (value) emit('authenticated', value)
}
</script>

<template>
  <main class="auth-page" tabindex="-1">
    <a class="brand-link" href="/" aria-label="返回 RepoPilot 首页" @click.prevent="emit('home')"><BrandMark /></a>
    <section class="auth-card" aria-labelledby="auth-title">
      <p class="eyebrow">RepoPilot / 工作台</p><h1 id="auth-title">进入工作台</h1>
      <p class="intro">输入管理员提供的访问密钥，进入你的代码任务工作区。</p>
      <form @submit.prevent="submit">
        <label for="access-token">工作台访问密钥</label>
        <div class="token-field"><input id="access-token" v-model="token" :type="inputType" autocomplete="current-password" placeholder="粘贴工作台访问密钥" required><button class="button" type="button" @click="reveal = !reveal">{{ reveal ? '隐藏' : '显示' }}</button></div>
        <p class="session-note">访问密钥只保存在当前会话内，刷新页面后需要重新输入。</p>
        <p v-if="props.error" class="alert" role="alert">{{ props.error }}</p>
        <button class="button primary submit-button" type="submit" :disabled="!token.trim() || props.loading">{{ props.loading ? '验证中…' : '进入工作台' }}</button>
      </form>
    </section>
  </main>
</template>

<style scoped>
.auth-page { width: min(440px, 100%); margin: 64px auto; padding: 0 16px; }
.brand-link { display: inline-flex; margin-bottom: 28px; }.eyebrow { color: var(--text-muted); font-size: 12px; } h1 { margin: 6px 0 12px; font-size: 28px; }.intro, .session-note { color: var(--text-secondary); }.token-field { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 8px; } input { width: 100%; min-height: 40px; padding: 8px 11px; border: 1px solid var(--border); border-radius: 7px; background: var(--surface); color: var(--text); font: inherit; } input:focus { border-color: var(--accent); outline: 2px solid var(--accent-soft); }.session-note { margin-top: 10px; font-size: 12px; }.submit-button { width: 100%; margin-top: 20px; }.button { min-height: 40px; padding: 8px 14px; border: 1px solid var(--border); border-radius: 7px; background: var(--surface); color: var(--text); font: inherit; cursor: pointer; }.button:hover { background: var(--surface-hover); }.primary { border-color: var(--accent); background: var(--accent); color: var(--surface); }.primary:hover { border-color: var(--accent-hover); background: var(--accent-hover); }.button:disabled { opacity: .55; cursor: not-allowed; }.alert { margin-top: 14px; padding: 10px; border-radius: 7px; background: var(--danger-bg); color: var(--danger-text); }
</style>
