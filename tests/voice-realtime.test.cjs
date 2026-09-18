const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {TurnDetector} = require('../static/js/voice-vad.js');

function detector() {
  const events = [], frames = [];
  const vad = new TurnDetector({onStart:()=>events.push('start'),onFrame:frame=>frames.push(frame),onEnd:reason=>events.push(reason)});
  const feed = (ms, rms, playback=false) => { for(let n=0;n<ms/20;n++) vad.feed(new ArrayBuffer(640), rms, playback); };
  return {vad, feed, events, frames};
}
test('silence and short clicks do not create an utterance',()=>{
  const d=detector(); d.feed(10000,.003); d.feed(60,.15); d.feed(3000,.003);
  assert.deepEqual(d.events,[]); assert.equal(d.frames.length,0);
});
test('keeps pre-roll, survives a thinking pause, commits once after silence',()=>{
  const d=detector(); d.feed(300,.003); d.feed(600,.1); d.feed(400,.002); d.feed(600,.1);
  assert.deepEqual(d.events,['start']); assert.ok(d.frames.length>=85);
  d.feed(1200,.002); d.feed(5000,.002);
  assert.deepEqual(d.events,['start','silence']);
});
test('quiet speech is not learned as noise and delayed confidence keeps the first syllable',()=>{
  const events=[], frames=[];
  const vad=new TurnDetector({onStart:()=>events.push('start'),onFrame:frame=>frames.push(frame),onEnd:()=>events.push('end')});
  const opening=new ArrayBuffer(640);
  vad.feed(opening,.006,false,.4);
  for(let n=0;n<24;n++) vad.feed(new ArrayBuffer(640),.006,false,.4);
  for(let n=0;n<5;n++) vad.feed(new ArrayBuffer(640),.006,false,.85);
  assert.deepEqual(events,['start']);
  assert.equal(frames[0],opening);
  for(let n=0;n<45;n++) vad.feed(new ArrayBuffer(640),.001,false,.05);
  assert.deepEqual(events,['start']); // A 900 ms thinking pause is not a new command.
  for(let n=0;n<20;n++) vad.feed(new ArrayBuffer(640),.006,false,.85);
  for(let n=0;n<60;n++) vad.feed(new ArrayBuffer(640),.001,false,.05);
  assert.deepEqual(events,['start','end']);
});
test('long speech is capped and next turn can start after reset',()=>{
  const d=detector(); d.feed(30000,.1); assert.deepEqual(d.events,['start','limit']);
  assert.equal(d.frames.length,1400); d.vad.reset(); d.feed(500,.1); d.feed(1200,.002);
  assert.deepEqual(d.events,['start','limit','start','silence']);
});
test('interrupting playback needs sustained speech and preserves its beginning',()=>{
  const d=detector(); d.feed(240,.04,true); assert.deepEqual(d.events,[]);
  d.feed(20,.04,true); assert.deepEqual(d.events,['start']); assert.equal(d.frames.length,13);
});
test('loud non-speech and distant speech fail the confidence and energy gates',()=>{
  const d=detector();
  for(let n=0;n<150;n++) d.vad.feed(new ArrayBuffer(1024),.12,false,.05);
  for(let n=0;n<100;n++) d.vad.feed(new ArrayBuffer(1024),.005,false,.99);
  assert.deepEqual(d.events,[]);
  for(let n=0;n<25;n++) d.vad.feed(new ArrayBuffer(1024),.15,false,.98);
  assert.deepEqual(d.events,['start']);
  for(let n=0;n<38;n++) d.vad.feed(new ArrayBuffer(1024),.12,false,.05);
  assert.deepEqual(d.events,['start','silence']);
});
function processor(rate) {
  let Processor; const frames=[];
  const context={sampleRate:rate, AudioWorkletProcessor:class{constructor(){this.port={postMessage:message=>frames.push(message)};}},registerProcessor:(name,type)=>{Processor=type;}};
  vm.runInNewContext(fs.readFileSync('static/js/voice-worklet.js','utf8'),context);
  return {instance:new Processor(),frames};
}
test('worklet emits correctly sized PCM16 at 16kHz from 48k and 44.1k input',()=>{
  for (const rate of [48000,44100,16000]) {
    const {instance,frames}=processor(rate);
    for(let i=0;i<rate;i+=128) instance.process([[Float32Array.from({length:Math.min(128,rate-i)},(_,n)=>.4*Math.sin(2*Math.PI*1000*(i+n)/rate))]]);
    assert.equal(frames.length,50); assert.equal(frames[0].pcm.byteLength,640);
    assert.ok(frames[0].rms>.25 && frames[0].rms<.3);
  }
});
test('worklet clamps and encodes signed little endian samples',()=>{
  const {instance,frames}=processor(16000); instance.process([[new Float32Array(320).fill(-2)]]);
  const view=new DataView(frames[0].pcm); assert.equal(view.getInt16(0,true),-32768);
});

