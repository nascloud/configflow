<template>
  <div
    class="stat-card"
    :style="{ '--card-color': color }"
    @click="handleClick"
  >
    <div class="card-glow"></div>
    <div class="card-content">
      <div class="icon-wrapper">
        <span class="icon">{{ icon }}</span>
      </div>
      <div class="stat-info">
        <p class="label">{{ label }}</p>
        <h2 class="value">{{ animatedValue }}</h2>
        <div class="change" v-if="change !== undefined">
          <span class="change-icon" :class="changeType">
            {{ changeIcon }}
          </span>
          <span class="change-text">{{ changeText }}</span>
        </div>
      </div>
    </div>
    <div class="card-decoration"></div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch, onMounted } from 'vue'
import { useRouter } from 'vue-router'

interface Props {
  icon: string
  label: string
  value: number
  change?: number
  color?: string
  route?: string
}

const props = withDefaults(defineProps<Props>(), {
  color: '#6B73FF',
  change: undefined,
  route: undefined
})

const router = useRouter()

// 点击跳转
const handleClick = () => {
  if (props.route) {
    router.push(props.route)
  }
}

// 数字滚动动画
const animatedValue = ref(0)

const animateValue = () => {
  const duration = 1000
  const start = 0
  const end = props.value
  const startTime = Date.now()

  const updateValue = () => {
    const now = Date.now()
    const progress = Math.min((now - startTime) / duration, 1)
    const easeProgress = 1 - Math.pow(1 - progress, 3) // easeOutCubic

    animatedValue.value = Math.round(start + (end - start) * easeProgress)

    if (progress < 1) {
      requestAnimationFrame(updateValue)
    }
  }

  requestAnimationFrame(updateValue)
}

onMounted(() => {
  animateValue()
})

watch(() => props.value, () => {
  animateValue()
})

const changeType = computed(() => {
  if (props.change === undefined) return ''
  return props.change > 0 ? 'positive' : props.change < 0 ? 'negative' : 'neutral'
})

const changeIcon = computed(() => {
  if (props.change === undefined) return ''
  if (props.change > 0) return '↗'
  if (props.change < 0) return '↘'
  return '→'
})

const changeText = computed(() => {
  if (props.change === undefined) return ''
  const absChange = Math.abs(props.change)
  if (props.change === 0) return '本周无变化'
  return `本周${props.change > 0 ? '+' : ''}${props.change}`
})
</script>

<style scoped>
.stat-card {
  position: relative;
  background: rgba(255, 255, 255, 0.98);
  backdrop-filter: blur(20px);
  -webkit-backdrop-filter: blur(20px);
  border: 1px solid rgba(107, 115, 255, 0.08);
  border-radius: 20px;
  padding: 24px;
  overflow: hidden;
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
  cursor: pointer;
  box-shadow: 0 4px 20px rgba(107, 115, 255, 0.1);
}

.stat-card:hover {
  transform: translateY(-2px);
  border-color: rgba(107, 115, 255, 0.15);
  box-shadow: 0 8px 32px rgba(107, 115, 255, 0.18);
}

.stat-card:hover .card-glow {
  opacity: 0.15;
}

.card-glow {
  position: absolute;
  top: -50%;
  left: -50%;
  width: 200%;
  height: 200%;
  background: radial-gradient(
    circle,
    var(--card-color, #6B73FF) 0%,
    transparent 70%
  );
  opacity: 0;
  transition: opacity 0.3s ease;
  pointer-events: none;
}

.card-content {
  position: relative;
  z-index: 1;
  display: flex;
  align-items: flex-start;
  gap: 20px;
}

.icon-wrapper {
  flex-shrink: 0;
  width: 52px;
  height: 52px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(
    135deg,
    rgba(107, 115, 255, 0.08),
    rgba(107, 115, 255, 0.04)
  );
  border: 1px solid rgba(107, 115, 255, 0.12);
  border-radius: 14px;
  transition: all 0.3s ease;
}

.stat-card:hover .icon-wrapper {
  transform: scale(1.05);
  background: linear-gradient(
    135deg,
    rgba(107, 115, 255, 0.12),
    rgba(107, 115, 255, 0.06)
  );
  border-color: rgba(107, 115, 255, 0.2);
  box-shadow: 0 4px 12px rgba(107, 115, 255, 0.15);
}

.icon {
  font-size: 26px;
  filter: none;
}

.stat-info {
  flex: 1;
  min-width: 0;
}

.label {
  font-size: 12px;
  font-weight: 600;
  color: #7d88af;
  margin: 0 0 10px 0;
  letter-spacing: 0.4px;
  text-transform: uppercase;
}

.value {
  font-size: 30px;
  font-weight: 700;
  background: linear-gradient(135deg, #2d3748 0%, #1a202c 100%);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
  margin: 0;
  line-height: 1.2;
}

.change {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  font-weight: 500;
}

.change-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  border-radius: 50%;
  font-size: 14px;
  transition: all 0.3s ease;
}

.change-icon.positive {
  background: rgba(76, 209, 55, 0.15);
  color: #4CD137;
}

.change-icon.negative {
  background: rgba(238, 82, 83, 0.15);
  color: #EE5253;
}

.change-icon.neutral {
  background: rgba(255, 255, 255, 0.1);
  color: rgba(255, 255, 255, 0.5);
}

.change-text {
  color: rgba(255, 255, 255, 0.6);
}

.card-decoration {
  position: absolute;
  bottom: -30px;
  right: -30px;
  width: 100px;
  height: 100px;
  background: radial-gradient(
    circle at center,
    var(--card-color, #6B73FF) 0%,
    transparent 70%
  );
  opacity: 0.08;
  pointer-events: none;
}

/* 响应式 */
@media (max-width: 768px) {
  .stat-card {
    padding: 20px;
  }

  .icon-wrapper {
    width: 48px;
    height: 48px;
  }

  .icon {
    font-size: 24px;
  }

  .value {
    font-size: 28px;
  }

  .label {
    font-size: 12px;
  }

  .card-content {
    gap: 16px;
  }
}
</style>
