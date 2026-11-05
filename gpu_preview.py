"""Standalone Mali preview prototype with the production capture/inference workload."""
import argparse
import json
import os
import statistics
import time
from pathlib import Path
from types import SimpleNamespace
from gpu_display import GpuDisplay


class RuntimeMetrics:
    def __init__(self):
        self.previous=None;self.frames=0;self.inferred=0
        self.xorg=0
        for p in Path('/proc').iterdir():
            try:
                if p.name.isdigit() and (p/'comm').read_text().strip()=='Xorg':self.xorg=int(p.name);break
            except OSError:pass

    @staticmethod
    def process(pid):
        try:
            fields=Path('/proc/{}/stat'.format(pid)).read_text().rsplit(') ',1)[1].split()
            return int(fields[11])+int(fields[12]),int(fields[21])*os.sysconf('SC_PAGE_SIZE')
        except (OSError,IndexError,ValueError):return 0,0

    def sample(self,frames,capture,inferred,submit_ms=()):
        now=time.monotonic();cpu=list(map(int,Path('/proc/stat').read_text().splitlines()[0].split()[1:9]))
        own,rss=self.process(os.getpid());xorg,_=self.process(self.xorg)
        previous=self.previous;self.previous=(now,cpu,own,xorg,frames,inferred)
        if previous is None:return None
        elapsed=now-previous[0];delta=[b-a for a,b in zip(previous[1],cpu)]
        clock=os.sysconf('SC_CLK_TCK')
        return dict(seconds=round(elapsed,2),preview_fps=round((frames-previous[4])/elapsed,1),
            capture_fps=round(capture,1),detect_fps=round((inferred-previous[5])/elapsed,1),
            system_cpu=round(100*(sum(delta)-delta[3]-delta[4])/max(1,sum(delta)),1),
            process_cpu_onecore=round(100*(own-previous[2])/clock/elapsed,1),
            xorg_cpu_onecore=round(100*(xorg-previous[3])/clock/elapsed,1),rss_mib=round(rss/1048576.,1),
            submit_ms=round(statistics.mean(submit_ms),2) if submit_ms else None)


class CameraHost:
    def __init__(self):
        self.demo=SimpleNamespace(active=False,mode='normal');self.error=''

    def camera_error(self,message):self.error='Camera: '+str(message);return False
    def object_error(self,message):self.error='Detection: '+str(message);return False
    def scan_error(self,message):self.error='QR/face: '+str(message);return False
    def camera_info(self,*args):return False
    def scan_ambiguous(self,*args):return False
    def scanned(self,*args):return False


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--duration',type=float,default=0,help='Seconds to run; zero waits for Esc')
    parser.add_argument('--fps',type=int,choices=[20,30],default=30)
    parser.add_argument('--stats',action='store_true',help='Emit one-second performance samples as JSON')
    args=parser.parse_args()
    viewer=GpuDisplay();camera=None
    try:
        # Create Mali EGL before GTK loads its separate software-GL dependencies.
        import app
        from gi.repository import GLib
        host=CameraHost();camera=app.Camera(host);host.camera=camera;camera.start()
        if args.stats:print(json.dumps(dict(vendor=viewer.vendor,renderer=viewer.renderer,
            output=[viewer.width,viewer.height],camera=[640,480],workload='YOLO + hand + QR/face',
            preview='video + GPU boxes; no interactive UI')),flush=True)
        context=GLib.MainContext.default();metrics=RuntimeMetrics();frames=0;times=[]
        started=last_report=time.monotonic();next_draw=started
        metrics.sample(0,0,0)
        while viewer.poll():
            now=time.monotonic()
            if args.duration>0 and now-started>=args.duration:break
            while context.pending():context.iteration(False)
            if host.error:raise RuntimeError(host.error)
            if not camera.is_alive():raise RuntimeError('Capture worker stopped')
            if now>=next_draw:
                with camera.frame_lock:frame,camera.latest=camera.latest,None
                if frame is not None:
                    before=time.monotonic();viewer.draw(frame,camera.objects.snapshot())
                    times.append((time.monotonic()-before)*1000);frames+=1;next_draw=before+1./args.fps
            if now-last_report>=1:
                row=metrics.sample(frames,camera.capture_fps,camera.objects.processed_frames,times)
                if row is not None and args.stats:
                    row['hand_loaded']=camera.gestures.loaded
                    row['hand_error']=camera.gestures.error
                    print(json.dumps(row),flush=True)
                times.clear();last_report=now
            time.sleep(.002)
    finally:
        if camera is not None:
            camera.stopping.set();camera.join(timeout=12)
        viewer.close()


if __name__=='__main__':main()
