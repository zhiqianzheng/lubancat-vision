#!/usr/bin/env python3
"""Production deployment CLI for LubanCat-2 v3 / Debian 10 / OV8858 CAM1.

Run with the vendor /usr/bin/python3 (Python 3.7). Models and wheels are
downloaded separately; this module never replaces the system GPU/NPU drivers.
"""
import argparse
import datetime
import hashlib
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CAMERA = '/dev/v4l/by-path/platform-rkisp-vir1-video-index0'
SERVICE = 'liman-checkin.service'
SOURCE_FILES = (
    'app.py', 'body_engine.py', 'core.py', 'gesture_client.py',
    'gesture_common.py', 'gesture_engine.py', 'gesture_handpose.py',
    'gestures.py', 'gpu_bridge.py', 'gpu_display.py', 'gpu_presenter.py',
    'gpu_preview.py', 'hand_motion.py', 'interaction_games.py',
    'objects.py', 'visual_demo.py', 'wait_display.py',
    'THIRD_PARTY_NOTICES.md', 'LICENSES/Rockchip.txt',
    'gesture-models/hand-LICENSE', 'gesture-models/palm-LICENSE',
    'deploy/liman-checkin.service', 'deploy.py',
)
RK = 'https://raw.githubusercontent.com/rockchip-linux/rknpu2/v1.5.2/'
ARTIFACTS = (
    ('models/yolov5s-640-640.rknn', RK + 'examples/rknn_yolov5_demo/model/RK3566_RK3568/yolov5s-640-640.rknn',
     '6ec478fa14fa9ee2067041158a410a61b128242d7bef21e3f16276c47ef38d08', None),
    ('lib/librknnrt.so', RK + 'runtime/RK356X/Linux/librknn_api/aarch64/librknnrt.so',
     '135f497b820ae161d36f756c26b949419d271918e915e71663d424e5c1d799e7', None),
    ('gesture-models/palm_detection_lite.tflite', 'https://storage.googleapis.com/mediapipe-assets/palm_detection_lite.tflite',
     'e9a4aaddf90dda56a87235303cf00e4c2d3fb28725f68fd88772997dac905c18', None),
    ('gesture-models/hand_landmark_lite.tflite', 'https://storage.googleapis.com/mediapipe-assets/hand_landmark_lite.tflite',
     'd7fde8ac11f8ce03f8663775bfc323f4fc9f2a38062b4f4efa142874ef5b2a48', None),
    ('gesture-models/movenet-lightning.tflite',
     'https://www.kaggle.com/api/v1/models/google/movenet/tfLite/singlepose-lightning-tflite-int8/1/download',
     'cd7cc22fa946e5d146a7b98d496853e1923e22828d3972d579973f27f91bb105', '4.tflite'),
    ('wheels/numpy-1.21.6-cp37-cp37m-manylinux_2_17_aarch64.manylinux2014_aarch64.whl',
     'https://files.pythonhosted.org/packages/b7/0d/86662f93102e42545cdf031da4fddf0ace9030ec67478932a628afc5973b/numpy-1.21.6-cp37-cp37m-manylinux_2_17_aarch64.manylinux2014_aarch64.whl',
     '7c4068a8c44014b2d55f3c3f574c376b2494ca9cc73d2f1bd692382b6dffe3db', None),
    ('wheels/tflite_runtime-2.11.0-cp37-cp37m-manylinux2014_aarch64.whl',
     'https://files.pythonhosted.org/packages/d3/04/ad399c2cf7b0f301b6a44704fa1e6b3f1f64e16b0cbbd01132e53dee1b95/tflite_runtime-2.11.0-cp37-cp37m-manylinux2014_aarch64.whl',
     '50832befa76d69de082394716846e12d85fa8257f3ddb0a4755479bdb0a47083', None),
    ('wheels/pip-23.1.2-py3-none-any.whl',
     'https://files.pythonhosted.org/packages/08/e3/57d4c24a050aa0bcca46b2920bff40847db79535dc78141eb83581a52eb8/pip-23.1.2-py3-none-any.whl',
     '3ef6ac33239e4027d9a5598a381b9d30880a1477e50039db2eac6e8a8f6d1b18', None),
)
PACKAGES = (
    'ca-certificates', 'python3-gi', 'python3-gi-cairo', 'python3-cairo',
    'python3-numpy', 'python3-opencv', 'python3-qrcode', 'python3-pil',
    'gir1.2-gtk-3.0', 'gir1.2-gstreamer-1.0', 'gir1.2-gst-plugins-base-1.0',
    'gstreamer1.0-plugins-base', 'gstreamer1.0-plugins-good',
    'gstreamer1.0-tools', 'libzbar0', 'fonts-noto-cjk', 'fontconfig',
    'opencv-data', 'libx11-6', 'lightdm', 'xfce4', 'xserver-xorg',
)


