"""On-demand model processes; disabled workers own no model subprocess."""
import json
import mmap
import select
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
import numpy as np
from gesture_common import HAND_CONNECTIONS

class _Cancelled(Exception):pass

class _FrameProcess:
    def __init__(self,engine,owner):
        self.owner=owner;self.process=None;self.shared=None;self.backing=None
        try:
            self.backing=tempfile.TemporaryFile(dir='/dev/shm');self.backing.truncate(640*480*4)
            self.shared=mmap.mmap(self.backing.fileno(),640*480*4)
            self.process=subprocess.Popen([sys.executable,str(Path(__file__).with_name(engine)),str(self.backing.fileno())],
                stdin=subprocess.PIPE,stdout=subprocess.PIPE,universal_newlines=True,bufsize=1,pass_fds=(self.backing.fileno(),))
        except BaseException:self.close();raise
    def reply(self,timeout):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            if self.owner.stopping.is_set() or not self.owner.enabled:raise _Cancelled()
            if select.select([self.process.stdout],[],[],.1)[0]:
                line=self.process.stdout.readline()
                if not line:raise RuntimeError('识别进程已退出')
                value=json.loads(line)
                if 'error' in value:raise RuntimeError(value['error'])
                return value
        raise RuntimeError('识别进程响应超时')
    def infer(self,frame,reset):
        self.shared.seek(0);self.shared.write(memoryview(frame).cast('B'))
        self.process.stdin.write(json.dumps({'reset':reset})+chr(10));self.process.stdin.flush()
        return self.reply(6)['result']
    def close(self):
        if self.process:
            try:self.process.terminate()
            except ProcessLookupError:pass
            try:self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:self.process.kill();self.process.wait()
            for stream in (self.process.stdin,self.process.stdout):
                try:stream.close()
                except OSError:pass
            self.process=None
        if self.shared:self.shared.close();self.shared=None
        if self.backing:self.backing.close();self.backing=None

class GestureWorker(threading.Thread):
    def __init__(self,engine='gesture_engine.py',enabled=True,interval=.3,idle=1.,ttl=.7):
        super().__init__(daemon=True)
        self.engine,self.interval,self.idle,self.ttl=engine,interval,idle,ttl
        self.lock=threading.Lock();self.ready=threading.Event();self.stopping=threading.Event()
        self.frame=None;self.result=None;self.generated=0.;self.enabled=enabled
        self.loaded=False;self.error='';self.processed_frames=0;self.elapsed=0.;self.reset=True;self.generation=0
        self.process_pid=None
    def submit(self,frame):
        with self.lock:
            if not self.enabled:return
            self.frame=frame
        self.ready.set()
    def set_enabled(self,enabled):
        with self.lock:
            if self.enabled==bool(enabled) and not (enabled and self.error):return
            self.enabled=bool(enabled);self.frame=None;self.result=None;self.reset=True;self.generation+=1
            if enabled:self.error=''
            else:self.loaded=False
        self.ready.set()
    def snapshot(self):
        with self.lock:
            result=self.result if self.enabled and time.monotonic()-self.generated<self.ttl else None
            return result,self.enabled,self.error
    def run(self):
        process=None;next_run=0.
        try:
            while not self.stopping.is_set():
                self.ready.wait(.2)
                if not self.enabled:
                    if process:process.close();process=None
                    self.process_pid=None;self.loaded=False;next_run=0.;self.ready.clear();continue
                if self.stopping.wait(max(0.,next_run-time.monotonic())):break
                self.ready.clear()
                with self.lock:has_frame=self.frame is not None
                if not has_frame:continue
                try:
                    if process is None:
                        process=_FrameProcess(self.engine,self);self.process_pid=process.process.pid
                        process.reply(30);self.loaded=True
                    # Take the newest frame after model loading and rate limiting.
                    with self.lock:
                        frame,self.frame=self.frame,None;generation=self.generation
                        reset,self.reset=self.reset,False
                    if frame is None or not self.enabled:continue
                    started=time.monotonic();result=process.infer(frame,reset)
                    if result is not None:
                        result['points']=np.asarray(result['points'],np.float32)
                        result['box']=np.asarray(result['box'],np.float32)
                    with self.lock:
                        self.processed_frames+=1
                        if result is not None:
                            result['sample_id']=self.processed_frames
                            if self.engine=='gesture_engine.py':
                                result['source_frame']=frame;result['source_time']=started
                        if self.enabled and generation==self.generation:
                            self.result=result;self.generated=time.monotonic()
                        self.elapsed=time.monotonic()-started
                    next_run=started+(self.idle if result is None else self.interval)
                except _Cancelled:
                    if process:process.close();process=None
                    self.process_pid=None;self.loaded=False
                except Exception as exc:
                    with self.lock:self.error=str(exc);self.result=None;self.enabled=False;self.frame=None
                    if process:process.close();process=None
                    self.process_pid=None;self.loaded=False
        finally:
            if process:process.close()
            self.process_pid=None;self.loaded=False
