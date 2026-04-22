import argparse
import json
from pathlib import Path
from typing import Dict, List, Tuple, Optional

import numpy as np
from PIL import Image, ImageDraw, ImageFilter
import onnxruntime as ort


# ---------- Helpers ----------

def np_softmax(logits: np.ndarray, axis: int = -1) -> np.ndarray:
    logits = logits - logits.max(axis=axis, keepdims=True)
    e = np.exp(logits)
    return e / e.sum(axis=axis, keepdims=True)


def np_sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-x))


def paste_black_rects(pil_img: Image.Image, boxes: np.ndarray) -> Image.Image:
    # Erwartet YOLOX-Boxen im Format (top, left, bottom, right)
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
    # Erwartet Boxen im Format [x1, y1, x2, y2]
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


# ---------- Weather (multilabel optional) ----------

class WeatherInfer:
    def __init__(
        self,
        onnx_path: Path,
        classes_path: Optional[Path],
        img_size: int = 136,
        use_gpu: bool = False,
        multilabel: bool = False,
    ):
        self.img_size = img_size
        self.multilabel = multilabel

        # Klassen-Mapping optional
        self.idx_to_class: Dict[int, str] = {}
        if classes_path and classes_path.exists():
            with classes_path.open("r", encoding="utf-8") as f:
                class_to_idx = json.load(f)
            self.idx_to_class = {v: k for k, v in class_to_idx.items()}

        providers = ["CPUExecutionProvider"]
        if use_gpu and "CUDAExecutionProvider" in ort.get_available_providers():
            providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]

        so = ort.SessionOptions()
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_BASIC
        so.intra_op_num_threads = 1
        so.inter_op_num_threads = 1
        so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        so.enable_mem_pattern = False
        so.enable_cpu_mem_arena = True
        self.sess = ort.InferenceSession(str(onnx_path), sess_options=so, providers=providers)

        self.MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
        self.STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)

        # Namen im Modell
        self.input_name = self.sess.get_inputs()[0].name
        self.output_name = self.sess.get_outputs()[0].name  # "logits"

    def _preprocess(self, img: Image.Image) -> np.ndarray:
        img = img.resize((self.img_size, self.img_size), resample=Image.BILINEAR)
        x = np.asarray(img).astype(np.float32) / 255.0  # HWC
        x = (x - self.MEAN) / self.STD
        x = np.transpose(x, (2, 0, 1))  # CHW
        x = np.expand_dims(x, axis=0)  # NCHW
        return x

    def predict_probs(self, img: Image.Image) -> Dict[str, float]:
        x = self._preprocess(img)
        logits = self.sess.run([self.output_name], {self.input_name: x})[0]  # [1, C]
        if self.multilabel:
            probs = np_sigmoid(logits)[0]
        else:
            probs = np_softmax(logits, axis=1)[0]
        if not self.idx_to_class:
            self.idx_to_class = {i: f"idx_{i}" for i in range(len(probs))}
        return {self.idx_to_class[i]: float(probs[i]) for i in range(len(probs))}


# ---------- YOLOX (Kennzeichen) ----------

class YOLOXDetector:
    # Erwartet ein ONNX mit zwei Inputs: Bild und Originalgröße; gibt (boxes, scores, classes) zurück
    def __init__(self, model_path: Path, classes_path: Path, size: int = 640):
        self.size = size

        self.class_labels = self._load_classes(classes_path)
        self.sess = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])

        ins = self.sess.get_inputs()
        self.in_img_name = ins[0].name
        self.in_shape_name = ins[1].name

    @staticmethod
    def _load_classes(path: Path) -> List[str]:
        with path.open("r", encoding="utf-8") as f:
            names = [line.strip() for line in f if line.strip()]
        return names

    def _letterbox_and_norm(self, img: Image.Image) -> Tuple[np.ndarray, np.ndarray]:
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

        orig_shape = np.expand_dims(np.array([ih, iw], dtype="float32"), 0)  # (H, W)
        return image_data, orig_shape

    def infer(self, img: Image.Image) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        x, orig_shape = self._letterbox_and_norm(img)
        out = self.sess.run(None, {self.in_img_name: x, self.in_shape_name: orig_shape})
        if len(out) >= 3:
            return out[0], out[1], out[2]
        raise RuntimeError("YOLOX ONNX-Ausgabe unerwartet. Erwartet 3 Ausgaben (boxes, scores, classes).")


# ---------- Head Detector ----------

class HeadDetector:
    def __init__(self, onnx_model_path: Path, confidence_threshold: float = 0.20):
        self.thr = float(confidence_threshold)
        self.sess = ort.InferenceSession(str(onnx_model_path), providers=["CPUExecutionProvider"])
        ins = self.sess.get_inputs()
        self.in_img_name = ins[0].name       # meist 'images'
        self.in_shape_name = ins[1].name     # meist 'orig_target_sizes'

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

        out = self.sess.run(None, {self.in_img_name: im_data, self.in_shape_name: orig_size})
        if len(out) < 3:
            raise RuntimeError("Head ONNX-Ausgabe unerwartet. Erwartet 3 Ausgaben (labels, boxes, scores).")
        labels, boxes, scores = out[0], out[1], out[2]

        scr = scores[0]
        mask = scr > self.thr
        box = boxes[0][mask]
        scr = scr[mask]
        return box, scr