def run(args, **kwargs):
    return subprocess.run([str(x) for x in args], check=True, **kwargs)


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def fetch(artifact, cache, offline=False):
    name, url, sha, member = artifact
    # Content-addressed cache: corrupted files are never accepted or executed.
    dest = cache / sha / Path(name).name
    if dest.is_file() and not dest.is_symlink() and digest(dest) == sha:
        return dest
    if offline:
        raise RuntimeError('离线缓存缺失或校验失败：' + name)
    cache.mkdir(parents=True, exist_ok=True)
    dest.parent.mkdir(exist_ok=True)
    if dest.parent.is_symlink():
        raise RuntimeError('缓存条目目录不能是符号链接')
    for attempt in range(3):
        try:
            with tempfile.TemporaryDirectory(prefix='.download-', dir=str(cache)) as tmp:
                raw, result = Path(tmp) / 'response', Path(tmp) / 'verified'
                request = urllib.request.Request(url, headers={'User-Agent': 'LubanCat-Vision/1.0'})
                with urllib.request.urlopen(request, timeout=30) as response, raw.open('wb') as output:
                    if not response.geturl().startswith('https://'):
                        raise RuntimeError('拒绝非 HTTPS 下载重定向')
                    size = 0
                    for chunk in iter(lambda: response.read(256 * 1024), b''):
                        size += len(chunk)
                        if size > 64 * 1024 * 1024:
                            raise RuntimeError('下载超过大小限制：' + name)
                        output.write(chunk)
                if member:
                    # Read exactly the expected regular member. Never extract paths.
                    with tarfile.open(str(raw), 'r:gz') as archive:
                        item = archive.getmember(member)
                        if not item.isfile() or item.size > 32 * 1024 * 1024:
                            raise RuntimeError('模型归档成员类型或大小错误')
                        with archive.extractfile(item) as stream, result.open('wb') as output:
                            shutil.copyfileobj(stream, output)
                else:
                    raw.rename(result)
                if digest(result) != sha:
                    raise RuntimeError('SHA-256 不匹配：' + name)
                os.replace(str(result), str(dest))
                print('已下载并校验：' + name, flush=True)
                return dest
        except Exception:
            if attempt == 2:
                raise
            time.sleep(attempt + 1)


def download(cache, offline=False):
    return {a[0]: fetch(a, cache, offline) for a in ARTIFACTS}


def account(name):
    import pwd
    value = pwd.getpwnam(name)
    if value.pw_uid == 0 or not re.fullmatch(r'[a-z_][a-z0-9_-]*', name):
        raise RuntimeError('请选择已有的普通桌面用户，例如 --user cat')
    if not Path(value.pw_dir).is_dir() or any(c in value.pw_dir for c in '\n\r"%'):
        raise RuntimeError('桌面用户的主目录无效')
    return value


def service_running():
    result = subprocess.run(['systemctl', 'show', SERVICE, '-p', 'ActiveState', '--value'],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, universal_newlines=True)
    return result.stdout.strip() not in ('', 'inactive', 'failed')


