"""Live chip tracking for the AR pinout overlay.

Each live frame -> where is the chip, which way is pin 1, where is every pin.

1. **Anchor on the marking.** OCR (Apple Vision, rotated quads) finds the accepted
   part's top marking in the frame. The quad gives the chip's *orientation*: the
   reading direction ``d`` and the text-up direction ``u``. By JEDEC/industry
   convention, with the marking upright, pin 1 is bottom-left (DIP) — so the text
   resolves the 180° ambiguity that a notch/dot detector can't see reliably.
   Captions/labels elsewhere in the frame are rejected because the marking sits
   on a *dark* body (light text on dark), while page/screen text is dark on light.
2. **Grow the body.** Starting from the marking, the connected region of
   body-coloured pixels (text filled in) gives the body extent along ``d``/``u``.
3. **Lay out the pins** on the body edges: DIP pins 1..N/2 along the bottom edge
   left→right, N/2+1..N back along the top edge; TO-220 leads along the bottom.

Pure functions (``estimate_pose``, ``layout_pins``) are unit-tested; the
``ChipTracker`` thread just feeds them frames and emits ``chip_pose`` events.
Identification is untouched: tracking only *places* an already-accepted pin map.
"""
from __future__ import annotations

import difflib
import io
import logging
import re
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional, Sequence

import numpy as np

log = logging.getLogger("glasses.tracker")

Point = tuple[float, float]
# (text, confidence, topLeft, topRight, bottomRight, bottomLeft) in pixel coords
TextQuad = tuple[str, float, Point, Point, Point, Point]

WORK_PX = 960          # longest side the tracker works at (OCR + mask)


@dataclass
class Pose:
    center: np.ndarray      # (x, y) px
    d: np.ndarray           # unit reading direction (toward the far end from pin 1)
    u: np.ndarray           # unit "up" (toward the top of the marking text)
    a0: float               # body extent along d, relative to center (a0 < 0 < a1)
    a1: float
    b0: float               # body extent along u (b0 < 0 = pin-1 / bottom side)
    b1: float
    marking: str = ""
    score: float = 0.0
    body_from_mask: bool = True

    def pt(self, s: float, t: float) -> np.ndarray:
        return self.center + self.d * s + self.u * t

    def corners(self) -> list[list[float]]:
        """[pin-1 corner (bottom-left), bottom-right, top-right, top-left]."""
        return [self.pt(self.a0, self.b0).tolist(), self.pt(self.a1, self.b0).tolist(),
                self.pt(self.a1, self.b1).tolist(), self.pt(self.a0, self.b1).tolist()]


