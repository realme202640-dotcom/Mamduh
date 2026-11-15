document.addEventListener("DOMContentLoaded", () => {
  const $ = (id) => document.getElementById(id);
  const tokenKey = "mirsal_access_token";
  const state = { token: localStorage.getItem(tokenKey), chats: [], selectedChatId: null, calls: [], streams: [], activeCallId: null, mediaStream: null, screenStream: null, muted: false };
  const views = ["auth-view", "chat-view", "broadcast-view", "calls-view"];

  function toast(message) {
    const el = $("toast"); el.textContent = message; el.classList.remove("hidden");
    clearTimeout(toast.timer); toast.timer = setTimeout(() => el.classList.add("hidden"), 3500);
  }
  async function api(path, options = {}) {
    const headers = { ...(options.headers || {}) };
    if (state.token) headers.Authorization = `Bearer ${state.token}`;
    if (options.body) headers["Content-Type"] = "application/json";
    const response = await fetch(path, { ...options, headers });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || "حدث خطأ في الاتصال بالخادم");
    return data;
  }
  function showView(id) {
    views.forEach((view) => $(view).classList.toggle("hidden", view !== id));
    document.querySelectorAll(".nav-btn").forEach((button) => button.classList.toggle("active", button.dataset.view === id));
  }
  function showAuth(message = "") {
    state.token = null; localStorage.removeItem(tokenKey); $("otp-box").classList.add("hidden"); showView("auth-view");
    if (message) { $("auth-message").textContent = message; $("auth-message").className = "message error"; }
  }
  function renderChats() {
    const query = $("chat-search").value.trim().toLocaleLowerCase();
    const filtered = state.chats.filter((chat) => `${chat.name} ${chat.phone} ${chat.lastMessage}`.toLocaleLowerCase().includes(query));
    const list = $("chat-list"); list.replaceChildren();
    if (!filtered.length) { list.innerHTML = '<div class="empty-state">لا توجد نتائج. استخدم ＋ لبدء محادثة.</div>'; return; }
    filtered.forEach((chat) => {
      const button = document.createElement("button"); button.type = "button"; button.className = `chat-item ${chat.id === state.selectedChatId ? "active" : ""}`;
      const info = document.createElement("div"); info.className = "chat-info";
      const avatar = document.createElement("div"); avatar.className = "avatar small"; avatar.textContent = (chat.name || "م").slice(0, 1);
      const text = document.createElement("div"); text.className = "chat-text";
      const name = document.createElement("strong"); name.textContent = chat.name;
      const preview = document.createElement("span"); preview.textContent = chat.lastMessage || chat.phone;
      text.append(name, preview); info.append(avatar, text); button.append(info); button.addEventListener("click", () => openChat(chat.id)); list.append(button);
    });
  }
  async function loadChats() { state.chats = await api("/api/chats"); renderChats(); }
  async function openChat(id) {
    state.selectedChatId = id; const chat = state.chats.find((item) => item.id === id); if (!chat) return;
    $("chat-title").textContent = chat.name; $("chat-avatar").textContent = (chat.name || "م").slice(0, 1); $("chat-status").textContent = chat.phone;
    renderChats(); showView("chat-view"); await loadMessages();
  }
  async function loadMessages() {
    if (!state.selectedChatId) return;
    const messages = await api(`/api/chats/${state.selectedChatId}/messages`); const box = $("messages"); box.replaceChildren();
    if (!messages.length) { box.innerHTML = '<div class="empty-state">ابدأ المحادثة بإرسال رسالة.</div>'; return; }
    messages.forEach((message) => {
      const bubble = document.createElement("div"); bubble.className = `message-bubble ${message.sender === "Me" ? "me" : "other"}`;
      const body = document.createElement("div"); body.textContent = message.text;
      const time = document.createElement("small"); time.textContent = message.time;
      bubble.append(body, time); box.append(bubble);
    }); box.scrollTop = box.scrollHeight;
  }
  async function loadStreams() {
    state.streams = await api("/api/live"); const list = $("stream-list"); list.replaceChildren();
    state.streams.forEach((stream) => {
      const row = document.createElement("div"); row.className = "stream-item";
      const info = document.createElement("div"); const title = document.createElement("strong"); title.textContent = stream.title;
      const host = document.createElement("span"); host.textContent = stream.host; info.append(title, host);
      const tag = document.createElement("span"); tag.className = `tag ${stream.status === "live" ? "live" : "scheduled"}`; tag.textContent = stream.status === "live" ? "مباشر" : "متوقف"; row.append(info, tag); list.append(row);
    });
    const current = state.streams.find((s) => s.status === "live");
    $("live-title").textContent = current ? current.title : "ابدأ بثاً جديداً"; $("viewers-count").textContent = current ? current.viewers : 0;
    $("host-name").textContent = current ? "مباشر الآن" : "غير مباشر"; $("live-badge").textContent = current ? "LIVE" : "جاهز";
  }
  async function loadCalls() {
    state.calls = await api("/api/calls"); const list = $("calls-list"); list.replaceChildren();
    state.calls.forEach((call) => {
      const row = document.createElement("div"); row.className = "call-item";
      const info = document.createElement("div"); info.className = "call-text"; const name = document.createElement("strong"); name.textContent = call.contact;
      const time = document.createElement("span"); time.textContent = call.status === "active" ? "مكالمة جارية" : call.duration; info.append(name, time);
      const status = document.createElement("span"); status.className = `status ${call.status === "active" ? "incoming" : "missed"}`; status.textContent = call.status === "active" ? "جارية" : "منتهية"; row.append(info, status); list.append(row);
    });
  }
  async function startLocalMedia(video = false) {
    if (!navigator.mediaDevices?.getUserMedia) throw new Error("المتصفح لا يدعم الوصول إلى الكاميرا والميكروفون");
    state.mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true, video });
    $("call-preview").srcObject = state.mediaStream; return state.mediaStream;
  }
  async function startCall(video = false) {
    if (!state.token) return showAuth("سجّل الدخول أولاً");
    const contact = prompt("أدخل رقم جهة الاتصال مع مفتاح الدولة (مثال +9665...)"); if (!contact) return;
    try {
      await startLocalMedia(video);
      const call = await api("/api/calls", { method: "POST", body: JSON.stringify({ contact }) });
      state.activeCallId = call.id; $("call-contact").textContent = call.contact; $("call-state").textContent = video ? "اتصال فيديو — معاينة محلية" : "اتصال صوتي — الميكروفون متصل";
      $("call-stage").classList.remove("hidden"); showView("calls-view"); await loadCalls();
      toast("تم بدء واجهة المكالمة. الاتصال بالطرف الآخر يحتاج خادم WebRTC وإشارات اتصال.");
    } catch (error) { stopMedia(); toast(error.message); }
  }
  function stopMedia() {
    [state.mediaStream, state.screenStream].forEach((stream) => stream?.getTracks().forEach((track) => track.stop()));
    state.mediaStream = null; state.screenStream = null; $("call-preview").srcObject = null; $("live-preview").srcObject = null;
  }

  $("register-btn").addEventListener("click", async () => {
    const raw = $("phone-input").value.trim(); if (!raw) return toast("أدخل رقم الهاتف");
    const phone = raw.startsWith("+") ? raw : `${$("country-code").value}${raw.replace(/^0+/, "")}`;
    try { await api("/api/register", { method: "POST", body: JSON.stringify({ phone, country_code: $("country-code").value }) });
      $("otp-box").classList.remove("hidden"); $("auth-message").textContent = "أرسلنا رمز التحقق برسالة SMS."; $("auth-message").className = "message success";
    } catch (error) { $("auth-message").textContent = error.message; $("auth-message").className = "message error"; }
  });
  $("verify-btn").addEventListener("click", async () => {
    const raw = $("phone-input").value.trim(); const phone = raw.startsWith("+") ? raw : `${$("country-code").value}${raw.replace(/^0+/, "")}`;
    try { const result = await api("/api/verify", { method: "POST", body: JSON.stringify({ phone, code: $("otp-input").value.trim() }) });
      state.token = result.access_token; localStorage.setItem(tokenKey, state.token); $("profile-phone").textContent = result.phone; $("auth-message").classList.add("hidden");
      showView("chat-view"); await Promise.all([loadChats(), loadStreams(), loadCalls()]);
    } catch (error) { $("auth-message").textContent = error.message; $("auth-message").className = "message error"; }
  });
  $("message-form").addEventListener("submit", async (event) => {
    event.preventDefault(); const text = $("message-input").value.trim(); if (!text || !state.selectedChatId) return;
    try { await api(`/api/chats/${state.selectedChatId}/messages`, { method: "POST", body: JSON.stringify({ text }) }); $("message-input").value = ""; await Promise.all([loadMessages(), loadChats()]); }
    catch (error) { toast(error.message); }
  });
  $("chat-search").addEventListener("input", renderChats);
  $("new-chat").addEventListener("click", async () => {
    const phone = prompt("رقم الهاتف مع مفتاح الدولة (مثال +9665...)"); if (!phone) return;
    const name = prompt("اسم جهة الاتصال (اختياري)") || "";
    try { const chat = await api("/api/chats", { method: "POST", body: JSON.stringify({ phone, name }) }); await loadChats(); await openChat(chat.id); }
    catch (error) { toast(error.message); }
  });
  $("start-live").addEventListener("click", async () => {
    const title = prompt("عنوان البث"); if (!title) return;
    try {
      const media = await navigator.mediaDevices.getUserMedia({ audio: true, video: true }); state.mediaStream = media; $("live-preview").srcObject = media;
      await api("/api/live", { method: "POST", body: JSON.stringify({ title }) }); await loadStreams(); toast("بدأت معاينة البث على هذا الجهاز. النشر للمشاهدين يحتاج خادم بث/WebRTC.");
    } catch (error) { stopMedia(); toast(error.message || "تعذر بدء البث"); }
  });
  $("new-call").addEventListener("click", () => startCall(false));
  $("audio-call").addEventListener("click", () => startCall(false));
  $("video-call").addEventListener("click", () => startCall(true));
  $("mute-call").addEventListener("click", () => {
    state.muted = !state.muted; state.mediaStream?.getAudioTracks().forEach((track) => { track.enabled = !state.muted; });
    $("mute-call").classList.toggle("selected", state.muted); $("mute-call").querySelector("span").textContent = state.muted ? "إلغاء الكتم" : "كتم";
  });
  $("share-screen").addEventListener("click", async () => {
    try {
      if (!navigator.mediaDevices?.getDisplayMedia) throw new Error("مشاركة الشاشة غير مدعومة في هذا المتصفح");
      state.screenStream = await navigator.mediaDevices.getDisplayMedia({ video: true }); $("call-preview").srcObject = state.screenStream;
      state.screenStream.getVideoTracks()[0].addEventListener("ended", () => { $("call-preview").srcObject = state.mediaStream; state.screenStream = null; });
    } catch (error) { toast(error.message); }
  });
  $("end-call").addEventListener("click", async () => {
    try { if (state.activeCallId) await api(`/api/calls/${state.activeCallId}/end`, { method: "POST" }); }
    catch (error) { toast(error.message); }
    state.activeCallId = null; $("call-stage").classList.add("hidden"); stopMedia(); await loadCalls();
  });
  $("logout").addEventListener("click", async () => {
    try { await api("/api/logout", { method: "POST" }); } catch (_) { /* Clear expired local session too. */ }
    showAuth("تم تسجيل الخروج");
  });
  document.querySelectorAll(".nav-btn").forEach((button) => button.addEventListener("click", () => {
    if (!state.token) return showView("auth-view");
    showView(button.dataset.view);
  }));

  if (state.token) {
    api("/api/me").then(async (user) => { $("profile-phone").textContent = user.phone; showView("chat-view"); await Promise.all([loadChats(), loadStreams(), loadCalls()]); })
      .catch(() => showAuth("انتهت الجلسة، سجّل الدخول مجدداً"));
  } else showView("auth-view");
});
