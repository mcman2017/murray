import io

import numpy as np
import pytest
from PIL import Image

from murray import imaging


def _img(arr) -> Image.Image:
    return Image.fromarray(np.asarray(arr, dtype=np.uint8))


def smooth(h=240, w=320):
    y, x = np.mgrid[0:h, 0:w]
    r = (np.sin(x / 23.0) * 0.5 + 0.5) * 255
    g = (np.cos(y / 17.0) * 0.5 + 0.5) * 255
    b = (x + y) % 256
    return _img(np.dstack([r, g, b]))


def test_clean_reference_trips_nothing():
    assert imaging.detect(imaging.stats(smooth())) == []
    # a dark but structured image (mostly black interior, coloured boundary) is still clean
    a = np.zeros((240, 320, 3))
    y, x = np.mgrid[0:240, 0:320]
    ring = np.hypot(x - 160, y - 120)
    a[..., 1] = np.clip(255 - np.abs(ring - 80) * 6, 0, 255)
    a[..., 2] = np.clip(200 - np.abs(ring - 60) * 3, 0, 255)
    found = imaging.detect(imaging.stats(_img(a)))
    assert "blank_or_uniform" not in found and "banding" not in found and "pixelation" not in found


def test_blank_frames_are_hard_failures():
    for value in (0, 255, 37):
        st = imaging.stats(_img(np.full((120, 160, 3), value)))
        assert imaging.detect(st) == ["blank_or_uniform"]
        assert imaging.hard_fail(st)
    # a white plot canvas with almost nothing drawn on it is blank too
    a = np.full((200, 300, 3), 255)
    a[100, 150] = (0, 0, 0)
    assert "blank_or_uniform" in imaging.detect(imaging.stats(_img(a)))
    assert imaging.hard_fail(imaging.stats(smooth())) is None


def test_banding_clipping_pixelation_contrast_each_fire_on_their_own_fixture():
    y, x = np.mgrid[0:240, 0:320]
    bands = np.dstack([(x // 40) * 32, (x // 40) * 20 + 40, 255 - (x // 40) * 30])
    assert "banding" in imaging.detect(imaging.stats(_img(bands)))

    clipped = np.dstack([np.full((240, 320), 255), (x % 256), np.where(x > 20, 255, 0)])
    assert "clipping" in imaging.detect(imaging.stats(_img(clipped)))

    rng = np.random.default_rng(3)
    coarse = rng.integers(0, 255, size=(15, 20, 3))
    blocks = np.repeat(np.repeat(coarse, 16, axis=0), 16, axis=1)
    assert "pixelation" in imaging.detect(imaging.stats(_img(blocks)))

    faint = np.full((240, 320, 3), 120)
    faint[100:108] = 150                      # a pale stroke on a mid-grey ground: visible, but barely
    assert imaging.detect(imaging.stats(_img(faint))) == ["low_contrast"]


def test_decode_png_is_strict():
    buf = io.BytesIO()
    smooth(60, 80).save(buf, format="PNG")
    assert imaging.decode_png(buf.getvalue()).size == (80, 60)
    jpg = io.BytesIO()
    smooth(60, 80).save(jpg, format="JPEG")
    with pytest.raises(imaging.ImageRejected):
        imaging.decode_png(jpg.getvalue())
    with pytest.raises(imaging.ImageRejected):
        imaging.decode_png(b"\x89PNG\r\n\x1a\n" + b"garbage" * 10)
    big = io.BytesIO()
    Image.new("RGB", (3000, 100)).save(big, format="PNG")
    with pytest.raises(imaging.ImageRejected):
        imaging.decode_png(big.getvalue())


def test_upload_normalisation_reencodes_and_bounds():
    jpg = io.BytesIO()
    smooth(600, 2400).save(jpg, format="JPEG")
    out = imaging.normalize_upload(jpg.getvalue())
    assert max(out.size) <= 1568 and out.mode == "RGB"
    assert imaging.to_png(out).startswith(imaging.PNG_MAGIC)
    with pytest.raises(imaging.ImageRejected):
        imaging.normalize_upload(b"not an image at all")


def test_contact_sheet_and_gif(tmp_path):
    frames = [smooth(90, 120).rotate(i * 20) for i in range(9)]
    idx = imaging.key_frames(len(frames))
    assert idx[0] == 0 and idx[-1] == 8 and len(idx) == 6
    sheet = imaging.contact_sheet(frames, idx)
    assert sheet.size == (3 * 384, 2 * 288)
    imaging.save_gif(frames, tmp_path / "a.gif")
    assert Image.open(tmp_path / "a.gif").n_frames == 9
