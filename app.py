#!/usr/bin/python3
import csv
import fcntl
import hashlib
import os
import sqlite3
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

os.umask(0o077)
import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Gst', '1.0')
gi.require_version('PangoCairo', '1.0')
from gi.repository import Gtk, Gdk, GdkPixbuf, GLib, Gst, Pango, PangoCairo
from gesture_client import GestureWorker, HAND_CONNECTIONS
from gesture_common import FullscreenGestureSequence
from visual_demo import VisualDemo
import cv2
import numpy as np
import cairo
from core import Store, QRScanner
from objects import ObjectDetector, LABELS

Gst.init(None)
DATA = Path.home() / '.local/share/liman-checkin'
DEVICE = '/dev/v4l/by-path/platform-rkisp-vir1-video-index0'

CSS = b'''
window { background: #101827; color: #e7eef8; font-family: "Noto Sans CJK SC", sans-serif; }
.title { font-size: 25px; font-weight: bold; }
.subtle { color: #a5b8cc; font-size: 14px; }
.card { background: #1a2940; border-radius: 12px; padding: 20px; }
.status { font-size: 34px; font-weight: bold; color: #67d9ef; }
.success { color: #65e4aa; }
.denied { color: #ff948f; }
button { background: #273b56; color: #edf7ff; border: 0; border-radius: 7px; padding: 10px 18px; }
button:hover { background: #385273; }
button.suggested-action { background: #147d98; }
entry { color: #edf7ff; background: #26384f; padding: 9px; }
treeview { color: #dfebf6; background: #19263a; }
treeview:selected { background: #285477; }
headerbar, notebook header { background: #19263a; color: #dfebf6; }
notebook tab { padding: 8px 14px; }
notebook, notebook stack, notebook > stack > box { background: #19263a; }
notebook tab:checked { background: #29445e; }
notebook tab label { color: #dfebf6; }
.gesture-status { font-size: 24px; font-weight: bold; }
.video-stats { background: rgba(16,24,39,.82); color: #e7eef8; padding: 7px 10px; border-radius: 6px; font-size: 13px; }
'''


def label(text, css=None, wrap=False):
    obj = Gtk.Label(label=text)
    obj.set_xalign(0)
    obj.set_line_wrap(wrap)
    if css:
        obj.get_style_context().add_class(css)
    return obj


class DetectionWorker(threading.Thread):
    def __init__(self, app):
        super().__init__(daemon=True)
        self.app = app
        self.lock, self.ready, self.stopping = threading.Lock(), threading.Event(), threading.Event()
        self.frame, self.results, self.generated = None, [], 0.
        self.processed_frames, self.inference_seconds = 0, 0.
        self.request_id = self.completed_request = None

    def submit(self, frame, request_id=None):
        with self.lock:
            self.frame = frame
            self.request_id = request_id
        self.ready.set()

    def snapshot(self):
        with self.lock:
            return list(self.results) if time.monotonic()-self.generated < 1.5 else []

    def scan_snapshot(self, request_id):
        with self.lock:
            return list(self.results) if self.completed_request == request_id else None

    def statistics(self):
        with self.lock:
            return list(self.results), self.inference_seconds, self.processed_frames

    def run(self):
        detector = None
        try:
            detector = ObjectDetector()
            next_run = 0.
            while not self.stopping.is_set():
                if not self.ready.wait(.5):
                    continue
                if self.stopping.wait(max(0., next_run-time.monotonic())):
                    break
                self.ready.clear()
                with self.lock:
                    frame, self.frame = self.frame, None
                    request_id = self.request_id
                if frame is None:
                    continue
                started = time.monotonic()
                results = detector.detect(cv2.cvtColor(frame, cv2.COLOR_BGRA2RGB))
                elapsed = time.monotonic()-started
                with self.lock:
                    self.results, self.generated = results, time.monotonic()
                    self.completed_request = request_id
                    self.processed_frames += 1
                    self.inference_seconds = elapsed
                next_run = started + .12
        except Exception as exc:
            GLib.idle_add(self.app.object_error, str(exc))
        finally:
            if detector:
                detector.close()


