<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { NAlert, NButton, NForm, NFormItem, NInput, NSelect, NSpin } from 'naive-ui'
import { Check, KeyRound, RefreshCw, Server } from '@lucide/vue'
import { getSettings, refreshModels, updateModelSettings, type ModelSettings } from '../api/settings'

const emit = defineEmits<{ busy: [value: boolean] }>()
const keyInput = ref<InstanceType<typeof NInput> | null>(null)
const changeKeyButton = ref<InstanceType<typeof NButton> | null>(null)
const loadedModel = ref('')
let active = true
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
const usingSavedKey = computed(() => settings.value.api_key_configured && settings.value.base_url.trim() === loadedBaseUrl.value)
const apiKeyEditing = ref(false)
watch(saving, value => emit('busy', value), { flush: 'sync' })

let refreshSequence = 0
async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    const result = await getSettings()
    if (!active) return
    settings.value = result
    loadedModel.value = result.model
    loadedBaseUrl.value = settings.value.base_url
    loaded.value = true
    if (settings.value.model) models.value = [settings.value.model]
  } catch (cause) {
    if (active) error.value = cause instanceof Error ? cause.message : '无法加载设置'
  } finally {
    if (active) loading.value = false
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
    if (!active || sequence !== refreshSequence || baseUrl !== settings.value.base_url.trim() || key !== apiKey.value) return
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
    if (!active) return
    settings.value = saved
    loadedBaseUrl.value = saved.base_url
    loadedModel.value = saved.model
    apiKey.value = ''
    apiKeyEditing.value = false
    if (saved.model && !models.value.includes(saved.model)) models.value.unshift(saved.model)
    notice.value = '模型设置已保存'
  } catch (cause) {
    if (active) error.value = cause instanceof Error ? cause.message : '保存失败'
  } finally {
    if (active) saving.value = false
  }
}

async function startApiKeyEdit(): Promise<void> {
  apiKeyEditing.value = true
  await nextTick()
  keyInput.value?.focus()
}

async function cancelApiKeyEdit(): Promise<void> {
  apiKey.value = ''
  apiKeyEditing.value = false
  if (settings.value.base_url.trim() === loadedBaseUrl.value && loadedModel.value) {
    models.value = [loadedModel.value]
    settings.value.model = loadedModel.value
  }
  await nextTick()
  changeKeyButton.value?.$el?.focus()
}

onMounted(() => void load())
onBeforeUnmount(() => { active = false; refreshSequence++; apiKey.value = ''; emit('busy', false) })
</script>