function liveHarness(media) {
  const sockets=[], states=[], transcripts=[], tracks=[];
  class Socket {
    static OPEN=1;
    constructor(url){this.url=url;this.readyState=1;this.bufferedAmount=0;this.sent=[];sockets.push(this);}
    send(value){this.sent.push(value);}
    close(){this.readyState=3;}
    event(value){this.onmessage({data:JSON.stringify(value)});}
  }
  class Context {
    constructor(){this.audioWorklet={addModule:async()=>{}};this.destination={};this.closed=false;}
    async resume(){} async close(){this.closed=true;}
    createMediaStreamSource(){return {connect(){},disconnect(){}};}
    createGain(){return {gain:{value:1},connect(){},disconnect(){}};}
    createBiquadFilter(){return {frequency:{value:0},Q:{value:0},connect(){},disconnect(){}};}
  }
  class Worklet {constructor(){this.port={};} connect(){} disconnect(){} }
  const stream={getTracks:()=>tracks}; tracks.push({stop(){this.stopped=true;}});
  class Filter {constructor(options){this.options=options;} async start(){} feed(){} stop(){this.stopped=true;} }
  const context={window:{},VoiceNoiseFilter:{NeuralFilter:Filter},VoiceVAD:{TurnDetector},AudioContext:Context,AudioWorkletNode:Worklet,WebSocket:Socket,
    navigator:{mediaDevices:{getUserMedia:media || (async()=>stream)}},location:{protocol:'http:',host:'localhost:8000'},
    setTimeout,clearTimeout,console};
  vm.runInNewContext(fs.readFileSync('static/js/voice-realtime.js','utf8'),context);
  const session=new context.window.TaskRealtime.LiveSession({workletURL:'/worklet.js',ticket:async()=>({ticket:'short-lived',websocket_path:'/voice/live-stream/'}),
    onState:(state)=>states.push(state),onTranscript:text=>transcripts.push(text),onSpeechStart:()=>states.push('interrupt'),onLevel:()=>{},isPlayback:()=>false,onError:message=>states.push('error:'+message)});
  return {session,sockets,states,transcripts,tracks,stream};
}
test('live session uses same-origin socket, streams frames, commits automatically, and cleans up',async(t)=>{
  const h=liveHarness(); t.after(()=>h.session.stop()); await h.session.start();
  const socket=h.sockets[0]; socket.onopen();
  assert.equal(socket.url,'ws://localhost:8000/voice/live-stream/');
  assert.equal(JSON.parse(socket.sent[0]).ticket,'short-lived');
  socket.event({event:'ready',sample_rate:16000,channels:1,audio_format:'pcm_s16le'});
  for(let n=0;n<95;n++) h.session.noise.options.onFrame({pcm:new ArrayBuffer(640),rms:n<25?.1:.001,probability:n<25?.99:.01});
  assert.ok(!socket.sent.some(value=>typeof value==='string' && JSON.parse(value).type==='commit'));
  for(let n=0;n<25;n++) h.session.noise.options.onFrame({pcm:new ArrayBuffer(640),rms:.001,probability:.01});
  assert.equal(JSON.parse(socket.sent[1]).type,'start');
  assert.equal(JSON.parse(socket.sent.at(-1)).type,'commit');
  assert.ok(!h.states.includes('interrupt'));
  socket.event({event:'recognizing'});
  assert.ok(h.states.includes('interrupt'));
  socket.event({event:'final',text:'Topshiriqlarni och'});
  await Promise.resolve(); assert.deepEqual(h.transcripts,['Topshiriqlarni och']);
  h.session.stop(); assert.ok(h.tracks.every(track=>track.stopped)); assert.equal(h.session.context.closed,true);
  assert.equal(h.session.noise.stopped,true);
});

test('fallback processing preserves the pending turn and a credit error stops without retry',async(t)=>{
  const h=liveHarness(); t.after(()=>h.session.stop()); await h.session.start();
  const socket=h.sockets[0]; socket.onopen(); socket.event({event:'ready',sample_rate:16000,channels:1,audio_format:'pcm_s16le'});
  h.session.waiting=true; socket.event({event:'processing',stage:'recovering'});
  assert.equal(h.session.active,true); assert.equal(h.transcripts.length,0);
  socket.event({event:'final',text:'Topshiriqlarni och'}); await new Promise(setImmediate);
  assert.deepEqual(h.transcripts,['Topshiriqlarni och']);
  h.sockets[1].event({event:'error',code:'insufficient_credits'});
  assert.equal(h.session.active,false); assert.ok(h.states.some(state=>state.includes('krediti')));
});
test('temporary service errors keep the microphone alive and stopping cancels reconnection',async()=>{
  const h=liveHarness(); await h.session.start();
  h.sockets[0].event({event:'error',code:'service_unavailable'});
  assert.equal(h.session.active,true); assert.ok(h.states.includes('reconnecting'));
  h.session.stop(); await new Promise(resolve=>setTimeout(resolve,1100));
  assert.equal(h.sockets.length,1);
});
test('stopping during microphone permission acquisition releases late tracks',async(t)=>{
  let resolve, requested; const permission=new Promise(done=>requested=done);
  const h=liveHarness(()=>{requested(); return new Promise(done=>resolve=done);});
  t.after(()=>h.session.stop());
  const starting=h.session.start(); await permission; h.session.stop(); resolve(h.stream); await starting;
  assert.ok(h.tracks[0].stopped); assert.equal(h.sockets.length,0);
});

