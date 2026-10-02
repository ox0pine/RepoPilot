<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { getGitHubSettings, updateGitHubSettings, type GitHubSettings } from '../api/settings'

const emit = defineEmits<{ busy: [value: boolean] }>()
const saved = ref<GitHubSettings | null>(null)
const apiToken = ref('')
const tokenEditing = ref(false)
const loaded = ref(false)
const loading = ref(true)
const saving = ref(false)
const error = ref('')
const notice = ref('')
const registration = ref('')
const busy = computed(() => saving.value)
const canSave = computed(() => loaded.value && !busy.value && (!!apiToken.value.trim() || !!saved.value?.api_token_configured))
const persistedFailurePrefix = 'GitHub settings were saved, but public key registration failed: '
let active = true
let discardDraftOnReload = false

watch(busy, value => emit('busy', value), { flush: 'sync' })
watch([apiToken, tokenEditing], () => { registration.value = ''; notice.value = '' }, { flush: 'sync' })

function discardDraft(): void {
  apiToken.value = ''
  tokenEditing.value = false
  discardDraftOnReload = false
}

async function load(): Promise<void> {
  if (loading.value && loaded.value || busy.value) return
  loading.value = true
  loaded.value = false
  error.value = ''
  registration.value = ''
  notice.value = ''
  try {
    const settings = await getGitHubSettings()
    if (!active) return
    saved.value = settings
    loaded.value = true
    if (discardDraftOnReload) {
      discardDraft()
      notice.value = '配置已保存，授权未成功，可重试。'
    }
  } catch (cause) {
    if (active) error.value = cause instanceof Error ? cause.message : '无法加载 GitHub 设置'
  } finally {
    if (active) loading.value = false
  }
}

async function save(): Promise<void> {
  if (!canSave.value) return
  saving.value = true
  error.value = ''; notice.value = ''; registration.value = ''
  try {
    const result = await updateGitHubSettings(apiToken.value.trim() ? { api_token: apiToken.value } : {})
    if (!active) return
    saved.value = result.settings
    discardDraft()
    if (result.registration.public_key === result.settings.public_key) {
      registration.value = result.registration.status === 'created'
        ? '已保存，公钥已添加到 GitHub'
        : '已保存，GitHub 已有此公钥'
    } else {
      error.value = '注册公钥与当前配置不一致，请重新加载后核对。'
      loaded.value = false
    }
  } catch (cause) {
    if (!active) return
    const detail = cause instanceof Error ? cause.message : '保存并授权失败'
    error.value = detail
    const persisted = detail.startsWith(persistedFailurePrefix)
    discardDraftOnReload = persisted
    try {
      const settings = await getGitHubSettings()
      if (!active) return
      saved.value = settings
      loaded.value = true
      if (persisted) {
        discardDraft()
        notice.value = '配置已保存，授权未成功，可重试。'
      } else {
        notice.value = '已加载当前配置，请检查错误后重试。'
      }
    } catch {
      if (!active) return
      loaded.value = false
      notice.value = persisted
        ? '配置已保存，授权失败。请重新加载后重试。'
        : '无法确认保存结果，请重新加载。草稿暂时保留。'
    }
  } finally {
    if (active) saving.value = false
  }
}

onMounted(() => void load())
onBeforeUnmount(() => { active = false; apiToken.value = ''; emit('busy', false) })
</script>

