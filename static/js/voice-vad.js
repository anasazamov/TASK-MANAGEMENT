(function (root, factory) {
  const exports = factory();
  if (typeof module === 'object' && module.exports) module.exports = exports;
  else root.VoiceVAD = exports;
})(typeof globalThis !== 'undefined' ? globalThis : this, () => {
  'use strict';
  // Neural speech confidence AND adaptive energy gate; accepts 20/32 ms PCM.
  class TurnDetector {
    constructor({onStart, onFrame, onEnd, silenceMs = 1200, maxMs = 28000}) {
      Object.assign(this, {onStart, onFrame, onEnd, silenceMs, maxMs});
      this.noise = 0.0015; this.reset();
    }
    reset() { this.pre = []; this.onset = 0; this.quiet = 0; this.length = 0; this.active = false; this.waiting = false; }
    feed(pcm, rms, playback = false, speechProbability = 1) {
      if (this.waiting) return;
      const ms = pcm.byteLength / 32;
      const threshold = Math.max(playback ? 0.018 : 0.005, this.noise * (playback ? 5 : 2.5));
      const speech = speechProbability >= (this.active ? 0.35 : playback ? 0.90 : 0.60);
      if (!this.active) {
        // Keep initial consonants while the neural model gains confidence.
        this.pre.push(pcm); if (this.pre.length > Math.ceil(640/ms)) this.pre.shift();
        if (speech && rms > threshold) this.onset += ms;
        else { this.onset = 0; if (speechProbability < 0.20) this.noise = Math.max(0.0005, Math.min(0.02, this.noise * 0.97 + rms * 0.03)); }
        if (this.onset < (playback ? 256 : 96)) return;
        this.active = true; this.length = this.pre.length * ms; this.onStart();
        for (const frame of this.pre) this.onFrame(frame);
        this.pre = []; return;
      }
      this.onFrame(pcm); this.length += ms;
      this.quiet = !speech || rms < threshold * 0.45 ? this.quiet + ms : 0;
      if (this.quiet >= this.silenceMs || this.length >= this.maxMs) {
        this.waiting = true; this.active = false;
        this.onEnd(this.length >= this.maxMs ? 'limit' : 'silence');
      }
    }
  }
  return {TurnDetector};
});
