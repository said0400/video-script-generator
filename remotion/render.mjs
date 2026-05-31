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
  "سر","أسرار","نجاح","خطأ","خطر","مال","قوة","مستقبل","ذكاء","عادة","فرصة",
  "حقيقة","كارثة","مذهل","خطير","صدمة","مهم","حرام","تحذير","انتبه","ثروة",
  "فشل","تغيير","معجزة","سحر","إدمان","علاج","دموع","ألم","فرح","حب",
];
const POWER_WORDS_EN = [
  "secret","success","mistake","danger","money","power","future","intelligence",
  "habit","opportunity","truth","disaster","amazing","critical","warning",
  "wealth","failure","change","miracle","addiction","cure","important","urgent",
];

function isPowerWord(word) {
  const w = word.replace(/[^\u0600-\u06FFa-zA-Z]/g, "").toLowerCase();
  return POWER_WORDS_AR.some(p => w.includes(p)) ||
         POWER_WORDS_EN.some(p => w === p || w.includes(p));
}

// ─────────────────────────────────────────────────────────────────────────────
// VISUAL CONFIG
// ─────────────────────────────────────────────────────────────────────────────

const ACCENT_COLORS = ["#FFE600","#FF3366","#00F5FF","#FF6B35","#7FFF00","#FF1493"];
const TRANSITIONS   = ["fade","slideleft","slideright","slideup","smoothleft",
                       "smoothright","circleopen","radial","pixelize","dissolve"];

const getTransition = i => TRANSITIONS[i % TRANSITIONS.length];

// المرحلة 3: مواضع النص المتناوبة
const TEXT_POSITIONS = [
  { name: "bottom", top: null,      bottom: "120px" },
  { name: "mid",    top: "680px",   bottom: null    },
  { name: "top",    top: "200px",   bottom: null    },
  { name: "mid",    top: "680px",   bottom: null    },
];

// المرحلة 5: Pattern Interrupts
const PATTERN_INTERRUPTS_AR = [
  "⚡ انتبه لهذا",
  "🧠 معلومة مهمة",
  "🚨 لا تتجاهل هذا",
  "🔥 أكمل للنهاية",
  "⏳ ما سيأتي أهم",
];
const PATTERN_INTERRUPTS_EN = [
  "⚡ Pay attention",
  "🧠 Important info",
  "🚨 Don't ignore this",
  "🔥 Keep watching",
  "⏳ Best part coming",
];

