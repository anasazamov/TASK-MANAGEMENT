/* Microphone -> 16 kHz mono PCM16; weighted resampling also supports 44.1 kHz. */
class VoicePCM extends AudioWorkletProcessor {
  constructor() {
    super(); this.ratio = sampleRate / 16000; this.remaining = this.ratio;
    this.sum = 0; this.frame = new Float32Array(320); this.offset = 0;
  }
  process(inputs) {
    const channels = inputs[0];
    if (!channels?.length) return true;
    for (let i = 0; i < channels[0].length; i++) {
      let sample = 0;
      for (const channel of channels) sample += channel[i] || 0;
      sample /= channels.length;
      let weight = 1;
      while (weight > 1e-8) {
        const take = Math.min(weight, this.remaining);
        this.sum += sample * take; this.remaining -= take; weight -= take;
        if (this.remaining < 1e-8) {
          this.frame[this.offset++] = this.sum / this.ratio;
          this.sum = 0; this.remaining = this.ratio;
          if (this.offset === 320) {
            const pcm = new ArrayBuffer(640), view = new DataView(pcm);
            let power = 0;
            for (let n = 0; n < 320; n++) {
              const value = Math.max(-1, Math.min(1, this.frame[n]));
              power += value * value;
              view.setInt16(n*2, Math.round(value * (value < 0 ? 32768 : 32767)), true);
            }
            this.port.postMessage({pcm, rms: Math.sqrt(power/320)}, [pcm]);
            this.offset = 0;
          }
        }
      }
    }
    return true;
  }
}
registerProcessor('voice-pcm', VoicePCM);