def platform_check():
    if platform.system() != 'Linux' or platform.machine() != 'aarch64':
        raise RuntimeError('安装仅支持 aarch64 Linux；电脑上可先运行 download 准备离线缓存')
    release = dict(line.split('=', 1) for line in Path('/etc/os-release').read_text().splitlines() if '=' in line)
    if release.get('ID', '').strip('"') != 'debian' or release.get('VERSION_ID', '').strip('"') != '10':
        raise RuntimeError('当前固定依赖仅验证 Debian 10 厂商系统，请使用匹配镜像')
    if sys.version_info[:2] != (3, 7) or platform.release() != '4.19.232':
        raise RuntimeError('需要厂商 Python 3.7 / Linux 4.19.232；不会自动替换 Python 或内核')
    model = Path('/proc/device-tree/model').read_text().rstrip('\0')
    compat = Path('/proc/device-tree/compatible').read_bytes()
    if model != 'EmbedFire LubanCat-2' or b'rk3568' not in compat:
        raise RuntimeError('仅支持已验证的 LubanCat-2 / RK3568 板型')
    if not Path(CAMERA).exists() or not stat.S_ISCHR(Path(CAMERA).stat().st_mode):
        raise RuntimeError('未发现 CAM1 摄像头节点。先在厂商系统启用 OV8858 CAM1 设备树并重启；本工具不改启动分区')
    if not Path('/sys/bus/platform/drivers/rknpu').is_dir():
        raise RuntimeError('未发现 RKNPU 驱动；需要匹配的厂商内核，不能仅靠 pip 安装')
    if subprocess.run(['systemctl', 'is-active', '--quiet', 'rkaiq_3A.service']).returncode:
        raise RuntimeError('rkaiq_3A.service 未运行，请先修复摄像头 ISP/3A 环境')
    print('板型、系统、摄像头节点及 ISP/NPU 驱动前置检查通过')
    return model


def probe(user, code, target, timeout=90):
    env = dict(os.environ, DISPLAY=':0', XAUTHORITY=str(Path(user.pw_dir) / '.Xauthority'), PYTHONNOUSERSITE='1')
    env.pop('PYTHONPATH', None)
    prelude = 'def require(ok, message):\n    if not ok: raise RuntimeError(message)\n'
    command = ['/usr/bin/python3', '-c', prelude + code]
    if os.geteuid() == 0:
        command = ['runuser', '-u', user.pw_name, '--'] + command
    elif os.geteuid() != user.pw_uid:
        raise RuntimeError('检查其他桌面用户时需要 sudo')
    run(command, cwd=str(target), env=env, timeout=timeout)


