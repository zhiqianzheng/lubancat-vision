"""Lightweight preview-side client; model runtime runs in a separate process."""
import json
import select
import mmap
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
import numpy as np
from gesture_common import HAND_CONNECTIONS

class GestureWorker(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.lock=threading.Lock();self.ready=threading.Event();self.stopping=threading.Event()
        self.frame=None;self.result=None;self.generated=0.;self.enabled=True
        self.loaded=False;self.error='';self.processed_frames=0;self.elapsed=0.;self.reset=True;self.generation=0
    def submit(self,frame):
        with self.lock:
            if not self.enabled:return
            self.frame=frame
        self.ready.set()
    def set_enabled(self,enabled):
        with self.lock:
            self.enabled=bool(enabled);self.frame=None;self.result=None;self.reset=True;self.generation+=1
        self.ready.set()
    def snapshot(self):
        with self.lock:
            result=self.result if self.enabled and time.monotonic()-self.generated<.7 else None
            return result,self.enabled,self.error
    def run(self):
        process=None;shared=None;backing=None
        try:
            backing=tempfile.TemporaryFile(dir='/dev/shm');backing.truncate(640*480*4)
            shared=mmap.mmap(backing.fileno(),640*480*4)
            process=subprocess.Popen([sys.executable,str(Path(__file__).with_name('gesture_engine.py')),str(backing.fileno())],
                stdin=subprocess.PIPE,stdout=subprocess.PIPE,universal_newlines=True,
                bufsize=1,pass_fds=(backing.fileno(),))
            def reply(timeout=5):
                if not select.select([process.stdout],[],[],timeout)[0]:
                    raise RuntimeError('手势推理超时')
                line=process.stdout.readline()
                if not line:raise RuntimeError('手势推理进程已退出')
                value=json.loads(line)
                if 'error' in value:raise RuntimeError(value['error'])
                return value
            reply(30)
            self.loaded=True
            next_run=0.
            while not self.stopping.is_set():
                if not self.ready.wait(.5):continue
                if self.stopping.wait(max(0.,next_run-time.monotonic())):break
                self.ready.clear()
                with self.lock:
                    frame,self.frame=self.frame,None
                    enabled=self.enabled;reset=self.reset;generation=self.generation
                    if frame is not None and enabled:self.reset=False
                if frame is None or not enabled:continue
                started=time.monotonic()
                shared.seek(0);shared.write(memoryview(frame).cast('B'))
                process.stdin.write(json.dumps({'reset':reset})+'\n');process.stdin.flush()
                value=reply();result=value['result']
                if result is not None:
                    result['points']=np.asarray(result['points'],np.float32)
                    result['box']=np.asarray(result['box'],np.float32)
                with self.lock:
                    if self.enabled and generation==self.generation:
                        self.result=result;self.generated=time.monotonic()
                    self.processed_frames+=1;self.elapsed=time.monotonic()-started
                next_run=started+(1. if result is None else .3)
        except Exception as exc:
            with self.lock:self.error=str(exc);self.result=None
        finally:
            if process:
                process.terminate()
                try:process.wait(timeout=2)
                except subprocess.TimeoutExpired:process.kill();process.wait()
                process.stdin.close();process.stdout.close()
            if shared:shared.close()
            if backing:backing.close()
