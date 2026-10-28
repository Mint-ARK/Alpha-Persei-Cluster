import { useEffect, useState } from 'react'
import { RefreshCw } from 'lucide-react'
import { Button } from '@heroui/react'
import { PageHeader } from '../components/kit'
import { useServer } from '../lib/serverContext'
import { fetchResourceDistribution, type DownloadQueueHint, type ResourceDistributionData } from '../lib/api'
import ResourceManifestManager from './ResourceManifestManager'
import ResourceDownloadControls from './ResourceDownloadControls'
import ResourceDownloadQueue from './ResourceDownloadQueue'
import OfficialResourceProbe from './OfficialResourceProbe'
import ResourceStorage from './ResourceStorage'

export default function ResourcesPanel() {
  const { isOnline, resVersionInfo } = useServer()
  const [distribution, setDistribution] = useState<ResourceDistributionData | null>(null)
  const [error, setError] = useState('')
  const [hint, setHint] = useState<DownloadQueueHint | null>(null)
  const load = async () => {
    setError('')
    try {
      const result = await fetchResourceDistribution()
      if (result.code !== 0) throw new Error(result.msg)
      setDistribution(result.data)
    } catch (e: any) { setError(e.message || '读取资源分发配置失败') }
  }
  useEffect(() => { if (isOnline) void load() }, [isOnline, resVersionInfo?.current_version])
  return <div className="space-y-5">
    <PageHeader title="资源分发" description="按渠道管理清单与共享资源，下载任务统一排队。配置对本台服务器全部存档生效。"
      actions={<><ResourceStorage online={isOnline} /><Button variant="secondary" isDisabled={!isOnline} onPress={load}><RefreshCw className="size-4" />刷新配置</Button></>} />
    {error && <p role="alert" className="text-danger">{error}</p>}
    {!distribution && !error && <p className="text-muted">{isOnline ? '正在读取资源分发配置…' : '请先启动服务器'}</p>}
    {distribution && <ResourceManifestManager data={distribution} onSaved={setDistribution} disabled={!isOnline}
      downloadControls={<ResourceDownloadControls online={isOnline} progress={null} />} />}
    <ResourceDownloadQueue online={isOnline} distribution={distribution} hint={hint} />
    <OfficialResourceProbe online={isOnline} onQueue={setHint} />
  </div>
}