<template>
  <section class="panel" aria-labelledby="github-settings-title">
    <div class="panel-heading"><div><h2 id="github-settings-title">连接 GitHub</h2><p class="muted">填写 token，自动生成 SSH 密钥并添加公钥到 GitHub。</p></div></div>
    <p v-if="loading" role="status">正在加载设置…</p>
    <p v-if="error" class="alert" role="alert">{{ error }}</p>
    <p v-if="notice" class="notice" role="status">{{ notice }}</p>
    <p v-if="registration" class="notice" role="status">{{ registration }}</p>
    <button v-if="!loading && !loaded" class="button" type="button" :disabled="busy" @click="load">重试加载</button>
    <form v-if="loaded && saved" @submit.prevent="save">
      <fieldset class="settings-fields" :disabled="busy">
        <div class="field">
          <p class="field-label">SSH 密钥</p>
          <p class="help">{{ saved.public_key && saved.private_key_configured ? '已保存，授权时复用' : '授权时自动配置' }}</p>
          <details v-if="saved.public_key">
            <summary>查看公钥</summary>
            <pre class="public-key">{{ saved.public_key }}</pre>
          </details>
        </div>
        <div class="field">
          <label for="github-api-token">GitHub API token</label>
          <p v-if="saved.api_token_configured" class="help">已保存，不回显。留空保留原 token。</p>
          <template v-if="saved.api_token_configured && !tokenEditing">
            <input id="github-api-token" type="password" value="****************" readonly autocomplete="off" aria-label="已保存的 GitHub API token">
            <button class="button" type="button" @click="tokenEditing = true">更换 token</button>
          </template>
          <input v-else id="github-api-token" v-model="apiToken" type="password" maxlength="4096" autocomplete="new-password" spellcheck="false" :required="!saved.api_token_configured" :placeholder="saved.api_token_configured ? '输入新 token，留空保留原值' : '粘贴 GitHub token'">
          <details class="help">
            <summary>如何获取 token？</summary>
            <p><a href="https://github.com/settings/personal-access-tokens" target="_blank" rel="noopener noreferrer">创建 fine-grained token</a>，账户权限选择 Git SSH keys: Read and write。</p>
            <p>使用 <a href="https://github.com/settings/tokens" target="_blank" rel="noopener noreferrer">classic token</a> 时，勾选 read:public_key 和 write:public_key。</p>
          </details>
        </div>
        <div class="actions"><button class="button primary" type="submit" :disabled="!canSave">{{ saving ? '保存并授权中…' : '保存并授权' }}</button></div>
        <p class="help">工作台共享配置。token 和私钥加密保存，私钥不上传。</p>
        <p class="help">切换分类或关闭窗口会丢弃未保存内容。</p>
        <a href="https://github.com/settings/keys" target="_blank" rel="noopener noreferrer">查看 GitHub 公钥</a>
      </fieldset>
    </form>
  </section>
</template>

<style scoped>
.settings-fields { padding: 0; margin: 0; border: 0; min-width: 0; }
.panel { padding: 24px; border: 1px solid var(--border); border-radius: 10px; background: var(--surface); }
.panel-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; margin-bottom: 22px; }
h2 { margin-bottom: 5px; font-size: 18px; }
.muted, .help { color: var(--text-secondary); }
.help { font-size: 12px; margin-top: 6px; line-height: 1.6; }
.field { margin-bottom: 20px; }
label { display: block; margin-bottom: 6px; font-weight: 600; }
input { box-sizing: border-box; width: 100%; min-height: 40px; padding: 8px 11px; border: 1px solid var(--border); border-radius: 6px; background: var(--surface); color: var(--text); font: inherit; }
.field-label { margin-bottom: 6px; font-weight: 600; }
.public-key { margin-top: 8px; padding: 10px; border: 1px solid var(--border); border-radius: 6px; white-space: pre-wrap; overflow-wrap: anywhere; font-size: 12px; }
summary { cursor: pointer; color: var(--text-secondary); font-size: 12px; }
input:focus { outline: 2px solid var(--accent-soft); border-color: var(--accent); }
.button { min-height: 40px; padding: 8px 14px; border: 1px solid var(--border); border-radius: 6px; background: var(--surface); color: var(--text); font: inherit; cursor: pointer; }
.button:hover { background: var(--surface-hover); }
.primary { background: var(--accent); color: white; border-color: var(--accent); }
button:disabled { opacity: .55; cursor: not-allowed; }
.alert, .notice { padding: 10px 12px; border-radius: 6px; margin-bottom: 16px; overflow-wrap: anywhere; }
.alert { background: var(--danger-soft); color: var(--danger); }
.notice { background: var(--accent-soft); color: var(--accent-hover); }
a { color: var(--accent-hover); }
</style>
