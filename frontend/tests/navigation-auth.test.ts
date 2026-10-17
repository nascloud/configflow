import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, h } from 'vue'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { useRoute } from 'vue-router'
import type { AxiosResponse, InternalAxiosRequestConfig } from 'axios'
import api from '@/api'
import router from '@/router'
import { checkNavigationAuth, invalidateAuthSession } from '@/authSession'
import { notify } from '@/lib/feedback'
import AppRail from '@/components/shell/AppRail.vue'

// Page rendering has separate component tests; keep the actual router guards,
// Axios instance and request/response interceptors while avoiding UI imports.
vi.mock('@/views/Login.vue', () => ({ default: { render: () => null } }))
vi.mock('@/views/Subscriptions.vue', () => ({ default: { render: () => null } }))

const originalAdapter = api.defaults.adapter
const requests: InternalAxiosRequestConfig[] = []
const response = (config: InternalAxiosRequestConfig, data: unknown = {}): AxiosResponse => ({
  config, data, status: 200, statusText: 'OK', headers: {}
})
const paths = () => requests.map(request => request.url)
const count = (path: string) => paths().filter(value => value === path).length
const page = { render: () => null }
let rail: VueWrapper | undefined
router.addRoute({ path: '/navigation-a', component: page })
router.addRoute({ path: '/navigation-b', component: page })
router.addRoute({ path: '/navigation-aggregation', component: page, meta: { requiresSubscriptionAggregation: true } })

beforeEach(async () => {
  await router.push('/login')
  localStorage.clear()
  invalidateAuthSession()
  requests.length = 0
  localStorage.setItem('token', 'current-token')
  localStorage.setItem('username', 'Test')
  api.defaults.adapter = async config => {
    requests.push(config)
    return response(config, config.url === '/auth/status' ? { authEnabled: true } : { enabled: true })
  }
  vi.spyOn(notify, 'error').mockImplementation(() => 0)
  vi.spyOn(notify, 'loading').mockImplementation(() => 'navigation-loading')
  vi.spyOn(notify, 'dismiss').mockImplementation(() => undefined)
  vi.spyOn(console, 'error').mockImplementation(() => undefined)
})

afterEach(async () => {
  rail?.unmount()
  rail = undefined
  await router.push('/login')
  api.defaults.adapter = originalAdapter
  localStorage.clear()
  invalidateAuthSession()
  vi.restoreAllMocks()
  vi.useRealTimers()
})

const jwt = (expires = 4600, signature = 'signature') => `header.${btoa(JSON.stringify({ exp: expires }))}.${signature}`
const deferred = <T,>() => {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail })
  return { promise, resolve, reject }
}
async function verifiedSession(expires = 4600) {
  const now = vi.spyOn(Date, 'now').mockReturnValue(1_000_000)
  localStorage.setItem('token', jwt(expires))
  await router.push('/navigation-a')
  now.mockReturnValue(1_061_000)
  return now
}

