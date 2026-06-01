// render.mjs — Retention-Optimized Video Renderer
// المراحل العشر لأقصى احتفاظ بالمشاهد

import { readFileSync, writeFileSync, mkdirSync, copyFileSync,
         symlinkSync, existsSync }                    from "fs";
import { spawnSync }                                   from "child_process";
import { chromium }                                    from "playwright";

const manifestPath = process.argv[2];
const outputPath   = process.argv[3];

if (!manifestPath || !outputPath) {
  console.error("Usage: node render.mjs <manifest.json> <output.mp4>");
  process.exit(1);
}

const props = JSON.parse(readFileSync(manifestPath, "utf-8"));
const { sentences, videos, audio, duration_s, title, word_timeline, aligned } = props;

const FPS    = 30;
const WIDTH  = 1080;
const HEIGHT = 1920;

const safeOut = outputPath.replace(/[^a-zA-Z0-9]/g, "_").replace(/_+/g, "_").slice(-22);
const TMP     = `/tmp/vsg_${safeOut}`;
mkdirSync(TMP, { recursive: true });

// ─────────────────────────────────────────────────────────────────────────────
// HELPERS
// ─────────────────────────────────────────────────────────────────────────────

function probeDuration(filePath) {
  const r = spawnSync("ffprobe", [
    "-v", "error", "-show_entries", "format=duration",
    "-of", "default=noprint_wrappers=1:nokey=1", filePath,
  ], { stdio: ["ignore", "pipe", "pipe"] });
  return parseFloat(r.stdout.toString().trim()) || 0;
}

const realAudioDuration = probeDuration(audio);
const effectiveDuration = realAudioDuration > 5 ? realAudioDuration : duration_s;
const totalFrames       = Math.ceil(effectiveDuration * FPS);

console.log(`📋 Sentences   : ${sentences.length}`);
console.log(`🎵 Audio       : ${realAudioDuration.toFixed(3)}s`);
console.log(`⏱️  Effective   : ${effectiveDuration.toFixed(3)}s`);
console.log(`🎞️  Frames      : ${totalFrames}`);

const isArabic = t => /[\u0600-\u06FF]/.test(t);
const esc      = s => (s||"").toString()
  .replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");

// ─────────────────────────────────────────────────────────────────────────────
// المرحلة 4: الكلمات المؤثرة — قاموس الكلمات القوية
// ─────────────────────────────────────────────────────────────────────────────

const POWER_WORDS_AR = [
  "سر","أسرار","صدمة","كارثة","خطر","تحذير","انتبه","نجاح",
  "فشل","ثروة","مال","قوة","مهم","فرصة","خطأ","علاج","مذهل",
  "حقيقة","إدمان","دموع","ألم","فرح","حب"
];
const POWER_WORDS_EN = [
  "secret","warning","danger","shock","success","failure",
  "money","wealth","power","critical","important","mistake",
  "future","intelligence","habit","opportunity","truth","disaster",
  "amazing","urgent","change","miracle","addiction","cure"
];

function isPowerWord(word) {
  const w = word.replace(/[^\u0600-\u06FFa-zA-Z]/g, "").toLowerCase();
  return POWER_WORDS_AR.some(p => w.includes(p)) ||
         POWER_WORDS_EN.some(p => w === p || w.includes(p));
}

// ─────────────────────────────────────────────────────────────────────────────
// VISUAL CONFIG
// ─────────────────────────────────────────────────────────────────────────────

const ACCENT_COLORS = ["#00FFFF","#39FF14","#FF003C","#FFD700","#A020F0","#FF6B00","#00E5FF","#FF1493"];
const TRANSITIONS   = ["fade","slideleft","slideright","slideup","smoothleft",
                       "smoothright","circleopen","radial","pixelize","dissolve"];

const getTransition = i => TRANSITIONS[i % TRANSITIONS.length];

const TEXT_POSITIONS = [
  { name: "top_left",   jc: "flex-start", ai: "flex-start", ta: "left",   pt: "220px", pb: "0" },
  { name: "top_center", jc: "flex-start", ai: "center",     ta: "center", pt: "220px", pb: "0" },
  { name: "top_right",  jc: "flex-start", ai: "flex-end",   ta: "right",  pt: "220px", pb: "0" },
  { name: "mid_left",   jc: "center",     ai: "flex-start", ta: "left",   pt: "0",     pb: "0" },
  { name: "mid_center", jc: "center",     ai: "center",     ta: "center", pt: "0",     pb: "0" },
  { name: "mid_right",  jc: "center",     ai: "flex-end",   ta: "right",  pt: "0",     pb: "0" },
  { name: "bot_left",   jc: "flex-end",   ai: "flex-start", ta: "left",   pt: "0",     pb: "260px" },
  { name: "bot_center", jc: "flex-end",   ai: "center",     ta: "center", pt: "0",     pb: "260px" },
  { name: "bot_right",  jc: "flex-end",   ai: "flex-end",   ta: "right",  pt: "0",     pb: "260px" },
];

const PATTERN_INTERRUPTS_AR = [
  "لكن انتظر...","الأمر مهم جداً","99% لا يعرفون هذا","لا تتخط هذه النقطة",
  "الجزء القادم هو الأهم","هنا يرتكب الجميع الخطأ","انتبه جيداً","هذا يغير كل شيء"
];
const PATTERN_INTERRUPTS_EN = [
  "Wait...","This is important","99% miss this","Don't skip this",
  "The next part matters","Most people fail here","Pay attention","This changes everything"
];

const ENGAGEMENT_QUESTIONS_AR = [
  "هل كنت تعرف هذا؟","كم مرة فعلت هذا؟","هل توافق؟",
  "اكتب نعم إذا فهمت","أخبرني برأيك","وصلت إلى هنا؟ 🔥","هل حدث لك هذا؟"
];
const ENGAGEMENT_QUESTIONS_EN = [
  "Did you know this?","How many times have you done this?","Do you agree?",
  "Comment YES if you understand","Tell me your opinion","Still watching? 🔥","Has this happened to you?"
];

