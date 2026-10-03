<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch, type ComponentPublicInstance } from 'vue'
import { NAlert, NButton, NCollapse, NCollapseItem, NForm, NFormItem, NInput, NSpin, NTag, type InputInst } from 'naive-ui'
import { Copy, KeyRound, GitBranch } from '@lucide/vue'
import { ApiError } from '../api/client'
import { authorizeGitHubSettings, getGitHubSettings, updateGitHubSettings, type GitHubSettings } from '../api/settings'
import { sessionVersion } from '../stores/session'

const emit = defineEmits<{ busy: [value: boolean] }>()
const saved = ref<GitHubSettings | null>(null)
const apiToken = ref('')
const tokenEditing = ref(false)
const loading = ref(false)
const loaded = ref(false)
const saving = ref(false)
const authorizing = ref(false)
const loadError = ref('')
const saveError = ref('')
const saveNotice = ref('')
const authorizationError = ref('')
const authorizationNotice = ref('')
const copyNotice = ref('')
const saveUncertain = ref(false)
const tokenInput = ref<InputInst | null>(null)
const replaceButton = ref<ComponentPublicInstance | null>(null)
const busy = computed(() => saving.value || authorizing.value)
const canSave = computed(() => loaded.value && tokenEditing.value && !!apiToken.value.trim() && !busy.value && !loading.value && !saveUncertain.value)
const canAuthorize = computed(() => loaded.value && !!saved.value?.api_token_configured && !!saved.value?.private_key_configured && !!saved.value?.public_key && !tokenEditing.value && !apiToken.value && !busy.value && !loading.value && !saveUncertain.value)
let active = true
let controller: AbortController | undefined
const initialSession = sessionVersion()

watch(busy, value => emit('busy', value), { flush: 'sync' })
watch(() => sessionVersion(), () => {
  active = false
  controller?.abort()
  apiToken.value = ''
  saved.value = null
  loaded.value = false
  loading.value = false
  saving.value = false
  authorizing.value = false
}, { flush: 'sync' })

function isCurrent(): boolean {
  return active && initialSession === sessionVersion()
}

function beginRequest(): AbortSignal {
  controller = new AbortController()
  return controller.signal
}

async function editToken(): Promise<void> {
  tokenEditing.value = true
  apiToken.value = ''
  saveError.value = ''
  saveNotice.value = ''
  await nextTick()
  if (isCurrent()) tokenInput.value?.focus()
}

async function cancelEdit(): Promise<void> {
  if (busy.value || loading.value || saveUncertain.value) return
  apiToken.value = ''
  tokenEditing.value = false
  saveError.value = ''
  saveNotice.value = ''
  await nextTick()
  if (isCurrent()) replaceButton.value?.$el?.focus()
}

async function load(): Promise<void> {
  if (!isCurrent() || loading.value || busy.value) return
  loading.value = true
  loadError.value = ''
  const wasUncertain = saveUncertain.value
  try {
    const settings = await getGitHubSettings(beginRequest())
    if (!isCurrent()) return
    saved.value = settings
    loaded.value = true
    saveUncertain.value = false
    if (!settings.api_token_configured) tokenEditing.value = true
    if (wasUncertain) {
      saveError.value = ''
      saveNotice.value = '已重新读取服务器配置。无法判断草稿是否已保存；草稿仍保留，可明确选择再次保存。'
    }
  } catch (cause) {
    if (isCurrent()) loadError.value = cause instanceof Error ? cause.message : '无法加载 GitHub 设置'
  } finally {
    if (isCurrent()) loading.value = false
  }
  if (isCurrent() && loaded.value && !saved.value?.api_token_configured) {
    await nextTick()
    if (isCurrent()) tokenInput.value?.focus()
  }
}

