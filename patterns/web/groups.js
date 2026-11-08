// A single browsing home for every generator. Group choice is a UI preference,
// never part of a saved artwork recipe.
export const familyGroups=[
 {id:'curves',name:'Curves & waves',families:['koch','dragon','levy','minkowski','hilbert','peano','gosper','spirograph','lissajous','harmonograph','guilloche','wave_contours']},
 {id:'plants',name:'Plants & growth',families:['plant','barnsley','phyllotaxis','crystal','dla','colonization','lightning','flowers','lsystem']},
 {id:'tilings',name:'Tilings & weaving',families:['penrose','truchet','ammann','pinwheel','starweave','woven']},
 {id:'recursive',name:'Recursive shapes',families:['sierpinski','carpet','nested_polygons','pythagoras']},
 {id:'complex',name:'Complex & inversion',families:['apollonian','inversion','schottky','julia','mandelbrot','burning_ship','newton']},
 {id:'fields',name:'Attractors & fields',families:['lorenz','rossler','clifford','dejong','reaction']},
 {id:'solids',name:'3D forms',families:['menger','tetrahedron','architecture','hatched_solids']},
];
export const groupForFamily=id=>familyGroups.find(g=>g.families.includes(id));
