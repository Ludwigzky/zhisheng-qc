/* System recorder/file fallback. It never requests a browser microphone. */
'use strict';
const SteelAudio = {selection:null, active:null, sequence:0, phase:'idle'};

function steelAudioSameRecord(snapshot) {
  const route=parseRoute();
  return !steelReadonly() && Steel.record && String(Steel.record.id)===String(snapshot.record_id) &&
    route.name==='steel' && String(route.id)===String(snapshot.record_id);
}
function steelAudioCurrent(operation) {
  return SteelAudio.active===operation && !operation.canceled && steelAudioSameRecord(operation.snapshot) &&
    Steel.revision===operation.snapshot.revision;
}
function steelAudioError(code,message) {
  const error=new Error(message || code);error.audioCode=code;return error;
}
function steelAudioDeadline(promise,operation,milliseconds,message) {
  return new Promise((resolve,reject)=>{
    let settled=false,timer;
    const finish=(fn,value)=>{
      if(settled)return;
      settled=true;clearTimeout(timer);operation.cancelers.delete(cancel);fn(value);
    };
    const cancel=()=>finish(reject,steelAudioError('canceled','已取消录音导入'));
    operation.cancelers.add(cancel);
    timer=setTimeout(()=>{
      finish(reject,steelAudioError('timeout',message));
      try{if(operation.controller)operation.controller.abort();}catch(_){/* optional API */}
      try{if(operation.reader)operation.reader.abort();}catch(_){/* cleanup */}
    },milliseconds);
    Promise.resolve(promise).then((value)=>finish(resolve,value),(error)=>finish(reject,error));
    if(operation.canceled)cancel();
  });
}
function steelAudioRead(file,operation) {
  if(typeof file.arrayBuffer==='function')return file.arrayBuffer();
  return new Promise((resolve,reject)=>{
    if(typeof FileReader==='undefined'){reject(steelAudioError('read','当前浏览器无法读取录音文件，请换用 Chrome / Edge。'));return;}
    const reader=new FileReader();operation.reader=reader;
    reader.onload=()=>resolve(reader.result);
    reader.onerror=()=>reject(steelAudioError('read','无法读取这个录音文件，请重新选择。'));
    reader.onabort=()=>reject(steelAudioError('canceled','已取消录音导入'));
    reader.readAsArrayBuffer(file);
  });
}
function steelAudioDecode(context,bytes) {
  // Old Android engines can expose only the callback form. Modern engines
  // can invoke both a callback and a Promise; settle once in either case.
  return new Promise((resolve,reject)=>{
    let settled=false;
    const finish=(fn,value)=>{if(!settled){settled=true;fn(value);}};
    try{
      const promise=context.decodeAudioData(bytes,(value)=>finish(resolve,value),(error)=>finish(reject,error));
      if(promise && typeof promise.then==='function')promise.then((value)=>finish(resolve,value),(error)=>finish(reject,error));
    }catch(error){finish(reject,error);}
  });
}
function steelCancelAudio() {
  SteelAudio.selection=null;
  const operation=SteelAudio.active;
  if(!operation)return false;
  operation.canceled=true;
  operation.cancelers.forEach((cancel)=>cancel());operation.cancelers.clear();
  try{if(operation.controller)operation.controller.abort();}catch(_){/* optional API */}
  try{if(operation.reader)operation.reader.abort();}catch(_){/* cleanup */}
  if(SteelAudio.active===operation){SteelAudio.active=null;SteelAudio.phase='idle';Steel.waiting=false;}
  return true;
}
function steelChooseAudio() {
  if(steelReadonly())return false;
  if(Steel.waiting || Steel.saving || S.recording){toast('请先结束当前录音或识别');return false;}
  const input=document.getElementById('steel-audio-file');
  if(!input || !steelTarget(Steel.current)){toast('请先选择要填写的字段',true);return false;}
  if(Steel.starting || REC.starting){
    Steel.micAttempt++;Steel.starting=false;
    // cancelRec releases its current session synchronously. Do not await:
    // the native picker must still be opened from this trusted tap.
    cancelRec();
    steelRefreshDock();
  }
  SteelAudio.selection={record_id:Steel.record.id,current_key:Steel.current,revision:Steel.revision,
    capture:Object.assign({},S.capture)};
  input.value='';
  try{input.click();return true;}
  catch(_){SteelAudio.selection=null;toast('无法打开系统录音，请使用导入文件或更换浏览器',true);return false;}
}
async function steelImportAudio(file,snapshot) {
  if(!file || !snapshot || !steelAudioSameRecord(snapshot) || Steel.revision!==snapshot.revision)return false;
  if(Steel.waiting || Steel.saving || S.recording)return false;
  const operation={id:++SteelAudio.sequence,snapshot,canceled:false,cancelers:new Set(),reader:null,
    controller:typeof AbortController!=='undefined'?new AbortController():null};
  SteelAudio.active=operation;SteelAudio.phase='decode';Steel.waiting=true;S.micProblem=null;
  try{
    if(!Number.isFinite(file.size) || file.size<=0)throw steelAudioError('empty','请选择非空录音文件。');
    if(file.size>10*1024*1024)throw steelAudioError('size','录音文件超过10MB，请按每项30秒以内分段录音。');
    const Offline=window.OfflineAudioContext || window.webkitOfflineAudioContext;
    if(!Offline)throw steelAudioError('unsupported','当前浏览器不支持录音文件解码，请用 Chrome / Edge 导入。');
    await render();
    if(!steelAudioCurrent(operation))throw steelAudioError('changed','记录或内容已改变，请重新选择录音。');
    let decoded;
    try{
      const context=new Offline(1,1,16000);
      const work=(async()=>{
        const bytes=await steelAudioRead(file,operation);
        if(!steelAudioCurrent(operation))throw steelAudioError('changed','记录或内容已改变，请重新选择录音。');
        return steelAudioDecode(context,bytes);
      })();
      decoded=await steelAudioDeadline(work,operation,15000,'录音解码超时，请换用较短的录音或其他格式。');
    }catch(error){
      if(error && error.audioCode)throw error;
      throw steelAudioError('format','这个录音格式无法解码。请选用浏览器可播放的 WAV、MP3 或 M4A 文件。');
    }
    if(!steelAudioCurrent(operation))throw steelAudioError('changed','记录或内容已改变，请重新选择录音。');
    if(!decoded || !Number.isFinite(decoded.duration) || decoded.duration<0.3 || decoded.duration>30)
      throw steelAudioError('duration','录音须为0.3至30秒，请一项一句分段录音。');
    const channels=decoded.numberOfChannels,frames=decoded.length;
    if(decoded.sampleRate!==16000 || !Number.isInteger(channels) || channels<1 || channels>8 ||
        !Number.isInteger(frames) || frames<4800 || frames>480000)
      throw steelAudioError('format','录音的采样率或声道格式无效，请换用普通音频录音文件。');
    const mono=new Float32Array(frames);
    for(let channel=0;channel<channels;channel++){
      const values=decoded.getChannelData(channel);
      if(!values || values.length!==frames)throw steelAudioError('format','录音音频数据不完整，请重新录音。');
      for(let frame=0;frame<frames;frame++){
        if(!Number.isFinite(values[frame]))throw steelAudioError('format','录音包含无效音频数据，请重新录音。');
        mono[frame]+=values[frame]/channels;
      }
    }
    const blob=await encodeWav(mono,16000,16000);
    if(!steelAudioCurrent(operation))throw steelAudioError('changed','记录或内容已改变，请重新选择录音。');
    SteelAudio.phase='upload';await render();
    if(!steelAudioCurrent(operation))throw steelAudioError('changed','记录或内容已改变，请重新选择录音。');
    const result=await steelAudioDeadline(uploadWav(blob,'/api/steel/asr/audio',
      {record_id:snapshot.record_id,current_key:snapshot.current_key},snapshot.capture,
      operation.controller?operation.controller.signal:undefined),operation,60000,'录音上传或识别超时，请检查手机与电脑的连接后重试。');
    if(!steelAudioCurrent(operation))throw steelAudioError('changed','记录或内容已改变，本次录音未填入，请重新选择。');
    await steelApplyVoice(Object.assign({},result,{record_id:snapshot.record_id,edit_revision:snapshot.revision}));
    return true;
  }catch(error){
    const failure=error || steelAudioError('unknown','录音导入失败，请重新选择文件后重试。');
    if(SteelAudio.active===operation && steelAudioSameRecord(snapshot) && failure.audioCode!=='canceled'){
      if(failure.audioCode==='changed')toast(failure.message,true);
      else S.micProblem={code:'audio-import',title:'录音未导入',
        message:failure.message || '录音导入失败，请检查网络或更换文件后重试。',
        detail:{secure:window.isSecureContext,permission:'not-requested',audio:SteelAudio.phase,error:failure.audioCode || failure.name || 'audio-import'}};
    }
    return false;
  }finally{
    // A previous record's slow decode/upload must not clear a newer record's
    // waiting state. Route cancellation removes ownership before rendering.
    if(SteelAudio.active===operation){
      SteelAudio.active=null;SteelAudio.phase='idle';Steel.waiting=false;
      if(steelAudioSameRecord(snapshot))await render();
    }
  }
}
document.addEventListener('change',(event)=>{
  const input=event.target;
  if(input.id!=='steel-audio-file')return;
  const snapshot=SteelAudio.selection;SteelAudio.selection=null;
  const file=input.files && input.files[0];
  if(file && snapshot)steelImportAudio(file,snapshot).catch(()=>toast('录音导入未完成，请刷新页面后重试',true));
});
window.addEventListener('hashchange',steelCancelAudio);
