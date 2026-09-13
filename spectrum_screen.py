#!/usr/bin/env python3
"""Convert PNG/JPG artwork to a ZX Spectrum 128 6912-byte .scr screen."""
from pathlib import Path
import argparse

try:
    from PIL import Image
except ImportError as exc:
    raise SystemExit("Install Pillow first: python3 -m pip install Pillow") from exc

PALETTE = [(0,0,0),(0,0,205),(205,0,0),(205,0,205),
           (0,205,0),(0,205,205),(205,205,0),(205,205,205)]
def nearest(rgb):
    return min(range(8), key=lambda i: sum((rgb[j]-PALETTE[i][j])**2 for j in range(3)))

def dist2(a,b):
    return (a[0]-b[0])**2+(a[1]-b[1])**2+(a[2]-b[2])**2

def two_clusters(pixels):
    """Split a cell's pixels into two colour clusters (k-means, k=2), seeded
    from the brightest and darkest pixel. Quantising each pixel to one of
    the 8 Spectrum colours first and then voting (the previous approach)
    falls apart on anti-aliased edges: blended in-between pixels each snap
    independently to whichever of the 8 palette colours happens to be
    numerically closest, often a colour unrelated to either true region,
    producing visible speckle instead of a clean edge. Clustering in the
    original RGB space first, then deciding ink/paper from the two cluster
    centres, keeps every blended pixel with whichever real region it's
    actually closer to."""
    def lum(p): return 0.299*p[0]+0.587*p[1]+0.114*p[2]
    c0, c1 = max(pixels, key=lum), min(pixels, key=lum)
    group0 = group1 = None
    for _ in range(6):
        group0, group1 = [], []
        for p in pixels:
            (group0 if dist2(p,c0)<=dist2(p,c1) else group1).append(p)
        if group0: c0 = tuple(sum(p[i] for p in group0)//len(group0) for i in range(3))
        if group1: c1 = tuple(sum(p[i] for p in group1)//len(group1) for i in range(3))
    # Paper is whichever cluster covers more of the cell (the background);
    # ink is the other (the foreground detail/text/edge).
    if len(group0)>=len(group1): return c0,c1
    return c1,c0

def convert(src, dst):
    im = Image.open(src).convert("RGB")
    im.thumbnail((256,192), Image.Resampling.LANCZOS)
    canvas = Image.new("RGB",(256,192),(0,0,0))
    canvas.paste(im,((256-im.width)//2,(192-im.height)//2))
    pixels = canvas.load()
    bitmap = bytearray(6144); attrs = bytearray(768)
    for cell_y in range(24):
        for cell_x in range(32):
            cell_pixels = [pixels[x,y] for y in range(cell_y*8,(cell_y+1)*8)
                           for x in range(cell_x*8,(cell_x+1)*8)]
            paper_rgb, ink_rgb = two_clusters(cell_pixels)
            paper, ink = nearest(paper_rgb), nearest(ink_rgb)
            attrs[cell_y*32+cell_x] = ink | (paper<<3)
            for row in range(8):
                bits = 0
                for col in range(8):
                    p = pixels[cell_x*8+col,cell_y*8+row]
                    if dist2(p,ink_rgb) < dist2(p,paper_rgb):
                        bits |= 0x80 >> col
                # The Spectrum's display file interleaves pixel rows in
                # thirds-of-the-screen, not top-to-bottom: row R of character
                # row cell_y lives at ((cell_y//8)*64 + row*8 + cell_y%8) row
                # -slots of 32 bytes, not ((cell_y%8)*8 + (cell_y//8*32) + row)
                # as this previously computed. The old formula collided
                # unrelated character rows onto the same bytes (e.g. cell_y=4
                # row=0 and cell_y=8 row=0 both landed on slot 32), silently
                # overwriting each other - invisible on a smooth gradient but
                # glaring on detailed content like text.
                screen_row = ((cell_y >> 3) << 6) | (row << 3) | (cell_y & 7)
                bitmap[screen_row*32+cell_x] = bits
    Path(dst).write_bytes(bitmap+attrs)
    print(f"wrote {dst} (6912 bytes)")

if __name__ == "__main__":
    p=argparse.ArgumentParser()
    p.add_argument("image", type=Path); p.add_argument("output", type=Path)
    a=p.parse_args(); convert(a.image,a.output)
