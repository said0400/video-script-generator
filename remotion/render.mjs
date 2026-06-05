// remotion/render.mjs — Full Paragraph Display
// ✨ ألوان ديناميكية حسب المشاعر
// ✨ Pulse على الكلمة الحالية
// ✨ Hook قوي في الثواني الأولى
// ✨ خطوط محسّنة مع فرق في الحجم
// ✨ Ken Burns + فلاتر سينمائية
// ✨ انتقالات سلسة

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
  display_title = title,
  emoji_left    = "🔥",
  emoji_right   = "💥",
  sentences,
  audio,
  videos,
  duration_s,
  power_words   = [],
  accent_colors = [],
  word_timeline = [],
  aligned       = [],
  lang          = "ar",
  clip_duration = 3.0,
  has_hook      = false,
  hook_keyword  = "",
  analysis      = {},
} = props;

const FPS    = 30;
const WIDTH  = 1080;
const HEIGHT = 1920;

const SILENCE_THRESHOLD = 0.15;

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


// ═══════════════════════════════════════════════════════════════════════════
// ✨ EMOTION COLOR SYSTEM — ألوان ديناميكية حسب المشاعر
// ═══════════════════════════════════════════════════════════════════════════

const EMOTION_COLORS = {
  curiosity: {
    bg:         "rgba(255, 215, 0, 0.95)",    // أصفر ذهبي
    bgSolid:    "#FFD700",
    current:    "#FF6B00",
    glow:       "rgba(255, 107, 0, 0.8)",
    label:      "Golden Curiosity",
  },
  fear: {
    bg:         "rgba(180, 0, 0, 0.92)",      // أحمر داكن
    bgSolid:    "#B40000",
    current:    "#FF4444",
    glow:       "rgba(255, 68, 68, 0.8)",
    label:      "Deep Fear",
  },
  hope: {
    bg:         "rgba(0, 180, 100, 0.92)",    // أخضر فاتح
    bgSolid:    "#00B464",
    current:    "#FFFFFF",
    glow:       "rgba(255, 255, 255, 0.9)",
    label:      "Fresh Hope",
  },
  joy: {
    bg:         "rgba(255, 120, 0, 0.95)",    // برتقالي
    bgSolid:    "#FF7800",
    current:    "#FFFFFF",
    glow:       "rgba(255, 255, 255, 0.9)",
    label:      "Vibrant Joy",
  },
  awe: {
    bg:         "rgba(80, 0, 180, 0.92)",     // بنفسجي
    bgSolid:    "#5000B4",
    current:    "#FFD700",
    glow:       "rgba(255, 215, 0, 0.8)",
    label:      "Deep Awe",
  },
  surprise: {
    bg:         "rgba(0, 150, 220, 0.92)",    // أزرق
    bgSolid:    "#0096DC",
    current:    "#FFD700",
    glow:       "rgba(255, 215, 0, 0.8)",
    label:      "Blue Surprise",
  },
  desire: {
    bg:         "rgba(220, 0, 100, 0.92)",    // وردي داكن
    bgSolid:    "#DC0064",
    current:    "#FFD700",
    glow:       "rgba(255, 215, 0, 0.8)",
    label:      "Deep Desire",
  },
  anger: {
    bg:         "rgba(200, 20, 0, 0.95)",     // أحمر قوي
    bgSolid:    "#C81400",
    current:    "#FFD700",
    glow:       "rgba(255, 215, 0, 0.9)",
    label:      "Burning Anger",
  },
  sadness: {
    bg:         "rgba(40, 60, 120, 0.92)",    // أزرق داكن
    bgSolid:    "#283C78",
    current:    "#A0C4FF",
    glow:       "rgba(160, 196, 255, 0.8)",
    label:      "Deep Sadness",
  },
  default: {
    bg:         "rgba(255, 215, 0, 0.95)",    // افتراضي: ذهبي
    bgSolid:    "#FFD700",
    current:    "#FF1744",
    glow:       "rgba(255, 23, 68, 0.8)",
    label:      "Default Golden",
  },
};

// احصل على ألوان المشاعر الحالية
function getEmotionColors() {
  const emotion = (analysis.primary_emotion || "").toLowerCase();
  return EMOTION_COLORS[emotion] || EMOTION_COLORS.default;
}

const COLORS = getEmotionColors();
console.log(`🎨 Color theme: ${COLORS.label}`);


// ═══════════════════════════════════════════════════════════════════════════
// HELPERS
// ═══════════════════════════════════════════════════════════════════════════

