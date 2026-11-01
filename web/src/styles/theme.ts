import type { GlobalThemeOverrides } from 'naive-ui'

export type ColorScheme = 'light' | 'dark'

// One cool graphite ramp plus a single cobalt accent from the logo, in both
// schemes. Semantic colors carry meaning only: statuses, checks, and diff lines.
const light: Record<string, string> = {
  bg: '#f6f7f9', surface: '#ffffff', 'surface-hover': '#eef1f5',
  accent: '#245bd6', 'accent-hover': '#1948b5', 'accent-soft': '#eaf0ff', 'on-accent': '#ffffff',
  text: '#202631', 'text-secondary': '#526071', 'text-muted': '#667181',
  border: '#d9dfe7', 'border-soft': '#e9edf2',
  'dot-success': '#22884f', 'dot-warning': '#a86f12', 'dot-danger': '#c33a3a',
  'warning-text': '#855b14', 'warning-bg': '#fff6e1',
  'success-bg': '#e8f7ee', 'success-text': '#1f6b3a',
  'danger-bg': '#fff0f0', 'danger-text': '#9b2c2c',
  'diff-add-bg': '#eef7f1', 'diff-add-mark': '#cfe9da',
  'diff-del-bg': '#fdf0f0', 'diff-del-mark': '#f4dcdc',
  'shadow-float': '0 8px 24px rgba(32, 38, 49, .08)',
  'shadow-bar': '0 -6px 16px rgba(32, 38, 49, .06)',
}

const dark: Record<string, string> = {
  bg: '#0e1116', surface: '#151a21', 'surface-hover': '#1d232c',
  accent: '#7ea6ff', 'accent-hover': '#9dbdff', 'accent-soft': '#1a2540', 'on-accent': '#0e1116',
  text: '#e7ebf1', 'text-secondary': '#a6b0be', 'text-muted': '#818c9c',
  border: '#262d37', 'border-soft': '#1e242c',
  'dot-success': '#3fbf7f', 'dot-warning': '#d99b32', 'dot-danger': '#ef6b6b',
  'warning-text': '#e6b969', 'warning-bg': '#2a2318',
  'success-bg': '#14261c', 'success-text': '#7fd0a1',
  'danger-bg': '#2b181a', 'danger-text': '#ff9d9d',
  'diff-add-bg': '#132520', 'diff-add-mark': '#1c3a2c',
  'diff-del-bg': '#2a181a', 'diff-del-mark': '#3b2123',
  'shadow-float': '0 8px 24px rgba(0, 0, 0, .55)',
  'shadow-bar': '0 -6px 16px rgba(0, 0, 0, .45)',
}

const palettes: Record<ColorScheme, Record<string, string>> = { light, dark }

export function cssVariables(scheme: ColorScheme): Record<string, string> {
  return Object.fromEntries(Object.entries(palettes[scheme]).map(([name, value]) => [`--${name}`, value]))
}

export function themeOverrides(scheme: ColorScheme): GlobalThemeOverrides {
  const color = palettes[scheme]
  return {
    common: {
      primaryColor: color.accent!, primaryColorHover: color['accent-hover']!,
      primaryColorPressed: color['accent-hover']!, primaryColorSuppl: color.accent!,
      bodyColor: color.bg!, cardColor: color.surface!, modalColor: color.surface!,
      textColorBase: color.text!, textColor1: color.text!, textColor2: color['text-secondary']!,
      textColor3: color['text-muted']!, borderColor: color.border!,
      fontFamily: 'var(--font-ui)', fontSize: '14px', lineHeight: '1.6',
      borderRadius: '8px', heightMedium: '40px',
      // Hierarchy comes from size and color, not weight.
      fontWeightStrong: '500',
    },
    Button: { heightSmall: '32px', paddingSmall: '0 12px', textColorPrimary: color['on-accent']!, textColorHoverPrimary: color['on-accent']!, textColorPressedPrimary: color['on-accent']!, textColorFocusPrimary: color['on-accent']! },
    Card: { borderRadius: '12px' },
  }
}
