(() => {
  'use strict';
  class NeuralFilter {
    constructor(options) { this.options = options; this.active = true; }
    async start() {
      return new Promise((resolve, reject) => {
        this.reject = reject;
        this.worker = new Worker(this.options.workerURL);
        this.timeout = setTimeout(() => this.fail('Nutq filtri yuklanmadi. Sahifani yangilab qayta urinib ko‘ring.'), 25000);
        this.worker.onerror = () => this.fail('Nutq filtri ishlamadi. Sahifani yangilab qayta urinib ko‘ring.');
        this.worker.onmessage = ({data}) => {
          if (!this.active) return;
          if (data.type === 'ready') { clearTimeout(this.timeout); this.reject = null; this.ready = true; resolve(); }
          else if (data.type === 'frame') this.options.onFrame(data);
          else if (data.type === 'error') this.fail('Nutq filtri ovozni qayta ishlay olmadi. Suhbatni qayta yoqing.');
        };
        this.worker.postMessage({type: 'init', runtimeURL: new URL(this.options.runtimeURL, location.href).href,
          wasmBase: new URL('.', new URL(this.options.runtimeURL, location.href)).href, modelURL: new URL(this.options.modelURL, location.href).href});
      });
    }
    feed(pcm) { if (this.active && this.ready) this.worker.postMessage({type: 'pcm', pcm}, [pcm]); }
    fail(message) { const reject = this.reject; this.reject = null; this.stop(); if (reject) reject(new Error(message)); else this.options.onError(message); }
    stop() {
      this.active = false; clearTimeout(this.timeout); this.worker?.terminate();
      this.reject?.(new Error('Nutq filtri to‘xtatildi.')); this.reject = null;
    }
  }
  window.VoiceNoiseFilter = {NeuralFilter};
})();