function getEmojis(t) {
  t = (t||"").toLowerCase();
  if (t.includes("نجاح")||t.includes("success"))  return ["🏆","🔥"];
  if (t.includes("سر")||t.includes("secret"))     return ["🤫","👁️"];
  if (t.includes("مال")||t.includes("money"))     return ["💰","🚀"];
  if (t.includes("خطر")||t.includes("danger"))    return ["🚨","⚠️"];
  if (t.includes("ذكاء")||t.includes("mind"))     return ["🧠","⚡"];
  if (t.includes("قوة")||t.includes("power"))     return ["⚡","🔥"];
  if (t.includes("حياة")||t.includes("life"))     return ["🌟","💫"];
  return ["🎯","✨"];
}

// ─────────────────────────────────────────────────────────────────────────────
// PATTERN INTERRUPT SCREEN
// ─────────────────────────────────────────────────────────────────────────────

function buildPatternInterruptHTML(message, accent) {
  const isAr  = isArabic(message);
  const dir   = isAr ? "rtl" : "ltr";
  const bFont = isAr
    ? `"Noto Naskh Arabic","Amiri",serif`
    : `"Inter","Helvetica Neue",Arial,sans-serif`;

  return `<!DOCTYPE html>
<html lang="${isAr?"ar":"en"}">
<head>
  <meta charset="UTF-8"/>
  <link href="https://fonts.googleapis.com/css2?family=Noto+Naskh+Arabic:wght@700;800;900&family=Inter:wght@700;800;900&display=swap" rel="stylesheet"/>
  <style>
    *{margin:0;padding:0;box-sizing:border-box;}
    html,body{width:${WIDTH}px;height:${HEIGHT}px;overflow:hidden;background:transparent;}
    .pi-wrap{
      position:absolute;inset:0;
      display:flex;justify-content:center;align-items:center;
    }
    .pi-box{
      background:${accent};border-radius:30px;
      padding:44px 72px;max-width:920px;
      display:flex;align-items:center;gap:20px;direction:${dir};
      box-shadow:0 0 100px ${accent}aa,0 20px 80px rgba(0,0,0,0.9);
      transform: scale(1.05); filter: drop-shadow(0 0 20px ${accent});
    }
    .pi-text{
      font-family:${bFont};font-size:${isAr?"78px":"74px"};font-weight:900;
      color:#000;text-align:center;line-height:1.2;word-break:break-word;
      text-transform:uppercase;
    }
    .overlay{position:absolute;inset:0;background:rgba(0,0,0,0.65);pointer-events:none;}
  </style>
</head>
<body>
  <div class="overlay"></div>
  <div class="pi-wrap">
    <div class="pi-box">
      <div class="pi-text">${esc(message)}</div>
    </div>
  </div>
</body>
</html>`;
}

// ─────────────────────────────────────────────────────────────────────────────
// ENGAGEMENT QUESTION SCREEN
// ─────────────────────────────────────────────────────────────────────────────

function buildEngagementHTML(question, accent) {
  const isAr  = isArabic(question);
  const dir   = isAr ? "rtl" : "ltr";
  const bFont = isAr
    ? `"Noto Naskh Arabic","Amiri",serif`
    : `"Inter","Helvetica Neue",Arial,sans-serif`;

  return `<!DOCTYPE html>
<html lang="${isAr?"ar":"en"}">
<head>
  <meta charset="UTF-8"/>
  <link href="https://fonts.googleapis.com/css2?family=Noto+Naskh+Arabic:wght@700;800;900&family=Inter:wght@700;800;900&display=swap" rel="stylesheet"/>
  <style>
    *{margin:0;padding:0;box-sizing:border-box;}
    html,body{width:${WIDTH}px;height:${HEIGHT}px;overflow:hidden;background:transparent;}
    .eq-wrap{
      position:absolute;bottom:220px;left:0;right:0;
      display:flex;justify-content:center;padding:0 60px;
    }
    .eq-box{
      background:rgba(0,0,0,0.90);border:4px solid ${accent};
      border-radius:24px;padding:32px 60px;max-width:960px;direction:${dir};
      box-shadow: 0 0 50px ${accent}66;
    }
    .eq-text{
      font-family:${bFont};font-size:${isAr?"62px":"58px"};font-weight:900;
      color:#fff;text-align:center;line-height:1.3;
      text-shadow:0 4px 16px rgba(0,0,0,0.9);
    }
  </style>
</head>
<body>
  <div class="eq-wrap">
    <div class="eq-box">
      <div class="eq-text">${esc(question)}</div>
    </div>
  </div>
</body>
</html>`;
}

// ─────────────────────────────────────────────────────────────────────────────
// SENTENCE SCREEN (WITH ULTRA HOOK & SUB-FRAME ANIMATIONS)
// ─────────────────────────────────────────────────────────────────────────────

function chunkText(wordsArray, size) {
  let out = "";
  for (let i = 0; i < wordsArray.length; i++) {
    out += esc(wordsArray[i]) + " ";
    if ((i + 1) % size === 0 && i !== wordsArray.length - 1) out += "<br/>";
  }
  return out.trim();
}