async function save(): Promise<void> {
  if (!isCurrent() || !canSave.value) return
  saving.value = true
  saveError.value = ''
  saveNotice.value = ''
  try {
    const settings = await updateGitHubSettings({ api_token: apiToken.value.trim() }, beginRequest())
    if (!isCurrent()) return
    saved.value = settings
    apiToken.value = ''
    tokenEditing.value = false
    authorizationError.value = ''
    authorizationNotice.value = ''
    saveNotice.value = 'Token 已保存。本次保存未验证仓库访问权限，也未执行 SSH 公钥授权。'
  } catch (cause) {
    if (!isCurrent()) return
    saveUncertain.value = !(cause instanceof ApiError) || cause.status >= 500 || cause.status < 400
    saveError.value = cause instanceof Error ? cause.message : '保存失败'
    if (saveUncertain.value) saveNotice.value = '无法确认此次保存结果。草稿已保留，请先重新读取配置；不会自动重新提交。'
  } finally {
    if (isCurrent()) {
      saving.value = false
      if (!tokenEditing.value) {
        await nextTick()
        if (isCurrent()) replaceButton.value?.$el?.focus()
      }
    }
  }
}

async function authorize(): Promise<void> {
  if (!isCurrent() || !canAuthorize.value) return
  authorizing.value = true
  authorizationError.value = ''
  authorizationNotice.value = ''
  try {
    const result = await authorizeGitHubSettings(beginRequest())
    if (!isCurrent()) return
    if (result.registration.public_key !== saved.value?.public_key || result.settings.public_key !== saved.value?.public_key) {
      authorizationError.value = '服务器公钥与当前显示的配置不同，请重新读取配置后核对。'
      loadError.value = authorizationError.value
      loaded.value = false
      return
    }
    authorizationNotice.value = result.registration.status === 'created'
      ? 'SSH 公钥已添加到 GitHub。此结果不代表仓库读取权限已验证。'
      : 'GitHub 已有此 SSH 公钥。此结果不代表仓库读取权限已验证。'
  } catch (cause) {
    if (isCurrent()) authorizationError.value = `${cause instanceof Error ? cause.message : 'SSH 公钥授权失败'}。本操作不会更改已保存的 token；可在 GitHub 核对公钥后手动重试。`
  } finally {
    if (isCurrent()) authorizing.value = false
  }
}

async function copyPublicKey(): Promise<void> {
  const publicKey = saved.value?.public_key
  if (!publicKey) return
  copyNotice.value = ''
  try {
    await navigator.clipboard.writeText(publicKey)
    if (isCurrent() && saved.value?.public_key === publicKey) copyNotice.value = '公钥已复制'
  } catch {
    if (isCurrent()) copyNotice.value = '无法访问剪贴板，请选中公钥手动复制。'
  }
}

onMounted(() => void load())
onBeforeUnmount(() => {
  active = false
  controller?.abort()
  apiToken.value = ''
  emit('busy', false)
})
</script>

