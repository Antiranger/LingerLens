const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const os=require('node:os');
const asar=require('@electron/asar');
const {auditPackage,APP_FILES}=require('../desktop/audit-package.cjs');

test('packager and package audit consume the same manifest',()=>{
  const config=require('../desktop/builder.cjs');
  assert.deepEqual(config.files,APP_FILES);
  assert.notEqual(config.files,APP_FILES);
  assert.equal(Object.isFrozen(APP_FILES),true);
  assert.ok(APP_FILES.includes('desktop/health.cjs'));
});

async function fixture(extra, verify) {
  const temp=fs.mkdtempSync(path.join(os.tmpdir(),'ll-package-'));
  try {
    const input=path.join(temp,'input'),app=path.join(temp,'app');
    const resources=process.platform === 'darwin'
      ? path.join(app,'LingerLens.app','Contents','Resources') : path.join(app,'resources');
    fs.mkdirSync(resources,{recursive:true});
    for(const name of [...APP_FILES,...extra]) {
      const file=path.join(input,name);fs.mkdirSync(path.dirname(file),{recursive:true});
      fs.writeFileSync(file,'fixture');
    }
    await asar.createPackage(input,path.join(resources,'app.asar'));
    await verify(app,resources);
  } finally {fs.rmSync(temp,{recursive:true,force:true});}
}

test('a real ASAR with the health probe passes, but extra code does not',async()=>{
  await fixture([],app=>assert.doesNotThrow(()=>auditPackage(app)));
  await fixture(['desktop/unexpected.cjs'],app=>assert.throws(()=>auditPackage(app),/Unexpected packaged paths/));
});

test('private runtime data remains forbidden beside the ASAR',async()=>{
  await fixture([], (app,resources)=>{
    const file=path.join(resources,'backend','providers.json');
    fs.mkdirSync(path.dirname(file),{recursive:true});fs.writeFileSync(file,'{}');
    assert.throws(()=>auditPackage(app),/providers.json/);
  });
});

test('builder normalization cannot rewrite the audit allowlist', async()=>{
  const config=require('../desktop/builder.cjs');
  const before=[...config.files];
  try {
    config.files.splice(0,config.files.length,{from:'.',to:'.',filter:['**/*']});
    assert.ok(APP_FILES.includes('desktop/health.cjs'));
    assert.equal(typeof APP_FILES[0],'string');
    await fixture([],app=>assert.doesNotThrow(()=>auditPackage(app)));
    await fixture(['unexpected.cjs'],app=>assert.throws(()=>auditPackage(app)));
  } finally {config.files.splice(0,config.files.length,...before);}
});
