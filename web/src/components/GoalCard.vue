<script setup lang="ts">
import { computed } from 'vue'
import { NButton } from 'naive-ui'
import { ChevronDown, CircleAlert, Target } from '@lucide/vue'
import type { TaskGoal } from '../api/tasks'

const props = defineProps<{
  goal: TaskGoal
  current: boolean
  canApprove: boolean
  canRevise: boolean
  feedbackOpen: boolean
  busy: boolean
  approved: boolean
}>()
const emit = defineEmits<{ approve: []; revise: [] }>()
const sections = computed(() => [
  { title: '修改范围', items: props.goal.content.scope },
  { title: '不包含', items: props.goal.content.non_goals },
  { title: '验收标准', items: props.goal.content.acceptance_criteria },
  { title: '建议执行计划', items: props.goal.content.plan },
])
</script>

<template>
  <article class="goal-card" :aria-label="`目标版本 ${goal.version}${current ? '，当前版本' : '，历史版本'}`">
    <header class="goal-heading">
      <span class="goal-title"><Target :size="18" aria-hidden="true" />目标 v{{ goal.version }}</span>
      <span class="version-label">{{ current ? '当前版本' : '历史版本 · 不可批准' }}</span>
      <span v-if="approved" class="approved-label">已批准</span>
    </header>
    <div class="goal-content">
      <p class="draft-note">请结合来源卡中的已读文件和覆盖说明审阅；计划尚未经执行验证。</p>
      <section><h3>目标</h3><p class="goal-summary">{{ goal.content.summary }}</p></section>
      <section v-for="section in sections" :key="section.title">
        <h3>{{ section.title }}</h3>
        <ol v-if="section.title === '建议执行计划' && section.items.length"><li v-for="(item, index) in section.items" :key="index">{{ item }}</li></ol>
        <ul v-else-if="section.items.length"><li v-for="(item, index) in section.items" :key="index">{{ item }}</li></ul>
        <p v-else class="empty-section">未列出</p>
      </section>
      <section v-if="goal.content.open_questions.length" class="question-warning">
        <h3><CircleAlert :size="17" aria-hidden="true" />待确认事项</h3>
        <p>以下问题仍需确认。批准目标不代表已解决执行前置条件。</p>
        <ul><li v-for="(question, index) in goal.content.open_questions" :key="index">{{ question }}</li></ul>
      </section>
      <section v-else><h3>待确认事项</h3><p class="empty-section">无</p></section>
    </div>
    <footer v-if="current" class="goal-actions">
      <p>仅确认目标，不会执行代码。</p>
      <div class="buttons">
        <NButton v-if="canApprove" type="primary" :disabled="busy" @click="emit('approve')">批准目标</NButton>
        <NButton v-if="canRevise" :disabled="busy" :aria-expanded="feedbackOpen" aria-controls="goal-feedback-panel" @click="emit('revise')">{{ feedbackOpen ? '收起修改' : '提出修改' }}<ChevronDown :size="15" class="feedback-chevron" :class="{ expanded: feedbackOpen }" aria-hidden="true" /></NButton>
      </div>
    </footer>
  </article>
</template>

<style scoped>
.goal-card { min-width: 0; border: 1px solid var(--border); border-radius: 12px; background: var(--surface); overflow: hidden; overflow-wrap: anywhere; }
.goal-heading { display: flex; align-items: center; flex-wrap: wrap; gap: 10px; padding: 16px 20px; border-bottom: 1px solid var(--border-soft); }
.goal-title { display: inline-flex; align-items: center; gap: 8px; font-weight: 600; color: var(--accent); }
.version-label { color: var(--text-muted); font-size: 12px; }
.approved-label { border-radius: 6px; padding: 2px 8px; background: var(--success-bg); color: var(--success-text); font-size: 12px; }
.goal-content { padding: 20px; }
.draft-note { margin: 0 0 20px; color: var(--text-muted); font-size: 12px; }
section + section { margin-top: 20px; }
h3 { margin: 0 0 8px; color: var(--text); font-size: 15px; }
p { margin: 0; }
.goal-summary, li { white-space: pre-wrap; }
ul, ol { margin: 0; padding-left: 22px; }
li + li { margin-top: 6px; }
.empty-section { color: var(--text-muted); }
.question-warning { padding: 16px; border-radius: 8px; background: var(--warning-bg); color: var(--warning-text); }
.question-warning h3 { display: flex; align-items: center; gap: 8px; color: inherit; }
.question-warning p { margin-bottom: 10px; font-size: 13px; }
.goal-actions { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 12px; padding: 16px 20px; border-top: 1px solid var(--border-soft); background: var(--surface); }
.goal-actions p { color: var(--text-muted); font-size: 12px; }
.buttons { display: flex; flex-wrap: wrap; gap: 10px; }
.feedback-chevron { margin-left: 6px; }
.feedback-chevron.expanded { transform: rotate(180deg); }
@media (max-width: 480px) { .goal-heading, .goal-content, .goal-actions { padding: 16px; } }
</style>
