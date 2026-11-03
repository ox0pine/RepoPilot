<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { NButton, NInput, NSkeleton } from 'naive-ui'
import { ArrowLeft, CheckCircle2, CircleAlert, Copy, LoaderCircle, MessageSquare } from '@lucide/vue'
import GoalCard from '../components/GoalCard.vue'
import RunPanel from '../components/RunPanel.vue'
import { approveGoal, generateGoal, getTask, type GenerationAction, type TaskDetail, type TaskGoal } from '../api/tasks'
import { createRun } from '../api/runs'
import { followLink, isPlainClick } from '../router'
import { sessionToken, sessionVersion } from '../stores/session'
import { consumeAutoGenerate, upsertTaskSummary } from '../stores/tasks'
import { formatDateTime } from '../lib/time'
import { isConflict, isUnknownOutcome, messageOf, needsConfiguration } from '../lib/errors'
import { repositoryName } from '../lib/github'
import { createRequestGate } from '../lib/liveRequest'
import { TASK_STATUS_LABELS, TASK_STATUS_TONES } from '../lib/taskStatus'

const props = defineProps<{ taskId: string }>()
const emit = defineEmits<{ settings: []; changed: [] }>()
const task = ref<TaskDetail | null>(null)
const loading = ref(false)
const readError = ref('')
const actionError = ref('')
const needsSettings = ref(false)
const busy = ref<'generate' | 'revise' | 'retry' | 'approve' | null>(null)
const runActive = ref(false)
const runPanelVersion = ref(0)
const feedback = ref('')
const feedbackOpen = ref(false)
const feedbackInput = ref<InstanceType<typeof NInput> | null>(null)
const timelineEnd = ref<HTMLElement | null>(null)
const announcement = ref('')
let timer: ReturnType<typeof setTimeout> | undefined
let generationProbe: ReturnType<typeof setTimeout> | undefined
let reading = false
let readSequence = 0
const gate = createRequestGate(() => props.taskId)
const generating = computed(() => task.value?.status === 'generating' || (busy.value !== null && busy.value !== 'approve'))
const canRevise = computed(() => !runActive.value && !!task.value?.current_goal_version && ['awaiting_approval', 'approved', 'generation_failed'].includes(task.value.status))
const canSubmitFeedback = computed(() => canRevise.value && !busy.value && !generating.value && !!feedback.value.trim() && feedback.value.trim().length <= 8000)
const messages = computed(() => [...(task.value?.messages ?? [])].sort((a, b) => a.id - b.id))
const goals = computed(() => new Map(task.value?.goals.map(goal => [goal.version, goal]) ?? []))
function goalFor(version: number | null): TaskGoal | undefined { return version === null ? undefined : goals.value.get(version) }
const currentGoal = computed(() => goalFor(task.value?.current_goal_version ?? null))
const historyMessages = computed(() => messages.value.filter(message => message.kind !== 'source'
  && !(message.kind === 'goal' && message.goal_version === task.value?.current_goal_version)))