<template>
  <section class="panel" aria-labelledby="model-settings-title">
    <div class="panel-heading"><h2 id="model-settings-title">模型服务</h2><p class="description">连接 OpenAI 兼容服务，用于生成和修改目标。</p></div>
    <div v-if="loading" role="status"><NSpin size="small" /> 正在加载设置…</div>
    <NAlert v-if="error" type="error" role="alert" class="feedback">{{ error }}</NAlert>
    <NAlert v-if="notice" type="info" role="status" class="feedback">{{ notice }}</NAlert>
    <NButton v-if="!loading && !loaded" attr-type="button" :disabled="loading" @click="load">重试加载</NButton>
    <NForm v-if="loaded" label-placement="top" @submit.prevent="save">
      <section class="settings-card" aria-labelledby="connection-title">
        <div class="card-heading"><span class="card-icon"><Server :size="19" aria-hidden="true" /></span><div><h3 id="connection-title">连接信息</h3><p class="help">填写服务地址及该服务的访问密钥</p></div></div>
        <NFormItem label="服务地址" :label-props="{ for: 'base-url' }">
          <div class="field-content"><NInput :value="settings.base_url" :disabled="saving" :input-props="{ id: 'base-url', autocomplete: 'url', spellcheck: false }" placeholder="https://api.openai.com/v1" @update:value="settings.base_url = $event" /><p class="help">使用 API 基础地址，通常以 <code>/v1</code> 结尾。</p></div>
        </NFormItem>
        <div v-if="usingSavedKey && !apiKeyEditing" class="saved-key"><KeyRound :size="18" aria-hidden="true" /><div><strong>API Key 已保存</strong><p class="help">密钥不会回显，当前端点继续使用已保存的密钥。</p></div><NButton ref="changeKeyButton" attr-type="button" :disabled="saving" @click="startApiKeyEdit">更换</NButton></div>
        <NFormItem v-else label="API Key" :label-props="{ for: 'api-key' }">
          <div class="field-content"><NInput ref="keyInput" v-model:value="apiKey" type="password" show-password-on="click" :disabled="saving" :input-props="{ id: 'api-key', autocomplete: 'new-password', spellcheck: false }" placeholder="输入密钥；免密服务可留空" /><div class="key-help"><p class="help">{{ usingSavedKey ? '留空会保留原密钥。取消可恢复已保存的模型。' : '更换服务地址后，不会沿用原服务的密钥。' }}</p><NButton v-if="usingSavedKey && apiKeyEditing" text attr-type="button" :disabled="saving" @click="cancelApiKeyEdit">取消更换</NButton></div></div>
        </NFormItem>
      </section>
      <section class="settings-card" aria-labelledby="selection-title">
        <div class="card-heading"><span class="card-icon"><Check :size="19" aria-hidden="true" /></span><div><h3 id="selection-title">选择模型</h3><p class="help">从当前端点读取可用模型，再保存设置</p></div></div>
        <NFormItem label="当前模型" :label-props="{ id: 'model-label', for: 'model' }">
          <div class="model-row"><NSelect :value="settings.model || null" :options="models.map(model => ({ label: model, value: model }))" filterable clearable :disabled="saving || refreshing" :input-props="{ id: 'model', 'aria-labelledby': 'model-label' }" placeholder="请先获取模型列表" @update:value="settings.model = $event ?? ''" /><NButton attr-type="button" :disabled="saving || refreshing || !settings.base_url.trim()" :loading="refreshing" @click="refresh"><template #icon><RefreshCw :size="16" aria-hidden="true" /></template>获取模型</NButton></div>
        </NFormItem>
        <p class="help">只读取模型列表，不会发起目标生成。</p>
      </section>
      <div class="actions"><p class="help">设置对整个工作台生效</p><NButton type="primary" attr-type="submit" :loading="saving" :disabled="saving || refreshing || !settings.base_url.trim() || !models.includes(settings.model)">保存模型设置</NButton></div>
    </NForm>
  </section>
</template>

<style scoped>
.panel-heading { margin-bottom: 24px; }
h2 { margin: 0; font-size: 23px; line-height: 1.4; }
.description { margin: 8px 0 0; color: var(--text-secondary); font-size: 13px; }
h3 { margin: 0; font-size: 15px; }
.settings-card { padding: 20px; border: 1px solid var(--border); border-radius: 12px; margin-bottom: 20px; background: var(--surface); }
.card-heading { display: flex; align-items: center; gap: 12px; margin-bottom: 22px; }
.card-icon { display: grid; place-items: center; width: 36px; height: 36px; flex-shrink: 0; border-radius: 10px; color: var(--accent); background: var(--accent-soft); }
.help { color: var(--text-secondary); margin: 6px 0 0; font-size: 12px; line-height: 1.6; }
.field-content, .model-row { width: 100%; min-width: 0; }
.model-row { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 8px; }
.saved-key { display: flex; align-items: center; gap: 12px; padding: 14px; background: var(--bg); border-radius: 10px; }
.saved-key > svg { flex-shrink: 0; color: var(--text-muted); }
.saved-key > div { flex: 1; min-width: 0; }
.saved-key strong { font-size: 13px; font-weight: 500; }
.saved-key .help { margin-top: 3px; }
.key-help { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; }
.key-help > button { margin-top: 6px; flex-shrink: 0; }
.actions { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 12px; }
.actions .help { margin: 0; }
.feedback { margin-bottom: 16px; overflow-wrap: anywhere; }
code { background: var(--code-bg); border-radius: 4px; padding: 2px 4px; }
@media (max-width: 640px) { .settings-card { padding: 16px; } .model-row { grid-template-columns: 1fr; } .saved-key { flex-wrap: wrap; } .actions > button { width: 100%; } }
</style>
