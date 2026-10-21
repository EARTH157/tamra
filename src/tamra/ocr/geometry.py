"""Image geometry for OCR, in numpy only.

The detection post-processing and the line crop follow RapidOCR 3.10.0
(https://github.com/RapidAI/RapidOCR, Apache-2.0): ch_ppocr_det/utils.py (DBPostProcess,
get_mini_boxes, box_score_fast, unclip, order_points_clockwise) and
utils/process_img.py (get_rotate_crop_image). OpenCV, pyclipper and shapely calls are
replaced by the numpy equivalents below.
"""

import math

import numpy as np

Box = np.ndarray  # (4, 2) float64 corners: top-left, top-right, bottom-right, bottom-left


def resize_bilinear(img: np.ndarray, h: int, w: int) -> np.ndarray:
    """Bilinear resize of an HxW or HxWxC image to float32, like cv2.INTER_LINEAR."""
    H, W = img.shape[:2]
    if (H, W) == (h, w):
        return img.astype(np.float32)
    ys = np.clip((np.arange(h) + 0.5) * H / h - 0.5, 0, H - 1)
    xs = np.clip((np.arange(w) + 0.5) * W / w - 0.5, 0, W - 1)
    y0, x0 = np.floor(ys).astype(int), np.floor(xs).astype(int)
    y1, x1 = np.minimum(y0 + 1, H - 1), np.minimum(x0 + 1, W - 1)
    f = img.astype(np.float32)
    if f.ndim == 2:
        f = f[:, :, None]
    wy, wx = (ys - y0)[:, None, None], (xs - x0)[None, :, None]
    top = f[y0][:, x0] * (1 - wx) + f[y0][:, x1] * wx
    bottom = f[y1][:, x0] * (1 - wx) + f[y1][:, x1] * wx
    out = top * (1 - wy) + bottom * wy
    return out[:, :, 0] if img.ndim == 2 else out


def components(mask: np.ndarray) -> list[np.ndarray]:
    """4-connected components of a bool mask, each as an (n, 3) int array of row runs
    (y, x_start, x_end) with x_end exclusive. Row runs plus union-find: no per-pixel loop."""
    parent: list[int] = []

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    runs: list[tuple[int, int, int, int]] = []
    previous: list[tuple[int, int, int]] = []
    padded = np.zeros((mask.shape[0], mask.shape[1] + 2), dtype=np.int8)
    padded[:, 1:-1] = mask
    edges = np.diff(padded, axis=1)
    for y in range(mask.shape[0]):
        starts = np.flatnonzero(edges[y] == 1)
        if not len(starts):
            previous = []
            continue
        ends = np.flatnonzero(edges[y] == -1)
        current = []
        j = 0
        for s, e in zip(starts.tolist(), ends.tolist(), strict=True):
            rid = len(parent)
            parent.append(rid)
            while j < len(previous) and previous[j][1] <= s:
                j += 1
            k = j
            while k < len(previous) and previous[k][0] < e:
                a, b = find(previous[k][2]), find(rid)
                if a != b:
                    parent[b] = a
                k += 1
            current.append((s, e, rid))
            runs.append((y, s, e, rid))
        previous = current
    groups: dict[int, list[tuple[int, int, int]]] = {}
    for y, s, e, rid in runs:
        groups.setdefault(find(rid), []).append((y, s, e))
    return [np.asarray(g, dtype=np.int64) for g in groups.values()]


def run_points(runs: np.ndarray) -> np.ndarray:
    """The pixel centres that bound a component: both ends of every run, as (x, y)."""
    ys = runs[:, 0].astype(np.float64)
    return np.concatenate(
        [np.stack([runs[:, 1], ys], 1), np.stack([runs[:, 2] - 1, ys], 1)]
    ).astype(np.float64)


def convex_hull(points: np.ndarray) -> np.ndarray:
    """Counter-clockwise convex hull (Andrew's monotone chain) of (n, 2) points."""
    pts = np.unique(points, axis=0)
    if len(pts) <= 2:
        return pts

    def cross(o, a, b) -> float:
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower: list = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper: list = []
    for p in pts[::-1]:
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return np.asarray(lower[:-1] + upper[:-1])


