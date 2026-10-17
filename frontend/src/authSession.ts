import type { AxiosInstance } from 'axios'

// Fresh checks are shared by navigation. A previously server-verified JWT may
// refresh in the background until its expiration; protected API requests still
// authenticate on the server and invalidate this hint immediately on a 401.
const AUTH_CACHE_MS = 60_000
let generation = 0
let observedToken: string | null | undefined
let cached: { token: string | null; allowed: boolean; until: number; verifiedUntil?: number } | undefined
let pending: { token: string | null; generation: number; promise: Promise<boolean> } | undefined

export const invalidateAuthSession = () => {
  generation++
  cached = undefined
  pending = undefined
}

export const getAuthSessionGeneration = () => generation

export const getAuthToken = (): string | null => {
  const token = localStorage.getItem('token')
  if (token !== observedToken) {
    invalidateAuthSession()
    observedToken = token
  }
  return token
}

const tokenExpiration = (token: string | null): number | undefined => {
  if (!token) return
  try {
    const payload = token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/')
    const { exp } = JSON.parse(atob(payload))
    // This is only a cache lifetime hint, never client-side JWT verification.
    if (typeof exp === 'number' && Number.isFinite(exp * 1000)) return exp * 1000
  } catch {
    // Non-JWT or malformed payloads still require server verification.
  }
}

export const checkNavigationAuth = async (client: Pick<AxiosInstance, 'get'>): Promise<boolean> => {
  const token = getAuthToken()
  const currentGeneration = generation
  const isCurrent = () => generation === currentGeneration && localStorage.getItem('token') === token
  if (cached?.token === token && cached.until > Date.now()) return cached.allowed
  // Only a successful /auth/verify establishes this grace period. Merely
  // finding authentication disabled, or decoding a token, cannot establish it.
  const canRefreshInBackground = cached?.token === token && cached.allowed
    && cached.verifiedUntil !== undefined && cached.verifiedUntil > Date.now()

  let check = pending
  if (!check || check.token !== token || check.generation !== currentGeneration) {
    const promise = (async () => {
      const { data } = await client.get('/auth/status')
      if (!data.authEnabled) return { allowed: true, verified: false }
      if (!token || !isCurrent()) return { allowed: false, verified: false }
      await client.get('/auth/verify')
      return { allowed: true, verified: true }
    })().then(({ allowed, verified }) => {
      if (isCurrent()) {
        const expires = tokenExpiration(token)
        cached = {
          token, allowed,
          until: Math.min(Date.now() + AUTH_CACHE_MS, expires ?? Infinity),
          verifiedUntil: verified ? expires : undefined,
        }
      }
      return allowed
    })
    check = { token, generation: currentGeneration, promise }
    pending = check
  }

  if (canRefreshInBackground) {
    // The API interceptor still handles a current-session 401. A temporary
    // network failure must neither block navigation nor reject unobserved.
    void check.promise.catch(() => undefined).finally(() => {
      if (pending === check) pending = undefined
    })
    return true
  }

  try {
    const allowed = await check.promise
    if (isCurrent()) return allowed
    // Logout must not be undone by a slow check. A replaced token needs its own
    // check, and an explicitly invalidated result must not repopulate the cache.
    if (token && !getAuthToken()) return false
    return checkNavigationAuth(client)
  } catch (error) {
    if (!isCurrent() && getAuthToken()) return checkNavigationAuth(client)
    throw error
  } finally {
    if (pending === check) pending = undefined
  }
}
