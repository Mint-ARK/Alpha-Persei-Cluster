"""Retained offline queue/index/gate probes. No actual CDN or player saves."""
from contextlib import ExitStack
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch
import uuid

sys.dont_write_bytecode=True
SERVER=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(SERVER))
import cdn_proxy as cdn
import cdn_integrity as core
import cdn_transfer_control as transfers
import cdn_active_downloads as active
from cdn_download_queue import DownloadQueue
from flask import Flask
import gm_api

if len(sys.argv)>1 and sys.argv[1]=='--passive-child':
    root=Path(sys.argv[2]); gate=transfers.PriorityGate(root/'transfers')
    with gate.passive():
        (root/'child-entered').write_text('ok')
        deadline=time.monotonic()+10
        while not (root/'child-release').exists() and time.monotonic()<deadline: time.sleep(.02)
    sys.exit(0)

ROOT=SERVER.parent/'archive/active_download_chain_20261028'/('checks_'+uuid.uuid4().hex)
ROOT.mkdir(parents=True)
def audit(event,args):
    if event=='open':
        name,_,flags=args
        if isinstance(name,(str,bytes,os.PathLike)) and flags&(os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC):
            if not Path(name).resolve().is_relative_to(ROOT): raise PermissionError('non-fixture write')
    if event in ('os.remove','os.rmdir','socket.connect','socket.bind'): raise PermissionError('network/deletion forbidden')
    if event in ('os.rename','shutil.copyfile') and any(not Path(p).resolve().is_relative_to(ROOT) for p in args[:2]):
        raise PermissionError('non-fixture mutation')
sys.addaudithook(audit)


class Response:
    status=200
    def __init__(self,body,connection,block=None):
        self.body=io.BytesIO(body); self.length=len(body); self.conn=connection; self.block=block
    def getheader(self,name): return str(self.length) if name.lower()=='content-length' else None
    def read(self,size=-1):
        if self.block and self.body.tell()>0:
            entered,release=self.block
            entered.set()
            while not release.wait(.01):
                if self.conn.closed.is_set(): raise OSError('socket aborted')
        return self.body.read(min(size,2))


class Connection:
    def __init__(self): self.closed=threading.Event()
    def close(self): self.closed.set()