function buildSentenceHTML(sentence, visibleCount, titleText, sentenceIdx,
                           totalSentences, posIdx, accent, styleIdx, wordFrameIdx, isHook) {
  sentence = (sentence||" ").trim() || " ";
  const words  = sentence.split(" ").filter(Boolean);
  const isAr   = isArabic(sentence);
  const isTAr  = isArabic(titleText);
  const dir    = isAr ? "rtl" : "ltr";
  const lang   = isAr ? "ar" : "en";
  const bFont  = isAr
    ? `"Noto Naskh Arabic","Amiri",serif`
    : `"Inter","Helvetica Neue",Arial,sans-serif`;
  const tFont  = isTAr
    ? `"Noto Naskh Arabic","Amiri",serif`
    : `"Inter","Helvetica Neue",Arial,sans-serif`;

  const vc     = Math.max(0, Math.min(visibleCount, words.length));
  const pct    = words.length > 0 ? ((vc / words.length)*100).toFixed(1) : "0";
  const isLast = sentenceIdx === totalSentences - 1;
  const isDone = words.length > 0 && vc >= words.length;
  const [e1, e2] = getEmojis(titleText);

  const pos = TEXT_POSITIONS[posIdx % TEXT_POSITIONS.length];
  let alignObj = pos.ai;
  let textAln  = pos.ta;
  if (isAr) {
    if (alignObj === "flex-start") alignObj = "flex-end";
    else if (alignObj === "flex-end") alignObj = "flex-start";
    if (textAln === "left") textAln = "right";
    else if (textAln === "right") textAln = "left";
  }

  const wf = wordFrameIdx || 0;
  const prog = Math.min(wf / 5.0, 1.0);
  let scale = 1.0;
  let shake = "";
  let flashOpacity = 0;
  let blur = 0;
  
  const curr = vc > 0 ? (words[vc-1]||"") : "";
  const currIsPower = isPowerWord(curr);

  if (currIsPower) {
    scale = 1.0 + Math.sin(prog * Math.PI) * 0.35;
    if (prog > 0 && prog < 1) {
      const shakes = [
        `translate(5px,-5px) rotate(3deg)`,
        `translate(-5px,5px) rotate(-3deg)`,
        `translate(3px,-3px) rotate(2deg)`,
        `translate(-3px,3px) rotate(-2deg)`,
      ];
      shake = shakes[wf % 4];
    }
    flashOpacity = Math.max(0, 0.30 * (1 - prog * 2));
    blur = Math.sin(prog * Math.PI) * 3;
  } else if (wf < 5 && vc > 0) {
    scale = 1.0 + Math.sin(prog * Math.PI) * 0.08;
    blur = Math.sin(prog * Math.PI) * 1.5;
  }

  const transformStr = `scale(${scale}) ${shake}`.trim();

  const prevText = chunkText(words.slice(0, Math.max(0, vc-1)), 4);
  const nextText = chunkText(words.slice(vc), 4);

  const STYLES    = ["word_pop","impact","neon_word","stacked","minimal_cap","karaoke"];
  const styleName = isHook ? "ultra_hook" : STYLES[styleIdx % STYLES.length];

  let mainCSS = "", mainHTML = "";
  const containerClass = `
      .wrap{position:absolute;inset:0;display:flex;flex-direction:column;
            justify-content:${pos.jc};align-items:${alignObj};text-align:${textAln};
            padding:${pos.pt} 56px ${pos.pb};direction:${dir};gap:6px;}`;

  if (styleName === "ultra_hook") {
    mainCSS = `
      .uh{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;
          justify-content:center;padding:0 40px;direction:${dir};}
      .uh-c{font-family:${bFont};font-size:${isAr?"170px":"160px"};font-weight:900;
            color:${accent};text-align:center;line-height:1.0;text-transform:uppercase;
            transform:${transformStr}; filter:blur(${blur}px); display:inline-block;
            -webkit-text-stroke: 4px #000; paint-order: stroke fill;
            text-shadow:0 0 80px ${accent}cc, 0 10px 50px rgba(0,0,0,1), 8px 8px 0px #000;
            word-break:break-word; z-index:10;}`;
    mainHTML = `<div class="uh">
      ${flashOpacity>0 ? `<div style="position:absolute;inset:0;background:#fff;opacity:${flashOpacity};z-index:99;"></div>` : ''}
      ${curr ? `<div class="uh-c">${esc(curr)}</div>` : ''}
    </div>`;
  }
  else if (styleName === "word_pop") {
    mainCSS = containerClass + `
      .wp-p{font-family:${bFont};font-size:${isAr?"48px":"44px"};font-weight:700;
            color:rgba(255,255,255,0.45);line-height:1.3;text-shadow:0 2px 12px rgba(0,0,0,0.95);
            max-width:960px;min-height:52px;}
      .wp-c{font-family:${bFont};font-size:${currIsPower?(isAr?"130px":"122px"):(isAr?"112px":"106px")};
            font-weight:900;color:${currIsPower?accent:"#fff"};line-height:1.0;
            letter-spacing:${isAr?"0.01em":"-0.03em"};display:inline-block;
            transform:${transformStr}; filter:blur(${blur}px);
            text-shadow:${currIsPower
              ?`0 0 60px ${accent}cc,0 0 100px ${accent}44,0 6px 32px rgba(0,0,0,1),5px 5px 0 rgba(0,0,0,0.85)`
              :`0 6px 32px rgba(0,0,0,1),5px 5px 0 rgba(0,0,0,0.85),-3px -3px 0 rgba(0,0,0,0.7)`};
            min-height:116px;z-index:5;}
      .wp-n{font-family:${bFont};font-size:${isAr?"40px":"36px"};font-weight:600;
            color:rgba(255,255,255,0.20);line-height:1.3;max-width:960px;}`;
    mainHTML = `<div class="wrap">
      ${flashOpacity>0 ? `<div style="position:absolute;inset:0;background:#fff;opacity:${flashOpacity};z-index:99;pointer-events:none;"></div>` : ''}
      <div class="wp-p">${prevText}&nbsp;</div>
      <div class="wp-c">${esc(curr)}</div>
      <div class="wp-n">&nbsp;${nextText}</div>
    </div>`;
  }
  else if (styleName === "impact") {
    mainCSS = containerClass + `
      .im-p{font-family:${bFont};font-size:${isAr?"40px":"36px"};font-weight:700;
            color:rgba(255,255,255,0.35);margin-bottom:10px;text-shadow:0 2px 8px rgba(0,0,0,0.9);}
      .im-c{font-family:${bFont};font-size:${currIsPower?(isAr?"134px":"126px"):(isAr?"124px":"116px")};
            font-weight:900;color:#fff;line-height:0.95;letter-spacing:${isAr?"0.01em":"-0.04em"};
            -webkit-text-stroke:${isAr?"5px":"6px"} rgba(0,0,0,0.95);paint-order:stroke fill;
            text-shadow:${currIsPower?`0 0 60px ${accent}cc,`:""}0 6px 30px rgba(0,0,0,0.95);
            display:inline-block; transform:${transformStr}; filter:blur(${blur}px); max-width:980px;}
      .im-n{font-family:${bFont};font-size:${isAr?"36px":"32px"};font-weight:600;
            color:rgba(255,255,255,0.20);margin-top:10px;}`;
    mainHTML = `<div class="wrap">
      ${flashOpacity>0 ? `<div style="position:absolute;inset:0;background:#fff;opacity:${flashOpacity};z-index:99;pointer-events:none;"></div>` : ''}
      <div class="im-p">${prevText}</div>
      <div class="im-c">${currIsPower?`<span style="color:${accent};-webkit-text-stroke:0">${esc(curr)}</span>`:esc(curr)}</div>
      <div class="im-n">${nextText}</div>
    </div>`;
  }
  else if (styleName === "neon_word") {
    mainCSS = containerClass + `
      .nw-p{font-family:${bFont};font-size:${isAr?"42px":"38px"};font-weight:700;
            color:rgba(255,255,255,0.30);line-height:1.3;max-width:960px;min-height:46px;}
      .nw-c{font-family:${bFont};font-size:${currIsPower?(isAr?"124px":"118px"):(isAr?"110px":"104px")};
            font-weight:900;color:#fff;line-height:1.0;letter-spacing:${isAr?"0.01em":"-0.03em"};
            text-shadow:0 0 25px ${accent},0 0 60px ${accent}aa,0 0 100px ${accent}55,0 4px 22px rgba(0,0,0,1);
            display:inline-block; transform:${transformStr}; filter:blur(${blur}px); min-height:114px;}
      .nw-n{font-family:${bFont};font-size:${isAr?"38px":"34px"};font-weight:600;
            color:rgba(255,255,255,0.15);line-height:1.3;max-width:960px;}`;
    mainHTML = `<div class="wrap">
      ${flashOpacity>0 ? `<div style="position:absolute;inset:0;background:#fff;opacity:${flashOpacity};z-index:99;pointer-events:none;"></div>` : ''}
      <div class="nw-p">${prevText}&nbsp;</div>
      <div class="nw-c">${esc(curr)}</div>
      <div class="nw-n">&nbsp;${nextText}</div>
    </div>`;
  }
  else if (styleName === "stacked") {
    mainCSS = containerClass + `
      .st-p{font-family:${bFont};font-size:${isAr?"46px":"42px"};font-weight:700;
            color:rgba(255,255,255,0.40);line-height:1.35;text-shadow:0 2px 10px rgba(0,0,0,0.95);
            min-height:48px;max-width:960px;}
      .st-c{font-family:${bFont};font-size:${currIsPower?(isAr?"118px":"112px"):(isAr?"104px":"98px")};
            font-weight:900;color:${currIsPower?accent:"#fff"};line-height:1.05;
            text-shadow:0 0 50px ${accent}bb,0 0 90px ${accent}55,0 4px 24px rgba(0,0,0,1),4px 4px 0 rgba(0,0,0,0.85);
            display:inline-block; transform:${transformStr}; filter:blur(${blur}px); min-height:108px;max-width:980px;}
      .st-n{font-family:${bFont};font-size:${isAr?"40px":"36px"};font-weight:600;
            color:rgba(255,255,255,0.20);line-height:1.35;min-height:44px;max-width:960px;}`;
    mainHTML = `<div class="wrap">
      ${flashOpacity>0 ? `<div style="position:absolute;inset:0;background:#fff;opacity:${flashOpacity};z-index:99;pointer-events:none;"></div>` : ''}
      <div class="st-p">${prevText}&nbsp;</div>
      <div class="st-c">${esc(curr)}</div>
      <div class="st-n">&nbsp;${nextText}</div>
    </div>`;
  }
  else if (styleName === "minimal_cap") {
    mainCSS = containerClass + `
      .mc-p{font-family:${bFont};font-size:${isAr?"40px":"36px"};font-weight:600;
            color:rgba(255,255,255,0.45);margin-bottom:10px;text-shadow:0 2px 8px rgba(0,0,0,0.9);}
      .mc-bar{background:rgba(0,0,0,0.85);padding:24px 56px;
              border-${isAr?"right":"left"}:8px solid ${accent};
              display:flex;align-items:baseline;flex-wrap:wrap;gap:0;
              ${isAr?"justify-content:flex-end;direction:rtl;":""}
              box-shadow:0 10px 30px rgba(0,0,0,0.5);}
      .mc-c{font-family:${bFont};font-size:${currIsPower?(isAr?"78px":"74px"):(isAr?"66px":"62px")};
            font-weight:900;color:${accent};line-height:1.2;
            display:inline-block; transform:${transformStr}; filter:blur(${blur}px);
            text-shadow:0 0 30px ${accent}66,0 2px 8px rgba(0,0,0,0.8);}
      .mc-r{font-family:${bFont};font-size:${isAr?"52px":"48px"};font-weight:700;
            color:rgba(255,255,255,0.35);line-height:1.2;
            ${isAr?"margin-right:10px":"margin-left:10px"};}`;
    mainHTML = `<div class="wrap" style="justify-content:flex-end; padding-bottom:180px;">
      ${flashOpacity>0 ? `<div style="position:absolute;inset:0;background:#fff;opacity:${flashOpacity};z-index:99;pointer-events:none;"></div>` : ''}
      ${prevText?`<div class="mc-p">${prevText.replace(/<br\/>/g," ")}</div>`:""}
      <div class="mc-bar">
        ${isAr
          ?`<span class="mc-r">${nextText.replace(/<br\/>/g," ")}</span><span class="mc-c"> ${esc(curr)}</span>`
          :`<span class="mc-c">${esc(curr)}</span><span class="mc-r"> ${nextText.replace(/<br\/>/g," ")}</span>`
        }
      </div>
    </div>`;
  }
  else {
    const kWords = words.map((w, i) => {
      const we      = esc(w);
      const isPower = isPowerWord(w);
      const isCurr  = i === Math.max(0, vc - 1);
      const isPrev  = i < Math.max(0, vc - 1);
      const powerStyle = isPower ? `font-size:${isAr?"76px":"72px"};` : "";
      if (isCurr)
        return `<span style="background:${accent};color:#000;padding:4px 18px;border-radius:12px;display:inline-block;transform:${transformStr};filter:blur(${blur}px);line-height:1.2;margin:0 4px;${powerStyle}box-shadow:0 0 20px ${accent}88;">${we}</span>`;
      if (isPrev)
        return `<span style="color:rgba(255,255,255,0.65);margin:0 4px;${powerStyle}">${we}</span>`;
      return `<span style="color:rgba(255,255,255,0.25);margin:0 4px;">${we}</span>`;
    }).join("");
    
    mainCSS  = containerClass + `
      .kk-t{font-family:${bFont};font-size:${isAr?"56px":"52px"};font-weight:800;
            line-height:1.6;text-shadow:0 2px 12px rgba(0,0,0,0.95);word-break:break-word;}`;
    mainHTML = `<div class="wrap" style="justify-content:center; align-items:center; text-align:center;">
      ${flashOpacity>0 ? `<div style="position:absolute;inset:0;background:#fff;opacity:${flashOpacity};z-index:99;pointer-events:none;"></div>` : ''}
      <div class="kk-t">${kWords}</div>
    </div>`;
  }

  const titleBarHTML = `
    <div style="position:absolute;top:60px;left:0;right:0;
                display:flex;justify-content:center;padding:0 50px;z-index:10;">
      <div style="display:inline-flex;align-items:center;gap:10px;
                  background:rgba(0,0,0,0.75);border-radius:40px;
                  padding:12px 26px;border:2px solid rgba(255,255,255,0.25);
                  max-width:880px;box-shadow:0 5px 20px rgba(0,0,0,0.5);">
        <span style="font-size:32px;line-height:1;">${e1}</span>
        <span style="font-family:${tFont};font-size:${isTAr?"28px":"26px"};font-weight:800;
                     color:rgba(255,255,255,0.95);line-height:1.2;
                     direction:${isTAr?"rtl":"ltr"};
                     text-shadow:0 2px 8px rgba(0,0,0,0.9);">${esc(titleText)}</span>
        <span style="font-size:32px;line-height:1;">${e2}</span>
      </div>
    </div>`;

  const endLabel = isTAr 
    ? "احفظ الفيديو 🔖<br><span style='font-size:36px;color:#333;font-weight:800;'>ستحتاجه لاحقاً</span>"
    : "SAVE THIS 🔖<br><span style='font-size:36px;color:#333;font-weight:800;'>You will need this later</span>";

  const engDiv = (isLast && isDone) ? `
    <div style="position:absolute;inset:0;display:flex;justify-content:center;
                align-items:center;pointer-events:none;z-index:20;background:rgba(0,0,0,0.4);">
      <div style="background:${accent};border-radius:40px;padding:36px 64px;
                  display:flex;flex-direction:column;align-items:center;text-align:center;
                  box-shadow:0 0 80px ${accent}cc,0 15px 50px rgba(0,0,0,0.8);
                  transform: scale(1.1); animation: pulse 2s infinite;">
        <span style="font-family:${tFont};font-size:64px;font-weight:900;
                     color:#000;line-height:1.2;">${endLabel}</span>
      </div>
    </div>` : "";

  const MAX_D  = 9;
  const half   = Math.floor(MAX_D/2);
  let dStart   = Math.max(0, sentenceIdx - half);
  dStart       = Math.min(dStart, Math.max(0, totalSentences - MAX_D));
  const dEnd   = Math.min(totalSentences, dStart + MAX_D);
  const dotsDiv = `
    <div style="position:absolute;bottom:68px;left:0;right:0;
                display:flex;justify-content:center;align-items:center;gap:10px;z-index:10;">
      ${Array.from({length: dEnd - dStart}, (_,k) => {
        const i = dStart + k;
        return i === sentenceIdx
          ? `<div style="width:26px;height:8px;border-radius:4px;background:${accent};box-shadow:0 0 10px ${accent};"></div>`
          : `<div style="width:8px;height:8px;border-radius:50%;background:rgba(255,255,255,0.25);"></div>`;
      }).join("")}
    </div>`;

  const progDiv = `
    <div style="position:absolute;bottom:44px;left:56px;right:56px;
                height:6px;background:rgba(255,255,255,0.15);border-radius:3px;overflow:hidden;z-index:10;">
      <div style="height:100%;width:${pct}%;
                  background:linear-gradient(90deg,${accent},${accent}88);border-radius:3px;
                  box-shadow:0 0 10px ${accent}aa;"></div>
    </div>`;

  const overlayDiv = `
    <div style="position:absolute;bottom:0;left:0;right:0;height:75%;
                background:linear-gradient(to top,
                  rgba(0,0,0,0.98) 0%,rgba(0,0,0,0.85) 20%,
                  rgba(0,0,0,0.50) 38%,rgba(0,0,0,0.20) 55%,transparent 75%
                );pointer-events:none;"></div>
    <div style="position:absolute;top:0;left:0;right:0;height:25%;
                background:linear-gradient(to bottom,
                  rgba(0,0,0,0.8) 0%, transparent 100%
                );pointer-events:none;"></div>`;

  return `<!DOCTYPE html>
<html lang="${lang}">
<head>
  <meta charset="UTF-8"/>
  <link href="https://fonts.googleapis.com/css2?family=Noto+Naskh+Arabic:wght@700;800;900&family=Amiri:wght@700&family=Inter:wght@700;800;900&display=swap" rel="stylesheet"/>
  <style>
    *{margin:0;padding:0;box-sizing:border-box;}
    html,body{width:${WIDTH}px;height:${HEIGHT}px;overflow:hidden;background:transparent;}
    ${mainCSS}
  </style>
</head>
<body>
  ${overlayDiv}
  ${titleBarHTML}
  ${mainHTML}
  ${dotsDiv}
  ${progDiv}
  ${engDiv}
</body>
</html>`;
}