# ---------- Pipeline ----------

def main():
    ap = argparse.ArgumentParser("Einzelbild-Pipeline (Detektionen zuerst, dann Modifikationen + JSON)")
    ap.add_argument("--input", required=True, help="Eingabebild")
    ap.add_argument("--output", required=True, help="Ausgabebild")

    # Weather
    ap.add_argument("--weather-onnx", default="weathernet.onnx")
    ap.add_argument("--classes", default="class_to_idx.json", help="optional: Mapping der Wetterklassen")
    ap.add_argument("--weather-img-size", type=int, default=136)
    ap.add_argument("--multilabel", action="store_true")
    ap.add_argument("--gpu", action="store_true", help="GPU nur fürs Wettermodell (falls verfügbar)")

    # YOLOX (Kennzeichen)
    ap.add_argument("--yolox-onnx", default="model.onnx")
    ap.add_argument("--yolox-classes", default="classes.txt")  # bei dir eine Zeile: 'licence'
    ap.add_argument("--yolox-size", type=int, default=640)
    ap.add_argument("--yolox-thresh", type=float, default=0.25)

    # Heads
    ap.add_argument("--head-onnx", default="head_model_640.onnx")
    ap.add_argument("--head-thresh", type=float, default=0.20)
    ap.add_argument("--head-padding", type=float, default=0.10)
    ap.add_argument("--head-blur", type=float, default=15.0)

    # Optionen
    ap.add_argument("--disable-weather", action="store_true")
    ap.add_argument("--disable-plates", action="store_true")
    ap.add_argument("--disable-heads", action="store_true")
    ap.add_argument("--save-json", action="store_true", help="Zusammenfassung als JSON neben dem Output speichern")
    args = ap.parse_args()

    base_dir = Path(__file__).resolve().parent
    in_path = Path(args.input)
    out_path = Path(args.output)
    if not in_path.is_file():
        raise SystemExit(f"Eingabedatei nicht gefunden: {in_path}")

    # Bild laden
    pil = Image.open(in_path).convert("RGB")

    # ---- 1) Erkennungen (nur berechnen) ----
    results = {"file": str(in_path), "weather": {}, "yolox": [], "heads": []}

    # Weather
    if not args.disable_weather:
        classes_path = base_dir / args.classes
        if not classes_path.exists():
            classes_path = None
        weather = WeatherInfer(
            onnx_path=base_dir / args.weather_onnx,
            classes_path=classes_path,
            img_size=args.weather_img_size,
            use_gpu=args.gpu,
            multilabel=args.multilabel,
        )
        weather_probs = weather.predict_probs(pil)
        results["weather"] = weather_probs

    # YOLOX (Kennzeichen)
    yolo_boxes_keep = np.empty((0, 4), dtype=np.float32)
    if not args.disable_plates:
        yolox = YOLOXDetector(
            model_path=base_dir / args.yolox_onnx,
            classes_path=base_dir / args.yolox_classes,
            size=args.yolox_size,
        )
        box_out, scores_out, classes_out = yolox.infer(pil)
        if scores_out is not None and len(scores_out) > 0:
            keep = scores_out >= float(args.yolox_thresh)
            yolo_boxes_keep = box_out[keep]
            # Ergebnisse sammeln
            for i in np.where(keep)[0]:
                cls_idx = int(classes_out[i])
                results["yolox"].append({
                    "class": yolox.class_labels[cls_idx] if 0 <= cls_idx < len(yolox.class_labels) else f"cls_{cls_idx}",
                    "score": float(scores_out[i]),
                    "box": [float(v) for v in box_out[i].tolist()],  # (top,left,bottom,right)
                })

    # Heads
    head_boxes = np.empty((0, 4), dtype=np.float32)
    if not args.disable_heads:
        heads = HeadDetector(
            onnx_model_path=base_dir / args.head_onnx,
            confidence_threshold=args.head_thresh,
        )
        head_boxes, head_scores = heads.detect(pil)
        for bi, sc in zip(head_boxes, head_scores):
            results["heads"].append({
                "score": float(sc),
                "box": [float(v) for v in bi.tolist()],  # [x1,y1,x2,y2]
            })

    # ---- 2) Modifikationen ----
    current = pil
    if yolo_boxes_keep.size > 0:
        current = paste_black_rects(current, yolo_boxes_keep)
    if head_boxes.size > 0:
        current = blur_heads_elliptical(current, head_boxes, padding_factor=args.head_padding, blur_factor=args.head_blur)

    # ---- 3) Ausgabe ----
    out_path.parent.mkdir(parents=True, exist_ok=True)
    current.save(out_path)
    print(f"OK: {in_path.name} -> {out_path.name}")

    if args.save_json:
        json_path = out_path.parent / (out_path.stem + "_pipeline.json")
        with json_path.open("w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        print(f"JSON gespeichert: {json_path.name}")


if __name__ == "__main__":
    main()