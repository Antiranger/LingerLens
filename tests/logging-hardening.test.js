const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { createDevLog, MAX_QUEUED_BYTES, MAX_QUEUED_OPERATIONS } = require('../desktop/devlog.cjs');
const delay = ms => new Promise(resolve => setTimeout(resolve,ms));

test('a blocked filesystem keeps the event loop responsive and the queue bounded',async()=>{
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),'ll-slow-log-'));
  let release; const gate=new Promise(resolve=>{release=resolve;});
  let blocked=false;
  const io={...fs.promises,appendFile:async(file,data)=>{
    if(data.length && !blocked){blocked=true;await gate;}
    return fs.promises.appendFile(file,data);
  }};
  const log=createDevLog({}, {io});
  try {
    log.arm(path.join(dir,'slow.log')); assert.equal(await log.flush(),true);
    log.write('in flight'); await delay(1);
    for(let i=0;i<5000;i++) log.write('x'.repeat(8192));
    assert.equal(blocked,true);
    assert.ok(log.state().queuedBytes<=MAX_QUEUED_BYTES);
    assert.ok(log.state().queuedOperations<=MAX_QUEUED_OPERATIONS);
    assert.ok(log.state().droppedChunks>0);
    const start=performance.now();
    assert.equal(await log.flush(25),false,'flush has a deadline, not an unbounded disk wait');
    assert.ok(performance.now()-start<1000);
    let heartbeat=false; setTimeout(()=>{heartbeat=true;},1); await delay(15);
    assert.equal(heartbeat,true,'timers run while the disk is still blocked');
    release(); assert.equal(await log.flush(2000),true);
    assert.equal(log.state().queuedBytes,0);
  } finally {release();await log.close();fs.rmSync(dir,{recursive:true,force:true});}
});

test('log rotation keeps at most four files inside the configured size',async()=>{
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),'ll-rotate-log-'));
  const log=createDevLog({}, {maxFileBytes:128});
  try {
    log.arm(path.join(dir,'run.log'));
    for(let n=0;n<40;n++) log.write('x'.repeat(64));
    assert.equal(await log.flush(),true);
    const files=fs.readdirSync(dir);
    assert.equal(files.length,4);
    for(const file of files) assert.ok(fs.statSync(path.join(dir,file)).size<=128);
  } finally {await log.close();fs.rmSync(dir,{recursive:true,force:true});}
});

test('file diagnostics never invoke synchronous file writes or redirected stderr',async()=>{
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),'ll-nosync-log-'));
  const old=fs.appendFileSync, oldStderr=process.stderr.write;
  fs.appendFileSync=()=>{throw new Error('synchronous file write');};
  process.stderr.write=()=>{throw new Error('redirected stderr');};
  const log=createDevLog({});
  try {
    log.arm(path.join(dir,'run.log'),'header'); log.writeLines(['diagnostic']);
    assert.equal(await log.flush(),true);
    assert.equal(log.state().droppedChunks,0);
  } finally {await log.close();fs.appendFileSync=old;process.stderr.write=oldStderr;fs.rmSync(dir,{recursive:true,force:true});}
});

test('invalid queue limits are rejected rather than disabling retention bounds',()=>{
  for(const limit of [0,-1,NaN,Infinity,1.1]) assert.throws(()=>createDevLog({}, {maxQueuedBytes:limit}),RangeError);
});