// ─────────────────────────────────────────────────────────────────────────────
// FRAME STATE MAP (Now tracking sub-frames for animation)
// ─────────────────────────────────────────────────────────────────────────────

const SCREEN_TYPE = {
  SENTENCE:   "sentence",
  PATTERN:    "pattern",
  ENGAGEMENT: "engagement",
};

function buildFrameStateMap(timeline, nFrames, realDur) {
  const sentenceCount    = sentences.length;
  const ULTRA_HOOK_FRAMES= Math.round(3.0 * FPS);
  const PI_INTERVAL      = Math.round(6 * FPS);
  const EQ_INTERVAL      = Math.round(10 * FPS);

  const patternInterruptFrames = new Set();
  const engagementFrames       = new Set();

  for (let f = PI_INTERVAL; f < nFrames - FPS * 2; f += PI_INTERVAL + Math.round(Math.random() * FPS)) {
    patternInterruptFrames.add(f);
  }
  for (let f = EQ_INTERVAL; f < nFrames - FPS * 2; f += EQ_INTERVAL) {
    engagementFrames.add(f);
  }

  const map = new Array(nFrames).fill(null).map(() => ({
    screen_type:        SCREEN_TYPE.SENTENCE,
    sentence_idx:       0,
    visible_word_count: 0,
    word_frame_idx:     0,
    position_idx:       0,
    style_idx:          0,
    accent_idx:         0,
    pi_idx:             0,
    eq_idx:             0,
    is_hook:            false,
  }));

  let last_vc = -1;
  let frames_since_reveal = 0;

  if (timeline && timeline.length > 0) {
    const timelineMax = timeline[timeline.length - 1].time;
    const scale       = timelineMax > 0.1 ? (realDur / timelineMax) : 1.0;
    const scaled      = timeline.map(ev => ({ ...ev, time: ev.time * scale }));

    for (let f = 0; f < nFrames; f++) {
      const t = f / FPS;
      let lo = 0, hi = scaled.length - 1, best = null;
      while (lo <= hi) {
        const mid = (lo + hi) >> 1;
        if (scaled[mid].time <= t + 0.001) { best = scaled[mid]; lo = mid + 1; }
        else hi = mid - 1;
      }
      if (best) {
        if (best.visible_word_count !== last_vc) {
          frames_since_reveal = 0;
          last_vc = best.visible_word_count;
        } else {
          frames_since_reveal++;
        }

        map[f] = {
          ...map[f],
          screen_type:        SCREEN_TYPE.SENTENCE,
          sentence_idx:       best.sentence_idx,
          visible_word_count: best.visible_word_count,
          word_frame_idx:     Math.min(frames_since_reveal, 5),
          position_idx:       best.sentence_idx,
          style_idx:          best.sentence_idx,
          accent_idx:         best.sentence_idx,
          is_hook:            f < ULTRA_HOOK_FRAMES,
        };
      }
    }
  } else {
    const cd = realDur / sentenceCount;
    for (let f = 0; f < nFrames; f++) {
      const t  = f / FPS;
      const si = Math.min(Math.floor(t / cd), sentenceCount - 1);
      const ws = (sentences[si]||"").split(" ");
      const lt = t - si * cd;
      const wi = Math.min(Math.floor((lt / cd) * ws.length), ws.length - 1);
      const vc = wi + 1;

      if (vc !== last_vc) {
        frames_since_reveal = 0;
        last_vc = vc;
      } else {
        frames_since_reveal++;
      }

      map[f] = {
        ...map[f],
        screen_type:        SCREEN_TYPE.SENTENCE,
        sentence_idx:       si,
        visible_word_count: vc,
        word_frame_idx:     Math.min(frames_since_reveal, 5),
        position_idx:       si,
        style_idx:          si,
        accent_idx:         si,
        is_hook:            f < ULTRA_HOOK_FRAMES,
      };
    }
  }

  let piIdx = 0;
  for (const pf of patternInterruptFrames) {
    for (let f = pf; f < Math.min(pf + 20, nFrames); f++) {
      if (map[f].screen_type === SCREEN_TYPE.SENTENCE)
        map[f] = { ...map[f], screen_type: SCREEN_TYPE.PATTERN, pi_idx: piIdx };
    }
    piIdx++;
  }

  let eqIdx = 0;
  for (const ef of engagementFrames) {
    for (let f = ef; f < Math.min(ef + 25, nFrames); f++) {
      if (map[f].screen_type === SCREEN_TYPE.SENTENCE)
        map[f] = { ...map[f], screen_type: SCREEN_TYPE.ENGAGEMENT, eq_idx: eqIdx };
    }
    eqIdx++;
  }

  return map;
}

