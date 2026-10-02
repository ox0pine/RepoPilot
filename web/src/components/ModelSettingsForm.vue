<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { NAlert, NButton, NForm, NFormItem, NInput, NSelect, NSpin, NTag } from 'naive-ui'
import { RefreshCw } from '@lucide/vue'
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
  if (saving.value || refreshing.value || !settings.value.base_url.trim()) return
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
    if (sequence === refreshSequence) {
      models.value = []
      settings.value.model = ''
      error.value = cause instanceof Error ? cause.message : '模型刷新失败'
    }
  } finally {
    if (sequence === refreshSequence) refreshing.value = false
  }
}

async function save(): Promise<void> {
  if (!loaded.value || saving.value || refreshing.value || !settings.value.base_url.trim() || !models.value.includes(settings.value.model)) return
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
    <div class="panel-heading"><div><h2 id="model-settings-title">模型与 API</h2><p class="help">配置 OpenAI 兼容端点，并从端点读取可用模型。</p></div><NTag v-if="loaded" :bordered="false">{{ settings.api_key_configured ? '已配置密钥' : '未配置密钥' }}</NTag></div>
    <div v-if="loading" role="status"><NSpin size="small" /> 正在加载设置…</div>
    <NAlert v-if="error" type="error" role="alert" class="feedback">{{ error }}</NAlert>
    <NAlert v-if="notice" type="info" role="status" class="feedback">{{ notice }}</NAlert>
    <NButton v-if="!loading && !loaded" attr-type="button" :disabled="loading" @click="load">重试加载</NButton>
    <NForm v-if="loaded" label-placement="top" @submit.prevent="save">
      <NFormItem label="Base URL" :label-props="{ for: 'base-url' }">
        <div class="field-content"><NInput :value="settings.base_url" :disabled="saving" :input-props="{ id: 'base-url', autocomplete: 'url', spellcheck: false }" placeholder="https://api.openai.com/v1" @update:value="settings.base_url = $event" /><p class="help">服务端会请求此地址下的 <code>/models</code> 端点。</p></div>
      </NFormItem>
      <NFormItem label="API Key" :label-props="{ for: 'api-key' }">
        <div class="field-content"><NInput :value="settings.api_key_configured && !apiKeyEditing ? maskedApiKey : apiKey" type="password" :disabled="saving" :input-props="{ id: 'api-key', autocomplete: 'new-password', spellcheck: false, readonly: settings.api_key_configured && !apiKeyEditing }" placeholder="输入模型服务密钥（可留空）" @focus="startApiKeyEdit" @update:value="apiKey = $event" /><p class="help">同一端点留空保留密钥；更换端点需重新填写</p></div>
      </NFormItem>
      <NFormItem label="模型" :label-props="{ id: 'model-label', for: 'model' }">
        <div class="model-row">
          <NSelect :value="settings.model || null" :options="models.map(model => ({ label: model, value: model }))" filterable clearable :disabled="saving || refreshing" :input-props="{ id: 'model', 'aria-labelledby': 'model-label' }" placeholder="先刷新模型列表" @update:value="settings.model = $event ?? ''" />
          <NButton attr-type="button" :disabled="saving || refreshing || !settings.base_url.trim()" :loading="refreshing" @click="refresh"><template #icon><RefreshCw :size="18" :stroke-width="1.75" aria-hidden="true" /></template>从端点刷新</NButton>
        </div>
      </NFormItem>
      <div class="actions"><NButton type="primary" attr-type="submit" :loading="saving" :disabled="saving || refreshing || !settings.base_url.trim() || !models.includes(settings.model)">保存设置</NButton></div>
    </NForm>
  </section>
</template>

<style scoped>
.panel-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; margin-bottom: 24px; }
h2 { margin-bottom: 8px; }
.help { color: var(--text-secondary); margin: 8px 0 0; font-size: 12px; }
.field-content, .model-row { width: 100%; min-width: 0; }
.model-row { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 8px; }
.actions { display: flex; justify-content: flex-end; }
.feedback { margin-bottom: 16px; overflow-wrap: anywhere; }
code { background: var(--code-bg); border-radius: 4px; padding: 2px 4px; }
@media (max-width: 640px) { .panel-heading { flex-direction: column; } .model-row { grid-template-columns: 1fr; } .actions > * { width: 100%; } }
</style>