<template>
  <section class="github-settings" aria-labelledby="github-settings-title">
    <header class="section-heading">
      <h2 id="github-settings-title"><GitBranch :size="22" aria-hidden="true" /> GitHub</h2>
      <p>配置仓库来源访问与修复分支提交权限，用于读取 commit 和 Issue，并在用户显式操作后向自己的仓库推送唯一分支。</p>
    </header>

    <div v-if="loading" class="loading-state" role="status"><NSpin size="small" /> 正在读取 GitHub 配置…</div>
    <NAlert v-if="loadError" type="error" role="alert" class="feedback">{{ loadError }}</NAlert>
    <NButton v-if="loadError || (!loaded && !loading)" :disabled="busy || loading" @click="load">重新读取配置</NButton>

    <template v-if="loaded && saved">
      <section class="settings-card" aria-labelledby="repository-access-title">
        <div class="card-heading">
          <div><h3 id="repository-access-title">仓库访问</h3><p class="help">保存凭据，不自动授权 SSH。</p></div>
          <NTag :bordered="false" :type="saved.api_token_configured ? 'success' : 'default'">{{ saved.api_token_configured ? '凭据已保存' : '未配置' }}</NTag>
        </div>

        <div v-if="saved.api_token_configured && !tokenEditing" class="credential-summary">
          <div><strong>GitHub API token</strong><p class="help">已加密保存；仓库读取权限将在创建对话时校验，分支写权限仅在显式提交修复分支时校验。</p></div>
          <NButton ref="replaceButton" :disabled="busy || loading" @click="editToken">更换 token</NButton>
        </div>
        <NForm v-else label-placement="top" @submit.prevent="save">
          <NFormItem :label="saved.api_token_configured ? '新的 GitHub API token' : 'GitHub API token'" :label-props="{ for: 'github-api-token' }">
            <NInput ref="tokenInput" v-model:value="apiToken" type="password" show-password-on="click" :maxlength="4096" :disabled="busy || loading" :input-props="{ id: 'github-api-token', autocomplete: 'new-password', spellcheck: false }" placeholder="粘贴 GitHub Personal Access Token" />
          </NFormItem>
          <div class="actions">
            <NButton v-if="saved.api_token_configured" :disabled="busy || loading || saveUncertain" @click="cancelEdit">取消更换</NButton>
            <NButton type="primary" attr-type="submit" :disabled="!canSave" :loading="saving">{{ saving ? '正在保存…' : saved.api_token_configured ? '保存新 token' : '保存 token' }}</NButton>
          </div>
        </NForm>

        <NAlert v-if="saveError" type="error" role="alert" class="feedback">{{ saveError }}</NAlert>
        <NAlert v-if="saveNotice" :type="saveUncertain ? 'warning' : 'info'" role="status" class="feedback">{{ saveNotice }}</NAlert>
        <NButton v-if="saveUncertain" :disabled="loading || busy" @click="load">重新读取配置，确认保存状态</NButton>

        <div class="permission-guide">
          <h4>创建 token 时，选择需要访问的仓库</h4>
          <p class="help"><a href="https://github.com/settings/personal-access-tokens" target="_blank" rel="noopener noreferrer">创建 Fine-grained token ↗</a>，在 Repository permissions 中为来源读取开启以下权限；若要提交修复分支，将 Contents 提升为 Read and write：</p>
          <div class="permission-list"><span>Contents <strong>Read-only / 分支交付需 Read and write</strong></span><span>Issues <strong>Read-only</strong></span></div>
          <p class="help">分支交付仅允许 token 所属用户自己的仓库，不自动创建 PR 或合并。组织仓库可能需要管理员批准。</p>
          <details class="classic-guide"><summary>使用 Classic token？</summary><p class="help"><a href="https://github.com/settings/tokens" target="_blank" rel="noopener noreferrer">Classic token</a> 读取或写入私有仓库通常需要 <strong>repo</strong> scope，包含广泛权限。建议优先使用上述限定仓库的 Fine-grained token。</p></details>
        </div>
        <p class="help privacy-note">工作台共享配置。关闭设置或切换分类会丢弃未保存的草稿。</p>
      </section>

      <NCollapse class="advanced-settings">
        <NCollapseItem title="高级 · SSH 公钥（可选）" name="ssh">
          <section class="settings-card advanced-card" aria-labelledby="ssh-settings-title">
            <h3 id="ssh-settings-title"><KeyRound :size="18" aria-hidden="true" /> SSH 公钥授权</h3>
            <p class="help">生成目标通过 GitHub API 读取来源，<strong>不需要 SSH 授权</strong>。只有需要通过 SSH 访问 GitHub 时，才执行此操作。</p>
            <p class="help">{{ saved.public_key && saved.private_key_configured ? '本地密钥对已生成并保存，私钥不会上传；这不代表公钥已获 GitHub 授权。' : '保存 token 时会生成本地密钥对，已有密钥会复用。' }}</p>
            <template v-if="saved.public_key">
              <p class="key-label">本地生成的公钥</p>
              <pre class="public-key" tabindex="0" aria-label="SSH 公钥">{{ saved.public_key }}</pre>
              <NButton :disabled="busy || loading" @click="copyPublicKey"><template #icon><Copy :size="16" aria-hidden="true" /></template>复制公钥</NButton>
              <p v-if="copyNotice" class="help" role="status">{{ copyNotice }}</p>
            </template>
            <p class="help">如需授权，已保存的 fine-grained token 还需 Account permissions → <strong>Git SSH keys: Read and write</strong>；classic token 需要 <strong>read:public_key</strong> 和 <strong>write:public_key</strong>。这些额外权限不是生成目标的前置条件。</p>
            <p v-if="tokenEditing || saveUncertain" class="help">请先保存或取消 token 草稿；保存结果不明确时，先重新读取配置，再授权公钥。</p>
            <div class="ssh-actions"><NButton :disabled="!canAuthorize" :loading="authorizing" @click="authorize">{{ authorizing ? '正在授权公钥…' : '将已保存公钥授权到 GitHub' }}</NButton><a href="https://github.com/settings/keys" target="_blank" rel="noopener noreferrer">在 GitHub 查看公钥</a></div>
            <NAlert v-if="authorizationError" type="error" role="alert" class="feedback">{{ authorizationError }}</NAlert>
            <NAlert v-if="authorizationNotice" type="success" role="status" class="feedback">{{ authorizationNotice }}</NAlert>
          </section>
        </NCollapseItem>
      </NCollapse>
    </template>
  </section>
