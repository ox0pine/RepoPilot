import { sessionVersion } from '../stores/session'

// One live-request gate per panel: it fences responses by session version, task
// identity and a monotonic epoch, and aborts everything the gate handed out.
export function createRequestGate(taskId: () => string) {
  const controllers = new Set<AbortController>()
  let epoch = 0
  return {
    open() {
      const controller = new AbortController()
      controllers.add(controller)
      const identity = epoch
      const version = sessionVersion()
      const id = taskId()
      return {
        controller,
        id,
        current: () => identity === epoch && version === sessionVersion() && id === taskId() && !controller.signal.aborted,
      }
    },
    reset() {
      epoch++
      for (const controller of controllers) controller.abort()
      controllers.clear()
    },
    release(controller: AbortController) {
      controllers.delete(controller)
    },
    // Epoch for freshness checks that are not tied to one request.
    version(): number {
      return epoch
    },
  }
}