function playerHarness(fetcher) {
  const buffers=[],sources=[];
  const audio={currentTime:2,destination:{},resume:async()=>{},
    createBuffer(channels,length,rate){const data=new Float32Array(length);const buffer={duration:length/rate,getChannelData:()=>data};buffers.push({rate,data});return buffer;},
    createBufferSource(){const source={connect(){},start(time){this.time=time;},stop(){this.stopped=true;}};sources.push(source);return source;}};
  const context={window:{},fetch:fetcher,AbortController,TextDecoder,Uint8Array,DataView,atob,setTimeout,clearTimeout};
  vm.runInNewContext(fs.readFileSync('static/js/voice-realtime.js','utf8'),context);
  return {player:new context.window.TaskRealtime.StreamPlayer(audio),buffers,sources,audio};
}
const pcmLine=()=>JSON.stringify({type:'audio',data:Buffer.from([0,128,255,127]).toString('base64')})+'\n';
function responseFor(read) {return {ok:true,headers:{get:()=> 'application/x-ndjson'},body:{getReader:()=>({read})}};}

test('PCM playback starts before the HTTP stream completes and schedules chunks in order',async(t)=>{
  let release,started; const first=new Promise(done=>started=done); let reads=0;
  const h=playerHarness(async()=>responseFor(async()=>{
    if (++reads===1) return {value:Buffer.from(pcmLine()+pcmLine()),done:false};
    if (reads===2) return new Promise(done=>release=done);
    return {done:true};
  }));
  t.after(()=>h.player.stop());
  const playing=h.player.play('/speak','signed','csrf',started); await first;
  assert.equal(h.sources.length,2); assert.equal(h.player.playing,true);
  assert.equal(h.buffers[0].rate,24000); assert.equal(h.buffers[0].data[0],-1);
  assert.ok(h.sources[1].time>h.sources[0].time);
  assert.equal(h.sources[0].time,h.audio.currentTime+.45);
  release({value:Buffer.from('{"type":"done"}\n'),done:false});
  for (const source of h.sources) source.onended();
  await playing; assert.equal(h.player.playing,false);
});

test('barge-in aborts HTTP, stops scheduled audio, and ignores late audio chunks',async(t)=>{
  let release,started,signal; const first=new Promise(done=>started=done); let reads=0;
  const h=playerHarness(async(url,options)=>{signal=options.signal; return responseFor(async()=>{
    if (++reads===1) return {value:Buffer.from(pcmLine()),done:false};
    return new Promise(done=>release=done);
  });});
  t.after(()=>h.player.stop());
  const playing=h.player.play('/speak','signed','csrf',started); await first;
  h.player.stop(); assert.equal(signal.aborted,true); assert.equal(h.sources[0].stopped,true);
  release({value:Buffer.from(pcmLine()+'{"type":"done"}\n'),done:false}); await playing;
  assert.equal(h.sources.length,1); assert.equal(h.player.playing,false);
});

test('TTS recovery updates progress and starts only the replacement audio',async(t)=>{
  let release,started,reads=0;const first=new Promise(done=>started=done),events=[];
  const h=playerHarness(async()=>responseFor(async()=>{
    if (++reads===1) return {value:Buffer.from('{"type":"ready"}\n{"type":"recovering","message":"Qayta tayyorlanmoqda"}\n'),done:false};
    if (reads===2) return {value:Buffer.from('{"type":"ready"}\n'+pcmLine()),done:false};
    if (reads===3) return new Promise(done=>release=done);
    return {done:true};
  }));
  t.after(()=>h.player.stop());
  const playing=h.player.play('/speak','signed','csrf',()=>{events.push('start');started();},message=>{
    assert.equal(h.sources.length,0);events.push(message);
  });
  await first;
  assert.deepEqual(events,['Qayta tayyorlanmoqda','start']);assert.equal(h.sources.length,1);
  release({value:Buffer.from('{"type":"done"}\n'),done:false});h.sources[0].onended();
  await playing;assert.equal(h.player.playing,false);
});

test('a 350 ms delivery stall does not insert a pause between 100 ms audio chunks',async(t)=>{
  let release,started,reads=0;const first=new Promise(done=>started=done);
  const chunk=JSON.stringify({type:'audio',data:Buffer.alloc(4800).toString('base64')})+'\n';
  const h=playerHarness(async()=>responseFor(async()=>{
    if (++reads===1) return {value:Buffer.from(chunk),done:false};
    if (reads===2) return new Promise(done=>release=done);
    return {done:true};
  }));
  t.after(()=>h.player.stop());
  const playing=h.player.play('/speak','signed','csrf',started);await first;
  h.audio.currentTime+=.35;
  release({value:Buffer.from(chunk+'{"type":"done"}\n'),done:false});
  await new Promise(setImmediate);
  assert.equal(h.sources.length,2);
  assert.equal(h.sources[1].time,h.sources[0].time+h.sources[0].buffer.duration);
  for (const source of h.sources) source.onended();
  await playing;
});
