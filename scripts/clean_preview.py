#!/usr/bin/env python3
"""清理渲染预览图顶部越界的坐标轴色标，产出 README 用的配图。

背景：tests/render-preview.mjs 里 drawAxes() 画的 Y 轴色标会越出画布，
在每张预览图顶部留下一条满宽的纯黄色横条（rgb 248,250,0）。

做法：只用渲染器自己的输出，不手工修图——
找出顶部连续、满宽且为纯黄色的行，用紧接着的第一行真实背景色回填，
其余像素一律不动。这样配图与实验台实际渲染结果一致。

用法：
    python3 scripts/clean_preview.py
产出：
    docs/preview/{motor,robot}.png
"""

from __future__ import annotations

import sys
from pathlib import Path

try:
    from PIL import Image
except ImportError:  # pragma: no cover - 仅在缺少 Pillow 时触发
    sys.exit("需要 Pillow：pip install pillow")

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / ".state" / "preview"
TARGET = ROOT / "docs" / "preview"

IMAGES = [("motor-default.png", "motor.png"), ("robot.png", "robot.png")]

YELLOW = (248, 250, 0)
TOLERANCE = 6


def is_banner_row(image: Image.Image, y: int) -> bool:
    """整行是否都是那条黄色色标（逐像素判定满宽）。"""
    pixels = image.load()
    width, _ = image.size
    for x in range(width):
        r, g, b = pixels[x, y]
        if abs(r - YELLOW[0]) > TOLERANCE or abs(g - YELLOW[1]) > TOLERANCE or b > 12:
            return False
    return True


def clean(image: Image.Image) -> tuple[Image.Image, int]:
    """把顶部满宽黄条替换为紧随其后的真实背景色，返回处理后的图与行数。"""
    pixels = image.load()
    _, height = image.size
    last = -1
    for y in range(height):
        if not is_banner_row(image, y):
            break
        last = y
    if last < 0:
        return image, 0
    if last + 1 >= height:
        raise SystemExit("整张图都是黄色色标，源文件可能已损坏")
    # 用第一条真实背景行的颜色回填；色标只有十几行，背景在该区间近似恒定
    fill = pixels[0, last + 1]
    for y in range(last + 1):
        for x in range(image.width):
            pixels[x, y] = fill
    return image, last + 1


def main() -> int:
    TARGET.mkdir(parents=True, exist_ok=True)
    for source_name, target_name in IMAGES:
        source = SOURCE / source_name
        if not source.exists():
            print(f"跳过（源文件缺失）：{source.relative_to(ROOT)}")
            print("  先生成预览：node --experimental-loader ./tests/browser-resolve.mjs tests/render-preview.mjs")
            return 1
        image = Image.open(source).convert("RGB")
        cleaned, rows = clean(image)
        target = TARGET / target_name
        cleaned.save(target, optimize=True)
        print(f"{source.name} → {target.relative_to(ROOT)}（清理顶部 {rows} 行色标）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
