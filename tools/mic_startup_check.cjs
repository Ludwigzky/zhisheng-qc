/** Deterministic microphone-startup regressions. No server or real device access. */
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const ROOT = path.resolve(__dirname, '..');
const failures = [];
const drain = async () => { for (let i = 0; i < 35; i++) await Promise.resolve(); };
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return {promise, resolve, reject};
}

function harness() {
  let now = 0, serial = 0;
  const timers = new Map(), nodes = new Map(), listeners = {};
  const metrics = {media: 0, contexts: [], order: [], toasts: [], api: 0};
  const mediaQueue = [], resumeQueue = [], closeQueue = [];
  const element = () => ({value: '', textContent: '', innerHTML: '', style: {setProperty() {}},
    classList: {values:new Set(),add(value){this.values.add(value);},remove(value){this.values.delete(value);},
      contains(value){return this.values.has(value);},
      toggle(value,force){const present=force===undefined ? !this.values.has(value) : Boolean(force);
        if(present)this.values.add(value);else this.values.delete(value);return present;}}, dataset: {},
    addEventListener() {}, querySelector: () => null, querySelectorAll: () => [], scrollIntoView() {}});
  const select = (key) => {
    if (!nodes.has(key)) nodes.set(key, element());
    return nodes.get(key);
  };
  const addListener = (name, handler) => (listeners[name] || (listeners[name] = [])).push(handler);
  const sandbox = {console, Promise, JSON, Math, Date, RegExp, Object, Array, String, Number, Boolean,
    Error, TypeError, Set, Map, Float32Array, ArrayBuffer, DataView, AbortController,
    setTimeout(fn, ms = 0) { const id = ++serial; timers.set(id, {at: now + ms, fn}); return id; },
    clearTimeout(id) { timers.delete(id); },
    setInterval() { return 0; }, clearInterval() {},
    location: {hash: '#/steel/21', protocol: 'https:', hostname: '192.168.43.12', origin: 'https://192.168.43.12:8760'},
    localStorage: {getItem() {}, setItem() {}},
    navigator: {userAgent: 'Android regression', permissions: {query: async () => ({state: 'prompt'})}},
    isSecureContext: true,
    document: {readyState: 'complete', querySelector: select, getElementById: select,
      querySelectorAll: () => [], addEventListener: addListener, body: element(), documentElement: element()},
    addEventListener: addListener,
    fetch: async () => { throw new Error('This microphone test must not make a network request'); },
  };
  sandbox.window = sandbox; sandbox.self = sandbox; sandbox.top = sandbox;
  sandbox.navigator.mediaDevices = {getUserMedia() {
    metrics.media++; metrics.order.push('media');
    const operation = mediaQueue.shift();
    return operation ? operation() : new Promise(() => {});
  }};
  sandbox.AudioContext = function FakeAudioContext() {
    const item = {resume: 0, close: 0, disconnect: 0};
    metrics.contexts.push(item); metrics.order.push('context');
    const resume = resumeQueue.shift();
    const close = closeQueue.shift();
    this.state = 'suspended'; this.sampleRate = 48000; this.destination = {};
    this.resume = () => {
      item.resume++; metrics.order.push('resume');
      if (resume) return resume(this);
      this.state = 'running'; return Promise.resolve();
    };
    this.close = () => {
      item.close++; this.state = 'closed';
      return close ? close(this) : Promise.resolve();
    };
    this.createMediaStreamSource = () => ({connect() {}});
    this.createScriptProcessor = () => ({connect() {}, disconnect() { item.disconnect++; }});
    this.createGain = () => ({gain: {}, connect() {}});
  };
  vm.createContext(sandbox);
  const files = ['app.js', 'steel_voice.js', 'steel.js'];
  for (const name of files) {
    let code = fs.readFileSync(path.join(ROOT, 'frontend', name), 'utf8');
    if (name === 'app.js') code = code.replace(/boot\(\);\s*$/, '');
    vm.runInContext(code, sandbox, {filename: name});
  }
  const run = (code) => vm.runInContext(code, sandbox);
  sandbox.__metrics = metrics;
  run(`
    render = async () => {};
    toast = (message) => __metrics.toasts.push(message);
    api = async () => { __metrics.api++; return new Promise(() => {}); };
    S.me = {id:1,name:'Test inspector'};
    S.capture = {mode:'raw',denoise:false,asrMode:'dictation'};
    Steel.template = {voice_targets:[{key:'grade',label:'牌号',kind:'identifier',destination:'header'}]};
    Steel.record = {id:21,version:1,status:'draft',owner_id:1};
    Steel.data = {grade:'Q355B',values:{},extra_rows:[],evidence:[],voice_candidates:[],voice_pending:[]};
    Steel.revision = 1;
  `);
  return {run, metrics, mediaQueue, resumeQueue, closeQueue,
    stream() {
      const result = {stops: 0};
      const track = {getSettings: () => ({sampleRate: 48000}), stop() { result.stops++; }};
      result.getAudioTracks = () => [track]; result.getTracks = () => [track];
      return result;
    },
    async advance(ms) {
      const end = now + ms;
      await drain();
      for (let i = 0; i < 1000; i++) {
        let next;
        for (const [id, timer] of timers) if (timer.at <= end && (!next || timer.at < next.timer.at)) next = {id, timer};
        if (!next) break;
        now = next.timer.at; timers.delete(next.id); next.timer.fn(); await drain();
        if (i === 999) throw new Error('Timer loop did not settle');
      }
      now = end; await drain();
    },
  };
}

