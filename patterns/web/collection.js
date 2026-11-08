import {botanicalFamilies,botanicalGenerators} from './botanical.js';
import {pinwheelFamily,pinwheel} from './pinwheel.js';
import {schottkyFamily,schottky} from './schottky.js';
import {solidFamily,hatched_solids} from './hatched-solids.js';
export const collectionFamilies=[botanicalFamilies[0],schottkyFamily,pinwheelFamily,solidFamily,botanicalFamilies[1]];
export const collectionGenerators={...botanicalGenerators,pinwheel,schottky,hatched_solids};
