/** Root and private-gateway URL compatibility; no server/browser needed. */
'use strict';
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const assert=require('node:assert/strict');
const ROOT=path.resolve(__dirname,'..');
const failures=[];
function harness(pathname){
  const nodes=new Map(),requests=[];
  const element=()=>({innerHTML:'',value:'',textContent:'',style:{},
    classList:{add(){},remove(){},toggle(){}},addEventListener(){},scrollIntoView(){}});
  const select=(id)=>{if(!nodes.has(id))nodes.set(id,element());return nodes.get(id);};
  class FormDataStub{constructor(){this.fields=[];}append(...args){this.fields.push(args);}}
  const sandbox={console,Promise,JSON,Math,Date,RegExp,Object,Array,String,Number,Boolean,Error,Set,Map,
    setTimeout:()=>0,clearTimeout(){},setInterval:()=>0,clearInterval(){},
    location:{pathname,hash:'#/urltest',search:'',origin:'https://gateway.example'},
    navigator:{},localStorage:{getItem(){},setItem(){}},FormData:FormDataStub,
    document:{readyState:'complete',querySelector:select,querySelectorAll:()=>[],getElementById:select,
      addEventListener(){},body:element()},
    addEventListener(){},
    fetch:async(url,options)=>{requests.push({url,options});return {ok:true,json:async()=>({ok:true})};},
  };
  sandbox.window=sandbox;
  vm.createContext(sandbox);
  const code=fs.readFileSync(path.join(ROOT,'frontend','app.js'),'utf8').replace(/boot\(\);\s*$/,'');
  vm.runInContext(code,sandbox,{filename:'app.js'});
  return {run:(value)=>vm.runInContext(value,sandbox),nodes,requests,sandbox};
}
async function check(name,work){try{await work();console.log('  OK  '+name);}catch(error){failures.push(name);console.error('  BAD '+name+'\n'+error.stack);}}
(async()=>{
  await check('root hosting preserves JSON and multipart URLs and request bodies',async()=>{
    const h=harness('/');
    await h.run("api('/api/me',{method:'POST',body:{word:'测试'}})");
    assert.equal(h.requests[0].url,'/api/me');
    assert.deepEqual(JSON.parse(h.requests[0].options.body),{word:'测试'});
    await h.run("uploadWav({wav:true},'/api/steel/asr/audio',{record_id:7,current_key:'grade'},{mode:'raw',denoise:false,asrMode:'dictation'},'abort-signal')");
    const request=h.requests[1];
    assert.equal(request.url,'/api/steel/asr/audio');assert.equal(request.options.signal,'abort-signal');
    assert.equal(request.options.credentials,'same-origin');
    assert.equal(request.options.body.fields.find((field)=>field[0]==='record_id')[1],7);
    assert.equal(h.run("appHTML('<a href=\"/api/report\">报告</a>')"),'<a href="/api/report">报告</a>');
  });
  await check('gateway API and WAV uploads include exactly one service prefix',async()=>{
    const h=harness('/s/weihua-iqc/');
    assert.equal(h.run('APP_MOUNT'),'/s/weihua-iqc');
    await h.run("api('/api/steel/records?status=draft')");
    await h.run("api('/s/weihua-iqc/api/steel/template')");
    await h.run("uploadWav({wav:true},'/api/steel/asr/audio',{record_id:9},{mode:'system',denoise:true,asrMode:'dictation'},'signal')");
    assert.deepEqual(h.requests.map((item)=>item.url),['/s/weihua-iqc/api/steel/records?status=draft',
      '/s/weihua-iqc/api/steel/template','/s/weihua-iqc/api/steel/asr/audio']);
    assert.equal(h.requests[2].options.signal,'signal');
    assert.equal(h.requests[2].options.body.fields.find((field)=>field[0]==='audio')[2],'say.wav');
  });
  await check('prefix detection requires the exact safe gateway path segment',async()=>{
    for(const [pathname,expected]of [['/s/abc_123-XYZ','/s/abc_123-XYZ'],['/s/abc_123-XYZ/assets','/s/abc_123-XYZ'],
      ['/',''],['/steel/9',''],['/s/name.evil/',''],['/other/s/name/',''],['/s//','']])
      assert.equal(harness(pathname).run('APP_MOUNT'),expected,pathname);
  });
  await check('root-local links transform while hash, external, relative, and already mounted URLs stay intact',async()=>{
    const h=harness('/s/weihua-iqc/');
    for(const [url,expected]of [['/api/a','/s/weihua-iqc/api/a'],['/','/s/weihua-iqc/'],
      ['#/steel/7','#/steel/7'],['https://other.example/a','https://other.example/a'],
      ['//cdn.example/x','//cdn.example/x'],['./help.html','./help.html'],['mailto:test@example.com','mailto:test@example.com'],
      ['/s/weihua-iqc/api/a','/s/weihua-iqc/api/a'],['/s/weihua-iqc?x=1','/s/weihua-iqc?x=1']]){
      h.sandbox.input=url;assert.equal(h.run('appURL(input)'),expected,url);
    }
    const html='<a data-note="href=\'/api/inert\'" href="/api/real?x=&amp;y=2">report</a>'+
      '<iframe src=\'/api/steel/3/preview\'></iframe><img SRC=/api/image.png>'+
      '<form action="/api/save"></form><a href="#/steel">hash</a><a href="https://other.example/api">outside</a>'+
      '<img src="//cdn.example/a.png"><a href="/s/weihua-iqc/api/already">ready</a>';
    h.sandbox.markup=html;
    const result=h.run('appHTML(markup)');
    assert.ok(result.includes('data-note="href=\'/api/inert\'"'));
    assert.ok(result.includes('href="/s/weihua-iqc/api/real?x=&amp;y=2"'));
    assert.ok(result.includes("src='/s/weihua-iqc/api/steel/3/preview'"));
    assert.ok(result.includes('SRC=/s/weihua-iqc/api/image.png'));
    assert.ok(result.includes('action="/s/weihua-iqc/api/save"'));
    assert.ok(result.includes('href="#/steel"'));assert.ok(result.includes('href="https://other.example/api"'));
    assert.ok(result.includes('src="//cdn.example/a.png"'));assert.ok(!result.includes('/s/weihua-iqc/s/weihua-iqc/'));
    assert.equal(h.run('appHTML(appHTML(markup))'),result,'Rewriting must be idempotent');
  });
  await check('rendered content, dock, and modal support downloads, iframes, images, and forms',async()=>{
    for(const prefix of ['', '/s/weihua-iqc']){
      const h=harness(prefix+'/');
      h.run(`VIEWS.urltest=async()=>({html:'<a href="/api/steel/5/xlsx">Excel</a><iframe src="/api/steel/5/preview"></iframe>',dock:'<a href="/mic-help.html">录音帮助</a>'});S.modal='<img src="/api/report/5.png"><form action="/api/save"></form>';`);
      await h.run('render()');
      assert.ok(h.nodes.get('#app').innerHTML.includes('href="'+prefix+'/api/steel/5/xlsx"'));
      assert.ok(h.nodes.get('#app').innerHTML.includes('src="'+prefix+'/api/steel/5/preview"'));
      assert.ok(h.nodes.get('#dock').innerHTML.includes('href="'+prefix+'/mic-help.html"'));
      assert.ok(h.nodes.get('#modal').innerHTML.includes('src="'+prefix+'/api/report/5.png"'));
      assert.ok(h.nodes.get('#modal').innerHTML.includes('action="'+prefix+'/api/save"'));
    }
  });
  await check('index assets are relative and slash canonicalization preserves query and hash',async()=>{
    const html=fs.readFileSync(path.join(ROOT,'frontend','index.html'),'utf8');
    assert.ok(html.includes('id="steel-audio-file"'));assert.ok(html.includes('capture="user"'));
    for(const file of ['style.css','steel.css','app.js','steel_voice.js','steel_audio.js','steel_download.js','steel.js','management.css','management.js'])assert.ok(html.includes('"./'+file+'?v=0.19.1"'),file+' must use a relative, versioned asset URL');
    assert.ok(html.indexOf('./app.js')<html.indexOf('./steel_voice.js'));
    assert.ok(html.indexOf('./steel_voice.js')<html.indexOf('./steel_audio.js'));
    assert.ok(html.indexOf('./steel_audio.js')<html.indexOf('./steel.js'));
    const script=/<script>([\s\S]*?)<\/script>/.exec(html)[1];
    for(const [pathname,expected]of [['/s/weihua-iqc','/s/weihua-iqc/?x=1#/steel/8'],['/s/weihua-iqc/',null],['/',null]]){
      let redirect=null;
      vm.runInNewContext(script,{window:{location:{pathname,search:'?x=1',hash:'#/steel/8',replace:(url)=>{redirect=url;}}}});
      assert.equal(redirect,expected,pathname);
    }
  });
  console.log('FAILS: '+failures.length);if(failures.length)process.exitCode=1;
})().catch((error)=>{console.error(error);process.exitCode=1;});
