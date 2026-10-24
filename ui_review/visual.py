"""Visual regression: tolerant pixel diff + perceptual hash, with a highlighted diff image."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from PIL import Image
from PIL import ImageChops

_PAD = (255, 0, 255)  # area that exists in only one of the two images


@dataclass(frozen=True)
class VisualDiff:
    changed_pixels: int
    total_pixels: int
    hash_distance: int
    size_changed: bool
    baseline_size: tuple[int, int]
    current_size: tuple[int, int]
    bbox: tuple[int, int, int, int] | None

    @property
    def ratio(self) -> float:
        return self.changed_pixels / self.total_pixels if self.total_pixels else 0.0


def dhash(image: Image.Image, size: int = 8) -> int:
    """64-bit difference hash: robust to scaling and small colour shifts."""
    small = image.convert("L").resize((size + 1, size), Image.Resampling.LANCZOS)
    px = list(small.tobytes())
    bits = 0
    for row in range(size):
        for col in range(size):
            left = px[row * (size + 1) + col]
            right = px[row * (size + 1) + col + 1]
            bits = (bits << 1) | (1 if left > right else 0)
    return bits


def hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def _pad(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    if image.size == size:
        return image
    canvas = Image.new("RGB", size, _PAD)
    canvas.paste(image, (0, 0))
    return canvas


def compare(
    baseline: Path,
    current: Path,
    *,
    tolerance: int = 16,
    diff_path: Path | None = None,
) -> VisualDiff:
    """Count pixels whose largest channel difference exceeds ``tolerance``.

    Images of different sizes are padded to the larger size, so added or removed height
    counts as changed. When ``diff_path`` is given and anything changed, writes the
    current screenshot faded with the changed pixels in red.
    """
    with Image.open(baseline) as a_raw, Image.open(current) as b_raw:
        a = a_raw.convert("RGB")
        b = b_raw.convert("RGB")
    size = (max(a.width, b.width), max(a.height, b.height))
    pa, pb = _pad(a, size), _pad(b, size)
    r, g, bl = ImageChops.difference(pa, pb).split()
    delta = ImageChops.lighter(ImageChops.lighter(r, g), bl)
    mask = delta.point(lambda v: 255 if v > tolerance else 0)
    changed = mask.histogram()[255]
    bbox = mask.getbbox()
    result = VisualDiff(
        changed_pixels=changed,
        total_pixels=size[0] * size[1],
        hash_distance=hamming(dhash(a), dhash(b)),
        size_changed=a.size != b.size,
        baseline_size=a.size,
        current_size=b.size,
        bbox=bbox,
    )
    if diff_path is not None and changed:
        faded = Image.blend(pb, Image.new("RGB", size, (255, 255, 255)), 0.7)
        red = Image.new("RGB", size, (230, 30, 60))
        Image.composite(red, faded, mask).save(diff_path, optimize=True)
    return result


def update_baseline(current: Path, baseline: Path) -> None:
    baseline.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(current, baseline)
