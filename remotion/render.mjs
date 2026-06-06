// remotion/render.mjs — Word-by-Word Display (CapCut Style)
// ✅ كل كلمة تظهر في وقتها الفعلي 100%
// ✅ لا offset — timestamps مباشرة من WhisperX
// ✅ كلمة واحدة في منتصف الشاشة
// ✅ حجم كبير جداً
// ✅ ألوان ديناميكية حسب المشاعر
// ✅ Power Words مميزة
// ✅ Custom Hook في أول 3 ثواني
// ✅ Ken Burns + فلاتر سينمائية
// ✅ انتقالات سلسة

import {
  readFileSync,
  writeFileSync,
  mkdirSync,
  copyFileSync,
  symlinkSync,
} from "fs";
import { spawnSync } from "child_process";
import { chromium } from "playwright";

// ═══════════════════════════════════════════════════════════════════════════
// READ MANIFEST
// ═══════════════════════════════════════════════════════════════════════════

const manifestPath = process.argv[2];
const outputPath   = process.argv[3];

if (!manifestPath || !outputPath) {
  console.error("Usage: node render.mjs <manifest.json> <output.mp4>");
  process.exit(1);
}

const props = JSON.parse(readFileSync(manifestPath, "utf-8"));

const {
  title,
  display_title  = title,
  emoji_left     = "🔥",
  emoji_right    = "💥",
  sentences,
  audio,
  videos,
  duration_s,
  power_words    = [],
  accent_colors  = [],
  aligned        = [],
  lang           = "ar",
  clip_duration  = 3.0,
  has_hook       = false,
  hook_keyword   = "",
  custom_hook    = "",
  analysis       = {},
  bg_style       = "video",
} = props;

const FPS              = 30;
const WIDTH            = 1080;
const HEIGHT           = 1920;
const TITLE_SLIDE_FRAMES = Math.floor(0.6 * FPS);

const safeOut = outputPath
  .replace(/[^a-zA-Z0-9]/g, "_")
  .replace(/_+/g, "_")
  .slice(-22);
const TMP = `/tmp/vsg_${safeOut}`;
mkdirSync(TMP, { recursive: true });

console.log(`📌 Title: ${emoji_left} ${display_title} ${emoji_right}`);
console.log(`🎬 Clip: ${clip_duration}s | Hook: ${has_hook ? "YES" : "NO"}`);
console.log(`🎯 Aligned: ${aligned.length} segments`);
console.log(`🌐 Lang: ${lang.toUpperCase()}`);
console.log(`💭 Emotion: ${analysis.primary_emotion || "unknown"}`);
console.log(`🪝 Custom Hook: "${custom_hook || "none"}"`);
console.log(`🎨 Style: Word-by-Word CapCut`);


// ═══════════════════════════════════════════════════════════════════════════
// EMOTION COLORS
// ═══════════════════════════════════════════════════════════════════════════

const EMOTION_COLORS = {
  curiosity: { word: "#FFD700", glow: "rgba(255,215,0,0.6)",   shadow: "rgba(0,0,0,0.95)", power: "#FF1744", label: "Golden"   },
  fear:      { word: "#FF4444", glow: "rgba(255,68,68,0.6)",   shadow: "rgba(0,0,0,0.95)", power: "#FFD700", label: "Fear"     },
  hope:      { word: "#00E676", glow: "rgba(0,230,118,0.6)",   shadow: "rgba(0,0,0,0.95)", power: "#FFFFFF", label: "Hope"     },
  joy:       { word: "#FF9100", glow: "rgba(255,145,0,0.6)",   shadow: "rgba(0,0,0,0.95)", power: "#FFFFFF", label: "Joy"      },
  awe:       { word: "#AA00FF", glow: "rgba(170,0,255,0.6)",   shadow: "rgba(0,0,0,0.95)", power: "#FFD700", label: "Awe"      },
  surprise:  { word: "#00B0FF", glow: "rgba(0,176,255,0.6)",   shadow: "rgba(0,0,0,0.95)", power: "#FFD700", label: "Surprise" },
  desire:    { word: "#FF1744", glow: "rgba(255,23,68,0.6)",   shadow: "rgba(0,0,0,0.95)", power: "#FFD700", label: "Desire"   },
  anger:     { word: "#FF1744", glow: "rgba(255,23,68,0.6)",   shadow: "rgba(0,0,0,0.95)", power: "#FFD700", label: "Anger"    },
  sadness:   { word: "#82B1FF", glow: "rgba(130,177,255,0.6)", shadow: "rgba(0,0,0,0.95)", power: "#FFFFFF", label: "Sadness"  },
  default:   { word: "#FFFFFF", glow: "rgba(255,255,255,0.5)", shadow: "rgba(0,0,0,0.95)", power: "#FF1744", label: "Default"  },
};

