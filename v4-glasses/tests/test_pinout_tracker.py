"""Pin-map extraction (tables / diagram / catalog) + live chip tracking geometry."""
from __future__ import annotations

import io
import threading
from pathlib import Path

import numpy as np
import pytest

from server import chip_tracker as ct
from server import pinout

ROOT = Path(__file__).resolve().parent.parent


# ---- golden pin maps (needs the cached datasheets; skipped on a fresh clone) ----------
GOLDEN = {
    "NE555P": {1: "GND", 2: "TRIG", 3: "OUT", 4: "RESET", 5: "CONT", 6: "THRES", 7: "DISCH", 8: "VCC"},
    "LM358P": {1: "OUT1", 2: "IN1-", 3: "IN1+", 4: "V-", 5: "IN2+", 6: "IN2-", 7: "OUT2", 8: "V+"},
    "LM741CN": {1: "OFFSET NULL", 2: "INVERTING INPUT", 3: "NONINVERTING INPUT", 4: "V-",
                5: "OFFSET NULL", 6: "OUTPUT", 7: "V+", 8: "NC"},
    "TL072CP": {1: "1OUT", 2: "1IN-", 3: "1IN+", 4: "VCC-", 5: "2IN+", 6: "2IN-", 7: "2OUT", 8: "VCC+"},
    "LM393P": {1: "1OUT", 2: "1IN-", 3: "1IN+", 4: "GND", 5: "2IN+", 6: "2IN-", 7: "2OUT", 8: "VCC"},
    "LM324N": {1: "1OUT", 4: "VCC+", 7: "2OUT", 8: "3OUT", 11: "VCC-", 14: "4OUT"},
    "SN74HC595N": {1: "QB", 7: "QH", 8: "GND", 9: "QH'", 10: "SRCLR", 11: "SRCLK", 12: "RCLK",
                   13: "OE", 14: "SER", 15: "QA", 16: "VCC"},
    "LM317T": {1: "ADJUST", 2: "OUTPUT", 3: "INPUT"},
    "SN74HC04N": {1: "1A", 2: "1Y", 7: "GND", 8: "4Y", 13: "6A", 14: "VCC"},
    "SN74HC27N": {1: "1A", 6: "2Y", 7: "GND", 8: "3Y", 12: "1Y", 13: "1C", 14: "VCC"},
    "SN74HC153N": {1: "1G", 2: "B", 7: "1Y", 8: "GND", 9: "2Y", 14: "A", 15: "2G", 16: "VCC"},
    "CD4011BE": {1: "A", 2: "B", 3: "J", 4: "K", 7: "VSS", 11: "M", 14: "VDD"},
}


def _have_pdf(part: str) -> bool:
    from server.catalog_api import load
    p = load().by_number(part)
    return bool(p and p.datasheet.get("path") and (ROOT / p.datasheet["path"]).exists())


@pytest.mark.parametrize("part", sorted(GOLDEN))
def test_catalog_part_pin_map_is_complete_and_correct(part):
    if part != "CD4011BE" and not _have_pdf(part):
        pytest.skip("datasheet not cached (run python -m catalog.fetch)")
    info = pinout.get_pins(part)
    got = {p["no"]: p["name"] for p in info["pins"]}
    assert len(got) == info["pin_count"], f"{part}: {len(got)}/{info['pin_count']} pins"
    for no, name in GOLDEN[part].items():
        assert got.get(no) == name, f"{part} pin {no}: {got.get(no)!r} != {name!r}"


def test_catalog_pins_beat_parsing_and_survive_yaml_no_key():
    # `no:` in YAML 1.1 is boolean False; the catalog uses `pin:` — and loads it.
    info = pinout.get_pins("CD4011BE")
    assert info["source"] == "catalog" and info["pins"][0] == {
        "name": "A", "no": 1, "desc": "Gate 1 input", "label": "A"}


# ---- table parsing edge cases (no PDFs needed) ---------------------------------
def test_table_wrapped_names_multi_pin_cells_and_superheader():
    rows = [["P", "IN", "I/O", "DESCRIPTION"],
            ["NAME", "NO.", None, None],
            ["INVERTING\nINPUT", "2", "I", "Inverting signal input"],
            ["NC", "8", "N/A", "No Connect"],
            ["NONINVERTING\nINPUT", "3", "I", "Noninverting signal input"],
            ["OFFSET NULL", "1, 5", "I", "Offset null"],
            ["OUTPUT", "6", "O", "Amplified signal output"],
            ["V+", "7", "I", "Positive supply"],
            ["V–", "4", "I", "Negative supply"]]
    pins = {p["no"]: p["name"] for p in pinout.parse_table_rows(rows, "PDIP-8")}
    assert pins == {1: "OFFSET NULL", 2: "INVERTING INPUT", 3: "NONINVERTING INPUT", 4: "V-",
                    5: "OFFSET NULL", 6: "OUTPUT", 7: "V+", 8: "NC"}