class ScanWorker(threading.Thread):
    def __init__(self, app):
        super().__init__(daemon=True)
        self.app = app
        self.lock, self.ready, self.stopping = threading.Lock(), threading.Event(), threading.Event()
        self.frame, self.faces, self.generated = None, [], 0.

    def submit(self, frame):
        with self.lock:
            self.frame = frame
        self.ready.set()

    def snapshot(self):
        with self.lock:
            return list(self.faces) if time.monotonic()-self.generated < 1.5 else []

    def run(self):
        scanner = None
        try:
            cascade_paths = [Path('/usr/share/opencv/haarcascades/haarcascade_frontalface_default.xml'),
                Path('/usr/share/opencv4/haarcascades/haarcascade_frontalface_default.xml')]
            cascade_path = next((p for p in cascade_paths if p.exists()), None)
            if cascade_path is None:
                raise RuntimeError('缺少人脸检测模型文件')
            detector = cv2.CascadeClassifier(str(cascade_path))
            eyes = cv2.CascadeClassifier(str(cascade_path.parent / 'haarcascade_eye_tree_eyeglasses.xml'))
            scanner = QRScanner()
            cooldown, last_face = {}, 0.
            next_run = 0.
            while not self.stopping.is_set():
                if not self.ready.wait(.5):
                    continue
                if self.stopping.wait(max(0., next_run-time.monotonic())):
                    break
                self.ready.clear()
                with self.lock:
                    frame, self.frame = self.frame, None
                if frame is None:
                    continue
                started = time.monotonic()
                gray = cv2.cvtColor(frame, cv2.COLOR_BGRA2GRAY)
                if started-last_face >= .6:
                    small = cv2.resize(gray, (320, 240))
                    candidates = detector.detectMultiScale(small, scaleFactor=1.12, minNeighbors=7, minSize=(40, 40))
                    faces = []
                    for x, y, w, h in candidates:
                        upper = small[y:y+int(h*.65), x:x+w]
                        detected_eyes = eyes.detectMultiScale(upper, scaleFactor=1.1, minNeighbors=3, minSize=(6, 6))
                        centers = [(ex+ew/2., ey+eh/2.) for ex, ey, ew, eh in detected_eyes]
                        if any(.2*w < abs(a[0]-b[0]) < .8*w and abs(a[1]-b[1]) < .2*h
                               for n, a in enumerate(centers) for b in centers[n+1:]):
                            faces.append((int(x*2), int(y*2), int((x+w)*2), int((y+h)*2)))
                    with self.lock:
                        self.faces, self.generated = faces, time.monotonic()
                    last_face = started
                payloads = list(dict.fromkeys(scanner.decode(gray)))
                if len(payloads) > 1:
                    GLib.idle_add(self.app.scan_ambiguous)
                elif payloads:
                    payload = payloads[0]
                    key = hashlib.sha256(payload.encode()).digest()
                    if started-cooldown.get(key, -100) >= 8:
                        cooldown = {k: v for k, v in cooldown.items() if started-v < 60}
                        cooldown[key] = started
                        GLib.idle_add(self.app.scanned, payload)
                next_run = started + .25
        except Exception as exc:
            GLib.idle_add(self.app.scan_error, str(exc))
        finally:
            if scanner:
                scanner.close()


class Camera(threading.Thread):
    def __init__(self, app):
        super().__init__(daemon=True)
        self.app, self.stopping = app, threading.Event()
        self.frame_lock, self.latest = threading.Lock(), None
        self.capture_fps = 0.
        self.objects = DetectionWorker(app)
        self.scans = ScanWorker(app)
        self.gestures = GestureWorker()
        self.body = GestureWorker(engine='body_engine.py',enabled=False,interval=.3,idle=.5,ttl=1.)

    def run(self):
        pipe = None
        try:
            cv2.setNumThreads(1)
            self.objects.start()
            self.scans.start()
            self.gestures.start()
            self.body.start()
            pipe = Gst.parse_launch('v4l2src device=' + DEVICE + ' io-mode=mmap ! '
                'video/x-raw,format=NV12,width=640,height=480 ! '
                'videorate drop-only=true ! video/x-raw,framerate=30/1 ! '
                'videoconvert ! video/x-raw,format=BGRx ! '
                'appsink name=frames max-buffers=1 drop=true sync=false')
            sink, bus = pipe.get_by_name('frames'), pipe.get_bus()
            pipe.set_state(Gst.State.PLAYING)
            last_frame = time.monotonic()
            last_report, frames = time.monotonic(), 0
            while not self.stopping.is_set():
                sample = sink.emit('try-pull-sample', 300 * Gst.MSECOND)
                if sample is None:
                    error = bus.timed_pop_filtered(0, Gst.MessageType.ERROR | Gst.MessageType.EOS)
                    if error:
                        raise RuntimeError(str(error.parse_error()[0]) if error.type == Gst.MessageType.ERROR else '摄像头流已结束')
                    if time.monotonic() - last_frame > 8:
                        raise RuntimeError('摄像头未返回画面，请检查连接或其他预览程序是否占用摄像头。')
                    continue
                tick, last_frame = time.monotonic(), time.monotonic()
                buf = sample.get_buffer()
                mapped, info = buf.map(Gst.MapFlags.READ)
                if not mapped:
                    raise RuntimeError('摄像头缓冲区读取失败')
                try:
                    frame = np.frombuffer(info.data, np.uint8).reshape(480, -1)[:, :640*4].reshape(480, 640, 4).copy()
                finally:
                    buf.unmap(info)
                buf, sample = None, None
                # Each buffer owns its storage and is read-only after publication.
                # Workers keep just its reference, replacing pending work with newer frames.
                frame.flags.writeable = False
                with self.frame_lock:
                    self.latest = frame
                if not self.app.demo.active:
                    self.objects.submit(frame)
                    self.scans.submit(frame)
                if self.app.demo.mode == 'body':
                    self.body.submit(frame)
                elif self.app.demo.mode != 'scanner':
                    self.gestures.submit(frame)
                frames += 1
                if tick - last_report >= 1:
                    self.capture_fps = frames/(tick-last_report)
                    last_report, frames = tick, 0
        except Exception as exc:
            GLib.idle_add(self.app.camera_error, str(exc))
        finally:
            if pipe:
                pipe.set_state(Gst.State.NULL)
            self.objects.stopping.set()
            self.scans.stopping.set()
            self.gestures.stopping.set()
            self.body.stopping.set()
            if self.objects.is_alive():
                self.objects.join(timeout=3)
            if self.scans.is_alive():
                self.scans.join(timeout=3)
            if self.gestures.is_alive():
                self.gestures.join(timeout=3)
            if self.body.is_alive():
                self.body.join(timeout=3)


