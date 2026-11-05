"""Single-hand gestures; preview buffers are shared read-only, never queued."""
import sys
import os
import time
from pathlib import Path

RUNTIME = Path(__file__).parent / 'gesture-runtime'
if RUNTIME.exists():
    sys.path.insert(0, str(RUNTIME))
import cv2
import numpy as np

from gesture_common import HAND_CONNECTIONS


class LiteNetwork:
    def __init__(self,path):
        from tflite_runtime.interpreter import Interpreter
        self.interpreter=Interpreter(model_path=str(path),num_threads=int(os.environ.get('LIMAN_GESTURE_THREADS','1')))
        self.interpreter.allocate_tensors()
        self.input=self.interpreter.get_input_details()[0]
        self.outputs=self.interpreter.get_output_details()
        self.outputs.sort(key=lambda o: int(o['name'].rsplit('_',1)[1]) if o['name'].rsplit('_',1)[-1].isdigit() else 0)
    def setPreferableBackend(self,value):pass
    def setPreferableTarget(self,value):pass
    def setInput(self,blob):
        self.interpreter.set_tensor(self.input['index'],np.ascontiguousarray(blob,np.float32))
    def getUnconnectedOutLayersNames(self):return [o['name'] for o in self.outputs]
    def forward(self,names):
        self.interpreter.invoke()
        return [self.interpreter.get_tensor(o['index']) for o in self.outputs]


def Network(path):
    return LiteNetwork(path)


def describe_hand(points):
    p = np.asarray(points, dtype=np.float32)
    if p.shape != (21,3) or not np.isfinite(p).all(): return {'label':'','number':None}
    def distance(a,b): return float(np.linalg.norm(p[a]-p[b]))
    def straight(a,b,c):
        u,v=p[a]-p[b],p[c]-p[b]
        den=float(np.linalg.norm(u)*np.linalg.norm(v))
        return den>1e-6 and float(np.dot(u,v)/den)<-.80
    extended=[]
    for base in [5,9,13,17]:
        extended.append(straight(base,base+1,base+3)
                        and distance(base+3,0)>1.12*distance(base+1,0))
    palm=max(distance(0,9),distance(5,17),1.)
    thumb=(straight(1,2,4) and distance(4,5)>.55*palm
           and distance(4,0)>1.1*distance(2,0))
    label='';number=None
    if not extended[0] and all(extended[1:]) and distance(4,8)<.30*palm:
        label='OK'
    elif all(extended):
        label='张开手掌';number=5 if thumb else 4
    elif extended == [True,True,False,False]:
        label='胜利手势'
        if not thumb:number=2
    elif extended == [True,False,False,False]:
        label='食指向上'
        if not thumb:number=1
    elif extended == [True,True,True,False] and not thumb:number=3
    if not any(extended):
        if thumb and p[4,1] < p[2,1]-.30*palm:label='点赞'
        if not thumb:label='握拳'
    return {'label':label,'number':number}


def classify(points):
    return describe_hand(points)['label']


def stop_pair(first,second):
    if first is None or second is None:return False
    if first.get('number')!=5 or second.get('number')!=5:return False
    a,b=first['points'],second['points']
    span=max(float(np.linalg.norm(p[5,:2]-p[17,:2])) for p in (a,b))
    return float(np.linalg.norm(a[0,:2]-b[0,:2]))>max(20.,span*1.2)