def min_area_rect(points: np.ndarray) -> tuple[Box, float]:
    """cv2.minAreaRect + boxPoints + RapidOCR's get_mini_boxes ordering: the corners of the
    smallest rotated rectangle around the points, and its shorter side."""
    hull = convex_hull(points)
    if len(hull) == 1:
        return np.repeat(hull, 4, axis=0).astype(np.float64), 0.0
    best = None
    for i in range(len(hull)):
        edge = hull[(i + 1) % len(hull)] - hull[i]
        length = math.hypot(edge[0], edge[1])
        if length == 0:
            continue
        u = edge / length
        v = np.array([-u[1], u[0]])
        pu, pv = hull @ u, hull @ v
        area = (pu.max() - pu.min()) * (pv.max() - pv.min())
        if best is None or area < best[0]:
            best = (area, u, v, pu.min(), pu.max(), pv.min(), pv.max())
    _, u, v, u0, u1, v0, v1 = best
    corners = [a * u + b * v for a, b in ((u0, v0), (u1, v0), (u1, v1), (u0, v1))]
    pts = sorted(corners, key=lambda p: p[0])
    left = (pts[0], pts[1]) if pts[1][1] > pts[0][1] else (pts[1], pts[0])  # (top, bottom)
    right = (pts[2], pts[3]) if pts[3][1] > pts[2][1] else (pts[3], pts[2])
    box = np.array([left[0], right[0], right[1], left[1]], dtype=np.float64)
    return box, float(min(u1 - u0, v1 - v0))


