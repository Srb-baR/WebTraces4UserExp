"""Extract the four vector figures directly from the supplied short-paper PDF."""
import argparse
from pathlib import Path

import pymupdf

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('paper', type=Path)
args = parser.parse_args()
# Page coordinates in PDF points, excluding captions.
boxes = [(2, (53, 83, 296, 241)), (3, (81, 83, 528, 237)),
         (3, (88, 269, 525, 436)), (3, (53, 482, 296, 610.5))]
with pymupdf.open(args.paper) as paper:
    for number, (page_index, coords) in enumerate(boxes, 1):
        clip = pymupdf.Rect(*coords)
        base = Path(__file__).with_name(f'figure_{number}')
        with pymupdf.open() as figure:
            page = figure.new_page(width=clip.width, height=clip.height)
            page.show_pdf_page(page.rect, paper, page_index, clip=clip)
            figure.save(base.with_suffix('.pdf'), garbage=4, deflate=True)
        paper[page_index].get_pixmap(clip=clip, dpi=300).save(base.with_suffix('.png'))
