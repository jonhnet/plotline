import {paperSize,paperLength} from '/paper-units.js';
// Media remains a complete saved snapshot; discovering new capabilities never
// mutates the document. Explicit size/orientation/margin edits replace it.
export const close = (a,b) => Math.abs(a-b)<0.001;
export const sameSize = (a,b) => a.every((n,i)=>close(n,b[i]));
const orientations=['landscape','portrait','landscape-reverse','portrait-reverse'];
export const angle = name => orientations.indexOf(name)*90;
export const orientation = media => orientations[(media.orientation_deg??(media.sheet_size_mm[0]>=media.sheet_size_mm[1]?0:90))/90];
export function orientProfile(media,turn){
  const result=structuredClone(media),base=angle(orientation(media)),[w,h]=media.sheet_size_mm;
  if(turn!==base&&turn!==(base+180)%360)throw Error('Orientation must match the sheet dimensions.');
  result.orientation_deg=turn;
  if(turn!==base){
    const [x,y,rw,rh]=result.printable_rect_mm;
    result.printable_rect_mm=[+(w-x-rw).toFixed(6),+(h-y-rh).toFixed(6),rw,rh];
    result.id=result.id.slice(0,56)+`-r${turn}`;
    if(result.calibration){
      const [a,b,c,d,e,f]=result.calibration.sheet_to_device_units;
      result.calibration.sheet_to_device_units=[-a,-b,-c,-d,a*w+c*h+e,b*w+d*h+f];
    }
  }
  return result;
}
export const margins = media => {
  const [w,h]=media.sheet_size_mm,[x,y,rw,rh]=media.printable_rect_mm;
  return [y,w-x-rw,h-y-rh,x]; // top, right, bottom, left
};
export const marginMode = media => media.id.startsWith('map-custom-')?'custom':
  media.id.startsWith('hp7585-')||media.calibration?.device==='HP7585B'?'plotter':'custom';
export function paperFor(media,papers){
  const sides=[...media.sheet_size_mm].sort((a,b)=>a-b);
  return papers.find(p=>sameSize(p.size_mm,sides))||{id:'saved-size',label:`Saved size · ${paperSize(sides)}`,size_mm:sides};
}
export function plotterProfile(size,catalog,capabilities,choice){
  // Last matching profile wins: current Plotline Studios data overrides the offline
  // fallback, but only when a user explicitly applies it.
  const base=size[0]>=size[1]?0:90,turn=choice===undefined?base:angle(choice);
  const profile=[...(catalog.plotter_media||[]),...(capabilities?.capabilities.media_profiles||[])]
    .filter(m=>marginMode(m)==='plotter'&&sameSize(m.sheet_size_mm,size)&&angle(orientation(m))===base).at(-1);
  return profile&&orientProfile(profile,turn);
}
export function outsidePlotter(media,plotter){
  return !!plotter&&margins(media).some((n,i)=>n+0.001<margins(plotter)[i]);
}
export function customProfile(size,edges,inset,plotter,choice){
  const [w,h]=size,[t,r,b,l]=edges;
  if(edges.some(n=>!Number.isFinite(n)||n<0)||!Number.isFinite(inset)||inset<0||
      w-l-r-2*inset<50||h-t-b-2*inset<50)throw Error(`Margins must leave at least ${paperLength(50)} of usable map width and height.`);
  const result={schema:'plotline.media-profile/1',id:`map-custom-${Math.round(w*100)}-${Math.round(h*100)}`,revision:1,
    label:'Custom margins',sheet_size_mm:[...size],printable_rect_mm:[l,t,w-l-r,h-t-b],content_inset_mm:inset};
  result.orientation_deg=choice===undefined?(plotter?.orientation_deg??(w>=h?0:90)):angle(choice);
  // An inward artistic margin does not discard a valid physical registration.
  if(plotter?.calibration&&!outsidePlotter(result,plotter))result.calibration=structuredClone(plotter.calibration);
  return result;
}
