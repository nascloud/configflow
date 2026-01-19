<template>
  <div class="dashboard-page">
    <!-- 统计卡片区域 -->
    <div class="stats-grid">
      <StatCard
        v-for="stat in statsData"
        :key="stat.label"
        :icon="stat.icon"
        :label="stat.label"
        :value="stat.value"
        :color="stat.color"
        :route="stat.route"
      />
    </div>

    <!-- Agent 状态区域 -->
    <div class="agent-status-section" v-if="agents.length > 0">
      <AgentStatus :agents="agents" />
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'
import { statsApi, agentApi } from '@/api'
import { ElMessage } from 'element-plus'
import StatCard from '@/components/StatCard.vue'
import AgentStatus from '@/components/AgentStatus.vue'

interface Agent {
  id: string
  name: string
  host: string
  port: number
  service_type: string
  status: string
  config_version?: string
  last_heartbeat?: string
  version?: string
  deployment_method?: string
}

// 当前时间
const currentTime = ref('')

// 更新时间
const updateTime = () => {
  const now = new Date()
  const hours = now.getHours().toString().padStart(2, '0')
  const minutes = now.getMinutes().toString().padStart(2, '0')
  const date = now.toLocaleDateString('zh-CN', {
    year: 'numeric',
    month: 'long',
    day: 'numeric',
    weekday: 'long'
  })
  currentTime.value = `${date} ${hours}:${minutes}`
}

// 统计数据
const statsData = ref([
  {
    icon: '📝',
    label: '订阅总数',
    value: 0,
    color: '#6B73FF',
    route: '/subscriptions'
  },
  {
    icon: '🌐',
    label: '节点总数',
    value: 0,
    color: '#4ECDC4',
    route: '/nodes'
  },
  {
    icon: '🔗',
    label: '策略组',
    value: 0,
    color: '#FF6B9D',
    route: '/proxy-groups'
  },
  {
    icon: '⚡',
    label: '规则总数',
    value: 0,
    color: '#F7B731',
    route: '/rules'
  }
])

// Agent 列表
const agents = ref<Agent[]>([])

// 加载 Agent 列表
const loadAgents = async () => {
  try {
    const response = await agentApi.getAll()
    agents.value = response.data || []
  } catch (error) {
    // 静默失败，不显示错误消息
    console.error('加载 Agent 列表失败:', error)
    agents.value = []
  }
}

// 加载总览统计数据
const loadOverview = async () => {
  try {
    const response = await statsApi.getOverview()
    if (response.data.success) {
      const data = response.data.data

      // 更新统计卡片数据（顺序：订阅、节点、策略组、规则）
      statsData.value[0].value = data.subscriptions.total
      statsData.value[1].value = data.nodes.total
      statsData.value[2].value = data.proxyGroups.total
      statsData.value[3].value = data.rules.total
    }
  } catch (error) {
    console.error('加载统计数据失败:', error)
  }
}

// 加载所有数据
const loadAllData = async () => {
  // 并行加载所有数据
  await Promise.all([
    loadOverview(),
    loadAgents()
  ])
}

let timeInterval: number
let dataInterval: number

onMounted(async () => {
  updateTime()

  // 加载所有数据
  await loadAllData()

  // 定时更新时间
  timeInterval = setInterval(updateTime, 60000)

  // 定时刷新数据（每30秒）
  dataInterval = setInterval(loadAllData, 30000)
})

onUnmounted(() => {
  if (timeInterval) {
    clearInterval(timeInterval)
  }
  if (dataInterval) {
    clearInterval(dataInterval)
  }
})
</script>

<style scoped>
.dashboard-page {
  padding: 28px 32px 40px;
  background: #f5f7ff;
  min-height: calc(100vh - 64px);
}

/* 页面头部 */
.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 16px;
  margin-bottom: 28px;
}

.title-block h2 {
  margin: 0;
  font-size: 26px;
  font-weight: 700;
  background: linear-gradient(135deg, #6b7dff 0%, #5b6dff 100%);
  -webkit-background-clip: text;
  color: transparent;
}

.title-block p {
  margin: 6px 0 0;
  font-size: 14px;
  color: #7f87af;
}

/* 统计卡片网格 */
.stats-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 24px;
  margin-bottom: 32px;
}

/* Agent 状态区域 */
.agent-status-section {
  margin-bottom: 24px;
}

/* 响应式 */
@media (max-width: 768px) {
  .dashboard-page {
    padding: 24px 16px;
  }

  .title-block h2 {
    font-size: 22px;
  }

  .title-block p {
    font-size: 13px;
  }

  .stats-grid {
    grid-template-columns: 1fr;
    gap: 16px;
  }
}

@media (max-width: 480px) {
  .dashboard-page {
    padding: 16px;
  }

  .title-block h2 {
    font-size: 20px;
  }

  .stats-grid {
    grid-template-columns: 1fr;
  }
}
</style>