def check(target, user, cpu=False, hardware=False):
    for name, _, sha, _ in ARTIFACTS:
        if name.startswith('wheels/'):
            continue
        path = target / name
        if not path.is_file() or path.is_symlink() or digest(path) != sha:
            raise RuntimeError('文件缺失或 SHA-256 不匹配：' + str(path))
    probe(user, '''import gi, cv2, numpy, cairo, qrcode, PIL, ctypes, os
gi.require_version('Gtk', '3.0'); gi.require_version('Gst', '1.0')
gi.require_version('PangoCairo', '1.0')
from gi.repository import Gtk, Gst, PangoCairo
Gst.init(None)
require(all(Gst.ElementFactory.find(n) for n in ('v4l2src','videoconvert','appsink')), '缺少 GStreamer 视频插件')
ctypes.CDLL('libzbar.so.0'); ctypes.CDLL('./lib/librknnrt.so')
require(os.access(%r, os.R_OK | os.W_OK), '桌面用户无摄像头访问权限')
paths=['/usr/share/opencv/haarcascades/','/usr/share/opencv4/haarcascades/']
require(any(all(os.path.isfile(p+n) for n in ('haarcascade_frontalface_default.xml','haarcascade_eye_tree_eyeglasses.xml')) for p in paths), '缺少 OpenCV 人脸/眼睛级联文件')
print('系统 Python、图形/视频依赖、扫码库及模型运行库加载通过')
''' % CAMERA, target)
    probe(user, '''import sys
sys.path.insert(0, 'gesture-runtime')
import numpy, cv2, tflite_runtime
from tflite_runtime.interpreter import Interpreter
require(numpy.__version__ == '1.21.6', '私有 NumPy 版本不匹配')
require(tflite_runtime.__version__ == '2.11.0', '私有 TFLite 版本不匹配')
for name in ('palm_detection_lite.tflite','hand_landmark_lite.tflite','movenet-lightning.tflite'):
    model=Interpreter(model_path='gesture-models/'+name,num_threads=1)
    model.allocate_tensors()
print('私有 NumPy/TFLite 版本和三个手部/人体模型加载通过')
''', target)
    if not cpu:
        # Isolate Mali from GTK/Mesa, as in the application itself.
        probe(user, "import ctypes,os; ctypes.CDLL('libmali.so.1'); require(os.access('/dev/mali0',os.R_OK|os.W_OK), '桌面用户无 Mali 设备权限'); print('Mali 库及设备权限通过')", target)
    shared = shutil.disk_usage('/dev/shm')
    if (shared.total if service_running() else shared.free) < 40 * 1024 * 1024:
        raise RuntimeError('/dev/shm 至少需要约 40 MiB 可用空间供预览和识别共享帧使用')
    font = subprocess.check_output(['fc-match', '-f', '%{family}', 'Noto Sans CJK SC'], universal_newlines=True)
    if 'Noto Sans CJK' not in font:
        raise RuntimeError('缺少 Noto Sans CJK 中文字体')
    if hardware:
        if service_running():
            raise RuntimeError('硬件实测需要独占摄像头，请先停止 liman-checkin 服务')
        probe(user, '''import cv2
from objects import ObjectDetector
cap=cv2.VideoCapture('v4l2src device=%s io-mode=mmap ! video/x-raw,format=BGRx,width=640,height=480,framerate=30/1 ! videoconvert ! video/x-raw,format=BGR ! appsink drop=true max-buffers=1 sync=false',cv2.CAP_GSTREAMER)
detector=None
try:
    ok,frame=cap.read()
    require(ok and frame is not None, '摄像头取帧失败')
    detector=ObjectDetector(); detector.detect(cv2.cvtColor(frame,cv2.COLOR_BGR2RGB))
    print('摄像头真实取帧及 NPU 推理通过；未保存图像')
finally:
    cap.release()
    if detector is not None:detector.close()
''' % CAMERA, target, timeout=30)
    print('依赖检查通过' + ('；已完成摄像头/NPU 实测' if hardware else '；尚未进行摄像头取帧、NPU 推理和 GPU 显示实测'))


def service_text(user, target, cpu):
    import grp
    group = grp.getgrgid(user.pw_gid).gr_name
    text = (ROOT / 'deploy/liman-checkin.service').read_text()
    text = text.replace('User=cat', 'User=' + user.pw_name).replace('Group=cat', 'Group=' + group)
    text = text.replace('/home/cat/.Xauthority', user.pw_dir + '/.Xauthority')
    text = text.replace('/opt/liman-checkin', str(target))
    return text.replace('UMask=0077', 'Environment=LIMAN_GPU_PREVIEW=' + ('0' if cpu else '1') + '\nUMask=0077')


def atomic_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', dir=str(path.parent), delete=False) as f:
        tmp = Path(f.name)
        f.write(text)
    try:
        tmp.chmod(0o644)
        os.replace(str(tmp), str(path))
    finally:
        if tmp.exists():tmp.unlink()


def validate_autologin(user):
    # lightdm.conf has higher priority than conf.d files.
    config = Path('/etc/lightdm/lightdm.conf')
    if config.exists():
        import configparser
        parsed = configparser.ConfigParser(strict=False, interpolation=None)
        parsed.read(str(config))
        for section in parsed.sections():
            if section.startswith('Seat'):
                value = parsed.get(section, 'autologin-user', fallback='').strip()
                if value and value != user.pw_name:
                    raise RuntimeError('lightdm.conf 的自动登录用户与 --user 不同，请先核对后再安装')


