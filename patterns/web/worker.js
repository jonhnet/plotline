import {generateSteps,fieldSampling,physicalOptions,refinementKey,loadLimits} from './generators.js';
import {isComplex} from './complex.js';
import {ProgressivePreview} from './preview.js';
let appearance,renderer;
const yieldInput=()=>new Promise(resolve=>setTimeout(resolve,0));
self.onmessage=({data})=>{
  if(data.type==='appearance'){appearance=data.recipe;renderer?.update(appearance);return;}
  run(data).catch(error=>self.postMessage({error:error.message}));
};
async function run({family,params,options={},recipe}){
  await loadLimits();
  appearance=recipe;let anchor=null,lastFrame=0,lastYield=performance.now(),lastProgress=0;
  async function drawFrame(paths,points,force=false){
    if(!renderer||(!force&&performance.now()-lastFrame<250))return;
    lastFrame=performance.now();const frame=await renderer.bitmap();self.postMessage({frame,paths,points},[frame]);
  }
  async function stage(stageParams,stageOptions,start,end){
    renderer=anchor&&appearance&&typeof OffscreenCanvas!=='undefined'?new ProgressivePreview(appearance,anchor):null;
    if(appearance)stageOptions={...stageOptions,...physicalOptions({...appearance,params:stageParams})};
    const steps=generateSteps(family,stageParams,stageOptions);let step,paths=0,points=0;
    while(!(step=steps.next()).done){
      const value=step.value;
      if(value.batch){renderer?.append(value.batch);paths=value.paths;points=value.points;}
      const now=performance.now();
      if(now-lastProgress>50){lastProgress=now;self.postMessage({progress:{fraction:start+(end-start)*value.fraction,label:value.label,paths,points}});}
      await drawFrame(paths,points);
      if(now-lastYield>16){await yieldInput();lastYield=performance.now();}
    }
    await drawFrame(paths,points,true);return step.value;
  }
  async function checkpoint(geometry,stageParams,samplingResolution){
    anchor=geometry;self.postMessage({checkpoint:{geometry,params:stageParams,samplingResolution}});await yieldInput();
  }
  let result;
  if(isComplex(family)&&recipe){
    const coarse=await stage(params,{...options,resolution:280},0,.02);
    await checkpoint(coarse,params,280);
    const sampling=fieldSampling(coarse,appearance),target=Math.min(sampling.resolution,recipe.sampling_limit||Infinity);
    if(target>560){const middle=await stage(params,{...options,resolution:560},.02,.08);await checkpoint(middle,params,560);}
    result=target===280?coarse:await stage(params,{...options,resolution:target},target>560?.08:.02,1);
    const actual=fieldSampling(result,appearance);
    result.sampling={...sampling,resolution:target,cell_mm:actual.cell_mm*actual.resolution/target};
    result.sampling.limited=result.sampling.cell_mm>result.sampling.target_mm*1.01;
  }else{
    const key=refinementKey(family),first={lorenz:40,rossler:100,barnsley:12,crystal:2,woven:3,dla:500,levy:7,clifford:1,dejong:1,peano:2,nested_polygons:10,pythagoras:3,lightning:4,inversion:2,ammann:1,colonization:1,architecture:1,starweave:1,guilloche:12,wave_contours:1,reaction:1000,flowers:1,schottky:2,pinwheel:1,hatched_solids:10,lsystem:2}[family];
    const levels=['menger','penrose','crystal','tetrahedron','minkowski','carpet'].includes(family)&&params.depth>2?Array.from({length:params.depth-1},(_,i)=>i+2):first&&params[key]>first?[first,params[key]]:
      family==='gosper'&&params.depth>2?[2,params.depth]:['koch','dragon','hilbert'].includes(family)&&params.depth>6?[params.depth-1,params.depth]:[params[key]];
    const weights=levels.map(d=>first&&['duration','strands','particles','layers','lines','time','density','whorls'].includes(key)?d:family==='menger'?10**d:2**(d||0)),total=weights.reduce((s,w)=>s+w,0);let done=0;
    for(let i=0;i<levels.length;i++){
      const stageParams={...params,...(levels[i]===undefined?{}:{[key]:levels[i]})};
      result=await stage(stageParams,options,done/total,(done+weights[i])/total);done+=weights[i];
      if(i<levels.length-1)await checkpoint(result,stageParams);
    }
  }
  self.postMessage({result});renderer=null;
}
