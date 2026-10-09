/* 听课搭子 网页版 · 实时草稿字幕
   用浏览器自带的语音识别(Chrome / Edge 桌面版)出「正在说的这句」的灰色草稿;定稿和中文照旧来自服务器(Groq + DeepSeek)。
   手机、平板、Safari 先不开:自带识别和录音会抢同一个麦克风,可能把正在录的课弄断。 */
(function(){
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  const ua = navigator.userAgent || "";
  const mobile = /Android|iPhone|iPad|iPod/i.test(ua) || (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1);
  const chromium = /Chrome\/|Edg\//.test(ua) && !/OPR\//.test(ua);
  const supported = !!SR && !mobile && chromium;
  let rec = null, active = false, onText = null, broken = false, restarts = 0;
  function make(){
    const r = new SR();
    r.lang = "en-GB"; r.continuous = true; r.interimResults = true; r.maxAlternatives = 1;
    r.onresult = e => {
      let interim = "";
      for(let i = e.resultIndex; i < e.results.length; i++){
        if(!e.results[i].isFinal) interim += e.results[i][0].transcript;
      }
      interim = interim.trim();
      if(onText) onText(interim.length > 160 ? "…" + interim.slice(-160) : interim);
    };
    r.onerror = e => { if(e.error === "not-allowed" || e.error === "service-not-allowed" || e.error === "audio-capture"){ broken = true; active = false; if(onText) onText(""); } };
    r.onend = () => {                 // Chrome 每隔一段时间或静音后会自己停,还在上课就接着开
      if(active && !broken && restarts < 500){ restarts++; setTimeout(() => { try{ if(active) r.start(); }catch(_){ } }, 250); }
    };
    return r;
  }
  window.TKDraft = {
    supported,
    start(cb){ if(!supported || broken) return false; onText = cb; active = true; restarts = 0; try{ rec = rec || make(); rec.start(); }catch(_){ } return true; },
    stop(){ active = false; try{ rec && rec.stop(); }catch(_){ } if(onText) onText(""); }
  };
})();
