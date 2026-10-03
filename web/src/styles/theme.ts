import type { GlobalThemeOverrides } from 'naive-ui'

const palette = {
  bg: '#f6f7f9', surface: '#ffffff', 'surface-hover': '#eef1f5',
  accent: '#245bd6', 'accent-hover': '#1948b5', 'accent-soft': '#eaf0ff',
  text: '#202631', 'text-secondary': '#526071', 'text-muted': '#667181',
  border: '#d9dfe7', 'border-soft': '#e9edf2',
  'warning-text': '#855b14', 'warning-bg': '#fff6e1',
  'success-bg': '#e8f7ee', 'success-text': '#1f6b3a',
  'danger-bg': '#fff0f0', 'danger-text': '#9b2c2c', 'code-bg': '#f0f2f5',
}

export const cssVariables: Record<string, string> = Object.fromEntries(
  Object.entries(palette).map(([name, value]) => [`--${name}`, value]),
)

export const themeOverrides: GlobalThemeOverrides = {
  common: {
    primaryColor: palette.accent, primaryColorHover: palette['accent-hover'],
    primaryColorPressed: palette['accent-hover'], primaryColorSuppl: palette.accent,
    bodyColor: palette.bg, cardColor: palette.surface, modalColor: palette.surface,
    textColorBase: palette.text, textColor1: palette.text, textColor2: palette['text-secondary'],
    textColor3: palette['text-muted'], borderColor: palette.border,
    fontFamily: 'var(--serif)', fontSize: '14px', lineHeight: '1.6',
    borderRadius: '8px', heightMedium: '40px',
  },
  Card: { borderRadius: '12px' },
}
