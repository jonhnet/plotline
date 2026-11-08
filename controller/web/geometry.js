// Browser coordinates are millimetres relative to the drawing-window centre.
// The artwork view follows the source's orientation; the plotter view follows
// the machine. Neither viewpoint changes the physical rotation sent on export.
export function matrix(view, width, rotation, facing) {
  const r = (rotation * Math.PI) / 180,
    c = Math.round(Math.cos(r)),
    s = Math.round(Math.sin(r));
  const k = width / view[2];
  return facing === "plotter"
    ? [s * k, c * k, c * k, -s * k, -view[0] * k, -view[1] * k]
    : [k, 0, 0, -k, -view[0] * k, -view[1] * k];
}
// Project physical plotter coordinates. In artwork view, undo the artwork's
// rotation so the paper and its asymmetric margins turn around upright ink.
export function project([x, y], facing, rotation) {
  if (facing === "plotter") return [y, x];
  const r = (rotation * Math.PI) / 180,
    c = Math.round(Math.cos(r)),
    s = Math.round(Math.sin(r));
  return [c * x + s * y, s * x - c * y];
}
export function rectangle(bounds, facing, rotation) {
  const a = project([bounds[0], bounds[1]], facing, rotation),
    b = project([bounds[2], bounds[3]], facing, rotation);
  return [
    Math.min(a[0], b[0]),
    Math.min(a[1], b[1]),
    Math.abs(a[0] - b[0]),
    Math.abs(a[1] - b[1]),
  ];
}
export function layout(setup, observation) {
  if (setup.registration) {
    const r=setup.registration, [w,h]=r.sheet_size_mm, [a,b,c,d,e,f]=r.sheet_to_device_units;
    const points=[[0,0],[w,0],[0,h],[w,h]].map(([x,y])=>[(a*x+c*y+e)/40,(b*x+d*y+f)/40]);
    return {units:40,limits:r.limits,window:r.limits.map(n=>n/40),
      paper:[Math.min(...points.map(p=>p[0])),Math.min(...points.map(p=>p[1])),Math.max(...points.map(p=>p[0])),Math.max(...points.map(p=>p[1]))]};
  }
  if (setup.window === "reported" && observation) {
    const b = observation.limits.map((x) => x / observation.units_per_mm);
    return {
      limits: observation.limits,
      units: observation.units_per_mm,
      window: [
        -(b[2] - b[0]) / 2,
        -(b[3] - b[1]) / 2,
        (b[2] - b[0]) / 2,
        (b[3] - b[1]) / 2,
      ],
      paper: null,
    };
  }
  const [back, front, left, right] = setup.margins_mm,
    x = setup.paper_x_mm,
    y = setup.paper_y_mm;
  const hx = Math.round((x - back - front) * 20),
    hy = Math.round((y - left - right) * 20);
  const w = hx / 20,
    h = hy / 20;
  return {
    units: 40,
    limits: [-hx, -hy, hx, hy],
    window: [-w / 2, -h / 2, w / 2, h / 2],
    paper: [-w / 2 - back, -h / 2 - left, w / 2 + front, h / 2 + right],
  };
}
export function fitError(job, setup, area) {
  if (!setup.pens.length) return "Choose at least one ink.";
  const [x0, y0, x1, y1] = area.window;
  if (x1 <= x0 || y1 <= y0) return "Margins leave no drawing area.";
  if (setup.registration) {
    const b=job.summary.bounds.map(n=>n/job.units_per_mm);
    if (b[0]<x0-.001 || b[1]<y0-.001 || b[2]>x1+.001 || b[3]>y1+.001)
      return "The PDF lies outside this drawing area. Adjust paper or placement in PDF studio.";
    if (area.limits.some(n=>Math.abs(n)>32767)) return "This paper exceeds the HP7585 coordinate range. Choose a smaller target sheet in PDF studio.";
    return "";
  }
  let [w, h] = [job.summary.width_mm, job.summary.height_mm];
  if (setup.rotation % 180) [w, h] = [h, w];
  if (w > x1 - x0 + 0.001 || h > y1 - y0 + 0.001)
    return "Drawing is too large for this area. Rotate it or choose a larger sheet; scale is preserved.";
  return "";
}

// Multiple logical inks may intentionally share one physical pen.
export function assignSlot(mapping, ink, slot) {
  return {...mapping,[ink]:slot};
}
