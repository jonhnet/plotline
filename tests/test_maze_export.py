"""Maze boundary uses native role strokes and exact physical page placement."""
import json
from pathlib import Path
import tempfile
import unittest

import pymupdf
from pattern_pdf import export
from print_contract import validate_intent

class MazeExport(unittest.TestCase):
    def test_maze_source_roles_and_solution_are_frozen_in_the_shared_boundary(self):
        media=json.loads((Path(__file__).resolve().parents[1]/'contracts/v1/examples/hp7585-tabloid-landscape.json').read_text())
        scene=dict(hash='b'*64,document_id='maze-test',recipe=dict(name='Hexagonal maze',media=media,
          inks=[dict(id='walls',label='Black',color_srgb='#000000',width_mm=.5),dict(id='answer',label='Red',color_srgb='#dd3344',width_mm=.25)],
          roles=[dict(label='Walls',ink_id='walls'),dict(label='Solution',ink_id='answer')]),
          paths=[dict(role=0,points=[[50,50],[100,50],[100,100]]),dict(role=1,points=[[60,60],[90,60]])])
        with tempfile.TemporaryDirectory() as directory:
            export(scene,directory,'mazes');root=Path(directory)
            intent=json.loads((root/'intent.json').read_text());validate_intent(intent,root/'drawing.pdf')
            self.assertEqual(intent['authoring'],dict(application='maze-studio',url='/mazes?source_recipe='+'b'*64,recipe_hash='b'*64,document_id='maze-test'))
            self.assertEqual(intent['pages'][0]['placement'],dict(scale=1,rotation_deg=0,offset_mm=[0,0]))
            self.assertEqual([b['ocg_name'] for b in intent['bindings']],['maze-role-0','maze-role-1'])
            with pymupdf.open(root/'drawing.pdf') as doc:
                self.assertAlmostEqual(doc[0].rect.width*25.4/72,431.8,places=4)
                self.assertEqual([round(d['width']*25.4/72,2) for d in doc[0].get_drawings()],[.5,.25])
                self.assertFalse(doc[0].get_images())
            scene['paths']=scene['paths'][:1];export(scene,directory,'mazes')
            intent=json.loads((root/'intent.json').read_text())
            self.assertEqual([i['id'] for i in intent['palette']['inks']],['walls'])
            self.assertEqual(len(intent['bindings']),1)
