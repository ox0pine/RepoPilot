<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { NButton, NInput } from 'naive-ui'
import { ApiError } from '../api/client'
import { cancelRun, createRun, getExecution, getRun, listRuns, type RunDetail, type RunStatus, type RunSummary } from '../api/runs'
import { sessionToken, sessionVersion } from '../stores/session'

const props = defineProps<{ taskId: string; revision: number; goalVersion: number; approved: boolean }>()
const emit = defineEmits<{ changed: []; active: [value: boolean] }>()
const image = ref('')
const setup = ref('')
const check = ref('')
const runs = ref<RunSummary[]>([])
const selectedId = ref('')
const detail = ref<RunDetail | null>(null)
const readError = ref('')
const configError = ref('')
const actionError = ref('')
const loading = ref(false)
const busy = ref<'start' | 'cancel' | null>(null)
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
const labels: Record<RunStatus, string> = { queued: '排队中', running: '执行中', completed: '检查通过，待人工审阅', failed: '执行失败', blocked: '执行受阻', exhausted: '预算已耗尽', cancelled: '已取消', interrupted: '执行已中断' }
const valid = computed(() => !!image.value.trim() && image.value.length <= 256 && !!check.value.trim() && check.value.length <= 4000 && setup.value.length <= 4000 && ![image.value, setup.value, check.value].some(value => value.includes('\0')))
const canStart = computed(() => props.approved && !guarded.value && !busy.value && valid.value)
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
async function loadConfig(): Promise<void> {
  const ctx = context()
  const original = image.value
  try {
    const value = await getExecution(ctx.controller.signal)
    if (ctx.current()) {
      configError.value = ''
      if (!original && image.value === original) image.value = value.default_image
    }
  } catch (error) { if (ctx.current()) configError.value = message(error) }
  finally { controllers.delete(ctx.controller) }
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
      ? await createRun(ctx.id, { expected_revision: props.revision, goal_version: props.goalVersion, image: image.value, setup_command: setup.value, check_command: check.value }, ctx.controller.signal)
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
    actionError.value = conflict ? '状态已更新，请重新读取并人工确认后再提交。输入已保留。' : unknown ? '提交结果未确认，请重新读取。不会自动重发。' : message(error)
    uncertain.value = unknown
    emit('changed')
    // Only read to reconcile; never retry a POST automatically.
    await refresh()
  } finally {
    controllers.delete(ctx.controller)
    if (ctx.current()) { busy.value = null; schedule() }
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
  readError.value = ''; configError.value = ''; actionError.value = ''; uncertain.value = false; busy.value = null
  image.value = ''; setup.value = ''; check.value = ''
  void loadConfig(); void refresh()
}, { immediate: true, flush: 'sync' })
onBeforeUnmount(() => { reset(); emit('active', false) })
</script>

