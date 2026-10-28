"""Unified PC/Android/iOS queue CLI. Running servers own the queue via HTTP."""
import argparse
import json
from pathlib import Path
import sys
import time
from urllib.error import URLError, HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def request_json(url, body=None):
    request=Request(url,data=json.dumps(body).encode() if body is not None else None,
        headers={'Content-Type':'application/json'})
    with urlopen(request,timeout=10) as response:
        result=json.load(response)
    if result.get('code') != 0:
        raise ValueError(result.get('msg','队列请求失败'))
    return result['data']


def main(argv=None, default_platform=None):
    parser=argparse.ArgumentParser(description='统一资源下载队列；默认连接本机运行中的服务端')
    parser.add_argument('--platform',choices=('pc','android','ios'),default=default_platform or 'pc')
    source=parser.add_mutually_exclusive_group()
    source.add_argument('--profile',help='清单方案短ID')
    source.add_argument('--manifest',help='本渠道完整主清单文件名')
    parser.add_argument('--scope',choices=('required','extended','all'),default='all')
    parser.add_argument('--manifest-only',action='store_true',help='仅准备官方主清单')
    parser.add_argument('--analyze',action='store_true',help='只分析，不启动资产下载')
    parser.add_argument('--standalone',action='store_true',help='无服务端时本地执行，仍遵守跨进程门控')
    parser.add_argument('--server-url',default='http://127.0.0.1')
    parser.add_argument('--workers',type=int,help='设置全局文件并发数1–128')
    args=parser.parse_args(argv)
    payload=dict(platform=args.platform,scope=args.scope,kind='manifest' if args.manifest_only else 'assets',
        profile_id=args.profile,filename=args.manifest,download=not args.analyze)
    manager=None
    try:
        import cdn_active_downloads as active
        if args.workers is not None: active.validate_concurrency(args.workers)
        if not args.manifest and not args.profile:
            if args.manifest_only: raise ValueError('仅准备清单需要指定--manifest')
            if args.standalone:
                import resource_distribution
                distribution=resource_distribution.get_status()
            else:
                distribution=request_json(args.server_url.rstrip('/')+'/api/gm/system/resource_distribution')
            payload['profile_id']=distribution['platforms'][args.platform]['effective_id']
        if args.standalone:
            from cdn_download_queue import DownloadQueue
            manager=DownloadQueue()
            if args.workers is not None: active.settings.save(args.workers)
            result=manager.enqueue(payload)
        else:
            base=args.server_url.rstrip('/')+'/api/gm/system/'
            if args.workers is not None:
                request_json(base+'resource_download_settings',dict(max_concurrency=args.workers))
            result=request_json(base+'download_queue',payload)
        task_id=result['task_id']
        print('TASK',task_id,flush=True)
        while True:
            status=manager.status() if manager else request_json(base+'download_queue')
            job=next(j for j in status['tasks'] if j['id']==task_id)
            print(f"\r{job['state']:18} {job['progress_percent']:6.1f}%  {job['speed_bps']/1024:9.1f} KiB/s",end='',flush=True)
            if job['state'] in ('completed','warnings','failed','cancelled','ready'):
                print('\n'+job['message'])
                return 1 if job['state']=='failed' else 0
            time.sleep(1)
    except KeyboardInterrupt:
        if manager: manager.action('pause_all')
        elif 'task_id' in locals():
            request_json(base+'download_queue/action',dict(action='pause',task_id=task_id))
        return 130
    except (ValueError,OSError,URLError,HTTPError) as error:
        print('\n下载入口失败:',error, file=sys.stderr)
        print('无运行中的服务端时，可显式使用--standalone。',file=sys.stderr)
        return 1
    finally:
        if manager: manager.close()


if __name__=='__main__':
    sys.exit(main())
