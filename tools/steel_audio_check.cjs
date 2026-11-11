/** Imported-audio regressions: virtual codecs/time only; no device or server. */
'use strict';
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const assert=require('node:assert/strict');
const ROOT=path.resolve(__dirname,'..');
const failures=[];
const flush=async()=>{for(let i=0;i<30;i++)await Promise.resolve();await new Promise(setImmediate);};
function deferred(){let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};}
function audioBuffer(seconds=1,channels=[0.6,-0.2]){
  const length=Math.round(seconds*16000);
  const values=channels.map((value)=>new Float32Array(length).fill(value));
  return {sampleRate:16000,length,duration:seconds,numberOfChannels:values.length,getChannelData:(i)=>values[i]};
}
function harness(){
  let now=0,serial=0;
  const timers=new Map(),nodes=new Map(),events={};
  const metrics={microphone:0,resume:0,contexts:[],decode:0,uploads:[],renders:0,toasts:[],picker:0};
  const decoder=[],uploader=[];
  const element=()=>({value:'',files:[],innerHTML:'',textContent:'',style:{setProperty(){}},dataset:{},
    classList:{items:new Set(),add(value){this.items.add(value);},remove(value){this.items.delete(value);},contains(value){return this.items.has(value);},toggle(value,force){const enabled=force===undefined?!this.items.has(value):!!force;if(enabled)this.items.add(value);else this.items.delete(value);return enabled;}},addEventListener(){},scrollIntoView(){},remove(){},
    querySelector(){return null;},click(){metrics.picker++;}});
  const select=(id)=>{if(!nodes.has(id))nodes.set(id,element());return nodes.get(id);};
  const sandbox={console,Promise,JSON,Math,Date,RegExp,Object,Array,String,Number,Boolean,Error,TypeError,
    Set,Map,Float32Array,ArrayBuffer,DataView,Blob,AbortController,
    setTimeout(fn,ms=0){const id=++serial;timers.set(id,{at:now+ms,fn});return id;},
    clearTimeout(id){timers.delete(id);},setInterval(){return 0;},clearInterval(){},
    location:{hash:'#/steel/21'},isSecureContext:true,
    navigator:{mediaDevices:{getUserMedia(){metrics.microphone++;throw Error('Import must never request getUserMedia');}}},
    document:{readyState:'complete',querySelector:select,getElementById:select,querySelectorAll:()=>[],
      addEventListener(name,fn){(events[name]||(events[name]=[])).push(fn);},body:element(),documentElement:element()},
    addEventListener(name,fn){(events[name]||(events[name]=[])).push(fn);},
    localStorage:{getItem(){},setItem(){}},fetch:async()=>{throw Error('No network in codec unit checks');},
  };
  sandbox.window=sandbox;
  sandbox.AudioContext=function(){metrics.resume++;throw Error('Import must use an offline context');};
  sandbox.OfflineAudioContext=function(channels,length,rate){
    metrics.contexts.push({channels,length,rate});
    this.decodeAudioData=(bytes,yes,no)=>{
      metrics.decode++;
      const operation=decoder.shift();
      if(operation)return operation(bytes,yes,no);
      return Promise.resolve(audioBuffer());
    };
  };
  vm.createContext(sandbox);
  for(const name of ['app.js','steel_voice.js','steel_audio.js','steel.js']){
    let code=fs.readFileSync(path.join(ROOT,'frontend',name),'utf8');
    if(name==='app.js')code=code.replace(/boot\(\);\s*$/,'');
    vm.runInContext(code,sandbox,{filename:name});
  }
  sandbox.__metrics=metrics;
  sandbox.__upload=(...args)=>{
    metrics.uploads.push(args);
    const operation=uploader.shift();
    return operation?operation(...args):Promise.resolve({audio_id:'imported.wav',text:'厚度五点六四',
      assignments:[{key:'thickness',value:5.64,confidence:0,raw:'厚度五点六四',destination:'value'}]});
  };
  const run=(code)=>vm.runInContext(code,sandbox);
  run(`
    render=async()=>{__metrics.renders++;};
    toast=(text)=>__metrics.toasts.push(text);
    uploadWav=(...args)=>__upload(...args);
    api=async()=>({evaluation:{rows:[],issues:[],overall:'pending'}});
    S.me={id:1,name:'Test inspector'};S.capture={mode:'raw',denoise:false,asrMode:'dictation'};
    Steel.template={voice_targets:[{key:'thickness',label:'厚度',kind:'number',destination:'value',positive:true}]};
    Steel.record={id:21,status:'draft',owner_id:1,version:1};
    Steel.data={values:{thickness:{value:[5.5],source:'manual',confirmed:true}},extra_rows:[],evidence:[],voice_pending:[],voice_candidates:[]};
    Steel.current='thickness';Steel.revision=1;
    globalThis.snapshot={record_id:21,current_key:'thickness',revision:1,capture:{mode:'raw',denoise:false,asrMode:'dictation'}};
  `);
  sandbox.file={name:'voice.m4a',type:'audio/mp4',size:1024,arrayBuffer:async()=>new ArrayBuffer(20)};
  return {run,metrics,decoder,uploader,sandbox,events,
    async advance(ms){
      const end=now+ms;await flush();
      for(let i=0;i<1000;i++){
        let next;
        for(const [id,timer]of timers)if(timer.at<=end&&(!next||timer.at<next.timer.at))next={id,timer};
        if(!next)break;
        now=next.timer.at;timers.delete(next.id);next.timer.fn();await flush();
        if(i===999)throw Error('Timer loop failed to settle');
      }
      now=end;await flush();
    },
  };
}
async function check(name,work){try{await work();console.log('  OK  '+name);}catch(error){failures.push(name);console.error('  BAD '+name+'\n'+error.stack);}}
(async()=>{
  await check('system picker is immediate, cancels pending web microphone, and never locks canceled selection',async()=>{
    const h=harness();
    h.run('Steel.starting=true;REC.starting=true;');
    assert.equal(h.run('steelChooseAudio()'),true);
    assert.equal(h.metrics.picker,1);
    assert.equal(h.run('Steel.waiting || Steel.starting || REC.starting'),false);
    assert.equal(h.metrics.microphone,0);assert.equal(h.metrics.resume,0);assert.equal(h.metrics.decode,0);
    assert.equal(h.run('SteelAudio.selection.record_id'),21);
    assert.equal(h.run('SteelAudio.selection.current_key'),'thickness');
  });
  await check('stereo is averaged into actual mono 16k PCM WAV and remains a review candidate',async()=>{
    const h=harness();
    assert.equal(await h.run('steelImportAudio(file,snapshot)'),true);
    assert.deepEqual(h.metrics.contexts,[{channels:1,length:1,rate:16000}]);
    assert.equal(h.metrics.microphone,0);assert.equal(h.metrics.resume,0);
    const [blob,url,extra,capture]=h.metrics.uploads[0];
    assert.notEqual(blob,h.sandbox.file);assert.equal(blob.type,'audio/wav');
    const bytes=await blob.arrayBuffer(),view=new DataView(bytes);
    assert.equal(Buffer.from(bytes,0,4).toString(),'RIFF');assert.equal(Buffer.from(bytes,8,4).toString(),'WAVE');
    assert.equal(view.getUint16(22,true),1);assert.equal(view.getUint32(24,true),16000);
    assert.equal(view.getUint16(34,true),16);assert.equal(bytes.byteLength,44+16000*2);
    assert.ok(Math.abs(view.getInt16(44,true)-Math.trunc(0.2*32767))<=1,'Both source channels must contribute');
    assert.equal(url,'/api/steel/asr/audio');assert.equal(extra.record_id,21);assert.equal(extra.current_key,'thickness');
    assert.equal(capture.mode,'raw');
    assert.equal(h.run('Steel.data.values.thickness.value[0]'),5.5,'Original confirmed reading must remain unchanged');
    assert.equal(h.run('Steel.data.voice_candidates[0].value'),5.64);
    assert.equal(h.run('Steel.data.voice_pending[0]'),'thickness');
    h.run("steelConfirmCandidate('thickness')");
    assert.equal(h.run('Steel.data.values.thickness.value[0]'),5.64);
    assert.equal(h.run('Steel.data.values.thickness.confirmed'),true);
    assert.equal(h.run('Steel.data.voice_pending.length'),0);
    assert.equal(h.run('Steel.waiting || SteelAudio.active'),null);
  });
  await check('legacy callback decoding works without AudioContext or microphone permission',async()=>{
    const h=harness();
    h.decoder.push((_bytes,yes)=>{Promise.resolve().then(()=>yes(audioBuffer(1,[0.1])));});
    assert.equal(await h.run('steelImportAudio(file,snapshot)'),true);
    assert.equal(h.metrics.uploads.length,1);assert.equal(h.metrics.microphone,0);assert.equal(h.metrics.resume,0);
  });
  await check('invalid encoded files fail visibly without uploading or changing readings',async()=>{
    const h=harness();h.decoder.push(()=>Promise.reject(Error('Unsupported codec')));
    assert.equal(await h.run('steelImportAudio(file,snapshot)'),false);
    assert.equal(h.metrics.uploads.length,0);assert.equal(h.run('Steel.waiting'),false);
    assert.match(h.run('S.micProblem.message'),/格式/);assert.equal(h.run('Steel.data.values.thickness.value[0]'),5.5);
  });
  await check('files over 10MB, recordings over 30 seconds, and non-finite PCM never upload',async()=>{
    for(const test of ['size','duration','sample']){
      const h=harness();
      if(test==='size')h.sandbox.file.size=10*1024*1024+1;
      if(test==='duration')h.decoder.push(()=>Promise.resolve(audioBuffer(31,[0.1])));
      if(test==='sample')h.decoder.push(()=>{const result=audioBuffer(1,[0.1]);result.getChannelData(0)[0]=NaN;return Promise.resolve(result);});
      assert.equal(await h.run('steelImportAudio(file,snapshot)'),false,test);
      assert.equal(h.metrics.uploads.length,0,test);assert.equal(h.run('Steel.waiting'),false,test);
      assert.ok(h.run('S.micProblem.message'),test);
    }
  });
  await check('decode timeout unlocks immediately and late decoder completion cannot upload',async()=>{
    const h=harness(),decoding=deferred();h.decoder.push(()=>decoding.promise);
    let settled=false;const pending=h.run('steelImportAudio(file,snapshot)').then((value)=>{settled=true;return value;});
    await h.advance(14999);assert.equal(settled,false);
    await h.advance(2);assert.equal(await pending,false);assert.equal(h.run('Steel.waiting'),false);
    decoding.resolve(audioBuffer());await flush();assert.equal(h.metrics.uploads.length,0);
    assert.match(h.run('S.micProblem.message'),/超时/);
  });
  await check('changed form revision discards decoded audio before upload',async()=>{
    const h=harness(),decoding=deferred();h.decoder.push(()=>decoding.promise);
    const pending=h.run('steelImportAudio(file,snapshot)');await flush();
    h.run('Steel.revision++;');decoding.resolve(audioBuffer());
    assert.equal(await pending,false);assert.equal(h.metrics.uploads.length,0);assert.equal(h.run('Steel.waiting'),false);
  });
  await check('route cancellation prevents old decode cleanup from clearing a new record wait',async()=>{
    const h=harness(),decoding=deferred();h.decoder.push(()=>decoding.promise);
    const pending=h.run('steelImportAudio(file,snapshot)');await flush();
    h.run("location.hash='#/steel/22';steelCancelAudio();Steel.record={id:22,status:'draft',owner_id:1};Steel.revision=2;Steel.waiting=true;");
    decoding.resolve(audioBuffer());assert.equal(await pending,false);
    assert.equal(h.metrics.uploads.length,0);assert.equal(h.run('Steel.waiting'),true);
  });
  await check('upload deadline aborts request and ignores its late recognition result',async()=>{
    const h=harness(),upload=deferred();h.uploader.push(()=>upload.promise);
    const pending=h.run('steelImportAudio(file,snapshot)');await flush();
    assert.equal(h.metrics.uploads.length,1);assert.equal(h.run('SteelAudio.phase'),'upload');
    const signal=h.metrics.uploads[0][4];assert.equal(signal.aborted,false);
    await h.advance(60001);assert.equal(await pending,false);assert.equal(signal.aborted,true);
    assert.equal(h.run('Steel.waiting'),false);
    upload.resolve({assignments:[{key:'thickness',value:99,confidence:1}]});await flush();
    assert.equal(h.run('Steel.data.voice_candidates.length'),0);assert.equal(h.run('Steel.data.values.thickness.value[0]'),5.5);
  });
  console.log('FAILS: '+failures.length);
  if(failures.length)process.exitCode=1;
})().catch((error)=>{console.error(error);process.exitCode=1;});