class GestureDetector:
    def __init__(self):
        from gesture_handpose import MPHandPose
        models = Path(__file__).parent/'gesture-models'
        self.palm = Network(models/'palm_detection_lite.tflite')
        self.hand = MPHandPose(str(models/'hand_landmark_lite.tflite'),confThreshold=.75)
        anchors=[]
        for grid,count in [(24,2),(12,6)]:
            for y in range(grid):
                for x in range(grid):
                    anchors.extend([((x+.5)/grid,(y+.5)/grid)]*count)
        self.anchors=np.asarray(anchors,np.float32)
        self.tracked=None
        self.last_search=0.
        self.last_stop_search=0.

    def palms(self,bgr):
        h,w=bgr.shape[:2];gain=192./max(h,w)
        rw,rh=round(w*gain),round(h*gain)
        left,top=(192-rw)//2,(192-rh)//2
        blob=np.zeros((192,192,3),np.float32)
        small=cv2.resize(bgr,(rw,rh))
        blob[top:top+rh,left:left+rw]=cv2.cvtColor(small,cv2.COLOR_BGR2RGB)/255.
        self.palm.setInput(blob[None])
        output=self.palm.forward(self.palm.getUnconnectedOutLayersNames())
        boxes=next(x for x in output if x.shape[-1]==18)[0]
        raw=next(x for x in output if x.shape[-1]==1)[0,:,0]
        scores=1./(1.+np.exp(-np.clip(raw,-50,50)))
        ids=np.flatnonzero(scores>=.65)
        if not len(ids):return []
        ids=ids[np.argsort(scores[ids])[::-1]][:20]
        found=[]
        for i in ids:
            center=boxes[i,:2]/192.+self.anchors[i]
            half=boxes[i,2:4]/384.
            box=np.r_[center-half,center+half]*max(h,w)-[left/gain,top/gain]*2
            if min(box[2:]-box[:2])<20:continue
            skip=False
            for other in found:
                tl=np.maximum(box[:2],other[:2]);br=np.minimum(box[2:],other[2:4])
                wh=np.maximum(0,br-tl);inter=float(np.prod(wh))
                union=float(np.prod(box[2:]-box[:2])+np.prod(other[2:4]-other[:2]))-inter
                if inter/max(union,1.)>.3:skip=True;break
            if skip:continue
            points=(boxes[i,4:].reshape(7,2)/192.+self.anchors[i])*max(h,w)-[left/gain,top/gain]
            found.append(np.r_[box,points.reshape(-1),scores[i]])
        return found[:2]

    def hand_result(self,bgr,palm):
        result=self.hand.infer(bgr,palm)
        if result is None:return None
        points=result[4:67].reshape(21,3)
        palm_points=points[[0,5,9,13,17,1,2],:2]
        lo,hi=palm_points.min(axis=0),palm_points.max(axis=0)
        if np.min(hi-lo)<8 or not np.isfinite(points).all():return None
        return dict(describe_hand(points),points=points.copy(),box=result[:4].copy(),
                    confidence=float(result[-1]),palm=np.r_[lo,hi,palm_points.reshape(-1),result[-1]])

    def detect(self,frame,stop_only=False):
        bgr=cv2.cvtColor(frame,cv2.COLOR_BGRA2BGR)
        now=time.monotonic()
        search=stop_only or self.tracked is None or now-self.last_search>=1.5
        palms=self.palms(bgr) if search else [self.tracked]
        if search:self.last_search=now
        if stop_only and len(palms)<2:return None
        if not palms:
            self.tracked=None;return None
        result=self.hand_result(bgr,palms[0])
        if result is None:
            self.tracked=None;return None
        self.tracked=result.pop('palm')
        result['stop']=False;result['stop_checked']=result['number']!=5
        if result['number']==5 and (stop_only or now-self.last_stop_search>=.5):
            self.last_stop_search=now;result['stop_checked']=True
            candidates=palms if search else self.palms(bgr)
            primary=result['points'][[0,5,9,13,17],:2].mean(axis=0)
            for palm in candidates:
                center=palm[4:18].reshape(7,2)[:5].mean(axis=0)
                if float(np.linalg.norm(center-primary))<max(20.,float(np.linalg.norm(result['points'][5,:2]-result['points'][17,:2]))):continue
                other=self.hand_result(bgr,palm)
                if stop_pair(result,other):result['stop']=True;break
        return result
