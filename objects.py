"""RKNN YOLOv5 inference using the board's existing RKNN runtime."""
import ctypes as C
from pathlib import Path
import cv2
import numpy as np

LABELS = ['人', '自行车', '汽车', '摩托车', '飞机', '公交车', '火车', '卡车', '船', '交通灯',
 '消防栓', '停车标志', '停车计时器', '长椅', '鸟', '猫', '狗', '马', '羊', '牛',
 '大象', '熊', '斑马', '长颈鹿', '背包', '雨伞', '手提包', '领带', '行李箱', '飞盘',
 '滑雪板', '单板滑雪板', '球', '风筝', '棒球棒', '棒球手套', '滑板', '冲浪板', '网球拍', '瓶子',
 '酒杯', '杯子', '叉子', '刀', '勺子', '碗', '香蕉', '苹果', '三明治', '橙子',
 '西兰花', '胡萝卜', '热狗', '披萨', '甜甜圈', '蛋糕', '椅子', '沙发', '盆栽', '床',
 '餐桌', '马桶', '电视', '笔记本电脑', '鼠标', '遥控器', '键盘', '手机', '微波炉', '烤箱',
 '烤面包机', '水槽', '冰箱', '书', '时钟', '花瓶', '剪刀', '泰迪熊', '吹风机', '牙刷']


class TensorAttr(C.Structure):
    _fields_ = [('index', C.c_uint32), ('n_dims', C.c_uint32), ('dims', C.c_uint32*16),
        ('name', C.c_char*256), ('n_elems', C.c_uint32), ('size', C.c_uint32),
        ('fmt', C.c_int), ('type', C.c_int), ('qnt_type', C.c_int), ('fl', C.c_int8),
        ('zp', C.c_int32), ('scale', C.c_float), ('w_stride', C.c_uint32),
        ('size_with_stride', C.c_uint32), ('pass_through', C.c_uint8), ('h_stride', C.c_uint32)]


class Input(C.Structure):
    _fields_ = [('index', C.c_uint32), ('buf', C.c_void_p), ('size', C.c_uint32),
        ('pass_through', C.c_uint8), ('type', C.c_int), ('fmt', C.c_int)]


class Output(C.Structure):
    _fields_ = [('want_float', C.c_uint8), ('is_prealloc', C.c_uint8), ('index', C.c_uint32),
        ('buf', C.c_void_p), ('size', C.c_uint32)]


def check(code, operation):
    if code != 0:
        raise RuntimeError('{} 返回错误 {}'.format(operation, code))


def nms(boxes, scores, classes, threshold=.45, maximum=50):
    if not len(boxes):
        return []
    order = np.argsort(scores)[::-1]
    keep = []
    areas = np.maximum(0, boxes[:, 2]-boxes[:, 0]) * np.maximum(0, boxes[:, 3]-boxes[:, 1])
    while order.size and len(keep) < maximum:
        i = order[0]
        keep.append(int(i))
        rest = order[1:]
        tl = np.maximum(boxes[i, :2], boxes[rest, :2])
        br = np.minimum(boxes[i, 2:], boxes[rest, 2:])
        wh = np.maximum(0., br-tl)
        inter = wh[:, 0]*wh[:, 1]
        iou = inter / np.maximum(areas[i]+areas[rest]-inter, 1e-6)
        order = rest[(iou <= threshold) | (classes[rest] != classes[i])]
    return keep


def postprocess(outputs, original_shape, gain, pad, threshold=.45):
    anchors = [[[10,13],[16,30],[33,23]], [[30,61],[62,45],[59,119]], [[116,90],[156,198],[373,326]]]
    all_boxes, all_scores, all_classes = [], [], []
    for output, anchor in zip(outputs, anchors):
        grid = output.shape[-1]
        values = output.reshape(3, 85, grid, grid).transpose(0, 2, 3, 1)
        mask = values[..., 4] >= threshold
        ai, gy, gx = np.nonzero(mask)
        if not len(ai):
            continue
        selected = values[mask]
        classes = selected[:, 5:].argmax(axis=1)
        scores = selected[:, 4] * selected[np.arange(len(ai)), 5+classes]
        good = scores >= threshold
        if not np.any(good):
            continue
        selected, scores, classes = selected[good], scores[good], classes[good]
        ai, gx, gy = ai[good], gx[good], gy[good]
        xy = (selected[:, :2]*2.-.5 + np.stack([gx, gy], axis=1))*(640./grid)
        wh = (selected[:, 2:4]*2.)**2 * np.asarray(anchor)[ai]
        boxes = np.concatenate([xy-wh/2., xy+wh/2.], axis=1)
        all_boxes.append(boxes)
        all_scores.append(scores)
        all_classes.append(classes)
    if not all_boxes:
        return []
    boxes, scores, classes = np.concatenate(all_boxes), np.concatenate(all_scores), np.concatenate(all_classes)
    boxes[:, [0, 2]] = np.clip((boxes[:, [0, 2]]-pad[0])/gain, 0, original_shape[1]-1)
    boxes[:, [1, 3]] = np.clip((boxes[:, [1, 3]]-pad[1])/gain, 0, original_shape[0]-1)
    return [(int(classes[i]), float(scores[i]), *map(float, boxes[i]))
        for i in nms(boxes, scores, classes)]


