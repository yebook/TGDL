/** Random operation identity supported by ordinary LAN HTTP deployments. */
export function operationId(): string {
  return Array.from(crypto.getRandomValues(new Uint8Array(16)), byte => byte.toString(16).padStart(2, '0')).join('')
}
