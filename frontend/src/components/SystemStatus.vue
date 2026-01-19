<template>
  <div class="system-status">
    <div class="status-items">
      <!-- License 状态 -->
      <div class="status-item">
        <div class="status-label-row">
          <div class="status-label">
            <span class="status-icon" :class="licenseStatusClass">●</span>
            <span>License</span>
          </div>
          <el-tooltip
            v-if="serverStatus"
            :content="serverStatus.message"
            placement="left"
          >
            <div class="server-status-badge">
              <span
                class="status-dot"
                :class="{
                  'status-connected': serverStatus.connected,
                  'status-disconnected': !serverStatus.connected
                }"
              ></span>
            </div>
          </el-tooltip>
        </div>
        <div class="status-value">
          <el-tag :type="data.license.active ? 'success' : 'info'" size="small">
            {{ data.license.active ? `已激活 (${data.license.type})` : '未激活' }}
          </el-tag>
        </div>
      </div>

      <!-- 激活设备 -->
      <div class="status-item" v-if="data.devices">
        <div class="status-label">
          <span class="status-icon device-icon">📱</span>
          <span>激活设备</span>
        </div>
        <div class="status-value">
          <span class="device-count">{{ data.devices.active }} / {{ data.devices.max }}</span>
          <el-progress
            :percentage="devicePercentage"
            :color="progressColor"
            :show-text="false"
            style="margin-top: 8px"
          />
        </div>
      </div>

      <!-- 最后验证时间 -->
      <div class="status-item" v-if="data.license.active && data.license.last_verify">
        <div class="status-label">
          <span class="status-icon time-icon">🕐</span>
          <span>最后验证</span>
        </div>
        <div class="status-value">
          <span class="time-display">
            {{ formatLastVerify(data.license.last_verify) }}
          </span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'

interface SystemData {
  license: {
    active: boolean
    type: string
    last_verify?: string
  }
  devices?: {
    active: number
    max: number
  }
}

interface ServerStatus {
  connected: boolean
  message: string
}

interface Props {
  data: SystemData
  serverStatus?: ServerStatus
}

const props = defineProps<Props>()

const licenseStatusClass = computed(() => {
  return props.data.license.active ? 'status-active' : 'status-inactive'
})

const devicePercentage = computed(() => {
  if (!props.data.devices) return 0
  return Math.round((props.data.devices.active / props.data.devices.max) * 100)
})

const progressColor = computed(() => {
  const percentage = devicePercentage.value
  if (percentage < 60) return '#4ECDC4'
  if (percentage < 90) return '#F7B731'
  return '#FF6B9D'
})

// 格式化最后验证时间
const formatLastVerify = (timeStr: string) => {
  try {
    const date = new Date(timeStr)
    const now = new Date()
    const diff = now.getTime() - date.getTime()
    const minutes = Math.floor(diff / 60000)
    const hours = Math.floor(diff / 3600000)
    const days = Math.floor(diff / 86400000)

    if (minutes < 1) return '刚刚'
    if (minutes < 60) return `${minutes}分钟前`
    if (hours < 24) return `${hours}小时前`
    if (days < 7) return `${days}天前`

    // 超过7天显示具体日期
    return date.toLocaleString('zh-CN', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit'
    })
  } catch (e) {
    return timeStr
  }
}
</script>

<style scoped>
.system-status {
  width: 100%;
  height: 100%;
  display: flex;
  flex-direction: column;
}

.status-items {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.status-item {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding-bottom: 20px;
  border-bottom: 1px solid rgba(107, 115, 255, 0.06);
}

.status-item:last-child {
  border-bottom: none;
  padding-bottom: 0;
}

.status-label-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.status-label {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  font-weight: 600;
  color: #7d88af;
  letter-spacing: 0.2px;
}

.server-status-badge {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  background: rgba(107, 115, 255, 0.08);
  border-radius: 6px;
  cursor: pointer;
  transition: all 0.3s ease;
  flex-shrink: 0;
}

.server-status-badge:hover {
  background: rgba(107, 115, 255, 0.15);
  transform: scale(1.05);
}

.server-status-badge .status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  position: relative;
  transition: all 0.3s ease;
}

.server-status-badge .status-dot::before {
  content: '';
  position: absolute;
  top: -3px;
  left: -3px;
  right: -3px;
  bottom: -3px;
  border-radius: 50%;
  opacity: 0;
  transition: opacity 0.3s ease;
}

.server-status-badge .status-connected {
  background: #4CD137;
  box-shadow: 0 0 8px rgba(76, 209, 55, 0.4);
}

.server-status-badge .status-connected::before {
  background: rgba(76, 209, 55, 0.3);
  animation: statusPulse 2s ease-in-out infinite;
}

.server-status-badge .status-disconnected {
  background: #EE5253;
  box-shadow: 0 0 8px rgba(238, 82, 83, 0.4);
}

@keyframes statusPulse {
  0%, 100% {
    opacity: 0;
  }
  50% {
    opacity: 1;
  }
}

.status-icon {
  font-size: 12px;
}

.status-icon.status-active {
  color: #4CD137;
  animation: pulse 2s ease-in-out infinite;
}

.status-icon.status-inactive {
  color: rgba(255, 255, 255, 0.3);
}

@keyframes pulse {
  0%, 100% {
    opacity: 1;
  }
  50% {
    opacity: 0.5;
  }
}

.device-icon,
.time-icon {
  font-size: 16px;
}

.status-value {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding-left: 24px;
}

.device-count,
.time-display {
  font-size: 14px;
  color: #2d3748;
  font-weight: 500;
}

:deep(.el-progress) {
  height: 6px !important;
}

:deep(.el-progress__outer) {
  background: rgba(255, 255, 255, 0.1);
}

:deep(.el-progress-bar__inner) {
  border-radius: 3px;
}

:deep(.el-tag) {
  border: none;
  padding: 4px 12px;
  border-radius: 12px;
  font-weight: 500;
}

:deep(.el-tag.el-tag--success) {
  background: linear-gradient(135deg, rgba(72, 187, 120, 0.15) 0%, rgba(56, 161, 105, 0.15) 100%);
  color: #38a169;
}

:deep(.el-tag.el-tag--info) {
  background: linear-gradient(135deg, rgba(113, 128, 150, 0.15) 0%, rgba(74, 85, 104, 0.15) 100%);
  color: #4a5568;
}
</style>
