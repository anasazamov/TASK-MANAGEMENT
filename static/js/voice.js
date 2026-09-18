(() => {
  'use strict';
  const panel = document.getElementById('voice-agent');
  if (!panel) return;
  const status = document.getElementById('voice-status');
  const answer = document.getElementById('voice-answer');
  const command = document.getElementById('voice-command');
  const record = panel.querySelector('[data-record]');
  const parse = panel.querySelector('[data-parse-command]');
  const uploadButton = panel.querySelector('[data-audio-upload]');
  const fileInput = document.getElementById('voice-audio-file');
  const cancelButton = panel.querySelector('[data-voice-cancel]');
  const retryButton = panel.querySelector('[data-voice-retry]');
  const checkButton = panel.querySelector('[data-voice-check]');
  const playback = document.getElementById('voice-playback');
  const replyPlayback = document.getElementById('voice-reply-playback');
  const speakButton = panel.querySelector('[data-speak-reply]');
  const stopReply = panel.querySelector('[data-stop-reply]');
  const autoSpeak = document.getElementById('voice-auto-speak');
  const speechStatus = document.getElementById('voice-speech-status');
  const form = document.getElementById('task-form');
  const csrf = form.querySelector('[name=csrfmiddlewaretoken]').value;
  const fields = Object.fromEntries(['title', 'description', 'assignee', 'due_at'].map(key => [key, document.getElementById(`id_${key}`)]));
  let ready = false, llmReady = false, ttsReady = false, state = 'checking', recorder, stream, timer, requestController;
  let processingActive = false, replyToken, speechUrl, speechController, speechGeneration = 0, speechShortened = false;
  let recordingGeneration = 0, cancelled = false, lastAudio, playbackUrl, deadlineKind = 'unspecified';
  let maxSeconds = 29, maxBytes = 10 * 1024 * 1024;
  const microphoneSupported = () => window.isSecureContext && navigator.mediaDevices?.getUserMedia && window.MediaRecorder;
  const showStatus = (message, error = false) => {
    status.textContent = message;
    panel.classList.toggle('voice-error', error);
  };
  const setState = next => {
    state = next;
    const idle = next === 'idle';
    record.disabled = !ready || (!idle && next !== 'recording');
    parse.disabled = !llmReady || !idle;
    command.disabled = !idle && next !== 'checking';
    uploadButton.disabled = !ready || !idle;
    cancelButton.hidden = !['recording', 'permission', 'processing'].includes(next);
    checkButton.hidden = (ready && llmReady) || !idle;
    speakButton.disabled = !ttsReady || !idle || Boolean(speechController);
    retryButton.disabled = !idle;
    record.classList.toggle('recording', next === 'recording');
    record.setAttribute('aria-pressed', String(next === 'recording'));
    record.setAttribute('aria-label', next === 'recording' ? 'Yozishni to‘xtatish va tahlil qilish' : 'Ovoz yozishni boshlash');
    panel.setAttribute('aria-busy', String(['processing', 'checking'].includes(next)));
  };
  const currentDraft = () => ({
    title: fields.title.value, description: fields.description.value,
    assignee_id: fields.assignee.value ? Number(fields.assignee.value) : null,
    due_at: fields.due_at.value,
    deadline_kind: fields.due_at.value ? 'specified' : deadlineKind,
  });
  fields.due_at.addEventListener('input', () => { deadlineKind = fields.due_at.value ? 'specified' : 'unspecified'; });
  document.querySelector('[data-due-clear]')?.addEventListener('click', () => { deadlineKind = 'none'; });

  async function api(url, options = {}) {
    requestController = new AbortController();
    const currentController = requestController;
    const timeout = setTimeout(() => currentController.abort('timeout'), 55000);
    try {
      const response = await fetch(url, {credentials: 'same-origin', ...options,
        headers: {'X-CSRFToken': csrf, ...options.headers}, signal: currentController.signal});
      const contentType = response.headers.get('content-type') || '';
      if (!contentType.includes('application/json')) {
        throw new Error(response.status === 403 ? 'Sessiya yoki xavfsizlik tokeni eskirgan. Sahifani yangilang.' : 'Server javob bermadi. Ulanishni tekshiring va qayta urinib ko‘ring.');
      }
      const data = await response.json();
      if (!response.ok) { const error = new Error(data.message || 'So‘rovni bajarib bo‘lmadi.'); error.code = data.error; throw error; }
      return data;
    } catch (error) {
      if (currentController.signal.aborted) {
        throw new Error(currentController.signal.reason === 'timeout' ? 'So‘rov vaqti tugadi. Qayta urinib ko‘ring.' : 'Amal bekor qilindi.');
      }
      if (error instanceof TypeError) throw new Error('Server bilan aloqa uzildi. Internet va serverni tekshiring.');
      throw error;
    } finally {
      clearTimeout(timeout);
      if (requestController === currentController) requestController = null;
    }
  }

  async function checkConnection() {
    setState('checking');
    try {
      const config = await api(panel.dataset.statusUrl);
      ready = config.stt;
      llmReady = config.llm;
      ttsReady = config.tts;
      maxSeconds = config.max_seconds;
      maxBytes = config.max_audio_bytes;
      const missing = [!config.stt && 'VoiceLab', !llmReady && 'OpenAI'].filter(Boolean);
      showStatus(missing.length ? `${missing.join(' va ')} API kaliti sozlanmagan. Administratorga murojaat qiling.`
        : microphoneSupported() ? 'Mikrofonni bosing. Ijrochi, topshiriq va muddatni ayting.'
        : 'Bu brauzerda mikrofon yozuvi mavjud emas. Audio fayl tanlang yoki buyruqni matn bilan kiriting.', Boolean(missing.length));
    } catch (error) {
      ready = llmReady = ttsReady = false;
      showStatus(error.message, true);
    } finally {
      setState('idle');
    }
  }

  async function fillDraft(text) {
    const snapshot = currentDraft();
    showStatus('Agent ijrochi, topshiriq va muddatni aniqlamoqda…');
    const result = await api(panel.dataset.draftUrl, {method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({command: text, current: snapshot, parent_id: panel.dataset.parent ? Number(panel.dataset.parent) : null})});
    if (cancelled) return;
    if (JSON.stringify(snapshot) !== JSON.stringify(currentDraft())) {
      throw new Error('Tahlil davomida forma o‘zgartirildi. O‘zgarishlaringiz saqlandi; buyruqni qayta yuboring.');
    }
    fields.title.value = result.draft.title;
    fields.description.value = result.draft.description;
    fields.assignee.value = result.draft.assignee_id ?? '';
    fields.due_at.value = result.draft.due_at;
    deadlineKind = result.draft.deadline_kind;
    answer.textContent = result.message;
    answer.hidden = false;
    replyToken = result.reply_token;
    speechShortened = result.speech_shortened;
    speakButton.hidden = !ttsReady;
    if (speechUrl) URL.revokeObjectURL(speechUrl);
    speechUrl = null;
    replyPlayback.hidden = true;
    showStatus('Agent javobi tayyor. Maydonlarni tekshiring yoki aniqlashtirish kiriting.');
    panel.querySelector('.voice-text').open = true;
    if (autoSpeak.checked && ttsReady) speakReply();
  }

  parse.addEventListener('click', async () => {
    const text = command.value.trim();
    if (!text) { showStatus('Avval buyruq yoki aniqlashtirishni kiriting.', true); command.focus(); return; }
    if (state !== 'idle') return;
    cancelled = false;
    stopSpeech();
    processingActive = true;
    answer.hidden = true;
    setState('processing');
    try { await fillDraft(text); }
    catch (error) { showStatus(error.message, !cancelled); }
    finally { processingActive = false; setState('idle'); }
  });

  function releaseMicrophone() {
    clearInterval(timer);
    stream?.getTracks().forEach(track => track.stop());
    stream = null;
  }

  function rememberAudio(blob, filename) {
    lastAudio = {blob, filename, requestId: crypto.randomUUID(), ticket: null};
    if (playbackUrl) URL.revokeObjectURL(playbackUrl);
    playbackUrl = URL.createObjectURL(blob);
    playback.src = playbackUrl;
    playback.hidden = false;
  }

  async function processAudio() {
    if (!lastAudio) return;
    cancelled = false;
    stopSpeech();
    processingActive = true;
    answer.hidden = true;
    retryButton.hidden = true;
    setState('processing');
    let transcribed = false;
    try {
      showStatus('Ovoz matnga aylantirilmoqda…');
      const data = new FormData();
      if (!lastAudio.ticket) data.append('audio', await VoiceAudio.toWav(lastAudio.blob), 'command.wav');
      data.append('request_id', lastAudio.requestId);
      let result = lastAudio.ticket ? {status: 'processing', ticket: lastAudio.ticket}
        : await api(panel.dataset.transcribeUrl, {method: 'POST', body: data});
      if (result.status === 'processing') {
        lastAudio.ticket = result.ticket;
        const until = Date.now() + 180000;
        while (result.status === 'processing' && !cancelled && Date.now() < until) {
          showStatus('VoiceLab yozuvni qayta ishlamoqda… Kutishingiz yoki bekor qilishingiz mumkin.');
          await new Promise(resolve => setTimeout(resolve, 2000));
          if (cancelled) return;
          result = await api(panel.dataset.pollUrl, {method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ticket: lastAudio.ticket})});
        }
        if (!cancelled && result.status === 'processing') throw new Error('Yozuv hali tayyor emas. Birozdan so‘ng qayta urinish tugmasini bosing.');
      }
      if (cancelled) return;
      command.value = result.transcript;
      transcribed = true;
      panel.querySelector('.voice-text').open = true;
      if (llmReady) await fillDraft(result.transcript);
      else showStatus('Ovoz matnga aylantirildi. Maydonlarni agent bilan to‘ldirish uchun OpenAI kaliti kerak.');
    } catch (error) {
      showStatus(error.message + (transcribed && !cancelled ? ' Eshitilgan matn saqlandi; tuzatib «Agent bilan to‘ldirish»ni bosing.' : ''), !cancelled);
      if (['voicelab_overloaded', 'voicelab_unavailable'].includes(error.code)) {
        lastAudio.ticket = null; lastAudio.requestId = crypto.randomUUID();
      }
      retryButton.hidden = transcribed;
    } finally { processingActive = false; setState('idle'); }
  }

  record.addEventListener('click', async () => {
    if (state === 'recording') {
      showStatus('Yozuv tayyorlanmoqda…');
      setState('processing');
      recorder.stop();
      releaseMicrophone();
      return;
    }
    if (state !== 'idle' || !ready) return;
    if (!microphoneSupported()) {
      showStatus('Mikrofon uchun HTTPS yoki localhost va ovoz yozishni qo‘llaydigan brauzer kerak. Audio fayl tanlashingiz ham mumkin.', true);
      return;
    }
    cancelled = false;
    stopSpeech();
    const generation = ++recordingGeneration;
    setState('permission');
    showStatus('Brauzerda mikrofon ruxsatini bering. Bekor qilish tugmasi bilan qaytishingiz mumkin.');
    playback.pause();
    try {
      const acquired = await navigator.mediaDevices.getUserMedia({audio: {channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true}});
      if (generation !== recordingGeneration || cancelled) { acquired.getTracks().forEach(track => track.stop()); return; }
      stream = acquired;
      const mimeType = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4', 'audio/ogg;codecs=opus'].find(type => MediaRecorder.isTypeSupported(type));
      if (!mimeType) throw new Error('Bu brauzer mos audio formatda yoza olmaydi. Audio fayl tanlang.');
      const chunks = [];
      let bytes = 0, recordingFailed = false;
      const activeRecorder = new MediaRecorder(stream, {mimeType, audioBitsPerSecond: 64000});
      recorder = activeRecorder;
      activeRecorder.addEventListener('dataavailable', event => {
        if (generation !== recordingGeneration) return;
        if (event.data.size) { chunks.push(event.data); bytes += event.data.size; }
        if (bytes > maxBytes && activeRecorder.state === 'recording') activeRecorder.stop();
      });
      activeRecorder.addEventListener('error', () => {
        if (generation !== recordingGeneration) return;
        recordingFailed = true;
        releaseMicrophone();
        if (activeRecorder.state !== 'inactive') activeRecorder.stop();
        setState('idle');
        showStatus('Ovoz yozishda xato yuz berdi. Qayta urinib ko‘ring yoki audio fayl tanlang.', true);
      });
      activeRecorder.addEventListener('stop', () => {
        if (cancelled || generation !== recordingGeneration || recordingFailed) return;
        releaseMicrophone();
        const blob = new Blob(chunks, {type: mimeType});
        if (!blob.size || blob.size > maxBytes) {
          setState('idle');
          showStatus(blob.size ? 'Audio 10 MB dan katta. Qisqaroq yozing.' : 'Audio yozilmadi. Mikrofonni tekshiring.', true);
          return;
        }
        rememberAudio(blob, mimeType.includes('mp4') ? 'command.m4a' : mimeType.includes('ogg') ? 'command.ogg' : 'command.webm');
        processAudio();
      });
      recorder.start(1000);
      setState('recording');
      let seconds = 0;
      showStatus('Yozilmoqda · 0:00. Tugatish uchun mikrofonni yana bosing.');
      timer = setInterval(() => {
        seconds += 1;
        showStatus(`Yozilmoqda · ${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}. Tugatish uchun mikrofonni yana bosing.`);
        if (seconds >= maxSeconds && recorder.state === 'recording') {
          setState('processing'); recorder.stop(); releaseMicrophone();
        }
      }, 1000);
    } catch (error) {
      if (generation !== recordingGeneration) return;
      releaseMicrophone();
      setState('idle');
      const messages = {
        NotAllowedError: 'Mikrofon ruxsati berilmadi. Brauzerning sayt ruxsatlaridan mikrofonni yoqing yoki audio fayl tanlang.',
        NotFoundError: 'Mikrofon topilmadi. Qurilmani ulang yoki audio fayl tanlang.',
        NotReadableError: 'Mikrofonni ochib bo‘lmadi. Boshqa ilova undan foydalanayotgan bo‘lishi mumkin.',
        SecurityError: 'Brauzer mikrofonni blokladi. Sayt ruxsatlari va HTTPS ulanishini tekshiring.',
      };
      showStatus(messages[error.name] || error.message || 'Ovoz yozishni boshlab bo‘lmadi.', true);
    }
  });

  cancelButton.addEventListener('click', () => {
    cancelled = true;
    recordingGeneration += 1;
    requestController?.abort('cancelled');
    if (recorder?.state === 'recording') recorder.stop();
    releaseMicrophone();
    showStatus('Amal bekor qilindi.');
    // Processing returns to idle in its finally block, preventing request races.
    if (!processingActive) setState('idle');
  });
  uploadButton.addEventListener('click', () => fileInput.click());
  fileInput.addEventListener('change', () => {
    const file = fileInput.files[0];
    fileInput.value = '';
    if (!file || state !== 'idle' || !ready) return;
    if (!/\.(webm|wav|mp3|m4a|aac|ogg|opus|flac)$/i.test(file.name) || !file.size || file.size > maxBytes) {
      showStatus('10 MB gacha bo‘lgan WebM, WAV, MP3, M4A, AAC, OGG yoki FLAC audio faylini tanlang.', true);
      return;
    }
    rememberAudio(file, file.name);
    processAudio();
  });
  retryButton.addEventListener('click', () => { if (state === 'idle') processAudio(); });
  checkButton.addEventListener('click', checkConnection);

  function stopSpeech() {
    speechGeneration += 1;
    speechController?.abort();
    speechController = null;
    replyPlayback.pause();
    stopReply.hidden = true;
    speechStatus.textContent = '';
    speakButton.disabled = !ttsReady || state !== 'idle';
  }

  async function speakReply() {
    if (!replyToken || !ttsReady) return;
    stopSpeech();
    const generation = speechGeneration;
    speakButton.disabled = true;
    stopReply.hidden = false;
    speechStatus.textContent = 'Ovozli javob tayyorlanmoqda…';
    const controller = new AbortController();
    speechController = controller;
    const timeout = setTimeout(() => controller.abort('timeout'), 60000);
    try {
      if (!speechUrl) {
        const response = await fetch(panel.dataset.speakUrl, {method: 'POST', credentials: 'same-origin',
          headers: {'Content-Type': 'application/json', 'X-CSRFToken': csrf},
          body: JSON.stringify({reply_token: replyToken}), signal: controller.signal});
        if (!response.ok) {
          const data = await response.json().catch(() => ({}));
          throw new Error(data.message || 'Ovozli javobni olib bo‘lmadi.');
        }
        if (!(response.headers.get('content-type') || '').startsWith('audio/wav')) throw new Error('Server audio javob qaytarmadi.');
        const blob = await response.blob();
        if (generation !== speechGeneration) return;
        speechUrl = URL.createObjectURL(blob);
      }
      if (generation !== speechGeneration) return;
      replyPlayback.src = speechUrl;
      replyPlayback.hidden = false;
      try {
        await replyPlayback.play();
        speechStatus.textContent = speechShortened ? 'Javobning qisqa varianti o‘qilmoqda; to‘liq javob yuqorida.' : 'Agent javobi o‘qilmoqda.';
      } catch (_) {
        speechStatus.textContent = 'Ovoz tayyor. Tinglash uchun audio pleyerdagi tugmani bosing.';
        stopReply.hidden = true;
      }
    } catch (error) {
      if (generation === speechGeneration) {
        speechStatus.textContent = controller.signal.aborted ? 'Ovozli javob vaqti tugadi. «Javobni tinglash» orqali qayta urinishingiz mumkin.'
          : `${error.message} Matnli javob yuqorida saqlandi.`;
        stopReply.hidden = true;
      }
    } finally {
      clearTimeout(timeout);
      if (generation === speechGeneration) {
        speechController = null;
        speakButton.disabled = state !== 'idle';
      }
    }
  }
  speakButton.addEventListener('click', speakReply);
  stopReply.addEventListener('click', stopSpeech);
  autoSpeak.addEventListener('change', () => { if (!autoSpeak.checked) stopSpeech(); });
  replyPlayback.addEventListener('ended', () => { stopReply.hidden = true; speechStatus.textContent = 'Ovozli javob tugadi.'; });
  form.addEventListener('submit', event => {
    if (['recording', 'permission', 'processing'].includes(state)) {
      event.preventDefault(); showStatus('Avval ovozli amalni tugating yoki bekor qiling.', true);
    }
  });
  window.addEventListener('pagehide', () => {
    cancelled = true;
    stopSpeech();
    recordingGeneration += 1;
    requestController?.abort('cancelled');
    if (recorder?.state === 'recording') recorder.stop();
    releaseMicrophone();
    if (playbackUrl) URL.revokeObjectURL(playbackUrl);
    if (speechUrl) URL.revokeObjectURL(speechUrl);
  });
  checkConnection().then(() => {
    if (new URLSearchParams(location.search).has('voice')) {
      panel.scrollIntoView({block: 'center'});
      if (ready) record.focus();
    }
  });
})();