// ─────────────────────────────────────────────────────────────────────────────
// RENDER UNIQUE PNG STATES
// ─────────────────────────────────────────────────────────────────────────────

async function renderAllPNGs(page, frameStateMap) {
  const uniqueStates = new Map();
  for (const state of frameStateMap) {
    let key;
    if      (state.screen_type === SCREEN_TYPE.PATTERN)    key = `pi_${state.pi_idx}`;
    else if (state.screen_type === SCREEN_TYPE.ENGAGEMENT) key = `eq_${state.eq_idx}`;
    else {
      key = `s_${state.sentence_idx}_${state.visible_word_count}_${state.word_frame_idx}`;
    }
    if (!uniqueStates.has(key)) uniqueStates.set(key, state);
  }
  console.log(`  📸 ${uniqueStates.size} unique states (with sub-frame motion)`);

  const initHtml = buildSentenceHTML(
    sentences[0]||" ", 0, title, 0, sentences.length, 0, ACCENT_COLORS[0], 0, 0, false
  );
  writeFileSync(`${TMP}/init.html`, initHtml, "utf-8");
  await page.goto(`file://${TMP}/init.html`, { waitUntil: "networkidle" });
  await page.waitForTimeout(2000);
  console.log("  ✅ Fonts loaded");

  const isAr       = isArabic(title) || isArabic(sentences[0]||"");
  const INTERRUPTS = isAr ? PATTERN_INTERRUPTS_AR : PATTERN_INTERRUPTS_EN;
  const QUESTIONS  = isAr ? ENGAGEMENT_QUESTIONS_AR : ENGAGEMENT_QUESTIONS_EN;
  const pngCache   = new Map();
  let rendered     = 0;

  for (const [key, state] of uniqueStates) {
    let html;
    if (state.screen_type === SCREEN_TYPE.PATTERN) {
      html = buildPatternInterruptHTML(
        INTERRUPTS[state.pi_idx % INTERRUPTS.length],
        ACCENT_COLORS[(state.pi_idx + 2) % ACCENT_COLORS.length]
      );
    } else if (state.screen_type === SCREEN_TYPE.ENGAGEMENT) {
      html = buildEngagementHTML(
        QUESTIONS[state.eq_idx % QUESTIONS.length],
        ACCENT_COLORS[(state.eq_idx + 1) % ACCENT_COLORS.length]
      );
    } else {
      html = buildSentenceHTML(
        sentences[state.sentence_idx]||" ",
        state.visible_word_count,
        title,
        state.sentence_idx,
        sentences.length,
        state.position_idx,
        ACCENT_COLORS[state.accent_idx % ACCENT_COLORS.length],
        state.style_idx,
        state.word_frame_idx,
        state.is_hook
      );
    }

    const htmlPath = `${TMP}/${key}.html`;
    writeFileSync(htmlPath, html, "utf-8");
    await page.goto(`file://${htmlPath}`, { waitUntil: "load" });
    await page.waitForTimeout(20);

    const pngPath = `${TMP}/${key}.png`;
    await page.screenshot({ path: pngPath, type: "png", omitBackground: true });
    pngCache.set(key, pngPath);
    rendered++;
    if (rendered % 50 === 0 || rendered === uniqueStates.size)
      process.stdout.write(`    ${rendered}/${uniqueStates.size} PNGs\n`);
  }
  return pngCache;
}

