(() => {
  'use strict';
  const panel = document.getElementById('voice-setup');
  if (!panel) return;
  const form = document.getElementById('voice-enrollment-form'), status = document.getElementById('voice-setup-status');
  const save = document.getElementById('voice-save'), consent = document.getElementById('voice-consent'), cancel = document.getElementById('voice-record-cancel');
  const buttons = [...panel.querySelectorAll('[data-record-sample]')], samples = new Map();
  let recording = false, busy = false, version = 0, context, stream, source, worklet, mute, timer, deadline, activeId;
  function show(text, error=false) { status.textContent = text; status.classList.toggle('danger', error); }
  function controls() {
    buttons.forEach(button => { button.disabled = recording || busy; });
    save.disabled = recording || busy || samples.size !== 3 || !consent.checked;
    cancel.hidden = !recording; consent.disabled = recording || busy;
    const remove = document.querySelector('#voice-remove-form button'); if (remove) remove.disabled = recording || busy;
  }
  function cleanup() {
    clearInterval(timer); clearTimeout(deadline); stream?.getTracks().forEach(track => { track.onended = null; track.stop(); }); stream = undefined;
    source?.disconnect(); worklet?.disconnect(); mute?.disconnect(); context?.close().catch(()=>{}); context = undefined;
    recording = false; controls();
  }
  function abort() {
    version++;
    if (recording && activeId) panel.querySelector(`[data-sample="${activeId}"] [data-sample-state]`).textContent = samples.has(activeId) ? 'Oldingi namuna saqlandi' : 'Yozilmagan';
    cleanup(); show('Yozuv bekor qilindi. Tayyor namunalar saqlanib turibdi.');
  }
  buttons.forEach(button => button.addEventListener('click', async () => {
    if (recording || busy) return;
    const id = button.dataset.recordSample, state = button.closest('[data-sample]').querySelector('[data-sample-state]');
    const current = ++version; activeId = id; recording = true; controls();
    show('Mikrofon ruxsatini kutyapman…');
    try {
      const acquired = await navigator.mediaDevices.getUserMedia({audio: {channelCount:1, echoCancellation:true, noiseSuppression:true, autoGainControl:true}});
      if (current !== version) { acquired.getTracks().forEach(track=>track.stop()); return; }
      stream = acquired; stream.getTracks().forEach(track=>{track.onended=abort;});
      context = new AudioContext(); await context.resume(); await context.audioWorklet.addModule(panel.dataset.workletUrl);
      if (current !== version) return;
      source = context.createMediaStreamSource(stream); worklet = new AudioWorkletNode(context, 'voice-pcm');
      mute = context.createGain(); mute.gain.value=0; source.connect(worklet); worklet.connect(mute); mute.connect(context.destination);
      const chunks = []; let count = 0;
      worklet.port.onmessage = event => {
        if (current !== version || !recording) return;
        chunks.push(event.data.pcm); count += event.data.pcm.byteLength;
        if (count >= 8*32000) {
          const bytes = new Uint8Array(count); let offset = 0;
          for (const chunk of chunks) { bytes.set(new Uint8Array(chunk), offset); offset += chunk.byteLength; }
          samples.set(id, VoiceAudio.wav(bytes.buffer));
          state.textContent = 'Yozildi ✓'; state.className = 'success';
          version++; cleanup(); show(samples.size === 3 ? 'Namunalar tayyor. Rozilikni belgilang va ovozingizni saqlang.' : 'Namuna yozildi. Keyingi matnni o‘qing.');
        }
      };
      show('Matnni hozir o‘qing. Mikrofon yoqilgan…');
      deadline = setTimeout(()=>{abort();show('Mikrofondan yetarli ovoz kelmadi. Qurilmani tekshirib qayta yozing.', true);}, 12000);
      timer = setInterval(()=>{ state.textContent = `Yozilmoqda · ${Math.min(8, count/32000).toFixed(1)} / 8 s`; }, 200);
    } catch (error) {
      if (current !== version) return;
      version++; cleanup(); state.textContent = samples.has(id) ? 'Oldingi namuna saqlandi' : 'Yozilmagan';
      show(error.name === 'NotAllowedError' ? 'Mikrofon ruxsati berilmadi. Ruxsat bering yoki matn bilan davom eting.' : 'Mikrofon yozuvi ochilmadi. Qurilma va brauzerni tekshiring.', true);
    }
  }));
  consent.addEventListener('change', controls); cancel.addEventListener('click', abort);
  async function post(url, body) {
    const response = await fetch(url, {method:'POST', credentials:'same-origin', headers:{'X-CSRFToken':form.querySelector('[name=csrfmiddlewaretoken]').value}, body, signal:AbortSignal.timeout(60000)});
    if (!response.headers.get('content-type')?.includes('application/json')) throw new Error('Sessiya tugagan yoki server javob bermadi. Sahifani yangilang.');
    const data = await response.json(); if (!response.ok) throw new Error(data.message || 'Namuna saqlanmadi.'); return data;
  }
  form.addEventListener('submit', async event => {
    event.preventDefault(); if (save.disabled) return;
    busy = true; controls(); show('Ovoz namunalari tekshirilmoqda…');
    try {
      const body = new FormData(); for (const [id, blob] of samples) body.append('sample'+id, blob, 'sample.wav'); body.append('consent','1');
      const data = await post(panel.dataset.enrollUrl, body); samples.clear(); show(data.message);
      const next = new URL(data.next, location.origin); if (next.origin !== location.origin) throw new Error('Qaytish manzili noto‘g‘ri.');
      location.assign(next.href);
    } catch (error) { show(error.message, true); }
    finally { busy = false; controls(); }
  });
  document.getElementById('voice-remove-form')?.addEventListener('submit', async event => {
    event.preventDefault(); if (recording || busy) return; busy=true; controls();
    try { await post(panel.dataset.removeUrl, new FormData()); location.reload(); }
    catch(error) { show(error.message, true); busy=false; controls(); }
  });
  window.addEventListener('pagehide', abort);
  document.addEventListener('visibilitychange', ()=>{if(document.hidden && recording) abort();});
})();
