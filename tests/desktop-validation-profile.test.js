const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../desktop/main.cjs'), 'utf8');
const start = source.indexOf('if (!smoke && process.env.LINGERLENS_DATA_DIR) {');
const end = source.indexOf("if (smoke) app.setPath", start);
assert.ok(start >= 0 && end > start);
const block = source.slice(start, end);
function run(directory, smoke=false) {
  const changes=[];
  const context={smoke,process:{env:directory?{LINGERLENS_DATA_DIR:directory}:{}},
    path:path.win32,fsSync:{mkdirSync:(...args)=>changes.push(['mkdir',...args])},
    app:{setPath:(...args)=>changes.push(['path',...args])}};
  vm.runInNewContext(block,context);
  return changes;
}
test('isolated validation profile is explicit and uses an absolute directory',()=>{
  assert.deepEqual(run(),[]);
  const changes=run('C:\\Users\\tester\\AppData\\Local\\SubtitleValidation');
  assert.equal(changes.length,2);assert.equal(changes[1][1],'userData');
  assert.equal(changes[1][2],'C:\\Users\\tester\\AppData\\Local\\SubtitleValidation');
});
test('relative user-data overrides fail rather than selecting an unexpected profile',()=>{
  assert.throws(()=>run('relative/profile'),/must be absolute/);
});
test('isolated smoke mode is not redirected to a manual test profile',()=>{
  assert.deepEqual(run('C:\\manual-profile',true),[]);
});
