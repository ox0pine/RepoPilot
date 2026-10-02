import { ref } from 'vue'
import { sessionToken } from '../stores/session'

export const route = ref('/')

export function isWorkspacePath(path: string): boolean {
  return path === '/app' || path === '/app/new' || (path.startsWith('/app/tasks/') && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(path.slice('/app/tasks/'.length)))
}

export function navigate(path: string, replace = false): void {
  let target: URL
  try {
    target = new URL(path, window.location.origin)
  } catch {
    return
  }
  if (target.origin !== window.location.origin) return
  if (target.pathname === '/app/settings') target.pathname = '/app'
  let destination = target.pathname + target.search
  if (isWorkspacePath(target.pathname) && !sessionToken.value) {
    destination = `/auth?redirect=${encodeURIComponent(destination)}`
  } else if (!isWorkspacePath(target.pathname) && target.pathname !== '/auth' && target.pathname !== '/') {
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
  try {
    const target = new URL(intended, window.location.origin)
    if (target.pathname === '/app/settings') target.pathname = '/app'
    navigate(target.origin === window.location.origin && isWorkspacePath(target.pathname) ? target.pathname + target.search : '/app', true)
  } catch {
    navigate('/app', true)
  }
}

export function followLink(event: MouseEvent, path: string): void {
  if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return
  const anchor = event.currentTarget
  if (anchor instanceof HTMLAnchorElement && (anchor.hasAttribute('download') || (anchor.target && anchor.target.toLowerCase() !== '_self'))) return
  const target = new URL(path, window.location.origin)
  if (target.origin !== window.location.origin) return
  event.preventDefault()
  navigate(path)
}
