<script setup lang="ts">
import { onBeforeUnmount, reactive, ref, watch } from 'vue'
import { NAlert, NButton, NInput, type InputInst } from 'naive-ui'
import { ArrowLeft, ArrowRight, CircleAlert, GitBranch, GitCommitHorizontal, Link, Settings, ShieldCheck } from '@lucide/vue'
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
    requestError.value = error instanceof ApiError ? error.message : '连接中断，无法确认任务是否已创建。请先查看最近任务，再决定是否重新提交。'
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
      <p class="eyebrow">从 Issue 开始</p>
      <h1 id="new-conversation-title">新建任务</h1>
      <p class="description">固定代码版本和 Issue，生成方案并在审阅后执行。</p>
    </header>
    <ol class="creation-steps" aria-label="任务流程">
      <li aria-current="step"><span class="step-number">1</span><span>确定来源</span></li>
      <li><span class="step-number">2</span><span>审阅并批准方案</span></li>
      <li><span class="step-number">3</span><span>执行并交付</span></li>
    </ol>
    <form class="source-form" novalidate :aria-busy="creating" @submit.prevent="submit">
      <div class="form-intro"><div><h2>任务来源</h2><p>选择仓库、代码版本与 Issue，作为方案和执行的依据。</p></div><span class="source-badge"><GitBranch :size="14" aria-hidden="true" />GitHub</span></div>
      <section v-if="requestError && needsSettings" class="configuration-notice" role="alert" aria-labelledby="configuration-title">
        <CircleAlert :size="20" class="notice-icon" aria-hidden="true" />
        <div class="notice-content"><h3 id="configuration-title">需要配置 GitHub 访问权限</h3><p>{{ requestError }}</p><p class="notice-hint">已保留当前填写内容。保存设置后可直接重试。</p></div>
        <NButton attr-type="button" class="settings-button" aria-haspopup="dialog" @click="emit('settings')"><template #icon><Settings :size="16" aria-hidden="true" /></template>打开设置</NButton>
      </section>
      <div class="field">
        <div class="field-heading"><label for="conversation-repository"><GitBranch :size="16" aria-hidden="true" />仓库链接</label><span class="field-tag">HTTPS</span></div>
        <NInput ref="repositoryInput" :value="values.repository_url" :disabled="creating" :status="errors.repository_url ? 'error' : undefined" placeholder="https://github.com/owner/repo" :input-props="{ id: 'conversation-repository', autocomplete: 'off', spellcheck: false, 'aria-invalid': !!errors.repository_url, 'aria-describedby': 'repository-help repository-error' }" @update:value="update('repository_url', $event)" @blur="validate('repository_url')" />
        <p id="repository-help" class="field-help">GitHub 仓库主页地址。私有仓库需要相应的 GitHub token 权限。</p>
        <p v-if="errors.repository_url" id="repository-error" class="field-error" role="alert">{{ errors.repository_url }}</p>
      </div>
      <div class="field">
        <div class="field-heading"><label for="conversation-commit"><GitCommitHorizontal :size="16" aria-hidden="true" />代码版本</label><span class="field-tag">完整 commit SHA</span></div>
        <NInput ref="commitInput" class="commit-input" :value="values.baseline_commit" :disabled="creating" :status="errors.baseline_commit ? 'error' : undefined" placeholder="粘贴 40 位 commit SHA" :input-props="{ id: 'conversation-commit', autocomplete: 'off', spellcheck: false, 'aria-invalid': !!errors.baseline_commit, 'aria-describedby': 'commit-help commit-error' }" @update:value="update('baseline_commit', $event)" @blur="validate('baseline_commit')" />
        <p id="commit-help" class="field-help">填写完整 commit SHA。任务每次执行都从此版本重新开始。</p>
        <p v-if="errors.baseline_commit" id="commit-error" class="field-error" role="alert">{{ errors.baseline_commit }}</p>
      </div>
      <div class="field">
        <div class="field-heading"><label for="conversation-issue"><Link :size="16" aria-hidden="true" />Issue 链接</label><span class="field-tag">同一仓库</span></div>
        <NInput ref="issueInput" :value="values.issue_url" :disabled="creating" :status="errors.issue_url ? 'error' : undefined" placeholder="https://github.com/owner/repo/issues/1" :input-props="{ id: 'conversation-issue', autocomplete: 'off', spellcheck: false, 'aria-invalid': !!errors.issue_url, 'aria-describedby': 'issue-help issue-error' }" @update:value="update('issue_url', $event)" @blur="validate('issue_url')" />
        <p id="issue-help" class="field-help">Issue 必须属于上述仓库，暂不支持 Pull Request。更换来源请新建任务。</p>
        <p v-if="errors.issue_url" id="issue-error" class="field-error" role="alert">{{ errors.issue_url }}</p>
      </div>
      <NAlert v-if="requestError && !needsSettings" type="error" :show-icon="true" role="alert" class="request-error" title="暂时无法创建任务">
        {{ requestError }}
        <p v-if="uncertainResult" class="error-guidance">系统不会自动重试。请先返回工作台查看最近任务，避免重复创建。</p>
        <a v-if="uncertainResult" class="back-link" href="/app" @click="followLink($event, '/app')">查看最近任务</a>
      </NAlert>
      <footer class="form-footer">
        <div class="creation-note"><ShieldCheck :size="18" aria-hidden="true" /><p>创建任务时会读取 Issue 和必要的源码。<br><span>此步骤仅生成方案，不会修改代码。</span></p></div>
        <NButton type="primary" attr-type="submit" class="submit-button" :loading="creating" :disabled="creating"><template #icon><ArrowRight v-if="!creating" :size="17" aria-hidden="true" /></template>{{ creating ? '正在检查任务来源' : needsSettings ? '重新检查并生成方案' : '创建任务并生成方案' }}</NButton>
      </footer>
      <p class="submission-status" role="status" aria-live="polite">{{ creating ? '正在检查仓库、commit 与 Issue…' : '' }}</p>
    </form>
  </section>
