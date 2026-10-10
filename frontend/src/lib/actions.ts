import type { Router } from 'vue-router'

/**
 * 跨页面动作：命令面板、总览快捷操作触发后跳到目标页，由目标页执行。
 * 通过路由 query `?run=<action>` 传递，目标页执行后清掉参数，刷新不会重复触发。
 */
export type AppAction = 'speedtest' | 'generate-mihomo' | 'pull-all' | 'push-all'

const TARGET: Record<AppAction, string> = {
  speedtest: '/nodes',
  'generate-mihomo': '/generate',
  'pull-all': '/subscriptions',
  'push-all': '/agents'
}

export const ACTION_LABEL: Record<AppAction, string> = {
  speedtest: '全部节点测速',
  'generate-mihomo': '生成 Mihomo 配置',
  'pull-all': '拉取全部订阅',
  'push-all': '推送到全部 Agent'
}

export const runAction = (router: Router, action: AppAction) =>
  router.push({ path: TARGET[action], query: { run: action } })

/** 目标页挂载后调用：命中则清掉参数并返回 true；没有路由（组件单独挂载）时视为无待办 */
export const consumeAction = (router: Router | undefined, action: AppAction): boolean => {
  if (!router) return false
  const route = router.currentRoute.value
  if (route.query.run !== action) return false
  const { run: _run, ...rest } = route.query
  router.replace({ path: route.path, query: rest })
  return true
}
