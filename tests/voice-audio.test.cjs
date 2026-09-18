const test=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');

test('WAV header exactly describes the PCM used by identity verification and STT',async()=>{
  const env={window:{},Blob,Uint8Array,DataView};vm.runInNewContext(fs.readFileSync('static/js/voice-audio.js','utf8'),env);
  const pcm=new ArrayBuffer(8),samples=new DataView(pcm);[-32768,-1,0,32767].forEach((n,i)=>samples.setInt16(i*2,n,true));
  const audio=await env.window.VoiceAudio.wav(pcm).arrayBuffer(),v=new DataView(audio);
  assert.equal(Buffer.from(audio).subarray(0,4).toString(),'RIFF');assert.equal(v.getUint32(4,true),44);
  assert.equal(v.getUint16(22,true),1);assert.equal(v.getUint32(24,true),16000);assert.equal(v.getUint16(34,true),16);
  assert.equal(v.getUint32(40,true),8);assert.deepEqual(Buffer.from(audio).subarray(44),Buffer.from(pcm));
});
