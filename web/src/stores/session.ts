import { readonly, ref } from 'vue'

const accessToken = ref('')
export const sessionToken = readonly(accessToken)
const generation = ref(0)

export function sessionVersion(): number {
  return generation.value
}

export function establishSession(token: string): void {
  generation.value += 1
  accessToken.value = token
}

export function clearSession(): void {
  generation.value += 1
  accessToken.value = ''
}
