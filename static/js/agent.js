(() => {
  'use strict';
  const panel = document.getElementById('system-agent');
  if (!panel) return;
  const $ = selector => panel.querySelector(selector);
  const input = $('#agent-command'), form = $('#agent-command-form');
  const status = $('[data-agent-status]'), log = $('.agent-messages'), scroll = $('.agent-scroll');
  const record = $('[data-agent-record]'), audio = $('[data-agent-audio]');
  const auto = $('[data-agent-auto]'), review = $('[data-agent-review]');
  const launcher = document.querySelector('.agent-launch');
  const csrf = form.querySelector('[name=csrfmiddlewaretoken]').value;
  const key = `sic-agent:${panel.dataset.user}:`;
  const storage = {
    get(name) { try { return sessionStorage.getItem(key + name); } catch (_) { return null; } },
    set(name, value) { try { sessionStorage.setItem(key + name, value); } catch (_) {} },
    remove(name) { try { sessionStorage.removeItem(key + name); } catch (_) {} },
  };
  let conversation = storage.get('conversation') || crypto.randomUUID();
  storage.set('conversation', conversation);
  auto.checked = storage.get('auto') !== 'false';
  review.checked = storage.get('review') === 'true';
  let config = {}, busy = false, loading = false, permission = false, recording = false;
  let recorder, stream, timer, generation = 0, replyToken, speechUrl, speechController;
  let proposal, retryAction, lastTrigger, initialized = false;
  let recordingSeconds = 0;
  let live, liveSpeech, liveState = 'off', turnEpoch = 0, queuedLive, usedSoftNavigation = false;
  const initialForms = new Map();
  const formValue = item => JSON.stringify([...new FormData(item).entries()].filter(([name]) => name !== 'csrfmiddlewaretoken'));
  function snapshotForms() {
    initialForms.clear();
    document.querySelectorAll('form[method=post]').forEach(item => { if (item !== form) initialForms.set(item, formValue(item)); });
  }
  snapshotForms();
  const dirty = () => [...initialForms].some(([item, value]) => item.isConnected && formValue(item) !== value);
  function compact(value) {
    panel.classList.toggle('agent-compact', value);
    $('[data-agent-expand]').hidden = !value;
    storage.set('compact', String(value));
  }
  compact(storage.get('compact') === 'true');
  function show(message, error = false) { status.textContent = message; status.classList.toggle('error', error); }
  function controls() {
    const liveOn = Boolean(live?.active);
    const occupied = busy || permission || recording;
    $('[data-agent-send]').disabled = occupied || loading;
    input.disabled = occupied;
    record.disabled = liveOn || loading || busy || permission || !config.stt || !navigator.mediaDevices?.getUserMedia || !window.MediaRecorder;
    record.setAttribute('aria-pressed', String(recording));
    record.querySelector('span').textContent = recording ? `To‘xtatish · ${recordingSeconds}s` : 'Gapirish';
    $('[data-agent-file]').disabled = liveOn || occupied || !config.stt || loading;
    $('[data-agent-reset]').disabled = liveOn || occupied || loading;
    $('[data-agent-confirm]').disabled = occupied || loading;
    $('[data-agent-cancel]').disabled = occupied || loading;
    $('[data-agent-listen]').disabled = occupied;
    $('[data-agent-retry]').disabled = occupied || loading;
    panel.querySelectorAll('[data-agent-example]').forEach(button => { button.disabled = occupied || loading; });
    panel.setAttribute('aria-busy', String(busy || loading));
    panel.dataset.live = String(liveOn);
    const liveButton = $('[data-agent-live]');
    liveButton.disabled = !liveOn && (occupied || loading || !config.stt);
    liveButton.setAttribute('aria-pressed', String(liveOn));
    liveButton.querySelector('span').textContent = liveOn ? 'Suhbatni tugatish' : 'Jonli suhbat';
    review.disabled = liveOn;
  }
  async function api(url, body, timeout = 95000) {
    const controller = new AbortController();
    const deadline = setTimeout(() => controller.abort(), timeout);
    try {
      const response = await fetch(url, {method: body === undefined ? 'GET' : 'POST', credentials: 'same-origin',
        headers: {'X-CSRFToken': csrf, ...(body instanceof FormData || body === undefined ? {} : {'Content-Type': 'application/json'})},
        body: body === undefined ? undefined : body instanceof FormData ? body : JSON.stringify(body), signal: controller.signal});
      if (!(response.headers.get('content-type') || '').includes('application/json')) throw new Error('Sessiya yoki server javobi noto‘g‘ri. Sahifani yangilang.');
      const data = await response.json();
      if (!response.ok) { const error = new Error(data.message || 'So‘rov bajarilmadi.'); error.data = data; throw error; }
      return data;
    } catch (error) {
      if (error.name === 'AbortError') throw new Error('Javob kechikdi. Qayta urinish oldingi so‘rov natijasini tekshiradi.');
      if (error instanceof TypeError) throw new Error('Server bilan aloqa uzildi. Qayta urinib ko‘ring.');
      throw error;
    } finally { clearTimeout(deadline); }
  }
  function renderMessages(messages) {
    log.replaceChildren();
    for (const item of messages || []) {
      const node = document.createElement('div'); node.className = `agent-message ${item.role === 'user' ? 'user' : 'assistant'}`;
      const label = document.createElement('small'); label.textContent = item.role === 'user' ? 'Siz' : item.verified ? 'Agent' : 'Agent · tekshirilmagan eski javob';
      const content = document.createElement('span'); content.textContent = item.content;
      node.append(label, content); log.append(node);
    }
    $('.agent-intro').hidden = Boolean(messages?.length);
    scroll.scrollTop = scroll.scrollHeight;
  }
  function renderProposal(value) {
    proposal = value;
    const card = $('.agent-proposal'), details = card.querySelector('dl');
    card.hidden = !value; details.replaceChildren();
    if (value) for (const [name, content] of Object.entries(value.preview)) {
      if (!content) continue;
      const term = document.createElement('dt'), definition = document.createElement('dd');
      term.textContent = name; definition.textContent = content; details.append(term, definition);
    }
    scroll.scrollTop = scroll.scrollHeight;
  }
  async function load() {
    loading = true; controls();
    try {
      const results = await Promise.allSettled([api(panel.dataset.statusUrl), api(panel.dataset.stateUrl, {conversation_id: conversation})]);
      if (results[0].status === 'fulfilled') {
        config = results[0].value;
        $('[data-agent-connection]').textContent = config.llm && config.stt ? 'Tayyor · Ovozli suhbatga tayyor.' :
          !config.llm ? 'OpenAI kaliti hali sozlanmagan. Hozir aniq sahifa ochish buyruqlari ishlaydi; murakkab suhbat uchun OpenAI kerak.' :
            'Matnli agent tayyor. Ovoz uchun Muxlisa AI sozlanishi kerak.';
      } else { $('[data-agent-connection]').textContent = results[0].reason.message; }
      if (results[1].status !== 'fulfilled') throw results[1].reason;
      const data = results[1].value;
      renderMessages(data.messages); renderProposal(data.proposal);
      if (data.reply_token) { replyToken = data.reply_token; $('[data-agent-listen]').hidden = !config.tts; }
      if (data.busy) show('Oldingi buyruq bajarilmoqda. Javobni olish uchun panelni qayta oching.');
      initialized = true;
    } catch (error) { show(error.message, true); }
    finally { loading = false; controls(); }
  }
  async function open(trigger) {
    lastTrigger = trigger || lastTrigger;
    if (trigger) compact(false);
    panel.hidden = false; launcher.hidden = true; storage.set('open', 'true');
    document.querySelectorAll('[data-agent-open]').forEach(button => button.setAttribute('aria-expanded', 'true'));
    if (!busy && !recording && !permission) await load();
    if (!busy) input.focus({preventScroll: true});
  }
  function stopSpeech() {
    liveSpeech?.stop();
    speechController?.abort(); speechController = undefined;
    audio.pause(); audio.removeAttribute('src'); audio.load(); audio.hidden = true;
    if (speechUrl) URL.revokeObjectURL(speechUrl);
    speechUrl = undefined; $('[data-agent-stop]').hidden = true;
  }
  function abandonRecording() {
    generation++; clearInterval(timer);
    if (recorder?.state === 'recording') recorder.stop();
    stream?.getTracks().forEach(track => track.stop());
    recording = false; permission = false; controls();
  }
  function close() {
    panel.hidden = true; launcher.hidden = false; storage.set('open', 'false');
    document.querySelectorAll('[data-agent-open]').forEach(button => button.setAttribute('aria-expanded', 'false'));
    stopLive(); abandonRecording(); stopSpeech(); lastTrigger?.focus();
  }
  async function speak(token) {
    if (!config.tts || !token || panel.hidden) return;
    stopSpeech();
    if (live?.active && live.context) {
      const epoch = turnEpoch;
      liveSpeech = new TaskRealtime.StreamPlayer(live.context);
      $('[data-agent-stop]').hidden = false;
      $('[data-live-label]').textContent = 'Javob tayyorlanmoqda…';
      try {
        await liveSpeech.play(panel.dataset.streamUrl, token, csrf, () => {
          $('[data-live-label]').textContent = 'Agent gapiryapti · gapirsangiz to‘xtaydi';
          show('Agentni tinglang yoki gapirib javobni to‘xtating.');
        });
        if (epoch === turnEpoch && live?.active) { show('Tinglayapman. Keyingi gapingizni ayting.'); $('[data-live-label]').textContent = 'Tinglayapman'; }
      } catch (error) { if (epoch === turnEpoch && live?.active) show(error.name === 'AbortError' ? 'Ovozli javob to‘xtadi. Gapirishingiz mumkin.' : error.message, true); }
      finally { if (epoch === turnEpoch) $('[data-agent-stop]').hidden = true; }
      return;
    }
    const controller = new AbortController(); speechController = controller;
    $('[data-agent-stop]').hidden = false;
    let timedOut = false;
    const timeout = setTimeout(() => { timedOut = true; controller.abort(); }, 55000);
    show('Ovozli javob tayyorlanmoqda…');
    try {
      const response = await fetch(panel.dataset.speakUrl, {method: 'POST', credentials: 'same-origin',
        headers: {'X-CSRFToken': csrf, 'Content-Type': 'application/json'}, body: JSON.stringify({reply_token: token}), signal: controller.signal});
      if (!response.ok) { const data = await response.json(); throw new Error(data.message || 'Ovozli javob olinmadi.'); }
      const blob = await response.blob();
      if (controller.signal.aborted || speechController !== controller || panel.hidden) return;
      speechUrl = URL.createObjectURL(blob); audio.src = speechUrl; audio.hidden = false;
      try { await audio.play(); show('Agent javobini tinglang yoki yangi buyruq bering.'); }
      catch (_) { show('Javobni eshitish uchun audio pleyeridagi ▶ tugmasini bosing.'); }
    } catch (error) {
      if (timedOut) { show('Ovozli javob kechikdi. Matn tayyor; «Tinglash» orqali qayta urinishingiz mumkin.', true); $('[data-agent-stop]').hidden = true; }
      else if (!controller.signal.aborted) { show(error.message, true); $('[data-agent-stop]').hidden = true; }
    }
    finally { clearTimeout(timeout); if (speechController === controller) speechController = undefined; }
  }
  function safeNavigation(value) {
    if (!value || typeof value.url !== 'string' || !/^\/(?:tasks\/(?:new\/|\d+\/)?|employees\/(?:new\/|\d+\/(?:edit|password)\/)?|pages\/(?:\d+\/)?|account\/password\/|structure\/|chains\/|timeline\/|notifications\/)?(?:\?[^#]*)?$/.test(value.url)) return null;
    const url = new URL(value.url, location.origin);
    return url.origin === location.origin ? url : null;
  }
  function applyForm(draft) {
    // The server wrote these fields; the user still reviews them and presses save.
    const fields = {title: document.getElementById('id_title'), description: document.getElementById('id_description'),
      assignee: document.getElementById('id_assignee'), due_at: document.getElementById('id_due_at')};
    if (!fields.title || !fields.assignee) return false;
    fields.title.value = draft.title || '';
    fields.description.value = draft.description || '';
    if (draft.assignee_id && fields.assignee.querySelector(`option[value="${draft.assignee_id}"]`)) {
      fields.assignee.value = String(draft.assignee_id);
    }
    if (fields.due_at) fields.due_at.value = draft.due_at || '';
    fields.title.focus({preventScroll: true});
    // The agent wrote these itself, so they are not the user's unsaved work and
    // must not block the next page it opens. Later edits count as unsaved again.
    snapshotForms();
    return true;
  }
  async function navigate(result, epoch = turnEpoch) {
    const url = safeNavigation(result.navigation);
    if (!url) return false;
    const box = $('.agent-navigation'), link = $('[data-agent-link]');
    box.hidden = false; link.href = url.pathname + url.search; link.textContent = result.navigation.label + ' →';
    const hasChanges = dirty();
    box.querySelector('p').textContent = hasChanges ? 'Formada saqlanmagan ma’lumot bor. Sahifani ochishdan oldin uni saqlang yoki quyidagi havola orqali o‘ting.' : 'Kerakli sahifa:';
    if (hasChanges || panel.hidden) {
      if (hasChanges) show('Saqlanmagan forma bor. Sahifaga o‘tish havolasi suhbatda ko‘rsatilgan.');
      scroll.scrollTop = scroll.scrollHeight; return false;
    }
    if (url.href === location.href && !result.task_id) { compact(true); return false; }
    if (live?.active) {
      // Replace Django's page content, retaining the live microphone, audio
      // context, panel and WebSocket in this document.
      try {
        const response = await fetch(url.href, {credentials: 'same-origin', signal: AbortSignal.timeout(15000)});
        if (!response.ok || response.redirected) throw new Error('Sahifani ochib bo‘lmadi. Suhbatdagi havoladan foydalaning.');
        const page = new DOMParser().parseFromString(await response.text(), 'text/html');
        if (page.querySelector('#system-agent')?.dataset.user !== panel.dataset.user) throw new Error('Sessiya o‘zgargan. Qayta tizimga kiring.');
        const selectors = ['.sidebar-scrim', '.sidebar', '.workspace', '#task-drawer'];
        if (selectors.some(selector => !page.querySelector(selector))) throw new Error('Sahifa formati noto‘g‘ri.');
        if (epoch !== turnEpoch || !live?.active || dirty()) return false;
        document.querySelector('#task-drawer[open]')?.close();
        for (const selector of selectors) document.querySelector(selector).replaceWith(page.querySelector(selector));
        document.title = page.title; document.body.classList.remove('menu-open');
        history.replaceState({agentPage: true}, '', location.href);
        history.pushState({agentPage: true}, '', url.href); usedSoftNavigation = true;
        window.TaskPage.mount(); snapshotForms(); compact(true); window.scrollTo(0, 0);
        return false; // No document unload: play the reply in the same session.
      } catch (error) { show(error.message || 'Sahifani ochish kechikdi.', true); return false; }
    }
    if (auto.checked && result.reply_token) storage.set('pending-speech', JSON.stringify({token: result.reply_token, at: Date.now()}));
    storage.set('open', 'true'); storage.set('compact', 'true'); location.assign(url.href); return true;
  }
  async function send(command, proposalId, retryPayload) {
    if (busy || recording || permission || !command.trim()) return;
    compact(false);
    const epoch = ++turnEpoch;
    stopSpeech(); busy = true; controls(); show('Agent buyruqni bajarmoqda…');
    $('[data-agent-retry]').hidden = true; $('.agent-navigation').hidden = true;
    const drawer = document.querySelector('#task-drawer[open]');
    const payload = retryPayload || {conversation_id: conversation, request_id: crypto.randomUUID(), command,
      path: drawer?.dataset.agentPath || location.pathname + location.search,
      ...(proposalId ? {proposal_id: proposalId} : {})};
    let result, navigating = false;
    try {
      result = await api(panel.dataset.messageUrl, payload);
      renderMessages(result.messages); renderProposal(result.proposal);
      input.value = ''; replyToken = result.reply_token;
      $('[data-agent-listen]').hidden = !config.tts || !replyToken;
      const filled = result.form ? applyForm(result.form) : false;
      show(result.mode === 'shortcut' && !config.llm ? 'Sahifa ochish buyrug‘i bajarildi. Murakkab suhbat uchun OpenAI sozlanishi kerak.'
        : filled ? 'Forma to‘ldirildi. Tekshirib, «Topshiriqni yuborish»ni bosing.'
        : result.form ? 'Topshiriq formasi bu sahifada topilmadi. «Yangi topshiriq» sahifasini oching.'
        : 'Tayyor. Yana nima qilish kerak?');
      if (filled) compact(true);
      retryAction = undefined;
      if (epoch === turnEpoch) navigating = await navigate(result, epoch);
    } catch (error) {
      if (error.data?.messages) { renderMessages(error.data.messages); renderProposal(error.data.proposal); }
      if (error.data?.reply_token) { replyToken = error.data.reply_token; $('[data-agent-listen]').hidden = !config.tts; }
      show(error.message, true);
      retryAction = () => send(command, proposalId, !error.data || error.data.error === 'busy' ? payload : undefined);
      $('[data-agent-retry]').hidden = false;
    } finally { busy = false; controls(); }
    if (result && !navigating && auto.checked && epoch === turnEpoch) speak(replyToken);
    flushLive();
  }
  async function transcribe(blob, name) {
    busy = true; controls(); stopSpeech(); show('Ovoz matnga aylantirilmoqda…');
    $('[data-agent-retry]').hidden = true;
    let result, command;
    try {
      if (blob.size > (config.max_audio_bytes || 5242880)) throw new Error('Audio hajmi 5 MB dan oshmasligi kerak.');
      const body = new FormData();
      body.append('audio', await VoiceAudio.toWav(blob), 'command.wav');
      result = await api(panel.dataset.transcribeUrl, body, 55000);
      command = result.transcript || '';
      if (!command.trim()) throw new Error('Ovozdan matn topilmadi. Qayta gapiring.');
      input.value = command; retryAction = undefined;
      show('Eshitilgan matn tayyor. Uni tuzatishingiz va yuborishingiz mumkin.');
    } catch (error) {
      show(error.message, true);
      // Muxlisa has no idempotency key: a retry is a new, separately billed call.
      retryAction = () => transcribe(blob, name);
      $('[data-agent-retry]').hidden = false;
    } finally { busy = false; controls(); }
    if (command && !review.checked && !panel.hidden) send(command);
  }
  async function startRecording() {
    if (recording) { recorder.stop(); return; }
    if (busy || permission) return;
    stopSpeech(); permission = true; controls(); show('Mikrofonga ruxsat kutilmoqda…');
    const version = ++generation;
    try {
      const acquired = await navigator.mediaDevices.getUserMedia({audio: {channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true}});
      if (version !== generation || panel.hidden) { acquired.getTracks().forEach(track => track.stop()); return; }
      stream = acquired;
      const type = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4', 'audio/ogg;codecs=opus'].find(value => MediaRecorder.isTypeSupported(value));
      const current = new MediaRecorder(stream, type ? {mimeType: type} : undefined);
      recorder = current;
      const chunks = [];
      current.ondataavailable = event => { if (event.data.size) chunks.push(event.data); };
      current.onstop = () => {
        clearInterval(timer); acquired.getTracks().forEach(track => track.stop());
        if (version !== generation) return;
        recording = false; controls();
        const mime = current.mimeType || type || 'audio/webm';
        const extension = mime.includes('mp4') ? 'm4a' : mime.includes('ogg') ? 'ogg' : 'webm';
        const blob = new Blob(chunks, {type: mime});
        if (blob.size) transcribe(blob, `voice.${extension}`); else show('Yozuv bo‘sh. Qayta gapiring.', true);
      };
      current.onerror = () => { abandonRecording(); show('Mikrofon yozuvi uzildi. Qayta urinib ko‘ring.', true); };
      current.start(); recording = true; recordingSeconds = 0;
      show('Tinglayapman. Gapirib bo‘lgach «To‘xtatish»ni bosing.');
      timer = setInterval(() => { recordingSeconds++; controls(); if (recordingSeconds >= (config.max_seconds || 29) && current.state === 'recording') current.stop(); }, 1000);
    } catch (error) {
      stream?.getTracks().forEach(track => track.stop());
      if (version === generation) show(error.name === 'NotAllowedError' ? 'Mikrofon ruxsatini bering yoki buyruqni yozing.' : 'Mikrofon ulanmagan yoki band. Audio fayl yuborish ham mumkin.', true);
    } finally { if (version === generation) { permission = false; controls(); } }
  }
  function flushLive() {
    if (!queuedLive || !live?.active || busy || live.detector.active || live.waiting) return;
    const command = queuedLive; queuedLive = undefined; send(command);
  }
  function stopLive() {
    turnEpoch++; queuedLive = undefined; stopSpeech(); live?.stop(); controls();
  }
  async function toggleLive() {
    if (live?.active) { stopLive(); return; }
    if (busy || permission || recording) return;
    if (window.isSecureContext === false) {
      show(`Sayt himoyasiz ${location.protocol}//${location.host} orqali ochilgan, brauzer mikrofonni bermaydi. Serverni --https bilan ishga tushirib, https://${location.hostname} manzilini oching.`, true); return;
    }
    if (!window.AudioContext || !window.AudioWorkletNode || !window.Worker || !window.VoiceNoiseFilter || !navigator.mediaDevices?.getUserMedia) {
      show('Brauzer jonli mikrofonni qo‘llamaydi. HTTPS yoki localhost orqali zamonaviy brauzerda oching.', true); return;
    }
    stopSpeech();
    live = new TaskRealtime.LiveSession({workletURL: panel.dataset.workletUrl,
      filterAssets: {workerURL: panel.dataset.filterWorkerUrl, runtimeURL: panel.dataset.filterRuntimeUrl,
        modelURL: panel.dataset.filterModelUrl},
      ticket: () => api(panel.dataset.sessionUrl, {}, 20000),
      isPlayback: () => Boolean(liveSpeech?.playing || !audio.paused),
      onLevel: level => { $('.agent-meter span').style.width = `${Math.round(level*100)}%`; },
      onSpeechStart: () => { turnEpoch++; queuedLive = undefined; stopSpeech(); },
      onTranscript: command => {
        input.value = command;
        if (review.checked) show('Eshitilgan matnni tekshirib yuboring.');
        else { queuedLive = command; show(busy ? 'Oldingi so‘rov tugashi bilan yangi gapingiz yuboriladi.' : 'Gapingiz qabul qilindi.'); flushLive(); }
      },
      onState: (state, message) => {
        liveState = state;
        $('[data-live-label]').textContent = ({off: 'Suhbat tugadi', connecting: 'Filtr va ulanish tayyorlanmoqda…', speaking: 'Siz gapiryapsiz', recognizing: 'Nutq aniqlanmoqda…', reconnecting: 'Ulanish tiklanmoqda…', listening: 'Tinglayapman · filtr faol'})[state] || message;
        if (state !== 'listening' || (!busy && !liveSpeech?.playing)) show(message);
        controls();
      },
      onError: message => { turnEpoch++; queuedLive = undefined; stopSpeech(); show(message, true); controls(); },
    });
    await live.start(); controls();
  }
  document.addEventListener('click', event => {
    const button = event.target.closest('[data-agent-open]');
    if (button) open(button);
  });
  $('[data-agent-live]').addEventListener('click', toggleLive);
  $('[data-agent-close]').addEventListener('click', close);
  $('[data-agent-expand]').addEventListener('click', () => { compact(false); scroll.scrollTop = scroll.scrollHeight; input.focus(); });
  document.addEventListener('keydown', event => { if (event.key === 'Escape' && !panel.hidden) { event.preventDefault(); close(); } });
  form.addEventListener('submit', event => { event.preventDefault(); send(input.value); });
  input.addEventListener('keydown', event => { if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); form.requestSubmit(); } });
  panel.querySelectorAll('[data-agent-example]').forEach(button => button.addEventListener('click', () => send(button.dataset.agentExample)));
  $('[data-agent-confirm]').addEventListener('click', () => { if (proposal) send('tasdiqlayman', proposal.id); });
  $('[data-agent-cancel]').addEventListener('click', () => { if (proposal) send('bekor qil', proposal.id); });
  $('[data-agent-link]').addEventListener('click', () => { stopSpeech(); storage.remove('pending-speech'); });
  record.addEventListener('click', startRecording);
  $('[data-agent-file]').addEventListener('click', () => $('[data-agent-upload]').click());
  $('[data-agent-upload]').addEventListener('change', event => { const file = event.target.files[0]; if (file) transcribe(file, file.name); event.target.value = ''; });
  $('[data-agent-retry]').addEventListener('click', () => retryAction?.());
  $('[data-agent-listen]').addEventListener('click', () => speak(replyToken));
  $('[data-agent-stop]').addEventListener('click', () => { turnEpoch++; stopSpeech(); });
  auto.addEventListener('change', () => { storage.set('auto', String(auto.checked)); if (!auto.checked) stopSpeech(); });
  review.addEventListener('change', () => storage.set('review', String(review.checked)));
  $('[data-agent-reset]').addEventListener('click', async () => {
    if (busy || recording || permission) return;
    stopSpeech(); busy = true; controls();
    try {
      await api(panel.dataset.resetUrl, {conversation_id: conversation});
      compact(false);
      conversation = crypto.randomUUID(); storage.set('conversation', conversation); storage.remove('pending-speech');
      replyToken = undefined; retryAction = undefined; input.value = ''; $('.agent-navigation').hidden = true;
      $('[data-agent-listen]').hidden = true; $('[data-agent-retry]').hidden = true;
      renderMessages([]); renderProposal(null); show('Yangi suhbat. Nima qilish kerak?');
      await load();
    } catch (error) { show(error.message, true); }
    finally { busy = false; controls(); }
  });
  window.addEventListener('pagehide', () => { stopLive(); abandonRecording(); stopSpeech(); });
  document.addEventListener('visibilitychange', () => { if (document.hidden && live?.active) stopLive(); });
  window.addEventListener('popstate', () => { if (usedSoftNavigation) { stopLive(); location.reload(); } });
  if (storage.get('open') === 'true' || new URLSearchParams(location.search).has('voice')) {
    open().then(() => {
      const pending = storage.get('pending-speech'); storage.remove('pending-speech');
      if (pending && initialized && auto.checked) try {
        const saved = JSON.parse(pending);
        if (Date.now() - saved.at < 60000) { replyToken = saved.token; $('[data-agent-listen]').hidden = !config.tts; speak(replyToken); }
      } catch (_) {}
    });
  }
})();
