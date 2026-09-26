"""mc3_hud_badge.py - draw the digital speedometer's backing plate

The badge behind the HUD numbers: an outer rounded frame, one tall pill on the
left for the gear, and two stacked pills on the right for rpm and speed.

WHY THE SIZE IS WHAT IT IS

The PS2 wants power-of-two textures, and the art is 3:1, which is not one. So
the canvas is 256x128 and the badge is drawn at its true 3:1 inside it, centred,
with the rest transparent. Nothing is stretched, and the blit can either use the
whole texture (the transparent rows cost nothing) or the tighter UV range this
prints.

The game's HUD space is 512x448 - measured from digital_speedometer.mod, whose
gear sits at (380, 389) and rpm at (470, 374). At that scale the badge from the
mock-up is about 117x39, so 256 texels across is a little over 2x oversampled:
it stays crisp and still costs only 128 KB at 32bpp.

    python mc3_hud_badge.py                 -> output/hud_badge.png
    python mc3_hud_badge.py --scale 2       -> a 512x256 preview
    python mc3_hud_badge.py --out foo.png
"""
import argparse
import os
import sys

from PIL import Image, ImageDraw, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

TEX_W, TEX_H = 256, 128
ASPECT = 3.0                    # the badge's own shape, from the mock-up

# Dark fill, light rim. The rim is what reads at HUD size, so it is deliberately
# heavier than it would be in print.
FILL = (74, 74, 74, 235)
RIM = (255, 255, 255, 255)
GLOW = (255, 255, 255, 70)


def rounded(draw, box, radius, fill=None, outline=None, width=1):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline,
                           width=width)


def build(scale=1):
    w, h = TEX_W * scale, TEX_H * scale
    img = Image.new('RGBA', (w, h), (0, 0, 0, 0))

    # The badge, at its true aspect, centred in the power-of-two canvas.
    bw = w
    bh = int(round(bw / ASPECT))
    top = (h - bh) // 2
    box = (0, top, bw - 1, top + bh - 1)

    rim = max(2, int(round(3 * scale)))
    pad = rim + max(1, scale)

    # A soft halo first, on its own layer, so the rim reads against both a dark
    # road and a bright sky.
    halo = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    hd = ImageDraw.Draw(halo)
    rounded(hd, box, radius=bh // 2, outline=GLOW, width=rim * 3)
    halo = halo.filter(ImageFilter.GaussianBlur(rim * 1.5))
    img.alpha_composite(halo)

    d = ImageDraw.Draw(img)
    rounded(d, box, radius=bh // 2, fill=FILL, outline=RIM, width=rim)

    inner_top = box[1] + pad
    inner_bot = box[3] - pad
    inner_h = inner_bot - inner_top

    # Left pill: the gear. It shares the frame's left corners, so it starts
    # inside the rim and its right end is fully round.
    gear_right = int(round(bw * 0.36))
    rounded(d, (pad, inner_top, gear_right, inner_bot),
            radius=inner_h // 2, fill=FILL, outline=RIM, width=rim)

    # Right pills: rpm over speed, the same gap between them as to the frame.
    gap = max(1, int(round(2 * scale)))
    ph = (inner_h - gap) // 2
    px0 = gear_right + gap * 2
    px1 = bw - pad - 1
    rounded(d, (px0, inner_top, px1, inner_top + ph),
            radius=ph // 2, fill=FILL, outline=RIM, width=rim)
    rounded(d, (px0, inner_bot - ph, px1, inner_bot),
            radius=ph // 2, fill=FILL, outline=RIM, width=rim)

    return img, box, (gear_right, px0, px1, inner_top, inner_bot)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--scale', type=int, default=1,
                    help='1 = the real 256x128 texture; >1 for a preview')
    ap.add_argument('--out')
    a = ap.parse_args(argv)

    img, box, guides = build(a.scale)
    out = a.out
    if not out:
        try:
            import mc3_output
            base = mc3_output.folder()
        except Exception:
            base = os.path.join(HERE, 'output')
        if not os.path.isdir(base):
            os.makedirs(base)
        out = os.path.join(base, 'hud_badge%s.png'
                           % ('' if a.scale == 1 else '_x%d' % a.scale))
    img.save(out)

    w, h = img.size
    gear_right, px0, px1, itop, ibot = guides
    print(out)
    print('  texture       %dx%d  (power of two, RGBA)' % (w, h))
    print('  the plate     y %d..%d  -> UV v from %.4f to %.4f'
          % (box[1], box[3], box[1] / float(h), (box[3] + 1) / float(h)))
    print('  gear pill     x %d..%d' % (0, gear_right))
    print('  right pills   x %d..%d' % (px0, px1))
    print('  in the 512x448 HUD space, drawn at %.0fx%.0f:'
          % (117.0, 117.0 / ASPECT))
    print('    centre of the pills, in screen coordinates, goes into --place of the mod')
    return 0


if __name__ == '__main__':
    sys.exit(main())
