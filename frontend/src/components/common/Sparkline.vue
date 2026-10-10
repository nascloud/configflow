<template>
  <canvas ref="canvas" class="block" aria-hidden="true" />
</template>

<script setup lang="ts">
import { onMounted, onUnmounted, ref, watch } from 'vue'
import { useThemeStore } from '@/stores/theme'
import { usePreferences } from '@/stores/preferences'

/**
 * 迷你趋势线：只画真实序列，少于两个点时只画一条基线，不补造数据。
 * 颜色取语义 token 名（如 --primary-accent），主题与强调色切换后自动重画。
 */
const props = withDefaults(
  defineProps<{
    data: number[]
    color?: string
    fill?: boolean
  }>(),
  { color: '--primary-accent', fill: true }
)

const canvas = ref<HTMLCanvasElement | null>(null)
const { theme } = useThemeStore()
const { prefs } = usePreferences()

const draw = () => {
  const cv = canvas.value
  if (!cv) return
  const rect = cv.getBoundingClientRect()
  if (!rect.width || !rect.height) return
  const dpr = Math.min(window.devicePixelRatio || 1, 2)
  cv.width = Math.round(rect.width * dpr)
  cv.height = Math.round(rect.height * dpr)
  const ctx = cv.getContext('2d')
  if (!ctx) return
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
  ctx.clearRect(0, 0, rect.width, rect.height)

  const cs = getComputedStyle(document.documentElement)
  const color = cs.getPropertyValue(props.color).trim() || '#d97757'
  const W = rect.width
  const H = rect.height - 2

  if (props.data.length < 2) {
    ctx.strokeStyle = cs.getPropertyValue('--border-strong').trim() || 'rgba(128,128,128,.3)'
    ctx.lineWidth = 1
    ctx.setLineDash([3, 4])
    ctx.beginPath()
    ctx.moveTo(0, H - 1)
    ctx.lineTo(W, H - 1)
    ctx.stroke()
    return
  }

  const min = Math.min(...props.data)
  const max = Math.max(...props.data)
  const xy = (v: number, i: number): [number, number] => [
    (i / (props.data.length - 1)) * W,
    H - ((v - min) / (max - min || 1)) * (H - 4) + 1
  ]

  ctx.beginPath()
  props.data.forEach((v, i) => {
    const [x, y] = xy(v, i)
    if (i) ctx.lineTo(x, y)
    else ctx.moveTo(x, y)
  })
  ctx.strokeStyle = color
  ctx.lineWidth = 1.5
  ctx.lineJoin = 'round'
  ctx.stroke()

  if (props.fill) {
    ctx.lineTo(W, H + 2)
    ctx.lineTo(0, H + 2)
    ctx.closePath()
    const grad = ctx.createLinearGradient(0, 0, 0, H)
    grad.addColorStop(0, `${color}55`)
    grad.addColorStop(1, `${color}00`)
    ctx.fillStyle = grad
    ctx.fill()
  }

  const [lx, ly] = xy(props.data[props.data.length - 1], props.data.length - 1)
  ctx.fillStyle = color
  ctx.beginPath()
  ctx.arc(lx - 2, ly, 2.5, 0, Math.PI * 2)
  ctx.fill()
}

let observer: ResizeObserver | null = null

onMounted(() => {
  draw()
  observer = new ResizeObserver(draw)
  if (canvas.value) observer.observe(canvas.value)
})

onUnmounted(() => observer?.disconnect())

watch(() => [props.data, props.color], draw, { deep: true })
watch([theme, () => prefs.value.accent], () => requestAnimationFrame(draw))
</script>