// ─────────────────────────────────────────────────────────────────────────────
// BUILD FRAME DIR
// ─────────────────────────────────────────────────────────────────────────────

function buildFrameDir(clipFrameMap, pngCache, idx) {
  const dir = `${TMP}/frames_${idx}`;
  mkdirSync(dir, { recursive: true });
  for (let f = 0; f < clipFrameMap.length; f++) {
    const state = clipFrameMap[f];
    let key;
    if      (state.screen_type === SCREEN_TYPE.PATTERN)    key = `pi_${state.pi_idx}`;
    else if (state.screen_type === SCREEN_TYPE.ENGAGEMENT) key = `eq_${state.eq_idx}`;
    else key = `s_${state.sentence_idx}_${state.visible_word_count}_${state.word_frame_idx}`;
    const src  = pngCache.get(key);
    const dest = `${dir}/frame_${String(f).padStart(6,"0")}.png`;
    if (!src) continue;
    try { symlinkSync(src, dest); } catch { copyFileSync(src, dest); }
  }
  return dir;
}

// ─────────────────────────────────────────────────────────────────────────────
// PROCESS BACKGROUND
// ─────────────────────────────────────────────────────────────────────────────

function processBackground(videoPath, duration, outPath, idx) {
  const n = Math.ceil(duration * FPS);
  const ZOOM_PATTERNS = [
    `zoompan=z='min(max(zoom\\,1.12)+0.0005\\,1.20)':x='iw/2-(iw/zoom/2)+on*0.3':y='ih/2-(ih/zoom/2)':d=${n}:s=${WIDTH}x${HEIGHT}:fps=${FPS}`,
    `zoompan=z='if(eq(on\\,1)\\,1.20\\,max(zoom-0.0005\\,1.12))':x='iw/2-(iw/zoom/2)-on*0.3':y='ih/2-(ih/zoom/2)':d=${n}:s=${WIDTH}x${HEIGHT}:fps=${FPS}`,
    `zoompan=z='min(max(zoom\\,1.12)+0.0004\\,1.18)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)+on*0.3':d=${n}:s=${WIDTH}x${HEIGHT}:fps=${FPS}`,
    `zoompan=z='1.15':x='iw/2-(iw/zoom/2)+on*0.2':y='ih/2-(ih/zoom/2)-on*0.2':d=${n}:s=${WIDTH}x${HEIGHT}:fps=${FPS}`,
  ];
  const kb    = ZOOM_PATTERNS[idx % ZOOM_PATTERNS.length];
  const color = `curves=r='0/0 0.5/0.46 1/0.88':g='0/0 0.5/0.50 1/0.97':b='0/0.04 0.5/0.56 1/1.0',hue=s=0.82,vignette=PI/5`;
  const fade  = `fade=t=in:st=0:d=0.28,fade=t=out:st=${(duration-0.28).toFixed(3)}:d=0.28`;
  const full  = `scale=${Math.round(WIDTH*1.1)}:${Math.round(HEIGHT*1.1)}:force_original_aspect_ratio=increase,`
              + `crop=${Math.round(WIDTH*1.1)}:${Math.round(HEIGHT*1.1)},${kb},${color},${fade}`;

  let r = spawnSync("ffmpeg",[
    "-y","-i",videoPath,"-t",duration.toFixed(3),
    "-vf",full,"-r",String(FPS),
    "-c:v","libx264","-preset","fast","-crf","22","-pix_fmt","yuv420p","-an",outPath,
  ],{stdio:["ignore","pipe","pipe"]});

  if (r.status !== 0) {
    const simple = `scale=${WIDTH}:${HEIGHT}:force_original_aspect_ratio=increase,`
                 + `crop=${WIDTH}:${HEIGHT},setsar=1,${color},${fade}`;
    r = spawnSync("ffmpeg",[
      "-y","-i",videoPath,"-t",duration.toFixed(3),
      "-vf",simple,"-r",String(FPS),
      "-c:v","libx264","-preset","fast","-crf","22","-pix_fmt","yuv420p","-an",outPath,
    ],{stdio:["ignore","pipe","pipe"]});
    if (r.status !== 0) { console.error("❌ BG failed"); process.exit(1); }
  }
  return outPath;
}