describe('navigation authentication with the real router and API interceptors', () => {
  it('checks the initial session once and switches subsequent pages without auth round trips', async () => {
    await router.push('/navigation-a')
    await router.push('/navigation-b')
    expect(router.currentRoute.value.path).toBe('/navigation-b')
    expect(paths()).toEqual(['/auth/status', '/auth/verify'])
  })

  it('shares a pending check across rapid navigation and keeps the last destination', async () => {
    let finish!: () => void
    api.defaults.adapter = config => {
      requests.push(config)
      if (config.url === '/auth/status') return new Promise(resolve => {
        finish = () => resolve(response(config, { authEnabled: true }))
      })
      return Promise.resolve(response(config))
    }
    const first = router.push('/navigation-a')
    await flushPromises()
    const second = router.push('/navigation-b')
    await flushPromises()
    expect(paths()).toEqual(['/auth/status'])
    finish()
    await Promise.all([first, second])
    expect(paths()).toEqual(['/auth/status', '/auth/verify'])
    expect(router.currentRoute.value.path).toBe('/navigation-b')
  })

  it('checks a changed token instead of reusing the previous session', async () => {
    await router.push('/navigation-a')
    localStorage.setItem('token', 'replacement-token')
    await router.push('/navigation-b')
    expect(count('/auth/verify')).toBe(2)
    expect(requests.at(-1)?.headers.get('Authorization')).toBe('Bearer replacement-token')
  })

  it('rechecks after the one-minute cache lifetime', async () => {
    const now = vi.spyOn(Date, 'now').mockReturnValue(1_000_000)
    await router.push('/navigation-a')
    now.mockReturnValue(1_060_001)
    await router.push('/navigation-b')
    expect(count('/auth/status')).toBe(2)
    expect(count('/auth/verify')).toBe(2)
  })

  it('switches on the first rail click after idle while the same verified JWT refreshes in the background', async () => {
    await verifiedSession()
    const status = deferred<AxiosResponse>()
    let statusConfig!: InternalAxiosRequestConfig
    api.defaults.adapter = config => {
      requests.push(config)
      if (config.url === '/auth/status') { statusConfig = config; return status.promise }
      return Promise.resolve(response(config))
    }
    rail = mount(defineComponent({ setup() {
      const route = useRoute()
      return () => h(AppRail, { activePath: route.path, profileName: 'Default', subscriptionAggregationEnabled: true })
    } }), { global: { plugins: [router], stubs: { Motion: true, ProfileSwitcher: true, Sparkline: true, BrandMark: true } } })

    await rail.get('a[href="/subscriptions"]').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/subscriptions')
    expect(rail.get('a[href="/subscriptions"]').attributes('aria-current')).toBe('page')
    expect(count('/auth/status')).toBe(2)
    expect(count('/auth/verify')).toBe(1)
    await router.push('/navigation-b')
    expect(count('/auth/status')).toBe(2)
    status.resolve(response(statusConfig, { authEnabled: true }))
    await flushPromises()
    expect(count('/auth/verify')).toBe(2)
    expect(router.currentRoute.value.path).toBe('/navigation-b')
    expect(notify.loading).not.toHaveBeenCalled()
  })

  it('still waits for server verification on the first navigation with a JWT', async () => {
    vi.spyOn(Date, 'now').mockReturnValue(1_000_000)
    localStorage.setItem('token', jwt())
    const verification = deferred<AxiosResponse>()
    let config!: InternalAxiosRequestConfig
    api.defaults.adapter = request => {
      if (request.url === '/auth/verify') { config = request; return verification.promise }
      return Promise.resolve(response(request, { authEnabled: true }))
    }
    const navigation = router.push('/navigation-a')
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/login')
    verification.resolve(response(config))
    await navigation
    expect(router.currentRoute.value.path).toBe('/navigation-a')
  })

  it('does not use the previous JWT after its expiration', async () => {
    await verifiedSession(1020)
    const verification = deferred<AxiosResponse>()
    let config!: InternalAxiosRequestConfig
    api.defaults.adapter = request => {
      if (request.url === '/auth/verify') { config = request; return verification.promise }
      return Promise.resolve(response(request, { authEnabled: true }))
    }
    const navigation = router.push('/navigation-b')
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/navigation-a')
    verification.reject({ config, response: { status: 401 } })
    await navigation
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/login')
    expect(localStorage.getItem('token')).toBeNull()
  })

  it('does not use the verified grace period for a replacement JWT', async () => {
    await verifiedSession()
    localStorage.setItem('token', jwt(4600, 'replacement'))
    const verification = deferred<AxiosResponse>()
    let config!: InternalAxiosRequestConfig
    api.defaults.adapter = request => {
      if (request.url === '/auth/verify') { config = request; return verification.promise }
      return Promise.resolve(response(request, { authEnabled: true }))
    }
    const navigation = router.push('/navigation-b')
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/navigation-a')
    expect(config.headers.get('Authorization')).toBe(`Bearer ${jwt(4600, 'replacement')}`)
    verification.resolve(response(config))
    await navigation
    expect(router.currentRoute.value.path).toBe('/navigation-b')
  })

  it('redirects immediately when a background verification rejects the current JWT', async () => {
    await verifiedSession()
    const verification = deferred<AxiosResponse>()
    let config!: InternalAxiosRequestConfig
    api.defaults.adapter = request => {
      if (request.url === '/auth/verify') { config = request; return verification.promise }
      return Promise.resolve(response(request, { authEnabled: true }))
    }
    await router.push('/navigation-b')
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/navigation-b')
    verification.reject({ config, response: { status: 401 } })
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/login')
    expect(localStorage.getItem('token')).toBeNull()
    expect(notify.error).toHaveBeenCalledTimes(1)
  })

  it.each([undefined, 503])('absorbs a background network/server failure and retries on the next navigation (%s)', async status => {
    await verifiedSession()
    const goodAdapter = api.defaults.adapter
    api.defaults.adapter = async config => {
      requests.push(config)
      if (config.url === '/auth/verify') throw { config, response: status ? { status } : undefined }
      return response(config, { authEnabled: true })
    }
    await router.push('/navigation-b')
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/navigation-b')
    expect(localStorage.getItem('token')).toBe(jwt())
    expect(notify.error).not.toHaveBeenCalled()
    api.defaults.adapter = goodAdapter
    await router.push('/navigation-a')
    await flushPromises()
    expect(count('/auth/verify')).toBe(3)
  })

  it('does not let a background success restore a logged-out session', async () => {
    await verifiedSession()
    const verification = deferred<AxiosResponse>()
    let config!: InternalAxiosRequestConfig
    const goodAdapter = api.defaults.adapter
    api.defaults.adapter = request => {
      requests.push(request)
      if (request.url === '/auth/verify') { config = request; return verification.promise }
      return Promise.resolve(response(request, { authEnabled: true }))
    }
    await router.push('/navigation-b')
    await flushPromises()
    localStorage.removeItem('token')
    await router.push('/login')
    verification.resolve(response(config))
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/login')
    expect(localStorage.getItem('token')).toBeNull()
    localStorage.setItem('token', jwt())
    api.defaults.adapter = goodAdapter
    await router.push('/navigation-a')
    expect(count('/auth/verify')).toBe(3)
  })

  it.each(['replacement', 'signature'])('ignores a background 401 after another login (%s token)', async signature => {
    await verifiedSession()
    const oldVerification = deferred<AxiosResponse>()
    let config!: InternalAxiosRequestConfig
    const goodAdapter = api.defaults.adapter
    api.defaults.adapter = request => {
      if (request.url === '/auth/verify') { config = request; return oldVerification.promise }
      return Promise.resolve(response(request, { authEnabled: true }))
    }
    await router.push('/navigation-b')
    await flushPromises()
    localStorage.removeItem('token')
    await router.push('/login')
    localStorage.setItem('token', jwt(4600, signature))
    api.defaults.adapter = goodAdapter
    await router.push('/navigation-a')
    oldVerification.reject({ config, response: { status: 401 } })
    await flushPromises()
    expect(localStorage.getItem('token')).toBe(jwt(4600, signature))
    expect(router.currentRoute.value.path).toBe('/navigation-a')
    expect(notify.error).not.toHaveBeenCalled()
  })

  it.each([null, jwt()])('rechecks an auth-disabled session after idle rather than treating it as a verified JWT (%s)', async token => {
    const now = vi.spyOn(Date, 'now').mockReturnValue(1_000_000)
    if (token) localStorage.setItem('token', token)
    else localStorage.removeItem('token')
    api.defaults.adapter = async config => response(config, { authEnabled: false })
    await router.push('/navigation-a')
    now.mockReturnValue(1_061_000)
    const status = deferred<AxiosResponse>()
    let config!: InternalAxiosRequestConfig
    api.defaults.adapter = request => {
      if (request.url === '/auth/status') { config = request; return status.promise }
      return Promise.reject({ config: request, response: { status: 401 } })
    }
    const navigation = router.push('/navigation-b')
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/navigation-a')
    status.resolve(response(config, { authEnabled: true }))
    await navigation
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/login')
  })

  it('never caches a JWT beyond its expiration', async () => {
    const now = vi.spyOn(Date, 'now').mockReturnValue(1_000_000)
    localStorage.setItem('token', `header.${btoa(JSON.stringify({ exp: 1010 }))}.signature`)
    await router.push('/navigation-a')
    now.mockReturnValue(1_010_001)
    await router.push('/navigation-b')
    expect(count('/auth/verify')).toBe(2)
  })

  it('redirects when authentication is enabled and no token exists', async () => {
    localStorage.removeItem('token')
    await router.push('/navigation-a')
    expect(router.currentRoute.value.path).toBe('/login')
    expect(paths()).toEqual(['/auth/status'])
  })

  it('allows and caches auth-disabled navigation without a token verification', async () => {
    localStorage.removeItem('token')
    api.defaults.adapter = async config => {
      requests.push(config)
      return response(config, { authEnabled: false })
    }
    await router.push('/navigation-a')
    await router.push('/navigation-b')
    expect(router.currentRoute.value.path).toBe('/navigation-b')
    expect(paths()).toEqual(['/auth/status'])
  })

  it.each([true, false])('retains aggregation restrictions when authEnabled is %s', async authEnabled => {
    api.defaults.adapter = async config => {
      requests.push(config)
      return response(config, config.url === '/auth/status' ? { authEnabled } : { enabled: false })
    }
    await router.push('/navigation-aggregation')
    expect(router.currentRoute.value.path).toBe('/subscriptions')
    expect(count('/settings/subscription-aggregation')).toBe(1)
    expect(count('/auth/status')).toBe(1)
  })

  it.each([true, false])('retains the saved aggregation fallback %s on temporary errors', async enabled => {
    localStorage.setItem('subscriptionAggregationEnabled', String(enabled))
    api.defaults.adapter = async config => {
      if (config.url === '/settings/subscription-aggregation') throw { config, response: { status: 503 } }
      return response(config, { authEnabled: true })
    }
    await router.push('/navigation-aggregation')
    expect(router.currentRoute.value.path).toBe(enabled ? '/navigation-aggregation' : '/subscriptions')
    expect(localStorage.getItem('token')).toBe('current-token')
  })

  it.each([undefined, 500])('preserves login and retries after a temporary verify failure (%s)', async status => {
    api.defaults.adapter = async config => {
      requests.push(config)
      if (config.url === '/auth/verify') throw { config, response: status ? { status } : undefined }
      return response(config, { authEnabled: true })
    }
    await router.push('/navigation-a')
    await router.push('/navigation-b')
    expect(localStorage.getItem('token')).toBe('current-token')
    expect(router.currentRoute.value.path).toBe('/navigation-b')
    expect(count('/auth/verify')).toBe(2)
    expect(notify.error).not.toHaveBeenCalled()
  })

  it('invalidates a cached session on a business API 401 and verifies a subsequent login', async () => {
    await router.push('/navigation-a')
    const goodAdapter = api.defaults.adapter
    api.defaults.adapter = async config => { throw { config, response: { status: 401 } } }
    await expect(api.get('/nodes')).rejects.toMatchObject({ response: { status: 401 } })
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/login')
    expect(localStorage.getItem('token')).toBeNull()
    api.defaults.adapter = goodAdapter
    localStorage.setItem('token', 'current-token')
    await router.push('/navigation-b')
    expect(count('/auth/verify')).toBe(2)
  })

  it('does not report an expired login for unauthenticated background requests', async () => {
    localStorage.removeItem('token')
    await router.push('/login')
    api.defaults.adapter = async config => { throw { config, response: { status: 401 } } }
    const push = vi.spyOn(router, 'push')

    await expect(api.get('/agents')).rejects.toMatchObject({ response: { status: 401 } })
    await expect(api.get('/agents')).rejects.toMatchObject({ response: { status: 401 } })
    await flushPromises()

    expect(notify.error).not.toHaveBeenCalled()
    expect(push).not.toHaveBeenCalled()
    expect(router.currentRoute.value.path).toBe('/login')
    expect(localStorage.getItem('username')).toBe('Test')
  })

  it('does not let a guest request 401 clear a login completed while it was pending', async () => {
    localStorage.removeItem('token')
    let rejectGuest!: () => void
    const goodAdapter = api.defaults.adapter
    api.defaults.adapter = config => new Promise((_resolve, reject) => {
      rejectGuest = () => reject({ config, response: { status: 401 } })
    })
    const guestRequest = api.get('/agents')

    localStorage.setItem('token', 'replacement-token')
    api.defaults.adapter = goodAdapter
    await router.push('/navigation-a')
    rejectGuest()
    await expect(guestRequest).rejects.toMatchObject({ response: { status: 401 } })

    expect(localStorage.getItem('token')).toBe('replacement-token')
    expect(router.currentRoute.value.path).toBe('/navigation-a')
    expect(notify.error).not.toHaveBeenCalled()
  })

  it('rejects invalid initial tokens without retaining a successful cache', async () => {
    api.defaults.adapter = async config => {
      if (config.url === '/auth/verify') throw { config, response: { status: 401 } }
      return response(config, { authEnabled: true })
    }
    await router.push('/navigation-a')
    expect(router.currentRoute.value.path).toBe('/login')
    expect(localStorage.getItem('token')).toBeNull()
  })

  it('does not let a stale 401 log out a replacement token', async () => {
    let rejectOld!: () => void
    const goodAdapter = api.defaults.adapter
    api.defaults.adapter = config => new Promise((_resolve, reject) => {
      rejectOld = () => reject({ config, response: { status: 401 } })
    })
    const oldRequest = api.get('/nodes')
    localStorage.setItem('token', 'replacement-token')
    api.defaults.adapter = goodAdapter
    await router.push('/navigation-a')
    rejectOld()
    await expect(oldRequest).rejects.toMatchObject({ response: { status: 401 } })
    expect(localStorage.getItem('token')).toBe('replacement-token')
    expect(router.currentRoute.value.path).toBe('/navigation-a')
    expect(notify.error).not.toHaveBeenCalled()
  })

  it('ignores a previous session 401 even if logging back in returns the same token', async () => {
    await router.push('/navigation-a')
    let rejectOld!: () => void
    const goodAdapter = api.defaults.adapter
    api.defaults.adapter = config => new Promise((_resolve, reject) => {
      rejectOld = () => reject({ config, response: { status: 401 } })
    })
    const oldRequest = api.get('/nodes')
    localStorage.removeItem('token')
    await router.push('/login')
    localStorage.setItem('token', 'current-token')
    api.defaults.adapter = goodAdapter
    await router.push('/navigation-b')
    rejectOld()
    await expect(oldRequest).rejects.toMatchObject({ response: { status: 401 } })
    expect(localStorage.getItem('token')).toBe('current-token')
    expect(router.currentRoute.value.path).toBe('/navigation-b')
  })

  it('does not restore cached authentication when logout races a slow verification', async () => {
    let finish!: () => void
    api.defaults.adapter = config => {
      requests.push(config)
      if (config.url === '/auth/verify') return new Promise(resolve => {
        finish = () => resolve(response(config))
      })
      return Promise.resolve(response(config, { authEnabled: true }))
    }
    const checking = checkNavigationAuth(api)
    await flushPromises()
    localStorage.removeItem('token')
    invalidateAuthSession()
    finish()
    expect(await checking).toBe(false)
    localStorage.setItem('token', 'current-token')
    const checkingAgain = checkNavigationAuth(api)
    await flushPromises()
    expect(count('/auth/verify')).toBe(2)
    finish()
    expect(await checkingAgain).toBe(true)
  })

  it('keeps the new route when an old navigation receives a late verification 401', async () => {
    await router.push('/navigation-b')
    invalidateAuthSession()
    let rejectOld!: () => void
    const goodAdapter = api.defaults.adapter
    api.defaults.adapter = config => {
      if (config.url === '/auth/verify') return new Promise((_resolve, reject) => {
        rejectOld = () => reject({ config, response: { status: 401 } })
      })
      return Promise.resolve(response(config, { authEnabled: true }))
    }
    const oldNavigation = router.push('/navigation-a')
    await flushPromises()
    localStorage.removeItem('token')
    await router.push('/login')
    localStorage.setItem('token', 'current-token')
    api.defaults.adapter = goodAdapter
    await router.push('/navigation-b')
    rejectOld()
    await oldNavigation
    expect(router.currentRoute.value.path).toBe('/navigation-b')
    expect(localStorage.getItem('token')).toBe('current-token')
  })
})

