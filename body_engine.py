"""MoveNet pose process, created only while the body game is enabled."""
import os
import sys
import json
import mmap
from pathlib import Path
os.nice(10)
if hasattr(os,'sched_setaffinity'):os.sched_setaffinity(0,{max(os.sched_getaffinity(0))})
from gestures import RUNTIME
from tflite_runtime.interpreter import Interpreter
import numpy as np
import cv2
cv2.setNumThreads(1)

class BodyDetector:
    def __init__(self):
        self.model=Interpreter(model_path=str(Path(__file__).parent/'gesture-models/movenet-lightning.tflite'),num_threads=1)
        self.model.allocate_tensors();self.input=self.model.get_input_details()[0];self.output=self.model.get_output_details()[0]
        if tuple(self.input['shape'])!=(1,192,192,3) or self.input['dtype']!=np.uint8:raise RuntimeError('人体模型输入不匹配')
    def detect(self,frame):
        rgb=cv2.cvtColor(frame,cv2.COLOR_BGRA2RGB);image=np.zeros((1,192,192,3),np.uint8)
        image[0,24:168]=cv2.resize(rgb,(192,144))
        self.model.set_tensor(self.input['index'],image);self.model.invoke();raw=self.model.get_tensor(self.output['index']).reshape(17,3)
        points=np.stack([raw[:,1]*640.,(raw[:,0]*192.-24)/.3,raw[:,2]],axis=1)
        outside=(points[:,0]<0)|(points[:,0]>=640)|(points[:,1]<0)|(points[:,1]>=480);points[outside,2]=0.
        points[:,0]=np.clip(points[:,0],0,639);points[:,1]=np.clip(points[:,1],0,479)
        if not all(points[i,2]>.25 for i in (5,6,11,12)):return None
        good=points[:,2]>.3
        if good.sum()<6:return None
        box=np.r_[points[good,:2].min(axis=0),points[good,:2].max(axis=0)]
        return {'points':points.tolist(),'box':box.tolist(),'label':'人体','confidence':float(points[good,2].mean())}

def send(value):print(json.dumps(value,ensure_ascii=True),flush=True)
def main():
    model=BodyDetector();shared=mmap.mmap(int(sys.argv[1]),640*480*4);frame=np.frombuffer(shared,np.uint8).reshape(480,640,4)
    frame.flags.writeable=False;send({'ready':True})
    for line in sys.stdin:
        json.loads(line);send({'result':model.detect(frame)})
if __name__=='__main__':
    try:main()
    except Exception as exc:send({'error':str(exc)})
