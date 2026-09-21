/* Short-lived STT tickets stay in memory. TTS is streamed by our Django server. */
(() => {
  'use strict';
  class LiveSession {
    constructor(options) {
      this.options = options; this.version = 0; this.active = false;
      this.failures = 0;
      this.detector = new VoiceVAD.TurnDetector({
        silenceMs: 1800,
        onStart: () => {
          this.utterance = true;
          this.options.onState('speaking', 'Tinglayapman… Gap tugaganda avtomatik yuboraman.');
          this.socket.send(JSON.stringify({type: 'start', language: 'uz', audio_format: 'pcm_s16le', sample_rate: 16000, channels: 1}));
        },
        onFrame: pcm => {
          if (!this.socket || this.socket.readyState !== WebSocket.OPEN || this.socket.bufferedAmount > 1024*1024) {
            this.fail('Jonli ulanish sekinlashdi yoki uzildi. Suhbatni qayta yoqing.'); return;
          }
          this.socket.send(pcm);
        },
        onEnd: () => {
          if (!this.active || !this.socket) return;
          this.waiting = true; this.socket.send(JSON.stringify({type: 'commit'}));
          this.options.onState('recognizing', 'Avval ovozingiz tekshirilmoqda…');
          clearTimeout(this.deadline);
          this.deadline = setTimeout(() => this.fail('Nutqni tanish javobi kechikdi. Suhbatni qayta yoqing.'), 60000);
        },
      });
    }
    async start() {
      if (this.active) return;
      this.active = true;
      const version = ++this.version;
      this.options.onState('connecting', 'Mikrofon va jonli ulanish tayyorlanmoqda…');
      try {
        this.context = new AudioContext();
        await this.context.resume();
        const stream = await navigator.mediaDevices.getUserMedia({audio: {channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true}});
        if (!this.active || version !== this.version) { stream.getTracks().forEach(track => track.stop()); return; }
        this.stream = stream;
        for (const track of stream.getTracks()) track.onended = () => { if (this.active) this.fail('Mikrofon uzildi. Ulanishni tiklab suhbatni qayta yoqing.'); };
        this.options.onState('connecting', 'Nutq va shovqin filtri tayyorlanmoqda…');
        this.noise = new VoiceNoiseFilter.NeuralFilter({...this.options.filterAssets,
          onFrame: ({pcm, rms, probability}) => {
            if (this.active && version === this.version && this.ready && !this.waiting) this.detector.feed(pcm, rms, this.options.isPlayback(), probability);
          }, onError: message => { if (this.active && version === this.version) this.fail(message); }});
        await this.noise.start();
        if (!this.active || version !== this.version) return;
        await this.context.audioWorklet.addModule(this.options.workletURL);
        if (!this.active || version !== this.version) return;
        this.source = this.context.createMediaStreamSource(stream);
        this.highpass = this.context.createBiquadFilter(); this.highpass.type = 'highpass'; this.highpass.frequency.value = 100; this.highpass.Q.value = 0.707;
        this.lowpass = this.context.createBiquadFilter(); this.lowpass.type = 'lowpass'; this.lowpass.frequency.value = 7000; this.lowpass.Q.value = 0.707;
        this.worklet = new AudioWorkletNode(this.context, 'voice-pcm');
        this.mute = this.context.createGain(); this.mute.gain.value = 0;
        this.source.connect(this.highpass); this.highpass.connect(this.lowpass); this.lowpass.connect(this.worklet);
        this.worklet.connect(this.mute); this.mute.connect(this.context.destination);
        this.worklet.port.onmessage = event => {
          if (!this.active) return;
          const {pcm, rms} = event.data;
          this.options.onLevel(Math.min(1, rms * 7));
          this.noise.feed(pcm);
        };
        await this.connect(version);
      } catch (error) {
        if (this.active && version === this.version) this.fail(error.name === 'NotAllowedError' ? 'Jonli suhbat uchun mikrofon ruxsatini bering.' : (error.message || 'Jonli ulanish ochilmadi.'));
      }
    }
    async connect(version = this.version) {
      this.ready = false; this.waiting = false; this.utterance = false; this.detector.reset();
      const data = await this.options.ticket();
      if (!this.active || version !== this.version) return;
      if (data.websocket_path !== '/voice/live-stream/' || typeof data.ticket !== 'string') throw new Error('Jonli ulanish manzili noto‘g‘ri.');
      const socket = new WebSocket((location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + data.websocket_path);
      socket.onopen = () => { if (this.socket === socket && this.active) socket.send(JSON.stringify({ticket: data.ticket})); };
      this.socket = socket;
      this.deadline = setTimeout(() => this.fail('Jonli ulanish ochilmadi. Server sozlamalarini tekshiring.'), 20000);
      socket.onmessage = event => {
        if (!this.active || this.socket !== socket || version !== this.version) return;
        try {
          const message = JSON.parse(event.data);
          if (message.event === 'ready') {
            if (message.sample_rate !== 16000 || message.channels !== 1 || message.audio_format !== 'pcm_s16le') throw new Error();
            clearTimeout(this.deadline); this.ready = true;
            this.options.onState('listening', this.ignoredMessage || 'Tinglayapman. Ovoz egasini tekshirish faol.');
            this.ignoredMessage = null;
          } else if (message.event === 'recognizing' && this.waiting) {
            // Ambient speech must not interrupt playback or cancel an active command.
            this.options.onSpeechStart();
            this.options.onState('recognizing', 'Ovozingiz tasdiqlandi. Nutq aniqlanmoqda…');
          } else if (message.event === 'ignored' && this.waiting) {
            this.ignoredMessage = 'Ovoz qisqa yoki noaniq. To‘liqroq gapiring.';
            this.finishSocket(socket); this.failures = 0;
            this.options.onState('listening', this.ignoredMessage);
            this.connect(version).catch(error => { if (this.active && version === this.version) this.fail(error.message); });
          } else if (message.event === 'processing' && this.waiting) {
            clearTimeout(this.deadline);
            this.deadline = setTimeout(() => this.fail('Nutqni tanish javobi kechikdi. Suhbatni qayta yoqing.'), 55000);
            this.options.onState('recognizing', 'Nutq qayta tekshirilmoqda. Bir oz kuting…');
          } else if (message.event === 'final') {
            if (!this.waiting || typeof message.text !== 'string' || message.text.length > 6000) throw new Error();
            this.finishSocket(socket);
            this.failures = 0;
            const text = message.text.trim();
            if (text) this.options.onTranscript(text);
            this.connect(version).catch(error => { if (this.active && version === this.version) this.fail(error.message); });
          } else if (message.event === 'error') {
            if (message.code === 'no_speech_detected') {
              this.finishSocket(socket);
              this.connect(version).catch(error => { if (this.active && version === this.version) this.fail(error.message); });
            } else if (['service_unavailable', 'realtime_stt_unavailable', 'overloaded', 'timeout', 'busy', 'connection_failed', 'recognition_failed', 'invalid_audio'].includes(message.code) && ++this.failures < 3) {
              this.finishSocket(socket);
              this.options.onState('reconnecting', 'Bu gap tanilmadi: Muxlisa xizmati vaqtincha javob bermadi. Ulanish tiklangach qayta ayting.');
              this.recoveryTimer = setTimeout(() => {
                if (this.active && version === this.version) this.connect(version).catch(error => { if (this.active && version === this.version) this.fail(error.message); });
              }, this.failures * 1000);
            } else {
              const messages = {insufficient_credits: 'Muxlisa hisobida mablag‘ tugagan. Balansni tekshiring.',
                forbidden: 'Muxlisa kalitida nutqni tanish ruxsati yo‘q.', rate_limit: 'Ovoz so‘rovlari limiti tugadi. Bir daqiqadan keyin qayta yoqing.'};
              this.fail(messages[message.code] || 'Nutqni tanish xizmati vaqtincha ishlamayapti. Birozdan keyin suhbatni qayta yoqing.');
            }
          }
        } catch (_) { this.fail('Jonli javob kutilgan formatda emas.'); }
      };
      socket.onerror = () => { if (this.socket === socket) this.fail('Server bilan jonli ulanish ochilmadi. Sahifani yangilab suhbatni qayta yoqing.'); };
      socket.onclose = () => {
        if (this.socket !== socket || !this.active) return;
        if (this.ready && !this.utterance && !this.waiting) {
          this.finishSocket(socket);
          this.connect(version).catch(error => { if (this.active && version === this.version) this.fail(error.message); });
        } else this.fail('Jonli ulanish yopildi. Suhbatni qayta yoqing.');
      };
    }
    finishSocket(socket) {
      clearTimeout(this.deadline); this.ready = false; this.waiting = false; this.utterance = false;
      this.socket = null; socket.close();
    }
    fail(message) { this.stop(); this.options.onError(message); }
    stop() {
      this.active = false; this.version++; this.ready = false;
      clearTimeout(this.deadline); clearTimeout(this.recoveryTimer);
      this.noise?.stop();
      const socket = this.socket; this.socket = null;
      if (socket) { if (socket.readyState === WebSocket.OPEN && this.utterance) socket.send(JSON.stringify({action: 'interrupt'})); socket.close(); }
      this.stream?.getTracks().forEach(track => { track.onended = null; track.stop(); });
      this.source?.disconnect(); this.worklet?.disconnect(); this.mute?.disconnect();
      this.highpass?.disconnect(); this.lowpass?.disconnect();
      this.context?.close().catch(() => {});
      this.detector.reset(); this.options.onLevel(0); this.options.onState('off', 'Jonli suhbat tugadi.');
    }
  }

  class StreamPlayer {
    constructor(context) { this.context = context; this.sources = new Set(); this.version = 0; this.playing = false; }
    stop() {
      this.version++; this.controller?.abort(); this.controller = null;
      for (const source of this.sources) { source.onended = null; try { source.stop(); } catch (_) {} }
      this.sources.clear(); this.playing = false; this.finish?.(); this.finish = null;
    }
    async play(url, token, csrf, onStart) {
      this.stop(); const version = this.version;
      const controller = new AbortController(); this.controller = controller;
      let next = 0, started = false, done = false, rate = 0;
      const timeout = setTimeout(() => controller.abort(), 65000);
      try {
        await this.context.resume();
        const response = await fetch(url, {method: 'POST', credentials: 'same-origin', signal: controller.signal,
          headers: {'X-CSRFToken': csrf, 'Content-Type': 'application/json'}, body: JSON.stringify({reply_token: token})});
        if (!response.ok) { const data = await response.json(); throw new Error(data.message || 'Jonli ovoz olinmadi.'); }
        if (!response.headers.get('content-type')?.includes('application/x-ndjson')) throw new Error('Jonli ovoz javobi noto‘g‘ri.');
        const reader = response.body.getReader(), decoder = new TextDecoder();
        let pending = '';
        while (true) {
          const result = await reader.read();
          if (version !== this.version) return;
          if (result.done) break;
          pending += decoder.decode(result.value, {stream: true});
          if (pending.length > 16*1024*1024) throw new Error('Ovoz oqimi juda katta.');
          let index;
          while ((index = pending.indexOf('\n')) >= 0) {
            if (version !== this.version) return;
            const line = pending.slice(0, index); pending = pending.slice(index+1);
            if (!line) continue;
            const item = JSON.parse(line);
            if (item.type === 'error') throw new Error(item.message);
            if (item.type === 'ready') {
              if (!Number.isInteger(item.sample_rate) || item.sample_rate < 8000 || item.sample_rate > 48000) throw new Error('Ovoz formati qo‘llanmaydi.');
              rate = item.sample_rate; continue;
            }
            if (item.type === 'done') { done = true; continue; }
            if (item.type !== 'audio') continue;
            const bytes = Uint8Array.from(atob(item.data), char => char.charCodeAt(0));
            if (bytes.length % 2) throw new Error('Ovoz formati buzilgan.');
            if (!rate) throw new Error('Ovoz oqimi formati kelmadi.');
            const view = new DataView(bytes.buffer), buffer = this.context.createBuffer(1, bytes.length/2, rate);
            const channel = buffer.getChannelData(0);
            for (let n = 0; n < channel.length; n++) channel[n] = view.getInt16(n*2, true) / 32768;
            const source = this.context.createBufferSource(); source.buffer = buffer; source.connect(this.context.destination);
            this.sources.add(source); this.playing = true;
            source.onended = () => { this.sources.delete(source); if (!this.sources.size) { this.playing = false; this.finish?.(); } };
            // Anchor the initial buffer to audio arrival, not the HTTP request.
            // Keep 450 ms of initial scheduling headroom so brief network stalls
            // do not split words. Already queued PCM remains sample-contiguous.
            next = Math.max(next, this.context.currentTime + (started ? 0.02 : 0.45)); source.start(next); next += buffer.duration;
            if (!started) { started = true; onStart(); }
          }
        }
        if (!done || !started) throw new Error('Ovoz oqimi yakunlanmadi.');
        if (this.sources.size) await new Promise(resolve => { this.finish = resolve; });
      } finally {
        clearTimeout(timeout);
        if (version === this.version) this.stop();
      }
    }
  }
  window.TaskRealtime = {LiveSession, StreamPlayer};
})();
