/* Silero v5, 16 kHz / 512 samples + 64-sample context. Runs locally in a worker. */
class SpeechModel {
  static async create(runtime, modelURL) {
    const session = await runtime.InferenceSession.create(modelURL, {executionProviders: ['wasm']});
    return new SpeechModel(runtime, session);
  }
  constructor(runtime, session) {
    this.ort = runtime; this.session = session; this.context = new Float32Array(64);
    this.state = new runtime.Tensor('float32', new Float32Array(256), [2, 1, 128]);
    this.rate = new runtime.Tensor('int64', [16000n]);
  }
  async process(frame) {
    if (frame.length !== 512) throw new Error('Invalid model frame');
    const samples = new Float32Array(576); samples.set(this.context); samples.set(frame, 64);
    const input = new this.ort.Tensor('float32', samples, [1, 576]);
    const previous = this.state;
    try {
      const result = await this.session.run({input, state: previous, sr: this.rate});
      const probability = result.output.data[0];
      this.state = result.stateN; this.context.set(frame.subarray(448));
      previous.dispose(); result.output.dispose();
      if (!Number.isFinite(probability)) throw new Error('Invalid probability');
      return probability;
    } finally { input.dispose(); }
  }
  async release() { this.state.dispose(); this.rate.dispose(); await this.session.release(); }
}
if (typeof module === 'object' && module.exports) module.exports = {SpeechModel};

if (typeof self !== 'undefined') {
  let model, processing = false, failed = false, offset = 0;
  const queue = [], frame = new Float32Array(512);
  const fail = () => { if (!failed) { failed = true; queue.length = 0; self.postMessage({type: 'error'}); } };
  async function drain() {
    if (processing || !model || failed) return;
    processing = true;
    try {
      while (queue.length && !failed) {
        const pcm = new DataView(queue.shift());
        for (let n = 0; n < pcm.byteLength; n += 2) {
          frame[offset++] = pcm.getInt16(n, true) / 32768;
          if (offset !== 512) continue;
          const probability = await model.process(frame);
          const output = new ArrayBuffer(1024), view = new DataView(output);
          let power = 0;
          for (let i = 0; i < 512; i++) { power += frame[i]*frame[i]; view.setInt16(i*2, Math.round(frame[i]*32768), true); }
          offset = 0;
          self.postMessage({type: 'frame', pcm: output, rms: Math.sqrt(power/512), probability}, [output]);
        }
      }
    } catch (_) { fail(); }
    finally { processing = false; }
  }
  self.onmessage = async ({data}) => {
    try {
      if (data.type === 'init') {
        importScripts(data.runtimeURL);
        ort.env.logLevel = 'error'; ort.env.wasm.numThreads = 1; ort.env.wasm.proxy = false;
        ort.env.wasm.wasmPaths = data.wasmBase;
        model = await SpeechModel.create(ort, data.modelURL);
        self.postMessage({type: 'ready'});
      } else if (data.type === 'pcm' && model && !failed) {
        // Bound backlog rather than recognizing stale audio on a slow device.
        if (!(data.pcm instanceof ArrayBuffer) || data.pcm.byteLength !== 640 || queue.length >= 25) { fail(); return; }
        queue.push(data.pcm); drain();
      }
    } catch (_) { fail(); }
  };
}