// Three regions: the plan, the execution, and the folded records (history plus source).
const sections = computed(() => {
  if (!task.value) return []
  const items: Array<{ id: string; label: string }> = []
  if (currentGoal.value) items.push({ id: 'plan', label: '方案' })
  if (task.value.current_goal_version) items.push({ id: 'run', label: '执行' })
  items.push(historyMessages.value.length ? { id: 'history', label: '记录' } : { id: 'source', label: '来源' })
  return items
})
const activeSection = ref('')
let measureFrame = 0
// The page is long enough that reading order needs a spine; the band marks where you are.
function measureSection(): void {
  measureFrame = 0
  const ids = sections.value.map(section => section.id)
  if (!ids.length) return
  // The last sections sit too close to the page end to ever reach the top.
  if (window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 4) {
    activeSection.value = ids[ids.length - 1]!
    return
  }
  let current = ids[0]!
  for (const id of ids) {
    const element = document.getElementById(`section-${id}`)
    if (element && element.getBoundingClientRect().top <= 120) current = id
  }
  activeSection.value = current
}
function queueMeasure(): void {
  if (measureFrame === 0) measureFrame = requestAnimationFrame(measureSection)
}
function jumpToSection(event: MouseEvent, id: string): void {
  if (isPlainClick(event)) return
  const element = document.getElementById(`section-${id}`)
  if (!element) return
  event.preventDefault()
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches
  element.scrollIntoView({ block: 'start', behavior: reduced ? 'auto' : 'smooth' })
  activeSection.value = id
}
function safeLink(value: string): string | undefined {
  return /^https:\/\/github\.com\/[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+(?:\/issues\/[1-9][0-9]*)?$/.test(value) ? value : undefined
}
function stopPolling(): void { if (timer !== undefined) clearTimeout(timer); timer = undefined }
function resetRequests(): void {
  readSequence++
  stopPolling()
  if (generationProbe !== undefined) clearTimeout(generationProbe)
  generationProbe = undefined
  gate.reset()
  reading = false
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
  upsertTaskSummary(detail)
  if (previous && detail.revision > previous.revision) {
    emit('changed')
    if (previous.status === 'generating' && detail.status !== 'generating') announcement.value = `方案状态：${TASK_STATUS_LABELS[detail.status]}`
  }
  schedulePolling()
  await nextTick()
  queueMeasure()
  if (addedMessages && nearBottom) {
    const identity = gate.version()
    await nextTick()
    if (identity === gate.version()) timelineEnd.value?.scrollIntoView({ block: 'end', behavior: 'auto' })
  }
}
async function loadDetail(force = false): Promise<boolean> {
  if ((reading && !force) || !sessionToken.value) return false
  const sequence = ++readSequence
  reading = true
  loading.value = !task.value
  const context = gate.open()
  const current = () => context.current() && sequence === readSequence
  try {
    const detail = await getTask(context.id, context.controller.signal)
    if (!current()) return false
    readError.value = ''
    await mergeDetail(detail)
    return true
  } catch (error) {
    if (current()) {
      readError.value = messageOf(error)
      stopPolling()
    }
    return false
  } finally {
    gate.release(context.controller)
    if (current()) { reading = false; loading.value = false; schedulePolling() }
  }
}
async function perform(action: GenerationAction | 'approve'): Promise<void> {
  if (!task.value || busy.value || runActive.value || task.value.status === 'generating') return
  if (action === 'revise' && !canSubmitFeedback.value) return
  const current = task.value
  const sentFeedback = feedback.value.trim()
  const context = gate.open()
  let approvalConfirmed = false
  busy.value = action
  actionError.value = ''
  needsSettings.value = false
  announcement.value = action === 'approve' ? '正在批准方案并开始执行' : '正在根据 Issue 生成方案'
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
    if (action === 'approve') {
      approvalConfirmed = true
      await createRun(context.id, {
        expected_revision: detail.revision,
        goal_version: detail.current_goal_version,
      }, context.controller.signal)
      if (!context.current()) return
      await loadDetail(true)
      if (!context.current()) return
    }
    if (action === 'revise') feedback.value = ''
    emit('changed')
    announcement.value = action === 'approve' ? '方案已批准，执行已排队' : '方案已生成，请审阅后批准执行'
  } catch (error) {
    if (!context.current()) return
    const conflict = isConflict(error)
    const unknown = isUnknownOutcome(error)
    actionError.value = approvalConfirmed
      ? conflict
        ? '方案已批准，执行状态有更新。请先查看执行记录。'
        : unknown
          ? '方案已批准，暂时无法确认是否已开始执行。请先刷新执行记录，避免重复提交。'
          : `方案已批准，执行未能开始：${messageOf(error)}。处理后可重新开始。`
      : action === 'approve' && unknown
        ? '暂时无法确认批准结果，尚未提交执行。请刷新任务后再确认。'
        : conflict ? '方案已更新，请重新审阅' : messageOf(error)
    needsSettings.value = needsConfiguration(error)
    announcement.value = actionError.value
    await loadDetail(true)
    if (context.current()) emit('changed')
  } finally {
    gate.release(context.controller)
    if (context.current()) {
      busy.value = null
      if (action === 'approve') runPanelVersion.value++
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
  const identity = gate.version()
  try {
    await navigator.clipboard.writeText(task.value.baseline_commit)
    if (identity === gate.version()) announcement.value = '完整 commit SHA 已复制'
  } catch {
    if (identity === gate.version()) announcement.value = '复制失败，请选择并复制完整 SHA'
  }
}
async function executionChanged(): Promise<void> {
  emit('changed')
  await loadDetail(true)
}
watch(() => [props.taskId, sessionVersion()] as const, async () => {
  resetRequests()
  const identity = gate.version()
  task.value = null
  runActive.value = false
  feedback.value = ''
  feedbackOpen.value = false
  readError.value = ''
  actionError.value = ''
  needsSettings.value = false
  busy.value = null
  announcement.value = ''
  loading.value = false
  const autoGenerate = consumeAutoGenerate(props.taskId)
  if (await loadDetail() && identity === gate.version() && autoGenerate && (task.value as TaskDetail | null)?.status === 'draft') void perform('generate')
}, { immediate: true, flush: 'sync' })
onMounted(() => {
  window.addEventListener('scroll', queueMeasure, { passive: true })
  window.addEventListener('resize', queueMeasure, { passive: true })
  queueMeasure()
})
onBeforeUnmount(() => {
  window.removeEventListener('scroll', queueMeasure)
  window.removeEventListener('resize', queueMeasure)
  if (measureFrame !== 0) cancelAnimationFrame(measureFrame)
  resetRequests()
})
</script>

<template>
  <div class="task">
    <a class="back-link" href="/app" @click="followLink($event, '/app')"><ArrowLeft :size="16" :stroke-width="1.5" aria-hidden="true" />任务列表</a>
    <p class="sr-only" role="status" aria-live="polite" aria-atomic="true">{{ announcement }}</p>
    <div v-if="loading && !task" class="loading-state" role="status" aria-label="正在加载任务"><NSkeleton height="36px" width="70%" /><NSkeleton height="140px" :sharp="false" /><NSkeleton height="260px" :sharp="false" /></div>
    <div v-if="readError" class="error-notice" role="alert"><p>无法加载任务：{{ readError }}{{ task ? '。当前显示的是上次加载的内容。' : '' }}</p><NButton size="small" :loading="loading" @click="loadDetail(true)">重新加载</NButton></div>
    <template v-if="task">
      <header class="task-heading">
        <div class="task-title-block">
          <h1>{{ task.title }}</h1>
          <p class="task-meta">
            <a :href="safeLink(task.repository_url)" target="_blank" rel="noopener noreferrer">{{ repositoryName(task.repository_url) }}</a>
            <span class="separator" aria-hidden="true">·</span>
            <a :href="safeLink(task.issue_url)" target="_blank" rel="noopener noreferrer"><span class="ident">#{{ task.source_snapshot.issue_number }}</span></a>
            <span class="separator" aria-hidden="true">·</span>
            <span class="commit"><code :title="task.baseline_commit">{{ task.baseline_commit }}</code><NButton size="tiny" quaternary :focusable="true" aria-label="复制完整 commit SHA" @click="copyCommit"><Copy :size="16" :stroke-width="1.5" aria-hidden="true" /></NButton></span>
          </p>
        </div>
        <span class="status" :data-tone="TASK_STATUS_TONES[task.status]" :class="{ 'is-live': generating }">{{ TASK_STATUS_LABELS[task.status] }}</span>
      </header>

      <nav v-if="sections.length > 1" class="section-nav" aria-label="页内导航">
        <a v-for="section in sections" :key="section.id" :href="`#section-${section.id}`" :class="{ active: activeSection === section.id }" :aria-current="activeSection === section.id ? 'location' : undefined" @click="jumpToSection($event, section.id)">{{ section.label }}</a>
      </nav>

      <div v-if="actionError" class="error-notice" role="alert"><p>{{ actionError }}</p><NButton v-if="needsSettings" @click="emit('settings')">打开设置</NButton></div>

      <section v-if="generating" class="next-step" role="status" aria-live="polite" aria-atomic="true">
        <LoaderCircle class="generation-spinner" :size="16" :stroke-width="1.5" aria-hidden="true" />
        <div><h2>{{ task.current_goal_version ? '正在更新方案' : '正在生成方案' }}</h2><p>{{ task.current_goal_version ? '根据你的修改意见调整方案。' : '正在分析 Issue 和相关代码，整理修改方案。' }}完成后即可审阅并批准执行。</p></div>
      </section>
      <section v-else-if="task.status === 'generation_failed'" class="next-step is-error" role="alert">
        <CircleAlert :size="16" :stroke-width="1.5" aria-hidden="true" />
        <div><h2>方案生成失败</h2><p>{{ task.last_error || '请稍后重试。' }}</p><p v-if="task.current_goal_version" class="hint">请重新生成方案后再批准执行。</p></div>
        <NButton size="small" :disabled="!!busy || runActive" @click="perform('retry')">重新生成</NButton>
      </section>
      <section v-else-if="task.status === 'draft'" class="next-step">
        <div><h2>生成方案</h2><p>任务来源已保存。根据 Issue 和相关代码生成修改方案，审阅后才会执行。</p></div>
        <NButton type="primary" :disabled="!!busy || runActive" @click="perform('generate')">生成方案</NButton>
      </section>

      <section v-if="currentGoal" id="section-plan" class="block">
        <GoalCard :goal="currentGoal" current :can-approve="task.status === 'awaiting_approval' && !runActive" :can-revise="canRevise" :feedback-open="feedbackOpen" :busy="!!busy || generating || runActive" :approved="task.status === 'approved' && task.approved_goal_version === currentGoal.version" @approve="perform('approve')" @revise="toggleFeedback" />
        <section v-show="feedbackOpen" id="goal-feedback-panel" class="feedback-card" aria-labelledby="feedback-title">
          <h3 id="feedback-title">提出修改</h3><p v-if="task.status === 'approved'" class="revocation-note">修改后需要重新批准方案；如果生成失败，请重试后再执行。</p>
          <label for="goal-feedback">需要调整的内容</label>
          <NInput ref="feedbackInput" v-model:value="feedback" type="textarea" placeholder="例如：只修复边界条件，保留现有接口，并补充测试" :autosize="{ minRows: 4, maxRows: 12 }" :disabled="!!busy || generating || !canRevise" :input-props="{ id: 'goal-feedback', 'aria-describedby': 'feedback-help' }" @keydown="feedbackKeydown" />
          <div class="feedback-footer"><p id="feedback-help"><kbd class="kbd">Ctrl</kbd><span aria-hidden="true">/</span><kbd class="kbd">⌘</kbd><span aria-hidden="true">+</span><kbd class="kbd">Enter</kbd> 提交 · 最多 8,000 字符</p><NButton type="primary" :disabled="!canSubmitFeedback" :loading="busy === 'revise'" @click="perform('revise')">更新方案</NButton></div>
          <p v-if="feedback.trim().length > 8000" class="feedback-error" role="alert">修改意见不能超过 8,000 字符，请缩短后提交。</p>
        </section>
      </section>

      <RunPanel v-if="task.current_goal_version" id="section-run" :key="`${task.id}:${runPanelVersion}`" :task-id="task.id" :revision="task.revision" :goal-version="task.current_goal_version" :approved="task.status === 'approved' && task.approved_goal_version === task.current_goal_version && !busy && !generating" @active="runActive = $event" @changed="executionChanged" />

      <details v-if="historyMessages.length" id="section-history" class="history-block">
        <summary>历史记录（{{ historyMessages.length }} 项）</summary>
        <ol class="timeline" aria-label="方案与批准记录">
          <li v-for="message in historyMessages" :key="message.id" class="timeline-item">
            <div class="message-meta"><span>{{ message.kind === 'feedback' ? '你的修改意见' : message.kind === 'approval' ? '批准记录' : '方案' }}<span v-if="message.goal_version" class="ident">v{{ message.goal_version }}</span></span><time :datetime="message.created_at">{{ formatDateTime(message.created_at) }}</time></div>
            <template v-if="message.kind === 'goal'">
              <details v-if="goalFor(message.goal_version)" class="history-goal"><summary>查看方案 v{{ message.goal_version }}</summary><GoalCard :goal="goalFor(message.goal_version)!" :current="false" :can-approve="false" :can-revise="false" :feedback-open="false" :busy="false" :approved="task.approved_goal_version === message.goal_version" /></details>
              <p v-else class="error-notice">无法加载这版方案，请重新加载任务。</p>
            </template>
            <div v-else class="message-card" :class="message.kind"><CheckCircle2 v-if="message.kind === 'approval'" :size="16" :stroke-width="1.5" aria-hidden="true" /><MessageSquare v-else :size="16" :stroke-width="1.5" aria-hidden="true" /><p>{{ message.kind === 'approval' ? '方案已批准' : message.text }}</p></div>
          </li>
        </ol>
      </details>

      <details id="section-source" class="context-note">
        <summary>任务来源快照</summary>
        <p>来源保存于 {{ formatDateTime(task.source_snapshot.fetched_at) }}。更换仓库、版本或 Issue，请新建任务。</p>
        <p>代码版本：<code>{{ task.source_snapshot.repository_context.commit }}</code></p>
        <p>生成方案时参考了以下代码片段，并非完整仓库审查。执行时会克隆完整的指定版本。</p>
        <p v-if="task.source_snapshot.repository_context.tree_truncated">仅显示部分文件目录。</p>
        <ul v-if="task.source_snapshot.repository_context.omissions.length"><li v-for="(omission, index) in task.source_snapshot.repository_context.omissions" :key="index">{{ omission }}</li></ul>
        <p v-if="!task.source_snapshot.repository_context.files.length">没有可展示的代码片段。</p>
        <details v-for="file in task.source_snapshot.repository_context.files" :key="file.path"><summary>{{ file.path }}{{ file.truncated ? '（内容已截断）' : '' }}</summary><p>{{ file.reason }}</p><p>Blob SHA：<code>{{ file.blob_sha }}</code></p><pre>{{ file.content }}</pre></details>
        <details><summary>参考文件目录</summary><pre>{{ task.source_snapshot.repository_context.tree.join('\n') }}</pre></details>
      </details>

      <div ref="timelineEnd" class="timeline-end" aria-hidden="true" />
    </template>
  </div>
</template>

<style scoped>
.task { width: 100%; max-width: var(--content-width-detail); color: var(--text); overflow-wrap: anywhere; }
.task-heading { display: flex; justify-content: space-between; align-items: flex-start; gap: 24px; margin: 8px 0 24px; }
.task-title-block { min-width: 0; }
h1 { margin: 0 0 8px; font-size: 26px; font-weight: 500; line-height: 1.3; }
.task-meta { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; margin: 0; color: var(--text-muted); font-size: 12px; }
.task-meta a { display: inline-flex; align-items: center; color: var(--text-secondary); text-decoration: none; overflow-wrap: anywhere; }
.task-meta a:hover { color: var(--text); }
.task-meta a:focus-visible, .commit code:focus-visible { border-radius: 4px; }
.task-meta .ident { color: var(--text-secondary); }
.task-meta .ident:hover { background: var(--border-soft); }
.separator { color: var(--text-muted); }
.commit { display: inline-flex; align-items: center; gap: 4px; min-width: 0; }
code { font-size: 11px; font-family: var(--font-mono); color: var(--text-muted); overflow-wrap: anywhere; min-width: 0; }
.task-heading > .status { flex-shrink: 0; margin-top: 8px; }
.section-nav { position: sticky; top: 0; z-index: 2; display: flex; gap: 4px; margin: 0 0 16px; padding: 8px 0; border-bottom: 1px solid var(--border-soft); background: var(--bg); overflow-x: auto; }
.section-nav a { flex: none; padding: 4px 10px; border: 1px solid transparent; border-radius: 6px; color: var(--text-secondary); font-size: 13px; line-height: 20px; text-decoration: none; }
.section-nav a:hover { background: var(--surface-hover); color: var(--text); }
.section-nav a.active { border-color: var(--border); background: var(--surface); color: var(--text); }
#section-plan, #section-run, #section-history, #section-source { scroll-margin-top: 56px; }
.block { margin-bottom: 24px; }
.next-step { display: flex; align-items: flex-start; gap: 12px; padding: 16px 20px; margin-bottom: 24px; border: 1px solid var(--border); border-radius: 12px; background: var(--surface); }
.next-step > svg { flex-shrink: 0; margin-top: 3px; color: var(--text-muted); }
.next-step > div { flex: 1; min-width: 0; }
.next-step h2 { margin: 0 0 4px; font-size: 15px; font-weight: 500; line-height: 1.5; }
.next-step p { margin: 0; color: var(--text-secondary); font-size: 13px; line-height: 1.7; }
.next-step .hint { margin-top: 4px; color: var(--text-muted); font-size: 12px; }
.next-step.is-error { border-color: var(--border); background: var(--danger-bg); }
.next-step.is-error > svg, .next-step.is-error h2 { color: var(--danger-text); }
.next-step.is-error p { color: var(--danger-text); }
.next-step > .generation-spinner { color: var(--accent); animation: generation-spin 1.1s linear infinite; }
@keyframes generation-spin { to { transform: rotate(360deg); } }
@media (prefers-reduced-motion: reduce) { .generation-spinner { animation: none; } }
.timeline { list-style: none; padding: 0; margin: 0; }
.timeline-item { margin-bottom: 16px; }
.timeline-item:last-child { margin-bottom: 0; }
.message-meta { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-bottom: 8px; color: var(--text-muted); font-size: 12px; font-variant-numeric: tabular-nums; }
.message-meta > span { display: inline-flex; align-items: center; gap: 8px; color: var(--text-secondary); font-weight: 500; }
.message-card { display: flex; flex-wrap: wrap; align-items: flex-start; gap: 8px; padding: 12px 16px; border: 1px solid var(--border-soft); border-radius: 8px; background: var(--surface); }
.message-card p { margin: 0; white-space: pre-wrap; min-width: 0; flex: 1; }
.message-card svg { flex-shrink: 0; margin-top: 4px; color: var(--text-muted); }
.message-card.feedback { background: var(--accent-soft); border-color: transparent; }
.message-card.approval { color: var(--success-text); background: var(--success-bg); border-color: transparent; }
.message-card.approval svg { color: var(--success-text); }
.history-block, .context-note { margin-bottom: 16px; border: 1px solid var(--border); border-radius: 12px; background: var(--surface); font-size: 13px; line-height: 1.7; }
.history-block > summary, .context-note > summary { cursor: pointer; padding: 12px 16px; color: var(--text-secondary); font-weight: 500; }
.history-block[open] > summary, .context-note[open] > summary { border-bottom: 1px solid var(--border-soft); margin-bottom: 16px; }
.history-block > ol, .context-note > p, .context-note > ul, .context-note > details { margin-left: 16px; margin-right: 16px; }
.history-block > ol { margin-bottom: 16px; }
.context-note > details { padding: 8px 0; }
.context-note > details > summary { cursor: pointer; padding: 4px 0; }
.history-goal { border: 1px solid var(--border-soft); border-radius: 8px; background: var(--bg); }
.history-goal > summary { cursor: pointer; padding: 8px 12px; color: var(--text-secondary); font-size: 13px; }
.history-goal[open] > summary { border-bottom: 1px solid var(--border-soft); }
.context-note pre { padding: 12px; white-space: pre-wrap; overflow-wrap: anywhere; word-break: break-word; max-height: 320px; overflow-y: auto; background: var(--surface-hover); border-radius: 8px; font-size: 12px; }
.error-notice { display: flex; align-items: flex-start; flex-wrap: wrap; gap: 12px; padding: 12px 16px; margin-bottom: 24px; border-radius: 8px; color: var(--danger-text); background: var(--danger-bg); font-size: 13px; }
.error-notice p { margin: 0; flex: 1; min-width: 0; }
.feedback-card { margin-top: 12px; padding: 20px; border: 1px solid var(--border); border-radius: 12px; background: var(--surface); }
.feedback-card h3 { margin: 0 0 12px; font-size: 15px; font-weight: 500; }
.feedback-card label { display: block; margin-bottom: 8px; color: var(--text-secondary); font-size: 13px; }
.feedback-footer { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 16px; margin-top: 12px; }
.feedback-footer p { display: flex; align-items: center; gap: 4px; margin: 0; color: var(--text-muted); font-size: 12px; }
.revocation-note { padding: 12px; margin: 0 0 16px; border-radius: 8px; background: var(--warning-bg); color: var(--warning-text); font-size: 13px; }
.feedback-error { color: var(--danger-text); margin: 8px 0 0; font-size: 12px; }
.loading-state { display: flex; flex-direction: column; gap: 16px; }
.timeline-end { height: 1px; }
@media (max-width: 768px) {
  h1 { font-size: 22px; }
  .task-heading { flex-wrap: wrap; gap: 8px; }
  .task-heading > .status { margin-top: 0; }
  .next-step { padding: 16px; flex-wrap: wrap; }
  .next-step > button { margin-left: 28px; }
  .section-nav { margin-left: -16px; margin-right: -16px; padding-left: 16px; padding-right: 16px; }
  .history-block > ol, .context-note > p, .context-note > ul, .context-note > details { margin-left: 12px; margin-right: 12px; }
  .feedback-card, .message-card { padding: 16px; }
}
</style>
