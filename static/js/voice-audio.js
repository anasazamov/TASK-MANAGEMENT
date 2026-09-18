/* Normalize microphone/files to the exact PCM that the server verifies and transcribes. */
(() => {
  'use strict';
  function wav(pcm) {
    const bytes = new Uint8Array(44 + pcm.byteLength), view = new DataView(bytes.buffer);
    const label = (offset, text) => [...text].forEach((c, i) => view.setUint8(offset+i, c.charCodeAt(0)));
    label(0, 'RIFF'); view.setUint32(4, 36+pcm.byteLength, true); label(8, 'WAVE'); label(12, 'fmt ');
    view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
    view.setUint32(24, 16000, true); view.setUint32(28, 32000, true); view.setUint16(32, 2, true); view.setUint16(34, 16, true);
    label(36, 'data'); view.setUint32(40, pcm.byteLength, true); bytes.set(new Uint8Array(pcm), 44);
    return new Blob([bytes], {type: 'audio/wav'});
  }
  async function toWav(blob) {
    if (blob.size > 10*1024*1024) throw new Error('Audio hajmi 10 MB dan oshmasligi kerak.');
    const context = new AudioContext();
    try {
      const decoded = await context.decodeAudioData(await blob.arrayBuffer());
      if (!decoded.length || decoded.duration > 30) throw new Error('30 soniyadan qisqa audio yozing.');
      const offline = new OfflineAudioContext(1, Math.ceil(decoded.duration*16000), 16000);
      const source = offline.createBufferSource(); source.buffer = decoded; source.connect(offline.destination); source.start();
      const channel = (await offline.startRendering()).getChannelData(0), pcm = new ArrayBuffer(channel.length*2), view = new DataView(pcm);
      for (let i=0; i<channel.length; i++) { const value = Math.max(-1, Math.min(1, channel[i])); view.setInt16(i*2, Math.round(value*(value<0?32768:32767)), true); }
      return wav(pcm);
    } finally { await context.close(); }
  }
  window.VoiceAudio = {wav, toWav};
})();
