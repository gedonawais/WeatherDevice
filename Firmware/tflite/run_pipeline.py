#!/usr/bin/env python3
import argparse
import json
from pathlib import Path
from typing import Dict, List, Tuple, Optional
import time

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

# TFLite statt ONNX Runtime
try:
    import tflite_runtime.interpreter as tflite
except ImportError:
    import tensorflow as tf
    tflite = tf.lite


# ---------- Helpers (unverändert) ----------

def np_softmax(logits: np.ndarray, axis: int = -1) -> np.ndarray:
    logits = logits - logits.max(axis=axis, keepdims=True)
    e = np.exp(logits)
    return e / e.sum(axis=axis, keepdims=True)


def np_sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-x))


def paste_black_rects(pil_img: Image.Image, boxes: np.ndarray) -> Image.Image:
    out = pil_img.copy()
    drw = ImageDraw.Draw(out)
    W, H = out.size
    for b in boxes:
        top, left, bottom, right = map(float, b)
        l = int(max(0, min(left, right)))
        r = int(min(W, max(left, right)))
        t = int(max(0, min(top, bottom)))
        bt = int(min(H, max(top, bottom)))
        if l >= r or t >= bt:
            continue
        drw.rectangle([l, t, r, bt], fill=(0, 0, 0))
    return out


def blur_heads_elliptical(
    image: Image.Image,
    boxes: np.ndarray,
    padding_factor: float = 0.10,
    blur_factor: float = 15.0,
) -> Image.Image:
    result_image = image.copy()
    mask = Image.new('L', image.size, 0)
    draw = ImageDraw.Draw(mask)

    for box in boxes:
        x1, y1, x2, y2 = map(float, box)
        width = x2 - x1
        height = y2 - y1
        if width <= 0 or height <= 0:
            continue

        pad_w = padding_factor * width
        pad_h = padding_factor * height

        x1n = max(0.0, x1 - pad_w)
        y1n = max(0.0, y1 - pad_h)
        x2n = min(image.width, x2 + pad_w)
        y2n = min(image.height, y2 + pad_h)

        l, t, r, b = map(int, [x1n, y1n, x2n, y2n])
        if l >= r or t >= b:
            continue
        draw.ellipse([l, t, r, b], fill=255)

    blurred = image.filter(ImageFilter.GaussianBlur(radius=blur_factor))
    result_image = Image.composite(blurred, image, mask)
    return result_image


def parse_size_arg(s: Optional[str]) -> Optional[Tuple[int, int]]:
    if s is None:
        return None
    if 'x' in s:
        parts = s.lower().split('x')
    elif ',' in s:
        parts = s.split(',')
    else:
        raise argparse.ArgumentTypeError("output-size must be in format WIDTHxHEIGHT (z.B. 1280x720)")
    if len(parts) != 2:
        raise argparse.ArgumentTypeError("output-size must be in format WIDTHxHEIGHT (z.B. 1280x720)")
    try:
        w = int(parts[0])
        h = int(parts[1])
    except ValueError:
        raise argparse.ArgumentTypeError("output-size values must be integers")
    if w <= 0 or h <= 0:
        raise argparse.ArgumentTypeError("output-size values must be positive")
    return (w, h)


def scale_boxes(
    boxes: np.ndarray,
    from_size: Tuple[int, int],
    to_size: Tuple[int, int],
    fmt: str = "yolox",
) -> np.ndarray:
    if boxes is None or boxes.size == 0:
        return boxes.copy() if isinstance(boxes, np.ndarray) else np.empty((0, 4), dtype=np.float32)
    from_w, from_h = from_size
    to_w, to_h = to_size
    if from_w == 0 or from_h == 0:
        raise ValueError("from_size must be non-zero")
    sx = to_w / float(from_w)
    sy = to_h / float(from_h)
    boxes = np.array(boxes, dtype=np.float32)
    scaled = boxes.copy()
    if fmt == "yolox":
        scaled[:, 0] = boxes[:, 0] * sy
        scaled[:, 1] = boxes[:, 1] * sx
        scaled[:, 2] = boxes[:, 2] * sy
        scaled[:, 3] = boxes[:, 3] * sx
    elif fmt == "head":
        scaled[:, 0] = boxes[:, 0] * sx
        scaled[:, 1] = boxes[:, 1] * sy
        scaled[:, 2] = boxes[:, 2] * sx
        scaled[:, 3] = boxes[:, 3] * sy
    else:
        raise ValueError("Unknown fmt for scale_boxes")
    return scaled