def test_table_picks_package_column_and_pigeonholes_one_missing_pin():
    rows = [["PIN", None, None, "", ""],
            ["NAME", "SOIC, PDIP", "LCCC", "I/O", "DESCRIPTION"],
            ["GND", "8", "10", "—", "Ground"], ["OE", "13", "17", "I", "Output enable"],
            ["Q\nA", "15", "19", "O", "QA"], ["Q\nB", "1", "2", "O", "QB"],
            ["Q\nC", "2", "3", "O", ""], ["Q\nD", "3", "4", "O", ""], ["Q\nE", "4", "5", "O", ""],
            ["Q\nF", "5", "7", "O", ""], ["Q\nG", "6", "8", "O", ""], ["Q\nH", "7", "9", "O", ""],
            ["Q\nH'", "9", "12", "O", ""], ["RCLK", "12", "14", "I", ""], ["SER", "14", "18", "I", ""],
            ["SRCLK", "11", "14", "I", ""], ["SRCLR", "10", "13", "I", ""],
            ["NC", "—", "1", "—", "No connection"],
            [None, None, "16", None, None],           # extraction misplaced VCC's PDIP cell
            ["V\nCC", "—", "20", "—", "Power"]]
    pins = {p["no"]: p for p in pinout.parse_table_rows(rows, "PDIP-16")}
    assert pins[15]["name"] == "QA" and pins[9]["name"] == "QH'" and pins[8]["name"] == "GND"
    assert pins[16]["name"] == "VCC" and pins[16]["inferred"] is True
    assert len(pins) == 16


def test_caption_filter_rejects_sibling_device_tables():
    assert pinout._caption_matches(["TL072x"], "TL072CP") is True
    assert pinout._caption_matches(["TL071x"], "TL072CP") is False
    assert pinout._caption_matches(["TL072M"], "TL072CP") is False
    assert pinout._caption_matches([], "TL072CP") is None


def test_short_labels():
    assert pinout.short_label("NONINVERTING INPUT") == "IN+"
    assert pinout.short_label("OFFSET NULL") == "NULL"
    assert pinout.short_label("VCC") == "VCC"


# ---- tracker: marking selection ---------------------------------------------------
def test_marking_score_prefers_chip_over_caption_containing_it():
    mk = ["SN74HC04N", "74HC04"]
    chip_misread = ct.marking_score("SN74HCOAN", mk)
    caption = ct.marking_score("74HC04 Hex Inverters DIP14 5 Pack", mk)
    assert chip_misread > caption
    assert ct.marking_score("SN74HC04N|", mk) > 0.95


# ---- tracker: geometry on a synthetic frame ---------------------------------------
def _synthetic(angle_deg: float, flip: bool):
    """White frame with a dark rotated DIP body; returns (rgb, quad) where the quad
    is what OCR would report for the marking (reading direction = body long axis)."""
    from PIL import Image, ImageDraw
    W, H = 800, 600
    im = Image.new("RGB", (W, H), (235, 235, 235))
    c = np.array([400.0, 300.0])
    th = np.deg2rad(angle_deg)
    d = np.array([np.cos(th), np.sin(th)])
    u = np.array([d[1], -d[0]])                      # text-up (image y grows down)
    if flip:
        d, u = -d, -u
    A, B = 220.0, 70.0                               # half length / half width of body
    body = [c - d * A - u * B, c + d * A - u * B, c + d * A + u * B, c - d * A + u * B]
    ImageDraw.Draw(im).polygon([tuple(p) for p in body], fill=(35, 35, 38))
    L, Ht = 240.0, 36.0
    tl, tr = c - d * L / 2 + u * Ht / 2, c + d * L / 2 + u * Ht / 2
    br, bl = c + d * L / 2 - u * Ht / 2, c - d * L / 2 - u * Ht / 2
    quad = ("SN74HC04N", 1.0, tuple(tl), tuple(tr), tuple(br), tuple(bl))
    return np.asarray(im), quad, body, d, u


@pytest.mark.parametrize("angle,flip", [(0, False), (25, False), (-30, False), (0, True), (40, True)])
def test_estimate_pose_finds_body_and_pin1_corner(angle, flip):
    rgb, quad, body, d, u = _synthetic(angle, flip)
    pose = ct.estimate_pose(rgb, [quad], ["SN74HC04N"])
    assert pose is not None and pose.body_from_mask
    corners = np.array(pose.corners())
    # pin-1 corner = bottom-left relative to the marking = body[0]
    assert np.linalg.norm(corners[0] - body[0]) < 12
    assert np.linalg.norm(corners[2] - body[2]) < 12


def test_caption_on_light_background_is_not_a_chip():
    from PIL import Image
    rgb = np.asarray(Image.new("RGB", (400, 300), (240, 240, 240)))
    quad = ("SN74HC04N", 1.0, (100, 130), (300, 130), (300, 160), (100, 160))
    assert ct.estimate_pose(rgb, [quad], ["SN74HC04N"]) is None


