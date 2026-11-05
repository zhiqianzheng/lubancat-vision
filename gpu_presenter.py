"""Private Mali process: stable shared pixels in, completed EGL swaps out."""
import argparse
import json
import mmap
import sys
import numpy as np
from gpu_display import GpuDisplay


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--window',type=int,required=True)
    parser.add_argument('--video-fd',type=int,required=True)
    parser.add_argument('--overlay-fd',type=int,required=True)
    args=parser.parse_args()
    pixels=mmap.mmap(args.video_fd,640*480*4)
    video=np.frombuffer(pixels,np.uint8).reshape(480,640,4)
    overlay_memory=mmap.mmap(args.overlay_fd,3840*2160*4)
    display=GpuDisplay(window=args.window)
    try:
        print(json.dumps(dict(ready=True,renderer=display.renderer)),flush=True)
        for line in sys.stdin:
            message=json.loads(line)
            if message.get('quit'):break
            display.width=message['width'];display.height=message['height']
            width,height=display.width,display.height
            if width<1 or height<1 or width*height>3840*2160:raise ValueError('Unsupported preview size')
            overlay=np.frombuffer(overlay_memory,np.uint8,count=width*height*4).reshape(height,width,4) if message.get('overlay') else None
            sequence=message['sequence']
            display.draw(video,overlay=overlay,fill=message.get('fill',True),
                overlay_changed=message.get('overlay_changed',True),
                on_uploaded=lambda:print(json.dumps(dict(released=sequence)),flush=True))
            del overlay
            print(json.dumps(dict(done=message['sequence'])),flush=True)
    finally:
        display.close();del video;pixels.close();overlay_memory.close()


if __name__=='__main__':main()
