<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import ModelSettingsForm from './ModelSettingsForm.vue'
import GitHubSettingsForm from './GitHubSettingsForm.vue'

const activeCategory = ref<'model' | 'github'>('model')
const githubBusy = ref(false)

const emit = defineEmits<{ close: [] }>()
const dialog = ref<HTMLDialogElement | null>(null)
onMounted(() => dialog.value?.showModal())
onBeforeUnmount(() => dialog.value?.close())
</script>

<template>
  <dialog ref="dialog" class="settings-dialog" aria-labelledby="settings-title" @cancel.prevent="!githubBusy && emit('close')">
    <header class="dialog-header">
      <h2 id="settings-title">设置</h2>
      <button class="close-button" type="button" aria-label="关闭设置" autofocus :disabled="githubBusy" @click="emit('close')">关闭</button>
    </header>
    <div class="dialog-body">
      <nav class="settings-categories" aria-label="设置分类">
        <button class="category" :class="{ active: activeCategory === 'model' }" type="button" :aria-current="activeCategory === 'model' ? 'page' : undefined" :disabled="githubBusy" @click="activeCategory = 'model'">模型提供方</button>
        <button class="category" :class="{ active: activeCategory === 'github' }" type="button" :aria-current="activeCategory === 'github' ? 'page' : undefined" :disabled="githubBusy" @click="activeCategory = 'github'">GitHub 授权</button>
      </nav>
      <div :key="activeCategory" class="settings-content">
        <ModelSettingsForm v-if="activeCategory === 'model'" />
        <GitHubSettingsForm v-else @busy="githubBusy = $event" />
      </div>
    </div>
  </dialog>
</template>

<style scoped>
.settings-dialog { width: min(960px, calc(100vw - 48px)); height: min(720px, calc(100dvh - 48px)); max-height: calc(100dvh - 48px); margin: auto; padding: 0; overflow: hidden; border: 1px solid var(--border); border-radius: 12px; background: var(--surface); color: var(--text); box-shadow: 0 24px 80px #20263133; animation: dialog-enter 180ms cubic-bezier(.2,.8,.2,1); }
.settings-dialog[open] { display: flex; flex-direction: column; }
.settings-dialog::backdrop { background: #20263100; animation: backdrop-enter 180ms ease-out forwards; }
.dialog-header { display: flex; flex-shrink: 0; justify-content: space-between; align-items: center; gap: 16px; padding: 16px 24px; border-bottom: 1px solid var(--border); }
h2 { margin: 0; font-size: 18px; }
.close-button { min-height: 36px; padding: 6px 12px; border: 1px solid var(--border); border-radius: 6px; background: var(--surface); color: var(--text-secondary); font: inherit; cursor: pointer; transition: background-color 150ms, border-color 150ms, color 150ms, transform 150ms; }
.close-button:hover { background: var(--surface-hover); transform: translateY(-1px); }
.dialog-body { display: grid; grid-template-columns: 180px minmax(0, 1fr); flex: 1; min-height: 0; overflow: hidden; }
.settings-categories { padding: 20px 12px; background: var(--bg); border-right: 1px solid var(--border); overflow-y: auto; }
.category { display: block; padding: 10px 12px; border-radius: 7px; color: var(--text-secondary); transition: background-color 150ms, color 150ms, transform 150ms; }
.category { font: inherit; border: 0; text-align: left; width: 100%; background: transparent; cursor: pointer; }
button:disabled { opacity: .55; cursor: not-allowed; }
.category:hover { background: var(--surface-hover); transform: translateX(2px); }
.category.active { background: var(--accent-soft); color: var(--accent-hover); font-weight: 600; }
.settings-content { min-width: 0; min-height: 0; padding: 24px; overflow-y: auto; overscroll-behavior: contain; scrollbar-gutter: stable; }
.settings-content :deep(.panel) { padding: 0; border: 0; border-radius: 0; }
@keyframes dialog-enter { from { opacity: 0; transform: translateY(12px) scale(.985); } to { opacity: 1; transform: translateY(0) scale(1); } }
@keyframes backdrop-enter { from { opacity: 0; } to { opacity: 1; } }
@media (max-width: 640px) {
  .settings-dialog { width: calc(100vw - 24px); height: calc(100dvh - 24px); max-height: calc(100dvh - 24px); }
  .dialog-header { padding: 12px 16px; }
  .dialog-body { grid-template-columns: 1fr; grid-template-rows: auto minmax(0, 1fr); }
  .settings-categories { padding: 12px 16px; border-right: 0; border-bottom: 1px solid var(--border); }
  .settings-content { padding: 16px; }
  .close-button { min-height: 44px; }
}
</style>
