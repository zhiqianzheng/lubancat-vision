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
    candidate='';since=0.
    for line in sys.stdin:
        command=json.loads(line)
        if command.get('reset'):
            detector.tracked=None;candidate='';since=0.
        started=time.monotonic();result=detector.detect(frame)
        if result is not None:
            name=result['label']
            if name!=candidate:candidate=name;since=started
            result['label']=name if name and started-since>=.18 else ''
            result['points']=result['points'].tolist();result['box']=result['box'].tolist()
        else:candidate=''
        send({'result':result})

if __name__=='__main__':
    try:main()
    except Exception as exc:send({'error':str(exc)})
