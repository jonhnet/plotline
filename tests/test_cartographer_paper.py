"""Physical paper/orientation snapshots and a portrait vector handoff."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import pymupdf

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from cartographer.model import default_recipe, layout, media_profiles, migrate, validate_recipe
from cartographer.paper import paper_sizes, plotter_profile, plotter_profiles
from cartographer.compose import compose
from print_contract import validate_intent, validate_media


class PaperTests(unittest.TestCase):
    def test_all_named_sizes_in_four_orientations_are_valid(self):
        papers=paper_sizes()
        self.assertEqual(len({p['id'] for p in papers}),len(papers))
        self.assertEqual(next(p['size_mm'] for p in papers if p['id']=='arch-e'),[914.4,1219.2])
        profiles=plotter_profiles()
        self.assertEqual(len(profiles),len(papers)*4)
        for media in profiles:
            with self.subTest(media=media['sheet_size_mm']):
                validate_media(media)
                recipe=default_recipe();recipe['media']=media
                validate_recipe(recipe)

    def test_reverse_orientations_turn_every_margin_and_calibration(self):
        for size,angle in [([431.8,279.4],0),([279.4,431.8],90)]:
            normal=plotter_profile(size,angle);turned=plotter_profile(size,angle+180)
            x,y,w,h=normal['printable_rect_mm']
            for actual,want in zip(turned['printable_rect_mm'],[size[0]-x-w,size[1]-y-h,w,h]):
                self.assertAlmostEqual(actual,want)
            self.assertEqual(turned['orientation_deg'],angle+180)
            validate_media(turned)
            if 'calibration' in normal:
                from print_contract import device_point
                a=device_point([60,35],normal);b=device_point([size[0]-60,size[1]-35],turned)
                for actual,want in zip(a,b):self.assertAlmostEqual(actual,want)
                self.assertEqual(turned['calibration']['hard_clip_units'],normal['calibration']['hard_clip_units'])

    def test_tabloid_turn_rotates_the_printable_rectangle(self):
        landscape=plotter_profile([431.8,279.4]);portrait=plotter_profile([279.4,431.8])
        x,y,w,h=landscape['printable_rect_mm']
        expected=[279.4-y-h,x,h,w]
        for actual,want in zip(portrait['printable_rect_mm'],expected):self.assertAlmostEqual(actual,want)
        self.assertIn('calibration',landscape)
        self.assertNotIn('calibration',portrait,'Do not send an unmodified landscape transform for a portrait page')
        recipe=default_recipe();old=copy.deepcopy(recipe)
        recipe['media']=portrait
        self.assertEqual(recipe['framing'],old['framing'])
        self.assertEqual(recipe['center'],old['center'])
        self.assertEqual(recipe['camera'],old['camera'])
        frame=layout(recipe)['frame_mm']
        self.assertAlmostEqual(frame[2]-frame[0],251.6)
        self.assertAlmostEqual(frame[3]-frame[1],374.95)

    def test_saved_authoring_profile_is_preserved(self):
        recipe=default_recipe();recipe['media']=media_profiles()[2]
        original=copy.deepcopy(recipe['media'])
        validate_recipe(recipe)
        self.assertEqual(migrate(recipe)['media'],original)
        self.assertEqual(recipe['media'],original)

    def test_reversed_portrait_pdf_and_intent_keep_physical_sheet_placement(self):
        from test_cartographer import fixture_data
        recipe=default_recipe();recipe['media']=plotter_profile([279.4,431.8],270)
        recipe['features']['labels']='none'
        with tempfile.TemporaryDirectory() as temp:
            output=Path(temp)/'portrait'
            with patch('cartographer.compose.dataset',return_value=fixture_data(recipe)):
                compose(recipe,output,version='paper-fixture')
            intent=json.loads((output/'map.print-intent.json').read_text())
            validate_intent(intent,output/'map.pdf')
            page=intent['pages'][0]
            self.assertEqual(page['source_size_mm'],[279.4,431.8])
            self.assertEqual(page['target_media'],recipe['media'])
            self.assertEqual(page['target_media']['orientation_deg'],270)
            self.assertEqual(page['placement'],dict(scale=1,rotation_deg=0,offset_mm=[0,0]))
            with pymupdf.open(output/'map.pdf') as pdf:
                self.assertAlmostEqual(pdf[0].rect.width,792,places=3)
                self.assertAlmostEqual(pdf[0].rect.height,1224,places=3)


if __name__=='__main__':unittest.main()
