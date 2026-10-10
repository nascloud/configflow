import { ref, watch } from 'vue'
import { useThemeStore } from '@/stores/theme'
import { usePreferences } from '@/stores/preferences'

/**
 * 图表配色：从 theme.css 的语义 token 读取，ECharts 需要具体色值而非 CSS 变量。
 * 主题切换后重新读取，图表随之换色。
 */
export interface ChartPalette {
  primary: string
  success: string
  warning: string
  danger: string
  info: string
  muted: string
}

const FALLBACK: ChartPalette = {
  primary: '#e8916f',
  success: '#a3b97f',
  warning: '#dcae86',
  danger: '#ec8676',
  info: '#8fb4dc',
  muted: '#a9a598'
}

const read = (): ChartPalette => {
  const cs = getComputedStyle(document.documentElement)
  const v = (name: string, fallback: string) => cs.getPropertyValue(name).trim() || fallback
  return {
    primary: v('--primary-accent', FALLBACK.primary),
    success: v('--success-accent', FALLBACK.success),
    warning: v('--warning-accent', FALLBACK.warning),
    danger: v('--destructive-accent', FALLBACK.danger),
    info: v('--info-accent', FALLBACK.info),
    muted: v('--muted-foreground', FALLBACK.muted)
  }
}

/** 给 #rrggbb 追加透明度；非六位十六进制原样返回 */
export const withAlpha = (color: string, alpha: number): string =>
  /^#[0-9a-f]{6}$/i.test(color)
    ? `${color}${Math.round(alpha * 255).toString(16).padStart(2, '0')}`
    : color

export const useChartPalette = () => {
  const { theme } = useThemeStore()
  const { prefs } = usePreferences()
  const palette = ref<ChartPalette>(read())
  watch([theme, () => prefs.value.accent], () => requestAnimationFrame(() => (palette.value = read())))
  return palette
}
