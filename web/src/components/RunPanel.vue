<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { NButton } from 'naive-ui'
import { ApiError } from '../api/client'
import { cancelRun, createDelivery, createRun, getRun, listRuns, type RunDetail, type RunStatus, type RunSummary } from '../api/runs'
import { sessionToken, sessionVersion } from '../stores/session'

const props = defineProps<{ taskId: string; revision: number; goalVersion: number; approved: boolean }>()
const emit = defineEmits<{ changed: []; active: [value: boolean] }>()
const runs = ref<RunSummary[]>([])
const selectedId = ref('')
const detail = ref<RunDetail | null>(null)
const readError = ref('')
const actionError = ref('')
const loading = ref(false)
const busy = ref<'start' | 'cancel' | 'delivery' | null>(null)
const known = ref(false)
const uncertain = ref(false)
let epoch = 0
let sequence = 0
let detailSequence = 0
let timer: ReturnType<typeof setTimeout> | undefined
const controllers = new Set<AbortController>()
const activeStatus = (status: RunStatus) => status === 'queued' || status === 'running'
const activeRun = computed(() => runs.value.find(run => activeStatus(run.status)))
const guarded = computed(() => !known.value || !!activeRun.value || uncertain.value || !!readError.value || !!busy.value)
const labels: Record<RunStatus, string> = { queued: '排队中', running: '执行中', completed: '检查通过，待审阅', failed: '执行失败', blocked: '需要处理', exhausted: '已达执行上限', cancelled: '已取消', interrupted: '执行中断' }
const canStart = computed(() => props.approved && !guarded.value)
function isRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === 'object' && !Array.isArray(value)
}
const environment = computed(() => {
  const events = detail.value?.events ?? []
  for (let index = events.length - 1; index >= 0; index--) {
    const event = events[index]!
    if (event.kind !== 'context') continue
    const value = event.payload.environment
    return isRecord(value) ? value : null
  }
  return null
})
const environmentProjects = computed(() => {
  const projects = environment.value?.projects
  return Array.isArray(projects) ? projects.filter(isRecord) : []
})
function message(error: unknown): string { return error instanceof Error ? error.message : '请求失败' }
function date(value: string): string { return new Date(value).toLocaleString('zh-CN') }
function stop(): void { if (timer !== undefined) clearTimeout(timer); timer = undefined }
function reset(): void {
  epoch++; sequence++; detailSequence++; stop()
  for (const controller of controllers) controller.abort()
  controllers.clear()
}
function context() {
  const controller = new AbortController()
  controllers.add(controller)
  const identity = epoch
  const version = sessionVersion()
  const id = props.taskId
  return { controller, id, current: () => identity === epoch && version === sessionVersion() && id === props.taskId && !controller.signal.aborted }
}
function schedule(): void {
  stop()
  if (activeRun.value && !readError.value && !busy.value) timer = setTimeout(() => { void refresh() }, 2000)
}
function merge(value: RunDetail): void {
  if (value.task_id !== props.taskId || value.id !== selectedId.value) return
  // A late read must not move a confirmed terminal state back to running.
  if (detail.value?.id === value.id && !activeStatus(detail.value.status) && activeStatus(value.status)) return
  detail.value = value
}
async function readSelected(): Promise<void> {
  const id = selectedId.value
  if (!id) return
  const ctx = context()
  const order = ++detailSequence
  try {
    const value = await getRun(ctx.id, id, ctx.controller.signal)
    if (ctx.current() && order === detailSequence && id === selectedId.value) merge(value)
  } catch (error) {
    if (ctx.current() && order === detailSequence) { readError.value = message(error); stop() }
  } finally { controllers.delete(ctx.controller) }
}
async function refresh(): Promise<void> {
  if (!sessionToken.value) return
  stop()
  const ctx = context()
  const order = ++sequence
  loading.value = true
  try {
    const values = await listRuns(ctx.id, ctx.controller.signal)
    if (!ctx.current() || order !== sequence) return
    runs.value = values
    known.value = true
    uncertain.value = false
    readError.value = ''
    if (!selectedId.value && values[0]) selectedId.value = values[0].id
    await readSelected()
  } catch (error) {
    if (ctx.current() && order === sequence) readError.value = message(error)
  } finally {
    controllers.delete(ctx.controller)
    if (ctx.current() && order === sequence) { loading.value = false; schedule() }
  }
}
async function select(id: string): Promise<void> {
  if (selectedId.value === id) return
  selectedId.value = id
  detail.value = null
  await readSelected()
}
async function mutate(action: 'start' | 'cancel'): Promise<void> {
  if (busy.value || (action === 'start' && !canStart.value) || (action === 'cancel' && (!detail.value || !activeStatus(detail.value.status)))) return
  const ctx = context()
  const cancelId = detail.value?.id
  busy.value = action
  stop()
  // Invalidate older GETs before the state-changing request.
  sequence++; detailSequence++
  actionError.value = ''
  try {
    const value = action === 'start'
      ? await createRun(ctx.id, { expected_revision: props.revision, goal_version: props.goalVersion }, ctx.controller.signal)
      : await cancelRun(ctx.id, cancelId!, ctx.controller.signal)
    if (!ctx.current()) return
    if (action === 'start') selectedId.value = value.id
    merge(value)
    runs.value = [value, ...runs.value.filter(run => run.id !== value.id)].sort((a, b) => b.created_at.localeCompare(a.created_at)).slice(0, 50)
    emit('changed')
    await refresh()
  } catch (error) {
    if (!ctx.current()) return
    const conflict = error instanceof ApiError && error.status === 409
    const unknown = !(error instanceof ApiError) || error.status >= 500 || error.status < 400
    actionError.value = conflict ? '执行状态已更新，请刷新记录后再操作。' : unknown ? '暂时无法确认操作结果，请先刷新记录，避免重复提交。' : message(error)
    uncertain.value = unknown
    emit('changed')
    // Only read to reconcile; never retry a POST automatically.
    await refresh()
  } finally {
    controllers.delete(ctx.controller)
    if (ctx.current()) { busy.value = null; schedule() }
  }
}
async function deliver(): Promise<void> {
  if (busy.value || !detail.value || detail.value.status !== 'completed' || !detail.value.patch || detail.value.delivery) return
  const ctx = context()
  const runId = detail.value.id
  busy.value = 'delivery'
  actionError.value = ''
  sequence++; detailSequence++
  try {
    const delivery = await createDelivery(ctx.id, runId, ctx.controller.signal)
    if (!ctx.current() || detail.value?.id !== runId) return
    detail.value = { ...detail.value, delivery }
  } catch (error) {
    if (!ctx.current()) return
    const unknown = !(error instanceof ApiError) || error.status >= 500 || error.status < 400
    actionError.value = unknown
      ? '暂时无法确认分支是否已提交。请先刷新记录；再次提交会核对原分支，不会覆盖其他变更。'
      : message(error)
    uncertain.value = unknown
    await refresh()
  } finally {
    controllers.delete(ctx.controller)
    if (ctx.current()) busy.value = null
  }
}
function download(extension: 'patch' | 'json'): void {
  if (!detail.value) return
  const body = extension === 'patch' ? detail.value.patch : JSON.stringify(detail.value, null, 2)
  const url = URL.createObjectURL(new Blob([body], { type: extension === 'patch' ? 'text/plain;charset=utf-8' : 'application/json;charset=utf-8' }))
  const anchor = document.createElement('a')
  anchor.href = url; anchor.download = `repopilot-${detail.value.id}.${extension}`
  anchor.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
watch(guarded, value => emit('active', value), { immediate: true, flush: 'sync' })
watch(() => [props.taskId, sessionVersion()] as const, () => {
  reset()
  runs.value = []; detail.value = null; selectedId.value = ''; known.value = false
  readError.value = ''; actionError.value = ''; uncertain.value = false; busy.value = null
  void refresh()
}, { immediate: true, flush: 'sync' })
onBeforeUnmount(() => { reset(); emit('active', false) })
</script>

<template>
  <section class="run-panel" aria-labelledby="run-heading">
    <header class="panel-header">
      <h2 id="run-heading">执行</h2>
      <div class="panel-actions">
        <NButton v-if="detail && activeStatus(detail.status)" :disabled="detail.cancel_requested || !!busy" :loading="busy === 'cancel'" @click="mutate('cancel')">取消执行</NButton>
        <NButton v-else-if="approved && !activeRun" :type="runs.length ? 'default' : 'primary'" :disabled="!canStart" :loading="busy === 'start'" @click="mutate('start')">{{ runs.length ? '重新执行' : '开始执行' }}</NButton>
        <NButton size="small" :disabled="!!busy" :loading="loading" @click="refresh">刷新</NButton>
      </div>
    </header>
    <p class="panel-note">
      每次执行都从任务的基准版本开始，不保留上次执行中的修改。
      <span v-if="!approved">批准方案后才能执行。</span>
      <span v-else-if="guarded && !activeRun">请等待当前操作完成，或刷新执行记录。</span>
    </p>
    <div v-if="actionError" class="notice is-warning" role="alert">{{ actionError }}</div>
    <div v-if="readError" class="notice is-warning" role="alert">无法加载执行记录：{{ readError }}。当前内容可能不是最新状态，请刷新后再操作。</div>

    <p v-if="known && !runs.length" class="empty-note">还没有执行记录。</p>
    <nav v-if="runs.length > 1" class="run-list" aria-label="执行记录">
      <button v-for="run in runs" :key="run.id" type="button" :aria-pressed="selectedId === run.id" @click="select(run.id)">
        <strong>{{ run.cancel_requested && activeStatus(run.status) ? '正在停止' : labels[run.status] }}</strong>
        <span>v{{ run.goal_version }} · {{ date(run.created_at) }}</span>
      </button>
    </nav>

    <article v-if="detail" class="run-detail" aria-label="选中执行的详情">
      <header class="detail-header">
        <h3>{{ detail.cancel_requested && activeStatus(detail.status) ? '正在停止' : labels[detail.status] }}</h3>
        <span class="detail-meta">方案 v{{ detail.goal_version }} · {{ detail.started_at ? date(detail.started_at) : '尚未开始' }}</span>
      </header>
      <p v-if="activeStatus(detail.status)" class="panel-note">离开页面后任务仍会继续。取消后，请等待状态更新。</p>
      <p v-if="detail.error" class="notice is-warning" role="alert">{{ detail.error }}</p>

      <section class="review-block" aria-labelledby="patch-title">
        <div class="block-header">
          <h4 id="patch-title">代码变更</h4>
          <NButton size="small" :disabled="!detail.patch" @click="download('patch')">下载补丁</NButton>
        </div>
        <pre class="patch">{{ detail.patch || '暂无可展示的代码变更，请结合执行报告查看原因。' }}</pre>
      </section>

      <section class="review-block" aria-labelledby="checks-title">
        <div class="block-header">
          <h4 id="checks-title">检查</h4>
          <span class="block-meta">{{ detail.checks.length ? `${detail.checks.filter(entry => entry.exit_code === 0).length} / ${detail.checks.length} 通过` : '尚未开始' }}</span>
        </div>
        <p v-if="!detail.checks.length" class="muted">检查开始后，结果会显示在这里。</p>
        <details v-for="(entry, index) in detail.checks" :key="index" class="check-record" :open="entry.exit_code !== 0">
          <summary>
            <span class="check-line">
              <span class="check-state" :class="entry.exit_code === null ? 'pending' : entry.exit_code === 0 ? 'passed' : 'failed'">{{ entry.exit_code === null ? '未完成' : entry.exit_code === 0 ? '通过' : `未通过（退出码 ${entry.exit_code}）` }}</span>
              <span class="check-phase">{{ entry.phase === 'setup' ? '环境准备' : entry.phase === 'baseline' ? '修改前检查' : '修改后检查' }}</span>
            </span>
            <code class="check-command">{{ entry.command }}</code>
          </summary>
          <pre>{{ entry.output || '没有输出' }}</pre>
          <p v-if="entry.truncated" class="muted">内容较长，仅显示部分输出。</p>
        </details>
        <details class="check-config">
          <summary>检查命令与运行环境</summary>
          <h5>检查命令</h5>
          <pre>{{ detail.check_command || '尚未选择检查命令' }}</pre>
          <h5>环境准备命令</h5>
          <pre>{{ detail.setup_command || '暂无准备记录' }}</pre>
          <p class="muted">系统自动准备依赖和代码分析工具。准备期间可能联网安装依赖，代码修改阶段保持离线。</p>
          <template v-if="environment">
            <h5>环境状态</h5>
            <p>{{ environment.ready === true ? '已就绪' : environment.ready === false ? '未就绪' : '尚未确认' }}</p>
            <section v-for="(project, index) in environmentProjects" :key="index" class="environment-project">
              <h5>{{ project.root || '.' }} · {{ project.language || '尚未识别语言' }}</h5>
              <dl>
                <dt>状态</dt><dd>{{ project.ready === true ? '已准备' : project.ready === false ? '未就绪' : '尚未确认' }}</dd>
                <dt>解释器</dt><dd>{{ project.interpreter || '不适用 / 尚未解析' }}</dd>
                <dt>Python / Node 版本</dt><dd>{{ project.python_version || '—' }} / {{ project.node_version || '—' }}</dd>
                <dt>包管理器</dt><dd>{{ project.package_manager || '—' }}</dd>
                <dt>TypeScript SDK</dt><dd>{{ project.typescript_sdk || '—' }}</dd>
              </dl>
              <p v-if="project.blocked_reason" class="notice is-warning">{{ project.blocked_reason }}</p>
              <pre>{{ JSON.stringify(project.servers ?? project.server_argv ?? {}, null, 2) }}</pre>
            </section>
            <details><summary>完整环境数据</summary><pre>{{ JSON.stringify(environment, null, 2) }}</pre></details>
          </template>
          <p v-else class="muted">运行环境尚未准备。</p>
        </details>
      </section>

      <section class="review-block" aria-labelledby="report-title">
        <div class="block-header"><h4 id="report-title">执行报告</h4></div>
        <pre>{{ detail.report || '执行结束后可查看报告。' }}</pre>
      </section>

      <section class="delivery-block" aria-labelledby="delivery-title">
        <div class="block-header"><h4 id="delivery-title">交付到修复分支</h4></div>
        <p class="muted">推送到你的 GitHub 仓库中的独立修复分支。默认分支不会改变，合并由你在 GitHub 上完成。</p>
        <template v-if="detail.delivery">
          <dl>
            <dt>分支</dt><dd><a :href="detail.delivery.branch_url" target="_blank" rel="noopener noreferrer">{{ detail.delivery.branch }}</a></dd>
            <dt>Commit</dt><dd><code>{{ detail.delivery.commit_sha }}</code></dd>
          </dl>
          <NButton tag="a" :href="detail.delivery.compare_url" target="_blank" rel="noopener noreferrer" type="primary">在 GitHub 审阅变更</NButton>
        </template>
        <div v-else class="delivery-actions">
          <NButton type="primary" :disabled="detail.status !== 'completed' || !detail.patch || !!busy" :loading="busy === 'delivery'" @click="deliver">提交修复分支</NButton>
          <p class="muted">需要检查通过、有代码变更，且 GitHub token 具有仓库写入权限。</p>
        </div>
      </section>

      <details class="run-raw">
        <summary>执行信息与日志</summary>
        <dl>
          <dt>执行编号</dt><dd><code>{{ detail.id }}</code></dd>
          <dt>方案版本</dt><dd>v{{ detail.goal_version }}</dd>
          <dt>运行镜像</dt><dd>{{ detail.image }} / {{ detail.image_id || '尚未准备' }}</dd>
          <dt>开始 / 结束</dt><dd>{{ detail.started_at ? date(detail.started_at) : '尚未开始' }} / {{ detail.finished_at ? date(detail.finished_at) : '尚未结束' }}</dd>
        </dl>
        <div class="raw-actions"><NButton size="small" @click="download('json')">下载执行记录</NButton></div>
        <h5>执行日志（{{ detail.events.length }} 条）</h5>
        <ol class="events"><li v-for="event in detail.events" :key="event.id"><strong>#{{ event.id }} · {{ event.kind }}</strong> · {{ date(event.created_at) }}<span v-if="event.payload.call_id" class="run-id">调用编号：{{ event.payload.call_id }}</span><pre>{{ JSON.stringify(event.payload, null, 2) }}</pre></li></ol>
      </details>
    </article>
  </section>
</template>

<style scoped>
.run-panel { min-width: 0; margin: 24px 0; padding: 20px; border: 1px solid var(--border); border-radius: 12px; background: var(--surface); overflow-wrap: anywhere; }
.panel-header { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 12px; }
h2 { margin: 0; font-size: 16px; }
.panel-actions { display: flex; align-items: center; gap: 8px; }
.panel-note { margin: 8px 0 0; color: var(--text-secondary); font-size: 13px; line-height: 1.7; }
.panel-note span { color: var(--text-muted); }
.empty-note { margin: 14px 0 0; color: var(--text-muted); font-size: 13px; }
.notice { padding: 12px; margin: 14px 0 0; border-radius: 8px; font-size: 13px; white-space: pre-wrap; }
.notice.is-warning { background: var(--warning-bg); color: var(--warning-text); }
.run-list { display: flex; flex-wrap: wrap; gap: 8px; padding: 0; margin: 14px 0 0; list-style: none; }
.run-list button { display: flex; flex-direction: column; gap: 2px; padding: 8px 12px; border: 1px solid var(--border); border-radius: 8px; background: var(--surface); color: var(--text-secondary); font: inherit; font-size: 12px; text-align: left; cursor: pointer; }
.run-list button strong { font-size: 13px; font-weight: 600; color: var(--text); }
.run-list button[aria-pressed="true"] { border-color: var(--accent); background: var(--accent-soft); }
.run-list button:focus-visible, summary:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.run-detail { margin-top: 16px; padding-top: 16px; border-top: 1px solid var(--border-soft); }
.detail-header { display: flex; align-items: baseline; flex-wrap: wrap; gap: 8px 12px; }
h3 { margin: 0; font-size: 15px; }
.detail-meta { color: var(--text-muted); font-size: 12px; }
.review-block { margin-top: 20px; }
.block-header { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 8px; }
h4 { margin: 0; font-size: 14px; }
.block-meta { color: var(--text-muted); font-size: 12px; }
h5 { margin: 16px 0 6px; font-size: 13px; font-weight: 600; }
pre { padding: 12px; margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; word-break: break-word; max-height: 360px; overflow-y: auto; border-radius: 6px; background: var(--code-bg); font-size: 12px; line-height: 1.6; }
.patch { max-height: 480px; }
.muted { color: var(--text-muted); font-size: 12px; line-height: 1.7; }
.check-record { margin-bottom: 6px; border: 1px solid var(--border-soft); border-radius: 8px; }
.check-record > summary { display: flex; flex-direction: column; gap: 4px; padding: 10px 12px; cursor: pointer; font-size: 12px; }
.check-line { display: flex; align-items: center; gap: 8px; }
.check-state { flex-shrink: 0; padding: 1px 7px; border-radius: 4px; font-weight: 600; }
.check-state.passed { background: var(--success-bg); color: var(--success-text); }
.check-state.failed { background: var(--danger-bg); color: var(--danger-text); }
.check-state.pending { background: var(--surface-hover); color: var(--text-secondary); }
.check-phase { flex-shrink: 0; color: var(--text-secondary); }
.check-command { color: var(--text-muted); background: none; padding: 0; overflow-wrap: anywhere; }
.check-record > pre { margin: 0 12px 12px; max-height: 240px; }
.check-config { margin-top: 10px; border: 1px solid var(--border-soft); border-radius: 8px; }
.check-config > summary, .run-raw > summary { padding: 10px 12px; cursor: pointer; color: var(--text-secondary); font-size: 13px; }
.check-config > h5, .check-config > p, .check-config > pre, .check-config > details, .check-config > section { margin-left: 12px; margin-right: 12px; }
.check-config > h5:first-of-type { margin-top: 4px; }
.environment-project { margin-top: 12px; }
dl { display: grid; grid-template-columns: auto minmax(0, 1fr); gap: 6px 12px; margin: 10px 0 0; font-size: 12px; }
dt { color: var(--text-muted); } dd { margin: 0; }
.delivery-block { margin-top: 20px; padding: 16px; border: 1px solid var(--border); border-radius: 10px; background: var(--bg); }
.delivery-actions { display: flex; align-items: center; flex-wrap: wrap; gap: 12px; margin-top: 12px; }
.delivery-actions .muted { flex: 1; min-width: 200px; margin: 0; }
.run-raw { margin-top: 20px; border-top: 1px solid var(--border-soft); }
.run-raw > summary { padding: 12px 0; }
.run-raw > h5, .run-raw > dl, .run-raw > ol, .run-raw > .raw-actions { margin-left: 0; margin-right: 0; }
.raw-actions { margin-top: 12px; }
.events { padding-left: 18px; margin: 0; font-size: 12px; }
.events li { margin-bottom: 12px; }
.run-id { display: block; margin-top: 4px; color: var(--text-muted); }
@media (max-width: 600px) {
  .run-panel { padding: 16px; }
  .panel-header { align-items: flex-start; }
  dl { grid-template-columns: minmax(0, 1fr); }
  dd { margin-bottom: 6px; }
  .delivery-actions > button { width: 100%; }
}
</style>
