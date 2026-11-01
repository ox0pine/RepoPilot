import { computed, readonly, ref } from 'vue'
import { cssVariables, themeOverrides, type ColorScheme } from './theme'

export type ThemePreference = 'system' | 'light' | 'dark'

const STORAGE_KEY = 'repopilot.theme'
const media = window.matchMedia('(prefers-color-scheme: dark)')

function storedPreference(): ThemePreference {
  try {
    const value = localStorage.getItem(STORAGE_KEY)
    return value === 'light' || value === 'dark' ? value : 'system'
  } catch {
    // Storage can be unavailable; follow the system instead of failing.
    return 'system'
  }
}

const preference = ref<ThemePreference>(storedPreference())

function resolve(): ColorScheme {
  if (preference.value === 'system') return media.matches ? 'dark' : 'light'
  return preference.value
}

const scheme = ref<ColorScheme>(resolve())

function apply(): void {
  scheme.value = resolve()
  for (const [name, value] of Object.entries(cssVariables(scheme.value))) {
    document.documentElement.style.setProperty(name, value)
  }
  // Native controls and scrollbars follow the resolved scheme, not the OS one.
  document.documentElement.style.colorScheme = scheme.value
}

apply()
media.addEventListener('change', () => {
  if (preference.value === 'system') apply()
})

export const colorScheme = readonly(scheme)
export const themePreference = readonly(preference)
export const themeForScheme = computed(() => themeOverrides(scheme.value))

export function setThemePreference(next: ThemePreference): void {
  preference.value = next
  try {
    localStorage.setItem(STORAGE_KEY, next)
  } catch {
    // The choice still applies for this session.
  }
  apply()
}