async function check(name, test) {
  try { await test(); console.log('  OK  ' + name); }
  catch (error) { failures.push(name); console.error('  BAD ' + name + '\n' + error.stack); }
}

(async () => {
  await check('an insecure phone page fails immediately with a persistent connection diagnosis', async () => {
    const h = harness();
    h.run('window.isSecureContext=false');
    await h.run("steelRecordVoice('grade')");
    assert.equal(h.metrics.media, 0, 'an insecure page must not wait for a browser permission prompt');
    assert.equal(h.metrics.contexts.length, 0);
    assert.equal(h.run('S.micProblem.code'), 'insecure');
    assert.equal(h.run('S.micProblem.detail.secure'), false);
    assert.equal(h.run('Steel.dockExpanded'), true, 'connection errors must remain visible');
    assert.equal(h.run('REC.starting || Steel.starting || S.recording'), false);
  });

  await check('steel microphone begins browser activation before a pending save or render', async () => {
    const h = harness();
    const pending = h.run("steelRecordVoice('grade')");
    assert.equal(h.metrics.media, 1, 'getUserMedia must run in the initial click stack');
    assert.equal(h.metrics.contexts[0].resume, 1, 'resume must run in the initial click stack');
    assert.equal(h.metrics.api, 0, 'opening a microphone must not await a server save');
    await h.run('cancelRec()'); await drain(); await pending;
    assert.equal(h.run('Steel.starting'), false);
  });

  await check('unanswered permission times out, unlocks, and late stream is stopped', async () => {
    const h = harness(), permission = deferred(), late = h.stream();
    h.mediaQueue.push(() => permission.promise);
    let settled = false;
    h.run("steelRecordVoice('grade')").then(() => { settled = true; });
    await h.advance(11999);
    assert.equal(settled, false);
    await h.advance(2);
    assert.equal(settled, true, 'permission may remain pending forever but UI must settle');
    assert.equal(h.run('REC.starting || Steel.starting || S.recording'), false);
    assert.equal(h.run('REC.session'), null);
    assert.ok(h.run('S.micProblem && S.micProblem.message'), 'failure must remain visible after toast expires');
    assert.equal(h.run('Steel.dockExpanded'), true, 'timed-out permission must keep recovery controls visible');
    permission.resolve(late); await drain();
    assert.equal(late.stops, 1);
    assert.equal(h.run('REC.stream'), null);
    assert.equal(h.metrics.contexts[0].close, 1);
  });

  await check('AudioContext resume hang times out after permission and releases tracks', async () => {
    const h = harness(), permission = deferred(), resume = deferred(), stream = h.stream();
    h.mediaQueue.push(() => permission.promise); h.resumeQueue.push(() => resume.promise);
    let settled = false;
    h.run("startRec({extra:{current_key:'grade'}})").then(() => { settled = true; });
    assert.equal(h.metrics.contexts[0].resume, 1);
    await h.advance(3000); permission.resolve(stream); await drain();
    await h.advance(4999); assert.equal(settled, false);
    await h.advance(2); assert.equal(settled, true);
    assert.equal(stream.stops, 1);
    assert.equal(h.run('REC.starting || S.recording'), false);
    assert.equal(h.metrics.contexts[0].close, 1);
    resume.resolve(); await drain();
    assert.equal(h.run('S.recording'), false, 'late audio activation must not restart capture');
  });

  await check('cancel immediately unlocks an unresolved permission request and close promise', async () => {
    const h = harness(), permission = deferred(), neverClose = deferred(), late = h.stream();
    h.mediaQueue.push(() => permission.promise); h.closeQueue.push(() => neverClose.promise);
    let settled = false;
    h.run("startRec({extra:{current_key:'grade'}})").then(() => { settled = true; });
    let canceled = false;
    h.run('cancelRec()').then(() => { canceled = true; });
    await drain();
    assert.equal(canceled, true, 'cleanup must not await an unresponsive AudioContext.close');
    assert.equal(settled, true, 'cancel must wake the pending startup');
    assert.equal(h.run('REC.starting || S.recording'), false);
    assert.equal(h.run('REC.session'), null);
    permission.resolve(late); await drain(); assert.equal(late.stops, 1);
  });

  await check('late callback from an expired attempt cannot unlock or clear its replacement', async () => {
    const h = harness(), oldPermission = deferred(), newPermission = deferred();
    const oldStream = h.stream(), newStream = h.stream();
    h.mediaQueue.push(() => oldPermission.promise, () => newPermission.promise);
    let oldSettled = false;
    h.run("startRec({extra:{current_key:'grade'}})").then(() => { oldSettled = true; });
    await h.advance(12001); assert.equal(oldSettled, true);
    const current = h.run("startRec({extra:{current_key:'width'}})");
    assert.equal(h.metrics.media, 2);
    assert.equal(h.run('REC.starting'), true);
    oldPermission.resolve(oldStream); await drain();
    assert.equal(oldStream.stops, 1);
    assert.equal(h.run('REC.starting'), true, 'old completion must preserve the replacement lock');
    newPermission.resolve(newStream); await current;
    assert.equal(h.run('S.recording'), true);
    assert.equal(h.run('S.recTarget.extra.current_key'), 'width');
    assert.equal(h.run('REC.stream'), newStream);
    await h.run('cancelRec()');
  });

  await check('permission rejection recovers despite a pending AudioContext resume and close', async () => {
    const h = harness();
    const denied = Object.assign(new Error('controlled microphone denial'), {name:'NotAllowedError'});
    h.mediaQueue.push(() => Promise.reject(denied));
    h.resumeQueue.push(() => new Promise(() => {})); h.closeQueue.push(() => new Promise(() => {}));
    let settled = false;
    h.run("steelRecordVoice('grade')").then(() => { settled = true; });
    await drain();
    assert.equal(settled, true);
    assert.equal(h.run('REC.starting || Steel.starting || S.recording'), false);
    assert.ok(h.run('S.micProblem && S.micProblem.message'));
    assert.equal(h.run('Steel.dockExpanded'), true, 'permission denial must not collapse its diagnosis');
    assert.equal(h.metrics.contexts[0].close, 1);
  });

  await check('route departure cancels pending capture before the browser answers', async () => {
    const h = harness(), permission = deferred(), late = h.stream();
    h.mediaQueue.push(() => permission.promise);
    let settled = false;
    h.run("steelRecordVoice('grade')").then(() => { settled = true; });
    h.run("location.hash='#/steel'; cancelRec()"); await drain();
    assert.equal(settled, true);
    assert.equal(h.run('REC.starting || Steel.starting || S.recording'), false);
    permission.resolve(late); await drain();
    assert.equal(late.stops, 1);
    assert.equal(h.run('REC.stream'), null);
  });

  await check('a canceled recording UI callback cannot announce or disturb a newer capture', async () => {
    const h = harness(), oldStream = h.stream(), newStream = h.stream();
    h.mediaQueue.push(() => Promise.resolve(oldStream), () => Promise.resolve(newStream));
    h.run('globalThis.__delayedRecordingUI=new Promise((resolve)=>{globalThis.__releaseRecordingUI=resolve;})');
    let oldSettled = false;
    const old = h.run("startRec({extra:{current_key:'grade'},onState:(phase)=>phase==='recording'?__delayedRecordingUI:undefined})");
    old.then(() => { oldSettled = true; });
    await drain();
    assert.equal(h.run('S.recording'), true);
    assert.equal(oldSettled, false, 'the old start is deliberately waiting on its UI callback');
    assert.equal(h.metrics.toasts.some((text) => text.includes('录音中')), false);
    h.run("location.hash='#/steel'");
    await h.run('cancelRec()');
    assert.equal(oldStream.stops, 1);
    assert.equal(h.run('S.recording'), false);
    h.run("location.hash='#/steel/22'");
    assert.equal(await h.run("startRec({extra:{current_key:'width'}})"), true);
    const announcements = h.metrics.toasts.filter((text) => text.includes('录音中')).length;
    assert.equal(announcements, 1, 'only the replacement recording has completed its UI');
    h.run('__releaseRecordingUI()');
    assert.equal(await old, false);
    assert.equal(h.metrics.toasts.filter((text) => text.includes('录音中')).length, announcements,
      'a late UI callback from a canceled route must not emit a recording toast');
    assert.equal(h.run('REC.stream'), newStream);
    assert.equal(h.run('S.recTarget.extra.current_key'), 'width');
    assert.equal(h.run('S.recording'), true);
    assert.equal(newStream.stops, 0);
    await h.run('cancelRec()');
  });

  await check('a stalled text synchronization times out and releases its network lock', async () => {
    const h = harness();
    h.run(`Steel.current='grade';Steel.text='Q二三五B';Steel.textDirty=true;
      Steel.textTarget={record_id:21,key:'grade'};Steel.textEpoch++;`);
    let settled = false, result;
    h.run('steelCommitText(false)').then((value) => { settled=true;result=value; });
    const controller = h.run('Steel.textJobs.grade.controller');
    await h.advance(9999); assert.equal(settled, false);
    await h.advance(2);
    assert.equal(settled, true);
    assert.equal(result, false);
    assert.equal(h.run('Steel.syncing'), false);
    assert.equal(h.run('Object.keys(Steel.textJobs).length'), 0);
    assert.equal(h.run('Steel.data.grade'), 'Q355B');
    assert.equal(h.run('Steel.textDirty'), true, 'unsynchronized text must remain available to retry');
    assert.ok(h.run("Steel.textError.includes('超时')"));
    assert.equal(controller.signal.aborted, true);
  });

  await check('a late text response after timeout cannot write or unlock a newer synchronization', async () => {
    const h = harness();
    h.run(`globalThis.__manualReplies=[];
      api=async(path)=>path==='/api/steel/asr/parse' ?
        new Promise((resolve)=>__manualReplies.push(resolve)) : {evaluation:{rows:[],issues:[]}};
      Steel.current='grade';Steel.text='Q二三五B';Steel.textDirty=true;
      Steel.textTarget={record_id:21,key:'grade'};Steel.textEpoch++;`);
    const expired = h.run('steelCommitText(false)');
    await h.advance(10001); assert.equal(await expired, false);
    h.run(`Steel.text='Q三四五B';Steel.textDirty=true;Steel.textEpoch++;
      Steel.textTarget={record_id:21,key:'grade'};`);
    const retry = h.run('steelCommitText(false)');
    h.run("__manualReplies[0]({assignments:[{key:'grade',value:'Q235B'}],unknown:[]})");
    await drain();
    assert.equal(h.run('Steel.data.grade'), 'Q355B');
    assert.equal(h.run('Steel.syncing'), true);
    assert.equal(h.run('Object.keys(Steel.textJobs).length'), 1);
    h.run("__manualReplies[1]({assignments:[{key:'grade',value:'Q345B'}],unknown:[]})");
    assert.equal(await retry, true);
    assert.equal(h.run('Steel.data.grade'), 'Q345B');
    assert.equal(h.run('Steel.syncing'), false);
    assert.equal(h.run('Object.keys(Steel.textJobs).length'), 0);
  });

  console.log('FAILS: ' + failures.length);
  if (failures.length) process.exitCode = 1;
})().catch((error) => { console.error(error); process.exitCode = 1; });
