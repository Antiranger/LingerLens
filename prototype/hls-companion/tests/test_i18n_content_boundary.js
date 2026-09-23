const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
test('localization observes only UI sinks, never transcript/chat/title content', () => {
  const observed=[];
  const nodes=Object.fromEntries(['stateText','subtitlesTimelineList','chatTimelineList','streamTitle'].map(id => [id,{id,childNodes:[]}]));
  const context={console, navigator:{languages:['en-US']},
    document:{documentElement:{},querySelector:()=>null,getElementById:id=>nodes[id]||null,dispatchEvent(){},createTextNode:text=>({nodeType:3,nodeValue:text})},
    window:{localStorage:{getItem:()=>null}},
    MutationObserver:class {observe(node){observed.push(node.id);} disconnect(){}},
    CustomEvent:class {}};
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../web-player/i18n.js'),'utf8'),context);
  assert.ok(observed.includes('stateText'));
  for(const id of ['subtitlesTimelineList','chatTimelineList','streamTitle']) assert.ok(!observed.includes(id),id);
});