function probeDuration(filePath) {
  const r = spawnSync(
    "ffprobe",
    [
      "-v", "error",
      "-show_entries", "format=duration",
      "-of", "default=noprint_wrappers=1:nokey=1",
      filePath,
    ],
    { stdio: ["ignore", "pipe", "pipe"] },
  );
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


// ═══════════════════════════════════════════════════════════════════════════
// TEXT DETECTION
// ═══════════════════════════════════════════════════════════════════════════

const isArabicText = (t) => /[\u0600-\u06FF]/.test(t);
const isFrenchText = (t) => /[àâçéèêëîïôùûüÿœæ]/i.test(t);

function getFontFamily(text) {
  if (isArabicText(text)) {
    return `"Noto Naskh Arabic", "Amiri", serif`;
  }
  return `"Noto Sans", "DejaVu Sans", sans-serif`;
}

function getLangAttr(text) {
  if (isArabicText(text)) return "ar";
  if (isFrenchText(text)) return "fr";
  return "en";
}

function getDir(text) {
  return isArabicText(text) ? "rtl" : "ltr";
}

const esc = (s) =>
  (s || "")
    .toString()
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
  return word
    .toString()
    .replace(/[.,!?؟،;:"'(){}[\]<>«»…]/g, "")
    .trim()
    .toLowerCase();
}

function isPowerWord(word) {
  if (!power_words || power_words.length === 0) return false;
  const normalized = normalizeWord(word);
  if (!normalized || normalized.length < 2) return false;
  return power_words.some((pw) => {
    const pwNorm = normalizeWord(pw);
    if (!pwNorm) return false;
    if (normalized === pwNorm) return true;
    if (pwNorm.length >= 3 && normalized.includes(pwNorm)) return true;
    if (normalized.length >= 3 && pwNorm.includes(normalized)) return true;
    return false;
  });
}


// ═══════════════════════════════════════════════════════════════════════════
// BUILD FRAME STATE MAP
// ═══════════════════════════════════════════════════════════════════════════

function buildFrameStateMap() {
  let segments = [];

  if (aligned && aligned.length > 0) {
    segments = aligned.map((seg) => ({
      sentence: seg.sentence || "",
      start:    seg.start    || 0,
      end:      seg.end      || 0,
      words:    (seg.words || []).map((w) => ({
        word:  w.word  || "",
        start: w.start || 0,
        end:   w.end   || 0,
      })),
    }));
  }

  if (segments.length === 0) {
    console.log("⚠️  No aligned segments - using equal split");
    const perSentence =
      effectiveDuration / Math.max(sentences.length, 1);

    for (let i = 0; i < sentences.length; i++) {
      const words   = sentences[i].split(/\s+/).filter(Boolean);
      const start   = i * perSentence;
      const end     = (i + 1) * perSentence;
      const wordDur =
        words.length > 0 ? (end - start) / words.length : 1;

      segments.push({
        sentence: sentences[i],
        start,
        end,
        words: words.map((w, j) => ({
          word:  w,
          start: start + j * wordDur,
          end:   start + (j + 1) * wordDur,
        })),
      });
    }
  }

  console.log(`\n📊 Segments (${segments.length}):`);
  segments.forEach((seg, i) => {
    const preview = (seg.sentence || "").substring(0, 50);
    const wc      = (seg.words || []).length;
    console.log(
      `  ${i + 1}. [${seg.start.toFixed(2)}s → ` +
      `${seg.end.toFixed(2)}s] ${wc}w: "${preview}..."`
    );
  });

  const map = new Array(totalFrames).fill(null);

  for (let f = 0; f < totalFrames; f++) {
    const t = f / FPS;

    let currentSeg    = null;
    let currentSegIdx = -1;

    for (let i = 0; i < segments.length; i++) {
      if (t >= segments[i].start && t < segments[i].end) {
        currentSeg    = segments[i];
        currentSegIdx = i;
        break;
      }
    }

    if (!currentSeg) {
      if (segments.length > 0 && t < segments[0].start) {
        map[f] = {
          segment_idx:      -1,
          segment:          null,
          current_word_idx: -1,
          fade_progress:    0,
        };
        continue;
      }

      const lastSeg = segments[segments.length - 1];
      if (lastSeg && t >= lastSeg.end) {
        map[f] = {
          segment_idx:      segments.length - 1,
          segment:          lastSeg,
          current_word_idx: (lastSeg.words?.length ?? 1) - 1,
          fade_progress:    1.0,
        };
        continue;
      }

      let prevSeg = null;
      let nextSeg = null;

      for (let i = segments.length - 1; i >= 0; i--) {
        if (t >= segments[i].end) {
          prevSeg = segments[i];
          break;
        }
      }
      for (let i = 0; i < segments.length; i++) {
        if (t < segments[i].start) {
          nextSeg = segments[i];
          break;
        }
      }

      const gapSize =
        prevSeg && nextSeg
          ? nextSeg.start - prevSeg.end
          : 999;

      if (gapSize > SILENCE_THRESHOLD) {
        map[f] = {
          segment_idx:      -1,
          segment:          null,
          current_word_idx: -1,
          fade_progress:    0,
        };
      } else {
        const fallbackSeg = prevSeg || segments[0];
        const fallbackIdx = prevSeg
          ? segments.indexOf(prevSeg)
          : 0;
        map[f] = {
          segment_idx:      fallbackIdx,
          segment:          fallbackSeg,
          current_word_idx: (fallbackSeg.words?.length ?? 1) - 1,
          fade_progress:    1.0,
        };
      }
      continue;
    }

    let currentWordIdx = -1;
    const segWords     = currentSeg.words || [];

    for (let i = 0; i < segWords.length; i++) {
      if (t >= segWords[i].start && t <= segWords[i].end) {
        currentWordIdx = i;
        break;
      }
      if (t > segWords[i].end) {
        currentWordIdx = i;
      }
    }

    const segStartFrame = Math.floor(currentSeg.start * FPS);
    const framesSince   = Math.max(0, f - segStartFrame);
    const fadeProgress  = Math.min(framesSince / 4, 1.0);

    map[f] = {
      segment_idx:      currentSegIdx,
      segment:          currentSeg,
      current_word_idx: currentWordIdx,
      fade_progress:    fadeProgress,
    };
  }

  console.log("\n📊 Sample sync:");
  [0, 0.25, 0.5, 0.75].forEach((pct) => {
    const f = Math.floor(totalFrames * pct);
    if (map[f]) {
      const t    = (f / FPS).toFixed(2);
      const seg  = map[f].segment;
      const wIdx = map[f].current_word_idx;
      const word =
        seg && seg.words && wIdx >= 0 && seg.words[wIdx]
          ? seg.words[wIdx].word
          : seg
          ? "---"
          : "SILENCE";
      console.log(
        `  ${t}s → seg ${map[f].segment_idx} | word "${word}"`
      );
    }
  });

  return map;
}


// ═══════════════════════════════════════════════════════════════════════════
// ✨ HTML BUILDER — مع كل الميزات الجديدة
// ═══════════════════════════════════════════════════════════════════════════

function buildHTML(opts) {
  const {
    segment,
    currentWordIdx,
    fadeProgress,
    isHookFrame = false,
  } = opts;

  // صمت
  if (!segment || !segment.words || segment.words.length === 0) {
    return (
      `<!DOCTYPE html><html>` +
      `<head><meta charset="UTF-8"/></head>` +
      `<body style="width:${WIDTH}px;height:${HEIGHT}px;` +
      `background:transparent;margin:0;padding:0;"></body>` +
      `</html>`
    );
  }

  const segWords     = segment.words || [];
  const allWordsText = segWords.map((w) => w.word);
  const fullText     = allWordsText.join(" ");

  const ar       = isArabicText(fullText);
  const dir      = getDir(fullText);
  const font     = getFontFamily(fullText);
  const langAttr = getLangAttr(fullText);

  const titleAr   = isArabicText(display_title);
  const titleFont = getFontFamily(display_title);
  const titleDir  = getDir(display_title);

  // ✨ حجم خط النص — أكبر من السابق لوضوح أكثر
  const wc = allWordsText.length;
  let textFontSize;
  if      (wc <= 5)  textFontSize = ar ? 90 : 86;
  else if (wc <= 10) textFontSize = ar ? 76 : 72;
  else if (wc <= 15) textFontSize = ar ? 64 : 60;
  else if (wc <= 20) textFontSize = ar ? 56 : 52;
  else               textFontSize = ar ? 48 : 44;

  // ✨ حجم خط العنوان — أصغر من النص الرئيسي بوضوح
  const titleFontSize = titleAr ? 40 : 36;

  // ✨ لون الخلفية الديناميكية حسب المشاعر
  const bgColor      = COLORS.bg;
  const currentColor = COLORS.current;
  const glowColor    = COLORS.glow;

  // لون النص العادي حسب لون الخلفية
  // خلفيات فاتحة → نص أسود | خلفيات داكنة → نص أبيض
  const darkBgs = ["fear", "awe", "sadness", "desire", "anger"];
  const emotion = (analysis.primary_emotion || "").toLowerCase();
  const isDarkBg      = darkBgs.includes(emotion);
  const normalColor   = isDarkBg ? "#FFFFFF" : "#000000";
  const upcomingColor = isDarkBg ? "rgba(255,255,255,0.6)" : "#555555";

  const showPowerSolo =
    allWordsText.length === 1 && isPowerWord(allWordsText[0]);

  // ✨ بناء الكلمات مع Pulse على الكلمة الحالية
  let wordIdx = 0;
  const wordsHTML = allWordsText
    .map((word) => {
      const isCurrent = wordIdx === currentWordIdx;
      const isPast    = wordIdx < currentWordIdx;

      let style = "";

      if (isCurrent) {
        // ✨ Pulse animation على الكلمة الحالية
        style =
          `color:${currentColor};` +
          `opacity:1.0;` +
          `text-shadow:` +
          `0 0 20px ${glowColor},` +
          `0 0 40px ${glowColor},` +
          `0 0 60px ${glowColor};` +
          `display:inline-block;` +
          `animation:pulse 0.15s ease-out;`;
      } else if (isPast) {
        style =
          `color:${normalColor};` +
          `opacity:1.0;` +
          `text-shadow:none;` +
          `display:inline;`;
      } else {
        style =
          `color:${upcomingColor};` +
          `opacity:0.75;` +
          `text-shadow:none;` +
          `display:inline;`;
      }

      wordIdx++;
      return (
        `<span class="word" style="${style}">${esc(word)}</span>`
      );
    })
    .join(" ");

  // ✨ Hook text حسب اللغة
  const hookTexts = {
    ar: "🔴 لا تتجاوز هذا",
    fr: "🔴 Ne ratez pas ça",
    en: "🔴 Don't skip this",
  };
  const hookText = hookTexts[lang] || hookTexts.en;

  let mainContent;
  if (showPowerSolo) {
    mainContent =
      `<div class="power-word-container">` +
      `<span class="power-word-text">${esc(allWordsText[0])}</span>` +
      `</div>`;
  } else {
    mainContent =
      `<div class="text-container">` +
      `<div class="text-bg">${wordsHTML}</div>` +
      `</div>`;
  }

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

    /* ✨ Pulse animation للكلمة الحالية */
    @keyframes pulse {
      0%   { transform: scale(1.0); }
      50%  { transform: scale(1.06); }
      100% { transform: scale(1.0); }
    }

    .overlay-top {
      position:absolute; top:0; left:0; right:0; height:35%;
      background:linear-gradient(
        to bottom,
        rgba(0,0,0,0.72) 0%,
        rgba(0,0,0,0.3) 60%,
        transparent 100%
      );
      pointer-events:none; z-index:1;
    }
    .overlay-bottom {
      position:absolute; bottom:0; left:0; right:0; height:45%;
      background:linear-gradient(
        to top,
        rgba(0,0,0,0.72) 0%,
        rgba(0,0,0,0.3) 60%,
        transparent 100%
      );
      pointer-events:none; z-index:1;
    }

    /* ✨ Hook Banner — الثواني الأولى */
    .hook-banner {
      position:absolute;
      top:180px;
      left:50%;
      transform:translateX(-50%);
      background:rgba(255,0,0,0.9);
      color:#FFFFFF;
      font-family:${titleFont};
      font-size:${ar ? "38px" : "34px"};
      font-weight:900;
      padding:14px 40px;
      border-radius:9999px;
      z-index:25;
      white-space:nowrap;
      box-shadow:
        0 0 30px rgba(255,0,0,0.8),
        0 6px 20px rgba(0,0,0,0.5);
      animation:hookPulse 1s ease-in-out infinite;
    }

    @keyframes hookPulse {
      0%,100% { transform:translateX(-50%) scale(1.0); }
      50%      { transform:translateX(-50%) scale(1.04); }
    }

    /* ✨ العنوان — حجم أصغر وأنيق */
    .title-container {
      position:absolute;
      top:${isHookFrame ? "300px" : "380px"};
      left:50%;
      transform:translateX(-50%);
      width:88%;
      max-width:960px;
      direction:${titleDir};
      text-align:center;
      z-index:20;
      transition:top 0.3s ease;
    }
    .title-box {
      display:inline-block;
      background:#FF0000;
      padding:16px 36px;
      border-radius:9999px;
      box-shadow:
        0 0 40px rgba(255,0,0,0.6),
        0 8px 25px rgba(0,0,0,0.5);
    }
    .title-text {
      font-family:${titleFont};
      font-size:${titleFontSize}px;
      font-weight:800;
      color:#FFFFFF;
      display:inline-flex;
      align-items:center;
      gap:12px;
      white-space:nowrap;
      text-shadow:0 2px 6px rgba(0,0,0,0.4);
    }
    .title-emoji {
      font-size:${titleAr ? "44px" : "40px"};
    }

    /* ✨ النص الرئيسي — مع لون ديناميكي */
    .text-container {
      position:absolute;
      left:50%;
      top:62%;
      transform:translate(-50%, -50%);
      width:85%;
      max-width:920px;
      direction:${dir};
      text-align:center;
      z-index:10;
      opacity:${fadeProgress};
    }

    /* ✨ خلفية ديناميكية حسب المشاعر */
    .text-bg {
      display:inline-block;
      background:${bgColor};
      font-family:${font};
      font-size:${textFontSize}px;
      font-weight:900;
      line-height:1.65;
      padding:28px 38px;
      border-radius:28px;
      box-shadow:
        0 8px 32px rgba(0,0,0,0.35),
        0 0 0 3px rgba(255,255,255,0.1);
      max-width:100%;
      word-wrap:break-word;
      overflow-wrap:break-word;
    }

    .word {
      transition:
        color       0.08s ease-out,
        opacity     0.08s ease-out,
        text-shadow 0.12s ease-out;
    }

    /* ✨ Power Word */
    .power-word-container {
      position:absolute;
      left:50%; top:62%;
      transform:translate(-50%, -50%);
      direction:${dir};
      text-align:center;
      z-index:10;
      opacity:${fadeProgress};
    }
    .power-word-text {
      display:inline-block;
      background:#FF0000;
      color:#FFD700;
      font-family:${font};
      font-size:${ar ? "155px" : "145px"};
      font-weight:900;
      line-height:1.5;
      padding:18px 44px;
      border-radius:9999px;
      box-shadow:
        0 0 90px rgba(255,0,0,0.85),
        0 18px 45px rgba(0,0,0,0.65);
      animation:pulse 0.3s ease-out;
    }
  </style>
</head>
<body>
  <div class="overlay-top"></div>
  <div class="overlay-bottom"></div>

  <!-- ✨ Hook Banner — يظهر فقط في الثواني الأولى -->
  ${isHookFrame ? `<div class="hook-banner">${esc(hookText)}</div>` : ""}

  <div class="title-container">
    <div class="title-box">
      <div class="title-text">
        <span class="title-emoji">${emoji_left}</span>
        <span>${esc(display_title)}</span>
        <span class="title-emoji">${emoji_right}</span>
      </div>
    </div>
  </div>

  ${mainContent}
</body>
</html>`;
}


// ═══════════════════════════════════════════════════════════════════════════
// RENDER PNGs
// ═══════════════════════════════════════════════════════════════════════════

async function renderAllPNGs(page, frameStateMap) {
  const uniqueStates = new Map();

  // ✨ Hook frames — الـ 3 ثواني الأولى
  const HOOK_DURATION_FRAMES = Math.floor(3.0 * FPS);

  for (let f = 0; f < frameStateMap.length; f++) {
    const state = frameStateMap[f];
    if (!state) continue;

    const isHookFrame = f < HOOK_DURATION_FRAMES;

    if (!state.segment) {
      const key = isHookFrame ? "silence_hook" : "silence";
      if (!uniqueStates.has(key))
        uniqueStates.set(key, { ...state, isHookFrame });
      continue;
    }

    const fadeStage =
      state.fade_progress >= 1.0
        ? "full"
        : Math.floor(state.fade_progress * 3);
    const key =
      `s${state.segment_idx}_w${state.current_word_idx}` +
      `_f${fadeStage}` +
      (isHookFrame ? "_hook" : "");
    if (!uniqueStates.has(key))
      uniqueStates.set(key, { ...state, isHookFrame });
  }

  console.log(`\n  📸 ${uniqueStates.size} unique states`);

  // Warmup عربي
  const initHtmlAr = buildHTML({
    segment: {
      words: [{ word: "مرحبا", start: 0, end: 1 }],
      start: 0, end: 1,
    },
    currentWordIdx: 0,
    fadeProgress:   1.0,
    isHookFrame:    false,
  });
  writeFileSync(`${TMP}/init_ar.html`, initHtmlAr, "utf-8");
  await page.goto(`file://${TMP}/init_ar.html`, {
    waitUntil: "networkidle",
  });
  await page.waitForTimeout(1000);

  // Warmup إنجليزي/فرنسي
  const initHtmlEn = buildHTML({
    segment: {
      words: [{ word: "Hello", start: 0, end: 1 }],
      start: 0, end: 1,
    },
    currentWordIdx: 0,
    fadeProgress:   1.0,
    isHookFrame:    false,
  });
  writeFileSync(`${TMP}/init_en.html`, initHtmlEn, "utf-8");
  await page.goto(`file://${TMP}/init_en.html`, {
    waitUntil: "networkidle",
  });
  await page.waitForTimeout(500);

  console.log("  ✅ Fonts loaded (AR + EN/FR)");

  const pngCache = new Map();
  let rendered   = 0;

  for (const [key, state] of uniqueStates) {
    const html = buildHTML({
      segment:        state.segment,
      currentWordIdx: state.current_word_idx,
      fadeProgress:   state.fade_progress,
      isHookFrame:    state.isHookFrame || false,
    });

    const htmlPath = `${TMP}/${key}.html`;
    writeFileSync(htmlPath, html, "utf-8");
    await page.goto(`file://${htmlPath}`, { waitUntil: "load" });
    await page.waitForTimeout(40);

    const pngPath = `${TMP}/${key}.png`;
    await page.screenshot({
      path:           pngPath,
      type:           "png",
      omitBackground: true,
    });
    pngCache.set(key, pngPath);
    rendered++;

    if (rendered % 30 === 0 || rendered === uniqueStates.size) {
      process.stdout.write(
        `    ${rendered}/${uniqueStates.size} PNGs\n`
      );
    }
  }

  return pngCache;
}


