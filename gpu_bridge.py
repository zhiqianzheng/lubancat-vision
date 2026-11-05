"""Nonblocking, single-in-flight GPU presentation; no GL driver enters GTK."""
import json
import mmap
import os
import select
import subprocess
import sys
import tempfile
import time
from pathlib import Path
import cairo


class GpuBridge:
    def __init__(self,window):
        self.process=None;self.memory=None;self.backing=None
        self.overlay_memory=None;self.overlay_backing=None;self.surface=None;self.size=None
        self.ready=False;self.busy=False;self.sequence=0;self.renderer='';self.error=''
        self.completed=0;self.last_done=0
        self.messages=b'';self.sent_at=0.
        self.overlay_key=None
        try:
            self.backing=tempfile.TemporaryFile(dir='/dev/shm');self.backing.truncate(640*480*4)
            self.memory=mmap.mmap(self.backing.fileno(),640*480*4)
            self.overlay_backing=tempfile.TemporaryFile(dir='/dev/shm');self.overlay_backing.truncate(3840*2160*4)
            self.overlay_memory=mmap.mmap(self.overlay_backing.fileno(),3840*2160*4)
            self.process=subprocess.Popen([sys.executable,str(Path(__file__).with_name('gpu_presenter.py')),
                '--window',str(window),'--video-fd',str(self.backing.fileno()),
                '--overlay-fd',str(self.overlay_backing.fileno())],stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,universal_newlines=True,bufsize=1,
                pass_fds=(self.backing.fileno(),self.overlay_backing.fileno()))
        except BaseException:self.close();raise

    def poll(self):
        if self.process is None:return False
        if self.process.poll() is not None:
            self.error='GPU presenter exited';return False
        while select.select([self.process.stdout],[],[],0)[0]:
            chunk=os.read(self.process.stdout.fileno(),65536)
            if not chunk:self.error='GPU presenter closed its output';return False
            self.messages+=chunk
            while b'\n' in self.messages:
                line,self.messages=self.messages.split(b'\n',1)
                if not line.startswith(b'{'):continue
                message=json.loads(line)
                if message.get('ready'):
                    self.renderer=message['renderer'];self.ready=True
                if message.get('released')==self.sequence:self.busy=False
                if message.get('done',0)>self.last_done:
                    self.last_done=message['done'];self.completed+=1
            if len(self.messages)>65536:self.error='Invalid GPU response';return False
        if self.busy and time.monotonic()-self.sent_at>3:
            self.error='GPU presenter stopped responding';return False
        return True

    def submit(self,pixels,width,height,fill=True,draw_overlay=None,overlay_key=None):
        if not self.poll() or not self.ready or self.busy:return False
        if width<1 or height<1 or width*height>3840*2160:raise ValueError('Preview exceeds 4K pixel budget')
        changed=overlay_key is None or overlay_key!=self.overlay_key or self.size!=(width,height)
        if draw_overlay is not None and changed:
            if self.size!=(width,height):
                self.surface=cairo.ImageSurface.create_for_data(self.overlay_memory,cairo.FORMAT_ARGB32,width,height,width*4)
                self.size=width,height
            self.surface.flush();cr=cairo.Context(self.surface)
            cr.set_operator(cairo.OPERATOR_CLEAR);cr.paint();cr.set_operator(cairo.OPERATOR_OVER)
            draw_overlay(cr);self.surface.flush()
        self.memory.seek(0);self.memory.write(memoryview(pixels).cast('B'))
        self.sequence+=1
        self.process.stdin.write(json.dumps(dict(sequence=self.sequence,width=width,height=height,
            fill=fill,overlay=draw_overlay is not None,overlay_changed=changed))+'\n')
        self.process.stdin.flush();self.busy=True;self.sent_at=time.monotonic()
        self.overlay_key=overlay_key
        return True

    def close(self):
        process,self.process=self.process,None
        self.ready=False;self.busy=False
        if process is not None:
            try:
                if process.poll() is None:
                    process.stdin.write(json.dumps(dict(quit=True))+'\n');process.stdin.flush()
                    process.wait(timeout=2)
            except (OSError,subprocess.TimeoutExpired):
                try:process.terminate()
                except ProcessLookupError:pass
                try:process.wait(timeout=2)
                except subprocess.TimeoutExpired:process.kill();process.wait()
            finally:
                for stream in [process.stdin,process.stdout]:
                    try:
                        if stream:stream.close()
                    except OSError:pass
        if self.memory is not None:self.memory.close();self.memory=None
        if self.backing is not None:self.backing.close();self.backing=None
        self.surface=None
        if self.overlay_memory is not None:
            try:self.overlay_memory.close()
            except BufferError:pass
            self.overlay_memory=None
        if self.overlay_backing is not None:self.overlay_backing.close();self.overlay_backing=None
