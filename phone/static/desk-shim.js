/* 听课搭子 网页版「电脑布局」适配层(在电脑版页面脚本之前加载)
   电脑版页面一行不改:它调用 /config、/start、/stream… 这里改成 /live/d/…?k=钥匙;
   电脑版由本机程序收音,网页版改成浏览器麦克风:按停顿切成 ≤15 秒的 WAV 段传 /live/chunk,服务器用 Groq 识别。 */
(function(){
  const url = new URL(location.href);
  let K = url.searchParams.get("k");
  try{ if(K) localStorage.setItem("k", K); else K = localStorage.getItem("k"); }catch(e){}
  if(url.searchParams.get("k")) history.replaceState(null, "", location.pathname);
  if(!K){ location.replace("/live/"); return; }

  const DESK = /^\/(config|records|balance|start|pause|stop|handoff|autoqa|translate|ask|ask\/history|ask\/save|slides|slides\/remove|settings|settings\/test|settings\/models|context|stream)(\?|$)/;
  const API = p => { const i = p.indexOf("?"); const path = i < 0 ? p : p.slice(0, i), q = i < 0 ? "" : p.slice(i + 1);
    return "/live/d" + path + "?k=" + encodeURIComponent(K) + (q ? "&" + q : ""); };
  const jsonResp = (o, status=200) => new Response(JSON.stringify(o), {status, headers:{"Content-Type":"application/json"}});
  const pageState = () => { try{ return S.state; }catch(e){ return "idle"; } };
  const pageElapsed = () => { try{ return S.elapsed || 0; }catch(e){ return 0; } };

  /* ---------------- 麦克风 → WAV 段 ---------------- */
  const mic = { on:false, stream:null, actx:null, src:null, proc:null, sink:null, inRate:48000,
    sid:null, seq:0, base:0, t0:0, pcm:[], n:0, segT0:0, voiced:0, silence:0, segMax:0, rawMax:0, got:false, pending:Promise.resolve() };
  const VTHR = 0.006, MAXSEG = 15;
  mic.clock = () => mic.base + (mic.on ? (performance.now() - mic.t0) / 1000 : 0);
  // 第一步必须在点击的那一刻同步执行(iPhone / Safari 只认用户点击里创建并唤醒的音频环境)
  mic.prepare = function(){
    const AC = window.AudioContext || window.webkitAudioContext;
    if(!AC || !navigator.mediaDevices || !navigator.mediaDevices.getUserMedia)
      return Promise.reject(new Error("这个浏览器不支持录音,请用 Chrome / Edge / Safari 打开(别在微信里打开)"));
    mic.actx = new AC(); try{ mic.actx.resume(); }catch(e){}
    return navigator.mediaDevices.getUserMedia({audio:{echoCancellation:false, noiseSuppression:true, autoGainControl:true}})
      .then(s => { mic.stream = s; return s; });
  };
  function build(){
    mic.inRate = mic.actx.sampleRate;
    mic.src = mic.actx.createMediaStreamSource(mic.stream);
    mic.proc = mic.actx.createScriptProcessor(4096, 1, 1);
    mic.sink = mic.actx.createGain(); mic.sink.gain.value = 0;      // 接到输出才会持续回调;音量 0,不出声
    mic.src.connect(mic.proc); mic.proc.connect(mic.sink); mic.sink.connect(mic.actx.destination);
    mic.proc.onaudioprocess = e => { mic.got = true; if(mic.on) onAudio(new Float32Array(e.inputBuffer.getChannelData(0))); };
  }
  function newSeg(){ mic.pcm = []; mic.n = 0; mic.voiced = 0; mic.silence = 0; mic.segMax = 0; }
  mic.run = async function(sid, seq, base){
    mic.sid = sid; mic.seq = seq || 0; mic.base = base || 0; mic.t0 = performance.now();
    try{ await mic.actx.resume(); }catch(e){}
    build(); newSeg(); mic.got = false; mic.rawMax = 0; mic.on = true;
    try{ mic.wake = await navigator.wakeLock?.request("screen"); }catch(e){}
    setTimeout(async () => {          // 3 秒还没声音数据(或全是 0,个别 iPhone 会这样):换个新的音频环境再接一次
      if(!mic.on || (mic.got && mic.rawMax > 0)) return;
      teardownGraph(); try{ mic.actx.close(); }catch(e){}
      const AC = window.AudioContext || window.webkitAudioContext; mic.actx = new AC(); try{ await mic.actx.resume(); }catch(e){}
      build(); newSeg();
      setTimeout(() => { if(mic.on && !(mic.got && mic.rawMax > 0)) try{ toast("麦克风没有声音数据:确认浏览器允许了麦克风;手机上别在微信 / QQ 里打开","error"); }catch(e){} }, 3000);
    }, 3000);
  };
  function onAudio(x){
    let s2 = 0; for(let i = 0; i < x.length; i++) s2 += x[i] * x[i];
    const rms = Math.sqrt(s2 / x.length), dt = x.length / mic.inRate;
    if(rms > mic.rawMax) mic.rawMax = rms;
    try{ levels.shift(); levels.push(Math.min(1, rms * 12)); }catch(e){}
    if(!mic.n) mic.segT0 = mic.clock();
    mic.pcm.push(x); mic.n += x.length; mic.segMax = Math.max(mic.segMax, rms);
    if(rms >= VTHR){ mic.voiced += dt; mic.silence = 0; } else mic.silence += dt;
    const len = mic.n / mic.inRate;
    if((mic.voiced >= 0.6 && mic.silence >= 1.0 && len >= 2) || len >= MAXSEG) flush();
  }
  function flush(){
    const len = mic.n / mic.inRate, t0 = mic.segT0, chunks = mic.pcm, n = mic.n, mx = mic.segMax, inR = mic.inRate;
    newSeg();
    if(n && len >= 0.8 && mx >= 0.0015){
      const wav = encodeWav(chunks, n, inR, 16000), seq = mic.seq++, sid = mic.sid;
      mic.pending = mic.pending.then(() => upload(wav, sid, seq, t0, len));
    }
    return mic.pending;
  }
  function encodeWav(chunks, n, inR, outR){
    const flat = new Float32Array(n); let o = 0; for(const c of chunks){ flat.set(c, o); o += c.length; }
    const ratio = inR / outR, m = Math.floor(n / ratio), out = new Int16Array(m);
    for(let i = 0; i < m; i++){
      const a = Math.floor(i * ratio), b = Math.min(n, Math.floor((i + 1) * ratio)); let s3 = 0;
      for(let j = a; j < b; j++) s3 += flat[j];
      out[i] = Math.max(-1, Math.min(1, s3 / Math.max(1, b - a))) * 0x7fff;
    }
    const buf = new ArrayBuffer(44 + m * 2), dv = new DataView(buf);
    const w = (p, t) => { for(let i = 0; i < t.length; i++) dv.setUint8(p + i, t.charCodeAt(i)); };
    w(0,"RIFF"); dv.setUint32(4, 36 + m * 2, true); w(8,"WAVE"); w(12,"fmt "); dv.setUint32(16,16,true);
    dv.setUint16(20,1,true); dv.setUint16(22,1,true); dv.setUint32(24,outR,true); dv.setUint32(28,outR*2,true);
    dv.setUint16(32,2,true); dv.setUint16(34,16,true); w(36,"data"); dv.setUint32(40, m * 2, true);
    new Int16Array(buf, 44).set(out);
    return new Blob([buf], {type:"audio/wav"});
  }
  async function upload(blob, sid, seq, t0, dur){
    const fd = new FormData(); fd.append("sid", sid); fd.append("seq", String(seq)); fd.append("t0", t0.toFixed(1)); fd.append("dur", dur.toFixed(1));
    fd.append("mime", "audio/wav"); fd.append("file", blob, "chunk.wav");
    for(let i = 0; i < 3; i++){        // 网络抖一下就重试,别丢这一句
      try{ const r = await nativeFetch("/live/chunk?k=" + encodeURIComponent(K), {method:"POST", body:fd}); if(r.ok || r.status === 410) return; }catch(e){}
      await new Promise(r => setTimeout(r, 1500 * (i + 1)));
    }
    try{ toast("网络不好,有一段没传上去","error"); }catch(e){}
  }
  function teardownGraph(){
    try{ if(mic.proc){ mic.proc.onaudioprocess = null; mic.proc.disconnect(); } if(mic.src) mic.src.disconnect(); if(mic.sink) mic.sink.disconnect(); }catch(e){}
  }
  // 停:把手里没说完的最后一句也传上去,等上传完再返回
  mic.stop = function(){
    if(mic.on){ mic.base = mic.clock(); try{ if(mic.n) flush(); }catch(e){} }
    mic.on = false; teardownGraph();
    mic.release();
    return mic.pending;
  };
  mic.release = function(){
    try{ mic.stream?.getTracks().forEach(t => t.stop()); }catch(e){}
    try{ mic.actx?.close(); }catch(e){}
    try{ mic.wake?.release?.(); }catch(e){}
    mic.stream = mic.actx = mic.proc = mic.src = mic.sink = null;
  };
  window.TK = {K, mic, API};

  /* ---------------- 接口改道 ---------------- */
  const nativeFetch = window.fetch.bind(window);
  function openLink(uri){
    if(!uri) return;
    if(uri.startsWith("obsidian://")){ location.href = uri; return; }
    window.open("/live/" + uri + (uri.includes("?") ? "&" : "?") + "k=" + encodeURIComponent(K), "_blank");
  }
  TK.openLink = openLink;
  window.fetch = async function(u, opt){
    opt = opt || {};
    if(typeof u !== "string") return nativeFetch(u, opt);
    if(u === "/open"){ try{ openLink(JSON.parse(opt.body || "{}").uri); }catch(e){} return jsonResp({ok:true}); }
    if(!DESK.test(u)) return nativeFetch(u, opt);
    const path = u.split("?")[0];
    let body = opt.body;
    if(path === "/settings" || path === "/settings/test"){       // 设置页里「手机版链接」那一格在网页版是 Groq key
      if(opt.method === "POST"){ const b = JSON.parse(body || "{}"); b.groq_key = (b.phone_link || "").trim(); delete b.phone_link; body = JSON.stringify(b); }
    }
    if(path === "/start"){
      // 点击的同一刻先要麦克风,拿不到就不开课
      try{ await mic.prepare(); }catch(e){ mic.release(); return jsonResp({ok:false, msg:"拿不到麦克风:" + (e.message || e) + "。点地址栏左边的锁图标,把麦克风改成允许"}, 400); }
      const r = await nativeFetch(API(u), {...opt, body}); const j = await r.clone().json().catch(() => ({}));
      if(j.ok) mic.run(j.sid, j.next_seq || 0, j.elapsed || 0); else mic.release();
      return r;
    }
    if(path === "/pause"){
      if(pageState() === "recording"){
        const el = mic.on ? mic.clock() : pageElapsed();
        await mic.stop();
        return nativeFetch(API(u), {...opt, method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({elapsed:el})});
      }
      try{ await mic.prepare(); }catch(e){ mic.release(); return jsonResp({ok:false, msg:"拿不到麦克风:" + (e.message || e)}, 400); }
      const r = await nativeFetch(API(u), {...opt, body}); const j = await r.clone().json().catch(() => ({}));
      if(j.ok && j.state === "recording") mic.run(j.sid, j.next_seq || 0, j.elapsed || 0); else mic.release();
      return r;
    }
    if(path === "/stop"){
      const el = mic.on ? mic.clock() : pageElapsed();
      await mic.stop();
      return nativeFetch(API(u), {...opt, method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({elapsed:el})});
    }
    const r = await nativeFetch(API(u), {...opt, body});
    if(path === "/balance"){
      r.clone().json().then(b => { if(b && b.trial) setTimeout(() => {
        try{ document.querySelector(".bal-h").firstChild.textContent = "免费试用 ";
          document.getElementById("bal-v").textContent = "还剩 " + Math.floor((b.trial_left || 0) / 60) + " 分钟";
          document.getElementById("bal-s").textContent = "用完后点下面「设置」填自己的 Groq / DeepSeek key(都免费申请)"; }catch(e){} }, 60); }).catch(() => {});
    }
    return r;
  };

  /* ---------------- 推送改道 ---------------- */
  const NativeES = window.EventSource;
  window.EventSource = function(u, cfg){
    if(typeof u === "string" && DESK.test(u)) u = API(u);
    const es = new NativeES(u, cfg);
    let handler = null;
    es.addEventListener("message", e => {
      if(!handler) return;
      let d = e.data;
      try{
        const m = JSON.parse(d);
        if(m.type === "context") TK.ctxDone = true;
        if(m.type === "saved"){ m.path = m.where || m.path; if(!m.uri && m.download) m.uri = m.download; d = JSON.stringify(m); }
        if(m.type === "state" && mic.on && m.state !== "recording"){ mic.stop(); }     // 别的设备暂停/结束了这节课
      }catch(err){}
      handler({data:d});
      try{
        const m = JSON.parse(d);
        if(m.type === "state" && m.state === "idle") TK.ctxDone = false;
        if(m.type === "saved" && !(m.uri || "").startsWith("obsidian://"))     // 网页版的同步目录 / 服务器:换成实话实说的提示
          setTimeout(() => { try{ toast("已保存:" + (m.where || m.path) + "(共 " + m.n + " 句)。左边「课堂记录」里点它可以下载", "ok"); }catch(e){} }, 0);
      }catch(err){}
    });
    Object.defineProperty(es, "onmessage", {get(){ return handler; }, set(f){ handler = f; }, configurable:true});
    return es;
  };
  addEventListener("pagehide", () => { if(mic.on) try{ flush(); }catch(e){} });
})();
