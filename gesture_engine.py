"""Private low-priority gesture model process, communicating one frame at a time."""
import json
import mmap
import os
import sys
import time
os.nice(10)
if hasattr(os,'sched_setaffinity'):
    os.sched_setaffinity(0,{max(os.sched_getaffinity(0))})
import gestures
from gesture_common import StableHandNumber
import numpy as np
import cv2
cv2.setNumThreads(1)

def send(value):
    print(json.dumps(value,ensure_ascii=True),flush=True)

def main():
    detector=gestures.GestureDetector()
    shared=mmap.mmap(int(sys.argv[1]),640*480*4)
    frame=np.frombuffer(shared,np.uint8).reshape(480,640,4)
    frame.flags.writeable=False
    send({'ready':True})
    candidate='';since=0.;numbers=StableHandNumber()
    for line in sys.stdin:
        command=json.loads(line)
        if command.get('reset'):
            detector.tracked=None;candidate='';since=0.;numbers.reset()
        started=time.monotonic();result=detector.detect(frame,stop_only=bool(command.get('monitor_only')))
        if result is not None:
            result['number']=numbers.update(result.get('number'),started)
            name=result['label']
            if name!=candidate:candidate=name;since=started
            result['label']=name if name and started-since>=.18 else ''
            result['points']=result['points'].tolist();result['box']=result['box'].tolist()
        else:candidate='';numbers.reset()
        send({'result':result})

if __name__=='__main__':
    try:main()
    except Exception as exc:send({'error':str(exc)})
