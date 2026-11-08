"""Bridge profiles and independent front-terrain checks; no network or plotter."""
import unittest

import numpy as np
import shapely
from shapely.strtree import STRtree

from cartographer.bridges import BridgeDecks,road_key,project_deck
from cartographer.model import default_recipe
from cartographer.terrain import MeshTerrain
from test_cartographer_terrain import grid_mesh
from build_map import parts


def road(identity,points,cls='secondary',level=1):
    return dict(id=identity,cls=cls,level=level,bridge=True,name='',geometry=shapely.LineString(points))


class BridgeTests(unittest.TestCase):
    def valley(self):
        xs=np.array([-100.,-50.,0.,50.,100.]);ys=np.array([-100.,0.,100.])
        z=np.tile(np.array([30.,5.,0.,10.,40.]),(3,1))
        return grid_mesh(xs,ys,z)

    def decks(self,roads,mesh=None):
        mesh=mesh or self.valley()
        return BridgeDecks(roads,mesh['vertices'],mesh['indices'])

    def test_span_holds_grade_across_valley_and_preserves_approach_heights(self):
        r=road('ravine',[(-100,0),(0,0),(100,0)])
        decks=self.decks([r]);xyz=shapely.get_coordinates(decks.geometry[road_key(r)],include_z=True)
        np.testing.assert_allclose(xyz[:,2],35+xyz[:,0]/20,atol=1e-8)
        self.assertAlmostEqual(decks.stats['max_clearance_m'],35)
        self.assertLess(shapely.force_2d(decks.geometry[road_key(r)]).hausdorff_distance(r['geometry']),1e-8)

    def test_split_ways_and_parallel_carriageways_keep_full_decks(self):
        roads=[road('left',[(-100,-10),(0,-10)]),road('right',[(0,-10),(100,-10)]),
               road('parallel',[(-100,10),(100,10)])]
        decks=self.decks(roads)
        self.assertEqual(decks.stats['spans'],2)
        for r in roads:
            g=decks.geometry[road_key(r)];xyz=shapely.get_coordinates(g,include_z=True)
            np.testing.assert_allclose(xyz[:,2],35+xyz[:,0]/20,atol=1e-8)
            self.assertAlmostEqual(g.length,r['geometry'].length)

    def test_branch_junction_interpolates_between_approaches_not_valley_floor(self):
        roads=[road('west',[(-100,0),(0,0)]),road('east',[(0,0),(100,0)]),
               road('north',[(0,0),(100,100)])]
        decks=self.decks(roads);junction=[]
        for r in roads:
            xyz=shapely.get_coordinates(decks.geometry[road_key(r)],include_z=True)
            junction.extend(xyz[np.linalg.norm(xyz[:,:2],axis=1)<1e-7,2])
        self.assertEqual(len(junction),3)
        self.assertGreater(min(junction),30)
        self.assertLess(max(junction)-min(junction),1e-8)

    def test_ground_ridge_between_original_vertices_is_not_penetrated(self):
        mesh=self.valley();mesh['vertices'][7,2]=65
        r=road('ridge',[(-100,0),(100,0)])
        decks=self.decks([r],mesh);g=decks.geometry[road_key(r)]
        xyz=shapely.get_coordinates(g,include_z=True)
        self.assertGreater(len(xyz),2)
        self.assertAlmostEqual(xyz[np.argmin(np.abs(xyz[:,0])),2],65)
        profile=decks.profiles[0];s=profile['ground_stations']
        heights=np.interp(s,np.r_[0,np.linalg.norm(np.diff(xyz[:,:2],axis=0),axis=1).cumsum()],xyz[:,2])
        self.assertTrue(np.all(heights>=profile['ground_heights']-1e-8))

    def test_resampling_does_not_discard_skewed_or_disconnected_original_lines(self):
        a=road('two',[(.191,-73.137),(.223,81.393)])
        a['geometry']=shapely.MultiLineString([a['geometry'],[(30.019,-75.137),(30.061,79.113)]])
        decks=self.decks([a]);g=decks.geometry[road_key(a)]
        self.assertEqual(len(parts(g,'LineString')),2)
        self.assertAlmostEqual(g.length,a['geometry'].length,places=8)
        self.assertTrue(np.isfinite(shapely.get_coordinates(g,include_z=True)).all())

    def test_incomplete_disconnected_span_falls_back_without_losing_source_lines(self):
        a=road('edge',[(-100,0),(100,0)])
        a['geometry']=shapely.MultiLineString([a['geometry'],[(0,20),(150,20)]])
        decks=self.decks([a])
        # The caller retains and drapes the original geometry when no complete
        # replacement exists, including the component outside the mesh.
        self.assertNotIn(road_key(a),decks.geometry)
        self.assertEqual(decks.stats['incomplete_spans'],1)
        self.assertEqual(decks.stats['fallback_features'],1)

    def test_elevated_bridge_is_hidden_by_foreground_hill_but_visible_over_valley(self):
        r=default_recipe();r['center']=[0,0];r['framing']['scale_denominator']=10000
        r['camera'].update(relief=True,azimuth_deg=0,elevation_deg=25,exaggeration=1)
        xs=np.array([-1000.,-700.,-350.,0.,350.,700.,1000.]);ys=np.array([-1000.,-400.,-100.,100.,400.,1000.])
        z=np.zeros((len(ys),len(xs)));z[2,3]=260
        terrain=MeshTerrain(grid_mesh(xs,ys,z),r)
        deck=shapely.LineString([(-700,100,120),(700,100,120)])
        kept=project_deck(terrain,deck,'bridge-test')
        world=np.asarray(deck.coords);image=world@terrain.projection.T*terrain.scale+terrain.offset
        full=shapely.LineString(image)
        self.assertGreater(kept.length,full.length*.3)
        self.assertLess(kept.length,full.length*.95)
        # Independent oracle uses all projected facets and their depth planes,
        # rather than the front-face clipping index used by project_deck.
        index=STRtree(terrain.projected);delta=image[1]-image[0];depth=world@terrain.camera
        for run in parts(kept,'LineString'):
            for f in np.linspace(.01,.99,20):
                p=run.interpolate(f,normalized=True);xy=np.array(p.coords[0])
                t=(xy-image[0])@delta/(delta@delta);d=depth[0]+t*(depth[1]-depth[0])
                for i in index.query(p,predicate='intersects'):
                    self.assertGreaterEqual(d+2e-4,terrain.depth_planes[i]@np.r_[xy,1])

    def test_above_flat_ground_remains_one_continuous_run_across_facets(self):
        r=default_recipe();r['center']=[0,0];r['framing']['scale_denominator']=10000
        r['camera'].update(relief=True,azimuth_deg=35,elevation_deg=25,exaggeration=3)
        xs=np.linspace(-1000,1000,15);ys=xs
        terrain=MeshTerrain(grid_mesh(xs,ys,np.zeros((15,15))),r)
        g=project_deck(terrain,shapely.LineString([(-500,0,20),(0,0,20),(500,0,20)]),'flat-bridge')
        self.assertEqual(len(parts(g,'LineString')),1)
        self.assertAlmostEqual(terrain.audit_lines[-1]['projected_length_pt'],g.length,places=4)


if __name__=='__main__':unittest.main()
