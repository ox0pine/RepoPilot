<script setup lang="ts">
import { nextTick, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { NAlert, NButton, NInput, type InputInst } from 'naive-ui'
import { ArrowLeft, ArrowRight, CircleAlert, GitBranch, GitCommitHorizontal, Link, Settings, ShieldCheck } from '@lucide/vue'
import { ApiError } from '../api/client'
import { isUnknownOutcome, messageOf, needsConfiguration } from '../lib/errors'
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

// The backend owns the source rules; this form only refuses to send blanks and
// fills per-field messages back from a 422 body.
const REQUIRED: Record<Field, string> = {
  repository_url: '请填写 GitHub 仓库链接',
  baseline_commit: '请填写 40 位十六进制 commit SHA',
  issue_url: '请填写 Issue 链接',
}
const FIELD_ORDER: Field[] = ['repository_url', 'baseline_commit', 'issue_url']

function inputFor(field: Field): InputInst | null {
  return field === 'repository_url' ? repositoryInput.value : field === 'baseline_commit' ? commitInput.value : issueInput.value
}
function validate(field: Field): string | null {
  const value = values[field].trim()
  if (!value) {
    errors[field] = REQUIRED[field]
    return null
  }
  errors[field] = ''
  return value
}
function update(field: Field, value: string): void {
  values[field] = value
  errors[field] = ''
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
    inputFor(!repository ? 'repository_url' : !commit ? 'baseline_commit' : 'issue_url')?.focus()
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
  let invalidField: Field | null = null
  try {
    const task = await createTask({ repository_url: repository, baseline_commit: commit, issue_url: issue }, request.signal)
    if (!current()) return
    setAutoGenerate(task.id)
    navigate(`/app/tasks/${task.id}`)
    emit('changed')
  } catch (error) {
    if (!current()) return
    requestError.value = messageOf(error, '连接中断，无法确认任务是否已创建。请先查看最近任务，再决定是否重新提交。')
    needsSettings.value = needsConfiguration(error)
    uncertainResult.value = isUnknownOutcome(error)
    if (error instanceof ApiError && Object.keys(error.fields).length) {
      Object.assign(errors, error.fields)
      invalidField = FIELD_ORDER.find(field => errors[field]) ?? null
    }
  } finally {
    if (current()) {
      creating.value = false
      controller = null
      if (invalidField) {
        // The field was disabled while the request ran; focus after re-enabling.
        await nextTick()
        inputFor(invalidField)?.focus()
      }
    }
  }
}
</script>

<template>
  <section class="new-task" aria-labelledby="new-task-title">
    <a class="back-link" href="/app" @click="followLink($event, '/app')"><ArrowLeft :size="16" :stroke-width="1.5" aria-hidden="true" />任务列表</a>
    <header class="page-header">
      <h1 id="new-task-title">新建任务</h1>
      <p class="description">固定代码版本和 Issue，生成方案并在审阅后执行。</p>
    </header>
    <form class="source-form" novalidate :aria-busy="creating" @submit.prevent="submit">
      <div class="form-intro"><div><h2>任务来源</h2><p>仓库、代码版本与 Issue 共同确定这次执行的起点。</p></div></div>
      <section v-if="requestError && needsSettings" class="configuration-notice" role="alert" aria-labelledby="configuration-title">
        <CircleAlert :size="16" :stroke-width="1.5" class="notice-icon" aria-hidden="true" />
        <div class="notice-content"><h3 id="configuration-title">需要配置 GitHub 访问权限</h3><p>{{ requestError }}</p><p class="notice-hint">已保留当前填写内容。保存设置后可直接重试。</p></div>
        <NButton size="small" attr-type="button" class="settings-button" aria-haspopup="dialog" @click="emit('settings')"><template #icon><Settings :size="16" :stroke-width="1.5" aria-hidden="true" /></template>打开设置</NButton>
      </section>
      <div class="field">
        <div class="field-heading"><label for="task-repository"><GitBranch :size="16" :stroke-width="1.5" aria-hidden="true" />仓库链接</label></div>
        <NInput ref="repositoryInput" :value="values.repository_url" :disabled="creating" :status="errors.repository_url ? 'error' : undefined" placeholder="https://github.com/owner/repo" :input-props="{ id: 'task-repository', autocomplete: 'off', spellcheck: false, 'aria-invalid': !!errors.repository_url, 'aria-describedby': 'repository-help repository-error' }" @update:value="update('repository_url', $event)" />
        <p id="repository-help" class="field-help">GitHub 仓库主页地址。私有仓库需要相应的 GitHub token 权限。</p>
        <p v-if="errors.repository_url" id="repository-error" class="field-error" role="alert">{{ errors.repository_url }}</p>
      </div>
      <div class="field">
        <div class="field-heading"><label for="task-commit"><GitCommitHorizontal :size="16" :stroke-width="1.5" aria-hidden="true" />代码版本</label></div>
        <NInput ref="commitInput" class="commit-input" :value="values.baseline_commit" :disabled="creating" :status="errors.baseline_commit ? 'error' : undefined" placeholder="40 位十六进制 SHA" :input-props="{ id: 'task-commit', autocomplete: 'off', spellcheck: false, 'aria-invalid': !!errors.baseline_commit, 'aria-describedby': 'commit-help commit-error' }" @update:value="update('baseline_commit', $event)" />
        <p id="commit-help" class="field-help">任务每次执行都从此版本重新开始，不接受分支名或短 SHA。</p>
        <p v-if="errors.baseline_commit" id="commit-error" class="field-error" role="alert">{{ errors.baseline_commit }}</p>
      </div>
      <div class="field">
        <div class="field-heading"><label for="task-issue"><Link :size="16" :stroke-width="1.5" aria-hidden="true" />Issue 链接</label></div>
        <NInput ref="issueInput" :value="values.issue_url" :disabled="creating" :status="errors.issue_url ? 'error' : undefined" placeholder="https://github.com/owner/repo/issues/1" :input-props="{ id: 'task-issue', autocomplete: 'off', spellcheck: false, 'aria-invalid': !!errors.issue_url, 'aria-describedby': 'issue-help issue-error' }" @update:value="update('issue_url', $event)" />
        <p id="issue-help" class="field-help">Issue 必须属于上述仓库，暂不支持 Pull Request。更换来源请新建任务。</p>
        <p v-if="errors.issue_url" id="issue-error" class="field-error" role="alert">{{ errors.issue_url }}</p>
      </div>
      <NAlert v-if="requestError && !needsSettings" type="error" :show-icon="true" role="alert" class="request-error" title="暂时无法创建任务">
        {{ requestError }}
        <p v-if="uncertainResult" class="error-guidance">系统不会自动重试。请先返回工作台查看最近任务，避免重复创建。</p>
        <a v-if="uncertainResult" class="back-link" href="/app" @click="followLink($event, '/app')">查看最近任务</a>
      </NAlert>
      <footer class="form-footer">
        <div class="creation-note"><ShieldCheck :size="16" :stroke-width="1.5" aria-hidden="true" /><p>创建任务时会读取 Issue 和必要的源码。<span>此步骤仅生成方案，不会修改代码。</span></p></div>
        <NButton type="primary" attr-type="submit" class="submit-button" :loading="creating" :disabled="creating"><template #icon><ArrowRight v-if="!creating" :size="16" :stroke-width="1.5" aria-hidden="true" /></template>{{ creating ? '正在检查任务来源' : needsSettings ? '重新检查并生成方案' : '创建任务并生成方案' }}</NButton>
      </footer>
      <p class="submission-status" role="status" aria-live="polite">{{ creating ? '正在检查仓库、commit 与 Issue…' : '' }}</p>
    </form>
  </section>
</template>

<style scoped>
.new-task { width: 100%; max-width: 720px; padding: 0 0 32px; min-width: 0; overflow-wrap: anywhere; }
.page-header { margin: 8px 0 24px; }
h1 { margin: 0 0 4px; font-size: 26px; font-weight: 500; line-height: 1.3; }
.description { margin: 0; color: var(--text-secondary); font-size: 13px; line-height: 1.7; }
.source-form { padding: 24px 28px; border: 1px solid var(--border); border-radius: 12px; background: var(--surface); }
.form-intro { padding-bottom: 20px; margin-bottom: 24px; border-bottom: 1px solid var(--border-soft); }
h2 { margin: 0 0 4px; font-size: 15px; font-weight: 500; }
.form-intro p { margin: 0; font-size: 12px; line-height: 1.7; color: var(--text-secondary); }
.field { margin-bottom: 24px; min-width: 0; }
.field-heading { margin-bottom: 8px; }
.field label { display: flex; align-items: center; gap: 8px; font-size: 13px; font-weight: 500; }
.field label svg { color: var(--text-muted); }
.field :deep(.n-input) { min-height: 40px; }
.commit-input :deep(input) { font-family: var(--font-mono); font-size: 12px; }
.field-help, .field-error { margin: 8px 0 0; font-size: 12px; line-height: 1.6; }
.field-help { color: var(--text-muted); }
.field-error { color: var(--danger-text); }
.configuration-notice { display: flex; align-items: flex-start; gap: 12px; padding: 16px; margin-bottom: 24px; background: var(--warning-bg); border-radius: 8px; color: var(--warning-text); }
.notice-icon { flex-shrink: 0; margin-top: 2px; }
.notice-content { min-width: 0; flex: 1; }
.notice-content h3 { margin: 0 0 4px; font-size: 13px; font-weight: 500; }
.notice-content p { margin: 0; font-size: 12px; line-height: 1.7; }
.notice-content .notice-hint { margin-top: 4px; }
.settings-button { flex-shrink: 0; }
.request-error { margin-bottom: 24px; }
.error-guidance { margin: 8px 0 0; }
.form-footer { display: flex; align-items: center; justify-content: space-between; gap: 24px; padding-top: 24px; border-top: 1px solid var(--border-soft); }
.creation-note { display: flex; gap: 8px; align-items: flex-start; color: var(--text-muted); }
.creation-note svg { flex-shrink: 0; margin-top: 3px; }
.creation-note p { margin: 0; font-size: 12px; line-height: 1.7; }
.creation-note span { display: block; color: var(--text-secondary); }
.submit-button { flex-shrink: 0; }
.submission-status { margin: 12px 0 0; color: var(--text-secondary); font-size: 13px; }
.submission-status:empty { display: none; }
@media (max-width: 768px) { .new-task { padding: 0 0 24px; } .source-form { padding: 20px; } }
@media (max-width: 560px) {
  .source-form { padding: 20px 16px; }
  .form-footer { flex-direction: column; align-items: stretch; gap: 16px; }
  .submit-button { width: 100%; min-height: 44px; }
  .configuration-notice { flex-wrap: wrap; }
  .notice-content { flex-basis: calc(100% - 32px); }
  .settings-button { margin-left: 32px; }
}
</style>
