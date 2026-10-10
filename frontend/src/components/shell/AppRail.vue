<template>
  <aside
    class="sticky top-0 z-10 flex h-dvh w-(--cf-rail-w) shrink-0 flex-col border-r border-border/60 bg-background/55 px-3 pt-[calc(env(safe-area-inset-top)+16px)] pb-4 backdrop-blur-xl"
  >
    <router-link
      to="/dashboard"
      class="group mb-4 flex items-center gap-2.5 px-2 text-foreground no-underline"
      aria-label="ConfigFlow 首页"
    >
      <BrandMark class="size-7 text-primary" :busy="busy" />
      <span class="leading-none">
        <span class="font-display block text-[19px] font-semibold tracking-[-0.01em]">ConfigFlow</span>
        <span class="mt-1 block font-mono text-[9.5px] tracking-[0.16em] text-muted-foreground uppercase">
          Proxy · Flow
        </span>
      </span>
    </router-link>

    <ProfileSwitcher variant="rail" class="mb-2" />

    <nav class="-mx-1 flex min-h-0 flex-1 flex-col gap-0.5 overflow-y-auto px-1 [scrollbar-width:none]" aria-label="主导航">
    <template v-for="group in groups" :key="group.scope">
      <div
        v-if="group.title"
        class="mt-5 mb-1.5 flex items-center gap-2 px-2.5 font-mono text-[10px] tracking-[0.14em] text-muted-foreground uppercase first:mt-1"
      >
        <!-- 作用域用色标 + 分组标题共同表意，不单靠颜色 -->
        <span class="size-1.5 shrink-0 rounded-full" :class="markClass(group.scope)" aria-hidden="true" />
        <span class="truncate">
          {{ group.title }}<span v-if="group.scope === 'profile'" class="normal-case tracking-normal"> · {{ profileName }}</span>
        </span>
        <span class="h-px flex-1 bg-border/60" aria-hidden="true" />
      </div>

      <router-link
        v-for="item in visibleItems(group)"
        :key="item.path"
        :to="item.path"
        :class="cn(
          'group relative flex min-h-9.5 items-center gap-2.5 rounded-lg px-2.5 text-[13px] font-medium text-muted-foreground no-underline transition-colors duration-200 focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none',
          activePath === item.path
            ? 'text-foreground'
            : 'hover:bg-accent/60 hover:text-foreground'
        )"
        :aria-current="activePath === item.path ? 'page' : undefined"
      >
        <!-- 选中态：共享 layoutId 的高亮块在切换时平滑滑动到新项 -->
        <Motion
          v-if="activePath === item.path"
          layout-id="rail-active"
          class="absolute inset-0 rounded-lg border border-border-strong bg-secondary"
          :transition="SPRING"
          aria-hidden="true"
        />
        <!-- 选中态除底色外再加一条左侧指示条，弱视条件下也能分辨 -->
        <span
          v-if="activePath === item.path"
          class="absolute top-1/2 -left-1 h-5 w-[3px] -translate-y-1/2 rounded-r-full bg-primary-accent shadow-[0_0_12px_var(--primary-accent)]"
          aria-hidden="true"
        />
        <component
          :is="iconOf(item.icon)"
          :class="cn(
            'relative size-4 shrink-0 transition-colors',
            activePath === item.path ? 'text-primary-accent' : 'group-hover:text-foreground'
          )"
          :stroke-width="2"
          aria-hidden="true"
        />
        <span class="relative truncate">{{ item.label }}</span>
      </router-link>
    </template>

    </nav>

    <div class="mt-3 grid gap-2 border-t border-border/60 px-1.5 pt-3 text-[11.5px] text-muted-foreground">
      <!-- 下行吞吐：会话内采样的真实曲线，没有 Agent 上报时不显示 -->
      <div v-if="downRate" class="flex items-center gap-2">
        <span>入</span>
        <Sparkline :data="downHistory" class="h-[22px] min-w-0 flex-1" />
        <b class="min-w-[62px] text-right font-mono font-medium text-foreground/80">{{ downRate }}</b>
      </div>
      <router-link
        to="/agents"
        class="flex items-center gap-2 rounded-md text-muted-foreground no-underline hover:text-foreground"
      >
        <span :class="cn('size-1.5 rounded-full', onlineCount ? 'live-dot' : 'bg-muted-foreground/60')" aria-hidden="true" />
        {{ onlineCount }} / {{ boundAgents.length }} Agent 在线
      </router-link>
      <div class="flex items-center font-mono text-[10.5px]">
        <span class="tracking-[0.08em] uppercase">{{ metaKeyLabel }}K 快速跳转</span>
        <span v-if="version" class="ml-auto">{{ version }}</span>
      </div>
    </div>
  </aside>
</template>

<script setup lang="ts">
import { Motion } from 'motion-v'
import BrandMark from '@/components/common/BrandMark.vue'
import ProfileSwitcher from '@/components/ProfileSwitcher.vue'
import Sparkline from '@/components/common/Sparkline.vue'
import { formatRate, useLive } from '@/stores/live'
import { computed } from 'vue'
import { NAV_GROUPS, type NavGroup, type NavItem, type NavScope } from '@/navigation'
import { iconOf } from '@/lib/icons'
import { SPRING } from '@/lib/motion'
import { cn } from '@/lib/utils'

const props = defineProps<{
  activePath: string
  profileName: string
  subscriptionAggregationEnabled: boolean
  version?: string
  /** 全局有任务进行中时品牌标记旋转 */
  busy?: boolean
}>()

const metaKeyLabel = /Mac|iPhone|iPad/.test(navigator.platform) ? '⌘' : 'Ctrl+'

const { throughput, downHistory, onlineCount, boundAgents } = useLive()
const downRate = computed(() => (throughput.value ? formatRate(throughput.value.down).join(' ') : ''))


const groups = NAV_GROUPS

const visibleItems = (group: NavGroup): NavItem[] =>
  group.items.filter(
    item => item.flag !== 'subscriptionAggregation' || props.subscriptionAggregationEnabled
  )

const markClass = (scope: NavScope): string =>
  scope === 'resource'
    ? 'bg-info-accent shadow-[0_0_8px_var(--info-accent)]'
    : scope === 'profile'
      ? 'bg-primary-accent shadow-[0_0_8px_var(--primary-accent)]'
      : 'bg-muted-foreground'
</script>