class QueueChecks(unittest.TestCase):
    def setUp(self):
        self.root=ROOT/self._testMethodName; self.root.mkdir()
        self.gate=transfers.PriorityGate(self.root/'transfers')
        self.stack=ExitStack(); self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(transfers,'gate',self.gate))
        self.stack.enter_context(patch.object(cdn,'DEFAULT_STATIC_DIR',str(self.root/'static')))
        import res_version_manager as versions
        self.stack.enter_context(patch.object(versions,'STATIC_RESOURCES_DIR',str(self.root/'static')))
        self.stack.enter_context(patch.object(cdn,'get_data_dir',side_effect=lambda p:str(self.root/'data'/p)))
        self.stack.enter_context(patch.object(cdn,'cache_roots',side_effect=lambda p:[str(self.root/'data'/p),str(self.root/'static'/p)]))
        self.stack.enter_context(patch.object(transfers,'recycle_partial',side_effect=lambda p:Path(p).replace(str(p)+'.recycled')))
        self.settings=active.DownloadSettings(self.root/'settings.json')
        self.stack.enter_context(patch.object(active,'settings',self.settings))
        self.queue=DownloadQueue(self.root/'queue',gate=self.gate,autostart=False)
        self.addCleanup(self.queue.close)
        self.payloads={}; self.opens=[]; self.block=None
        self.names={}
        for platform in ('pc','android','ios'):
            folder=self.root/'static'/platform; folder.mkdir(parents=True)
            (self.root/'data'/platform).mkdir(parents=True)
            entries=[]
            for name,body in [('core.ys',b'CORE'),('chapter.ys',b'CHAPTER')]:
                digest=hashlib.md5(body).hexdigest(); self.payloads[digest+'.ys']=body
                entries.append(f'{name}|{digest}|{len(body)}')
            raw=json.dumps(dict(appVersion=307,buildCode=321,versionName='v5.3.1',assetHashList=entries,
                assetPendDict=dict(disable=False,pendDict=dict(chapter=['chapter.ys'])))).encode()
            filename='assethash_307_321_'+hashlib.md5(raw).hexdigest()+'.bytes'
            (folder/filename).write_bytes(raw); self.names[platform]=filename
        def opener(_host,path,*_args,**_kwargs):
            def open_response(*_):
                self.opens.append(path)
                connection=Connection()
                return connection,Response(self.payloads[path.rsplit('/',1)[-1]],connection,self.block)
            return open_response
        self.stack.enter_context(patch.object(cdn,'_upstream_opener',side_effect=opener))

    def add(self,scope='all',download=True,platform='pc'):
        result=self.queue.enqueue(dict(platform=platform,filename=self.names[platform],scope=scope,download=download))
        return result['task_id']
    def job(self,ident): return next(j for j in self.queue.status()['tasks'] if j['id']==ident)
    def test_01_scopes_and_analysis_do_not_download(self):
        for scope,count in [('required',1),('extended',1),('all',2)]:
            ident=self.add(scope,False); self.queue.run_once()
            job=self.job(ident)
            self.assertEqual(job['state'],'ready'); self.assertEqual(job['stats']['total_files'],count)
        self.assertEqual(self.opens,[])
    def test_02_indexed_execution_does_not_recheck_cache(self):
        ident=self.add()
        original=self.queue._execute
        def execute(items,token):
            with patch.object(core,'valid_file',side_effect=AssertionError('download repeated existence/hash validation')):
                return original(items,token)
        with patch.object(self.queue,'_execute',side_effect=execute): self.queue.run_once()
        self.assertEqual(self.job(ident)['state'],'completed'); self.assertEqual(len(self.opens),2)
    def test_03_composite_index_deduplicates_and_speed_counts_once(self):
        a=self.add(); b=self.add(); self.queue.settings(dict(parallel_tasks=2))
        self.queue.run_once()
        self.assertEqual(len(self.opens),2)
        jobs=[self.job(a),self.job(b)]
        self.assertTrue(all(j['state']=='completed' and j['stats']['done_files']==2 for j in jobs))
        self.assertEqual(sum(j['wire_bytes'] for j in jobs),sum(map(len,self.payloads.values())))
        self.assertEqual(self.queue.status()['total_speed_bps'],sum(j['speed_bps'] for j in jobs))
    def test_04_passive_waiting_blocks_index_generation(self):
        ident=self.add(); called=threading.Event()
        original=self.queue.index.build
        def build(*args,**kwargs): called.set(); return original(*args,**kwargs)
        with patch.object(self.queue.index,'build',side_effect=build):
            with self.gate.passive():
                worker=threading.Thread(target=self.queue.run_once); worker.start()
                self.assertFalse(called.wait(.2)); self.assertEqual(self.job(ident)['state'],'waiting_passive')
            worker.join(3)
        self.assertFalse(worker.is_alive()); self.assertTrue(called.is_set())
    def test_05_cancel_single_running_job_aborts_and_recycles(self):
        self.settings.save(1)
        entered,release=threading.Event(),threading.Event(); self.block=(entered,release)
        ident=self.add(); worker=threading.Thread(target=self.queue.run_once); worker.start()
        self.assertTrue(entered.wait(3)); self.queue.action('cancel',ident); worker.join(3)
        self.assertFalse(worker.is_alive()); self.assertEqual(self.job(ident)['state'],'cancelled')
        self.assertFalse(list((self.root/'data').rglob('*.part')))
        self.assertTrue(list((self.root/'data').rglob('*.recycled')))
    def test_06_cancelling_subscriber_does_not_abort_shared_object(self):
        self.settings.save(1); self.queue.settings(dict(parallel_tasks=2))
        entered,release=threading.Event(),threading.Event(); self.block=(entered,release)
        a=self.add(); b=self.add(); worker=threading.Thread(target=self.queue.run_once); worker.start()
        self.assertTrue(entered.wait(3)); self.queue.action('cancel',a)
        self.assertFalse(next(iter(self.queue._objects.values()))['token'].is_set())
        release.set(); worker.join(3)
        self.assertFalse(worker.is_alive()); self.assertEqual(self.job(b)['state'],'completed')
        self.assertEqual(self.job(a)['state'],'cancelled'); self.assertEqual(len(self.opens),2)
    def test_07_passive_preemption_updates_cache_before_resume(self):
        self.settings.save(1)
        entered,release=threading.Event(),threading.Event(); self.block=(entered,release)
        ident=self.add(); worker=threading.Thread(target=self.queue.run_once); worker.start()
        self.assertTrue(entered.wait(3))
        item=next(iter(self.queue._objects.values())); platform,filename=item['key']
        with self.gate.passive():
            core.publish_bytes(str(self.root/'data'/platform),item['spec'],self.payloads[filename])
        worker.join(3); self.assertFalse(worker.is_alive())
        self.assertEqual(self.job(ident)['state'],'queued')
        self.block=None; self.queue.run_once()
        self.assertEqual(self.job(ident)['state'],'completed')
        self.assertEqual(self.job(ident)['stats']['cached_files'],1)
        self.assertEqual(len(self.opens),2)
    def test_08_cross_process_priority_preempts_active_socket(self):
        self.settings.save(1)
        entered,release=threading.Event(),threading.Event(); self.block=(entered,release)
        ident=self.add(); worker=threading.Thread(target=self.queue.run_once); worker.start()
        self.assertTrue(entered.wait(3))
        proc=subprocess.Popen([sys.executable,'-B',str(Path(__file__).resolve()),'--passive-child',str(self.root)],
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        deadline=time.monotonic()+5
        while not (self.root/'child-entered').exists() and time.monotonic()<deadline: time.sleep(.02)
        (self.root/'child-release').write_text('ok')
        out,errors=proc.communicate(timeout=5); worker.join(3)
        self.assertEqual(proc.returncode,0,errors.decode(errors='replace'))
        self.assertTrue((self.root/'child-entered').exists()); self.assertFalse(worker.is_alive())
        self.assertEqual(self.job(ident)['state'],'queued')
    def test_09_restart_pauses_jobs_and_reuses_immutable_snapshot(self):
        ident=self.add(); self.queue._jobs[0]['state']='downloading'; self.queue._save(); self.queue.close()
        replacement=DownloadQueue(self.root/'queue',gate=self.gate,autostart=False)
        self.addCleanup(replacement.close)
        self.assertEqual(replacement.status()['tasks'][0]['state'],'paused')
        self.assertTrue((self.root/'queue'/replacement._jobs[0]['source']['snapshot']).is_file())
        replacement.action('resume',ident); replacement.run_once()
        self.assertEqual(replacement.status()['tasks'][0]['state'],'completed')
    def test_10_bad_metadata_and_no_owner_stealing(self):
        ident=self.add()
        other=DownloadQueue(self.root/'queue',gate=self.gate,autostart=False)
        with self.assertRaises(ValueError): other.action('pause_all')
        for value in (0,9,True,'2'):
            with self.assertRaises(ValueError): self.queue.settings(dict(parallel_tasks=value))
        self.queue.action('cancel',ident); self.queue.action('remove',ident)
        self.assertEqual(self.queue.status()['tasks'],[])
    def test_11_tampered_snapshot_is_rejected(self):
        filename,body=next(iter(self.payloads.items()))
        (self.root/'data/pc'/filename).write_bytes(body)
        ident=self.add(); source=self.queue._jobs[0]['source']
        path=self.root/'queue'/source['snapshot']; data=json.loads(path.read_bytes())
        data['assetPendDict']['pendDict']['chapter'].append('missing.ys')
        raw=json.dumps(data).encode(); path.write_bytes(raw); source['sha256']=hashlib.sha256(raw).hexdigest()
        # Snapshot SHA alone cannot bypass the digest embedded in the filename.
        self.queue.run_once(); self.assertEqual(self.job(ident)['state'],'failed')
    def test_13_unresolved_refs_and_valid_cache_are_reported(self):
        path=self.root/'static/pc'/self.names['pc']; data=json.loads(path.read_bytes())
        data['assetPendDict']['pendDict']['chapter'].append('missing.ys')
        raw=json.dumps(data).encode(); name='assethash_307_321_'+hashlib.md5(raw).hexdigest()+'.bytes'
        (path.parent/name).write_bytes(raw); self.names['pc']=name
        filename,body=next(iter(self.payloads.items())); (self.root/'data/pc'/filename).write_bytes(body)
        ident=self.add(); self.queue.run_once(); job=self.job(ident)
        self.assertEqual(job['state'],'warnings'); self.assertEqual(job['stats']['unresolved_paths'],['missing.ys'])
        self.assertEqual(job['stats']['cached_files'],1); self.assertEqual(len(self.opens),1)
    def test_14_passive_readers_are_concurrent(self):
        release=threading.Event(); all_entered=threading.Event(); count=[0]; lock=threading.Lock()
        def reader():
            with self.gate.passive():
                with lock:
                    count[0]+=1
                    if count[0]==13: all_entered.set()
                release.wait(3)
        workers=[threading.Thread(target=reader) for _ in range(13)]
        for worker in workers: worker.start()
        entered=all_entered.wait(2); release.set()
        for worker in workers: worker.join(3)
        self.assertTrue(entered); self.assertEqual(self.gate.status()['passive_requests'],0)
    def test_15_fifo_and_queued_cancel_do_not_build_index(self):
        a=self.add(download=False); b=self.add(download=False); c=self.add(download=False)
        self.queue.action('cancel',a); self.queue.action('up',c)
        self.queue.run_once()
        self.assertEqual(self.job(c)['state'],'ready'); self.assertEqual(self.job(b)['state'],'queued')
        self.assertEqual(self.job(a)['stats'],{})
    def test_16_resuming_analysis_does_not_authorize_download(self):
        ident=self.add(download=False)
        self.queue.action('pause',ident); self.queue.action('resume',ident); self.queue.run_once()
        self.assertEqual(self.job(ident)['state'],'ready'); self.assertEqual(self.opens,[])
    def test_17_manifest_preparation_is_a_queue_task(self):
        ident=self.queue.enqueue(dict(platform='pc',kind='manifest',filename=self.names['pc']))['task_id']
        self.queue.run_once()
        self.assertEqual(self.job(ident)['state'],'completed')
        self.assertEqual(self.job(ident)['stats']['cached_files'],1); self.assertEqual(self.opens,[])
    def test_18_failure_does_not_abort_other_objects_and_retry_reindexes(self):
        filename,body=next(iter(self.payloads.items())); self.payloads[filename]=b'bad'
        ident=self.add(); self.queue.run_once(); job=self.job(ident)
        self.assertEqual(job['state'],'failed'); self.assertEqual(job['stats']['failed_files'],1)
        self.assertEqual(job['stats']['done_files'],1)
        self.payloads[filename]=body; self.queue.action('retry',ident); self.queue.run_once(); job=self.job(ident)
        self.assertEqual(job['state'],'completed'); self.assertEqual(job['stats']['cached_files'],1)
    def test_21_empty_or_finished_queue_cannot_latch_pause(self):
        self.queue.action('pause_all')
        self.assertFalse(self.queue.status()['paused'])
        ident=self.add(download=False); self.queue.run_once()
        self.queue.action('pause_all')
        self.assertFalse(self.queue.status()['paused'])
        self.assertEqual(self.job(ident)['state'],'ready')
        self.queue.action('cancel',ident)
        self.queue.paused=True; self.queue._save()
        self.assertFalse(self.queue.status()['paused'])
        # Historical metadata written by the old empty-queue pause action.
        metadata=json.loads((self.queue.root/'queue.json').read_text())
        metadata['paused']=True
        (self.queue.root/'queue.json').write_text(json.dumps(metadata))
        self.queue.close()
        replacement=DownloadQueue(self.queue.root,gate=self.gate,autostart=False)
        self.addCleanup(replacement.close)
        self.assertFalse(replacement.status()['paused'])
        self.queue=replacement
        next_id=self.add(download=False); self.queue.run_once()
        self.assertEqual(self.job(next_id)['state'],'ready'); self.assertEqual(self.opens,[])
    def test_22_manifest_analysis_requires_explicit_start(self):
        ident=self.queue.enqueue(dict(platform='pc',kind='manifest',filename=self.names['pc'],download=False))['task_id']
        with patch.object(self.queue,'_manifest',wraps=self.queue._manifest) as prepare:
            self.queue.run_once()
            self.assertEqual(self.job(ident)['state'],'ready'); prepare.assert_not_called()
            self.queue.action('start',ident); self.queue.run_once()
            prepare.assert_called_once()
        self.assertEqual(self.job(ident)['state'],'completed')
    def test_23_pause_with_pending_work_still_holds_new_tasks(self):
        first=self.add(download=False); self.queue.action('pause_all')
        second=self.add(download=False)
        self.assertTrue(self.queue.status()['paused']); self.assertFalse(self.queue.run_once())
        self.assertEqual(self.job(first)['state'],'paused'); self.assertEqual(self.job(second)['state'],'queued')
        self.queue.action('resume_all'); self.queue.run_once(); self.queue.run_once()
        self.assertTrue(all(self.job(ident)['state']=='ready' for ident in (first,second)))
        self.assertEqual(self.opens,[])
    def test_19_same_digest_different_platforms_stays_isolated(self):
        self.queue.settings(dict(parallel_tasks=2))
        a=self.add(platform='pc'); b=self.add(platform='ios'); self.queue.run_once()
        self.assertEqual(len(self.opens),4)
        self.assertEqual(self.job(a)['state'],'completed'); self.assertEqual(self.job(b)['state'],'completed')
        self.assertEqual(len(list((self.root/'data/pc').glob('*.ys'))),2)
        self.assertEqual(len(list((self.root/'data/ios').glob('*.ys'))),2)
    def test_20_corrupt_cache_is_repaired_with_backup(self):
        filename,body=next(iter(self.payloads.items())); path=self.root/'data/pc'/filename
        bad=b'X'*len(body); path.write_bytes(bad)
        ident=self.add(); self.queue.run_once()
        self.assertEqual(self.job(ident)['state'],'completed'); self.assertEqual(path.read_bytes(),body)
        self.assertTrue(any(p.read_bytes()==bad for p in path.parent.glob(filename+'.before_publish_*')))
    def test_12_api_get_has_no_task_side_effect_and_actions_roundtrip(self):
        import cdn_download_queue
        app=Flask(__name__); app.register_blueprint(gm_api.gm_bp,url_prefix='/api/gm')
        with patch.object(cdn_download_queue,'get_manager',return_value=self.queue):
            client=app.test_client()
            self.assertEqual(client.get('/api/gm/system/download_queue').get_json()['data']['tasks'],[])
            result=client.post('/api/gm/system/download_queue',json=dict(platform='pc',filename=self.names['pc'],download=False)).get_json()
            self.assertEqual(result['code'],0)
            ident=result['data']['task_id']
            self.queue.run_once()
            result=client.post('/api/gm/system/download_queue/action',json=dict(action='start',task_id=ident)).get_json()
            self.assertEqual(result['code'],0); self.queue.run_once()
            self.assertEqual(self.job(ident)['state'],'completed')
            report=client.get('/api/gm/system/download_queue/report/'+ident).get_json()
            self.assertEqual(report['code'],0); self.assertNotIn('snapshot',report['data']['task']['source'])
            self.assertNotEqual(client.post('/api/gm/system/download_queue',json=[]).get_json()['code'],0)


if __name__=='__main__':
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(QueueChecks))
    (ROOT/'result.json').write_text(json.dumps(dict(tests=result.testsRun,failures=len(result.failures),errors=len(result.errors))))
    print('Retained fixtures:',ROOT)
    sys.exit(not result.wasSuccessful())
