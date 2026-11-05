# Third-party notices

## OpenCV Zoo / MediaPipe Hands

`gesture_handpose.py` adapts the OpenCV Zoo `MPHandPose` implementation, retaining its geometry and replacing its inference adapter for the board runtime. Upstream: https://github.com/opencv/opencv_zoo/tree/main/models/handpose_estimation_mediapipe . Apache-2.0 terms are retained in `gesture-models/hand-LICENSE`. Palm component terms are retained in `gesture-models/palm-LICENSE`. Original authors retain their copyrights.

The separately downloaded palm and hand landmark models originate from MediaPipe assets, https://github.com/google-ai-edge/mediapipe . Model weights are not redistributed in this repository.

## Rockchip / YOLOv5

RKNN integration and YOLO decoding follow the Rockchip RKNPU2 YOLOv5 example for RK3568: https://github.com/rockchip-linux/rknpu2/tree/v1.5.2/examples/rknn_yolov5_demo . Rockchip's copyright, redistribution conditions and disclaimer are retained in `LICENSES/Rockchip.txt`. The RKNN runtime and YOLO model are downloaded separately and remain subject to their upstream terms.

The underlying YOLOv5 project is https://github.com/ultralytics/yolov5 . No YOLO training framework or model weight is included here.

## Other dependencies

GTK, GStreamer, OpenCV, NumPy, TensorFlow Lite Runtime, ZBar, qrcode and Pillow are external dependencies. Their packages and licenses are not replaced by these notices. No third-party runtime wheel or shared library is redistributed here.

These notices do not grant a new license over this project's original code.