// ─────────────────────────────────────────────────────────────────────────────
// FFMPEG HELPERS
// ─────────────────────────────────────────────────────────────────────────────

function framesToMov(frameDir, outPath) {
  const r = spawnSync("ffmpeg",[
    "-y","-framerate",String(FPS),
    "-i",`${frameDir}/frame_%06d.png`,
    "-vf",`scale=${WIDTH}:${HEIGHT},format=rgba`,
    "-c:v","png","-an",outPath,
  ],{stdio:["ignore","pipe","pipe"]});
  if (r.status !== 0) { console.error("❌ frames→mov:\n"+r.stderr.toString().slice(-400)); process.exit(1); }
  return outPath;
}

function overlayOnBackground(bgMp4, captionMov, outPath) {
  const r = spawnSync("ffmpeg",[
    "-y","-i",bgMp4,"-i",captionMov,
    "-filter_complex","[1:v]format=rgba[cap];[0:v][cap]overlay=0:0:format=auto,format=yuv420p[out]",
    "-map","[out]","-c:v","libx264","-preset","fast","-crf","20","-pix_fmt","yuv420p","-an",outPath,
  ],{stdio:["ignore","pipe","pipe"]});
  if (r.status !== 0) { console.error("❌ Overlay:\n"+r.stderr.toString().slice(-500)); process.exit(1); }
  return outPath;
}

