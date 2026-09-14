const test = require('node:test');
const assert = require('node:assert/strict');
const {createChatOverlay} = require('../web-player/live-messages-client.js');
function setup() {
 const nodes=[];const flags={};
 const container={children:nodes,clientWidth:700,clientHeight:200,classList:{toggle:(k,v)=>flags[k]=v},replaceChildren:()=>nodes.splice(0),append:n=>nodes.push(n)};
 const original=global.document;
 global.document={createElement:()=>({style:{setProperty(k,v){this[k]=v;}},getBoundingClientRect:()=>({width:100}),addEventListener(){},remove(){nodes.splice(nodes.indexOf(this),1);}})};
 return {nodes,flags,overlay:createChatOverlay({container}),cleanup:()=>{global.document=original;}};
}
test('overlay uses leftward bounded tracks and does not duplicate revised messages',()=>{
 const x=setup();try {
  const messages=Array.from({length:30},(_,i)=>({id:String(i),mediaTime:100,text:'chat',kind:'text'}));
  x.overlay.render(100,messages);assert.equal(x.nodes.length,1);
  assert.equal(x.nodes[0].style['--chat-distance'],'-800px');
  x.overlay.render(100.25,messages);assert.equal(x.nodes.length,2);
  x.overlay.render(102,messages);assert.equal(x.nodes.length,2);
  x.overlay.render(102,messages,{paused:true});assert.equal(x.nodes.length,2);assert.equal(x.flags.paused,true);
  x.overlay.render(102,messages,{enabled:false});assert.equal(x.nodes.length,0);
 } finally{x.cleanup();}
});
test('overlay waits for playback and successful translation, text is not HTML',()=>{
 const x=setup();try{
  const m={id:'1',mediaTime:100,text:'<img src=x>',translationState:'pending'};
  x.overlay.render(99,[m]);assert.equal(x.nodes.length,0);
  x.overlay.render(100,[m],{translated:true});assert.equal(x.nodes.length,0);
  m.translationState='done';m.translation='<translated>';
  x.overlay.render(100,[m],{translated:true});assert.equal(x.nodes[0].textContent,'<translated>');
  x.overlay.render(101,[m],{translated:true});assert.equal(x.nodes.length,1);
  x.overlay.render(90,[m]);assert.equal(x.nodes.length,0);
 }finally{x.cleanup();}
});

test('late batch is sorted and enters one at a time without catch-up burst',()=>{
 const x=setup();try{
  const rows=[{id:'late',text:'second',mediaTime:100.4,seq:2},{id:'early',text:'first',mediaTime:100,seq:1}];
  x.overlay.render(100,rows);assert.equal(x.nodes.length,1);assert.equal(x.nodes[0].textContent,'first');
  x.overlay.render(100.25,rows);assert.equal(x.nodes.length,1);
  x.overlay.render(100.5,rows);assert.equal(x.nodes.length,2);assert.equal(x.nodes[1].textContent,'second');
 }finally{x.cleanup();}
});



