"""Run with distribution Python: physical raster controls and transform invariants."""
import copy
import tempfile
from pathlib import Path
import unittest
import numpy as np
from PIL import Image
from raster_lab.pdf_render import placed_image, render, PT
from raster_lab.engine import check_dependencies
from raster_lab import engine, palette

class RendererTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):check_dependencies()
    def test_quarter_turn_reflection_and_shear_keep_sample_positions(self):
        rgb=np.full((40,80,3),255,np.uint8);rgb[5:15,10:30]=[0,80,30]
        image=Image.fromarray(rgb)
        for linear in [[80,0,0,40],[0,80,-40,0],[-80,0,0,40],[80,0,20,40],[40,0,0,80]]:
            m=[v*PT for v in linear]+[100*PT,100*PT]
            placed,size,origin=placed_image(image,m)
            a,b,c,d,e,f=m
            point=np.array([a*.25+c*.25+e,b*.25+d*.25+f])/PT
            ij=(point-origin)/size*np.array(placed.size)
            actual=np.array(placed)[int(ij[1]),int(ij[0])]
            self.assertLess(np.abs(actual.astype(int)-[0,80,30]).max(),3)
    def test_modes_auto_and_actual_unequal_widths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'pixels.ppm'
            inks=[dict(id=r,color_srgb=c,width_mm=w) for r,c,w in [('c','#009f7c',.25),('m','#e32991',.4),('y','#efcf00',.6),('k','#000000',.3)]]
            base=dict(pixels=str(source),matrix=[30*PT,0,0,30*PT,0,0],paths=str(root/'paths.npz'),report=str(root/'report.json'),inks=inks,
                options=dict(screening=True,palette='auto',fullness=.75,edge_falloff_mm=6.35,black_treatment='off',inks={r:r.lower() for r in 'CMYK'}))
            for color,expected in [((120,120,120),'black'),((80,120,180),'cmy')]:
                Image.new('RGB',(80,80),color).save(source)
                report=render(base);self.assertEqual(report['screen_palette'],expected);self.assertEqual(report['recovered_paths'],0)
            # Neutral CMYK is K shading, unlike CMY + contrast recovery.
            Image.new('RGB',(80,80),(100,100,100)).save(source)
            request=copy.deepcopy(base);request['options']['palette']='cmyk'
            report=render(request);self.assertGreater(report['screen_paths_by_ink']['K'],0)
            self.assertTrue(all(report['screen_paths_by_ink'][r]==0 for r in 'CMY'))
            # Explicit ink shapes doesn't need any screen materials.
            request['options'].update(screening=False,black_treatment='shapes',inks={'K':'k'})
            report=render(request);self.assertGreater(report['recovered_paths'],0);self.assertEqual(report['screen_paths_by_ink'],{})
            request['options']['black_treatment']='off'
            with self.assertRaisesRegex(ValueError,'Enable screening'):render(request)

    def test_selected_inks_have_independent_ids_widths_and_shared_black_group(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'pixels.ppm'
            from PIL import ImageDraw
            image=Image.new('RGB',(80,80),(35,80,190))
            ImageDraw.Draw(image).rectangle((30,30,60,60),fill='black');image.save(source)
            inks=[dict(id=i,label=i,color_srgb=c,width_mm=w) for i,c,w in
                  [('ocean','#245bd6',.25),('warm','#ff0000',.4),('dark','#000000',.3)]]
            request=dict(pixels=str(source),matrix=[30*PT,0,0,30*PT,0,0],
                paths=str(root/'paths.npz'),report=str(root/'report.json'),inks=inks,
                options=dict(screening=True,palette='selected',screen_inks=['ocean','warm','dark'],
                             fullness=1,edge_falloff_mm=6.35,black_treatment='shapes',inks={'K':'dark'}))
            report=render(request)
            self.assertEqual(report['screen_palette'],'selected')
            self.assertEqual(report['role_inks'],{'P0':'ocean','P1':'warm','K':'dark'})
            self.assertEqual(report['separation']['widths_mm'],[.25,.4,.3])
            self.assertEqual(report['separation']['color_space'],'oklab')
            self.assertGreater(report['recovered_paths'],0)
            with np.load(request['paths'],allow_pickle=False) as data:
                self.assertIn('P0_points',data);self.assertIn('K_points',data)
                self.assertFalse(any(k.startswith('P2_') for k in data))
            # Recovery pen can be excluded from the color mixtures entirely.
            request['options']['screen_inks']=['ocean','warm']
            report=render(request)
            self.assertNotIn('K',report['separation']['colors'])
            self.assertEqual(report['role_inks']['K'],'dark')
            request['options'].update(screening=False,screen_inks=[])
            report=render(request);self.assertEqual(report['screen_paths_by_ink'],{})
            for ids in [[], ['ocean','ocean'], ['missing']]:
                request['options'].update(screening=True,black_treatment='off',screen_inks=ids)
                with self.assertRaises(ValueError):render(request)

    def test_unequal_screen_widths_keep_coverage_by_changing_path_length(self):
        # Hold source, page size and ink colors fixed; swap the two diameters.
        # Inspect the emitted PDF-worker paths with a separate finite-width
        # rasterization, rather than trusting the screen's tone-curve report.
        colors={'blue':'#245bd6','yellow':'#ffff00'}
        target=palette.predict(np.array([[[.35,.35]]]),palette.colors_rgb(tuple(colors.items())))
        pixels=np.tile(np.rint(target*255).astype(np.uint8),(120,120,1))
        measured={ink:{} for ink in colors}
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'pixels.ppm';Image.fromarray(pixels).save(source)
            request=dict(pixels=str(source),matrix=[60*PT,0,0,60*PT,0,0],
                paths=str(root/'paths.npz'),report=str(root/'report.json'),
                options=dict(screening=True,palette='selected',screen_inks=list(colors),
                             fullness=1,edge_falloff_mm=0,black_treatment='off',inks={}))
            for widths in [dict(blue=.3,yellow=.7),dict(blue=.7,yellow=.3)]:
                request['inks']=[dict(id=ink,label=ink,color_srgb=color,width_mm=widths[ink])
                                 for ink,color in colors.items()]
                report=render(request)
                self.assertEqual(report['separation']['widths_mm'],list(widths.values()))
                drawing=engine.Drawing(60,60,.25,{},widths,colors)
                with np.load(request['paths'],allow_pickle=False) as data:
                    for role,ink in report['role_inks'].items():
                        points=data[role+'_points']/PT;offsets=data[role+'_offsets']
                        drawing.paths[ink]=[points[a:b] for a,b in zip(offsets,offsets[1:])]
                for ink in colors:
                    # Exclude the outside 6 mm: finite edges contain a different
                    # number of rows as diameter changes, unlike an interior tone.
                    mask=engine.stroke_mask(drawing,ink,1200,1200,2)
                    coverage=float(mask[120:-120,120:-120].mean()/255)
                    length=sum(map(engine.path_length,drawing.paths[ink]))
                    measured[ink][widths[ink]]=(coverage,length)
                    self.assertAlmostEqual(coverage,.35,delta=.02)
            for ink in colors:
                thin,thick=measured[ink][.3],measured[ink][.7]
                self.assertAlmostEqual(thin[0],thick[0],delta=.015,msg=ink)
                self.assertAlmostEqual(thin[1]/thick[1],.7/.3,delta=.15,msg=ink)

    def test_scanned_swatches_change_paths_and_match_explicit_material_colors(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'pixels.ppm'
            Image.new('RGB',(80,80),'#e8cba0').save(source)
            inks=[dict(id=i,label=i,color_srgb=c,measured_color_srgb=m,width_mm=w)
                  for i,c,m,w in [('yellow','#ffff00','#edae60',.519),('blue','#0000ff','#6182bc',.337)]]
            request=dict(pixels=str(source),matrix=[30*PT,0,0,30*PT,0,0],
                paths=str(root/'paths.npz'),report=str(root/'report.json'),inks=inks,
                options=dict(screening=True,palette='selected',screen_inks=['yellow','blue'],
                             fullness=1,edge_falloff_mm=0,black_treatment='off',inks={}))
            def paths():
                with np.load(request['paths'],allow_pickle=False) as data:
                    return {k:data[k].copy() for k in data}
            original=copy.deepcopy(inks)
            scanned=render(request);actual=paths()
            self.assertEqual(inks,original,'Rendering must not rewrite nominal pen identities')
            self.assertEqual([i['color_srgb'] for i in scanned['screen_inks']],['#edae60','#6182bc'])
            for ink in inks:ink['color_srgb']=ink.pop('measured_color_srgb')
            render(request);explicit=paths()
            self.assertEqual(actual.keys(),explicit.keys())
            for k in actual:np.testing.assert_array_equal(actual[k],explicit[k])
            for ink,nominal in zip(inks,original):ink.update(color_srgb=nominal['color_srgb'],measured_color_srgb=None)
            render(request);uncalibrated=paths()
            self.assertTrue(any(k not in uncalibrated or not np.array_equal(v,uncalibrated[k]) for k,v in actual.items()),
                            'The scan must change the plotted mixtures, not just the report swatches')

if __name__=='__main__':unittest.main()