def install(args):
    if os.geteuid() != 0:
        raise RuntimeError('安装需要 sudo /usr/bin/python3 deploy.py install ...')
    platform_check()
    user = account(args.user)
    target = args.target
    if not target.is_absolute() or target.parent != Path('/opt') or target.is_symlink() or target.resolve() != target:
        raise RuntimeError('--target 必须是 /opt 下的直接子目录，且不能是符号链接')
    if any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-.' for c in target.name):
        raise RuntimeError('安装目录名只允许字母、数字、下划线、点和短横线')
    if target == ROOT or target in ROOT.parents or ROOT in target.parents:
        raise RuntimeError('请从独立的 Git checkout 运行安装，不要覆盖当前运行目录')
    if target.exists() and (not target.is_dir() or not (target / 'app.py').is_file()):
        raise RuntimeError('已有目标不是应用目录，拒绝覆盖：' + str(target))
    for name in SOURCE_FILES:
        if not (ROOT / name).is_file() or (ROOT / name).is_symlink():
            raise RuntimeError('源码不完整：' + name)
    unit = Path('/etc/systemd/system') / SERVICE
    if unit.exists() and ('ExecStart=/usr/bin/python3 ' + str(target) + '/app.py') not in unit.read_text():
        raise RuntimeError('已有同名服务指向其他位置，拒绝覆盖')
    active = service_running()
    if active and not args.replace_running:
        raise RuntimeError('程序正在运行；确认可中断后加 --replace-running，或先手动停止服务')
    if args.autologin:
        validate_autologin(user)
    cache = args.cache
    if cache.is_symlink():
        raise RuntimeError('缓存目录不能是符号链接')
    artifacts = download(cache, args.offline)
    if not args.offline:
        env = dict(os.environ, DEBIAN_FRONTEND='noninteractive')
        try:
            run(['apt-get', 'update'], env=env)
            run(['apt-get', 'install', '-y', '--no-install-recommends'] + list(PACKAGES), env=env)
        except subprocess.CalledProcessError:
            raise RuntimeError('系统依赖安装失败。Debian 10 已归档，请检查厂商 APT 源、网络与系统时间；本工具不自动替换软件源')
    else:
        print('离线安装跳过 APT；系统依赖须已预装，稍后会逐项检查')
    import grp
    groups = [g for g in ('video', 'render') if g in {x.gr_name for x in grp.getgrall()}]
    if groups:
        run(['usermod', '-a', '-G', ','.join(groups), user.pw_name])
    version = datetime.datetime.now(datetime.timezone.utc).strftime('V%Y%m%dT%H%M%SZ-') + os.urandom(3).hex()
    record_dir = Path('/var/lib/liman-vision-deploy') / version
    record_dir.mkdir(parents=True, mode=0o700)
    old_unit = unit.read_bytes() if unit.exists() else None
    auto = Path('/etc/lightdm/lightdm.conf.d/90-liman-vision.conf')
    old_auto = auto.read_bytes() if auto.exists() else None
    old_default = subprocess.check_output(['systemctl', 'get-default'], universal_newlines=True).strip()
    enabled = subprocess.run(['systemctl', 'is-enabled', '--quiet', SERVICE]).returncode == 0
    for name, data in [('previous.service', old_unit), ('previous-lightdm.conf', old_auto)]:
        if data is not None:
            (record_dir / name).write_bytes(data)
    previous = target.with_name(target.name + '.previous-' + version)
    stage = Path(tempfile.mkdtemp(prefix='.liman-stage-', dir=str(target.parent)))
    stage.chmod(0o755)
    switched = stopped = False
    record = {'version': version, 'purpose': 'Install LubanCat Vision application and pinned private dependencies',
              'device': 'EmbedFire LubanCat-2 / RK3568', 'hostname': platform.node(), 'kernel': platform.release(),
              'source': str(ROOT), 'target': str(target), 'user': user.pw_name,
              'previous_default_target': old_default,
              'baseline': str(previous) if target.exists() else None, 'status': 'preparing',
              'verification': {}, 'rollback': 'No automatic user rollback. Stop active tasks and preserve current state before restoring the previous application directory and service configuration. System packages/groups are not rolled back.'}
    try:
        try:
            record['source_commit'] = subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'],
                                                             stderr=subprocess.DEVNULL, universal_newlines=True).strip()
            record['source_branch'] = subprocess.check_output(['git', '-C', str(ROOT), 'branch', '--show-current'],
                                                             stderr=subprocess.DEVNULL, universal_newlines=True).strip()
        except (OSError, subprocess.CalledProcessError):
            record['source_commit'] = record['source_branch'] = None
        for name in SOURCE_FILES:
            dest = stage / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(str(ROOT / name), str(dest))
            dest.chmod(0o644)
        for name, path in artifacts.items():
            if name.startswith('wheels/'):
                continue
            dest = stage / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(str(path), str(dest))
            dest.chmod(0o644)
        wheel_dir = stage / '.install-wheels'
        wheel_dir.mkdir(mode=0o700)
        pinned = {name: sha for name, _, sha, _ in ARTIFACTS}
        wheels = []
        for name, path in artifacts.items():
            if not name.startswith('wheels/'):
                continue
            dest = wheel_dir / Path(name).name
            shutil.copyfile(str(path), str(dest))
            if digest(dest) != pinned[name]:
                raise RuntimeError('执行前 wheel 校验失败：' + name)
            if name == 'wheels/pip-23.1.2-py3-none-any.whl':
                pip = dest
            else:
                wheels.append(str(dest))
        # Use a pinned, private pip without upgrading the system's old pip/NumPy.
        run(['/usr/bin/python3', '-I', '-c', 'import sys,runpy; sys.path.insert(0,sys.argv.pop(1)); runpy.run_module("pip",run_name="__main__")', pip,
             'install', '--no-index', '--no-deps', '--no-compile', '--disable-pip-version-check',
             '--target', stage / 'gesture-runtime'] + wheels,
            env=dict({k: v for k, v in os.environ.items() if not k.startswith('PIP_')}, PIP_CONFIG_FILE=os.devnull))
        shutil.rmtree(str(wheel_dir))
        for folder, dirs, files in os.walk(str(stage)):
            os.chmod(folder, 0o755)
            for name in files:
                if not (Path(folder) / name).is_symlink():
                    os.chmod(str(Path(folder) / name), 0o644)
        if active:
            run(['systemctl', 'stop', SERVICE]); stopped = True
        check(stage, user, args.cpu_preview, args.smoke)
        record['verification']['dependencies'] = 'passed on staged files as desktop user'
        record['verification']['camera_npu'] = 'passed' if args.smoke else 'not tested'
        record['verification']['gpu_display'] = 'not tested; library/permissions checked unless CPU preview selected'
        record['verification']['physical_confirmation'] = 'not obtained'
        inventory = subprocess.run(['dpkg-query', '-W', '-f=${Package}\t${Version}\n'] + list(PACKAGES),
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        record['system_packages'] = inventory.stdout
        record['package_inventory_complete'] = inventory.returncode == 0
        record['files'] = {str(p.relative_to(stage)): digest(p) for p in stage.rglob('*') if p.is_file() and not p.is_symlink()}
        if active and not stopped:
            run(['systemctl', 'stop', SERVICE]); stopped = True
        if target.exists():
            target.rename(previous)
        try:
            stage.rename(target)
        except BaseException:
            if previous.exists():
                previous.rename(target)
            raise
        switched = True
        atomic_text(unit, service_text(user, target, args.cpu_preview))
        if args.autologin:
            atomic_text(auto, '[Seat:*]\nautologin-user=' + user.pw_name + '\nautologin-user-timeout=0\n')
            run(['systemctl', 'set-default', 'graphical.target'])
        run(['systemctl', 'daemon-reload'])
        run(['systemctl', 'enable', SERVICE])
        if args.start:
            probe(user, "import gi; gi.require_version('Gdk','3.0'); from gi.repository import Gdk; d=Gdk.Display.open(':0'); require(d is not None, '请先登录 X11 桌面；自动登录配置需下次登录或重启才生效'); d.close()", target)
            run(['systemctl', 'start', SERVICE])
            time.sleep(3)
            run(['systemctl', 'is-active', '--quiet', SERVICE])
            record['verification']['service'] = 'active after start; not complete UI/physical acceptance'
        elif stopped:
            run(['systemctl', 'start', SERVICE])
            record['verification']['service'] = 'previously running service restarted'
        else:
            record['verification']['service'] = 'enabled; not started'
        record['installed_configuration'] = {'service_sha256': digest(unit), 'display': ':0',
            'xauthority': str(Path(user.pw_dir) / '.Xauthority'), 'cpu_preview': args.cpu_preview,
            'autologin_configured': args.autologin,
            'lightdm_config_sha256': digest(auto) if args.autologin else None}
        record['status'] = 'installed'
        print('安装完成：' + str(target) + '\n版本记录：' + str(record_dir))
        print('开机服务已启用。未自动重启桌面或开发板；请现场确认画面与交互。')
    except BaseException:
        record['status'] = 'failed'
        if switched:
            # Keep failed staged software for diagnosis; never delete user data.
            subprocess.run(['systemctl', 'stop', SERVICE])
            target.rename(target.with_name(target.name + '.failed-' + version))
            if previous.exists():
                previous.rename(target)
            if old_unit is None:
                if unit.exists():unit.unlink()
            else:
                unit.write_bytes(old_unit)
            if args.autologin:
                if old_auto is None:
                    if auto.exists():auto.unlink()
                else:auto.write_bytes(old_auto)
                run(['systemctl', 'set-default', old_default])
            run(['systemctl', 'daemon-reload'])
            if not enabled:
                subprocess.run(['systemctl', 'disable', SERVICE], stdout=subprocess.DEVNULL)
        if stopped:
            recovery = subprocess.run(['systemctl', 'start', SERVICE])
            record['previous_service_restart_returncode'] = recovery.returncode
        raise
    finally:
        (record_dir / 'record.json').write_text(json.dumps(record, ensure_ascii=False, indent=2))
        if stage.exists():
            shutil.rmtree(str(stage))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='首次部署：sudo /usr/bin/python3 deploy.py install --user cat --autologin --start\n'
               '无桌面登录时先去掉 --start，登录/重启后由服务启动。\n'
               '检查：sudo /usr/bin/python3 deploy.py check --user cat [--smoke]\n'
               '离线缓存：python3 deploy.py download --cache ./vision-cache\n'
               '板端离线安装：sudo /usr/bin/python3 deploy.py install --offline --cache ./vision-cache --user cat\n'
               '前提：厂商 Debian 10 / 4.19.232 已启用 OV8858 CAM1、ISP 和 RKNPU 驱动。\n'
               '不修改内核/设备树、APT 源、系统 GPU 驱动或用户数据。\n'
               '首次联网安装的 APT 包使用厂商/系统源；精确包版本保存在该机 dpkg 数据库，未锁定跨镜像的系统包。')
    parser.add_argument('command', choices=('install', 'check', 'download'))
    parser.add_argument('--cache', type=Path, default=Path('/var/cache/liman-vision'))
    parser.add_argument('--target', type=Path, default=Path('/opt/liman-checkin'))
    parser.add_argument('--user', default='cat')
    parser.add_argument('--offline', action='store_true', help='不联网；缓存及系统依赖须已备齐')
    parser.add_argument('--autologin', action='store_true', help='配置 LightDM 下次自动登录所选用户')
    parser.add_argument('--start', action='store_true', help='安装后启动；需要所选用户已有 :0 X11 会话')
    parser.add_argument('--replace-running', action='store_true', help='允许安装最后阶段暂停并替换正在运行的程序')
    parser.add_argument('--cpu-preview', action='store_true', help='使用标准预览，不要求 Mali；YOLO 仍需要 NPU')
    parser.add_argument('--smoke', action='store_true', help='独占摄像头实际取帧并执行一次 NPU 推理，不保存画面')
    args = parser.parse_args()
    try:
        if args.command == 'download':
            download(args.cache, args.offline)
            print('全部模型、运行库和 Python wheel 的 SHA-256 校验通过')
        elif args.command == 'check':
            platform_check()
            check(args.target, account(args.user), args.cpu_preview, args.smoke)
        else:
            platform_check()
            import fcntl
            Path('/run/lock').mkdir(parents=True, exist_ok=True)
            with open('/run/lock/liman-vision-deploy.lock', 'w') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                install(args)
        return 0
    except (Exception, KeyboardInterrupt) as exc:
        print('部署未完成：' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