<template>
  <section class="run-panel" aria-labelledby="run-heading">
    <h2 id="run-heading">执行与成果审阅</h2>
    <p>批准不会自动执行，请单独开始执行。本次从固定 commit 重新开始，不继承上次修改；结果仍需人工审阅。</p>
    <div v-if="configError" class="notice" role="alert">默认镜像读取失败：{{ configError }} <NButton size="small" @click="loadConfig">重新读取默认值</NButton>（也可手动填写镜像）</div>
    <form class="run-form" @submit.prevent="mutate('start')">
      <label for="run-image">执行镜像</label>
      <NInput v-model:value="image" :disabled="!!busy" :input-props="{ id: 'run-image', maxlength: 256 }" placeholder="请先准备 Docker 执行镜像" />
      <label for="run-setup">准备命令（可空）</label>
      <NInput v-model:value="setup" type="textarea" :disabled="!!busy" :autosize="{ minRows: 2, maxRows: 8 }" :input-props="{ id: 'run-setup', maxlength: 4000, 'aria-describedby': 'setup-risk' }" placeholder="例如安装项目依赖" />
      <p id="setup-risk" class="muted">准备阶段允许联网，并可能执行第三方依赖安装脚本。这是可信用户选择的动作，不保证防御任意网络攻击。更高隔离需求请使用预装镜像并留空准备命令；不注入宿主凭据。</p>
      <label for="run-check">检查命令（必填）</label>
      <NInput v-model:value="check" type="textarea" :disabled="!!busy" :autosize="{ minRows: 2, maxRows: 8 }" :input-props="{ id: 'run-check', maxlength: 4000 }" placeholder="例如 python -m pytest" />
      <div class="actions"><NButton attr-type="submit" type="primary" :disabled="!canStart" :loading="busy === 'start'">开始执行</NButton><span class="muted">{{ !approved ? '请先批准最新 Goal' : guarded ? '正在执行或状态尚未确认，请先等待或重新读取' : '检查通过仅表示命令退出 0，不代表独立验收' }}</span></div>
    </form>
    <div v-if="actionError" class="notice" role="alert">{{ actionError }}</div>
    <div v-if="readError" class="notice" role="alert">执行记录读取失败：{{ readError }}。保留已读内容；请显式重新读取，状态以后端为准。</div>
    <div class="actions"><h3>执行历史（最新 50 条）</h3><NButton size="small" :disabled="!!busy" :loading="loading" @click="refresh">重新读取</NButton></div>
    <p v-if="known && !runs.length" class="muted">尚无执行记录。</p>
    <ul class="run-list"><li v-for="run in runs" :key="run.id"><button type="button" :aria-pressed="selectedId === run.id" @click="select(run.id)"><strong>{{ run.cancel_requested && activeStatus(run.status) ? '正在停止' : labels[run.status] }}</strong> · Goal v{{ run.goal_version }} · {{ date(run.created_at) }}<span class="run-id">{{ run.id }}</span></button></li></ul>
    <article v-if="detail" class="run-detail" aria-label="选中执行的详情">
      <div class="actions"><h3>{{ detail.cancel_requested && activeStatus(detail.status) ? '正在停止' : labels[detail.status] }}</h3><NButton v-if="activeStatus(detail.status)" :disabled="detail.cancel_requested || !!busy" :loading="busy === 'cancel'" @click="mutate('cancel')">取消执行</NButton></div>
      <p class="muted">切换页面或退出不会取消执行。取消后等待后端确认容器停止与清理成功。</p>
      <dl><dt>Run</dt><dd>{{ detail.id }}</dd><dt>Goal</dt><dd>v{{ detail.goal_version }}</dd><dt>镜像 / ID</dt><dd>{{ detail.image }} / {{ detail.image_id || '尚未解析' }}</dd><dt>开始 / 结束</dt><dd>{{ detail.started_at ? date(detail.started_at) : '尚未开始' }} / {{ detail.finished_at ? date(detail.finished_at) : '尚未结束' }}</dd></dl>
      <p v-if="detail.error" class="notice" role="alert">{{ detail.error }}</p>
      <details><summary>本次准备与检查命令</summary><h4>准备命令</h4><pre>{{ detail.setup_command || '（空）' }}</pre><h4>检查命令</h4><pre>{{ detail.check_command }}</pre></details>
      <h4>实际检查记录</h4>
      <p v-if="!detail.checks.length" class="muted">尚无检查记录。</p>
      <section v-for="(entry, index) in detail.checks" :key="index" class="check-record"><strong>{{ entry.phase }} · 退出码 {{ entry.exit_code === null ? '未获得' : entry.exit_code }}</strong><pre>{{ entry.command }}</pre><pre>{{ entry.output || '（无输出）' }}</pre><p v-if="entry.truncated" class="notice">输出已截断</p></section>
      <details><summary>工具与执行日志（{{ detail.events.length }} 条）</summary><ol class="events"><li v-for="event in detail.events" :key="event.id"><strong>#{{ event.id }} · {{ event.kind }}</strong> · {{ date(event.created_at) }}<span v-if="event.payload.call_id" class="run-id">call_id: {{ event.payload.call_id }}</span><pre>{{ JSON.stringify(event.payload, null, 2) }}</pre></li></ol></details>
      <h4>报告</h4><pre>{{ detail.report || '尚无报告。' }}</pre>
      <h4>Patch / Diff</h4><pre class="patch">{{ detail.patch || '尚无已保存 Patch；是否无变更请以报告为准。' }}</pre>
      <div class="actions"><NButton :disabled="!detail.patch" @click="download('patch')">下载 .patch</NButton><NButton @click="download('json')">下载 .json 报告</NButton></div>
    </article>
  </section>
</template>

<style scoped>
.run-panel { min-width: 0; padding: 20px; border: 1px solid var(--border); border-radius: 12px; margin: 24px 0; background: var(--surface); overflow-wrap: anywhere; }
h2 { margin: 0 0 12px; font-size: 19px; } h3 { margin: 0; font-size: 15px; } h4 { margin: 18px 0 8px; font-size: 14px; }
p { line-height: 1.7; } .muted { color: var(--text-muted); font-size: 12px; }
.run-form { display: grid; gap: 10px; margin: 20px 0; } label { font-size: 13px; font-weight: 600; } .run-form p { margin: 0; }
.actions { display: flex; align-items: center; flex-wrap: wrap; gap: 12px; margin: 14px 0; }
.notice { padding: 12px; margin: 12px 0; background: var(--warning-bg); color: var(--warning-text); border-radius: 8px; white-space: pre-wrap; }
.run-list { list-style: none; padding: 0; display: grid; gap: 8px; }.run-list button { width: 100%; text-align: left; padding: 12px; font: inherit; font-size: 12px; border: 1px solid var(--border); border-radius: 8px; background: var(--surface); color: var(--text-secondary); cursor: pointer; overflow-wrap: anywhere; }
.run-list button[aria-pressed="true"] { border-color: var(--accent); background: var(--accent-soft); }.run-list button:focus-visible, summary:focus-visible { outline: 2px solid var(--accent); outline-offset: 3px; }
.run-id { display: block; font-size: 11px; margin-top: 5px; }.run-detail { border-top: 1px solid var(--border); margin-top: 18px; padding-top: 8px; min-width: 0; }
dl { display: grid; grid-template-columns: auto minmax(0, 1fr); gap: 8px 12px; font-size: 12px; } dt { color: var(--text-muted); } dd { margin: 0; }
pre { white-space: pre-wrap; overflow-wrap: anywhere; word-break: break-word; padding: 12px; background: var(--code-bg); border-radius: 6px; max-height: 360px; overflow-y: auto; font-size: 12px; line-height: 1.6; }
.patch { max-height: 480px; } summary { cursor: pointer; padding: 12px 0; font-size: 13px; }.events { padding-left: 20px; font-size: 12px; }.events li { margin-bottom: 16px; }.check-record { margin: 14px 0; font-size: 13px; }
@media (max-width: 600px) { .run-panel { padding: 16px; } dl { grid-template-columns: minmax(0, 1fr); } dd { margin-bottom: 8px; } }
</style>