def test_dip_layout_is_counterclockwise_from_pin1():
    pose = ct.Pose(center=np.array([0.0, 0.0]), d=np.array([1.0, 0.0]), u=np.array([0.0, -1.0]),
                   a0=-70, a1=70, b0=-20, b1=20)
    pins = ct.layout_pins(pose, [{"no": i, "name": f"P{i}"} for i in range(1, 9)], 8, "dip")
    by = {p["no"]: p for p in pins}
    # bottom row (y=+20 in image coords), left -> right
    assert by[1]["y"] == pytest.approx(20) and by[1]["x"] < by[4]["x"]
    # top row, right -> left: pin 5 above pin 4, pin 8 above pin 1
    assert by[5]["y"] == pytest.approx(-20) and by[5]["x"] == pytest.approx(by[4]["x"])
    assert by[8]["x"] == pytest.approx(by[1]["x"])
    assert (by[1]["nx"], by[1]["ny"]) == (0.0, 1.0) and (by[8]["nx"], by[8]["ny"]) == (0.0, -1.0)


def test_inline_layout_to220():
    pose = ct.Pose(center=np.array([0.0, 0.0]), d=np.array([1.0, 0.0]), u=np.array([0.0, -1.0]),
                   a0=-50, a1=50, b0=-30, b1=30)
    pins = ct.layout_pins(pose, [{"no": 1, "name": "ADJUST"}, {"no": 2, "name": "OUTPUT"},
                                 {"no": 3, "name": "INPUT"}], 3, "inline")
    xs = [p["x"] for p in sorted(pins, key=lambda p: p["no"])]
    assert xs == sorted(xs) and all(p["y"] == pytest.approx(30) for p in pins)


def test_track_frame_payload_is_normalized():
    from PIL import Image
    rgb, quad, *_ = _synthetic(15, False)
    buf = io.BytesIO()
    Image.fromarray(rgb).save(buf, format="JPEG", quality=90)
    target = {"part": "SN74HC04N", "markings": ["SN74HC04N"], "pin_count": 14, "layout": "dip",
              "pins": [{"no": i, "name": f"P{i}", "label": f"P{i}"} for i in range(1, 15)]}
    res = ct.track_frame(buf.getvalue(), target, lambda im: [quad])
    assert res["ok"] and len(res["pins"]) == 14
    assert all(0 <= p["x"] <= 1 and 0 <= p["y"] <= 1 for p in res["pins"])


def test_tracker_thread_emits_chip_pose():
    from PIL import Image
    from server.stream import FrameBuffer
    rgb, quad, *_ = _synthetic(0, False)
    buf = io.BytesIO()
    Image.fromarray(rgb).save(buf, format="JPEG")
    fb, got, ev = FrameBuffer(), [], threading.Event()

    def emit(t, **kw):
        got.append((t, kw)); ev.set()
    tr = ct.ChipTracker(fb, emit, quad_fn=lambda im: [quad])
    assert tr.start({"part": "X", "markings": ["SN74HC04N"], "pins": [{"no": 1, "name": "A"}],
                     "pin_count": 8, "layout": "dip"})
    fb.put(buf.getvalue())
    assert ev.wait(5)
    tr.stop()
    assert got[0][0] == "chip_pose" and got[0][1]["ok"]


def test_tracker_survives_rapid_off_on_toggle():
    from PIL import Image
    from server.stream import FrameBuffer
    rgb, quad, *_ = _synthetic(0, False)
    buf = io.BytesIO()
    Image.fromarray(rgb).save(buf, format="JPEG")
    fb, ev = FrameBuffer(), threading.Event()
    tr = ct.ChipTracker(fb, lambda t, **kw: ev.set() if kw.get("ok") else None,
                        quad_fn=lambda im: [quad])
    target = {"part": "X", "markings": ["SN74HC04N"], "pins": [], "pin_count": 8, "layout": "dip"}
    for _ in range(20):
        tr.start(target); tr.stop()
    tr.start(target)
    fb.put(buf.getvalue())
    assert ev.wait(5), "tracker died after rapid toggling"
    tr.stop()


# ---- device MJPEG puller ------------------------------------------------------------
def test_pull_mjpeg_splits_frames(monkeypatch):
    from server import stream
    f1, f2 = b"\xff\xd8AAAA\xff\xd9", b"\xff\xd8BBBBBB\xff\xd9"
    body = b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + f1 + b"\r\n--frame\r\n\r\n" + f2 + b"\r\n"
    stop = threading.Event()

    class Resp:
        def __enter__(self): return self
        def __exit__(self, *a): stop.set()
        def raise_for_status(self): pass
        def iter_content(self, chunk_size):            # deliberately split mid-frame
            for i in range(0, len(body), 7):
                yield body[i:i + 7]
    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **k: Resp())
    fb = stream.FrameBuffer()
    seen = []
    orig = fb.put
    fb.put = lambda data: (seen.append(data), orig(data))
    stream.pull_mjpeg("http://dev/stream", fb, stop, retry_s=0)
    assert seen == [f1, f2]
