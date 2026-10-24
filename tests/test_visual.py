from __future__ import annotations

from pathlib import Path

from PIL import Image
from PIL import ImageDraw

from ui_review import visual


def page(path: Path, *, size: tuple[int, int] = (200, 300), box: str | None = None) -> Path:
    img = Image.new("RGB", size, (250, 250, 250))
    draw = ImageDraw.Draw(img)
    for y in range(0, size[1], 20):
        draw.rectangle((10, y + 4, size[0] - 10, y + 12), fill=(40, 40, 60))
    if box:
        draw.rectangle((50, 50, 150, 120), fill=box)
    img.save(path)
    return path


def test_identical_images_match(tmp_path: Path) -> None:
    a = page(tmp_path / "a.png")
    diff = visual.compare(a, a, diff_path=tmp_path / "d.png")
    assert diff.changed_pixels == 0 and diff.ratio == 0 and diff.hash_distance == 0
    assert diff.bbox is None and not diff.size_changed
    assert not (tmp_path / "d.png").exists()


def test_changes_below_tolerance_are_ignored(tmp_path: Path) -> None:
    a = page(tmp_path / "a.png")
    with Image.open(a) as raw:
        shifted = raw.point(lambda v: min(255, v + 5))
    shifted.save(tmp_path / "b.png")
    assert visual.compare(a, tmp_path / "b.png", tolerance=16).changed_pixels == 0
    assert visual.compare(a, tmp_path / "b.png", tolerance=2).changed_pixels > 0


def test_changed_region_is_counted_and_drawn(tmp_path: Path) -> None:
    a = page(tmp_path / "a.png")
    b = page(tmp_path / "b.png", box="#e11d48")
    diff = visual.compare(a, b, diff_path=tmp_path / "d.png")
    assert 0.05 < diff.ratio < 0.2
    assert diff.bbox is not None and diff.bbox[0] >= 50 and diff.bbox[2] <= 151
    with Image.open(tmp_path / "d.png") as img:
        assert img.getpixel((100, 80)) == (230, 30, 60)
        assert img.getpixel((5, 5)) != (230, 30, 60)


def test_size_change_counts_padding(tmp_path: Path) -> None:
    a = page(tmp_path / "a.png")
    b = page(tmp_path / "b.png", size=(200, 360))
    diff = visual.compare(a, b)
    assert diff.size_changed
    assert diff.baseline_size == (200, 300) and diff.current_size == (200, 360)
    assert diff.changed_pixels >= 200 * 60 * 0.5


def test_dhash_is_robust_to_scaling() -> None:
    img = Image.new("RGB", (400, 400), "white")
    ImageDraw.Draw(img).ellipse((50, 50, 300, 300), fill="black")
    other = Image.new("RGB", (400, 400), "white")
    ImageDraw.Draw(other).rectangle((0, 200, 400, 400), fill="black")
    h = visual.dhash(img)
    assert visual.hamming(h, visual.dhash(img.resize((200, 200)))) <= 4
    assert visual.hamming(h, visual.dhash(other)) > 10


def test_update_baseline_copies(tmp_path: Path) -> None:
    a = page(tmp_path / "a.png")
    target = tmp_path / "base" / "nested" / "a.png"
    visual.update_baseline(a, target)
    assert target.read_bytes() == a.read_bytes()
    assert visual.VisualDiff(0, 0, 0, False, (0, 0), (0, 0), None).ratio == 0.0