# ---------- Weather (TFLite Version) ----------

class WeatherInfer:
    def __init__(
        self,
        tflite_path: Path,
        classes_path: Optional[Path],
        img_size: int = 136,
        multilabel: bool = False,
    ):
        self.img_size = img_size
        self.multilabel = multilabel

        self.idx_to_class: Dict[int, str] = {}
        if classes_path and classes_path.exists():
            with classes_path.open("r", encoding="utf-8") as f:
                class_to_idx = json.load(f)
            self.idx_to_class = {v: k for k, v in class_to_idx.items()}

        # TFLite Interpreter
        self.interpreter = tflite.Interpreter(model_path=str(tflite_path))
        self.interpreter.allocate_tensors()

        self.input_details = self.interpreter.get_input_details()
        self.output_details = self.interpreter.get_output_details()
        self.input_name = self.input_details[0]['name']
        self.output_name = self.output_details[0]['name']

        self.MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
        self.STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)

    def _preprocess(self, img: Image.Image) -> np.ndarray:
        img = img.resize((self.img_size, self.img_size), resample=Image.BILINEAR)
        x = np.asarray(img).astype(np.float32) / 255.0
        x = (x - self.MEAN) / self.STD
        x = np.transpose(x, (2, 0, 1))
        x = np.expand_dims(x, axis=0)
        return x

    def predict_probs(self, img: Image.Image) -> Dict[str, float]:
        x = self._preprocess(img)
        self.interpreter.set_tensor(self.input_details[0]['index'], x)
        self.interpreter.invoke()
        logits = self.interpreter.get_tensor(self.output_details[0]['index'])
        
        if self.multilabel:
            probs = np_sigmoid(logits)[0]
        else:
            probs = np_softmax(logits, axis=1)[0]
            
        if not self.idx_to_class:
            self.idx_to_class = {i: f"idx_{i}" for i in range(len(probs))}
        return {self.idx_to_class[i]: float(probs[i]) for i in range(len(probs))}


# ---------- YOLOX (TFLite Version) ----------

