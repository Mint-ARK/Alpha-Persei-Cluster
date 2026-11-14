"""Persistent global download queue; execution groups share one indexed object pool.

Metadata is checkpointed on task/state changes, not rewritten per network chunk.
On process restart unfinished jobs are paused; verified cache is the durable
file-level progress. No player database is opened by this module.
"""
from collections import deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import copy
import json
import os
from pathlib import Path
import threading
import time
import uuid

import cdn_active_downloads as active
import cdn_integrity as integrity
import cdn_proxy
import cdn_transfer_control as transfers
from cdn_download_index import IndexManager, MANIFEST_NAME, SCOPES, VOICE_LANGUAGES, freeze_source

TERMINAL = {'completed', 'warnings', 'failed', 'cancelled'}
RUNNING = {'indexing', 'downloading', 'waiting_passive'}
KINDS = ('assets', 'voice', 'manifest')


class DownloadQueue:
    def __init__(self, root=None, gate=None, autostart=True):
        self.root = Path(root or transfers.STATE_ROOT)
        self.gate = gate or transfers.gate
        self.index = IndexManager(self.root)
        self.autostart = autostart
        self._condition = threading.Condition(threading.RLock())
        self._stop = threading.Event()
        self._replan = threading.Event()
        self._thread = None
        self._owner = None
        self._session = None
        self._objects = {}
        self._active_ids = []
        self._group_id = ''
        self._group_stats = {}
        self._samples = deque()
        self._jobs = []
        self.parallel_tasks = 1
        self.paused = False
        self.warning = ''
        self._load_error = False
        path = self.root/'queue.json'
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding='utf-8-sig'))
                if data.get('version') != 1 or not isinstance(data.get('tasks'), list):
                    raise ValueError('Unsupported queue metadata')
                self.parallel_tasks = self._parallel(data.get('parallel_tasks', 1))
                self.paused = bool(data.get('paused', False))
                for job in data['tasks']:
                    if not isinstance(job, dict) or not isinstance(job.get('id'), str) or job.get('kind') not in KINDS:
                        raise ValueError('Invalid queue entry')
                    integrity.platform(job['platform'])
                    if job['state'] not in TERMINAL | {'ready', 'paused'}:
                        job.update(state='paused', message='服务重启后已暂停，可继续')
                    self._jobs.append(job)
            except (OSError, ValueError, KeyError, TypeError):
                self.warning = '下载队列记录读取失败，原文件保留，请检查后再操作'
                self._load_error = True
        self._normalize_idle_pause()

    def _normalize_idle_pause(self):
        if not any(job['state'] in RUNNING | {'queued', 'paused'} for job in self._jobs):
            self.paused = False

    @staticmethod
    def _parallel(value):
        if type(value) is not int or not 1 <= value <= 8:
            raise ValueError('并行任务数须为1至8的整数')
        return value

    def _claim(self):
        if self._load_error:
            raise ValueError(self.warning)
        if self._owner is None:
            lease = transfers.FileLease(self.root/'queue-owner.lease')
            if not lease.acquire():
                lease.close()
                raise ValueError('下载队列正由另一进程管理，请使用运行中的Web接口')
            self._owner = lease

    def _save(self):
        self._normalize_idle_pause()
        integrity.publish_json(str(self.root/'queue.json'), dict(version=1,
            parallel_tasks=self.parallel_tasks, paused=self.paused, tasks=self._jobs))

    def _wake(self):
        self._condition.notify_all()
        if self.autostart and (self._thread is None or not self._thread.is_alive()):
            self._thread = threading.Thread(target=self._loop, name='cdn-queue', daemon=True)
            self._thread.start()

    def enqueue(self, data):
        if not isinstance(data, dict):
            raise ValueError('任务参数必须为JSON对象')
        platform = integrity.platform(data.get('platform'))
        scope = data.get('scope', 'all')
        kind = data.get('kind', 'assets')
        if scope not in SCOPES or kind not in KINDS:
            raise ValueError('任务类型或下载范围无效')
        languages = data.get('voice_languages', [])
        if not isinstance(languages, list) or any(lang not in VOICE_LANGUAGES for lang in languages):
            raise ValueError('语音只支持中文和日语')
        languages = [lang for lang in VOICE_LANGUAGES if lang in languages]
        if kind == 'voice' and not languages:
            raise ValueError('请选择至少一种语音')
        approved = data.get('download', kind == 'manifest')
        for_distribution = data.get('for_distribution', False)
        if type(for_distribution) is not bool or (for_distribution and kind != 'manifest'):
            raise ValueError('只有清单任务可以登记分发方案')
        if type(approved) is not bool:
            raise ValueError('请选择分析或下载')
        with self._condition:
            self._claim()
            if sum(j['state'] not in TERMINAL for j in self._jobs) >= 100:
                raise ValueError('待处理任务已达100条，请先处理现有任务')
            if len(self._jobs) >= 500:
                raise ValueError('任务记录已达500条，请先移除已完成记录')
            if kind != 'manifest':
                try:
                    source = freeze_source(self.root, platform, data.get('profile_id'), data.get('filename'), languages)
                except ValueError:
                    name=data.get('filename')
                    if not data.get('prepare_manifest') or data.get('profile_id') or not isinstance(name,str) or not MANIFEST_NAME.fullmatch(name):
                        raise
                    source=dict(platform=platform,filename=name,label=name,pending_official=True)
            else:
                name = data.get('filename')
                if not isinstance(name, str) or not MANIFEST_NAME.fullmatch(name):
                    raise ValueError('请提供官方完整主清单文件名')
                source = dict(platform=platform, filename=name, label=name)
            job = dict(id=uuid.uuid4().hex, platform=platform, scope=scope, kind=kind,
                source=source, label=source['label'], approved=approved, state='queued',
                created_at=int(time.time()), message='已加入全局队列', stats={}, wire_bytes=0)
            if languages:
                job['voice_languages'] = languages
            if for_distribution:
                job['for_distribution'] = True
            self._normalize_idle_pause()
            self._jobs.append(job)
            try:
                self._save()
            except Exception:
                self._jobs.pop(); raise
            if len(self._active_ids) < self.parallel_tasks:
                self._replan.set()
            self._wake()
            return dict(task_id=job['id'], queue=self.status())

    def settings(self, data):
        if not isinstance(data, dict):
            raise ValueError('设置必须为JSON对象')
        value = self._parallel(data.get('parallel_tasks'))
        with self._condition:
            self._claim()
            old = self.parallel_tasks
            self.parallel_tasks = value
            try:
                self._save()
            except Exception:
                self.parallel_tasks = old; raise
            self._replan.set(); self._wake()
            return self.status()

    def action(self, action, task_id=None):
        with self._condition:
            self._claim()
            if action in ('pause_all', 'resume_all'):
                self.paused = action == 'pause_all'
                for job in self._jobs:
                    if self.paused and job['state'] in RUNNING | {'queued'}:
                        job.update(state='paused', message='队列已暂停')
                    elif not self.paused and job['state'] == 'paused':
                        job.update(state='queued', message='等待调度')
                if self.paused and self._session:
                    self._session.cancel('paused')
            else:
                job = next((j for j in self._jobs if j['id'] == task_id), None)
                if not job:
                    raise ValueError('任务不存在')
                if action == 'pause' and job['state'] not in TERMINAL | {'ready'}:
                    job.update(state='paused', message='任务已暂停，完整缓存保留')
                elif action in ('resume', 'start', 'retry') and job['state'] in {'ready', 'paused', 'failed', 'warnings'}:
                    job.update(state='queued', approved=True if action=='start' else job['approved'], message='等待调度')
                elif action == 'cancel' and job['state'] not in TERMINAL:
                    job.update(state='cancelled', message='任务已取消，完整缓存保留')
                elif action == 'remove' and job['state'] in TERMINAL:
                    self._jobs.remove(job)
                elif action in ('up', 'down') and job['state'] == 'queued':
                    position = self._jobs.index(job)
                    other = position + (-1 if action == 'up' else 1)
                    if 0 <= other < len(self._jobs) and self._jobs[other]['state'] == 'queued':
                        self._jobs[position], self._jobs[other] = self._jobs[other], job
                else:
                    raise ValueError('当前任务状态不支持此操作')
                for item in self._objects.values():
                    subscribers=self._subscribers(item)
                    if not subscribers:
                        item['token'].cancel('unreferenced')
                    elif item['owner'] not in [j['id'] for j in subscribers]:
                        item['owner']=subscribers[0]['id']
                if self._session and not any(j['id'] in self._active_ids and j['state'] in RUNNING for j in self._jobs):
                    self._session.cancel('unreferenced')
            self._save(); self._wake()
            return self.status()

    def _subscribers(self, item):
        return [j for j in self._jobs if j['id'] in item['subscribers'] and j['state'] == 'downloading']

    def _received(self, owner_id, amount):
        with self._condition:
            now = time.monotonic()
            self._samples.append((now, owner_id, amount))
            job = next((j for j in self._jobs if j['id'] == owner_id), None)
            if job:
                job['wire_bytes'] += amount
            while self._samples and self._samples[0][0] < now-2:
                self._samples.popleft()

    def status(self):
        with self._condition:
            now = time.monotonic()
            speeds = {}
            for when, owner, amount in self._samples:
                if when >= now-2:
                    speeds[owner] = speeds.get(owner, 0) + amount/2
            jobs=[]
            for stored in self._jobs:
                public={**stored,'source':dict(stored['source']),'stats':dict(stored['stats'])}
                # Polling carries compact summaries; full bounded details are
                # exported only when requested, not copied every 1.5 seconds.
                for field in ('failures','unresolved_paths'):
                    if field in public['stats']:
                        public['stats'][field]=copy.deepcopy(public['stats'][field][:5])
                jobs.append(public)
            for job in jobs:
                stats = job['stats']
                total = stats.get('total_bytes', 0)
                completed = stats.get('cached_bytes', 0)+stats.get('done_bytes', 0)
                job.update(speed_bps=round(speeds.get(job['id'], 0)),
                    progress_percent=round(100*completed/total, 1) if total else 100 if job['state'] in {'completed','warnings'} else 0,
                    group_id=self._group_id if job['id'] in self._active_ids else '')
                self._hide_snapshots(job['source'])
            return dict(tasks=jobs, parallel_tasks=self.parallel_tasks, max_parallel_tasks=8,
                paused=self.paused, total_speed_bps=round(sum(speeds.values())),
                active_group_id=self._group_id, active_task_ids=list(self._active_ids),
                group_stats=dict(self._group_stats),active_files=len(self._objects),
                gate=self.gate.status(), file_concurrency=active.settings.limit(), warning=self.warning)

    def report(self, ident):
        with self._condition:
            job=next((j for j in self._jobs if j['id']==ident),None)
            if not job: raise ValueError('任务不存在')
            result=copy.deepcopy(job)
            self._hide_snapshots(result['source'])
            return dict(version=1,task=result,scope='server_shared_cache',
                failure_detail_limit=1000,unresolved_detail_limit=200,
                note='统计为去重后的文件对象；跨任务共享对象的网络流量只归属实际接收任务。')

    @staticmethod
    def _hide_snapshots(source):
        source.pop('snapshot', None)
        if 'voice_package' in source:
            source['voice_package'] = {k: v for k, v in source['voice_package'].items() if k != 'snapshot'}
            source['voice_hashes'] = {lang: {k: v for k, v in voice.items() if k != 'snapshot'}
                                      for lang, voice in source['voice_hashes'].items()}

    def _loop(self):
        try:
            while not self._stop.is_set():
                if not self.run_once():
                    with self._condition:
                        self._condition.wait(.2)
        except Exception as error:
            with self._condition:
                self.warning = '下载调度已停止：'+str(error)
                for job in self._jobs:
                    if job['state'] in RUNNING:
                        job.update(state='paused', message=self.warning)
        finally:
            if self._owner:
                self._owner.close(); self._owner = None

    def run_once(self):
        with self._condition:
            if self.paused or self._stop.is_set():
                return False
            jobs = [j for j in self._jobs if j['state'] in {'queued', 'waiting_passive'}][:self.parallel_tasks]
            if not jobs:
                return False
            self._replan.clear()
            for job in jobs:
                job.update(state='waiting_passive', message='等待主动执行权')
            self._active_ids = [j['id'] for j in jobs]
            self._group_id = uuid.uuid4().hex
            self._group_stats = {}
        session = None
        try:
            with self.gate.active(self._stop) as session:
                with self._condition:
                    self._session = session
                    jobs = [j for j in jobs if j['state'] == 'waiting_passive']
                    for job in jobs:
                        job.update(state='indexing', message='正在建立目标索引')
                assets = []
                for job in jobs:
                    integrity.checkpoint(session)
                    if job['kind'] == 'manifest':
                        if not job['approved']:
                            languages = VOICE_LANGUAGES if job.get('for_distribution') else job.get('voice_languages', [])
                            job.update(state='ready', message='等待确认开始准备清单',
                                stats=dict(total_files=2+len(languages) if languages else 1, cached_files=0, done_files=0))
                            continue
                        try:
                            self._manifest(job, session)
                        except integrity.Cancelled:
                            raise
                        except Exception as error:
                            job.update(state='failed', message=str(error))
                    else:
                        if job['source'].get('pending_official'):
                            import res_version_manager as versions
                            try:
                                versions.download_official_manifest(job['platform'],job['source']['filename'],versions.PLATFORM_CHANNEL_IDS[job['platform']])
                                languages = job.get('voice_languages', [])
                                if languages:
                                    versions.prepare_voice_manifests(job['platform'], job['source']['filename'], languages)
                                job['source']=freeze_source(self.root,job['platform'],filename=job['source']['filename'],languages=languages)
                            except integrity.Cancelled:
                                raise
                            except Exception as error:
                                job.update(state='failed',message=str(error)); continue
                        assets.append(job)
                prepared = {}
                for job in assets:
                    try:
                        prepared[job['id']] = self.index.blueprint(job)
                    except (OSError, ValueError) as error:
                        job.update(state='failed', message=str(error))
                assets = [j for j in assets if j['id'] in prepared]
                if assets:
                    def index_progress(checked, total):
                        with self._condition:
                            for job in assets:
                                if job['state'] == 'indexing':
                                    job['message'] = f'正在核对目标目录文件 {checked} / {total}（当前执行组）'

                    missing, summaries = self.index.build(assets, session, prepared, progress=index_progress)
                    with self._condition:
                        for job in assets:
                            if job['state'] != 'indexing':
                                continue
                            job['stats'] = summaries[job['id']]
                            job.update(state='downloading' if job['approved'] else 'ready',
                                message='正在下载' if job['approved'] else '分析完成，等待开始下载')
                        eligible={j['id'] for j in assets if j['state']=='downloading'}
                        union={}
                        for ident,(objects,_) in prepared.items():
                            if ident in eligible: union.update(objects)
                        needed=[item for item in missing if eligible.intersection(item['subscribers'])]
                        self._group_stats=dict(total_files=len(union),total_bytes=sum(s.size for s in union.values()),
                            cached_files=len(union)-len(needed),cached_bytes=sum(s.size for s in union.values())-sum(i['spec'].size for i in needed),
                            done_files=0,done_bytes=0,failed_files=0)
                        self._save()
                    self._execute(missing, session)
                    with self._condition:
                        for job in assets:
                            if job['state'] == 'downloading':
                                if self._replan.is_set():
                                    job.update(state='queued', message='等待重新分组')
                                else:
                                    stats=job['stats']
                                    state='failed' if stats['failed_files'] else 'warnings' if stats['unresolved_count'] else 'completed'
                                    job.update(state=state, message='部分文件失败，可重试' if state=='failed' else '已解析资源完成，存在未解析引用' if state=='warnings' else '下载范围内资源已验证完成')
        except integrity.Cancelled:
            with self._condition:
                for job in jobs:
                    if job['state'] in RUNNING:
                        job.update(state='queued' if not self._stop.is_set() and not self.paused else 'paused',
                            message='被动下载优先，恢复时更新索引' if (session and session.reason=='passive') or self.gate.passive_pending() else '任务已暂停')
        except Exception as error:
            with self._condition:
                for job in jobs:
                    if job['state'] in RUNNING:
                        job.update(state='failed', message=str(error))
        finally:
            self.index.checkpoint_receipts(force=True)
            with self._condition:
                self._session=None; self._objects={}; self._active_ids=[]; self._group_id=''
                self._save()
        return True

    def _manifest(self, job, session):
        import res_version_manager as versions
        if job['state'] != 'indexing':
            return
        job.update(state='downloading', message='正在准备官方主清单')
        session.progress=lambda count: self._received(job['id'], count)
        try:
            result=versions.download_official_manifest(job['platform'], job['source']['filename'], versions.PLATFORM_CHANNEL_IDS[job['platform']])
            integrity.checkpoint(session)
            if job['state'] != 'downloading':
                return
            valid=result['valid']
            languages = VOICE_LANGUAGES if job.get('for_distribution') else job.get('voice_languages', [])
            for_distribution = set(languages) == set(VOICE_LANGUAGES)
            voice_total = 1 + len(languages) if languages else 0
            if valid and for_distribution and not result.get('official_verified'):
                versions._check_official_manifest_target(job['platform'], job['source']['filename'])
            voice_done = 0
            if valid and languages:
                voice_done = versions.prepare_voice_manifests(job['platform'], job['source']['filename'], languages,
                                                             target_checked=for_distribution)
            integrity.checkpoint(session)
            with self._condition:
                integrity.checkpoint(session)
                if job['state'] != 'downloading':
                    return
                if valid and for_distribution:
                    versions.register_downloaded_distribution(job['platform'], job['source']['filename'])
                downloaded = int(result['downloaded']) + voice_done
                message = '清单已保存' if downloaded else '清单已存在，已跳过'
                if for_distribution:
                    message = '分发方案已就绪，可手动应用'
                job.update(state='completed' if valid else 'failed', message=message if valid else '同名文件异常，未覆盖')
                job['stats']=dict(total_files=1+voice_total,total_bytes=0,
                    cached_files=int(result['skipped'] and valid)+(voice_total-voice_done if valid else 0),
                    done_files=downloaded,
                    cached_bytes=0,done_bytes=0,failed_files=int(not valid),unresolved_count=0,failures=[],unresolved_paths=[])
        finally:
            session.progress=None

    def _execute(self, missing, session):
        pools={ident:deque() for ident in self._active_ids}
        for item in missing:
            refs=[j['id'] for j in self._subscribers(item)]
            if refs:
                pools[refs[0]].append(item)
        rotation=deque(ident for ident, values in pools.items() if values)
        pending={}
        def worker(item):
            token=item['token']
            with transfers.bind(token):
                platform,filename=item['key']
                return cdn_proxy.download_verified(platform,filename,directory=item['output_dir'],expected_size=item['spec'].size,
                    expected_md5=item['spec'].md5,cancel=token,indexed=True,
                    progress=lambda n:self._received(item['owner'],n), on_published=self.index.record_verified)
        with ThreadPoolExecutor(max_workers=active.MAX_CONCURRENCY,thread_name_prefix='cdn-object') as pool:
            try:
                while pending or rotation:
                    integrity.checkpoint(session)
                    while rotation and len(pending)<active.settings.limit() and not self._replan.is_set():
                        owner=rotation.popleft(); item=pools[owner].popleft()
                        if pools[owner]: rotation.append(owner)
                        with self._condition:
                            refs=self._subscribers(item)
                            if not refs: continue
                            item['owner']=owner if any(j['id']==owner for j in refs) else refs[0]['id']
                            item['token']=transfers.TransferToken(session); item['token']._gate=self.gate
                            self._objects[item['key']]=item
                        pending[pool.submit(worker,item)]=item
                    if not pending:
                        break
                    done,_=wait(pending,timeout=.1,return_when=FIRST_COMPLETED)
                    self.index.checkpoint_receipts()
                    for future in done:
                        item=pending.pop(future)
                        failure=None
                        try:
                            future.result()
                        except integrity.Cancelled:
                            integrity.checkpoint(session)
                            continue
                        except Exception as error:
                            integrity.checkpoint(session)
                            failure=str(error)
                        finally:
                            with self._condition:
                                self._objects.pop(item['key'],None)
                        with self._condition:
                            self._group_stats['failed_files' if failure else 'done_files']+=1
                            if not failure: self._group_stats['done_bytes']+=item['spec'].size
                            for job in self._subscribers(item):
                                stats=job['stats']
                                if failure:
                                    stats['failed_files']+=1
                                    if len(stats['failures'])<1000:
                                        stats['failures'].append(dict(filename=item['key'][1],error=failure))
                                else:
                                    stats['done_files']+=1; stats['done_bytes']+=item['spec'].size
            except BaseException:
                session.cancel(session.reason or 'group_stopped')
                for future in pending:
                    future.cancel()
                raise

    def close(self):
        self._stop.set()
        with self._condition:
            if self._session: self._session.cancel('shutdown')
            self._condition.notify_all()
        if self._thread:
            self._thread.join(35)
            if self._thread.is_alive():
                raise RuntimeError('下载线程仍在退出，队列所有权保留')
        elif self._owner:
            self._owner.close(); self._owner=None


_manager = None
_manager_lock = threading.Lock()


def get_manager():
    global _manager
    with _manager_lock:
        if _manager is None:
            _manager=DownloadQueue()
        return _manager


def close_manager():
    with _manager_lock:
        if _manager is not None:
            _manager.close()
