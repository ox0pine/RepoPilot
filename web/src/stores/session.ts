import { readonly, ref } from 'vue'

const accessToken = ref('')
export const sessionToken = readonly(accessToken)
let generation = 0

export function sessionVersion(): number {
  return generation
}

export function establishSession(token: string): void {
  generation += 1
  accessToken.value = token
}

export function clearSession(): void {
  generation += 1
  accessToken.value = ''
}