// المرحلة 9: أسئلة التفاعل
const ENGAGEMENT_QUESTIONS_AR = [
  "هل توافق؟ 👇",
  "هل حدث معك هذا؟",
  "اكتب نعم إذا فهمت ✅",
  "أخبرني برأيك 💬",
  "انتظر النهاية 🔥",
];
const ENGAGEMENT_QUESTIONS_EN = [
  "Do you agree? 👇",
  "Has this happened to you?",
  "Comment YES if you get it ✅",
  "Tell me your thoughts 💬",
  "Wait for the end 🔥",
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
// المرحلة 1: HOOK SCREEN
// ─────────────────────────────────────────────────────────────────────────────

function buildHookHTML(hookText, accent) {
  const isAr  = isArabic(hookText);
  const dir   = isAr ? "rtl" : "ltr";
  const lang  = isAr ? "ar" : "en";
  const bFont = isAr
    ? `"Noto Naskh Arabic","Amiri",serif`
    : `"Inter","Helvetica Neue",Arial,sans-serif`;

  const words      = hookText.trim().split(/\s+/);
  const splitAt    = Math.min(2, Math.floor(words.length / 2));
  const highlighted = words.slice(0, splitAt).join(" ");
  const rest        = words.slice(splitAt).join(" ");

  return `<!DOCTYPE html>
<html lang="${lang}">
<head>
  <meta charset="UTF-8"/>
  <link href="https://fonts.googleapis.com/css2?family=Noto+Naskh+Arabic:wght@700;800&family=Amiri:wght@700&family=Inter:wght@700;800;900&display=swap" rel="stylesheet"/>
  <style>
    *{margin:0;padding:0;box-sizing:border-box;}
    html,body{width:${WIDTH}px;height:${HEIGHT}px;overflow:hidden;background:transparent;}
    .hook-wrap{
      position:absolute;inset:0;
      display:flex;flex-direction:column;
      justify-content:center;align-items:center;
      padding:0 60px;direction:${dir};gap:20px;
    }
    .hook-text{
      font-family:${bFont};font-size:${isAr?"110px":"102px"};font-weight:900;
      text-align:center;line-height:1.1;max-width:960px;word-break:break-word;
    }
    .hook-hi{
      color:${accent};
      text-shadow:0 0 60px ${accent}cc,0 0 120px ${accent}44,
                  0 6px 30px rgba(0,0,0,1),4px 4px 0 rgba(0,0,0,0.9);
    }
    .hook-rest{
      color:#fff;
      text-shadow:0 6px 30px rgba(0,0,0,1),4px 4px 0 rgba(0,0,0,0.9);
    }
    .hook-bar{
      width:120px;height:6px;border-radius:3px;
      background:${accent};box-shadow:0 0 20px ${accent};
    }
    .overlay-t{position:absolute;top:0;left:0;right:0;height:35%;
      background:linear-gradient(to bottom,rgba(0,0,0,0.7),transparent);}
    .overlay-b{position:absolute;bottom:0;left:0;right:0;height:35%;
      background:linear-gradient(to top,rgba(0,0,0,0.7),transparent);}
    .flash{position:absolute;inset:0;background:${accent};opacity:0.22;
      mix-blend-mode:overlay;pointer-events:none;}
  </style>
</head>
<body>
  <div class="overlay-t"></div>
  <div class="overlay-b"></div>
  <div class="flash"></div>
  <div class="hook-wrap">
    <div class="hook-text">
      ${isAr
        ? `<span class="hook-rest">${esc(rest)} </span><span class="hook-hi">${esc(highlighted)}</span>`
        : `<span class="hook-hi">${esc(highlighted)} </span><span class="hook-rest">${esc(rest)}</span>`
      }
    </div>
    <div class="hook-bar"></div>
  </div>
</body>
</html>`;
}

// ─────────────────────────────────────────────────────────────────────────────
// المرحلة 5: PATTERN INTERRUPT SCREEN
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
  <link href="https://fonts.googleapis.com/css2?family=Noto+Naskh+Arabic:wght@700;800&family=Inter:wght@700;800;900&display=swap" rel="stylesheet"/>
  <style>
    *{margin:0;padding:0;box-sizing:border-box;}
    html,body{width:${WIDTH}px;height:${HEIGHT}px;overflow:hidden;background:transparent;}
    .pi-wrap{
      position:absolute;inset:0;
      display:flex;justify-content:center;align-items:center;
    }
    .pi-box{
      background:${accent};border-radius:24px;
      padding:36px 64px;max-width:900px;
      display:flex;align-items:center;gap:20px;direction:${dir};
      box-shadow:0 0 80px ${accent}88,0 20px 60px rgba(0,0,0,0.8);
    }
    .pi-text{
      font-family:${bFont};font-size:${isAr?"72px":"68px"};font-weight:900;
      color:#000;text-align:center;line-height:1.15;word-break:break-word;
    }
    .overlay{position:absolute;inset:0;background:rgba(0,0,0,0.45);pointer-events:none;}
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
// المرحلة 9: ENGAGEMENT QUESTION SCREEN
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
  <link href="https://fonts.googleapis.com/css2?family=Noto+Naskh+Arabic:wght@700;800&family=Inter:wght@700;800;900&display=swap" rel="stylesheet"/>
  <style>
    *{margin:0;padding:0;box-sizing:border-box;}
    html,body{width:${WIDTH}px;height:${HEIGHT}px;overflow:hidden;background:transparent;}
    .eq-wrap{
      position:absolute;bottom:180px;left:0;right:0;
      display:flex;justify-content:center;padding:0 60px;
    }
    .eq-box{
      background:rgba(0,0,0,0.80);border:3px solid ${accent};
      border-radius:20px;padding:28px 52px;max-width:920px;direction:${dir};
    }
    .eq-text{
      font-family:${bFont};font-size:${isAr?"58px":"54px"};font-weight:800;
      color:#fff;text-align:center;line-height:1.3;
      text-shadow:0 2px 12px rgba(0,0,0,0.9);
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
// المرحلتان 2+3+4: SENTENCE SCREEN
// ─────────────────────────────────────────────────────────────────────────────

function buildSentenceHTML(sentence, visibleCount, titleText, sentenceIdx,
                           totalSentences, posIdx, accent, styleIdx) {
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

  const pos    = TEXT_POSITIONS[posIdx % TEXT_POSITIONS.length];
  const topVal = pos.top || "auto";
  const botVal = pos.bottom || "auto";

  const STYLES    = ["word_pop","impact","neon_word","stacked","minimal_cap","karaoke"];
  const styleName = STYLES[styleIdx % STYLES.length];

  const prev = esc(words.slice(0, vc-1).join(" "));
  const curr = vc > 0 ? (words[vc-1]||"") : "";
  const next = esc(words.slice(vc).join(" "));
  const currIsPower = isPowerWord(curr);

  function buildWordSpans(wordList, visibleN) {
    return wordList.map((w, i) => {
      const we      = esc(w);
      const isPower = isPowerWord(w);
      const isCurr  = i === visibleN - 1;
      const isPrev  = i < visibleN - 1;
      const powerStyle = isPower ? `font-size:${isAr?"70px":"66px"};` : "";
      if (isCurr)
        return `<span style="background:${accent};color:#000;padding:3px 14px;border-radius:10px;display:inline-block;line-height:1.2;margin:0 3px;${powerStyle}">${we}</span>`;
      if (isPrev)
        return `<span style="color:rgba(255,255,255,0.52);margin:0 3px;${powerStyle}">${we}</span>`;
      return `<span style="color:rgba(255,255,255,0.16);margin:0 3px;">${we}</span>`;
    }).join("");
  }

  let mainCSS = "", mainHTML = "";

  if (styleName === "word_pop") {
    mainCSS = `
      .wp{position:absolute;top:${topVal};bottom:${botVal};left:0;right:0;
          display:flex;flex-direction:column;align-items:center;padding:0 56px;
          direction:${dir};gap:6px;justify-content:center;}
      .wp-p{font-family:${bFont};font-size:${isAr?"44px":"40px"};font-weight:700;
            color:rgba(255,255,255,0.35);text-align:center;line-height:1.3;
            text-shadow:0 2px 12px rgba(0,0,0,0.95);max-width:960px;
            min-height:52px;word-break:break-word;}
      .wp-c{font-family:${bFont};
            font-size:${currIsPower?(isAr?"130px":"122px"):(isAr?"112px":"106px")};
            font-weight:900;color:${currIsPower?accent:"#fff"};text-align:center;
            line-height:1.0;letter-spacing:${isAr?"0.01em":"-0.03em"};
            text-shadow:${currIsPower
              ?`0 0 60px ${accent}cc,0 0 100px ${accent}44,0 6px 32px rgba(0,0,0,1),5px 5px 0 rgba(0,0,0,0.85)`
              :`0 6px 32px rgba(0,0,0,1),5px 5px 0 rgba(0,0,0,0.85),-3px -3px 0 rgba(0,0,0,0.7)`};
            min-height:116px;word-break:break-word;}
      .wp-n{font-family:${bFont};font-size:${isAr?"36px":"32px"};font-weight:600;
            color:rgba(255,255,255,0.16);text-align:center;line-height:1.3;
            max-width:960px;word-break:break-word;}`;
    mainHTML = `<div class="wp">
      <div class="wp-p">${prev}&nbsp;</div>
      <div class="wp-c">${esc(curr)}</div>
      <div class="wp-n">&nbsp;${next}</div>
    </div>`;
  }
  else if (styleName === "impact") {
    mainCSS = `
      .im{position:absolute;top:${topVal};bottom:${botVal};left:0;right:0;
          display:flex;flex-direction:column;align-items:center;padding:0 36px;
          justify-content:center;}
      .im-p{font-family:${bFont};font-size:${isAr?"34px":"30px"};font-weight:700;
            color:rgba(255,255,255,0.28);text-align:center;margin-bottom:10px;
            text-shadow:0 2px 8px rgba(0,0,0,0.9);}
      .im-c{font-family:${bFont};
            font-size:${currIsPower?(isAr?"134px":"126px"):(isAr?"124px":"116px")};
            font-weight:900;color:#fff;text-align:center;line-height:0.95;
            letter-spacing:${isAr?"0.01em":"-0.04em"};
            -webkit-text-stroke:${isAr?"5px":"6px"} rgba(0,0,0,0.95);
            paint-order:stroke fill;
            text-shadow:${currIsPower?`0 0 50px ${accent}aa,`:""}0 6px 30px rgba(0,0,0,0.95);
            word-break:break-word;max-width:980px;}
      .im-n{font-family:${bFont};font-size:${isAr?"32px":"28px"};font-weight:600;
            color:rgba(255,255,255,0.14);text-align:center;margin-top:10px;}`;
    mainHTML = `<div class="im">
      <div class="im-p">${prev}</div>
      <div class="im-c">${currIsPower?`<span style="color:${accent};-webkit-text-stroke:0">${esc(curr)}</span>`:esc(curr)}</div>
      <div class="im-n">${next}</div>
    </div>`;
  }
  else if (styleName === "neon_word") {
    mainCSS = `
      .nw{position:absolute;top:${topVal};bottom:${botVal};left:0;right:0;
          display:flex;flex-direction:column;align-items:center;padding:0 56px;
          direction:${dir};gap:8px;justify-content:center;}
      .nw-p{font-family:${bFont};font-size:${isAr?"38px":"34px"};font-weight:700;
            color:rgba(255,255,255,0.24);text-align:center;line-height:1.3;
            max-width:960px;min-height:46px;word-break:break-word;}
      .nw-c{font-family:${bFont};
            font-size:${currIsPower?(isAr?"124px":"118px"):(isAr?"110px":"104px")};
            font-weight:900;color:#fff;text-align:center;line-height:1.0;
            letter-spacing:${isAr?"0.01em":"-0.03em"};
            text-shadow:0 0 25px ${accent},0 0 50px ${accent}aa,
                        0 0 90px ${accent}44,0 4px 22px rgba(0,0,0,1),
                        4px 4px 0 rgba(0,0,0,0.9);
            min-height:114px;word-break:break-word;}
      .nw-n{font-family:${bFont};font-size:${isAr?"34px":"30px"};font-weight:600;
            color:rgba(255,255,255,0.10);text-align:center;line-height:1.3;
            max-width:960px;word-break:break-word;}`;
    mainHTML = `<div class="nw">
      <div class="nw-p">${prev}&nbsp;</div>
      <div class="nw-c">${esc(curr)}</div>
      <div class="nw-n">&nbsp;${next}</div>
    </div>`;
  }
  else if (styleName === "stacked") {
    mainCSS = `
      .st{position:absolute;top:${topVal};bottom:${botVal};left:0;right:0;
          display:flex;flex-direction:column;align-items:center;padding:0 52px;
          direction:${dir};gap:4px;justify-content:center;}
      .st-p{font-family:${bFont};font-size:${isAr?"40px":"36px"};font-weight:700;
            color:rgba(255,255,255,0.30);text-align:center;line-height:1.35;
            text-shadow:0 2px 10px rgba(0,0,0,0.95);min-height:48px;
            max-width:960px;word-break:break-word;}
      .st-c{font-family:${bFont};
            font-size:${currIsPower?(isAr?"118px":"112px"):(isAr?"104px":"98px")};
            font-weight:900;color:${currIsPower?accent:"#fff"};text-align:center;
            line-height:1.05;
            text-shadow:0 0 40px ${accent}bb,0 0 80px ${accent}44,
                        0 4px 24px rgba(0,0,0,1),4px 4px 0 rgba(0,0,0,0.85);
            min-height:108px;max-width:980px;word-break:break-word;}
      .st-n{font-family:${bFont};font-size:${isAr?"36px":"32px"};font-weight:600;
            color:rgba(255,255,255,0.14);text-align:center;line-height:1.35;
            min-height:44px;max-width:960px;word-break:break-word;}`;
    mainHTML = `<div class="st">
      <div class="st-p">${prev}&nbsp;</div>
      <div class="st-c">${esc(curr)}</div>
      <div class="st-n">&nbsp;${next}</div>
    </div>`;
  }
  else if (styleName === "minimal_cap") {
    mainCSS = `
      .mc{position:absolute;top:${topVal};bottom:${botVal==="auto"?"120px":botVal};
          left:0;right:0;direction:${dir};}
      .mc-p{font-family:${bFont};font-size:${isAr?"34px":"30px"};font-weight:600;
            color:rgba(255,255,255,0.34);text-align:center;padding:0 60px;
            margin-bottom:10px;text-shadow:0 2px 8px rgba(0,0,0,0.9);}
      .mc-bar{background:rgba(0,0,0,0.78);padding:20px 52px;
              border-${isAr?"right":"left"}:6px solid ${accent};
              display:flex;align-items:baseline;flex-wrap:wrap;gap:0;
              ${isAr?"justify-content:flex-end;direction:rtl;":""}}
      .mc-c{font-family:${bFont};
            font-size:${currIsPower?(isAr?"72px":"68px"):(isAr?"60px":"56px")};
            font-weight:900;color:${accent};line-height:1.2;
            text-shadow:0 0 20px ${accent}55,0 2px 8px rgba(0,0,0,0.8);}
      .mc-r{font-family:${bFont};font-size:${isAr?"46px":"42px"};font-weight:700;
            color:rgba(255,255,255,0.26);line-height:1.2;
            ${isAr?"margin-right:10px":"margin-left:10px"};}`;
    mainHTML = `<div class="mc">
      ${prev?`<div class="mc-p">${prev}</div>`:""}
      <div class="mc-bar">
        ${isAr
          ?`<span class="mc-r">${next}</span><span class="mc-c"> ${esc(curr)}</span>`
          :`<span class="mc-c">${esc(curr)}</span><span class="mc-r"> ${next}</span>`
        }
      </div>
    </div>`;
  }
  else {
    const kWords = buildWordSpans(words, vc);
    mainCSS  = `
      .kk{position:absolute;top:${topVal};bottom:${botVal==="auto"?"140px":botVal};
          left:0;right:0;padding:0 48px;direction:${dir};}
      .kk-t{font-family:${bFont};font-size:${isAr?"50px":"46px"};font-weight:800;
            text-align:center;line-height:1.6;
            text-shadow:0 2px 12px rgba(0,0,0,0.95);word-break:break-word;}`;
    mainHTML = `<div class="kk"><div class="kk-t">${kWords}</div></div>`;
  }

  const titleBarHTML = `
    <div style="position:absolute;top:60px;left:0;right:0;
                display:flex;justify-content:center;padding:0 50px;z-index:10;">
      <div style="display:inline-flex;align-items:center;gap:10px;
                  background:rgba(0,0,0,0.65);border-radius:40px;
                  padding:10px 22px;border:1.5px solid rgba(255,255,255,0.20);
                  max-width:880px;">
        <span style="font-size:28px;line-height:1;">${e1}</span>
        <span style="font-family:${tFont};font-size:${isTAr?"25px":"23px"};font-weight:800;
                     color:rgba(255,255,255,0.92);line-height:1.2;
                     direction:${isTAr?"rtl":"ltr"};
                     text-shadow:0 1px 6px rgba(0,0,0,0.8);">${esc(titleText)}</span>
        <span style="font-size:28px;line-height:1;">${e2}</span>
      </div>
    </div>`;

  const saveLabel = isTAr ? "احفظ الفيديو 🔖" : "Save This 🔖";
  const engDiv    = (isLast && isDone) ? `
    <div style="position:absolute;inset:0;display:flex;justify-content:center;
                align-items:center;pointer-events:none;z-index:20;">
      <div style="background:${accent};border-radius:60px;padding:22px 52px;
                  display:flex;align-items:center;
                  box-shadow:0 0 60px ${accent}aa,0 10px 40px rgba(0,0,0,0.7);">
        <span style="font-family:${tFont};font-size:48px;font-weight:900;
                     color:#000;white-space:nowrap;">${saveLabel}</span>
      </div>
    </div>` : "";

  const MAX_D  = 9;
  const half   = Math.floor(MAX_D/2);
  let dStart   = Math.max(0, sentenceIdx - half);
  dStart       = Math.min(dStart, Math.max(0, totalSentences - MAX_D));
  const dEnd   = Math.min(totalSentences, dStart + MAX_D);
  const dotsDiv = `
    <div style="position:absolute;bottom:68px;left:0;right:0;
                display:flex;justify-content:center;align-items:center;gap:9px;z-index:10;">
      ${Array.from({length: dEnd - dStart}, (_,k) => {
        const i = dStart + k;
        return i === sentenceIdx
          ? `<div style="width:22px;height:7px;border-radius:4px;background:${accent};"></div>`
          : `<div style="width:7px;height:7px;border-radius:50%;background:rgba(255,255,255,0.18);"></div>`;
      }).join("")}
    </div>`;

  const progDiv = `
    <div style="position:absolute;bottom:44px;left:56px;right:56px;
                height:4px;background:rgba(255,255,255,0.10);border-radius:2px;overflow:hidden;z-index:10;">
      <div style="height:100%;width:${pct}%;
                  background:linear-gradient(90deg,${accent},${accent}66);border-radius:2px;"></div>
    </div>`;

  const overlayDiv = `
    <div style="position:absolute;bottom:0;left:0;right:0;height:75%;
                background:linear-gradient(to top,
                  rgba(0,0,0,0.96) 0%,rgba(0,0,0,0.78) 20%,
                  rgba(0,0,0,0.45) 38%,rgba(0,0,0,0.18) 55%,transparent 72%
                );pointer-events:none;"></div>`;

  return `<!DOCTYPE html>
<html lang="${lang}">
<head>
  <meta charset="UTF-8"/>
  <link href="https://fonts.googleapis.com/css2?family=Noto+Naskh+Arabic:wght@700;800&family=Amiri:wght@700&family=Inter:wght@700;800;900&display=swap" rel="stylesheet"/>
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
// FRAME STATE MAP
// ─────────────────────────────────────────────────────────────────────────────

const SCREEN_TYPE = {
  HOOK:       "hook",
  SENTENCE:   "sentence",
  PATTERN:    "pattern",
  ENGAGEMENT: "engagement",
};

function buildFrameStateMap(timeline, nFrames, realDur) {
  const sentenceCount    = sentences.length;
  const HOOK_FRAMES      = Math.round(2 * FPS);
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
    position_idx:       0,
    style_idx:          0,
    accent_idx:         0,
    pi_idx:             0,
    eq_idx:             0,
    is_hook:            false,
  }));

  for (let f = 0; f < Math.min(HOOK_FRAMES, nFrames); f++) {
    map[f] = { ...map[f], screen_type: SCREEN_TYPE.HOOK, is_hook: true };
  }

  if (timeline && timeline.length > 0) {
    const timelineMax = timeline[timeline.length - 1].time;
    const scale       = timelineMax > 0.1 ? (realDur / timelineMax) : 1.0;
    const scaled      = timeline.map(ev => ({ ...ev, time: ev.time * scale }));

    for (let f = HOOK_FRAMES; f < nFrames; f++) {
      const t = f / FPS;
      let lo = 0, hi = scaled.length - 1, best = null;
      while (lo <= hi) {
        const mid = (lo + hi) >> 1;
        if (scaled[mid].time <= t + 0.001) { best = scaled[mid]; lo = mid + 1; }
        else hi = mid - 1;
      }
      if (best) {
        map[f] = {
          ...map[f],
          screen_type:        SCREEN_TYPE.SENTENCE,
          sentence_idx:       best.sentence_idx,
          visible_word_count: best.visible_word_count,
          position_idx:       best.sentence_idx,
          style_idx:          best.sentence_idx,
          accent_idx:         best.sentence_idx,
        };
      }
    }
  } else {
    const cd = (realDur - 2) / sentenceCount;
    for (let f = HOOK_FRAMES; f < nFrames; f++) {
      const t  = (f / FPS) - 2;
      const si = Math.min(Math.floor(t / cd), sentenceCount - 1);
      const ws = (sentences[si]||"").split(" ");
      const lt = t - si * cd;
      const wi = Math.min(Math.floor((lt / cd) * ws.length), ws.length - 1);
      map[f] = {
        ...map[f],
        screen_type:        SCREEN_TYPE.SENTENCE,
        sentence_idx:       si,
        visible_word_count: wi + 1,
        position_idx:       si,
        style_idx:          si,
        accent_idx:         si,
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
    if      (state.screen_type === SCREEN_TYPE.HOOK)       key = "hook_0";
    else if (state.screen_type === SCREEN_TYPE.PATTERN)    key = `pi_${state.pi_idx}`;
    else if (state.screen_type === SCREEN_TYPE.ENGAGEMENT) key = `eq_${state.eq_idx}`;
    else key = `s_${state.sentence_idx}_${state.visible_word_count}`;
    if (!uniqueStates.has(key)) uniqueStates.set(key, state);
  }
  console.log(`  📸 ${uniqueStates.size} unique states`);

  const initHtml = buildSentenceHTML(
    sentences[0]||" ", 0, title, 0, sentences.length, 0, ACCENT_COLORS[0], 0
  );
  writeFileSync(`${TMP}/init.html`, initHtml, "utf-8");
  await page.goto(`file://${TMP}/init.html`, { waitUntil: "networkidle" });
  await page.waitForTimeout(2000);
  console.log("  ✅ Fonts loaded");

  const isAr      = isArabic(title) || isArabic(sentences[0]||"");
  const INTERRUPTS = isAr ? PATTERN_INTERRUPTS_AR : PATTERN_INTERRUPTS_EN;
  const QUESTIONS  = isAr ? ENGAGEMENT_QUESTIONS_AR : ENGAGEMENT_QUESTIONS_EN;
  const hookText   = sentences[0] || title;
  const pngCache   = new Map();
  let rendered     = 0;

  for (const [key, state] of uniqueStates) {
    let html;
    if (state.screen_type === SCREEN_TYPE.HOOK) {
      html = buildHookHTML(hookText, ACCENT_COLORS[0]);
    } else if (state.screen_type === SCREEN_TYPE.PATTERN) {
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
      );
    }

    const htmlPath = `${TMP}/${key}.html`;
    writeFileSync(htmlPath, html, "utf-8");
    await page.goto(`file://${htmlPath}`, { waitUntil: "load" });
    await page.waitForTimeout(30);

    const pngPath = `${TMP}/${key}.png`;
    await page.screenshot({ path: pngPath, type: "png", omitBackground: true });
    pngCache.set(key, pngPath);
    rendered++;
    if (rendered % 20 === 0 || rendered === uniqueStates.size)
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
    if      (state.screen_type === SCREEN_TYPE.HOOK)       key = "hook_0";
    else if (state.screen_type === SCREEN_TYPE.PATTERN)    key = `pi_${state.pi_idx}`;
    else if (state.screen_type === SCREEN_TYPE.ENGAGEMENT) key = `eq_${state.eq_idx}`;
    else key = `s_${state.sentence_idx}_${state.visible_word_count}`;
    const src  = pngCache.get(key);
    const dest = `${dir}/frame_${String(f).padStart(6,"0")}.png`;
    if (!src) continue;
    try { symlinkSync(src, dest); } catch { copyFileSync(src, dest); }
  }
  return dir;
}

// ─────────────────────────────────────────────────────────────────────────────
// المرحلة 6: PROCESS BACKGROUND — 4 أنماط Zoom ديناميكي
// ─────────────────────────────────────────────────────────────────────────────

function processBackground(videoPath, duration, outPath, idx) {
  const n = Math.ceil(duration * FPS);
  const ZOOM_PATTERNS = [
    `zoompan=z='min(zoom+0.0004,1.09)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=${n}:s=${WIDTH}x${HEIGHT}:fps=${FPS}`,
    `zoompan=z='if(eq(on\\,1)\\,1.09\\,max(zoom-0.0004\\,1.0))':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=${n}:s=${WIDTH}x${HEIGHT}:fps=${FPS}`,
    `zoompan=z='min(zoom+0.0003,1.07)':x='min(iw*0.05+on*0.3\\,iw*0.1)':y='ih/2-(ih/zoom/2)':d=${n}:s=${WIDTH}x${HEIGHT}:fps=${FPS}`,
    `zoompan=z='1.06':x='max(iw*0.1-on*0.3\\,0)':y='ih/2-(ih/zoom/2)':d=${n}:s=${WIDTH}x${HEIGHT}:fps=${FPS}`,
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
// MAIN
// ─────────────────────────────────────────────────────────────────────────────

console.log("\n🚀 Starting v2 render (10-phase retention system)...\n");

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

console.log("🖼️  Rendering states...");
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

console.log("🎵 Merging voiceover...");
mergeAudio(dissolved, audio, outputPath);
console.log(`\n🎉 Final video → ${outputPath}`);
console.log(`   Phases: Hook | Sentences | Pattern Interrupts | Engagement | Dynamic Zoom`);