describe('visible navigation waits', () => {
  it('shows and dismisses the existing loading feedback when initial authentication is slow', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    const status = deferred<AxiosResponse>()
    let config!: InternalAxiosRequestConfig
    api.defaults.adapter = request => {
      if (request.url === '/auth/status') { config = request; return status.promise }
      return Promise.resolve(response(request))
    }
    const navigation = router.push('/navigation-a')
    await flushPromises()
    await vi.advanceTimersByTimeAsync(199)
    expect(notify.loading).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(1)
    expect(notify.loading).toHaveBeenCalledWith('正在打开页面…')
    status.resolve(response(config, { authEnabled: true }))
    await navigation
    expect(notify.dismiss).toHaveBeenCalledWith('navigation-loading')
  })

  it('also covers lazy page loading and clears feedback after a successful import', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    await router.push('/navigation-a')
    const imported = deferred<{ default: typeof page }>()
    const removeRoute = router.addRoute({ path: '/navigation-lazy', component: () => imported.promise })
    try {
      const navigation = router.push('/navigation-lazy')
      await flushPromises()
      await vi.advanceTimersByTimeAsync(200)
      expect(router.currentRoute.value.path).toBe('/navigation-a')
      expect(notify.loading).toHaveBeenCalledOnce()
      imported.resolve({ default: page })
      await navigation
      expect(router.currentRoute.value.path).toBe('/navigation-lazy')
      expect(notify.dismiss).toHaveBeenCalledOnce()
    } finally { removeRoute() }
  })

  it('clears feedback and explains a failed page import', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    await router.push('/navigation-a')
    const imported = deferred<{ default: typeof page }>()
    const removeRoute = router.addRoute({ path: '/navigation-failed-import', component: () => imported.promise })
    try {
      const navigation = router.push('/navigation-failed-import')
      const rejected = expect(navigation).rejects.toThrow('import failed')
      await flushPromises()
      await vi.advanceTimersByTimeAsync(200)
      imported.reject(new Error('import failed'))
      await rejected
      expect(router.currentRoute.value.path).toBe('/navigation-a')
      expect(notify.dismiss).toHaveBeenCalledOnce()
      expect(notify.error).toHaveBeenCalledWith('页面打开失败，请重试', expect.any(String))
    } finally { removeRoute() }
  })

  it('does not let an older failed navigation dismiss the newest wait or display its stale error', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    await router.push('/navigation-a')
    const oldPage = deferred<{ default: typeof page }>()
    const newPage = deferred<{ default: typeof page }>()
    const removeOld = router.addRoute({ path: '/navigation-old-import', component: () => oldPage.promise })
    const removeNew = router.addRoute({ path: '/navigation-new-import', component: () => newPage.promise })
    try {
      const first = router.push('/navigation-old-import')
      const rejected = expect(first).rejects.toThrow('old import failed')
      await flushPromises()
      await vi.advanceTimersByTimeAsync(200)
      const second = router.push('/navigation-new-import')
      await flushPromises()
      await vi.advanceTimersByTimeAsync(200)
      expect(notify.loading).toHaveBeenCalledTimes(2)
      expect(notify.dismiss).toHaveBeenCalledTimes(1)
      oldPage.reject(new Error('old import failed'))
      await rejected
      expect(notify.dismiss).toHaveBeenCalledTimes(1)
      expect(notify.error).not.toHaveBeenCalled()
      newPage.resolve({ default: page })
      await second
      expect(router.currentRoute.value.path).toBe('/navigation-new-import')
      expect(notify.dismiss).toHaveBeenCalledTimes(2)
    } finally { removeOld(); removeNew() }
  })

  it('clears a cancelled import wait when the current page is clicked again, ignoring a late import error', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    await router.push('/navigation-a')
    const imported = deferred<{ default: typeof page }>()
    const removeRoute = router.addRoute({ path: '/navigation-cancelled-import', component: () => imported.promise })
    try {
      const navigation = router.push('/navigation-cancelled-import')
      const rejected = expect(navigation).rejects.toThrow('cancelled import failed')
      await flushPromises()
      await vi.advanceTimersByTimeAsync(200)
      expect(notify.loading).toHaveBeenCalledOnce()
      await router.push('/navigation-a')
      expect(notify.dismiss).toHaveBeenCalledOnce()
      imported.reject(new Error('cancelled import failed'))
      await rejected
      expect(notify.error).not.toHaveBeenCalled()
      expect(notify.dismiss).toHaveBeenCalledOnce()
      expect(router.currentRoute.value.path).toBe('/navigation-a')
    } finally { removeRoute() }
  })

  it('keeps a new destination loading after a queued current-page click cancels the old import', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    await router.push('/navigation-a')
    const oldPage = deferred<{ default: typeof page }>()
    const newPage = deferred<{ default: typeof page }>()
    const removeOld = router.addRoute({ path: '/navigation-cancel-old', component: () => oldPage.promise })
    const removeNew = router.addRoute({ path: '/navigation-keep-new', component: () => newPage.promise })
    try {
      const first = router.push('/navigation-cancel-old')
      await flushPromises()
      await vi.advanceTimersByTimeAsync(200)
      const duplicate = router.push('/navigation-a')
      const newest = router.push('/navigation-keep-new')
      await duplicate
      await flushPromises()
      await vi.advanceTimersByTimeAsync(200)
      expect(notify.loading).toHaveBeenCalledTimes(2)
      expect(notify.dismiss).toHaveBeenCalledTimes(1)
      oldPage.resolve({ default: page })
      await first
      expect(notify.dismiss).toHaveBeenCalledTimes(1)
      newPage.resolve({ default: page })
      await newest
      expect(router.currentRoute.value.path).toBe('/navigation-keep-new')
      expect(notify.dismiss).toHaveBeenCalledTimes(2)
    } finally { removeOld(); removeNew() }
  })

  it('clears feedback when a later route guard cancels navigation', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    await router.push('/navigation-a')
    const decision = deferred<boolean>()
    const removeGuard = router.beforeResolve(to => to.path === '/navigation-b' ? decision.promise : true)
    try {
      const navigation = router.push('/navigation-b')
      await flushPromises()
      await vi.advanceTimersByTimeAsync(200)
      expect(notify.loading).toHaveBeenCalledOnce()
      decision.resolve(false)
      await navigation
      expect(router.currentRoute.value.path).toBe('/navigation-a')
      expect(notify.dismiss).toHaveBeenCalledOnce()
      expect(notify.error).not.toHaveBeenCalled()
    } finally { removeGuard() }
  })
})