class YOLOXDetector:
    def __init__(self, model_path: Path, classes_path: Path, size: int = 640, thresh: float = 0.25):
        self.size = size
        self.thresh = thresh
        self.class_labels = self._load_classes(classes_path)
        
        self.interpreter = tflite.Interpreter(model_path=str(model_path))
        self.interpreter.allocate_tensors()
        
        self.input_details = self.interpreter.get_input_details()
        self.output_details = self.interpreter.get_output_details()

    @staticmethod
    def _load_classes(path: Path) -> List[str]:
        with path.open("r", encoding="utf-8") as f:
            names = [line.strip() for line in f if line.strip()]
        return names

    def _letterbox_and_norm(self, img: Image.Image) -> np.ndarray:
        iw, ih = img.size
        w = h = self.size
        scale = min(w / iw, h / ih)
        nw, nh = int(iw * scale), int(ih * scale)
        resized = img.resize((nw, nh), Image.BICUBIC)
        canvas = Image.new("RGB", (w, h), (128, 128, 128))
        canvas.paste(resized, ((w - nw) // 2, (h - nh) // 2))
        image_data = np.expand_dims(np.array(canvas, dtype="float32"), 0)
        image_data /= 255.0
        image_data -= np.array([0.485, 0.456, 0.406])
        image_data /= np.array([0.229, 0.224, 0.225])
        return image_data

    def infer(self, img: Image.Image) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        x = self._letterbox_and_norm(img)
        self.interpreter.set_tensor(self.input_details[0]['index'], x)
        self.interpreter.invoke()
        
        # Je nach Modell können die Ausgänge unterschiedlich sein
        outputs = []
        for detail in self.output_details:
            outputs.append(self.interpreter.get_tensor(detail['index']))
        
        if len(outputs) >= 3:
            return outputs[0], outputs[1], outputs[2]
        raise RuntimeError("YOLOX TFLite Ausgabe unerwartet")


# ---------- Head Detector (TFLite Version) ----------

class HeadDetector:
    def __init__(self, tflite_path: Path, confidence_threshold: float = 0.20):
        self.thr = float(confidence_threshold)
        self.interpreter = tflite.Interpreter(model_path=str(tflite_path))
        self.interpreter.allocate_tensors()
        
        self.input_details = self.interpreter.get_input_details()
        self.output_details = self.interpreter.get_output_details()

    @staticmethod
    def _preprocess(img: Image.Image, target_size=(640, 640)) -> np.ndarray:
        im_resized = img.resize(target_size, Image.LANCZOS)
        arr = np.array(im_resized, dtype=np.float32)
        arr = np.expand_dims(np.transpose(arr / 255.0, (2, 0, 1)), axis=0)
        return arr

    def detect(self, img: Image.Image) -> Tuple[np.ndarray, np.ndarray]:
        w, h = img.size
        orig_size = np.array([[w, h]], dtype=np.int64)
        im_data = self._preprocess(img)
        
        self.interpreter.set_tensor(self.input_details[0]['index'], im_data)
        self.interpreter.set_tensor(self.input_details[1]['index'], orig_size)
        self.interpreter.invoke()
        
        outputs = []
        for detail in self.output_details:
            outputs.append(self.interpreter.get_tensor(detail['index']))
        
        if len(outputs) < 3:
            raise RuntimeError("Head TFLite Ausgabe unerwartet")
        
        labels, boxes, scores = outputs[0], outputs[1], outputs[2]
        scr = scores[0]
        mask = scr > self.thr
        box = boxes[0][mask]
        scr = scr[mask]
        return box, scr


# ---------- Pipeline (angepasst) ----------

def main():
    ap = argparse.ArgumentParser("Einzelbild-Pipeline mit TFLite auf Raspberry Pi Zero")
    ap.add_argument("--input", required=True, help="Eingabebild")
    ap.add_argument("--output", required=True, help="Ausgabebild")

    # Weather
    ap.add_argument("--weather-tflite", default="weathernet_float16.tflite")
    ap.add_argument("--classes", default="class_to_idx.json")
    ap.add_argument("--weather-img-size", type=int, default=136)
    ap.add_argument("--multilabel", action="store_true")

    # YOLOX (Kennzeichen)
    ap.add_argument("--yolox-tflite", default="model_float16.tflite")
    ap.add_argument("--yolox-classes", default="classes.txt")
    ap.add_argument("--yolox-size", type=int, default=640)
    ap.add_argument("--yolox-thresh", type=float, default=0.25)

    # Heads
    ap.add_argument("--head-tflite", default="head_model_640_float16.tflite")
    ap.add_argument("--head-thresh", type=float, default=0.20)
    ap.add_argument("--head-padding", type=float, default=0.10)
    ap.add_argument("--head-blur", type=float, default=15.0)

    ap.add_argument("--output-size", type=str, default=None)
    ap.add_argument("--disable-weather", action="store_true")
    ap.add_argument("--disable-plates", action="store_true")
    ap.add_argument("--disable-heads", action="store_true")
    ap.add_argument("--save-json", action="store_true")
    ap.add_argument("--benchmark", action="store_true", help="Zeige Inferenz-Zeiten")
    args = ap.parse_args()

    base_dir = Path(__file__).resolve().parent
    in_path = Path(args.input)
    out_path = Path(args.output)
    if not in_path.is_file():
        raise SystemExit(f"Eingabedatei nicht gefunden: {in_path}")

    pil = Image.open(in_path).convert("RGB")
    orig_w, orig_h = pil.size

    out_size = parse_size_arg(args.output_size)
    if out_size is None:
        out_w, out_h = orig_w, orig_h
    else:
        out_w, out_h = out_size

    results = {"file": str(in_path), "weather": {}, "yolox": [], "heads": [], "output_size": [out_w, out_h]}
    timings = {}

    # ---- Weather ----
    if not args.disable_weather:
        start = time.time()
        classes_path = base_dir / args.classes
        if not classes_path.exists():
            classes_path = None
        weather = WeatherInfer(
            tflite_path=base_dir / args.weather_tflite,
            classes_path=classes_path,
            img_size=args.weather_img_size,
            multilabel=args.multilabel,
        )
        weather_probs = weather.predict_probs(pil)
        results["weather"] = weather_probs
        timings["weather"] = time.time() - start

    # ---- YOLOX ----
    yolo_boxes_keep = np.empty((0, 4), dtype=np.float32)
    if not args.disable_plates:
        start = time.time()
        yolox = YOLOXDetector(
            model_path=base_dir / args.yolox_tflite,
            classes_path=base_dir / args.yolox_classes,
            size=args.yolox_size,
        )
        box_out, scores_out, classes_out = yolox.infer(pil)
        if scores_out is not None and len(scores_out) > 0:
            keep = scores_out >= float(args.yolox_thresh)
            yolo_boxes_keep = box_out[keep]
            for i in np.where(keep)[0]:
                cls_idx = int(classes_out[i])
                results["yolox"].append({
                    "class": yolox.class_labels[cls_idx] if 0 <= cls_idx < len(yolox.class_labels) else f"cls_{cls_idx}",
                    "score": float(scores_out[i]),
                    "box": [float(v) for v in box_out[i].tolist()],
                })
        timings["yolox"] = time.time() - start

    # ---- Heads ----
    head_boxes = np.empty((0, 4), dtype=np.float32)
    if not args.disable_heads:
        start = time.time()
        heads = HeadDetector(
            tflite_path=base_dir / args.head_tflite,
            confidence_threshold=args.head_thresh,
        )
        head_boxes, head_scores = heads.detect(pil)
        for bi, sc in zip(head_boxes, head_scores):
            results["heads"].append({
                "score": float(sc),
                "box": [float(v) for v in bi.tolist()],
            })
        timings["heads"] = time.time() - start

    # ---- Skalierung und Modifikation ----
    yolo_boxes_scaled = np.empty((0, 4), dtype=np.float32)
    head_boxes_scaled = np.empty((0, 4), dtype=np.float32)

    if yolo_boxes_keep is not None and yolo_boxes_keep.size > 0:
        yolo_boxes_scaled = scale_boxes(yolo_boxes_keep, (orig_w, orig_h), (out_w, out_h), fmt="yolox")
        for i, entry in enumerate(results["yolox"]):
            entry["box_scaled"] = [float(v) for v in yolo_boxes_scaled[i].tolist()]

    if head_boxes is not None and head_boxes.size > 0:
        head_boxes_scaled = scale_boxes(head_boxes, (orig_w, orig_h), (out_w, out_h), fmt="head")
        for i, entry in enumerate(results["heads"]):
            entry["box_scaled"] = [float(v) for v in head_boxes_scaled[i].tolist()]

    current = pil.resize((out_w, out_h), Image.LANCZOS)

    if yolo_boxes_scaled.size > 0:
        current = paste_black_rects(current, yolo_boxes_scaled)
    if head_boxes_scaled.size > 0:
        current = blur_heads_elliptical(current, head_boxes_scaled, padding_factor=args.head_padding, blur_factor=args.head_blur)

    # ---- Ausgabe ----
    out_path.parent.mkdir(parents=True, exist_ok=True)
    current.save(out_path)
    print(f"OK: {in_path.name} -> {out_path.name} (output size: {out_w}x{out_h})")

    if args.benchmark and timings:
        print("\n--- Inferenz-Zeiten ---")
        for name, t in timings.items():
            print(f"{name}: {t:.3f}s")
        print(f"Gesamt: {sum(timings.values()):.3f}s")

    if args.save_json:
        json_path = out_path.parent / (out_path.stem + "_pipeline.json")
        with json_path.open("w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        print(f"JSON gespeichert: {json_path.name}")


if __name__ == "__main__":
    main()