</template>

<style scoped>
.new-conversation { width: 100%; max-width: 800px; margin: 0 auto; padding: 0 0 32px; min-width: 0; overflow-wrap: anywhere; }
.back-link { display: inline-flex; align-items: center; gap: 6px; color: var(--text-secondary); text-decoration: none; min-height: 36px; font-size: 13px; }
.back-link:hover { color: var(--accent); }
.back-link:focus-visible { outline: 2px solid var(--accent); outline-offset: 4px; border-radius: 4px; }
.page-header { margin: 18px 0 22px; }
.eyebrow { margin: 0 0 8px; color: var(--accent); font-size: 12px; font-weight: 600; }
h1 { margin: 0 0 10px; font-size: clamp(26px, 3vw, 32px); font-weight: 650; line-height: 1.3; letter-spacing: -0.025em; }
.description { margin: 0; color: var(--text-secondary); font-size: 14px; line-height: 1.7; }
.creation-steps { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; padding: 0; margin: 0 0 24px; list-style: none; }
.creation-steps li { display: flex; align-items: center; gap: 8px; min-width: 0; color: var(--text-muted); font-size: 12px; }
.creation-steps li[aria-current] { color: var(--accent); font-weight: 600; }
.step-number { display: inline-flex; flex: 0 0 24px; width: 24px; height: 24px; align-items: center; justify-content: center; border: 1px solid var(--border); border-radius: 50%; font-size: 11px; }
[aria-current] .step-number { color: var(--surface); background: var(--accent); border-color: var(--accent); }
.source-form { padding: 24px 28px; border: 1px solid var(--border); border-radius: 14px; background: var(--surface); }
.form-intro { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; padding-bottom: 20px; margin-bottom: 22px; border-bottom: 1px solid var(--border-soft); }
h2 { margin: 0 0 6px; font-size: 16px; font-weight: 600; }
.form-intro p { margin: 0; font-size: 12px; line-height: 1.7; color: var(--text-secondary); }
.source-badge { display: inline-flex; align-items: center; gap: 6px; flex-shrink: 0; padding: 4px 9px; border: 1px solid var(--border-soft); border-radius: 6px; color: var(--text-secondary); font-size: 11px; }
.field { margin-bottom: 22px; min-width: 0; }
.field-heading { display: flex; align-items: center; justify-content: space-between; gap: 10px; margin-bottom: 8px; }
.field label { display: flex; align-items: center; gap: 7px; font-size: 13px; font-weight: 600; }
.field label svg { color: var(--text-muted); }
.field-tag { color: var(--text-muted); font-size: 11px; text-align: right; }
.field :deep(.n-input) { min-height: 42px; }
.commit-input :deep(input) { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 12px; }
.field-help, .field-error { margin: 7px 0 0; font-size: 12px; line-height: 1.6; }
.field-help { color: var(--text-muted); }
.field-error { color: var(--danger-text); }
.configuration-notice { display: flex; align-items: flex-start; gap: 12px; padding: 16px; margin-bottom: 22px; background: var(--warning-bg); border-radius: 8px; color: var(--warning-text); }
.notice-icon { flex-shrink: 0; margin-top: 1px; }
.notice-content { min-width: 0; flex: 1; }
.notice-content h3 { margin: 0 0 5px; font-size: 13px; font-weight: 600; }
.notice-content p { margin: 0; font-size: 12px; line-height: 1.7; }
.notice-content .notice-hint { margin-top: 4px; }
.settings-button { flex-shrink: 0; min-height: 36px; }
.request-error { margin-bottom: 22px; }
.error-guidance { margin: 8px 0 0; }
.form-footer { display: flex; align-items: center; justify-content: space-between; gap: 20px; padding-top: 20px; border-top: 1px solid var(--border-soft); }
.creation-note { display: flex; gap: 8px; align-items: flex-start; color: var(--text-muted); }
.creation-note svg { flex-shrink: 0; margin-top: 2px; }
.creation-note p { margin: 0; font-size: 12px; line-height: 1.7; }
.creation-note span { color: var(--text-secondary); }
.submit-button { min-height: 42px; flex-shrink: 0; }
.submission-status { margin: 12px 0 0; color: var(--text-secondary); font-size: 13px; }
.submission-status:empty { display: none; }
@media (max-width: 768px) { .new-conversation { padding: 0 0 24px; } .page-header { margin-top: 14px; } .source-form { padding: 20px; } .creation-steps { gap: 8px; } }
@media (max-width: 560px) { .source-form { padding: 20px 16px; } .form-intro { gap: 10px; } .creation-steps li { flex-direction: column; align-items: flex-start; gap: 6px; font-size: 11px; } .form-footer { flex-direction: column; align-items: stretch; gap: 16px; } .submit-button { width: 100%; min-height: 44px; } .configuration-notice { flex-wrap: wrap; } .notice-content { flex-basis: calc(100% - 32px); } .settings-button { margin-left: 32px; } }
</style>
