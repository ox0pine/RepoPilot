import { ref } from 'vue'
import { sessionToken } from '../stores/session'

export const route = ref('/')
const workspacePaths: Record<string, true | undefined> = { '/app': true, '/app/settings': true }

export function navigate(path: string, replace = false): void {
  const target = new URL(path, window.location.origin)
  if (target.origin !== window.location.origin) return
  if (target.pathname === '/app/settings') target.pathname = '/app'
  let destination = target.pathname + target.search
  if (workspacePaths[target.pathname] && !sessionToken.value) {
    destination = `/auth?redirect=${encodeURIComponent(destination)}`
  } else if (!workspacePaths[target.pathname] && target.pathname !== '/auth' && target.pathname !== '/') {
    destination = '/'
  }
  window.history[replace ? 'replaceState' : 'pushState']({}, '', destination)
  route.value = window.location.pathname
}

export function syncRoute(): void {
  navigate(window.location.pathname + window.location.search, true)
}

export function enterWorkspace(): void {
  const intended = new URLSearchParams(window.location.search).get('redirect') || '/app'
  const target = new URL(intended, window.location.origin)
  navigate(target.origin === window.location.origin && workspacePaths[target.pathname] ? target.pathname + target.search : '/app', true)
}
