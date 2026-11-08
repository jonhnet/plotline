// Paper geometry stays in mm in recipes/APIs. This preference is presentation
// only, shared by every Studio on this origin; pen diameters remain in mm.
const storageKey='plotline-paper-unit',listeners=new Set(),fields=new Map();
let unit='in';
try{unit=(localStorage.getItem(storageKey)??localStorage.getItem('squidprint-paper-unit'))==='mm'?'mm':'in';}catch{}
export const paperUnit=()=>unit;
const factor=(power=1,u=unit)=>(u==='in'?25.4:1)**power;
export const paperValue=(mm,power=1)=>mm/factor(power);
export const paperMillimeters=(value,power=1)=>Number((value*factor(power)).toPrecision(15));
export const paperUnitLabel=(power=1)=>unit+(power===2?'²':'');
const decimal=(n,precision)=>Number(n.toFixed(precision)).toString();
export const paperLength=(mm,{precision=2,power=1}={})=>`${decimal(paperValue(mm,power),precision)} ${paperUnitLabel(power)}`;
export const paperSize=(mm,{precision=2}={})=>`${mm.map(n=>decimal(paperValue(n),precision)).join(' × ')} ${unit}`;
export function onPaperUnitChange(listener){listeners.add(listener);return ()=>listeners.delete(listener);}
function change(next){
  if(next===unit)return;
  // Read pending input using the unit in which it was entered, before repaint.
  const values=[...fields].map(([input])=>[input,readPaperField(input)]);
  unit=next;
  for(const [input,mm] of values)writePaperField(input,mm);
  for(const listener of listeners)listener(unit);
}
export function setPaperUnit(next){
  if(!['in','mm'].includes(next))throw Error('Unknown paper unit');
  try{localStorage.setItem(storageKey,next);}catch{}
  change(next);
}
if(typeof window!=='undefined')window.addEventListener('storage',event=>{
  if(event.key===storageKey||event.key===null)change(event.newValue==='mm'?'mm':'in');
});
export function writePaperField(input,mm){
  const field=fields.get(input);if(!field)throw Error('Paper field is not bound: '+input.id);
  field.mm=mm;field.unit=unit;
  input.value=Number.isFinite(mm)?decimal(paperValue(mm,field.power),8):'';
  field.text=input.value;
  for(const bound of ['min','max'])if(field[bound]!=null)input[bound]=String(field[bound]/factor(field.power));
}
export function readPaperField(input){
  const field=fields.get(input);if(!field)throw Error('Paper field is not bound: '+input.id);
  // Keeping the original value avoids rounding geometry during display changes,
  // recipe reads, and edits to another field on the same form.
  return input.value===field.text?field.mm:input.value===''?NaN:Number((input.valueAsNumber*factor(field.power,field.unit)).toPrecision(15));
}
// Every document workspace mounts the same preference control in its tab row.
function mountPreferences(root){
  const bar=root.querySelector('.document-bar');if(!bar||bar.querySelector('.studio-preferences'))return;
  const menu=document.createElement('details');menu.className='studio-preferences';
  menu.innerHTML=`<summary aria-label="Studio preferences" title="Studio preferences"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m10 3-.6 2.4-2 .9-2.2-.7-2 3.4L5 10.7v2.6l-1.8 1.7 2 3.4 2.2-.7 2 .9L10 21h4l.6-2.4 2-.9 2.2.7 2-3.4-1.8-1.7v-2.6l1.8-1.7-2-3.4-2.2.7-2-.9L14 3Z"/><circle cx="12" cy="12" r="3"/></svg></summary><div class="studio-preferences-body"><label>Paper units<select id="paper-unit" data-paper-unit-selector><option value="in">Inches</option><option value="mm">Millimeters</option></select></label></div>`;
  bar.append(menu);
  document.addEventListener('click',event=>{if(!menu.contains(event.target))menu.open=false;});
  document.addEventListener('keydown',event=>{if(event.key==='Escape'&&menu.open){menu.open=false;menu.querySelector('summary').focus();}});
}
export function bindPaperUnits(root=document){
  mountPreferences(root);
  for(const input of root.querySelectorAll('input[data-paper-length]')){
    if(fields.has(input))continue;
    const initial=input.valueAsNumber;
    fields.set(input,{power:Number(input.dataset.paperPower)||1,min:input.hasAttribute('min')?Number(input.min):null,max:input.hasAttribute('max')?Number(input.max):null});
    // Measured margins need not lie on a spinner grid in either display unit.
    input.step='any';writePaperField(input,initial);
  }
  const selectors=[...root.querySelectorAll('select[data-paper-unit-selector]')];
  for(const selector of selectors){
    selector.addEventListener('input',event=>event.stopPropagation());
    selector.addEventListener('change',event=>{event.stopPropagation();setPaperUnit(selector.value);});
  }
  const render=()=>{
    for(const selector of selectors)selector.value=unit;
    for(const label of root.querySelectorAll('[data-paper-unit]'))label.textContent=paperUnitLabel(Number(label.dataset.paperPower)||1);
  };
  render();return onPaperUnitChange(render);
}
