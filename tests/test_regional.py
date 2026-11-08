from pathlib import Path
import sys
import tempfile
import unittest

import pymupdf
from shapely.geometry import box, LineString

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT/'tools'))
from regional_map import Drawing, GREEN, BLACK
from pdf_native import extract


class RegionalTests(unittest.TestCase):
    def test_polygon_clip_keeps_hole_and_separates_city_from_context(self):
        town=box(20,20,180,180).difference(box(80,80,120,120))
        drawing=Drawing(200,200)
        drawing.paint(LineString([(0,100),(200,100)]),'context/roads','road',color=BLACK,width=2)
        drawing.commit(box(0,0,200,200).difference(town))
        drawing.paint(box(0,0,200,200),'parks','park',color=None,width=0,fill=GREEN)
        drawing.commit(town)
        with tempfile.TemporaryDirectory() as directory:
            pdf=Path(directory)/'map.pdf';drawing.doc.save(pdf)
            with pymupdf.open(pdf) as doc:
                pix=doc[0].get_pixmap(alpha=False)
                self.assertEqual(pix.pixel(50,50),(0,127,0))
                self.assertEqual(pix.pixel(10,50),(255,255,255))
                self.assertEqual(pix.pixel(100,90),(255,255,255))
                self.assertEqual(pix.pixel(100,100),(0,0,0))
                self.assertEqual(pix.pixel(10,100),(0,0,0))
                self.assertEqual(pix.pixel(50,100),(0,127,0))
            events,_=extract(pdf)
            clips=[e for e in events['events'] if e['kind']=='clip_fill']
            self.assertEqual(len(clips),2)
            self.assertTrue(all(c['even_odd'] for c in clips))
        drawing.doc.close()


if __name__=='__main__':unittest.main()