def _inside(box: np.ndarray, xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
    """Which (xs, ys) points lie in the convex quadrilateral `box`, its edges included."""
    signs = []
    for i in range(4):
        (ax, ay), (bx, by) = box[i], box[(i + 1) % 4]
        signs.append((bx - ax) * (ys - ay) - (by - ay) * (xs - ax))
    s = np.stack(signs)
    return np.all(s >= -1e-9, axis=0) | np.all(s <= 1e-9, axis=0)


def box_score(prob: np.ndarray, box: Box) -> float:
    """box_score_fast: the mean probability inside the box (corners truncated to integers)."""
    h, w = prob.shape
    xmin = int(np.clip(np.floor(box[:, 0].min()), 0, w - 1))
    xmax = int(np.clip(np.ceil(box[:, 0].max()), 0, w - 1))
    ymin = int(np.clip(np.floor(box[:, 1].min()), 0, h - 1))
    ymax = int(np.clip(np.ceil(box[:, 1].max()), 0, h - 1))
    quad = box.astype(np.int32).astype(np.float64)
    ys, xs = np.mgrid[ymin : ymax + 1, xmin : xmax + 1]
    inside = _inside(quad, xs.astype(np.float64), ys.astype(np.float64))
    if not inside.any():
        return 0.0
    return float(prob[ymin : ymax + 1, xmin : xmax + 1][inside].mean())


def unclip(box: Box, ratio: float) -> np.ndarray:
    """DB unclip: pyclipper's round-join offset of the box by area * ratio / perimeter.

    pyclipper works on integer coordinates: the corners are truncated and the result is
    rounded. Only the straight offset edges are returned (8 points); the rounded corners lie
    inside their minimum-area rectangle, which is all the caller takes from them.
    """
    u, v = box[1] - box[0], box[3] - box[0]
    w, h = float(np.hypot(*u)), float(np.hypot(*v))
    if w == 0 or h == 0:
        return box.copy()
    d = (w * h) * ratio / (2 * (w + h))
    pts = np.trunc(box)
    centre = pts.mean(axis=0)
    out = []
    for i in range(4):
        a, b = pts[i], pts[(i + 1) % 4]
        edge = (b - a) / max(float(np.hypot(*(b - a))), 1e-9)
        normal = np.array([edge[1], -edge[0]])
        if np.dot(normal, (a + b) / 2 - centre) < 0:
            normal = -normal
        out += [a + d * normal, b + d * normal]
    return np.rint(np.asarray(out))


def order_clockwise(box: np.ndarray) -> Box:
    """order_points_clockwise: top-left, top-right, bottom-right, bottom-left."""
    xs = box[np.argsort(box[:, 0], kind="stable")]
    left, right = xs[:2], xs[2:]
    tl, bl = left[np.argsort(left[:, 1], kind="stable")]
    tr, br = right[np.argsort(right[:, 1], kind="stable")]
    return np.array([tl, tr, br, bl], dtype=np.float64)


def sort_boxes(boxes: list[Box], y_gap: float = 10.0) -> list[Box]:
    """Reading order: by the top-left corner's y, then x within lines closer than y_gap."""
    if not boxes:
        return []
    order = sorted(range(len(boxes)), key=lambda i: boxes[i][0, 1])
    line, last, keyed = 0, None, []
    for i in order:
        y = boxes[i][0, 1]
        if last is not None and y - last >= y_gap:
            line += 1
        last = y
        keyed.append((line, boxes[i][0, 0], i))
    return [boxes[i] for _, _, i in sorted(keyed)]


def _homography(src: np.ndarray, dst: np.ndarray) -> np.ndarray:
    """The 3x3 perspective transform that maps the 4 src points to the 4 dst points."""
    a, b = [], []
    for (x, y), (u, v) in zip(src, dst, strict=True):
        a.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        b.append(u)
        a.append([0, 0, 0, x, y, 1, -v * x, -v * y])
        b.append(v)
    m = np.linalg.solve(np.asarray(a, np.float64), np.asarray(b, np.float64))
    return np.append(m, 1.0).reshape(3, 3)


def _cubic(t: np.ndarray) -> list[np.ndarray]:
    """OpenCV's bicubic weights (a = -0.75) for the taps at -1, 0, 1, 2 around a sample."""
    a = -0.75

    def near(x):
        return ((a + 2) * x - (a + 3)) * x * x + 1

    def far(x):
        return ((a * x - 5 * a) * x + 8 * a) * x - 4 * a

    return [far(t + 1), near(t), near(1 - t), far(2 - t)]


def crop_quad(img: np.ndarray, box: Box) -> tuple[np.ndarray, bool]:
    """get_rotate_crop_image: the box warped upright (bicubic, border replicate). A crop at
    least 1.5 times taller than wide is turned 90 degrees; the flag says so."""
    w = int(max(np.hypot(*(box[0] - box[1])), np.hypot(*(box[2] - box[3]))))
    h = int(max(np.hypot(*(box[0] - box[3])), np.hypot(*(box[1] - box[2]))))
    w, h = max(w, 1), max(h, 1)
    target = np.array([[0, 0], [w, 0], [w, h], [0, h]], np.float64)
    inverse = np.linalg.inv(_homography(box, target))
    gy, gx = np.mgrid[0:h, 0:w].astype(np.float64)
    q = inverse @ np.stack([gx.ravel(), gy.ravel(), np.ones(gx.size)])
    sx, sy = (q[0] / q[2]).reshape(h, w), (q[1] / q[2]).reshape(h, w)
    x0, y0 = np.floor(sx).astype(int), np.floor(sy).astype(int)
    wx, wy = _cubic(sx - x0), _cubic(sy - y0)
    f = img.astype(np.float32)
    if f.ndim == 2:
        f = f[:, :, None]
    H, W = f.shape[:2]
    out = np.zeros((h, w, f.shape[2]), np.float32)
    for j in range(4):
        yy = np.clip(y0 + j - 1, 0, H - 1)
        row = np.zeros_like(out)
        for i in range(4):
            xx = np.clip(x0 + i - 1, 0, W - 1)
            row += f[yy, xx] * wx[i][:, :, None]
        out += row * wy[j][:, :, None]
    crop = np.clip(np.rint(out), 0, 255).astype(np.uint8)
    if img.ndim == 2:
        crop = crop[:, :, 0]
    if h / w >= 1.5:
        return np.rot90(crop), True
    return crop, False


def slice_box(box: Box, a: float, b: float) -> tuple[float, float, float, float]:
    """Axis-aligned bounds (x0, y0, x1, y1) of the part [a, b) of the box's width."""
    top0, top1 = box[0] + (box[1] - box[0]) * a, box[0] + (box[1] - box[0]) * b
    bottom0, bottom1 = box[3] + (box[2] - box[3]) * a, box[3] + (box[2] - box[3]) * b
    pts = np.array([top0, top1, bottom0, bottom1])
    return (
        float(pts[:, 0].min()),
        float(pts[:, 1].min()),
        float(pts[:, 0].max()),
        float(pts[:, 1].max()),
    )
