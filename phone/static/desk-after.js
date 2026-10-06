/* 听课搭子 网页版「电脑布局」:电脑版页面脚本跑完之后的几处网页版专属调整(不改它的模块和布局) */
(function(){
  if(!window.TK) return;
  const $ = s => document.querySelector(s);
  // 0) 平板宽度(约 1000–1300px)下按钮和模块标题别折成两行
  const st = document.createElement("style");
  st.textContent = "#ctrl .btn{white-space:nowrap;flex:none} .mod-h{white-space:nowrap} .mod-h small{overflow:hidden;text-overflow:ellipsis;min-width:0} @media (max-width:1366px){#ctxInfo{display:none}}";
  document.head.appendChild(st);
  // 1) 网页版不需要「移交手机」:换台设备打开同一条链接就接上了
  const hb = $("#handBtn"); if(hb) hb.style.display = "none";
  // 2) 品牌旁标一下网页版
  const br = $(".brand"); if(br && !br.querySelector(".webtag")){ const t = document.createElement("small"); t.className = "webtag"; t.textContent = "网页版"; t.style.cssText = "font-size:12px;color:var(--mute);font-weight:400;margin-left:6px"; br.appendChild(t); }
  // 3) 设置页:「手机版链接」那一格改成 Groq key(网页版的语音识别用它)
  const ph = $("#stPhone");
  if(ph){
    ph.type = "password"; ph.placeholder = "gsk_ 开头;留空=沿用现在的"; ph.autocomplete = "off";
    const lab = ph.parentElement.querySelector("label");
    if(lab) lab.innerHTML = 'Groq key <span class="note" id="stGroqNow"></span> <span class="note">语音识别用,免费:<a href="https://console.groq.com/keys" target="_blank">console.groq.com/keys</a></span>';
    const foot = document.createElement("div"); foot.className = "note";
    foot.style.cssText = "font-size:12px;color:var(--mute);margin:-4px 0 10px";
    foot.innerHTML = '课程表、笔记同步到哪、你的专属链接:<a href="#" id="toAccount" style="color:var(--blue)">在账号设置里改</a>';
    ph.parentElement.after(foot);
    $("#toAccount").onclick = e => { e.preventDefault(); try{ localStorage.setItem("view","phone"); }catch(_){} location.href = "/live/?settings=1"; };
  }
  if(typeof openSettings === "function"){
    const orig = openSettings;
    openSettings = async function(){ await orig(); try{ $("#stPhone").value = ""; $("#stGroqNow").textContent = ST.has_groq ? ("现在:" + ST.groq_masked) : (ST.owner ? "(用作者的)" : "(还没填,正在用免费试用)"); }catch(e){} };
    const gb = $("#gearBtn"); if(gb) gb.onclick = openSettings;
  }
  // 4) 左下角加「手机布局」:平板 / 手机上想一格一格看就点它
  const sf = $(".side-foot");
  if(sf && !$("#phoneView")){
    const b = document.createElement("button"); b.className = "gear"; b.id = "phoneView"; b.title = "换成手机布局(一个模块一页)"; b.textContent = "📱";
    b.style.cssText = "flex:none;width:auto";
    b.onclick = () => { try{ localStorage.setItem("view","phone"); }catch(e){} location.href = "/live/"; };
    sf.appendChild(b);
  }
  // 「了解我」:新用户还没有这门课的记录时,别一直显示「打包中…」
  if(typeof ctxInfo === "function"){
    const orig = ctxInfo;
    ctxInfo = function(sources, tokens){ orig(sources, tokens);
      try{ if($("#ctxOn").checked && !(sources || []).length && TK.ctxDone) $("#ctxInfo").textContent = "暂时没有资料(这门课上过一节后就有)"; }catch(e){} };
  }
  // 5) 页面是刷新/重开的,而服务器上那节课还在「录制中」:麦克风已经断了,先暂停,等用户点「继续」(浏览器要求点一下才能开麦)
  if(typeof applyState === "function"){
    const orig = applyState; let checked = false;
    applyState = function(st){
      orig(st);
      if(!checked && st && st.state){ checked = true;
        if(st.state === "recording" && !TK.mic.on){ post("/pause"); toast("页面刷新过,录音停在这里了:点「▶ 继续」接着录", "error"); } }
    };
  }
})();
