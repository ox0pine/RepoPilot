export function repositoryName(url: string): string {
  return url.replace('https://github.com/', '')
}