function xfadeConcat(clipPaths, clipDurations) {
  if (clipPaths.length === 1) return clipPaths[0];
  const XFADE = 0.30;
  const filters = [];
  let offset = 0, last = "[0:v]";
  for (let i = 1; i < clipPaths.length; i++) {
    offset += clipDurations[i-1] - XFADE;
    const out = i === clipPaths.length-1 ? "[vout]" : `[v${i}]`;
    filters.push(`${last}[${i}:v]xfade=transition=${getTransition(i-1)}:duration=${XFADE}:offset=${offset.toFixed(3)}${out}`);
    last = out;
  }
  const outPath = `${TMP}/xfaded.mp4`;
  const r = spawnSync("ffmpeg",[
    "-y",...clipPaths.flatMap(p=>["-i",p]),
    "-filter_complex",filters.join(";"),
    "-map","[vout]","-c:v","libx264","-preset","fast","-crf","20","-pix_fmt","yuv420p","-an",outPath,
  ],{stdio:["ignore","pipe","pipe"]});
  if (r.status !== 0) {
    const lst = `${TMP}/list.txt`;
    writeFileSync(lst, clipPaths.map(p=>`file '${p}'`).join("\n"));
    const raw = `${TMP}/raw.mp4`;
    spawnSync("ffmpeg",["-y","-f","concat","-safe","0","-i",lst,"-c","copy",raw],{stdio:"inherit"});
    return raw;
  }
  return outPath;
}

function mergeAudio(videoPath, audioPath, outPath) {
  const aDur = probeDuration(audioPath);
  const vDur = probeDuration(videoPath);
  console.log(`🎵 Audio: ${aDur.toFixed(3)}s | 🎬 Video: ${vDur.toFixed(3)}s`);
  let finalVideo = videoPath;
  if (vDur < aDur - 0.3) {
    const ext = `${TMP}/video_ext.mp4`;
    const r   = spawnSync("ffmpeg",[
      "-y","-i",videoPath,
      "-vf",`tpad=stop_mode=clone:stop_duration=${(aDur-vDur+0.5).toFixed(3)}`,
      "-c:v","libx264","-preset","fast","-crf","22","-pix_fmt","yuv420p","-an",ext,
    ],{stdio:["ignore","pipe","pipe"]});
    if (r.status === 0) finalVideo = ext;
    else {
      const lp = `${TMP}/video_loop.mp4`;
      spawnSync("ffmpeg",["-y","-stream_loop","-1","-i",videoPath,"-t",aDur.toFixed(3),"-c","copy",lp],{stdio:["ignore","pipe","pipe"]});
      if (existsSync(lp)) finalVideo = lp;
    }
  }
  const r = spawnSync("ffmpeg",[
    "-y","-i",finalVideo,"-i",audioPath,
    "-map","0:v:0","-map","1:a:0",
    "-c:v","copy","-c:a","aac","-b:a","192k",
    "-t",aDur.toFixed(3),outPath,
  ],{stdio:["ignore","pipe","pipe"]});
  if (r.status !== 0) { console.error("❌ Merge:\n"+r.stderr.toString().slice(-400)); process.exit(1); }
  console.log(`✅ Final: ${aDur.toFixed(3)}s → ${outPath}`);
}

// ─────────────────────────────────────────────────────────────────────────────
// MAIN — ✨ FIX: تغليف الكود في async main() مع error handling صحيح
// ─────────────────────────────────────────────────────────────────────────────

async function main() {
  console.log("\n🚀 Starting Ultimate Retention Renderer (Ultra Hook + Sub-Frame Motion)...\n");

  const frameStateMap = buildFrameStateMap(word_timeline, totalFrames, effectiveDuration);

  const browser = await chromium.launch({
    headless: true,
    args: ["--no-sandbox","--disable-setuid-sandbox","--disable-dev-shm-usage",
           "--disable-gpu","--no-zygote","--font-render-hinting=none","--lang=ar,en"],
  });
  const context = await browser.newContext({
    viewport: { width: WIDTH, height: HEIGHT },
    deviceScaleFactor: 1,
    locale: "ar-SA",
  });
  const page = await context.newPage();

  console.log("🖼️  Rendering dynamic states...");
  const pngCache = await renderAllPNGs(page, frameStateMap);
  await browser.close();
  console.log(`✅ ${pngCache.size} PNGs done\n`);

  const sentenceData = (aligned && aligned.length > 0)
    ? aligned
    : sentences.map((s,i) => ({
        sentence: s,
        start:    (effectiveDuration / sentences.length) * i,
        end:      (effectiveDuration / sentences.length) * (i + 1),
      }));

  const finalClips = [], clipDurations = [];

  console.log("🎬 Processing clips...");
  for (let i = 0; i < sentences.length; i++) {
    const info      = sentenceData[i] || {};
    const clipStart = info.start ?? (effectiveDuration / sentences.length) * i;
    const clipEnd   = info.end   ?? (effectiveDuration / sentences.length) * (i + 1);
    const clipDur   = Math.max(clipEnd - clipStart, 0.5);
    const nFrames   = Math.ceil(clipDur * FPS);
    const startF    = Math.floor(clipStart * FPS);
    const clipMap   = frameStateMap.slice(startF, startF + nFrames);

    process.stdout.write(`  [${i+1}/${sentences.length}] ${clipDur.toFixed(2)}s "${(sentences[i]||"").slice(0,30)}"... `);

    const frameDir   = buildFrameDir(clipMap, pngCache, i);
    const captionMov = `${TMP}/caption_${i}.mov`;
    framesToMov(frameDir, captionMov);

    const videoSrc = videos[i] || videos[videos.length - 1];
    const bgMp4    = `${TMP}/bg_${String(i).padStart(3,"0")}.mp4`;
    processBackground(videoSrc, clipDur, bgMp4, i);

    const finalClip = `${TMP}/final_${String(i).padStart(3,"0")}.mp4`;
    overlayOnBackground(bgMp4, captionMov, finalClip);
    finalClips.push(finalClip);
    clipDurations.push(clipDur);
    process.stdout.write("✓\n");
  }

  const transNames = finalClips.slice(0,-1).map((_,i) => getTransition(i));
  console.log(`\n✨ Transitions: ${transNames.join(" → ")}`);
  const dissolved = xfadeConcat(finalClips, clipDurations);

  console.log("🎵 Merging voiceover & SFX prep...");
  mergeAudio(dissolved, audio, outputPath);
  console.log(`\n🎉 Final video → ${outputPath}`);
}

// ✨ FIX: error handling عند مستوى الـ process
main().catch((err) => {
  console.error("\n❌ Fatal error in render.mjs:");
  console.error(err);
  process.exit(1);
});
