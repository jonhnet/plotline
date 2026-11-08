// Sheet coordinates are independent of PDF placement. Device X is paper feed;
// device Y is across the platen. Keep saved media snapshots self-contained.
export const sheetAngle = media => media.orientation_deg ?? (media.calibration
  ? [[1,0,0,-1],[0,1,1,0],[-1,0,0,1],[0,-1,-1,0]].findIndex(axes=>axes.every((v,i)=>Math.abs(media.calibration.sheet_to_device_units[i]-v*media.calibration.units_per_mm)<1e-6))*90
  : media.sheet_size_mm[0]>=media.sheet_size_mm[1]?0:90);
export const platenWidth = (media,angle=sheetAngle(media)) => media.sheet_size_mm[angle%180?0:1];
export const fitsPlaten = width => width>=279.4-1e-6 && width<=914.4+1e-6;
const clean = values => values.map(v=>Number(v.toFixed(6)));

export function loadSheet(media,angle) {
  const result=structuredClone(media),base=sheetAngle(media),turns=(angle-base+360)%360/90;
  if(![0,90,180,270].includes(angle))throw Error('Choose a quarter-turn sheet orientation.');
  const [w,h]=media.sheet_size_mm,[x,y,rw,rh]=media.printable_rect_mm;
  const edges=[x,y,w-x-rw,h-y-rh];
  const [l,t,r,b]=edges.map((_,i)=>edges[(i-turns+4)%4]);
  result.orientation_deg=angle;
  result.printable_rect_mm=clean([l,t,w-l-r,h-t-b]);
  if(turns%2) {
    // Different feed/platen dimensions require different device limits. The old
    // measurement does not calibrate this loading; registration becomes nominal.
    delete result.calibration;
  } else if(turns===2 && result.calibration) {
    const [a,b,c,d,e,f]=result.calibration.sheet_to_device_units;
    result.calibration.sheet_to_device_units=clean([-a,-b,-c,-d,a*w+c*h+e,b*w+d*h+f]);
  }
  return result;
}

export function turnSheet(media) {
  const result=structuredClone(media),[,h]=media.sheet_size_mm,[x,y,rw,rh]=media.printable_rect_mm;
  result.sheet_size_mm.reverse();
  result.orientation_deg=(sheetAngle(media)+90)%360;
  result.printable_rect_mm=clean([h-y-rh,x,rh,rw]);
  if(result.calibration) {
    const [a,b,c,d,e,f]=result.calibration.sheet_to_device_units;
    result.calibration.sheet_to_device_units=clean([-c,-d,a,b,c*h+e,d*h+f]);
  }
  return result;
}