class App(Gtk.Window):
    def __init__(self):
        super().__init__(title='黎曼 · 视觉检测与打卡')
        self.set_default_size(1360, 900)
        self.store = Store(DATA)
        self.result_until = 0.
        self.video_bytes = bytearray(640*480*4)
        self.video_pixels = np.frombuffer(self.video_bytes, np.uint8).reshape(480, 640, 4)
        self.video_surface = cairo.ImageSurface.create_for_data(self.video_bytes, cairo.FORMAT_RGB24, 640, 480, 640*4)
        self.has_frame = False
        self.video_sequence, self.displayed_sequence = 0, -1
        self.rendered_frames, self.inference_frames = 0, 0
        self.render_fps, self.inference_fps = 0., 0.
        self.previous_inference_frames = 0
        self.stats_tick = time.monotonic()
        self.text_layouts = {}
        self.label_font = Pango.FontDescription('Noto Sans CJK SC 10')
        self.gesture_font = Pango.FontDescription('Noto Sans CJK SC Bold 18')
        self.preview_fullscreen = False
        self.gesture_sequence = FullscreenGestureSequence()
        self.demo = VisualDemo()
        self.scan_sequence = 0
        self.normal_fill = None
        self.normal_hand = None
        self.gesture_notice = ''
        self.gesture_notice_until = 0.
        self.connect('destroy', self.quit)
        self.connect('key-press-event', self.preview_key)
        provider = Gtk.CssProvider()
        self.css_provider = provider
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        root.set_border_width(22)
        self.add(root)
        top = Gtk.Box(spacing=16)
        heading = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        heading.pack_start(label('黎曼 · 视觉检测与打卡', 'title'), False, False, 0)
        heading.pack_start(label('实时物体检测与中文标注  /  二维码授权验证', 'subtle'), False, False, 0)
        top.pack_start(heading, True, True, 0)
        self.clock = label('', 'subtle')
        top.pack_end(self.clock, False, False, 0)
        root.pack_start(top, False, False, 0)
        middle = Gtk.Box(spacing=18)
        root.pack_start(middle, True, True, 0)
        self.canvas = Gtk.DrawingArea()
        self.canvas.set_size_request(640, 440)
        self.canvas.connect('draw', self.draw)
        self.canvas.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        self.canvas.connect('button-press-event', self.preview_click)
        video = Gtk.Overlay()
        video.add(self.canvas)
        controls = Gtk.Box(spacing=8)
        controls.set_halign(Gtk.Align.END)
        controls.set_valign(Gtk.Align.START)
        controls.set_margin_top(12)
        controls.set_margin_end(12)
        self.video_stats = label('正在启动画面…', 'video-stats')
        self.video_stats.set_width_chars(24)
        controls.pack_start(self.video_stats, False, False, 0)
        self.gesture_toggle = Gtk.ToggleButton(label='手势识别')
        self.gesture_toggle.set_active(True)
        self.gesture_toggle.set_tooltip_text('握拳→张开手掌进入全屏；张开手掌→握拳退出全屏。每个姿势稳定保持约一秒。')
        self.gesture_toggle.connect('toggled', lambda *_: self.configure_interaction_workers())
        controls.pack_start(self.gesture_toggle, False, False, 0)
        self.demo_enable = Gtk.ToggleButton(label='互动：关')
        self.demo_enable.set_tooltip_text('先选玩法再开启；关闭后停止对应互动模型和游戏。')
        self.demo_enable.connect('toggled', self.change_demo_mode)
        controls.pack_start(self.demo_enable, False, False, 0)
        self.demo_selector = Gtk.ComboBoxText()
        for key, title in [('paint', '隔空画画'), ('particles', '掌心粒子'), ('pinch', '捏合缩放'), ('bricks', '隔空打砖块'), ('scanner', '物体扫描仪'), ('body', '人体动作游戏')]:
            self.demo_selector.append(key, title)
        self.demo_selector.set_active_id('bricks')
        self.demo_selector.set_tooltip_text('演示模式暂停物体检测、扫码及手势全屏切换；切回普通模式恢复。F11、Esc 仍可用。')
        self.demo_selector.connect('changed', self.change_demo_mode)
        controls.pack_start(self.demo_selector, False, False, 0)
        self.clear_drawing = Gtk.Button(label='清空画布')
        self.clear_drawing.set_no_show_all(True)
        self.clear_drawing.connect('clicked', lambda *_: self.demo.reset_current())
        controls.pack_start(self.clear_drawing, False, False, 0)
        self.scan_button = Gtk.Button(label='扫描（空格）')
        self.scan_button.set_no_show_all(True)
        self.scan_button.connect('clicked', self.start_object_scan)
        controls.pack_start(self.scan_button, False, False, 0)
        self.save_scan_button = Gtk.Button(label='保存卡片')
        self.save_scan_button.set_no_show_all(True)
        self.save_scan_button.connect('clicked', self.save_scan_card)
        controls.pack_start(self.save_scan_button, False, False, 0)
        self.fill_preview = Gtk.ToggleButton(label='铺满屏幕')
        self.fill_preview.set_tooltip_text('保持比例铺满屏幕，可能裁切画面边缘；关闭后显示完整画面。')
        self.fill_preview.connect('toggled', lambda *_: self.canvas.queue_draw())
        self.fill_preview.set_no_show_all(True)
        controls.pack_start(self.fill_preview, False, False, 0)
        self.fullscreen_button = Gtk.Button(label='全屏画面（F11）')
        self.fullscreen_button.connect('clicked', lambda *_: self.set_preview_fullscreen(not self.preview_fullscreen))
        controls.pack_start(self.fullscreen_button, False, False, 0)
        video.add_overlay(controls)
        middle.pack_start(video, True, True, 0)
        side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        side.get_style_context().add_class('card')
        side.set_size_request(310, -1)
        middle.pack_start(side, False, False, 0)
        side.pack_start(label('验证结果', 'subtle'), False, False, 0)
        self.status = label('等待扫码', 'status')
        self.status.set_line_wrap(True)
        self.status.set_max_width_chars(14)
        side.pack_start(self.status, False, False, 0)
        self.detail = label('请将个人二维码对准摄像头。', wrap=True)
        side.pack_start(self.detail, False, False, 0)
        self.camera_label = label('正在启动摄像头…', 'subtle', True)
        side.pack_start(self.camera_label, False, False, 0)
        self.object_label = label('正在加载物体检测模型…', 'subtle', True)
        self.object_label.set_max_width_chars(26)
        side.pack_start(self.object_label, False, False, 0)
        self.stats = label('', 'subtle', True)
        side.pack_start(self.stats, False, False, 0)
        enroll = Gtk.Button(label='＋ 登记人员 / 生成二维码')
        enroll.get_style_context().add_class('suggested-action')
        enroll.connect('clicked', self.enroll)
        side.pack_start(enroll, False, False, 8)
        supported = Gtk.Button(label='查看支持的 80 类物体')
        supported.connect('clicked', lambda *_: self.message('支持的物体类别',
            '只对模型支持且置信度达到 45% 的目标画框。\n\n' +
            '\n'.join('、'.join(LABELS[i:i+8]) for i in range(0, len(LABELS), 8))))
        side.pack_start(supported, False, False, 0)
        side.pack_start(label('首次使用：登记姓名并生成二维码，\n保存至手机或打印，再对准摄像头。', 'subtle', True), False, False, 0)
        side.pack_end(label('人脸框只用于检测有人，不识别身份。\n身份验证以个人二维码为准。\n不保存人脸照片或视频。', 'subtle', True), False, False, 0)
        tabs = Gtk.Notebook()
        tabs.set_size_request(-1, 235)
        root.pack_start(tabs, False, False, 0)
        people_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.people_model = Gtk.ListStore(str, str, str, str, str, str)
        self.people_view = self.table(self.people_model, ['姓名', '编号', '部门', '授权状态', '最近通过'], [1, 2, 3, 4, 5], people_box)
        actions = Gtk.Box(spacing=8)
        for text, action in [('查看个人二维码', self.show_selected_qr), ('撤销 / 恢复授权', self.toggle_person), ('打开二维码目录', self.open_directory)]:
            button = Gtk.Button(label=text)
            button.connect('clicked', action)
            actions.pack_start(button, False, False, 0)
        people_box.pack_start(actions, False, False, 0)
        tabs.append_page(people_box, label('人员与凭证'))
        logs_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.log_model = Gtk.ListStore(str, str, str, str, str)
        self.table(self.log_model, ['时间（板子本地时区）', '姓名', '编号', '结果', '说明'], range(5), logs_box)
        export = Gtk.Button(label='导出全部打卡记录 CSV')
        export.connect('clicked', self.export)
        logs_box.pack_start(export, False, False, 0)
        tabs.append_page(logs_box, label('打卡记录 · 最近 200 条'))
        self.footer = label('资料保存在本机，关闭窗口后不会丢失。', 'subtle')
        root.pack_start(self.footer, False, False, 0)
        self.preview_root, self.preview_middle = root, middle
        self.preview_panels = [top, side, tabs, self.footer]
        self.refresh()
        self.camera = Camera(self)
        self.configure_interaction_workers()
        self.camera.start()
        GLib.timeout_add(15, self.paint)
        GLib.timeout_add_seconds(1, self.tick)
        self.show_all()
        self.maximize()
        if os.environ.get('LIMAN_CAMERA_FULLSCREEN') == '1':
            self.fill_preview.set_active(os.environ.get('LIMAN_CAMERA_FILL', '1') == '1')
            GLib.idle_add(self.set_preview_fullscreen, True)

    def set_preview_fullscreen(self, enabled):
        self.preview_fullscreen = bool(enabled)
        self.text_layouts.clear()
        for widget in self.preview_panels:
            widget.set_visible(not enabled)
        self.preview_root.set_border_width(0 if enabled else 22)
        self.preview_root.set_spacing(0 if enabled else 14)
        self.preview_middle.set_spacing(0 if enabled else 18)
        self.canvas.set_size_request(1 if enabled else 640, 1 if enabled else 440)
        self.fullscreen_button.set_label('退出全屏（Esc）' if enabled else '全屏画面（F11）')
        self.fill_preview.set_visible(enabled)
        if enabled:
            self.fullscreen()
        else:
            self.unfullscreen()
            self.maximize()
        self.present()
        self.canvas.queue_draw()
        return False

    def preview_key(self, _widget, event):
        if event.keyval == Gdk.KEY_space and self.demo.mode == 'scanner':
            self.start_object_scan()
            return True
        if event.keyval == Gdk.KEY_F11:
            self.set_preview_fullscreen(not self.preview_fullscreen)
            return True
        if event.keyval == Gdk.KEY_Escape and self.preview_fullscreen:
            self.set_preview_fullscreen(False)
            return True
        return False

    def preview_click(self, _widget, event):
        if event.button == 1 and event.type == Gdk.EventType._2BUTTON_PRESS:
            self.set_preview_fullscreen(not self.preview_fullscreen)
            return True
        return False

    def table(self, model, names, indexes, parent):
        view = Gtk.TreeView(model=model)
        for name, index in zip(names, indexes):
            renderer = Gtk.CellRendererText()
            renderer.set_property('ellipsize', 3)
            col = Gtk.TreeViewColumn(name, renderer, text=index)
            col.set_expand(True)
            col.set_resizable(True)
            view.append_column(col)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll.add(view)
        parent.pack_start(scroll, True, True, 0)
        return view

    def refresh(self):
        self.people_model.clear()
        for p in self.store.people():
            self.people_model.append([p['id'], p['name'], p['employee'], p['department'],
                '有效' if p['enabled'] else '已撤销', p['last_seen'] or '尚未打卡'])
        self.log_model.clear()
        for event in self.store.events():
            self.log_model.append([event[k] for k in ['happened', 'name', 'employee', 'result', 'reason']])
        count, passed = self.store.counts()
        self.stats.set_text('有效人员：{} 人\n累计通过：{} 次'.format(count, passed))

    def message(self, title, text):
        dialog = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.OK, text=title)
        dialog.format_secondary_text(text)
        dialog.run()
        dialog.destroy()

    def enroll(self, *_):
        dialog = Gtk.Dialog(title='登记人员', transient_for=self, modal=True)
        dialog.add_buttons('取消', Gtk.ResponseType.CANCEL, '生成个人二维码', Gtk.ResponseType.OK)
        box = dialog.get_content_area()
        box.set_spacing(10)
        box.set_border_width(20)
        entries = []
        for title, hint, max_length in [('姓名（必填）', '请输入姓名', 40), ('人员编号（选填，不可重复）', '例如 LM001', 32), ('部门（选填）', '例如研发部', 40)]:
            box.pack_start(label(title), False, False, 0)
            entry = Gtk.Entry()
            entry.set_placeholder_text(hint)
            entry.set_max_length(max_length)
            box.pack_start(entry, False, False, 0)
            entries.append(entry)
        box.pack_start(label('二维码是个人凭证，请妥善保管。', 'subtle'), False, False, 0)
        dialog.show_all()
        while dialog.run() == Gtk.ResponseType.OK:
            try:
                person_id = self.store.enroll(*(entry.get_text() for entry in entries))
            except (ValueError, sqlite3.IntegrityError) as exc:
                self.message('登记未完成', '人员编号已存在，请更换编号。' if isinstance(exc, sqlite3.IntegrityError) else str(exc))
                continue
            except Exception as exc:
                self.message('保存失败', str(exc))
                continue
            dialog.destroy()
            self.refresh()
            self.show_qr(person_id)
            return
        dialog.destroy()

    def selected(self):
        model, it = self.people_view.get_selection().get_selected()
        if it is None:
            self.message('请先选择人员', '在人员列表中点击一行，再进行操作。')
            return None
        return self.store.person(model[it][0])

    def show_selected_qr(self, *_):
        person = self.selected()
        if person:
            self.show_qr(person['id'])

    def show_qr(self, person_id):
        person = self.store.person(person_id)
        dialog = Gtk.Dialog(title='个人二维码 · ' + person['name'], transient_for=self, modal=True)
        dialog.add_button('关闭', Gtk.ResponseType.CLOSE)
        box = dialog.get_content_area()
        box.set_border_width(20)
        box.set_spacing(12)
        box.pack_start(label(person['name'] + (' · ' + person['employee'] if person['employee'] else ''), 'title'), False, False, 0)
        pix = GdkPixbuf.Pixbuf.new_from_file_at_scale(person['qr_path'], 370, 370, True)
        box.pack_start(Gtk.Image.new_from_pixbuf(pix), False, False, 0)
        explanation = label('请用手机保存此二维码，或打开下方文件后打印。\n' + person['qr_path'], 'subtle', True)
        explanation.set_selectable(True)
        box.pack_start(explanation, False, False, 0)
        dialog.show_all()
        dialog.run()
        dialog.destroy()

    def toggle_person(self, *_):
        person = self.selected()
        if not person:
            return
        action = '撤销' if person['enabled'] else '恢复'
        dialog = Gtk.MessageDialog(transient_for=self, modal=True, buttons=Gtk.ButtonsType.OK_CANCEL,
            message_type=Gtk.MessageType.QUESTION, text='{} {} 的授权？'.format(action, person['name']))
        if dialog.run() == Gtk.ResponseType.OK:
            self.store.set_enabled(person['id'], not person['enabled'])
            self.refresh()
            self.footer.set_text('已{} {} 的二维码授权。'.format(action, person['name']))
        dialog.destroy()

    def open_directory(self, *_):
        subprocess.Popen(['gio', 'open', str(self.store.qr_dir)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def export(self, *_):
        path = Path.home() / 'Desktop' / ('打卡记录-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '.csv')
        try:
            with path.open('x', encoding='utf-8-sig', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(['时间', '姓名', '编号', '结果', '说明'])
                for row in self.store.all_events():
                    values = [row[k] for k in ['happened', 'name', 'employee', 'result', 'reason']]
                    writer.writerow(["'" + v if v.startswith(('=', '+', '-', '@', '\t', '\r')) else v for v in values])
            self.message('导出成功', '文件已保存到桌面：\n' + str(path))
        except Exception as exc:
            self.message('导出失败', str(exc))

    def set_result(self, title, detail, css):
        context = self.status.get_style_context()
        context.remove_class('success')
        context.remove_class('denied')
        if css:
            context.add_class(css)
        self.status.set_text(title)
        self.detail.set_text(detail)
        self.result_until = time.monotonic() + 6

    def scanned(self, payload):
        try:
            passed, name, reason = self.store.authorize(payload)
            self.set_result('通过 · ' + name if passed else '未授权',
                '验证成功，打卡记录已保存。' if passed else reason + '，请先登记人员信息。',
                'success' if passed else 'denied')
            self.refresh()
        except Exception as exc:
            self.set_result('保存失败', '未能保存本次记录：' + str(exc), 'denied')
        return False

    def scan_ambiguous(self):
        self.set_result('请单人扫码', '检测到多个二维码，请每次只出示一个个人凭证。', None)
        return False

    def camera_error(self, error):
        self.camera_label.set_text('摄像头不可用：' + error)
        self.set_result('摄像头异常', '关闭其他相机预览程序后，重新打开本程序。', 'denied')
        return False

    def scan_error(self, error):
        self.footer.set_text('扫码或人脸检测不可用：' + error)
        return False

    def camera_info(self, fps, faces):
        self.camera_label.set_text('摄像头在线 · {:.0f} FPS\n检测到人脸：{}（不识别身份）'.format(fps, faces))
        return False

    def object_info(self, results, elapsed):
        names = list(dict.fromkeys(LABELS[item[0]] for item in results))
        self.object_label.set_text('物体检测在线 · {:.0f} ms/次\n当前：{}'.format(elapsed*1000,
            '、'.join(names[:7]) if names else '未发现支持类别的物体'))
        return False

    def object_error(self, error):
        self.object_label.set_text('物体检测不可用：' + error)
        return False

    def paint(self):
        self.apply_gesture_fullscreen()
        if self.demo.mode == 'body':
            result, enabled, error = self.camera.body.snapshot()
        elif self.demo.mode == 'scanner':
            result, enabled, error = None, True, ''
        else:
            result, enabled, error = self.camera.gestures.snapshot()
        animated = self.demo.update(result, enabled, error, time.monotonic())
        if self.demo.mode == 'scanner':
            scene = self.demo.scene
            if scene.request_id is not None and scene.results is None:
                results = self.camera.objects.scan_snapshot(scene.request_id)
                if results is not None:scene.accept(results, lambda cls: LABELS[cls])
            can_scan = scene.request_id is None or scene.results is not None or bool(scene.error)
            if self.scan_button.get_sensitive() != can_scan:self.scan_button.set_sensitive(can_scan)
            can_save = scene.best is not None
            if self.save_scan_button.get_sensitive() != can_save:self.save_scan_button.set_sensitive(can_save)
        with self.camera.frame_lock:
            frame, self.camera.latest = self.camera.latest, None
        if frame is not None:
            np.copyto(self.video_pixels, frame)
            self.video_surface.mark_dirty()
            self.has_frame = True
            self.video_sequence += 1
        if frame is not None or animated:
            self.canvas.queue_draw()
        return True

    def draw(self, widget, cr):
        cr.save()
        cr.set_source_rgb(.035, .06, .1)
        cr.paint()
        if self.has_frame:
            if self.displayed_sequence != self.video_sequence:
                self.rendered_frames += 1
                self.displayed_sequence = self.video_sequence
            w, h = widget.get_allocated_width(), widget.get_allocated_height()
            scale = (max if self.preview_fullscreen and self.fill_preview.get_active() else min)(w/640., h/480.)
            cr.translate((w-640*scale)/2., (h-480*scale)/2.)
            cr.scale(scale, scale)
            cr.set_source_surface(self.demo.background(self.video_surface), 0, 0)
            cr.get_source().set_filter(cairo.FILTER_BILINEAR)
            cr.paint()
            if not self.demo.active:
                cr.set_source_rgb(.29, .88, .73)
                cr.set_line_width(2)
                for x1, y1, x2, y2 in self.camera.scans.snapshot():
                    cr.rectangle(x1, y1, x2-x1, y2-y1)
                    cr.stroke()
                colors = [(.32,.9,.69), (.35,.78,1.), (1.,.77,.32), (.9,.55,1.), (1.,.55,.48)]
                for cls, score, x1, y1, x2, y2 in self.camera.objects.snapshot():
                    color = colors[cls % len(colors)]
                    cr.set_source_rgb(*color)
                    cr.set_line_width(2)
                    cr.rectangle(x1, y1, x2-x1, y2-y1)
                    cr.stroke()
                    text = '{} {:.0f}%'.format(LABELS[cls], score*100)
                    layout = self.text_layouts.get(text)
                    if layout is None:
                        if len(self.text_layouts) >= 128:
                            self.text_layouts.clear()
                        layout = PangoCairo.create_layout(cr)
                        layout.set_font_description(self.label_font)
                        layout.set_text(text, -1)
                        self.text_layouts[text] = layout
                    tw, th = layout.get_pixel_size()
                    tx, ty = min(x1, 640-tw-8), max(0, y1-th-6)
                    cr.rectangle(tx, ty, tw+8, th+6)
                    cr.fill()
                    cr.set_source_rgb(.025,.055,.075)
                    cr.move_to(tx+4, ty+3)
                    PangoCairo.show_layout(cr, layout)
            gesture, enabled, error = self.camera.gestures.snapshot()
            if gesture is not None and not self.demo.active:
                points = gesture['points']
                cr.set_source_rgb(1., .82, .2)
                cr.set_line_width(2)
                for first, second in HAND_CONNECTIONS:
                    cr.move_to(float(points[first, 0]), float(points[first, 1]))
                    cr.line_to(float(points[second, 0]), float(points[second, 1]))
                cr.stroke()
                for point in points:
                    cr.arc(float(point[0]), float(point[1]), 2., 0, 2*np.pi)
                    cr.fill()
                x1, y1, x2, y2 = gesture['box']
                cr.rectangle(float(x1), float(y1), float(x2-x1), float(y2-y1))
                cr.stroke()
            if self.demo.active:
                self.demo.draw(cr, time.monotonic(), self.video_surface)
        cr.restore()
        text = self.gesture_status_text()
        key = 'gesture:' + text
        layout = self.text_layouts.get(key)
        if layout is None:
            if len(self.text_layouts) >= 128:
                self.text_layouts.clear()
            layout = PangoCairo.create_layout(cr)
            layout.set_font_description(self.gesture_font)
            layout.set_text(text, -1)
            self.text_layouts[key] = layout
        tw, th = layout.get_pixel_size()
        tx, ty = 18, max(18, widget.get_allocated_height()-th-32)
        cr.set_source_rgba(.035, .06, .1, .9)
        cr.rectangle(tx, ty, tw+24, th+16)
        cr.fill()
        cr.set_source_rgb(1., .86, .3)
        cr.move_to(tx+12, ty+8)
        PangoCairo.show_layout(cr, layout)
        return False

    def configure_interaction_workers(self):
        if not hasattr(self, 'camera'):return
        hand = self.demo.mode in ('normal','paint','particles','pinch','bricks')
        self.gesture_toggle.set_sensitive(hand)
        self.camera.gestures.set_enabled(hand and self.gesture_toggle.get_active())
        self.camera.body.set_enabled(self.demo.mode == 'body')

    def change_demo_mode(self, _widget):
        mode = self.demo_selector.get_active_id() if self.demo_enable.get_active() else 'normal'
        self.demo_enable.set_label('互动：开' if self.demo_enable.get_active() else '互动：关')
        if not self.demo.active and mode != 'normal':
            self.normal_fill = self.fill_preview.get_active()
            self.normal_hand = self.gesture_toggle.get_active()
            self.fill_preview.set_active(False)
        self.demo.set_mode(mode)
        self.gesture_sequence.reset()
        self.gesture_notice = ''
        labels = {'paint':'清空画布','pinch':'复位卡片','bricks':'重开游戏','body':'重开/校准','scanner':'重新取景'}
        self.clear_drawing.set_visible(mode in labels)
        if mode in labels:self.clear_drawing.set_label(labels[mode])
        self.scan_button.set_visible(mode == 'scanner')
        self.save_scan_button.set_visible(mode == 'scanner')
        if mode in ('paint','particles','pinch','bricks'):
            self.gesture_toggle.set_active(True)
        if mode == 'normal' and self.normal_fill is not None:
            self.fill_preview.set_active(self.normal_fill)
            self.gesture_toggle.set_active(self.normal_hand)
            self.normal_fill = self.normal_hand = None
        self.configure_interaction_workers()
        self.canvas.queue_draw()

    def start_object_scan(self, *_):
        if self.demo.mode != 'scanner' or not self.has_frame:return
        scene = self.demo.scene
        if scene.request_id is not None and scene.results is None and not scene.error:return
        self.scan_sequence += 1
        scene.start(self.video_pixels, self.scan_sequence, time.monotonic())
        self.camera.objects.submit(scene.frame, request_id=self.scan_sequence)

    def save_scan_card(self, *_):
        if self.demo.mode != 'scanner' or self.demo.scene.best is None:return
        directory = DATA / 'scans'
        try:
            directory.mkdir(parents=True,exist_ok=True,mode=0o700)
            path = directory / ('scan-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '.png')
            self.demo.scene.save(path)
            self.message('识别卡片已保存',str(path))
        except Exception as exc:self.message('保存失败',str(exc))

    def apply_gesture_fullscreen(self):
        if self.demo.active:
            return
        result, enabled, error = self.camera.gestures.snapshot()
        now = time.monotonic()
        action = self.gesture_sequence.update(
            result['label'] if result else '',
            result.get('sample_id') if result else None,
            now, enabled and not error)
        if not enabled or error:
            self.gesture_notice = ''
        if action is not None:
            if self.preview_fullscreen != action:
                self.set_preview_fullscreen(action)
            self.gesture_notice = '已进入全屏' if action else '已退出全屏'
            self.gesture_notice_until = now + 1.8

    def gesture_status_text(self):
        if self.demo.active:
            worker = self.camera.body if self.demo.mode == 'body' else self.camera.gestures
            if self.demo.mode != 'scanner' and worker.enabled and not worker.loaded:
                return '正在加载人体模型…' if self.demo.mode == 'body' else '正在加载手部模型…'
            return self.demo.status_text(time.monotonic())
        result, enabled, error = self.camera.gestures.snapshot()
        if error:
            text = '手势识别不可用：' + error[:100]
        elif not enabled:
            text = '手势识别已关闭'
        elif not self.camera.gestures.loaded:
            text = '手势：正在加载模型…'
        elif self.gesture_notice and time.monotonic() < self.gesture_notice_until:
            text = '手势控制：' + self.gesture_notice
        elif result is not None and result['label']:
            text = '手势：' + result['label']
        elif result is not None:
            text = '手势：请保持姿势'
        else:
            text = '手势：请将单手伸入画面'
        return text

    def tick(self):
        stamp = time.monotonic()
        elapsed = stamp-self.stats_tick
        results, inference_seconds, inference_frames = self.camera.objects.statistics()
        self.render_fps = self.rendered_frames/elapsed
        self.inference_fps = (inference_frames-self.previous_inference_frames)/elapsed
        self.previous_inference_frames = inference_frames
        self.rendered_frames, self.inference_frames, self.stats_tick = 0, 0, stamp
        if not self.preview_fullscreen:
            if self.demo.active:
                self.camera_label.set_text('摄像头在线 · {:.0f} FPS'.format(self.camera.capture_fps))
                self.object_label.set_text('演示中：物体检测与扫码已暂停，\n切回普通模式后恢复。')
            else:
                self.camera_info(self.camera.capture_fps, len(self.camera.scans.snapshot()))
                self.object_info(results, inference_seconds)
        if self.demo.active:
            self.video_stats.set_text('画面 {:.0f} FPS · 演示模式'.format(self.render_fps))
        else:
            self.video_stats.set_text('画面 {:.0f} FPS · 检测 {:.0f} FPS'.format(self.render_fps, self.inference_fps))
        self.clock.set_text(datetime.now().astimezone().strftime('%Y-%m-%d  %H:%M:%S %Z'))
        if self.result_until and time.monotonic() > self.result_until:
            self.set_result('等待扫码', '请将个人二维码对准摄像头。', None)
            self.result_until = 0
        return True

    def quit(self, *_):
        self.camera.stopping.set()
        self.camera.join(timeout=3)
        self.store.db.close()
        Gtk.main_quit()


if __name__ == '__main__':
    DATA.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = (DATA / 'app.lock').open('a+')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.seek(0)
        try:
            os.kill(int(lock.read().strip()), signal.SIGUSR1)
        except (ValueError, OSError):
            pass
        sys.exit(0)
    app = None
    signal.signal(signal.SIGUSR1, lambda *_: GLib.idle_add(lambda: app.present() if app else False))
    lock.seek(0)
    lock.truncate()
    lock.write(str(os.getpid()))
    lock.flush()
    app = App()
    Gtk.main()