const emotion = (analysis.primary_emotion || "").toLowerCase();
const COLORS  = EMOTION_COLORS[emotion] || EMOTION_COLORS.default;
console.log(`🎨 Color: ${COLORS.label}`);


// ═══════════════════════════════════════════════════════════════════════════
// HELPERS
// ═══════════════════════════════════════════════════════════════════════════

function probeDuration(filePath) {
  const r = spawnSync("ffprobe", [
    "-v", "error",
    "-show_entries", "format=duration",
    "-of", "default=noprint_wrappers=1:nokey=1",
    filePath,
  ], { stdio: ["ignore", "pipe", "pipe"] });
  return parseFloat(r.stdout.toString().trim()) || 0;
}

const realAudioDuration = probeDuration(audio);
const effectiveDuration = realAudioDuration > 5
  ? realAudioDuration
  : duration_s;
const totalFrames = Math.ceil(effectiveDuration * FPS);

console.log(`📋 Sentences : ${sentences.length}`);
console.log(`🎵 Audio     : ${realAudioDuration.toFixed(3)}s`);
console.log(`🎞️  Frames    : ${totalFrames}`);

const isArabicText = (t) => /[\u0600-\u06FF]/.test(t);
const isFrenchText = (t) => /[àâçéèêëîïôùûüÿœæ]/i.test(t);

function getFontFamily(text) {
  if (isArabicText(text)) return `"Noto Naskh Arabic", "Amiri", serif`;
  return `"Noto Sans", "DejaVu Sans", sans-serif`;
}

function getDir(text) {
  return isArabicText(text) ? "rtl" : "ltr";
}

function getLangAttr(text) {
  if (isArabicText(text)) return "ar";
  if (isFrenchText(text)) return "fr";
  return "en";
}