# ---- marking selection ------------------------------------------------------------
def _norm(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", s.upper())


def marking_score(text: str, markings: Sequence[str]) -> float:
    """How well an OCR line matches one of the part's top markings (0..1)."""
    t = _norm(text)
    if not t:
        return 0.0
    best = 0.0
    for m in markings:
        mm = _norm(m)
        if not mm:
            continue
        if mm in t:   # 'SN74HC04N' inside 'SN74HC04N|' -- but a long caption that merely
            # CONTAINS the marking ('74HC04 Hex Inverters DIP14...') scores low
            best = max(best, 0.7 + 0.3 * len(mm) / max(len(t), 1))
            continue
        best = max(best, difflib.SequenceMatcher(None, t, mm).ratio())
    return best


def _quad_arrays(q: TextQuad):
    tl, tr, br, bl = (np.asarray(p, dtype=np.float64) for p in q[2:6])
    return tl, tr, br, bl


def _quad_geometry(q: TextQuad):
    tl, tr, br, bl = _quad_arrays(q)
    d = (tr - tl) + (br - bl)
    u = (tl - bl) + (tr - br)
    length = np.linalg.norm(d) / 2
    height = np.linalg.norm(u) / 2
    d = d / (np.linalg.norm(d) or 1)
    # make u exactly perpendicular to d, keeping its sense
    u_perp = np.array([d[1], -d[0]])
    if np.dot(u_perp, u) < 0:
        u_perp = -u_perp
    center = (tl + tr + br + bl) / 4
    return center, d, u_perp, length, height


def _sample_lum(gray: np.ndarray, pts: np.ndarray) -> np.ndarray:
    h, w = gray.shape
    xs = np.clip(pts[:, 0].round().astype(int), 0, w - 1)
    ys = np.clip(pts[:, 1].round().astype(int), 0, h - 1)
    return gray[ys, xs]


def _quad_grid(center, d, u, half_len, half_h, n=15) -> np.ndarray:
    s = np.linspace(-half_len, half_len, n)
    t = np.linspace(-half_h, half_h, max(3, n // 3))
    S, T = np.meshgrid(s, t)
    return center + np.outer(S.ravel(), d) + np.outer(T.ravel(), u)


def pick_marking(quads: Sequence[TextQuad], gray: np.ndarray,
                 markings: Sequence[str], min_score: float = 0.6):
    """Best OCR line that (a) matches the marking and (b) sits on a dark body."""
    best, best_key = None, None
    for q in quads:
        sc = marking_score(q[0], markings) if markings else 0.5
        if sc < min_score:
            continue
        c, d, u, L, H = _quad_geometry(q)
        if L < 4 or H < 2:
            continue
        # light-on-dark check: median over the text box is the BODY colour when the
        # strokes are thin; also sample a ring just outside the box.
        inside = np.median(_sample_lum(gray, _quad_grid(c, d, u, L / 2, H / 2)))
        ring = np.concatenate([
            _sample_lum(gray, _quad_grid(c + u * H * 0.95, d, u, L / 2, H * 0.2, 9)),
            _sample_lum(gray, _quad_grid(c - u * H * 0.95, d, u, L / 2, H * 0.2, 9)),
        ])
        bg = float(np.median(np.concatenate([[inside], ring])))
        if bg > 150:                    # dark text on a light page/screen -> not a chip
            continue
        key = (sc, -bg, L)
        if best_key is None or key > best_key:
            best, best_key = (q, sc, bg), key
    return best


# ---- body extent ------------------------------------------------------------------
def estimate_pose(rgb: np.ndarray, quads: Sequence[TextQuad],
                  markings: Sequence[str]) -> Optional[Pose]:
    """Chip pose in the pixel frame of ``rgb`` (H×W×3 uint8), or None."""
    from scipy import ndimage as ndi

    f = rgb.astype(np.float32)
    gray = 0.299 * f[..., 0] + 0.587 * f[..., 1] + 0.114 * f[..., 2]
    picked = pick_marking(quads, gray, markings)
    if not picked:
        return None
    q, score, body_lum = picked
    c, d, u, L, H = _quad_geometry(q)

    # Fallback geometry from typical DIP proportions relative to the marking.
    fb = Pose(center=c, d=d, u=u, a0=-0.75 * L, a1=0.75 * L, b0=-2.2 * H, b1=2.2 * H,
              marking=q[0], score=score, body_from_mask=False)

    # body mask: pixels close to the body colour, with all on-body text filled in
    tol = max(28.0, 0.45 * body_lum)
    mask = np.abs(gray - body_lum) < tol
    h, w = gray.shape
    yy, xx = np.mgrid[0:h, 0:w]
    for oq in quads:                                  # fill text boxes lying on the body
        oc, od, ou, oL, oH = _quad_geometry(oq)
        if np.median(_sample_lum(gray, _quad_grid(oc, od, ou, oL / 2, oH / 2))) > 150:
            continue
        rel_x = (xx - oc[0]) * od[0] + (yy - oc[1]) * od[1]
        rel_y = (xx - oc[0]) * ou[0] + (yy - oc[1]) * ou[1]
        mask |= (np.abs(rel_x) <= oL / 2 + 2) & (np.abs(rel_y) <= oH / 2 + 2)
    k = max(3, int(round(H * 0.35)))
    mask = ndi.binary_closing(mask, np.ones((k, k)), iterations=1)

    # Walk outward from the marking along each axis, measuring how much of a short
    # cross-section is body-coloured; stop at the first sustained drop. This finds
    # the body EDGE locally, so a dark background that happens to touch the body
    # somewhere (screen bezel, shadow) can't drag the extent away.
    a0, a1 = _walk(mask, c, d, u, half_band=1.2 * H, limit=5 * L, gap=max(3, H * 0.3))
    b0, b1 = _walk(mask, c, u, d, half_band=0.45 * L, limit=6 * H, gap=max(3, H * 0.25))
    if a0 is None or b0 is None:
        return fb
    if (a1 - a0) < 0.9 * L or (b1 - b0) < 1.5 * H:
        return fb
    return Pose(center=c, d=d, u=u, a0=float(a0), a1=float(a1), b0=float(b0), b1=float(b1),
                marking=q[0], score=score)


def _walk(mask: np.ndarray, c: np.ndarray, axis: np.ndarray, across: np.ndarray,
          half_band: float, limit: float, gap: float, thresh: float = 0.5):
    """Extent (neg, pos) of the body along ``axis`` from ``c``: profile of body
    fraction over a cross-section of +-half_band, cut at the first run of >= gap
    px below ``thresh``. Returns (None, None) if the centre itself isn't body."""
    h, w = mask.shape
    steps = np.arange(-int(limit), int(limit) + 1, dtype=np.float64)
    offs = np.linspace(-half_band, half_band, 11)
    px = c[0] + steps[:, None] * axis[0] + offs[None, :] * across[0]
    py = c[1] + steps[:, None] * axis[1] + offs[None, :] * across[1]
    inside = (px >= 0) & (px < w) & (py >= 0) & (py < h)
    xi = np.clip(px.round().astype(int), 0, w - 1)
    yi = np.clip(py.round().astype(int), 0, h - 1)
    frac = (mask[yi, xi] & inside).mean(axis=1)
    mid = len(steps) // 2
    if frac[mid - 2: mid + 3].mean() < thresh:
        return None, None
    g = int(max(1, round(gap)))

    def edge(direction):
        i, run, last_ok = mid, 0, mid
        while 0 <= i + direction < len(steps):
            i += direction
            if frac[i] >= thresh:
                run, last_ok = 0, i
            else:
                run += 1
                if run >= g:
                    break
        return steps[last_ok]
    return float(edge(-1)), float(edge(+1))


# ---- pin layout --------------------------------------------------------------------
def layout_pins(pose: Pose, pins: Sequence[dict], pin_count: Optional[int],
                layout: str = "dip") -> list[dict]:
    """Anchor every pin on the body edge: [{no,label,name,x,y,nx,ny}] (px + outward unit)."""
    by_no = {p["no"]: p for p in pins}
    n = pin_count or (max(by_no) if by_no else 0)
    if n <= 0:
        return []
    out = []
    span = pose.a1 - pose.a0

    def emit(no, s, t, outward):
        p = by_no.get(no, {"name": "?", "label": "?"})
        x, y = pose.pt(s, t)
        out.append({"no": no, "name": p.get("name", ""), "label": p.get("label", p.get("name", "")),
                    "desc": p.get("desc", ""), "inferred": bool(p.get("inferred")),
                    "x": float(x), "y": float(y),
                    "nx": float(outward[0]), "ny": float(outward[1])})

    if layout == "inline":                         # TO-220/TO-92: leads along the bottom
        for k in range(1, n + 1):
            frac = 0.5 + (k - (n + 1) / 2) * (0.25 if n == 3 else 1.0 / (n + 1))
            emit(k, pose.a0 + frac * span, pose.b0, -pose.u)
        return out

    m = (n + 1) // 2
    for k in range(1, m + 1):                      # bottom row: pin 1 at the left end
        emit(k, pose.a0 + (k - 0.5) / m * span, pose.b0, -pose.u)
    for k in range(1, n - m + 1):                  # top row: N/2+1 at the right end
        emit(m + k, pose.a1 - (k - 0.5) / m * span, pose.b1, pose.u)
    return out


# ---- OCR adaptor ----------------------------------------------------------------------
def vision_quads(pil_img) -> list[TextQuad]:  # pragma: no cover - macOS runtime
    """Apple Vision text lines with their rotated quads (pixel coords, top-left origin)."""
    import Quartz
    import Vision
    from Foundation import NSData

    w, h = pil_img.size
    buf = io.BytesIO()
    pil_img.save(buf, format="JPEG", quality=90)
    raw = buf.getvalue()
    src = Quartz.CGImageSourceCreateWithData(NSData.dataWithBytes_length_(raw, len(raw)), None)
    cg = Quartz.CGImageSourceCreateImageAtIndex(src, 0, None)
    req = Vision.VNRecognizeTextRequest.alloc().init()
    req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    req.setUsesLanguageCorrection_(False)
    handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(cg, None)
    if not handler.performRequests_error_([req], None):
        return []
    out = []
    for o in req.results() or []:
        cand = o.topCandidates_(1)
        if not cand:
            continue
        P = lambda p: (float(p.x) * w, (1.0 - float(p.y)) * h)  # noqa: E731
        out.append((str(cand[0].string()), float(cand[0].confidence()),
                    P(o.topLeft()), P(o.topRight()), P(o.bottomRight()), P(o.bottomLeft())))
    return out


def default_quad_fn() -> Optional[Callable]:
    try:
        import Vision  # noqa: F401
        import Quartz  # noqa: F401
        return vision_quads
    except Exception:
        return None


def track_frame(jpeg: bytes, target: dict, quad_fn: Callable) -> dict:
    """One frame -> a ``chip_pose`` payload (normalized 0..1 coords)."""
    from PIL import Image

    im = Image.open(io.BytesIO(jpeg))
    im.draft("RGB", (WORK_PX, WORK_PX))              # fast JPEG downscale on decode
    im = im.convert("RGB")
    if max(im.size) > WORK_PX:
        im.thumbnail((WORK_PX, WORK_PX))
    w, h = im.size
    rgb = np.asarray(im)
    pose = estimate_pose(rgb, quad_fn(im), target.get("markings", []))
    if pose is None:
        return {"ok": False, "part": target.get("part"), "reason": "marking not in view"}
    pins = layout_pins(pose, target.get("pins", []), target.get("pin_count"),
                       target.get("layout", "dip"))
    nz = lambda p: [p[0] / w, p[1] / h]              # noqa: E731
    return {
        "ok": True, "part": target.get("part"), "w": w, "h": h,
        "corners": [nz(p) for p in pose.corners()],
        "pins": [{**p, "x": p["x"] / w, "y": p["y"] / h,
                  # outward direction stays in pixel space (aspect-correct)
                  } for p in pins],
        "marking": pose.marking, "score": round(pose.score, 3),
        "fit": "body" if pose.body_from_mask else "marking",
    }


class ChipTracker:
    """Background loop: latest frame -> chip_pose events, only while the overlay is on."""

    def __init__(self, frames, emit: Callable[..., None],
                 quad_fn: Optional[Callable] = None) -> None:
        self._frames = frames
        self._emit = emit
        self._quad_fn = quad_fn if quad_fn is not None else default_quad_fn()
        self._target: Optional[dict] = None
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    @property
    def available(self) -> bool:
        return self._quad_fn is not None

    @property
    def active(self) -> bool:
        return self._target is not None

    def start(self, target: dict) -> bool:
        """Begin (or re-target) tracking. Safe to call repeatedly / right after stop()."""
        if not self.available:
            return False
        with self._lock:
            self._target = target
            self._stop.clear()
            if self._thread is None:                 # the loop clears this, under the lock, on exit
                self._thread = threading.Thread(target=self._run, daemon=True, name="chip-tracker")
                self._thread.start()
        return True

    def stop(self) -> None:
        with self._lock:
            self._target = None
            self._stop.set()

    def _run(self) -> None:
        last = -1
        misses = 0
        while True:
            with self._lock:                          # exit decision is atomic with start()
                if self._stop.is_set():
                    self._thread = None
                    return
                target = self._target
            frame, seq = self._frames.wait_next(last, timeout=0.5)
            if frame is None or seq == last or target is None:
                continue
            last = seq
            t0 = time.monotonic()
            try:
                res = track_frame(frame, target, self._quad_fn)
            except Exception as e:                        # never kill the loop on one bad frame
                log.warning(f"tracker frame failed: {e}")
                res = {"ok": False, "part": target.get("part"), "reason": "tracker error"}
            if self._target is not target:                # toggled/re-targeted mid-frame: drop stale pose
                continue
            res["ms"] = round((time.monotonic() - t0) * 1000)
            misses = 0 if res.get("ok") else misses + 1
            if res.get("ok") or misses in (1, 5):          # don't spam misses
                self._emit("chip_pose", **res)
