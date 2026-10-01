<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { getSettings, refreshModels, updateModelSettings, type ModelSettings } from '../api/settings'

const settings = ref<ModelSettings>({ base_url: '', model: '', api_key_configured: false })
const apiKey = ref('')
const loadedBaseUrl = ref('')
const models = ref<string[]>([])
const loading = ref(true)
const loaded = ref(false)
const refreshing = ref(false)
const saving = ref(false)
const error = ref('')
const notice = ref('')
const maskedApiKey = '****************'
const apiKeyEditing = ref(false)

let refreshSequence = 0
async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    settings.value = await getSettings()
    loadedBaseUrl.value = settings.value.base_url
    loaded.value = true
    if (settings.value.model) models.value = [settings.value.model]
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '无法加载设置'
  } finally {
    loading.value = false
  }
}
watch([() => settings.value.base_url, apiKey], () => {
  if (loading.value || saving.value) return
  refreshSequence += 1
  models.value = []
  settings.value.model = ''
  refreshing.value = false
  notice.value = ''
  error.value = ''
}, { flush: 'sync' })

async function refresh(): Promise<void> {
  const sequence = ++refreshSequence
  const baseUrl = settings.value.base_url.trim()
  const key = apiKey.value
  refreshing.value = true
  error.value = ''
  notice.value = ''
  try {
    const result = await refreshModels({ base_url: baseUrl, api_key: key || (baseUrl === loadedBaseUrl.value ? undefined : '') })
    if (sequence !== refreshSequence || baseUrl !== settings.value.base_url.trim() || key !== apiKey.value) return
    models.value = result.models
    if (!models.value.includes(settings.value.model)) settings.value.model = ''
    notice.value = result.models.length ? `已发现 ${result.models.length} 个模型` : '端点可访问，但没有返回模型'
  } catch (cause) {
    if (sequence === refreshSequence) error.value = cause instanceof Error ? cause.message : '模型刷新失败'
  } finally {
    if (sequence === refreshSequence) refreshing.value = false
  }
}

async function save(): Promise<void> {
  if (!loaded.value || !models.value.includes(settings.value.model)) return
  refreshSequence += 1
  refreshing.value = false
  saving.value = true
  error.value = ''
  notice.value = ''
  const baseUrl = settings.value.base_url.trim()
  try {
    const saved = await updateModelSettings({ base_url: baseUrl, api_key: apiKey.value || (baseUrl === loadedBaseUrl.value ? undefined : ''), model: settings.value.model })
    settings.value = saved
    loadedBaseUrl.value = saved.base_url
    apiKeyEditing.value = false
    if (saved.model && !models.value.includes(saved.model)) models.value.unshift(saved.model)
    notice.value = '模型设置已保存'
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '保存失败'
  } finally {
    saving.value = false
  }
}

function startApiKeyEdit(): void {
  if (settings.value.api_key_configured && !apiKeyEditing.value) {
    apiKeyEditing.value = true
    apiKey.value = ''
  }
}

onMounted(() => void load())
</script>

<template>
  <section class="panel" aria-labelledby="model-settings-title">
    <div class="panel-heading"><div><p class="eyebrow">模型提供方</p><h2 id="model-settings-title">模型与 API</h2><p class="muted">配置 OpenAI 兼容端点，并从端点读取可用模型。</p></div><span class="status">{{ settings.api_key_configured ? '已配置密钥' : '未配置密钥' }}</span></div>
    <p v-if="loading" role="status">正在加载设置…</p><p v-if="error" class="alert" role="alert">{{ error }}</p><p v-if="notice" class="notice" role="status">{{ notice }}</p>
    <button v-if="!loading && !loaded" class="button" type="button" @click="load">重试加载</button>
    <form v-if="loaded" class="settings-form" @submit.prevent="save">
      <fieldset :disabled="saving" class="settings-fields">
        <div class="field">
          <label for="base-url">Base URL</label>
          <input id="base-url" v-model="settings.base_url" placeholder="https://api.openai.com/v1" autocomplete="url" required>
          <p class="help">服务端会请求此地址下的 <code>/models</code> 端点。</p>
        </div>
        <div class="field">
          <label for="api-key">API Key</label>
          <input id="api-key" :value="settings.api_key_configured && !apiKeyEditing ? maskedApiKey : apiKey" type="password" :readonly="settings.api_key_configured && !apiKeyEditing" :placeholder="settings.api_key_configured ? maskedApiKey : '输入模型服务密钥（可留空）'" autocomplete="new-password" @focus="startApiKeyEdit" @input="apiKey = ($event.target as HTMLInputElement).value">
          <p class="help">密钥不会在页面回显。仅在端点不变时，留空才会保留已保存的密钥；更换端点不会复用旧密钥。</p>
        </div>
        <div class="field">
          <label for="model">模型</label>
          <div class="model-row">
            <select id="model" v-model="settings.model"><option value="" disabled>先刷新模型列表</option><option v-for="model in models" :key="model" :value="model">{{ model }}</option></select>
            <button class="button" type="button" :disabled="refreshing || !settings.base_url.trim()" @click="refresh">{{ refreshing ? '刷新中…' : '从端点刷新' }}</button>
          </div>
        </div>
        <div class="actions"><button class="button primary" type="submit" :disabled="saving || refreshing || !settings.base_url.trim() || !settings.model">{{ saving ? '保存中…' : '保存设置' }}</button></div>
      </fieldset>
    </form>
  </section>
</template>

<style scoped>
.settings-fields { padding: 0; margin: 0; border: 0; min-width: 0; }
.panel { padding: 24px; border: 1px solid var(--border); border-radius: 10px; background: var(--surface); }.panel-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; margin-bottom: 22px; }.eyebrow { margin-bottom: 5px; color: var(--text-muted); font-size: 11px; letter-spacing: .05em; text-transform: uppercase; } h2 { margin-bottom: 5px; font-size: 18px; }.muted, .help { color: var(--text-secondary); }.status { padding: 4px 8px; border-radius: 6px; background: var(--accent-soft); color: var(--accent-hover); font-size: 11px; white-space: nowrap; }.field { margin-bottom: 20px; } label { display: block; margin-bottom: 6px; font-weight: 600; } input, select { width: 100%; min-height: 40px; padding: 8px 11px; border: 1px solid var(--border); border-radius: 7px; background: var(--surface); color: var(--text); font: inherit; } input:focus, select:focus { border-color: var(--accent); outline: 2px solid var(--accent-soft); }.help { margin: 6px 0 0; font-size: 12px; }.model-row { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 8px; }.button { min-height: 40px; padding: 8px 14px; border: 1px solid var(--border); border-radius: 7px; background: var(--surface); color: var(--text); font: inherit; cursor: pointer; }.button:hover { background: var(--surface-hover); }.primary { border-color: var(--accent); background: var(--accent); color: var(--surface); }.primary:hover { border-color: var(--accent-hover); background: var(--accent-hover); }.button:disabled { opacity: .55; cursor: not-allowed; }.actions { display: flex; justify-content: flex-end; padding-top: 4px; }.alert, .notice { padding: 11px 12px; border-radius: 7px; }.alert { background: var(--danger-bg); color: var(--danger-text); }.notice { background: var(--success-bg); color: var(--success-text); } code { padding: 2px 4px; border-radius: 4px; background: var(--code-bg); } @media (max-width: 640px) { .panel { padding: 16px; }.panel-heading { flex-direction: column; }.model-row { grid-template-columns: 1fr; }.actions .button { width: 100%; } }
</style>
