import type { OverviewStatusData } from './api'

/** A mounted single-account save overrides a browser's previous account. */
export function resolveMountedUid(
  status: Pick<OverviewStatusData, 'save_owner_uid' | 'default_uid'>,
  storedUid: string | null,
): number | null {
  const validUid = (value: number | null | undefined): value is number =>
    typeof value === 'number' && Number.isSafeInteger(value) && value > 0
  if (validUid(status.save_owner_uid)) return status.save_owner_uid
  if ((!storedUid || storedUid === '10001') && validUid(status.default_uid)) {
    return status.default_uid
  }
  return null
}
