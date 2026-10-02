<script setup lang="ts">
import { onBeforeUnmount, reactive, ref, watch } from 'vue'
import { NAlert, NButton, NInput, type InputInst } from 'naive-ui'
import { ArrowLeft, GitBranch, Plus, Settings } from '@lucide/vue'
import { ApiError } from '../api/client'
import { createTask, type CreateTask } from '../api/tasks'
import { followLink, navigate } from '../router'
import { sessionVersion } from '../stores/session'
import { setAutoGenerate } from '../stores/tasks'

const emit = defineEmits<{ settings: []; changed: [] }>()
type Field = keyof CreateTask
const values = reactive<CreateTask>({ repository_url: '', baseline_commit: '', issue_url: '' })
const errors = reactive<Record<Field, string>>({ repository_url: '', baseline_commit: '', issue_url: '' })
const repositoryInput = ref<InputInst | null>(null)
const commitInput = ref<InputInst | null>(null)
const issueInput = ref<InputInst | null>(null)
const creating = ref(false)
const requestError = ref('')
const needsSettings = ref(false)
const uncertainResult = ref(false)
let controller: AbortController | null = null
let active = true

// Parse the original path rather than URL.pathname: browser URL parsing would
// silently resolve dot segments or backslashes that the backend rejects.
function trimSource(value: string): string {
  return value.replace(/^[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+|[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+$/g, '')
}
function githubPath(value: string): string {
  const text = trimSource(value)
  if (/[\s\u0000-\u0020\u007f\u0085]/.test(text)) throw new Error('GitHub 链接不能包含空白或控制字符')
  const match = /^https:\/\/([^/?#]*)([^?#]*)$/i.exec(text)
  const authority = match && /^github\.com(?::([0-9]+))?$/i.exec(match[1]!)
  if (!match || !authority || (authority[1] !== undefined && Number(authority[1]) !== 443)) {
    throw new Error('目前仅支持不含凭据、查询参数或片段的 GitHub HTTPS 链接')
  }
  return match[2]!
}
function repositoryParts(path: string): [string, string] {
  const parts = path.split('/')
  if (parts.length !== 3 || parts[0]) throw new Error('仓库链接格式应为 https://github.com/owner/repo')
  const owner = parts[1]!
  const repository = parts[2]!.endsWith('.git') ? parts[2]!.slice(0, -4) : parts[2]!
  if (!/^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$/.test(owner)
    || !/^[A-Za-z0-9_.-]{1,100}$/.test(repository) || repository === '.' || repository === '..') {
    throw new Error('请输入有效的 GitHub 仓库名称')
  }
  return [owner.toLowerCase(), repository.toLowerCase()]
}
function normalize(field: Field): string {
  if (field === 'baseline_commit') {
    const sha = trimSource(values[field])
    if (!/^[0-9a-fA-F]{40}$/.test(sha)) throw new Error('请输入完整的 40 位十六进制 commit SHA')
    return sha.toLowerCase()
  }
  const path = githubPath(values[field])
  if (field === 'repository_url') {
    const [owner, repository] = repositoryParts(path.endsWith('/') ? path.slice(0, -1) : path)
    return `https://github.com/${owner}/${repository}`
  }
  const parts = path.split('/')
  if (parts.length !== 5 || parts[3] !== 'issues' || !/^[0-9]+$/.test(parts[4]!)) {
    throw new Error('Issue 链接格式应为 https://github.com/owner/repo/issues/正整数')
  }
  if (parts[2]!.endsWith('.git')) throw new Error('Issue 链接不能包含仓库克隆地址的 .git 后缀')
  const [owner, repository] = repositoryParts(parts.slice(0, 3).join('/'))
  const number = parts[4]!.replace(/^0+/, '')
  if (!number) throw new Error('Issue 编号必须为正整数')
  if (parts[4]!.length > 4300) throw new Error('请输入有效的 Issue 编号')
  return `https://github.com/${owner}/${repository}/issues/${number}`
}
function validate(field: Field): string | null {
  try {
    const normalized = normalize(field)
    if (field === 'issue_url') {
      let repository: string | null = null
      try { repository = normalize('repository_url') } catch { /* Repository has its own field error. */ }
      if (repository && normalized.slice(0, normalized.lastIndexOf('/issues/')) !== repository) {
        throw new Error('Issue 必须属于所填写的 GitHub 仓库')
      }
    }
    errors[field] = ''
    return normalized
  } catch (error) {
    errors[field] = error instanceof Error ? error.message : '请输入有效的来源'
    return null
  }
}
function update(field: Field, value: string): void {
  values[field] = value
  errors[field] = ''
  if (field === 'repository_url') errors.issue_url = ''
}
function cancelRequest(): void {
  controller?.abort()
  controller = null
  creating.value = false
}
watch(() => sessionVersion(), () => {
  cancelRequest()
  requestError.value = ''
  needsSettings.value = false
  uncertainResult.value = false
}, { flush: 'sync' })
onBeforeUnmount(() => {
  active = false
  cancelRequest()
})
async function submit(): Promise<void> {
  if (creating.value) return
  const repository = validate('repository_url')
  const commit = validate('baseline_commit')
  const issue = validate('issue_url')
  if (!repository || !commit || !issue) {
    const input = !repository ? repositoryInput : !commit ? commitInput : issueInput
    input.value?.focus()
    return
  }
  const version = sessionVersion()
  const request = new AbortController()
  controller = request
  creating.value = true
  requestError.value = ''
  needsSettings.value = false
  uncertainResult.value = false
  const current = () => active && !request.signal.aborted && version === sessionVersion() && controller === request
  try {
    const task = await createTask({ repository_url: repository, baseline_commit: commit, issue_url: issue }, request.signal)
    if (!current()) return
    setAutoGenerate(task.id)
    navigate(`/app/tasks/${task.id}`)
    emit('changed')
  } catch (error) {
    if (!current()) return
    requestError.value = error instanceof ApiError ? error.message : '连接中断，无法确认对话是否已创建。请先查看最近对话，再决定是否重新提交。'
    needsSettings.value = error instanceof ApiError && error.status === 503
    uncertainResult.value = !(error instanceof ApiError) || error.status >= 500 && error.status !== 503
  } finally {
    if (current()) {
      creating.value = false
      controller = null
    }
  }
}
</script>

<template>
  <section class="new-conversation" aria-labelledby="new-conversation-title">
    <a class="back-link" href="/app" @click="followLink($event, '/app')"><ArrowLeft :size="16" aria-hidden="true" />返回工作台</a>
    <header class="page-header">
      <p class="eyebrow">从真实 Issue 开始</p>
      <h1 id="new-conversation-title">新建对话</h1>
      <p class="description">填写来源后生成目标，批准不会立即执行代码。</p>
    </header>
    <form class="source-form" novalidate :aria-busy="creating" @submit.prevent="submit">
      <div class="form-intro"><span class="source-icon"><GitBranch :size="20" aria-hidden="true" /></span><div><h2>对话来源</h2><p>目前仅支持 GitHub 仓库及同仓库的 Issue，不支持 Pull Request。</p></div></div>
      <div class="field">
        <label for="conversation-repository">仓库链接</label>
        <NInput ref="repositoryInput" :value="values.repository_url" :disabled="creating" :status="errors.repository_url ? 'error' : undefined" placeholder="https://github.com/owner/repo" :input-props="{ id: 'conversation-repository', autocomplete: 'off', spellcheck: false, 'aria-invalid': !!errors.repository_url, 'aria-describedby': 'repository-help repository-error' }" @update:value="update('repository_url', $event)" @blur="validate('repository_url')" />
        <p id="repository-help" class="field-help">填写 HTTPS 链接；私有仓库需要已配置的 GitHub token 具备读取权限。</p>
        <p v-if="errors.repository_url" id="repository-error" class="field-error" role="alert">{{ errors.repository_url }}</p>
      </div>
      <div class="field">
        <label for="conversation-commit">完整 commit SHA</label>
        <NInput ref="commitInput" :value="values.baseline_commit" :disabled="creating" :status="errors.baseline_commit ? 'error' : undefined" placeholder="粘贴完整的 40 位 commit SHA" :input-props="{ id: 'conversation-commit', autocomplete: 'off', spellcheck: false, 'aria-invalid': !!errors.baseline_commit, 'aria-describedby': 'commit-help commit-error' }" @update:value="update('baseline_commit', $event)" @blur="validate('baseline_commit')" />
        <p id="commit-help" class="field-help">使用该仓库的完整 40 位十六进制 SHA，不支持分支名或短 SHA。</p>
        <p v-if="errors.baseline_commit" id="commit-error" class="field-error" role="alert">{{ errors.baseline_commit }}</p>
      </div>
      <div class="field">
        <label for="conversation-issue">Issue 链接</label>
        <NInput ref="issueInput" :value="values.issue_url" :disabled="creating" :status="errors.issue_url ? 'error' : undefined" placeholder="https://github.com/owner/repo/issues/1" :input-props="{ id: 'conversation-issue', autocomplete: 'off', spellcheck: false, 'aria-invalid': !!errors.issue_url, 'aria-describedby': 'issue-help issue-error' }" @update:value="update('issue_url', $event)" @blur="validate('issue_url')" />
        <p id="issue-help" class="field-help">Issue 必须属于上方仓库。创建后保存来源快照，更换来源需新建对话。</p>
        <p v-if="errors.issue_url" id="issue-error" class="field-error" role="alert">{{ errors.issue_url }}</p>
      </div>
      <NAlert v-if="requestError" type="error" :show-icon="false" role="alert" class="request-error">
        {{ requestError }}
        <p v-if="uncertainResult" class="error-guidance">不会自动重新提交。可返回工作台刷新最近对话，避免重复创建。</p>
        <NButton v-if="needsSettings" attr-type="button" class="settings-button" aria-haspopup="dialog" @click="emit('settings')"><template #icon><Settings :size="16" aria-hidden="true" /></template>打开设置</NButton>
        <a v-if="uncertainResult" class="back-link" href="/app" @click="followLink($event, '/app')">查看最近对话</a>
      </NAlert>
      <footer class="form-footer">
        <p>目标基于 Issue 快照生成，不代表已检索代码或验证计划。</p>
        <NButton type="primary" attr-type="submit" class="submit-button" :loading="creating" :disabled="creating"><template #icon><Plus v-if="!creating" :size="18" aria-hidden="true" /></template>{{ creating ? '正在校验来源' : '创建并生成目标' }}</NButton>
      </footer>
      <p class="submission-status" role="status" aria-live="polite">{{ creating ? '正在校验仓库、commit 与 Issue，请稍候。' : '' }}</p>
    </form>
  </section>
</template>

<style scoped>
.new-conversation { width: 100%; max-width: 720px; margin: 0 auto; padding: 32px 0 48px; min-width: 0; overflow-wrap: anywhere; }
.back-link { display: inline-flex; align-items: center; gap: 8px; color: var(--text-secondary); text-decoration: none; min-height: 40px; }
.back-link:hover { color: var(--accent); }
.back-link:focus-visible { outline: 2px solid var(--accent); outline-offset: 4px; border-radius: 4px; }
.page-header { margin: 24px 0; }
.eyebrow { margin: 0 0 8px; color: var(--accent); font-size: 13px; font-weight: 600; }
h1 { margin: 0 0 12px; font-size: clamp(26px, 3vw, 32px); font-weight: 650; line-height: 1.3; letter-spacing: -0.025em; }
.description { margin: 0; color: var(--text-secondary); font-size: 15px; }
.source-form { padding: 28px; border: 1px solid var(--border); border-radius: 12px; background: var(--surface); }
.form-intro { display: flex; align-items: flex-start; gap: 12px; margin-bottom: 28px; }
.source-icon { display: flex; align-items: center; justify-content: center; flex: 0 0 40px; height: 40px; color: var(--accent); background: var(--accent-soft); border-radius: 10px; }
h2 { margin: 0 0 4px; font-size: 16px; font-weight: 600; }
.form-intro p { margin: 0; font-size: 13px; color: var(--text-secondary); }
.field { margin-bottom: 24px; min-width: 0; }
.field label { display: block; margin-bottom: 8px; font-weight: 600; }
.field :deep(.n-input) { min-height: 42px; }
.field-help, .field-error { margin: 8px 0 0; font-size: 12px; line-height: 1.6; }
.field-help { color: var(--text-muted); }
.field-error { color: var(--danger-text); }
.request-error { margin-bottom: 24px; }
.settings-button { margin-top: 12px; min-height: 40px; }
.error-guidance { margin: 8px 0 0; }
.form-footer { display: flex; flex-direction: column; gap: 16px; padding-top: 20px; border-top: 1px solid var(--border-soft); }
.form-footer p { margin: 0; color: var(--text-secondary); font-size: 13px; }
.submit-button { min-height: 44px; width: 100%; }
.submission-status { margin: 12px 0 0; color: var(--text-secondary); font-size: 13px; }
.submission-status:empty { display: none; }
@media (max-width: 768px) { .new-conversation { padding: 16px 0 32px; } .page-header { margin-top: 16px; } .source-form { padding: 20px 16px; } }
</style>
