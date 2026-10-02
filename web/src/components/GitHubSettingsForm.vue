<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { NAlert, NButton, NCollapse, NCollapseItem, NForm, NFormItem, NInput, NSpin, NTag } from 'naive-ui'
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
    <div class="panel-heading"><h2 id="github-settings-title">连接 GitHub</h2><p class="help">填写 token，自动配置 SSH 密钥并添加公钥到 GitHub。</p></div>
    <div v-if="loading" role="status"><NSpin size="small" /> 正在加载设置…</div>
    <NAlert v-if="error" type="error" role="alert" class="feedback">{{ error }}</NAlert>
    <NAlert v-if="notice" type="info" role="status" class="feedback">{{ notice }}</NAlert>
    <NAlert v-if="registration" type="success" role="status" class="feedback">{{ registration }}</NAlert>
    <NButton v-if="!loading && !loaded" attr-type="button" :disabled="busy" @click="load">重试加载</NButton>
    <NForm v-if="loaded && saved" label-placement="top" @submit.prevent="save">
      <div class="configuration">
        <p class="field-label">本地 SSH 密钥配置</p>
        <NTag :bordered="false">{{ saved.public_key && saved.private_key_configured ? '已保存，授权时复用' : '授权时自动配置' }}</NTag>
        <NCollapse v-if="saved.public_key" class="details"><NCollapseItem title="查看公钥" name="public-key" :disabled="busy"><pre class="public-key">{{ saved.public_key }}</pre></NCollapseItem></NCollapse>
      </div>
      <NFormItem label="GitHub API token" :label-props="{ for: 'github-api-token' }">
        <div class="field-content">
          <p v-if="saved.api_token_configured" class="help">已保存，不回显。留空保留原 token。</p>
          <template v-if="saved.api_token_configured && !tokenEditing">
            <NInput type="password" value="****************" :disabled="busy" :input-props="{ id: 'github-api-token', readonly: true, autocomplete: 'off' }" />
            <NButton class="replace-token" attr-type="button" :disabled="busy" @click="tokenEditing = true">更换 token</NButton>
          </template>
          <NInput v-else :value="apiToken" type="password" :maxlength="4096" :disabled="busy" :input-props="{ id: 'github-api-token', maxlength: 4096, autocomplete: 'new-password', spellcheck: false }" :placeholder="saved.api_token_configured ? '输入新 token，留空保留原值' : '粘贴 GitHub token'" @update:value="apiToken = $event" />
          <NCollapse class="details"><NCollapseItem title="如何获取 token？" name="permissions" :disabled="busy">
            <p class="help"><a href="https://github.com/settings/personal-access-tokens" target="_blank" rel="noopener noreferrer">创建 fine-grained token</a>，账户权限选择 Git SSH keys: Read and write。</p>
            <p class="help">使用 <a href="https://github.com/settings/tokens" target="_blank" rel="noopener noreferrer">classic token</a> 时，勾选 read:public_key 和 write:public_key。</p>
          </NCollapseItem></NCollapse>
        </div>
      </NFormItem>
      <div class="actions"><NButton type="primary" attr-type="submit" :disabled="!canSave" :loading="saving">{{ saving ? '保存并授权中…' : '保存并授权' }}</NButton></div>
      <p class="help">工作台共享配置。token 和私钥加密保存，私钥不上传。</p>
      <p class="help">切换分类或关闭窗口会丢弃未保存内容。</p>
      <a href="https://github.com/settings/keys" target="_blank" rel="noopener noreferrer">查看 GitHub 公钥</a>
    </NForm>
  </section>
</template>

<style scoped>
.panel-heading { margin-bottom: 24px; }
h2 { margin-bottom: 8px; }
.help { color: var(--text-secondary); font-size: 12px; margin: 8px 0; line-height: 1.6; }
.configuration { margin-bottom: 24px; }
.field-label { margin-bottom: 8px; font-weight: 600; }
.field-content { width: 100%; min-width: 0; }
.public-key { margin: 8px 0; padding: 12px; background: var(--code-bg); border: 1px solid var(--border); border-radius: 8px; white-space: pre-wrap; overflow-wrap: anywhere; font-size: 12px; }
.details, .replace-token { margin-top: 12px; }
.actions { display: flex; justify-content: flex-end; margin-bottom: 16px; }
.feedback { margin-bottom: 16px; overflow-wrap: anywhere; }
@media (max-width: 640px) { .actions > * { width: 100%; } }
</style>
