"""生成项目 logo（512x512 PNG）：深色圆角底 + 剑形徽记。

用超采样（4x）再缩小，边缘更干净。只用 Pillow 的基础绘图，不依赖外部素材。
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

S = 512
SS = 4  # 超采样倍数
W = S * SS

BG_TOP = (15, 23, 42)      # slate-900
BG_BOTTOM = (30, 41, 59)   # slate-800
GLOW = (56, 189, 248)      # sky-400
BLADE_TOP = (226, 232, 240)
BLADE_BOT = (148, 163, 184)
GUARD = (250, 204, 21)     # amber-400
GRIP = (120, 113, 108)


def vertical_gradient(size: int, top: tuple[int, int, int], bottom: tuple[int, int, int]) -> Image.Image:
    img = Image.new("RGB", (1, size))
    px = img.load()
    for y in range(size):
        t = y / max(size - 1, 1)
        px[0, y] = tuple(round(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
    return img.resize((size, size), Image.BILINEAR)


def rounded_mask(size: int, radius: int) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=255)
    return mask


def main() -> None:
    base = vertical_gradient(W, BG_TOP, BG_BOTTOM).convert("RGBA")

    # 背后的柔光
    glow = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse((W * 0.18, W * 0.10, W * 0.82, W * 0.74), fill=GLOW + (70,))
    glow = glow.filter(ImageFilter.GaussianBlur(W * 0.06))
    base = Image.alpha_composite(base, glow)

    layer = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)

    cx = W / 2
    # 剑身：竖直向上的细长五边形
    top_y = W * 0.16
    bot_y = W * 0.60
    half = W * 0.055
    blade = [
        (cx, top_y),
        (cx + half, top_y + W * 0.09),
        (cx + half * 0.85, bot_y),
        (cx - half * 0.85, bot_y),
        (cx - half, top_y + W * 0.09),
    ]
    # 剑身纵向渐变
    blade_img = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    bd = ImageDraw.Draw(blade_img)
    bd.polygon(blade, fill=(255, 255, 255, 255))
    grad = vertical_gradient(W, BLADE_TOP, BLADE_BOT).convert("RGBA")
    blade_img = Image.composite(grad, Image.new("RGBA", (W, W), (0, 0, 0, 0)), blade_img.split()[3])
    layer = Image.alpha_composite(layer, blade_img)
    d = ImageDraw.Draw(layer)

    # 剑脊高光
    d.line([(cx, top_y + W * 0.07), (cx, bot_y)], fill=(255, 255, 255, 90), width=int(W * 0.006))

    # 护手（横向）
    gy = bot_y + W * 0.015
    gh = W * 0.035
    gw = W * 0.24
    d.rounded_rectangle(
        (cx - gw / 2, gy, cx + gw / 2, gy + gh), radius=gh * 0.45, fill=GUARD + (255,)
    )

    # 握柄
    hy = gy + gh + W * 0.008
    hh = W * 0.10
    hw = W * 0.045
    d.rounded_rectangle((cx - hw / 2, hy, cx + hw / 2, hy + hh), radius=hw * 0.35, fill=GRIP + (255,))
    # 柄上缠绳
    for i in range(1, 5):
        yy = hy + hh * i / 5
        d.line([(cx - hw / 2, yy), (cx + hw / 2, yy - W * 0.008)], fill=(60, 55, 52, 200), width=int(W * 0.007))

    # 柄头
    pr = W * 0.038
    d.ellipse((cx - pr, hy + hh - pr * 0.2, cx + pr, hy + hh + pr * 1.8), fill=GUARD + (255,))

    base = Image.alpha_composite(base, layer)

    # 底部铭文
    txt = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    td = ImageDraw.Draw(txt)
    try:
        from PIL import ImageFont

        font = ImageFont.truetype("C:/Windows/Fonts/georgia.ttf", int(W * 0.058))
    except Exception:
        font = None
    label = "DIMENSIONAL PORTAL"
    if font:
        box = td.textbbox((0, 0), label, font=font)
        tw = box[2] - box[0]
        td.text(((W - tw) / 2, W * 0.855), label, font=font, fill=(203, 213, 225, 230))
    base = Image.alpha_composite(base, txt)

    out = base.resize((S, S), Image.LANCZOS)
    out.putalpha(rounded_mask(S, int(S * 0.22)))

    Path("assets").mkdir(exist_ok=True)
    out.save("assets/logo.png", "PNG", optimize=True)
    print("saved assets/logo.png", out.size, Path("assets/logo.png").stat().st_size, "bytes")


if __name__ == "__main__":
    main()
