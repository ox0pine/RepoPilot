<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { NButton, NInput, NSkeleton } from 'naive-ui'
import { CheckCircle2, Copy, ExternalLink, LoaderCircle, MessageSquare } from '@lucide/vue'
import GoalCard from '../components/GoalCard.vue'
import { ApiError } from '../api/client'
import { approveGoal, generateGoal, getTask, type GenerationAction, type TaskDetail, type TaskGoal, type TaskStatus } from '../api/tasks'
import { sessionToken, sessionVersion } from '../stores/session'
import { consumeAutoGenerate } from '../stores/tasks'

const props = defineProps<{ taskId: string }>()
const emit = defineEmits<{ settings: []; changed: [] }>()
const task = ref<TaskDetail | null>(null)
const loading = ref(false)
const readError = ref('')
const actionError = ref('')
const needsSettings = ref(false)
const busy = ref<'generate' | 'revise' | 'retry' | 'approve' | null>(null)
const feedback = ref('')
const feedbackOpen = ref(false)
const feedbackInput = ref<InstanceType<typeof NInput> | null>(null)
const timelineEnd = ref<HTMLElement | null>(null)
const announcement = ref('')
let epoch = 0
let timer: ReturnType<typeof setTimeout> | undefined
let generationProbe: ReturnType<typeof setTimeout> | undefined
let reading = false
let readSequence = 0
const controllers = new Set<AbortController>()
const statuses: Record<TaskStatus, string> = {
  draft: '待生成', generating: '生成中', awaiting_approval: '待批准', approved: '已批准', generation_failed: '生成失败',
}
const generating = computed(() => task.value?.status === 'generating' || (busy.value !== null && busy.value !== 'approve'))
const canRevise = computed(() => !!task.value?.current_goal_version && ['awaiting_approval', 'approved', 'generation_failed'].includes(task.value.status))
const canSubmitFeedback = computed(() => canRevise.value && !busy.value && !generating.value && !!feedback.value.trim() && feedback.value.trim().length <= 8000)
const messages = computed(() => [...(task.value?.messages ?? [])].sort((a, b) => a.id - b.id))
const goals = computed(() => new Map(task.value?.goals.map(goal => [goal.version, goal]) ?? []))
function goalFor(version: number | null): TaskGoal | undefined { return version === null ? undefined : goals.value.get(version) }
function safeLink(value: string): string | undefined {
  return /^https:\/\/github\.com\/[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+(?:\/issues\/[1-9][0-9]*)?$/.test(value) ? value : undefined
}
function formatDate(value: string): string { return new Date(value).toLocaleString('zh-CN') }
function errorText(error: unknown): string { return error instanceof Error ? error.message : '请求失败，请重试' }
function stopPolling(): void { if (timer !== undefined) clearTimeout(timer); timer = undefined }
function resetRequests(): void {
  epoch++
  readSequence++
  stopPolling()
  if (generationProbe !== undefined) clearTimeout(generationProbe)
  generationProbe = undefined
  for (const controller of controllers) controller.abort()
  controllers.clear()
  reading = false
}
function requestContext() {
  const controller = new AbortController()
  controllers.add(controller)
  const identity = epoch
  const version = sessionVersion()
  const id = props.taskId
  return { controller, id, current: () => identity === epoch && version === sessionVersion() && id === props.taskId && !controller.signal.aborted }
}
function schedulePolling(): void {
  stopPolling()
  if (task.value?.status === 'generating' && !readError.value) timer = setTimeout(() => { void loadDetail() }, 2000)
}
async function mergeDetail(detail: TaskDetail): Promise<void> {
  if (detail.id !== props.taskId || (task.value && detail.revision < task.value.revision)) return
  const previous = task.value
  const addedMessages = !!previous && detail.messages.some(message => !previous.messages.some(old => old.id === message.id))
  const end = timelineEnd.value
  const nearBottom = !!end && end.getBoundingClientRect().bottom <= window.innerHeight + 120
  task.value = detail
  if (previous && detail.revision > previous.revision) {
    emit('changed')
    if (previous.status === 'generating' && detail.status !== 'generating') announcement.value = `目标状态：${statuses[detail.status]}`
  }
  schedulePolling()
  if (addedMessages && nearBottom) {
    const identity = epoch
    await nextTick()
    if (identity === epoch) timelineEnd.value?.scrollIntoView({ block: 'end', behavior: 'auto' })
  }
}
async function loadDetail(force = false): Promise<boolean> {
  if ((reading && !force) || !sessionToken.value) return false
  const sequence = ++readSequence
  reading = true
  loading.value = !task.value
  const context = requestContext()
  const current = () => context.current() && sequence === readSequence
  try {
    const detail = await getTask(context.id, context.controller.signal)
    if (!current()) return false
    readError.value = ''
    await mergeDetail(detail)
    return true
  } catch (error) {
    if (current()) {
      readError.value = errorText(error)
      stopPolling()
    }
    return false
  } finally {
    controllers.delete(context.controller)
    if (current()) { reading = false; loading.value = false; schedulePolling() }
  }
}
async function perform(action: GenerationAction | 'approve'): Promise<void> {
  if (!task.value || busy.value || task.value.status === 'generating') return
  if (action === 'revise' && !canSubmitFeedback.value) return
  const current = task.value
  const sentFeedback = feedback.value.trim()
  const context = requestContext()
  busy.value = action
  actionError.value = ''
  needsSettings.value = false
  announcement.value = action === 'approve' ? '正在批准目标' : '正在根据 Issue 生成目标'
  try {
    const result = action === 'approve'
      ? approveGoal(context.id, { expected_revision: current.revision, goal_version: current.current_goal_version }, context.controller.signal)
      : generateGoal(context.id, { expected_revision: current.revision, action, ...(action === 'revise' ? { feedback: sentFeedback } : {}) }, context.controller.signal)
    // The POST is non-streaming. Read the persisted lease once after it starts;
    // subsequent polling is enabled only by an actual generating response.
    if (action !== 'approve') generationProbe = setTimeout(() => {
      generationProbe = undefined
      if (context.current()) void loadDetail()
    }, 250)
    const detail = await result
    if (!context.current()) return
    await mergeDetail(detail)
    if (!context.current()) return
    if (action === 'revise') feedback.value = ''
    emit('changed')
    announcement.value = action === 'approve' ? '目标已批准，代码执行尚未接入' : '新目标已生成，请审阅并批准'
  } catch (error) {
    if (!context.current()) return
    actionError.value = error instanceof ApiError && error.status === 409 ? '目标已更新，请重新确认' : errorText(error)
    needsSettings.value = error instanceof ApiError && error.status === 503
    announcement.value = actionError.value
    await loadDetail(true)
    if (context.current()) emit('changed')
  } finally {
    controllers.delete(context.controller)
    if (context.current()) {
      busy.value = null
      if (generationProbe !== undefined) clearTimeout(generationProbe)
      generationProbe = undefined
    }
  }
}
async function toggleFeedback(): Promise<void> {
  feedbackOpen.value = !feedbackOpen.value
  if (feedbackOpen.value) {
    await nextTick()
    feedbackInput.value?.focus()
  }
}
function feedbackKeydown(event: KeyboardEvent): void {
  if (event.key === 'Enter' && (event.ctrlKey || event.metaKey) && !event.isComposing) {
    event.preventDefault()
    if (canSubmitFeedback.value) void perform('revise')
  }
}
async function copyCommit(): Promise<void> {
  if (!task.value) return
  const identity = epoch
  try {
    await navigator.clipboard.writeText(task.value.baseline_commit)
    if (identity === epoch) announcement.value = '完整 commit SHA 已复制'
  } catch {
    if (identity === epoch) announcement.value = '复制失败，请选择并复制完整 SHA'
  }
}
watch(() => [props.taskId, sessionVersion()] as const, async () => {
  resetRequests()
  const identity = epoch
  task.value = null
  feedback.value = ''
  feedbackOpen.value = false
  readError.value = ''
  actionError.value = ''
  needsSettings.value = false
  busy.value = null
  announcement.value = ''
  loading.value = false
  const autoGenerate = consumeAutoGenerate(props.taskId)
  if (await loadDetail() && identity === epoch && autoGenerate && (task.value as TaskDetail | null)?.status === 'draft') void perform('generate')
}, { immediate: true, flush: 'sync' })
onBeforeUnmount(resetRequests)
</script>

<template>
  <div class="conversation">
    <p class="sr-only" role="status" aria-live="polite" aria-atomic="true">{{ announcement }}</p>
    <div v-if="loading && !task" class="loading-state" role="status" aria-label="正在读取对话"><NSkeleton height="36px" width="70%" /><NSkeleton height="140px" :sharp="false" /><NSkeleton height="260px" :sharp="false" /></div>
    <div v-if="readError" class="error-notice" role="alert"><p>对话读取失败：{{ readError }}。{{ task ? '已显示内容会保留，状态以重新读取为准。' : '' }}</p><NButton :loading="loading" @click="loadDetail(true)">重新读取</NButton></div>
    <template v-if="task">
      <header class="conversation-heading"><div><p class="eyebrow">Issue 驱动的目标审阅</p><h1>{{ task.title }}</h1></div><span class="status-badge" :class="task.status">{{ statuses[task.status] }}</span></header>
      <section class="source-card" aria-label="固定对话来源">
        <div class="source-row"><span class="source-label">仓库</span><a :href="safeLink(task.repository_url)" target="_blank" rel="noopener noreferrer">{{ task.repository_url }}<ExternalLink :size="14" aria-hidden="true" /></a></div>
        <div class="source-row"><span class="source-label">Issue</span><a :href="safeLink(task.issue_url)" target="_blank" rel="noopener noreferrer">{{ task.issue_url }}<ExternalLink :size="14" aria-hidden="true" /></a></div>
        <div class="source-row"><span class="source-label">Commit</span><code>{{ task.baseline_commit }}</code><NButton size="small" aria-label="复制完整 commit SHA" @click="copyCommit"><Copy :size="14" aria-hidden="true" />复制</NButton></div>
        <p class="source-note">来源快照固定于 {{ formatDate(task.source_snapshot.fetched_at) }}。更换仓库、commit 或 Issue 请新建对话。</p>
      </section>
      <ol class="timeline" aria-label="对话历史">
        <li v-for="message in messages" :key="message.id" class="timeline-item">
          <div class="message-meta"><span>{{ message.kind === 'source' ? '来源摘要' : message.kind === 'feedback' ? '你的修改意见' : message.kind === 'approval' ? `批准记录 · v${message.goal_version}` : `目标草案 · v${message.goal_version}` }}</span><time :datetime="message.created_at">{{ formatDate(message.created_at) }}</time></div>
          <template v-if="message.kind === 'goal'">
            <template v-if="goalFor(message.goal_version)">
              <GoalCard v-if="message.goal_version === task.current_goal_version" :goal="goalFor(message.goal_version)!" current :can-approve="task.status === 'awaiting_approval'" :can-revise="canRevise" :feedback-open="feedbackOpen" :busy="!!busy || generating" :approved="task.status === 'approved' && task.approved_goal_version === message.goal_version" @approve="perform('approve')" @revise="toggleFeedback" />
              <details v-else class="history-goal"><summary>查看历史目标 v{{ message.goal_version }}（不可批准）</summary><GoalCard :goal="goalFor(message.goal_version)!" :current="false" :can-approve="false" :can-revise="false" :feedback-open="false" :busy="false" :approved="false" /></details>
            </template>
            <p v-else class="error-notice">该目标版本无法读取，请重新读取对话。</p>
          </template>
          <div v-else class="message-card" :class="message.kind"><CheckCircle2 v-if="message.kind === 'approval'" :size="18" aria-hidden="true" /><MessageSquare v-else-if="message.kind === 'feedback'" :size="18" aria-hidden="true" /><p>{{ message.text }}</p><span v-if="message.kind === 'approval'" class="approval-note">历史批准记录 · 不代表代码已执行</span></div>
        </li>
      </ol>
      <div v-if="task.status === 'approved'" class="success-notice" role="status"><CheckCircle2 :size="20" aria-hidden="true" /><div><strong>目标已批准</strong><p>代码执行尚未接入。批准仅确认当前目标，不会启动进程、修改或推送代码。</p></div></div>
      <section v-if="generating" class="generation-card" role="status" aria-live="polite" aria-atomic="true">
        <div class="generation-heading">
          <span class="generation-icon" aria-hidden="true"><LoaderCircle :size="20" :stroke-width="1.75" /></span>
          <div class="generation-copy"><h2>{{ task.current_goal_version ? '正在更新目标' : '正在生成目标' }}</h2><p>{{ task.current_goal_version ? '结合你的修改意见，调整目标与验收标准。' : '根据 Issue 整理目标、修改范围与验收标准。' }}</p></div>
          <span class="generation-dots" aria-hidden="true"><i></i><i></i><i></i></span>
        </div>
        <div class="generation-preview" aria-hidden="true"><span></span><span></span><span></span></div>
        <p class="generation-note">生成完成后，将在这里显示可审阅的目标草案。</p>
      </section>
      <div v-if="actionError" class="error-notice" role="alert"><p>{{ actionError }}</p><NButton v-if="needsSettings" @click="emit('settings')">打开设置</NButton></div>
      <div v-if="task.status === 'generation_failed' && !generating" class="error-notice" role="alert"><div><strong>目标生成失败</strong><p>{{ task.last_error || '目标生成未完成，请显式重试。' }}</p><p v-if="task.current_goal_version">旧目标仅供参考，不能批准失败修改前的版本。</p></div><NButton :disabled="!!busy" @click="perform('retry')">重试生成</NButton></div>
      <div v-if="task.status === 'draft' && !generating" class="draft-actions"><p>来源已保存，可以开始生成目标。刷新页面不会自动重复生成。</p><NButton type="primary" :disabled="!!busy" @click="perform('generate')">生成目标</NButton></div>
      <section v-if="task.current_goal_version" v-show="feedbackOpen" id="goal-feedback-panel" class="feedback-card" aria-labelledby="feedback-title">
        <h2 id="feedback-title">提出修改</h2><p v-if="task.status === 'approved'" class="revocation-note">提交修改将立即撤销当前批准；即使重新生成失败，也不会恢复旧批准。</p>
        <label for="goal-feedback">需要调整的内容</label>
        <NInput ref="feedbackInput" v-model:value="feedback" type="textarea" placeholder="说明需要调整的目标、范围或验收标准" :autosize="{ minRows: 4, maxRows: 12 }" :disabled="!!busy || generating || !canRevise" :input-props="{ id: 'goal-feedback', 'aria-describedby': 'feedback-help' }" @keydown="feedbackKeydown" />
        <div class="feedback-footer"><p id="feedback-help">Ctrl / Cmd + Enter 提交，Enter 换行 · 最多 8,000 字符</p><NButton type="primary" :disabled="!canSubmitFeedback" :loading="busy === 'revise'" @click="perform('revise')">修改并重新生成</NButton></div>
        <p v-if="feedback.trim().length > 8000" class="feedback-error" role="alert">修改意见不能超过 8,000 字符，请缩短后提交。</p>
      </section>
      <div ref="timelineEnd" class="timeline-end" aria-hidden="true" />
    </template>
  </div>
</template>

<style scoped>
.conversation { width: 100%; max-width: 960px; margin: 0 auto; color: var(--text); overflow-wrap: anywhere; }
.conversation-heading { display: flex; justify-content: space-between; align-items: flex-start; gap: 20px; margin-bottom: 24px; }
.conversation-heading > div { min-width: 0; }
h1 { font-size: 26px; line-height: 1.4; margin: 0; }
.eyebrow { margin: 0 0 8px; font-size: 12px; color: var(--text-muted); }
.status-badge { flex-shrink: 0; padding: 5px 10px; border-radius: 6px; background: var(--surface-hover); color: var(--text-secondary); font-size: 12px; }
.status-badge.awaiting_approval { background: var(--warning-bg); color: var(--warning-text); }
.status-badge.generating { background: var(--accent-soft); color: var(--accent); }
.status-badge.approved { background: var(--success-bg); color: var(--success-text); }
.status-badge.generation_failed { background: var(--danger-bg); color: var(--danger-text); }
.source-card { padding: 20px; border: 1px solid var(--border); border-radius: 12px; background: var(--surface); margin-bottom: 24px; }
.source-row { display: flex; align-items: center; flex-wrap: wrap; gap: 8px 12px; min-width: 0; }
.source-row + .source-row { margin-top: 10px; }
.source-label { width: 56px; flex-shrink: 0; color: var(--text-muted); font-size: 12px; }
a { color: var(--accent); text-decoration: none; overflow-wrap: anywhere; }
a svg { display: inline; vertical-align: middle; margin-left: 6px; }
a:hover { text-decoration: underline; }
a:focus-visible, summary:focus-visible { outline: 2px solid var(--accent); outline-offset: 3px; }
code { font-size: 12px; background: var(--code-bg); border-radius: 4px; padding: 3px 6px; overflow-wrap: anywhere; min-width: 0; }
.source-note { margin: 14px 0 0; font-size: 12px; color: var(--text-muted); }
.timeline { list-style: none; padding: 0; margin: 0; }
.timeline-item { margin-bottom: 24px; }
.message-meta { display: flex; flex-wrap: wrap; gap: 6px 12px; align-items: center; margin-bottom: 8px; color: var(--text-muted); font-size: 12px; }
.message-meta > span { color: var(--text-secondary); font-weight: 600; }
.message-card { display: flex; flex-wrap: wrap; align-items: flex-start; gap: 10px; padding: 16px 20px; border: 1px solid var(--border-soft); border-radius: 12px; background: var(--surface); }
.message-card p { margin: 0; white-space: pre-wrap; min-width: 0; flex: 1; }
.message-card svg { flex-shrink: 0; margin-top: 3px; }
.message-card.feedback { background: var(--accent-soft); }
.message-card.approval { color: var(--success-text); background: var(--success-bg); }
.approval-note { flex-basis: 100%; font-size: 12px; }
.history-goal > summary { cursor: pointer; color: var(--text-secondary); padding: 14px 16px; border: 1px solid var(--border); border-radius: 8px; background: var(--surface); }
.history-goal[open] > summary { margin-bottom: 10px; }
.error-notice, .success-notice, .draft-actions { display: flex; align-items: flex-start; flex-wrap: wrap; gap: 12px; padding: 16px 20px; border-radius: 12px; margin-bottom: 24px; }
.error-notice { color: var(--danger-text); background: var(--danger-bg); }
.success-notice { color: var(--success-text); background: var(--success-bg); }
.draft-actions { background: var(--surface); border: 1px solid var(--border); align-items: center; justify-content: space-between; }
.error-notice p, .success-notice p, .draft-actions p { margin: 4px 0 0; }
.error-notice > p { flex: 1; min-width: 0; }
.success-notice > svg { flex-shrink: 0; }
.success-notice > div { min-width: 0; flex: 1; }
.generation-card { padding: 22px 24px; margin-bottom: 24px; border: 1px solid var(--border); border-radius: 12px; background: var(--surface); }
.generation-heading { display: flex; align-items: center; gap: 14px; }
.generation-icon { display: grid; place-items: center; flex: 0 0 40px; height: 40px; border-radius: 12px; color: var(--accent); background: var(--accent-soft); }
.generation-icon svg { animation: generation-spin 2s linear infinite; }
.generation-copy { flex: 1; min-width: 0; }
.generation-copy h2 { margin: 0; color: var(--text); font-size: 15px; font-weight: 600; line-height: 1.6; }
.generation-copy p { margin: 4px 0 0; color: var(--text-secondary); font-size: 13px; line-height: 1.6; }
.generation-dots { display: flex; flex-shrink: 0; gap: 4px; padding-left: 8px; }
.generation-dots i { width: 4px; height: 4px; border-radius: 50%; background: var(--text-muted); animation: generation-pulse 1.8s ease-in-out infinite; }
.generation-dots i:nth-child(2) { animation-delay: .2s; }
.generation-dots i:nth-child(3) { animation-delay: .4s; }
.generation-preview { display: grid; gap: 9px; max-width: 440px; margin: 22px 0 18px 54px; }
.generation-preview span { display: block; height: 7px; border-radius: 4px; background: var(--surface-hover); }
.generation-preview span:nth-child(2) { width: 86%; }
.generation-preview span:nth-child(3) { width: 58%; }
.generation-note { margin: 0 0 0 54px; color: var(--text-muted); font-size: 12px; line-height: 1.6; }
@keyframes generation-spin { to { transform: rotate(360deg); } }
@keyframes generation-pulse { 0%, 100% { opacity: .3; } 50% { opacity: .8; } }
@media (prefers-reduced-motion: reduce) { .generation-icon svg, .generation-dots i { animation: none; } }
@media (max-width: 480px) { .generation-card { padding: 18px 16px; } .generation-heading { align-items: flex-start; gap: 12px; } .generation-dots { display: none; } .generation-preview { margin-left: 52px; } .generation-note { margin-left: 52px; } }
.feedback-card { padding: 20px; border: 1px solid var(--border); border-radius: 12px; background: var(--surface); }
h2 { margin: 0 0 16px; font-size: 17px; }
label { display: block; margin-bottom: 8px; color: var(--text-secondary); font-size: 13px; }
.feedback-footer { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 12px; margin-top: 12px; }
.feedback-footer p { margin: 0; color: var(--text-muted); font-size: 12px; }
.revocation-note { padding: 12px; margin: 0 0 16px; border-radius: 8px; background: var(--warning-bg); color: var(--warning-text); font-size: 13px; }
.feedback-error { color: var(--danger-text); margin-bottom: 0; }
.loading-state { display: flex; flex-direction: column; gap: 24px; }
.timeline-end { height: 1px; }
.sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0; }
@media (max-width: 768px) { h1 { font-size: 23px; } .conversation-heading { flex-wrap: wrap; gap: 12px; } .source-card, .feedback-card, .message-card { padding: 16px; } .source-row { align-items: flex-start; } .source-row a { flex: 1; min-width: 0; } .source-row code { flex-basis: calc(100% - 82px); } .source-row .n-button { margin-left: 68px; } }
</style>