class ObjectDetector:
    def __init__(self, model=None):
        private_runtime = Path(__file__).parent / 'lib/librknnrt.so'
        self.lib = C.CDLL(str(private_runtime) if private_runtime.exists() else '/usr/lib/librknnrt.so')
        signatures = {
            'rknn_init': ([C.POINTER(C.c_uint64), C.c_void_p, C.c_uint32, C.c_uint32, C.c_void_p], C.c_int),
            'rknn_query': ([C.c_uint64, C.c_int, C.c_void_p, C.c_uint32], C.c_int),
            'rknn_inputs_set': ([C.c_uint64, C.c_uint32, C.POINTER(Input)], C.c_int),
            'rknn_run': ([C.c_uint64, C.c_void_p], C.c_int),
            'rknn_outputs_get': ([C.c_uint64, C.c_uint32, C.POINTER(Output), C.c_void_p], C.c_int),
            'rknn_outputs_release': ([C.c_uint64, C.c_uint32, C.POINTER(Output)], C.c_int),
            'rknn_destroy': ([C.c_uint64], C.c_int),
        }
        for name, (args, result) in signatures.items():
            fn = getattr(self.lib, name)
            fn.argtypes, fn.restype = args, result
        self.ctx = C.c_uint64(0)
        model = Path(model or Path(__file__).parent / 'models/yolov5s-640-640.rknn')
        raw = model.read_bytes()
        buffer = C.create_string_buffer(raw)
        check(self.lib.rknn_init(C.byref(self.ctx), buffer, len(raw), 0, None), 'NPU 初始化')
        try:
            num = (C.c_uint32*2)()
            check(self.lib.rknn_query(self.ctx, 0, num, C.sizeof(num)), '查询模型')
            if tuple(num) != (1, 3):
                raise RuntimeError('模型输入输出与 YOLOv5 不一致')
            self.attrs = []
            for i in range(3):
                attr = TensorAttr(index=i)
                check(self.lib.rknn_query(self.ctx, 2, C.byref(attr), C.sizeof(attr)), '查询输出')
                dims = tuple(attr.dims[:attr.n_dims])
                if dims != (1, 255, 80//(2**i), 80//(2**i)):
                    raise RuntimeError('模型输出尺寸异常：{}'.format(dims))
                self.attrs.append(attr)
            self.input_image = np.full((640, 640, 3), 114, np.uint8)
        except Exception:
            self.close()
            raise

    def detect(self, rgb, threshold=.45):
        height, width = rgb.shape[:2]
        gain = min(640./width, 640./height)
        new_w, new_h = round(width*gain), round(height*gain)
        left, top = (640-new_w)//2, (640-new_h)//2
        image = self.input_image
        image.fill(114)
        image[top:top+new_h, left:left+new_w] = (
            rgb if (new_w, new_h) == (width, height)
            else cv2.resize(rgb, (new_w, new_h)))
        item = Input(index=0, buf=image.ctypes.data, size=image.nbytes, pass_through=0, type=3, fmt=1)
        check(self.lib.rknn_inputs_set(self.ctx, 1, C.byref(item)), '提交画面')
        check(self.lib.rknn_run(self.ctx, None), '物体检测')
        results = (Output*3)()
        for i in range(3):
            results[i].index, results[i].want_float = i, 1
        check(self.lib.rknn_outputs_get(self.ctx, 3, results, None), '获取检测结果')
        try:
            outputs = []
            for result, attr in zip(results, self.attrs):
                if not result.buf or result.size < attr.n_elems*4:
                    raise RuntimeError('模型返回的数据不完整')
                outputs.append(np.ctypeslib.as_array(C.cast(result.buf, C.POINTER(C.c_float)),
                    shape=(attr.n_elems,)).reshape(tuple(attr.dims[:attr.n_dims])))
            # Decode views while RKNN still owns the buffers, then release them.
            return postprocess(outputs, rgb.shape, gain, (left, top), threshold)
        finally:
            self.lib.rknn_outputs_release(self.ctx, 3, results)

    def close(self):
        if self.ctx.value:
            self.lib.rknn_destroy(self.ctx)
            self.ctx.value = 0
