# LubanCat Vision

在 LubanCat-2 / RK3568 上运行的 CSI 摄像头应用：YOLOv5 物体检测、中文标签、二维码登记与打卡、五种单手静态手势识别。支持开机全屏，以及运行时开关手势识别。

## 实际运行效果

以下为开发板 HDMI 桌面的实际截屏。界面概览使用临时空数据库展示，不包含真实姓名、工号或二维码凭证。

### 完整界面与物体检测

摄像头预览、中文物体标签、二维码登记入口及人员与记录面板。

![开发板上的完整应用界面与 YOLO 物体检测](assets/screenshots/interface-overview.png)

### 张开手掌

实时叠加手部关键点与骨架，左下角显示“手势：张开手掌”；背景物体仍同时进行检测。

![实际摄像头画面识别张开手掌并显示手部骨架](assets/screenshots/gesture-open-palm.png)

### 比耶手势

识别双指比耶姿势，左下角显示“手势：胜利手势”。

![实际摄像头画面识别比耶并显示胜利手势](assets/screenshots/gesture-victory.png)

## 当前功能

- OV8858 CSI CAM1 预览，640×480 采集，全屏等比例显示或裁切铺满。F11 / 双击切换全屏，Esc 退出全屏。
- RKNN NPU 执行 YOLOv5s，支持 COCO 80 类物体，中文名称和置信度叠加。
- 张开手掌、握拳、比耶、点赞、食指向上；显示手部关键点和手势名称。当前手势没有绑定拍照、录像等操作。
- 登记姓名、工号、部门并生成随机个人二维码，扫码授权、撤销/恢复授权和导出记录。人脸框只检测人脸，不确认身份。
- 预览、物体检测、扫码独立处理最新帧；手势模型运行在独立低优先级进程，通过内存映射交换单帧。

## 已验证环境

LubanCat-2 v3 / RK3568，Debian 10，Linux 4.19，Python 3.7.3，OpenCV 3.2，GTK3 / X11 / XFCE；摄像头使用 OV8858、CAM1 和 rkisp-vir1。其他硬件、摄像头、发行版尚未验证。

这是应用源码，不包含系统镜像、内核设备树和摄像头驱动。先确认系统已识别摄像头，rkaiq 3A 正常工作，且桌面能够正常登录。

## 目录

- `app.py`：GTK 界面、预览、全屏与检测叠加。
- `core.py`：本机 SQLite、二维码登记和授权逻辑。
- `objects.py`：RKNN YOLOv5 推理与后处理。
- `gesture_client.py`、`gesture_engine.py`：预览侧客户端与独立推理进程。
- `gestures.py`、`gesture_handpose.py`、`gesture_common.py`：手掌检测、手部关键点与五种手势判断。
- `wait_display.py`、`deploy/liman-checkin.service`：等待 X11 就绪并自动启动应用。

## 运行依赖

系统需提供 Python 3、PyGObject/GTK3、GStreamer 及其 Python 绑定、V4L2 和 videoconvert 插件、OpenCV、NumPy、PyCairo、PangoCairo、qrcode、Pillow、libzbar、Noto CJK 字体，以及 OpenCV Haar 人脸和眼睛级联模型。

手势进程使用私有目录 `gesture-runtime/`，已验证版本为 `tflite-runtime==2.11.0` 与 `numpy==1.21.6`，需选用 CPython 3.7 / aarch64 对应构建。不要将电脑上的 x86/Windows 包复制到板子。主程序使用系统 NumPy，私有依赖仅由手势进程载入。

## 模型与 RKNN 运行库

仓库不附带模型权重或二进制运行库。部署时按下表放置，先查看各上游项目的许可证与模型说明。不要用不兼容的新运行库直接替换系统运行库。当前 RKNN 模型对应私有 v1.5.2 运行库。

| 部署路径 | 上游下载 | SHA-256 |
|---|---|---|
| `models/yolov5s-640-640.rknn` | [下载](https://raw.githubusercontent.com/rockchip-linux/rknpu2/v1.5.2/examples/rknn_yolov5_demo/model/RK3566_RK3568/yolov5s-640-640.rknn) | `6ec478fa14fa9ee2067041158a410a61b128242d7bef21e3f16276c47ef38d08` |
| `lib/librknnrt.so` | [下载](https://raw.githubusercontent.com/rockchip-linux/rknpu2/v1.5.2/runtime/RK356X/Linux/librknn_api/aarch64/librknnrt.so) | `135f497b820ae161d36f756c26b949419d271918e915e71663d424e5c1d799e7` |
| `gesture-models/palm_detection_lite.tflite` | [下载](https://storage.googleapis.com/mediapipe-assets/palm_detection_lite.tflite) | `e9a4aaddf90dda56a87235303cf00e4c2d3fb28725f68fd88772997dac905c18` |
| `gesture-models/hand_landmark_lite.tflite` | [下载](https://storage.googleapis.com/mediapipe-assets/hand_landmark_lite.tflite) | `d7fde8ac11f8ce03f8663775bfc323f4fc9f2a38062b4f4efa142874ef5b2a48` |

## 板端启动

1. 将应用部署至 `/opt/liman-checkin`，按上表放置模型和运行库，并准备 `gesture-runtime/`。
2. 检查 `app.py` 中 `DEVICE`；当前为 `/dev/v4l/by-path/platform-rkisp-vir1-video-index0`。
3. 在本机 X11 桌面用户下运行 `python3 /opt/liman-checkin/app.py`，先确认预览、识别和扫码正常。
4. 自动启动可使用 `deploy/liman-checkin.service`。此配置针对桌面用户 `cat`、`DISPLAY=:0` 和 `/home/cat/.Xauthority`；其他环境需调整，并配置好桌面自动登录与摄像头访问权限。
5. 安装 unit 后执行 `systemctl daemon-reload` 和 `systemctl enable --now liman-checkin.service`；通过 `journalctl -u liman-checkin.service` 查看日志。

启动环境变量 `LIMAN_CAMERA_FULLSCREEN=1` 开启全屏，`LIMAN_CAMERA_FILL=1` 开启等比例裁切铺满。界面右上角可以关闭手势识别。识别时先将单手完整伸入画面，保持姿势约 1～2 秒；光照、遮挡、手的位置会影响效果。

## 本地资料

人员、工号、打卡记录和二维码保存在运行用户的 `~/.local/share/liman-checkin/`。请自行备份；不要上传该目录。本仓库仅收录明确选择的运行效果图，不包含人员数据库、二维码凭证、Wi-Fi/SSH 密码、令牌、运行日志或测试材料。

## 验证范围

本版本在实际开发板上验证了摄像头、YOLO NPU 推理、二维码流程和五种手势样例，使用者也实际确认了手势可识别、流畅度可接受。性能随温度、光照、场景与显示配置变化；这不是固定帧率或端到端延迟保证。

第三方来源与许可见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。未为本项目原创部分自动选定开源许可证。