const esc = (s) =>
  (s || "").toString()
    .replace(/&/g,  "&amp;")
    .replace(/</g,  "&lt;")
    .replace(/>/g,  "&gt;")
    .replace(/"/g,  "&quot;")
    .replace(/'/g,  "&#039;");


// ═══════════════════════════════════════════════════════════════════════════
// POWER WORDS
// ═══════════════════════════════════════════════════════════════════════════

function normalizeWord(word) {
  if (!word) return "";
  return word.toString()
    .replace(/[.,!?؟،;:"'(){}[\]<>«»…]/g, "")
    .trim().toLowerCase();
}

function isPowerWord(word) {
  if (!power_words || power_words.length === 0) return false;
  const n = normalizeWord(word);
  if (!n || n.length < 2) return false;
  return power_words.some((pw) => {
    const pn = normalizeWord(pw);
    if (!pn) return false;
    if (n === pn) return true;
    if (pn.length >= 3 && n.includes(pn)) return true;
    if (n.length >= 3 && pn.includes(n)) return true;
    return false;
  });
}


// ═══════════════════════════════════════════════════════════════════════════
// ✅ BUILD WORD-LEVEL FRAME MAP
// كل كلمة تظهر في وقتها الفعلي من WhisperX
// ═══════════════════════════════════════════════════════════════════════════

function buildFrameStateMap() {
  const allWords = [];

  // ✅ استخراج timestamps مباشرة من WhisperX بدون تعديل
  if (aligned && aligned.length > 0) {
    for (const seg of aligned) {
      if (seg.words && seg.words.length > 0) {
        for (const w of seg.words) {
          if (
            w.word &&
            w.start !== undefined &&
            w.end   !== undefined
          ) {
            allWords.push({
              word:  w.word.trim(),
              start: parseFloat(w.start) || 0,
              end:   parseFloat(w.end)   || 0,
            });
          }
        }
      }
    }
  }

  // Fallback: توزيع متساوٍ إذا لم يوجد word-level alignment
  if (allWords.length === 0 && sentences.length > 0) {
    console.log("⚠️  No word-level alignment — using equal split");
    const allText = sentences.join(" ").split(/\s+/).filter(Boolean);
    const perWord = effectiveDuration / Math.max(allText.length, 1);
    for (let i = 0; i < allText.length; i++) {
      allWords.push({
        word:  allText[i],
        start: i * perWord,
        end:   (i + 1) * perWord,
      });
    }
  }

  if (allWords.length === 0) {
    console.log("⚠️  No words found — empty video");
    return new Array(totalFrames).fill(null);
  }

  // ✅ تمديد كل كلمة حتى بداية التالية
  // يمنع الفراغات الصغيرة بين الكلمات (< 0.4s)
  for (let i = 0; i < allWords.length - 1; i++) {
    const gap = allWords[i + 1].start - allWords[i].end;
    if (gap > 0 && gap < 0.4) {
      allWords[i].end = allWords[i + 1].start;
    }
  }

  console.log(`\n📊 Words: ${allWords.length} total`);
  console.log(`   First: [${allWords[0].start.toFixed(3)}s → ${allWords[0].end.toFixed(3)}s] "${allWords[0].word}"`);
  console.log(`   Last:  [${allWords[allWords.length-1].start.toFixed(3)}s → ${allWords[allWords.length-1].end.toFixed(3)}s] "${allWords[allWords.length-1].word}"`);

  // بناء frame map
  const map = new Array(totalFrames).fill(null);

  for (let f = 0; f < totalFrames; f++) {
    const t = f / FPS;
    let currentWord = null;

    // ✅ Binary search للبحث السريع والدقيق
    let lo = 0, hi = allWords.length - 1;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      const w   = allWords[mid];
      if (t >= w.start && t < w.end) {
        currentWord = w;
        break;
      } else if (t < w.start) {
        hi = mid - 1;
      } else {
        lo = mid + 1;
      }
    }

    // ✅ إذا لم نجد كلمة حالية
    if (!currentWord) {
      if (t < allWords[0].start) {
        // قبل أول كلمة → صمت
        map[f] = null;
        continue;
      }

      if (t >= allWords[allWords.length - 1].end) {
        // بعد آخر كلمة → نعرضها
        currentWord = allWords[allWords.length - 1];
      } else {
        // بين كلمتين → فجوة
        for (let i = allWords.length - 1; i >= 0; i--) {
          if (t >= allWords[i].end) {
            const gap = (i + 1 < allWords.length)
              ? allWords[i + 1].start - allWords[i].end
              : 999;
            // فجوة صغيرة → نعرض الكلمة السابقة
            if (gap < 0.4) {
              currentWord = allWords[i];
            }
            // فجوة كبيرة → صمت
            break;
          }
        }
      }
    }

    if (currentWord) {
      const wordDur  = Math.max(currentWord.end - currentWord.start, 0.05);
      const progress = Math.min(
        Math.max((t - currentWord.start) / wordDur, 0),
        1.0
      );

      map[f] = {
        word:     currentWord.word,
        start:    currentWord.start,
        end:      currentWord.end,
        progress: progress,
        isPower:  isPowerWord(currentWord.word),
      };
    } else {
      map[f] = null;
    }
  }

  // تقرير التغطية
  const covered = map.filter(Boolean).length;
  console.log(
    `   Coverage: ${covered}/${totalFrames} frames ` +
    `(${((covered / totalFrames) * 100).toFixed(1)}%)`
  );

  return map;
}


// ═══════════════════════════════════════════════════════════════════════════
// HTML BUILDER — Word-by-Word CapCut Style
// ═══════════════════════════════════════════════════════════════════════════

function buildHTML(opts) {
  const {
    word,
    progress    = 0,
    isPower     = false,
    isHookFrame = false,
    globalFrame = 0,
  } = opts;

  // صمت → شاشة شفافة
  if (!word) {
    return (
      `<!DOCTYPE html><html><head><meta charset="UTF-8"/></head>` +
      `<body style="width:${WIDTH}px;height:${HEIGHT}px;` +
      `background:transparent;margin:0;padding:0;"></body></html>`
    );
  }

  const ar       = isArabicText(word);
  const dir      = getDir(word);
  const font     = getFontFamily(word);
  const langAttr = getLangAttr(word);

  const titleAr   = isArabicText(display_title);
  const titleFont = getFontFamily(display_title);
  const titleDir  = getDir(display_title);

  // ✅ حجم الكلمة — ضخم جداً حسب طول الكلمة
  const wordLen = word.length;
  let fontSize;
  if (isPower) {
    fontSize = ar ? 185 : 175;
  } else if (wordLen <= 2) {
    fontSize = ar ? 165 : 155;
  } else if (wordLen <= 4) {
    fontSize = ar ? 145 : 135;
  } else if (wordLen <= 6) {
    fontSize = ar ? 125 : 115;
  } else if (wordLen <= 9) {
    fontSize = ar ? 105 : 98;
  } else if (wordLen <= 12) {
    fontSize = ar ? 88 : 82;
  } else {
    fontSize = ar ? 72 : 68;
  }

  // ✅ Scale animation: ظهور سريع ثم ثبات
  const scaleIn = progress < 0.12
    ? 0.65 + (progress / 0.12) * 0.35
    : 1.0;

  // ألوان
  const wordColor = isPower ? COLORS.power : COLORS.word;
  const glowColor = COLORS.glow;
  const shadowClr = COLORS.shadow;
  const glowSize  = isPower ? "90px" : "45px";
  const glowSize2 = isPower ? "130px" : "70px";

  // Power Word → خلفية pill
  const pillBg = isPower
    ? `background:rgba(200,0,0,0.88);padding:22px 55px;border-radius:9999px;`
    : `background:transparent;padding:0;`;

  // Slide-in للعنوان
  const slideProgress = globalFrame < TITLE_SLIDE_FRAMES
    ? globalFrame / TITLE_SLIDE_FRAMES
    : 1.0;
  const easedSlide   = 1 - Math.pow(1 - slideProgress, 3);
  const slideStartX  = titleDir === "rtl" ? 120 : -120;
  const slideCurrent = slideStartX * (1 - easedSlide);
  const titleOpacity = easedSlide;
  const titleSize    = titleAr ? 38 : 34;

  // Hook text
  const defaultHooks = {
    ar: "🔴 لا تتجاوز هذا",
    fr: "🔴 Ne ratez pas ça",
    en: "🔴 Don't skip this",
  };
  const hookText = (
    custom_hook && custom_hook.trim().length > 0
      ? custom_hook.trim()
      : defaultHooks[lang] || defaultHooks.en
  );
  const hookIsAr = isArabicText(hookText);
  const hookDir  = hookIsAr ? "rtl" : "ltr";
  const hookFont = getFontFamily(hookText);

  return `<!DOCTYPE html>
<html lang="${langAttr}">
<head>
  <meta charset="UTF-8"/>
  <style>
    * { margin:0; padding:0; box-sizing:border-box; }
    html, body {
      width:${WIDTH}px; height:${HEIGHT}px;
      overflow:hidden; background:transparent;
    }

    .overlay-top {
      position:absolute; top:0; left:0; right:0; height:28%;
      background:linear-gradient(
        to bottom,rgba(0,0,0,0.7) 0%,transparent 100%
      );
      pointer-events:none; z-index:1;
    }
    .overlay-bottom {
      position:absolute; bottom:0; left:0; right:0; height:32%;
      background:linear-gradient(
        to top,rgba(0,0,0,0.7) 0%,transparent 100%
      );
      pointer-events:none; z-index:1;
    }

    /* Hook Banner */
    .hook-banner {
      position:absolute; top:155px; left:50%;
      transform:translateX(-50%);
      background:rgba(210,0,0,0.93);
      color:#FFFFFF;
      font-family:${hookFont};
      font-size:${hookIsAr ? "34px" : "30px"};
      font-weight:900;
      padding:13px 38px;
      border-radius:9999px;
      z-index:25;
      white-space:nowrap;
      direction:${hookDir};
      box-shadow:
        0 0 40px rgba(210,0,0,0.85),
        0 6px 22px rgba(0,0,0,0.55);
    }

    /* العنوان */
    .title-container {
      position:absolute;
      top:${isHookFrame ? "278px" : "338px"};
      left:50%;
      width:90%; max-width:960px;
      direction:${titleDir}; text-align:center;
      z-index:20;
      transform:translateX(calc(-50% + ${slideCurrent.toFixed(2)}px));
      opacity:${titleOpacity.toFixed(4)};
    }
    .title-box {
      display:inline-block;
      background:rgba(220,0,0,0.92);
      padding:13px 32px;
      border-radius:9999px;
      box-shadow:
        0 0 32px rgba(220,0,0,0.55),
        0 6px 22px rgba(0,0,0,0.45);
    }
    .title-text {
      font-family:${titleFont};
      font-size:${titleSize}px;
      font-weight:800;
      color:#FFFFFF;
      display:inline-flex; align-items:center; gap:10px;
      white-space:nowrap;
      text-shadow:0 2px 6px rgba(0,0,0,0.45);
    }
    .title-emoji { font-size:${titleAr ? "40px" : "36px"}; }

    /* ✅ الكلمة — في منتصف الشاشة بالضبط */
    .word-container {
      position:absolute;
      left:50%; top:52%;
      transform:translate(-50%,-50%) scale(${scaleIn.toFixed(4)});
      direction:${dir}; text-align:center;
      z-index:10;
      width:95%;
      max-width:1000px;
    }
    .word-pill {
      display:inline-block;
      ${pillBg}
      box-shadow:${isPower
        ? "0 0 70px rgba(200,0,0,0.75),0 14px 40px rgba(0,0,0,0.65)"
        : "none"};
    }
    .word-text {
      font-family:${font};
      font-size:${fontSize}px;
      font-weight:900;
      color:${wordColor};
      line-height:1.2;
      text-shadow:
        0 0 ${glowSize} ${glowColor},
        0 0 ${glowSize2} ${glowColor},
        0 5px 25px ${shadowClr},
        2px 2px 0px rgba(0,0,0,0.8),
        -2px -2px 0px rgba(0,0,0,0.8),
        0 0 8px rgba(0,0,0,0.9);
      letter-spacing:${ar ? "1px" : "3px"};
      display:block;
      word-break:break-word;
    }
  </style>
</head>
<body>
  <div class="overlay-top"></div>
  <div class="overlay-bottom"></div>

  ${isHookFrame
    ? `<div class="hook-banner">${esc(hookText)}</div>`
    : ""}

  <div class="title-container">
    <div class="title-box">
      <div class="title-text">
        <span class="title-emoji">${emoji_left}</span>
        <span>${esc(display_title)}</span>
        <span class="title-emoji">${emoji_right}</span>
      </div>
    </div>
  </div>

  <div class="word-container">
    <div class="word-pill">
      <span class="word-text">${esc(word)}</span>
    </div>
  </div>
</body>
</html>`;
}


// ═══════════════════════════════════════════════════════════════════════════
// RENDER PNGs
// ═══════════════════════════════════════════════════════════════════════════

async function renderAllPNGs(page, frameStateMap) {
  const uniqueStates = new Map();
  const HOOK_FRAMES  = Math.floor(3.0 * FPS);

  for (let f = 0; f < frameStateMap.length; f++) {
    const state       = frameStateMap[f];
    const isHookFrame = f < HOOK_FRAMES;
    const slideKey    = f < TITLE_SLIDE_FRAMES ? `sl${f}` : "sld";

    if (!state) {
      const key = `empty_${isHookFrame ? "h" : "n"}_${slideKey}`;
      if (!uniqueStates.has(key))
        uniqueStates.set(key, {
          word: null, isHookFrame, globalFrame: f,
        });
      continue;
    }

    const scaleStage = state.progress < 0.12
      ? Math.floor(state.progress * 25)
      : "done";

    const key =
      `w_${state.word}_p${state.isPower ? 1 : 0}` +
      `_sc${scaleStage}` +
      `_${isHookFrame ? "h" : "n"}` +
      `_${slideKey}`;

    if (!uniqueStates.has(key))
      uniqueStates.set(key, {
        ...state, isHookFrame, globalFrame: f,
      });
  }

  console.log(`\n  📸 ${uniqueStates.size} unique states`);

  // Warmup — تحميل الخطوط
  for (const [initWord, initLang] of [["مرحبا", "ar"], ["Hello", "en"]]) {
    const html = buildHTML({
      word:        initWord,
      progress:    1.0,
      isPower:     false,
      isHookFrame: false,
      globalFrame: TITLE_SLIDE_FRAMES,
    });
    const p = `${TMP}/init_${initLang}.html`;
    writeFileSync(p, html, "utf-8");
    await page.goto(`file://${p}`, { waitUntil: "networkidle" });
    await page.waitForTimeout(initLang === "ar" ? 1000 : 500);
  }
  console.log("  ✅ Fonts loaded");

  const pngCache = new Map();
  let rendered   = 0;

  for (const [key, state] of uniqueStates) {
    const html = buildHTML({
      word:        state.word,
      progress:    state.progress    || 0,
      isPower:     state.isPower     || false,
      isHookFrame: state.isHookFrame || false,
      globalFrame: state.globalFrame || 0,
    });

    const htmlPath = `${TMP}/${key}.html`;
    writeFileSync(htmlPath, html, "utf-8");
    await page.goto(`file://${htmlPath}`, { waitUntil: "load" });
    await page.waitForTimeout(35);

    const pngPath = `${TMP}/${key}.png`;
    await page.screenshot({
      path: pngPath, type: "png", omitBackground: true,
    });
    pngCache.set(key, pngPath);
    rendered++;

    if (rendered % 50 === 0 || rendered === uniqueStates.size)
      process.stdout.write(`    ${rendered}/${uniqueStates.size} PNGs\n`);
  }

  return pngCache;
}


// ═══════════════════════════════════════════════════════════════════════════
// BUILD FRAME DIR
// ═══════════════════════════════════════════════════════════════════════════

function buildFrameDir(clipFrameMap, pngCache, idx, clipStartFrame) {
  const dir = `${TMP}/frames_${idx}`;
  mkdirSync(dir, { recursive: true });

  const HOOK_FRAMES = Math.floor(3.0 * FPS);

  for (let f = 0; f < clipFrameMap.length; f++) {
    const state       = clipFrameMap[f];
    const globalFrame = clipStartFrame + f;
    const isHookFrame = globalFrame < HOOK_FRAMES;
    const slideKey    = globalFrame < TITLE_SLIDE_FRAMES
      ? `sl${globalFrame}`
      : "sld";

    let key;
    if (!state) {
      key = `empty_${isHookFrame ? "h" : "n"}_${slideKey}`;
    } else {
      const scaleStage = state.progress < 0.12
        ? Math.floor(state.progress * 25)
        : "done";
      key =
        `w_${state.word}_p${state.isPower ? 1 : 0}` +
        `_sc${scaleStage}` +
        `_${isHookFrame ? "h" : "n"}` +
        `_${slideKey}`;
    }

    const src  = pngCache.get(key);
    const dest = `${dir}/frame_${String(f).padStart(6, "0")}.png`;
    if (!src) continue;

    try { symlinkSync(src, dest); }
    catch { copyFileSync(src, dest); }
  }

  return dir;
}


// ═══════════════════════════════════════════════════════════════════════════
// PROCESS BACKGROUND — Ken Burns + فلاتر سينمائية
// ═══════════════════════════════════════════════════════════════════════════

function processBackground(
  videoPath,
  duration,
  outPath,
  idx,
  isHook = false,
) {
  const probeResult = spawnSync("ffprobe", [
    "-v", "error",
    "-show_entries", "format=duration",
    "-of", "default=noprint_wrappers=1:nokey=1",
    videoPath,
  ], { stdio: ["ignore", "pipe", "pipe"] });

  const sourceDuration =
    parseFloat(probeResult.stdout.toString().trim()) || 0;

  const inputArgs = sourceDuration >= duration + 0.5
    ? ["-i", videoPath]
    : ["-stream_loop", "-1", "-i", videoPath];

  // Ken Burns
  const motionType = idx % 4;
  let zoomFilter;

  if (isHook) {
    zoomFilter =
      `scale=w='trunc((iw*1.5)/2)*2':h='trunc((ih*1.5)/2)*2',` +
      `zoompan=z='min(zoom+0.0015,1.5)':` +
      `x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':` +
      `d=${Math.ceil(duration * FPS)}:s=${WIDTH}x${HEIGHT}:fps=${FPS}`;
  } else if (motionType === 0) {
    zoomFilter =
      `scale=w='trunc((iw*1.3)/2)*2':h='trunc((ih*1.3)/2)*2',` +
      `zoompan=z='min(zoom+0.0008,1.3)':` +
      `x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':` +
      `d=${Math.ceil(duration * FPS)}:s=${WIDTH}x${HEIGHT}:fps=${FPS}`;
  } else if (motionType === 1) {
    zoomFilter =
      `scale=w='trunc((iw*1.3)/2)*2':h='trunc((ih*1.3)/2)*2',` +
      `zoompan=z='max(zoom-0.0008,1.0)':` +
      `x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':` +
      `d=${Math.ceil(duration * FPS)}:s=${WIDTH}x${HEIGHT}:fps=${FPS}`;
  } else if (motionType === 2) {
    zoomFilter =
      `scale=w='trunc((iw*1.2)/2)*2':h='trunc((ih*1.2)/2)*2',` +
      `zoompan=z='1.1':x='if(gte(x,iw/10),x-0.5,iw/10)':` +
      `y='ih/2-(ih/zoom/2)':` +
      `d=${Math.ceil(duration * FPS)}:s=${WIDTH}x${HEIGHT}:fps=${FPS}`;
  } else {
    zoomFilter =
      `scale=w='trunc((iw*1.2)/2)*2':h='trunc((ih*1.2)/2)*2',` +
      `zoompan=z='1.1':x='if(lte(x,iw-iw/10),x+0.5,iw-iw/10)':` +
      `y='ih/2-(ih/zoom/2)':` +
      `d=${Math.ceil(duration * FPS)}:s=${WIDTH}x${HEIGHT}:fps=${FPS}`;
  }

  // فلاتر سينمائية
  const cinematic = [
    "curves=r='0/0 0.3/0.28 0.7/0.76 1/0.92':" +
      "g='0/0 0.3/0.28 0.7/0.78 1/0.94':" +
      "b='0/0.02 0.3/0.30 0.7/0.82 1/0.98'",
    "hue=s=0.9",
    isHook
      ? "eq=contrast=1.15:brightness=0.02:saturation=1.1"
      : "eq=contrast=1.08:brightness=-0.01:saturation=0.95",
    "vignette=PI/5:eval=frame",
    "unsharp=3:3:0.4:3:3:0.0",
    isHook ? "" : "noise=alls=2:allf=t+u",
  ].filter(Boolean).join(",");

  const safeDur = Math.max(duration, 0.5);
  const fadeIn  = Math.min(0.4, safeDur * 0.1);
  const fadeOut = Math.min(0.4, safeDur * 0.1);
  const fade    =
    `fade=t=in:st=0:d=${fadeIn.toFixed(3)},` +
    `fade=t=out:st=${(safeDur - fadeOut).toFixed(3)}:d=${fadeOut.toFixed(3)}`;

  const videoFilter = `${zoomFilter},${cinematic},${fade}`;

  let r = spawnSync("ffmpeg", [
    "-y", ...inputArgs,
    "-t", duration.toFixed(3),
    "-vf", videoFilter,
    "-r", String(FPS),
    "-c:v", "libx264", "-preset", "fast",
    "-crf", isHook ? "17" : "19",
    "-pix_fmt", "yuv420p", "-an",
    outPath,
  ], { stdio: ["ignore", "pipe", "pipe"] });

  if (r.status !== 0) {
    // Fallback 1
    r = spawnSync("ffmpeg", [
      "-y", "-stream_loop", "-1", "-i", videoPath,
      "-t", duration.toFixed(3),
      "-vf",
      `scale=${WIDTH}:${HEIGHT}:force_original_aspect_ratio=increase,` +
      `crop=${WIDTH}:${HEIGHT},setsar=1,${cinematic},${fade}`,
      "-r", String(FPS),
      "-c:v", "libx264", "-preset", "fast", "-crf", "21",
      "-pix_fmt", "yuv420p", "-an", outPath,
    ], { stdio: ["ignore", "pipe", "pipe"] });

    if (r.status !== 0) {
      // Fallback 2 — minimal
      spawnSync("ffmpeg", [
        "-y", "-stream_loop", "-1", "-i", videoPath,
        "-t", duration.toFixed(3),
        "-vf",
        `scale=${WIDTH}:${HEIGHT}:force_original_aspect_ratio=increase,` +
        `crop=${WIDTH}:${HEIGHT},setsar=1`,
        "-r", String(FPS),
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-pix_fmt", "yuv420p", "-an", outPath,
      ], { stdio: ["ignore", "pipe", "pipe"] });
    }
  }

  return outPath;
}


// ═══════════════════════════════════════════════════════════════════════════
// FFMPEG HELPERS
// ═══════════════════════════════════════════════════════════════════════════

function framesToMov(frameDir, outPath) {
  spawnSync("ffmpeg", [
    "-y", "-framerate", String(FPS),
    "-i", `${frameDir}/frame_%06d.png`,
    "-vf", `scale=${WIDTH}:${HEIGHT},format=rgba`,
    "-c:v", "png", "-an", outPath,
  ], { stdio: ["ignore", "pipe", "pipe"] });
  return outPath;
}


function overlayOnBackground(bgMp4, captionMov, outPath) {
  spawnSync("ffmpeg", [
    "-y", "-i", bgMp4, "-i", captionMov,
    "-filter_complex",
    "[1:v]format=rgba[cap];" +
    "[0:v][cap]overlay=0:0:format=auto,format=yuv420p[out]",
    "-map", "[out]",
    "-c:v", "libx264", "-preset", "fast", "-crf", "19",
    "-pix_fmt", "yuv420p", "-an", outPath,
  ], { stdio: ["ignore", "pipe", "pipe"] });
  return outPath;
}


function xfadeConcat(clipPaths, clipDurations) {
  if (clipPaths.length === 0) return "";
  if (clipPaths.length === 1) return clipPaths[0];

  const TRANSITIONS = [
    "fade", "fadeblack", "fadegrays",
    "smoothleft", "smoothright", "circlecrop",
  ];
  const XFADE = 0.5;

  const filters = [];
  let offset = 0, last = "[0:v]";

  for (let i = 1; i < clipPaths.length; i++) {
    offset += clipDurations[i - 1] - XFADE;
    if (offset < 0) offset = 0;
    const out   = i === clipPaths.length - 1 ? "[vout]" : `[v${i}]`;
    const trans = TRANSITIONS[(i - 1) % TRANSITIONS.length];
    filters.push(
      `${last}[${i}:v]xfade=transition=${trans}:` +
      `duration=${XFADE}:offset=${offset.toFixed(3)}${out}`
    );
    last = out;
  }

  const outPath = `${TMP}/xfaded.mp4`;
  const r = spawnSync("ffmpeg", [
    "-y",
    ...clipPaths.flatMap((p) => ["-i", p]),
    "-filter_complex", filters.join(";"),
    "-map", "[vout]",
    "-c:v", "libx264", "-preset", "fast", "-crf", "19",
    "-pix_fmt", "yuv420p", "-an", outPath,
  ], { stdio: ["ignore", "pipe", "pipe"] });

  if (r.status !== 0) {
    // Fallback: simple concat
    const lst = `${TMP}/list.txt`;
    writeFileSync(
      lst,
      clipPaths.map((p) => `file '${p}'`).join("\n"),
    );
    const raw = `${TMP}/raw.mp4`;
    spawnSync("ffmpeg", [
      "-y", "-f", "concat", "-safe", "0",
      "-i", lst, "-c", "copy", raw,
    ], { stdio: "inherit" });
    return raw;
  }

  return outPath;
}


function mergeAudio(videoPath, audioPath, outPath) {
  const aDur = probeDuration(audioPath);
  const vDur = probeDuration(videoPath);
  console.log(
    `🎵 Audio: ${aDur.toFixed(3)}s | ` +
    `🎬 Video: ${vDur.toFixed(3)}s`
  );

  let finalVideo = videoPath;

  if (vDur < aDur - 0.3) {
    console.log("⚠️  Video shorter — looping...");
    const looped = `${TMP}/video_looped.mp4`;
    const r = spawnSync("ffmpeg", [
      "-y", "-stream_loop", "-1", "-i", videoPath,
      "-t", aDur.toFixed(3),
      "-c:v", "libx264", "-preset", "fast", "-crf", "21",
      "-pix_fmt", "yuv420p", "-an", looped,
    ], { stdio: ["ignore", "pipe", "pipe"] });
    if (r.status === 0) {
      finalVideo = looped;
      console.log(`  ✅ Looped to ${aDur.toFixed(2)}s`);
    }
  }

  spawnSync("ffmpeg", [
    "-y",
    "-i", finalVideo,
    "-i", audioPath,
    "-map", "0:v:0",
    "-map", "1:a:0",
    "-c:v", "copy",
    "-c:a", "aac", "-b:a", "192k",
    "-t", aDur.toFixed(3),
    outPath,
  ], { stdio: ["ignore", "pipe", "pipe"] });

  console.log(`✅ Final: ${aDur.toFixed(3)}s → ${outPath}`);
}


// ═══════════════════════════════════════════════════════════════════════════
// MAIN
// ═══════════════════════════════════════════════════════════════════════════

async function main() {
  console.log(
    "\n🚀 Starting Renderer — Word-by-Word CapCut Style\n"
  );

  const frameStateMap = buildFrameStateMap();

  const browser = await chromium.launch({
    headless: true,
    args: [
      "--no-sandbox",
      "--disable-setuid-sandbox",
      "--disable-dev-shm-usage",
      "--disable-gpu",
      "--no-zygote",
      "--font-render-hinting=none",
      "--lang=ar,fr,en",
    ],
  });

  const context = await browser.newContext({
    viewport:          { width: WIDTH, height: HEIGHT },
    deviceScaleFactor: 1,
    locale:            "ar-SA",
  });
  const page = await context.newPage();

  console.log("🖼️  Rendering PNGs...");
  const pngCache = await renderAllPNGs(page, frameStateMap);
  await browser.close();
  console.log(`✅ ${pngCache.size} PNGs done\n`);

  const totalClips =
    Math.max(1, Math.floor(effectiveDuration / clip_duration));
  const actualClipDuration = effectiveDuration / totalClips;

  console.log(
    `📊 ${totalClips} clips × ${actualClipDuration.toFixed(2)}s`
  );
  console.log(`🎥 Videos: ${videos.length}`);

  const finalClips    = [];
  const clipDurations = [];

  console.log("\n🎬 Processing clips...");

  for (let i = 0; i < totalClips; i++) {
    const clipStart = i * actualClipDuration;
    const clipEnd   = Math.min(
      (i + 1) * actualClipDuration,
      effectiveDuration,
    );
    const clipDur = Math.max(clipEnd - clipStart, 0.5);
    const nFrames = Math.ceil(clipDur * FPS);
    const startF  = Math.floor(clipStart * FPS);
    const clipMap = frameStateMap.slice(startF, startF + nFrames);

    const isHook   = i === 0 && has_hook;
    const videoIdx = i % videos.length;
    const videoSrc = videos[videoIdx];

    process.stdout.write(
      `  [${i + 1}/${totalClips}] ` +
      `${clipDur.toFixed(2)}s${isHook ? " 🔥HOOK" : ""}... `
    );

    const frameDir   = buildFrameDir(clipMap, pngCache, i, startF);
    const captionMov = `${TMP}/caption_${i}.mov`;
    framesToMov(frameDir, captionMov);

    const bgMp4 = `${TMP}/bg_${String(i).padStart(3, "0")}.mp4`;
    processBackground(videoSrc, clipDur, bgMp4, i, isHook);

    const finalClip =
      `${TMP}/final_${String(i).padStart(3, "0")}.mp4`;
    overlayOnBackground(bgMp4, captionMov, finalClip);
    finalClips.push(finalClip);
    clipDurations.push(clipDur);
    process.stdout.write("✓\n");
  }

  console.log(`\n✨ Concatenating ${finalClips.length} clips...`);
  const dissolved = xfadeConcat(finalClips, clipDurations);

  console.log("🎵 Merging audio...");
  mergeAudio(dissolved, audio, outputPath);
  console.log(`\n🎉 Final → ${outputPath}\n`);
}

main().catch((err) => {
  console.error("\n❌ Fatal error:");
  console.error(err);
  process.exit(1);
});
