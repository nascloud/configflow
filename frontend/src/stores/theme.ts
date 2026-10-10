import { computed, ref, watch } from 'vue'

/** 实际生效的主题 */
export type ThemeMode = 'dark' | 'light'
/** 用户选择：跟随系统，或固定深 / 浅色 */
export type ThemePreference = ThemeMode | 'system'

const STORAGE_KEY = 'configflow-theme'

const readStored = (): ThemePreference => {
  try {
    const value = localStorage.getItem(STORAGE_KEY)
    if (value === 'dark' || value === 'light' || value === 'system') return value
  } catch {
    // 隐私模式或禁用存储时静默回落到默认
  }
  // 没有保存过选择时跟随系统
  return 'system'
}

const darkQuery = typeof window.matchMedia === 'function' ? window.matchMedia('(prefers-color-scheme: dark)') : null
const systemTheme = ref<ThemeMode>(darkQuery && !darkQuery.matches ? 'light' : 'dark')
darkQuery?.addEventListener?.('change', event => {
  systemTheme.value = event.matches ? 'dark' : 'light'
})

const preference = ref<ThemePreference>(readStored())
const theme = computed<ThemeMode>(() => (preference.value === 'system' ? systemTheme.value : preference.value))

/* 地址栏底色跟随 theme.css 的 --background，避免颜色在两处各写一遍而走样。
 * 读不到（样式尚未就绪）时回落到与当前 token 等价的近似值。
 */
const FALLBACK_BG: Record<ThemeMode, string> = { dark: '#191817', light: '#f5f3ec' }

const apply = (mode: ThemeMode): void => {
  document.documentElement.dataset.theme = mode
  const meta = document.querySelector('meta[name="theme-color"]')
  if (!meta) return
  const bg = getComputedStyle(document.documentElement).getPropertyValue('--background').trim()
  meta.setAttribute('content', bg || FALLBACK_BG[mode])
}

apply(theme.value)
watch(theme, apply)

watch(preference, value => {
  try {
    localStorage.setItem(STORAGE_KEY, value)
  } catch {
    // 存储不可用时仅保持当前会话生效
  }
})

export const useThemeStore = () => ({
  /** 实际生效的主题（跟随系统时随系统变化） */
  theme,
  /** 用户选择的模式 */
  preference,
  /** 顶栏快捷切换：切到与当前生效主题相反的固定模式 */
  toggleTheme: (): void => {
    preference.value = theme.value === 'dark' ? 'light' : 'dark'
  },
  setTheme: (mode: ThemePreference): void => {
    preference.value = mode
  }
})