</template>

<style scoped>
.github-settings { min-width: 0; }
.section-heading { margin-bottom: 24px; }
h2, h3 { display: flex; align-items: center; gap: 8px; margin: 0; }
h2 { font-size: 23px; line-height: 1.4; }
h3 { font-size: 15px; }
h4 { margin: 0 0 8px; font-size: 13px; }
.section-heading > p { margin: 8px 0 0; color: var(--text-secondary); font-size: 13px; line-height: 1.6; }
.settings-card { padding: 20px; border: 1px solid var(--border); border-radius: 12px; background: var(--surface); }
.card-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; margin-bottom: 20px; }
.card-heading :deep(.n-tag) { flex-shrink: 0; }
.help { color: var(--text-secondary); font-size: 13px; margin: 8px 0; line-height: 1.7; overflow-wrap: anywhere; }
.credential-summary { display: flex; justify-content: space-between; align-items: center; gap: 16px; padding: 16px; background: var(--bg); border-radius: 8px; }
.credential-summary :deep(.n-button) { flex-shrink: 0; }
.actions { display: flex; justify-content: flex-end; flex-wrap: wrap; gap: 12px; margin-bottom: 16px; }
.permission-guide { margin-top: 20px; padding-top: 20px; border-top: 1px solid var(--border-soft); }
.permission-list { display: flex; flex-wrap: wrap; gap: 8px; margin: 12px 0; }
.permission-list > span { padding: 6px 10px; border: 1px solid var(--border-soft); border-radius: 6px; font-size: 12px; }
.permission-list strong { margin-left: 8px; color: var(--text-muted); font-weight: 400; }
.classic-guide { margin-top: 12px; color: var(--text-secondary); font-size: 12px; }
.classic-guide summary { cursor: pointer; }
.classic-guide summary:focus-visible { outline: 2px solid var(--accent); outline-offset: 3px; }
.privacy-note { margin-top: 16px; }
.advanced-settings { margin-top: 24px; }
.advanced-card { border-color: var(--border-soft); }
.key-label { margin: 20px 0 8px; font-weight: 600; font-size: 13px; }
.public-key { margin: 0 0 12px; padding: 12px; background: var(--code-bg); border: 1px solid var(--border); border-radius: 8px; white-space: pre-wrap; overflow-wrap: anywhere; font-size: 12px; }
.ssh-actions { display: flex; flex-wrap: wrap; align-items: center; gap: 16px; margin-top: 20px; }
.feedback { margin-top: 16px; overflow-wrap: anywhere; }
.loading-state { display: flex; align-items: center; gap: 12px; margin-bottom: 20px; color: var(--text-secondary); }
a { color: var(--accent); overflow-wrap: anywhere; }
@media (max-width: 640px) {
  .settings-card { padding: 16px; }
  .card-heading { gap: 8px; }
  .credential-summary { flex-direction: column; align-items: flex-start; gap: 12px; }
  .credential-summary :deep(.n-button), .actions > *, .ssh-actions :deep(.n-button) { width: 100%; }
  .ssh-actions :deep(.n-button__content) { white-space: normal; }
  .ssh-actions :deep(.n-button) { height: auto; min-height: 40px; padding-top: 8px; padding-bottom: 8px; }
}
</style>
