const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {pathToFileURL} = require('node:url');
const ort = require('../static/vendor/voice-filter-v1/ort.wasm.min.js');
const {SpeechModel} = require('../static/js/voice-neural-worker.js');
const {TurnDetector} = require('../static/js/voice-vad.js');
ort.env.wasm.numThreads = 1;
ort.env.wasm.wasmPaths = pathToFileURL(path.resolve('static/vendor/voice-filter-v1')+path.sep).href;

async function run(samples) {
  const model = await SpeechModel.create(ort, fs.readFileSync('static/vendor/voice-filter-v1/silero_vad_v5.onnx'));
  const events=[], probabilities=[];
  const gate = new TurnDetector({onStart:()=>events.push('start'),onFrame:()=>{},onEnd:()=>events.push('end')});
  try {
    for(let offset=0;offset+512<=samples.length;offset+=512) {
      const frame=samples.slice(offset,offset+512), probability=await model.process(frame);
      let power=0; for(const value of frame) power+=value*value;
      probabilities.push(probability);
      gate.feed(new ArrayBuffer(1024),Math.sqrt(power/512),false,probability);
    }
    return {events,probabilities};
  } finally {await model.release();}
}

test('real Silero model rejects silence, fan hum, loud broadband noise and keyboard clicks',async()=>{
  let seed=17;
  const random=()=>{seed=(1664525*seed+1013904223)>>>0;return seed/4294967296-.5;};
  for(const type of ['silence','hum','noise','clicks']) {
    const samples=Float32Array.from({length:48000},(_,n)=>type==='silence'?0:type==='hum'?.08*Math.sin(n*2*Math.PI*100/16000)+.01*random():type==='noise'?.18*random():n%7000<40?.7*random():0);
    const result=await run(samples);
    assert.deepEqual(result.events,[],type);
  }
});

test('real Silero model retains the synthetic Uzbek command with moderate noise',async()=>{
  const pcm=fs.readFileSync('tests/fixtures/synthetic-uzbek.pcm');
  const samples=new Float32Array(pcm.length/2+24000);
  let seed=21;
  for(let n=0;n<pcm.length/2;n++) {
    seed=(1664525*seed+1013904223)>>>0;
    samples[n]=pcm.readInt16LE(n*2)/32768+.006*(seed/4294967296-.5);
  }
  const result=await run(samples);
  assert.ok(result.events.includes('start'));
  assert.ok(result.events.includes('end'));
  assert.ok(result.probabilities.some(p=>p>.9));
});