// ═══════════════════════════════════════════════════════════════════════════
// BUILD FRAME DIR
// ═══════════════════════════════════════════════════════════════════════════

function buildFrameDir(clipFrameMap, pngCache, idx, clipStartFrame) {
  const dir = `${TMP}/frames_${idx}`;
  mkdirSync(dir, { recursive: true });

  const HOOK_DURATION_FRAMES = Math.floor(3.0 * FPS);

  for (let f = 0; f < clipFrameMap.length; f++) {
    const state = clipFrameMap[f];
    if (!state) continue;

    const globalFrame = clipStartFrame + f;
    const isHookFrame = globalFrame < HOOK_DURATION_FRAMES;

    let key;
    if (!state.segment) {
      key = isHookFrame ? "silence_hook" : "silence";
    } else {
      const fadeStage =
        state.fade_progress >= 1.0
          ? "full"
          : Math.floor(state.fade_progress * 3);
      key =
        `s${state.segment_idx}_w${state.current_word_idx}` +
        `_f${fadeStage}` +
        (isHookFrame ? "_hook" : "");
    }

    const src  = pngCache.get(key);
    const dest =
      `${dir}/frame_${String(f).padStart(6, "0")}.png`;
    if (!src) continue;

    try {
      symlinkSync(src, dest);
    } catch {
      copyFileSync(src, dest);
    }
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
  const probeResult = spawnSync(
    "ffprobe",
    [
      "-v", "error",
      "-show_entries", "format=duration",
      "-of", "default=noprint_wrappers=1:nokey=1",
      videoPath,
    ],
    { stdio: ["ignore", "pipe", "pipe"] },
  );

  const sourceDuration =
    parseFloat(probeResult.stdout.toString().trim()) || 0;

  const inputArgs =
    sourceDuration >= duration + 0.5
      ? ["-i", videoPath]
      : ["-stream_loop", "-1", "-i", videoPath];

  // ✨ Ken Burns Effect
  const motionType = idx % 4;
  let zoomFilter;

  if (isHook) {
    zoomFilter =
      `scale=w='trunc((iw*1.5)/2)*2':h='trunc((ih*1.5)/2)*2',` +
      `zoompan=` +
      `z='min(zoom+0.0015,1.5)':` +
      `x='iw/2-(iw/zoom/2)':` +
      `y='ih/2-(ih/zoom/2)':` +
      `d=${Math.ceil(duration * FPS)}:` +
      `s=${WIDTH}x${HEIGHT}:` +
      `fps=${FPS}`;
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
      `zoompan=z='1.1':` +
      `x='if(gte(x,iw/10),x-0.5,iw/10)':y='ih/2-(ih/zoom/2)':` +
      `d=${Math.ceil(duration * FPS)}:s=${WIDTH}x${HEIGHT}:fps=${FPS}`;
  } else {
    zoomFilter =
      `scale=w='trunc((iw*1.2)/2)*2':h='trunc((ih*1.2)/2)*2',` +
      `zoompan=z='1.1':` +
      `x='if(lte(x,iw-iw/10),x+0.5,iw-iw/10)':y='ih/2-(ih/zoom/2)':` +
      `d=${Math.ceil(duration * FPS)}:s=${WIDTH}x${HEIGHT}:fps=${FPS}`;
  }

  // ✨ فلاتر سينمائية
  const cinematicFilters = [
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

  const safeDur    = Math.max(duration, 0.5);
  const fadeInDur  = Math.min(0.4, safeDur * 0.1);
  const fadeOutDur = Math.min(0.4, safeDur * 0.1);
  const fadeFilter =
    `fade=t=in:st=0:d=${fadeInDur.toFixed(3)},` +
    `fade=t=out:st=${(safeDur - fadeOutDur).toFixed(3)}:d=${fadeOutDur.toFixed(3)}`;

  const videoFilter =
    `${zoomFilter},${cinematicFilters},${fadeFilter}`;

  let r = spawnSync(
    "ffmpeg",
    [
      "-y", ...inputArgs,
      "-t", duration.toFixed(3),
      "-vf", videoFilter,
      "-r", String(FPS),
      "-c:v", "libx264", "-preset", "fast",
      "-crf", isHook ? "17" : "19",
      "-pix_fmt", "yuv420p", "-an",
      outPath,
    ],
    { stdio: ["ignore", "pipe", "pipe"] },
  );

  if (r.status !== 0) {
    const simpleFilter =
      `scale=${WIDTH}:${HEIGHT}:force_original_aspect_ratio=increase,` +
      `crop=${WIDTH}:${HEIGHT},setsar=1,` +
      `${cinematicFilters},${fadeFilter}`;

    r = spawnSync(
      "ffmpeg",
      [
        "-y", "-stream_loop", "-1", "-i", videoPath,
        "-t", duration.toFixed(3),
        "-vf", simpleFilter,
        "-r", String(FPS),
        "-c:v", "libx264", "-preset", "fast", "-crf", "21",
        "-pix_fmt", "yuv420p", "-an",
        outPath,
      ],
      { stdio: ["ignore", "pipe", "pipe"] },
    );

    if (r.status !== 0) {
      spawnSync(
        "ffmpeg",
        [
          "-y", "-stream_loop", "-1", "-i", videoPath,
          "-t", duration.toFixed(3),
          "-vf",
          `scale=${WIDTH}:${HEIGHT}:force_original_aspect_ratio=increase,` +
          `crop=${WIDTH}:${HEIGHT},setsar=1`,
          "-r", String(FPS),
          "-c:v", "libx264", "-preset", "fast", "-crf", "23",
          "-pix_fmt", "yuv420p", "-an",
          outPath,
        ],
        { stdio: ["ignore", "pipe", "pipe"] },
      );
    }
  }

  return outPath;
}


// ═══════════════════════════════════════════════════════════════════════════
// FFMPEG HELPERS
// ═══════════════════════════════════════════════════════════════════════════

function framesToMov(frameDir, outPath) {
  const r = spawnSync(
    "ffmpeg",
    [
      "-y",
      "-framerate", String(FPS),
      "-i", `${frameDir}/frame_%06d.png`,
      "-vf", `scale=${WIDTH}:${HEIGHT},format=rgba`,
      "-c:v", "png", "-an",
      outPath,
    ],
    { stdio: ["ignore", "pipe", "pipe"] },
  );

  if (r.status !== 0) {
    console.error("❌ framesToMov failed");
    console.error(r.stderr?.toString().slice(-200) ?? "");
  }
  return outPath;
}


function overlayOnBackground(bgMp4, captionMov, outPath) {
  const r = spawnSync(
    "ffmpeg",
    [
      "-y",
      "-i", bgMp4,
      "-i", captionMov,
      "-filter_complex",
      "[1:v]format=rgba[cap];" +
      "[0:v][cap]overlay=0:0:format=auto," +
      "format=yuv420p[out]",
      "-map", "[out]",
      "-c:v", "libx264", "-preset", "fast", "-crf", "19",
      "-pix_fmt", "yuv420p", "-an",
      outPath,
    ],
    { stdio: ["ignore", "pipe", "pipe"] },
  );

  if (r.status !== 0) {
    console.error("❌ overlay failed");
    console.error(r.stderr?.toString().slice(-200) ?? "");
  }
  return outPath;
}


// ═══════════════════════════════════════════════════════════════════════════
// XFADE CONCAT — انتقالات ناعمة
// ═══════════════════════════════════════════════════════════════════════════

function xfadeConcat(clipPaths, clipDurations) {
  if (clipPaths.length === 0) return "";
  if (clipPaths.length === 1) return clipPaths[0];

  const TRANSITIONS = [
    "fade",
    "fadeblack",
    "fadegrays",
    "smoothleft",
    "smoothright",
    "circlecrop",
  ];

  const XFADE = 0.5;

  const filters = [];
  let offset = 0;
  let last   = "[0:v]";

  for (let i = 1; i < clipPaths.length; i++) {
    offset += clipDurations[i - 1] - XFADE;
    if (offset < 0) offset = 0;
    const out   =
      i === clipPaths.length - 1 ? "[vout]" : `[v${i}]`;
    const trans = TRANSITIONS[(i - 1) % TRANSITIONS.length];
    filters.push(
      `${last}[${i}:v]xfade=transition=${trans}:` +
      `duration=${XFADE}:offset=${offset.toFixed(3)}${out}`
    );
    last = out;
  }

  const outPath = `${TMP}/xfaded.mp4`;
  const r = spawnSync(
    "ffmpeg",
    [
      "-y",
      ...clipPaths.flatMap((p) => ["-i", p]),
      "-filter_complex", filters.join(";"),
      "-map", "[vout]",
      "-c:v", "libx264", "-preset", "fast", "-crf", "19",
      "-pix_fmt", "yuv420p", "-an",
      outPath,
    ],
    { stdio: ["ignore", "pipe", "pipe"] },
  );

  if (r.status !== 0) {
    console.error("⚠️  xfade failed - simple fade fallback");

    const simpleFade = [];
    let off2 = 0;
    let last2 = "[0:v]";
    for (let i = 1; i < clipPaths.length; i++) {
      off2 += clipDurations[i - 1] - 0.3;
      if (off2 < 0) off2 = 0;
      const out2 =
        i === clipPaths.length - 1 ? "[vout]" : `[v${i}]`;
      simpleFade.push(
        `${last2}[${i}:v]xfade=transition=fade:` +
        `duration=0.3:offset=${off2.toFixed(3)}${out2}`
      );
      last2 = out2;
    }

    const r2 = spawnSync(
      "ffmpeg",
      [
        "-y",
        ...clipPaths.flatMap((p) => ["-i", p]),
        "-filter_complex", simpleFade.join(";"),
        "-map", "[vout]",
        "-c:v", "libx264", "-preset", "fast", "-crf", "21",
        "-pix_fmt", "yuv420p", "-an",
        outPath,
      ],
      { stdio: ["ignore", "pipe", "pipe"] },
    );

    if (r2.status !== 0) {
      const lst = `${TMP}/list.txt`;
      writeFileSync(
        lst,
        clipPaths.map((p) => `file '${p}'`).join("\n"),
      );
      const raw = `${TMP}/raw.mp4`;
      spawnSync(
        "ffmpeg",
        ["-y", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", raw],
        { stdio: "inherit" },
      );
      return raw;
    }
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
    console.log("⚠️  Video shorter - looping...");
    const looped = `${TMP}/video_looped.mp4`;
    const r = spawnSync(
      "ffmpeg",
      [
        "-y", "-stream_loop", "-1", "-i", videoPath,
        "-t", aDur.toFixed(3),
        "-c:v", "libx264", "-preset", "fast", "-crf", "21",
        "-pix_fmt", "yuv420p", "-an",
        looped,
      ],
      { stdio: ["ignore", "pipe", "pipe"] },
    );
    if (r.status === 0) {
      finalVideo = looped;
      console.log(`  ✅ Looped to ${aDur.toFixed(2)}s`);
    }
  }

  const r = spawnSync(
    "ffmpeg",
    [
      "-y",
      "-i", finalVideo,
      "-i", audioPath,
      "-map", "0:v:0",
      "-map", "1:a:0",
      "-c:v", "copy",
      "-c:a", "aac", "-b:a", "192k",
      "-t", aDur.toFixed(3),
      outPath,
    ],
    { stdio: ["ignore", "pipe", "pipe"] },
  );

  if (r.status !== 0) {
    console.error("❌ Merge failed");
    process.exit(1);
  }

  console.log(`✅ Final: ${aDur.toFixed(3)}s → ${outPath}`);
}


// ═══════════════════════════════════════════════════════════════════════════
// MAIN
// ═══════════════════════════════════════════════════════════════════════════

async function main() {
  console.log(
    "\n🚀 Starting Renderer — Emotion Colors + Pulse + Hook\n"
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
    const clipStart      = i * actualClipDuration;
    const clipEnd        = Math.min(
      (i + 1) * actualClipDuration,
      effectiveDuration,
    );
    const clipDur        = Math.max(clipEnd - clipStart, 0.5);
    const nFrames        = Math.ceil(clipDur * FPS);
    const startF         = Math.floor(clipStart * FPS);
    const clipMap        = frameStateMap.slice(startF, startF + nFrames);

    const isHook   = i === 0 && has_hook;
    const videoIdx = i % videos.length;
    const videoSrc = videos[videoIdx];

    process.stdout.write(
      `  [${i + 1}/${totalClips}] ` +
      `${clipDur.toFixed(2)}s${isHook ? " 🔥HOOK" : ""}... `
    );

    // ✨ تمرير clipStartFrame لتحديد هل هذا الكليب في الـ Hook zone
    const frameDir   = buildFrameDir(clipMap, pngCache, i, startF);
    const captionMov = `${TMP}/caption_${i}.mov`;
    framesToMov(frameDir, captionMov);

    const bgMp4 =
      `${TMP}/bg_${String(i).padStart(3, "0")}.mp4`;
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
