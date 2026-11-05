"""Latest-frame optical flow for pinch mode; models remain the geometry reference."""
import threading
import time
import cv2
import numpy as np

class HandMotionTracker:
    def __init__(self):self.reset()
    def reset(self):
        self.gray=None;self.points=None;self.model_id=None;self.model_time=0.;self.label='';self.confidence=0.
    @staticmethod
    def gray_image(frame):return cv2.resize(cv2.cvtColor(frame,cv2.COLOR_BGRA2GRAY),(320,240))
    @staticmethod
    def flow(first,second,points):
        old=np.ascontiguousarray(points.reshape(-1,1,2),np.float32)
        options=dict(winSize=(19,19),maxLevel=2,criteria=(cv2.TERM_CRITERIA_EPS|cv2.TERM_CRITERIA_COUNT,12,.02))
        new,status,error=cv2.calcOpticalFlowPyrLK(first,second,old,None,**options)
        if new is None:return None,None
        back,reverse,_=cv2.calcOpticalFlowPyrLK(second,first,new,None,**options)
        if back is None:return None,None
        good=status.reshape(-1).astype(bool)&reverse.reshape(-1).astype(bool)
        good &= np.linalg.norm(back.reshape(-1,2)-old.reshape(-1,2),axis=1)<1.8
        good &= np.isfinite(new.reshape(-1,2)).all(axis=1)
        good &= (error.reshape(-1)<40)
        if not(good[8] and (good[4] or good[12])) or int(good.sum())<8:return None,None
        result=new.reshape(-1,2)
        shift=np.median(result[good]-old.reshape(-1,2)[good],axis=0)
        result[~good]=old.reshape(-1,2)[~good]+shift
        return result,good
    def track(self,frame,model,now):
        if model is None or model.get('source_frame') is None:
            self.reset();return None
        source_time=model.get('source_time',now)
        if now-source_time>1.:
            self.reset();return None
        gray=self.gray_image(frame);model_id=model.get('sample_id')
        changed=model_id!=self.model_id
        if changed or self.gray is None:
            source=self.gray_image(model['source_frame'])
            seed=np.asarray(model['points'],np.float32)[:,:2]*.5
            points,good=self.flow(source,gray,seed)
            if points is None:
                if self.gray is None:return None
                points,good=self.flow(self.gray,gray,self.points)
            if points is None:self.reset();return None
            self.model_id=model_id;self.model_time=source_time
            self.label=model['label'];self.confidence=model.get('confidence',0.)
        else:
            points,good=self.flow(self.gray,gray,self.points)
            if points is None:self.reset();return None
        self.gray=gray;self.points=points.copy()
        xy=points*2.
        critical=[8,4 if good[4] else 12]
        if np.any(xy[critical,0]<0) or np.any(xy[critical,0]>639) or np.any(xy[critical,1]<0) or np.any(xy[critical,1]>479):
            self.reset();return None
        full=np.asarray(model['points'],np.float32).copy();full[:,:2]=xy
        return {'points':full,'box':np.r_[xy.min(axis=0),xy.max(axis=0)],'label':self.label,
                'confidence':self.confidence,'valid_points':good.tolist(),'source_time':source_time}

class HandMotionWorker(threading.Thread):
    def __init__(self,gestures):
        super().__init__(daemon=True)
        self.gestures=gestures;self.lock=threading.Lock();self.ready=threading.Event();self.stopping=threading.Event()
        self.enabled=False;self.frame=None;self.result=None;self.generated=0.;self.generation=0
        self.processed_frames=0;self.elapsed=0.;self.error=''
    def set_enabled(self,enabled):
        with self.lock:
            if self.enabled==bool(enabled):return
            self.enabled=bool(enabled);self.frame=None;self.result=None;self.generation+=1;self.error=''
        self.ready.set()
    def submit(self,frame):
        with self.lock:
            if not self.enabled:return
            self.frame=frame
        self.ready.set()
    def snapshot(self):
        with self.lock:return self.result if self.enabled and time.monotonic()-self.generated<.18 else None
    def run(self):
        tracker=HandMotionTracker();epoch=-1;next_run=0.
        while not self.stopping.is_set():
            self.ready.wait(.2)
            if self.stopping.wait(max(0.,next_run-time.monotonic())):break
            self.ready.clear()
            with self.lock:frame,self.frame=self.frame,None;enabled=self.enabled;generation=self.generation
            if generation!=epoch:tracker.reset();epoch=generation
            if not enabled or frame is None:continue
            started=time.monotonic()
            try:
                model,model_enabled,error=self.gestures.snapshot()
                result=tracker.track(frame,model if model_enabled and not error else None,started)
                with self.lock:
                    self.processed_frames+=1;self.elapsed=time.monotonic()-started
                    if result is not None:
                        result['sample_id']=('flow',self.processed_frames);result['tracked_at']=started
                    if self.enabled and generation==self.generation:self.result=result;self.generated=time.monotonic()
            except Exception as exc:
                tracker.reset()
                with self.lock:self.result=None;self.error=str(exc)
            next_run=started+1./20
