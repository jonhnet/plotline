// Coordinate diagnostics must agree with physical preparation, in either view.
import assert from 'node:assert/strict';
import {drawingBounds,measuredWindow,outsideEdges,layout,rectangle} from '../web/geometry.js';
const observation={units_per_mm:40,limits:[0,-2000,8000,2000]};
const raw={units_per_mm:40,summary:{width_mm:180,height_mm:80,bounds:[4400,-1600,11600,1600]}};
const setup={window:'planned',rotation:0,facing:'artwork',paper_x_mm:240,paper_y_mm:140,margins_mm:[20,20,20,20]};
assert.equal(measuredWindow(setup,null),null);
assert.deepEqual(drawingBounds(raw,setup),[-90,-40,90,40]);
assert.deepEqual(outsideEdges(drawingBounds(raw,setup),measuredWindow(setup,observation)),[{edge:'back (−X)',mm:90}]);
const registered={...setup,registration:{sheet_size_mm:[240,140],sheet_to_device_units:[40,0,0,-40,3200,2800],limits:[4000,-2000,12000,2000]}};
assert.deepEqual(layout(registered,observation).paper,[80,-70,320,70]);
assert.deepEqual(layout(registered,observation).window,[100,-50,300,50],'A stale measurement cannot shrink planned bounds');
for(const rotation of [0,90,180,270])for(const facing of ['artwork','plotter']){
  const s={...registered,rotation,facing};
  assert.deepEqual(drawingBounds(raw,s),[110,-40,290,40],'Registered geometry is never centered or rotated again');
  assert.deepEqual(measuredWindow(s,observation),[0,-50,200,50]);
  assert.deepEqual(outsideEdges(drawingBounds(raw,s),measuredWindow(s,observation)),[{edge:'front (+X)',mm:90}]);
  const projected=rectangle(measuredWindow(s,observation),facing,rotation);
  const size=(facing==='plotter'||rotation%180)?[100,200]:[200,100];
  assert.deepEqual(projected.slice(2),size);
  const reported={...setup,window:'reported',rotation,facing};
  assert.deepEqual(measuredWindow(reported,observation),[-100,-50,100,50],'Unregistered reported-mode plans use window center');
  assert.deepEqual(drawingBounds(raw,reported),rotation%180?[-40,-90,40,90]:[-90,-40,90,40]);
  assert.deepEqual(outsideEdges(drawingBounds(raw,reported),measuredWindow(reported,observation)),rotation%180?[{edge:'left (−Y)',mm:40},{edge:'right (+Y)',mm:40}]:[]);
}
assert.deepEqual(outsideEdges([0,0,100,100],[0,0,100,100]),[]);
console.log('PASS physical offsets, reported centering, registered origins, four rotations and both viewpoints.');
