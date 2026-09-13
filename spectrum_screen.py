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

def convert(src, dst):
    im = Image.open(src).convert("RGB")
    im.thumbnail((256,192), Image.Resampling.LANCZOS)
    canvas = Image.new("RGB",(256,192),(0,0,0))
    canvas.paste(im,((256-im.width)//2,(192-im.height)//2))
    pixels = canvas.load()
    bitmap = bytearray(6144); attrs = bytearray(768)
    for cell_y in range(24):
        for cell_x in range(32):
            colours = [nearest(pixels[x,y]) for y in range(cell_y*8,(cell_y+1)*8)
                       for x in range(cell_x*8,(cell_x+1)*8)]
            paper = max(set(colours), key=colours.count)
            ink = max((c for c in set(colours) if c != paper), key=colours.count, default=paper)
            attrs[cell_y*32+cell_x] = ink | (paper<<3)
            for row in range(8):
                bits = 0
                for col in range(8):
                    if nearest(pixels[cell_x*8+col,cell_y*8+row]) == ink:
                        bits |= 0x80 >> col
                screen_row = ((cell_y & 7) << 3) | ((cell_y & 24) << 2) | row
                bitmap[screen_row*32+cell_x] = bits
    Path(dst).write_bytes(bitmap+attrs)
    print(f"wrote {dst} (6912 bytes)")

if __name__ == "__main__":
    p=argparse.ArgumentParser()
    p.add_argument("image", type=Path); p.add_argument("output", type=Path)
    a=p.parse_args(); convert(a.image,a.output)
