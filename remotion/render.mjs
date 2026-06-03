// remotion/render.mjs — KCS + Per-Word Sync with WhisperX
// ✨ النسخة النهائية:
//    - مزامنة دقيقة مع كل كلمة (من WhisperX)
//    - عنوان من Excel (أحمر ساطع + دائري)
//    - خلفية سوداء داكنة + نص أبيض + Karaoke ذهبي
//    - الكلمات القوية: خلفية حمراء + ذهبي (وحدها)
//    - كل 3 ثوانٍ فيديو جديد + Zoom in

import { readFileSync, writeFileSync, mkdirSync, copyFileSync,
         symlinkSync, existsSync } from "fs";
import { spawnSync } from "child_process";
import { chromium } from "playwright";

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
  power_words = [],
  accent_colors = [],
  word_timeline = [],
  aligned = [],
  lang = "ar",
  clip_duration = 3.0,
  has_hook      = false,
  hook_keyword  = "",
} = props;

const FPS    = 30;
const WIDTH  = 1080;
const HEIGHT = 1920;

const safeOut = outputPath.replace(/[^a-zA-Z0-9]/g, "_").replace(/_+/g, "_").slice(-22);
const TMP     = `/tmp/vsg_${safeOut}`;
mkdirSync(TMP, { recursive: true });

console.log(`📌 Display Title: ${emoji_left} ${display_title} ${emoji_right}`);
console.log(`🎬 Clip duration: ${clip_duration}s | Hook: ${has_hook ? "YES" : "NO"}`);
console.log(`🎯 Word timeline: ${word_timeline.length} events | Aligned: ${aligned.length} sentences`);

// ═════════════════════════════════════════════════════════════════════════════
// 🎯 KCS CONFIGURATION
// ═════════════════════════════════════════════════════════════════════════════

const KCS = {
  FADE_IN_FRAMES: 3,
};

// ═════════════════════════════════════════════════════════════════════════════
// 🎨 COLORS
// ═════════════════════════════════════════════════════════════════════════════

const DEFAULT_COLORS = [
  "#FFD700", "#00E5FF", "#FF6B00", "#39FF14",
];

const POWER_COLORS = (accent_colors && accent_colors.length >= 2)
  ? accent_colors
  : DEFAULT_COLORS;

// ═════════════════════════════════════════════════════════════════════════════
// 🔥 POWER WORDS DETECTION
// ═════════════════════════════════════════════════════════════════════════════

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
  
  return power_words.some(pw => {
    const pwNorm = normalizeWord(pw);
    if (!pwNorm) return false;
    if (normalized === pwNorm) return true;
    if (pwNorm.length >= 3 && normalized.includes(pwNorm)) return true;
    if (normalized.length >= 3 && pwNorm.includes(normalized)) return true;
    return false;
  });
}

console.log(`🔥 Power Words (${power_words.length}): ${power_words.slice(0, 8).join(", ")}${power_words.length > 8 ? "..." : ""}`);

// ═════════════════════════════════════════════════════════════════════════════
// 🛠️ HELPERS
// ═════════════════════════════════════════════════════════════════════════════

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

const isArabicText = t => /[\u0600-\u06FF]/.test(t);
const esc = s => (s||"").toString()
  .replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");

// ═════════════════════════════════════════════════════════════════════════════
// 🎯 BUILD WORD-LEVEL TIMELINE FROM WHISPER
// ═════════════════════════════════════════════════════════════════════════════

function buildWordTimeline() {
  const wordTimings = [];
  
  if (aligned && aligned.length > 0) {
    console.log("📝 Using Whisper word_timeline for precise sync");
    
    for (let sIdx = 0; sIdx < aligned.length; sIdx++) {
      const sentence = aligned[sIdx];
      const words = sentence.words || [];
      
      if (words.length > 0) {
        for (let wIdx = 0; wIdx < words.length; wIdx++) {
          const w = words[wIdx];
          wordTimings.push({
            word: w.word,
            start: w.start,
            end: w.end,
            sentence_idx: sIdx,
            word_in_sentence: wIdx,
            total_in_sentence: words.length,
          });
        }
      } else {
        const sentenceWords = sentence.sentence.trim().split(/\s+/).filter(Boolean);
        const sStart = sentence.start;
        const sEnd = sentence.end;
        const wordDur = (sEnd - sStart) / sentenceWords.length;
        
        for (let wIdx = 0; wIdx < sentenceWords.length; wIdx++) {
          wordTimings.push({
            word: sentenceWords[wIdx],
            start: sStart + (wIdx * wordDur),
            end: sStart + ((wIdx + 1) * wordDur),
            sentence_idx: sIdx,
            word_in_sentence: wIdx,
            total_in_sentence: sentenceWords.length,
          });
        }
      }
    }
  } else {
    console.log("⚠️  No Whisper timeline - using equal distribution");
    
    let totalWords = 0;
    sentences.forEach(s => {
      totalWords += s.trim().split(/\s+/).filter(Boolean).length;
    });
    
    const timePerWord = effectiveDuration / totalWords;
    let currentTime = 0;
    
    sentences.forEach((sentence, sIdx) => {
      const sentenceWords = sentence.trim().split(/\s+/).filter(Boolean);
      sentenceWords.forEach((word, wIdx) => {
        wordTimings.push({
          word: word,
          start: currentTime,
          end: currentTime + timePerWord,
          sentence_idx: sIdx,
          word_in_sentence: wIdx,
          total_in_sentence: sentenceWords.length,
        });
        currentTime += timePerWord;
      });
    });
  }
  
  console.log(`📊 Total words in timeline: ${wordTimings.length}`);
  return wordTimings;
}

// ═════════════════════════════════════════════════════════════════════════════
// 📦 ✨ FIXED: BUILD CHUNKS — مزامنة دقيقة مع كل كلمة
// ═════════════════════════════════════════════════════════════════════════════

function buildChunks(wordTimings) {
  const chunks = [];
  let currentSentenceIdx = -1;
  let buffer = [];
  
  const flushBuffer = () => {
    if (buffer.length > 0) {
      chunks.push({
        words: buffer.map(w => w.word),
        wordTimings: [...buffer],
        start: buffer[0].start,
        end: buffer[buffer.length - 1].end,
        hasPower: false,
        sentence_idx: buffer[0].sentence_idx,
      });
      buffer = [];
    }
  };
  
  for (let i = 0; i < wordTimings.length; i++) {
    const wt = wordTimings[i];
    const isPower = isPowerWord(wt.word);
    const isNewSentence = currentSentenceIdx !== -1 && 
                          currentSentenceIdx !== wt.sentence_idx;
    
    // ✨ عند جملة جديدة: اطبع الـ buffer السابق
    if (isNewSentence) {
      flushBuffer();
    }
    
    currentSentenceIdx = wt.sentence_idx;
    
    if (isPower) {
      // ✨ كلمة قوية: اطبع ما قبلها + اعرضها وحدها
      flushBuffer();
      chunks.push({
        words: [wt.word],
        wordTimings: [wt],
        start: wt.start,
        end: wt.end,
        hasPower: true,
        sentence_idx: wt.sentence_idx,
      });
    } else {
      // كلمة عادية: أضفها للجملة الحالية
      buffer.push(wt);
    }
  }
  
  // اطبع آخر buffer
  flushBuffer();
  
  console.log(`📦 Total chunks: ${chunks.length}`);
  return chunks;
}

// ═════════════════════════════════════════════════════════════════════════════
// 📐 LINE SPLITTING — توزيع ذكي على عدة سطور
// ═════════════════════════════════════════════════════════════════════════════

function splitChunkIntoLines(words) {
  if (words.length <= 3) {
    return [words];
  }
  
  if (words.length <= 6) {
    const mid = Math.ceil(words.length / 2);
    return [words.slice(0, mid), words.slice(mid)];
  }
  
  if (words.length <= 9) {
    const third = Math.ceil(words.length / 3);
    return [
      words.slice(0, third),
      words.slice(third, third * 2),
      words.slice(third * 2),
    ];
  }
  
  if (words.length <= 12) {
    const quarter = Math.ceil(words.length / 4);
    return [
      words.slice(0, quarter),
      words.slice(quarter, quarter * 2),
      words.slice(quarter * 2, quarter * 3),
      words.slice(quarter * 3),
    ];
  }
  
  // جمل طويلة: 5 سطور
  const fifth = Math.ceil(words.length / 5);
  return [
    words.slice(0, fifth),
    words.slice(fifth, fifth * 2),
    words.slice(fifth * 2, fifth * 3),
    words.slice(fifth * 3, fifth * 4),
    words.slice(fifth * 4),
  ];
}

// ═════════════════════════════════════════════════════════════════════════════
// 🎨 HTML BUILDER — جملة كاملة + خلفية تتماشى مع النص
// ═════════════════════════════════════════════════════════════════════════════

function buildKaraokeHTML(opts) {
  const {
    chunk,
    currentWordIdx,
    fadeProgress,
  } = opts;
  
  const allWords = chunk.words;
  const ar = isArabicText(allWords.join(" "));
  const dir = ar ? "rtl" : "ltr";
  const font = ar
    ? `"Noto Naskh Arabic","Amiri",serif`
    : `"Inter","Helvetica Neue",Arial,sans-serif`;
  
  // العنوان
  const titleAr = isArabicText(display_title);
  const titleFont = titleAr
    ? `"Noto Naskh Arabic","Amiri",serif`
    : `"Inter","Helvetica Neue",Arial,sans-serif`;
  const titleDir = titleAr ? "rtl" : "ltr";
  
  // تحقق إذا الـ chunk يحتوي كلمة قوية واحدة فقط
  const showPowerWordSolo = chunk.hasPower && allWords.length === 1;
  
  // ✨ حساب حجم الخط ديناميكياً حسب طول الجملة
  let fontSize;
  if (allWords.length <= 4) {
    fontSize = ar ? 80 : 76;
  } else if (allWords.length <= 8) {
    fontSize = ar ? 68 : 64;
  } else if (allWords.length <= 12) {
    fontSize = ar ? 58 : 54;
  } else if (allWords.length <= 16) {
    fontSize = ar ? 50 : 46;
  } else {
    fontSize = ar ? 44 : 40;
  }
  
  let mainContentHTML = "";
  
  if (showPowerWordSolo) {
    // 🟡 الكلمة القوية وحدها
    mainContentHTML = `
      <div class="power-word-container">
        <div class="power-word-box">
          <span class="power-word-text">${esc(allWords[0])}</span>
        </div>
      </div>
    `;
  } else {
    // 📝 الجملة الكاملة - كل الكلمات تظهر معاً
    const lines = splitChunkIntoLines(allWords);
    
    let wordCounter = 0;
    const linesHTML = lines.map(lineWords => {
      const wordsHTML = lineWords.map(word => {
        const isCurrent = wordCounter === currentWordIdx;
        const isPast    = wordCounter < currentWordIdx;
        
        // ✨ Karaoke effect
        let opacity;
        let color;
        if (isCurrent) {
          opacity = 1.0;
          color = "#FFD700";        // ⭐ ذهبي للكلمة الحالية
        } else if (isPast) {
          opacity = 1.0;
          color = "#FFFFFF";        // أبيض للسابقة
        } else {
          opacity = 0.70;
          color = "#CCCCCC";        // رمادي فاتح للقادمة
        }
        
        wordCounter++;
        
        return `<span class="word" style="opacity: ${opacity}; color: ${color};">${esc(word)}</span>`;
      }).join(" ");
      
      return `<div class="line-container">
        <span class="line-bg">${wordsHTML}</span>
      </div>`;
    }).join("");
    
    mainContentHTML = `
      <div class="text-container" style="font-size: ${fontSize}px;">
        ${linesHTML}
      </div>
    `;
  }
  
  return `<!DOCTYPE html>
<html lang="${ar?"ar":"en"}">
<head>
  <meta charset="UTF-8"/>
  <link href="https://fonts.googleapis.com/css2?family=Noto+Naskh+Arabic:wght@700;800;900&family=Inter:wght@700;800;900&display=swap" rel="stylesheet"/>
  <style>
    *{margin:0;padding:0;box-sizing:border-box;}
    html,body{
      width:${WIDTH}px;
      height:${HEIGHT}px;
      overflow:hidden;
      background:transparent;
    }
    
    /* تدرج علوي وسفلي خفيف */
    .overlay-top{
      position:absolute;
      top:0;left:0;right:0;
      height:35%;
      background:linear-gradient(to bottom,
        rgba(0,0,0,0.7) 0%,
        rgba(0,0,0,0.3) 60%,
        transparent 100%
      );
      pointer-events:none;
      z-index:1;
    }
    
    .overlay-bottom{
      position:absolute;
      bottom:0;left:0;right:0;
      height:45%;
      background:linear-gradient(to top,
        rgba(0,0,0,0.7) 0%,
        rgba(0,0,0,0.3) 60%,
        transparent 100%
      );
      pointer-events:none;
      z-index:1;
    }
    
    /* ════════════════════════════════════════════════════════════ */
    /* ✨ العنوان */
    /* ════════════════════════════════════════════════════════════ */
    .title-container{
      position:absolute;
      top:450px;
      left:50%;
      transform:translateX(-50%);
      width:90%;
      max-width:980px;
      direction:${titleDir};
      text-align:center;
      z-index:20;
    }
    
    .title-box{
      display:inline-block;
      background:#FF0000;
      padding:22px 50px;
      border-radius:9999px;
      box-shadow:
        0 0 50px rgba(255,0,0,0.7),
        0 10px 30px rgba(0,0,0,0.5);
    }
    
    .title-text{
      font-family:${titleFont};
      font-size:${titleAr ? "46px" : "42px"};
      font-weight:900;
      color:#FFFFFF;
      letter-spacing:${titleAr ? "0" : "-0.02em"};
      display:inline-flex;
      align-items:center;
      gap:16px;
      white-space:nowrap;
      text-shadow:0 2px 6px rgba(0,0,0,0.4);
    }
    
    .title-emoji{
      font-size:${titleAr ? "54px" : "50px"};
    }
    
    /* ════════════════════════════════════════════════════════════ */
    /* 📝 ✨ الجملة الكاملة - خلفية تتماشى مع كل سطر */
    /* ════════════════════════════════════════════════════════════ */
    .text-container{
      position:absolute;
      left:50%;
      top:62%;
      transform:translate(-50%, -50%);
      width:90%;
      max-width:960px;
      direction:${dir};
      text-align:center;
      z-index:10;
      opacity:${fadeProgress};
    }
    
    /* ✨ Container لكل سطر */
    .line-container{
      display:block;
      text-align:center;
      margin-bottom:10px;
    }
    
    .line-container:last-child{
      margin-bottom:0;
    }
    
    /* ✨ الخلفية تتماشى مع طول النص */
    .line-bg{
      display:inline-block;
      background:rgba(0,0,0,0.92);
      padding:12px 28px;
      border-radius:9999px;
      max-width:100%;
    }
    
    .word{
      font-family:${font};
      font-weight:900;
      line-height:1.3;
      display:inline;
      margin:0 5px;
      transition:opacity 0.15s ease-out, color 0.15s ease-out;
    }
    
    /* ════════════════════════════════════════════════════════════ */
    /* 🔥 الكلمة القوية - خلفية حمراء + ذهبي */
    /* ════════════════════════════════════════════════════════════ */
    .power-word-container{
      position:absolute;
      left:50%;
      top:62%;
      transform:translate(-50%, -50%);
      direction:${dir};
      text-align:center;
      z-index:10;
      opacity:${fadeProgress};
    }
    
    .power-word-box{
      display:inline-block;
      background:#FF0000;
      padding:36px 72px;
      border-radius:9999px;
      box-shadow:
        0 0 80px rgba(255,0,0,0.8),
        0 15px 40px rgba(0,0,0,0.6);
      animation:powerPulse 0.4s ease-out;
    }
    
    .power-word-text{
      font-family:${font};
      font-size:${ar ? "140px" : "130px"};
      font-weight:900;
      color:#FFD700;
      letter-spacing:${ar ? "0" : "-0.03em"};
      display:inline-block;
      text-shadow:0 4px 12px rgba(0,0,0,0.6);
    }
    
    @keyframes powerPulse {
      0% {
        transform:scale(0.7);
        opacity:0;
      }
      60% {
        transform:scale(1.05);
        opacity:1;
      }
      100% {
        transform:scale(1);
        opacity:1;
      }
    }
  </style>
</head>
<body>
  <div class="overlay-top"></div>
  <div class="overlay-bottom"></div>
  
  <!-- ✨ العنوان من Excel -->
  <div class="title-container">
    <div class="title-box">
      <div class="title-text">
        <span class="title-emoji">${emoji_left}</span>
        <span>${esc(display_title)}</span>
        <span class="title-emoji">${emoji_right}</span>
      </div>
    </div>
  </div>
  
  ${mainContentHTML}
</body>
</html>`;
}

// ═════════════════════════════════════════════════════════════════════════════
// 🎬 ✨ FIXED: BUILD FRAME STATE MAP — مزامنة دقيقة من Whisper word timings
// ═════════════════════════════════════════════════════════════════════════════

function buildFrameStateMap() {
  const wordTimings = buildWordTimeline();
  const chunks = buildChunks(wordTimings);
  
  if (chunks.length === 0) {
    return [];
  }
  
  const map = new Array(totalFrames).fill(null);
  
  for (let f = 0; f < totalFrames; f++) {
    const t = f / FPS;
    
    // ✨ ابحث عن الـ chunk الحالي بدقة باستخدام timings الفعلية
    let currentChunk = null;
    let currentChunkIdx = 0;
    
    for (let i = 0; i < chunks.length; i++) {
      const chunk = chunks[i];
      
      // الـ chunk يبدأ في chunk.start وينتهي في chunk.end
      if (t >= chunk.start && t < chunk.end) {
        currentChunk = chunk;
        currentChunkIdx = i;
        break;
      }
      
      // إذا الوقت بين chunks، استخدم الـ chunk السابق
      if (i < chunks.length - 1 && t >= chunk.end && t < chunks[i + 1].start) {
        currentChunk = chunk;
        currentChunkIdx = i;
        break;
      }
    }
    
    // إذا لم نجد، استخدم آخر chunk أو أول chunk
    if (!currentChunk) {
      if (t < chunks[0].start) {
        currentChunk = chunks[0];
        currentChunkIdx = 0;
      } else {
        currentChunk = chunks[chunks.length - 1];
        currentChunkIdx = chunks.length - 1;
      }
    }
    
    // ✨ ابحث عن الكلمة الحالية بدقة باستخدام word timings
    let currentWordIdx = 0;
    for (let i = 0; i < currentChunk.wordTimings.length; i++) {
      const wt = currentChunk.wordTimings[i];
      
      // الكلمة الحالية: الوقت بين start و end
      if (t >= wt.start && t <= wt.end) {
        currentWordIdx = i;
        break;
      }
      
      // إذا تجاوزنا الكلمة، حدّث المؤشر
      if (t > wt.end) {
        currentWordIdx = i;
      }
    }
    
    // Fade in بسيط في بداية كل chunk
    const chunkStartFrame = Math.floor(currentChunk.start * FPS);
    const framesSinceChunkStart = f - chunkStartFrame;
    const fadeProgress = Math.max(0, Math.min(framesSinceChunkStart / KCS.FADE_IN_FRAMES, 1.0));
    
    map[f] = {
      chunk_idx:        currentChunkIdx,
      chunk:            currentChunk,
      current_word_idx: currentWordIdx,
      fade_progress:    fadeProgress,
      sentence_idx:     currentChunk.sentence_idx,
    };
  }
  
  // إحصائيات
  console.log("\n📊 Sample sync points:");
  const sampleEvents = [
    0, 
    Math.floor(totalFrames * 0.25), 
    Math.floor(totalFrames * 0.5), 
    Math.floor(totalFrames * 0.75)
  ];
  
  sampleEvents.forEach(f => {
    if (map[f]) {
      const t = (f / FPS).toFixed(2);
      const chunk = map[f].chunk;
      const wordIdx = map[f].current_word_idx;
      const word = chunk.words[wordIdx] || "?";
      const wt = chunk.wordTimings[wordIdx];
      if (wt) {
        console.log(`   ${t}s → chunk ${map[f].chunk_idx} | word "${word}" (${wt.start.toFixed(2)}s-${wt.end.toFixed(2)}s)`);
      }
    }
  });
  
  return map;
}

// ═════════════════════════════════════════════════════════════════════════════
// 🖼️ RENDER UNIQUE PNGs
// ═════════════════════════════════════════════════════════════════════════════

async function renderAllPNGs(page, frameStateMap) {
  const uniqueStates = new Map();
  
  for (const state of frameStateMap) {
    if (!state) continue;
    
    const fadeStage = state.fade_progress >= 1.0 ? "full" : Math.floor(state.fade_progress * 3);
    const key = `c${state.chunk_idx}_w${state.current_word_idx}_f${fadeStage}`;
    
    if (!uniqueStates.has(key)) {
      uniqueStates.set(key, state);
    }
  }
  
  console.log(`\n  📸 ${uniqueStates.size} unique states`);
  
  const initHtml = buildKaraokeHTML({
    chunk:           { words: ["تحميل"], hasPower: false },
    currentWordIdx:  0,
    fadeProgress:    1.0,
  });
  writeFileSync(`${TMP}/init.html`, initHtml, "utf-8");
  await page.goto(`file://${TMP}/init.html`, { waitUntil: "networkidle" });
  await page.waitForTimeout(2500);
  console.log("  ✅ Fonts loaded");
  
  const pngCache = new Map();
  let rendered = 0;
  
  for (const [key, state] of uniqueStates) {
    const html = buildKaraokeHTML({
      chunk:           state.chunk,
      currentWordIdx:  state.current_word_idx,
      fadeProgress:    state.fade_progress,
    });
    
    const htmlPath = `${TMP}/${key}.html`;
    writeFileSync(htmlPath, html, "utf-8");
    await page.goto(`file://${htmlPath}`, { waitUntil: "load" });
    await page.waitForTimeout(40);
    
    const pngPath = `${TMP}/${key}.png`;
    await page.screenshot({ 
      path: pngPath, 
      type: "png", 
      omitBackground: true 
    });
    pngCache.set(key, pngPath);
    rendered++;
    
    if (rendered % 30 === 0 || rendered === uniqueStates.size) {
      process.stdout.write(`    ${rendered}/${uniqueStates.size} PNGs\n`);
    }
  }
  
  return pngCache;
}

// ═════════════════════════════════════════════════════════════════════════════
// BUILD FRAME DIRECTORY
// ═════════════════════════════════════════════════════════════════════════════

function buildFrameDir(clipFrameMap, pngCache, idx) {
  const dir = `${TMP}/frames_${idx}`;
  mkdirSync(dir, { recursive: true });
  
  for (let f = 0; f < clipFrameMap.length; f++) {
    const state = clipFrameMap[f];
    if (!state) continue;
    
    const fadeStage = state.fade_progress >= 1.0 ? "full" : Math.floor(state.fade_progress * 3);
    const key = `c${state.chunk_idx}_w${state.current_word_idx}_f${fadeStage}`;
    
    const src = pngCache.get(key);
    const dest = `${dir}/frame_${String(f).padStart(6,"0")}.png`;
    if (!src) continue;
    
    try { symlinkSync(src, dest); } 
    catch { copyFileSync(src, dest); }
  }
  
  return dir;
}

// ═════════════════════════════════════════════════════════════════════════════
// 🎬 PROCESS BACKGROUND — Zoom In + HOOK
// ═════════════════════════════════════════════════════════════════════════════

function processBackground(videoPath, duration, outPath, idx, isHook = false) {
  
  const probeResult = spawnSync("ffprobe", [
    "-v", "error",
    "-show_entries", "format=duration",
    "-of", "default=noprint_wrappers=1:nokey=1",
    videoPath,
  ], { stdio: ["ignore", "pipe", "pipe"] });
  
  const sourceDuration = parseFloat(probeResult.stdout.toString().trim()) || 0;
  
  const hookTag = isHook ? "🔥 HOOK" : "";
  console.log(`     [bg ${idx}] ${hookTag} source: ${sourceDuration.toFixed(2)}s | need: ${duration.toFixed(2)}s`);
  
  let inputArgs;
  
  if (sourceDuration >= duration + 0.5) {
    inputArgs = ["-i", videoPath];
  } else {
    inputArgs = ["-stream_loop", "-1", "-i", videoPath];
  }
  
  // Zoom in
  const startScale = 1.0;
  const endScale   = isHook ? 1.4 : 1.15;
  const scaleStep  = (endScale - startScale) / duration;
  
  const zoomFilter = 
    `scale=w='trunc((iw*(${startScale}+${scaleStep.toFixed(6)}*t))/2)*2':` +
    `h='trunc((ih*(${startScale}+${scaleStep.toFixed(6)}*t))/2)*2':` +
    `eval=frame`;
  
  const cinematicFilters = [
    "curves=r='0/0 0.3/0.25 0.7/0.78 1/0.92':g='0/0 0.3/0.27 0.7/0.80 1/0.95':b='0/0.05 0.3/0.32 0.7/0.85 1/1.0'",
    "hue=s=0.85",
    isHook 
      ? "eq=contrast=1.20:brightness=0.00:saturation=1.05"
      : "eq=contrast=1.10:brightness=-0.02:saturation=0.95",
    "vignette=PI/4.5",
    "unsharp=5:5:0.6:5:5:0.0",
  ].join(",");
  
  const fadeFilter = isHook
    ? `fade=t=out:st=${(duration - 0.2).toFixed(3)}:d=0.2`
    : `fade=t=in:st=0:d=0.3,fade=t=out:st=${(duration - 0.3).toFixed(3)}:d=0.3`;
  
  const videoFilter = 
    `${zoomFilter},` +
    `crop=${WIDTH}:${HEIGHT}:(iw-${WIDTH})/2:(ih-${HEIGHT})/2,` +
    `setsar=1,` +
    `${cinematicFilters},` +
    `${fadeFilter}`;
  
  const r = spawnSync("ffmpeg", [
    "-y",
    ...inputArgs,
    "-t", duration.toFixed(3),
    "-vf", videoFilter,
    "-r", String(FPS),
    "-c:v", "libx264",
    "-preset", "fast",
    "-crf", isHook ? "18" : "20",
    "-pix_fmt", "yuv420p",
    "-an",
    outPath,
  ], { stdio: ["ignore", "pipe", "pipe"] });
  
  if (r.status !== 0) {
    console.error(`     [bg ${idx}] FAILED:`, r.stderr.toString().slice(-300));
    
    const basicFilter = 
      `scale=${Math.round(WIDTH * 1.1)}:${Math.round(HEIGHT * 1.1)}:force_original_aspect_ratio=increase,` +
      `crop=${WIDTH}:${HEIGHT},setsar=1,${cinematicFilters},${fadeFilter}`;
    
    const r2 = spawnSync("ffmpeg", [
      "-y", "-stream_loop", "-1",
      "-i", videoPath,
      "-t", duration.toFixed(3),
      "-vf", basicFilter,
      "-r", String(FPS),
      "-c:v", "libx264", "-preset", "fast", "-crf", "22",
      "-pix_fmt", "yuv420p", "-an",
      outPath,
    ], { stdio: ["ignore", "pipe", "pipe"] });
    
    if (r2.status !== 0) {
      console.error("❌ Background failed");
      process.exit(1);
    }
  }
  
  return outPath;
}

// ═════════════════════════════════════════════════════════════════════════════
// FFMPEG HELPERS
// ═════════════════════════════════════════════════════════════════════════════

function framesToMov(frameDir, outPath) {
  const r = spawnSync("ffmpeg",[
    "-y","-framerate",String(FPS),
    "-i",`${frameDir}/frame_%06d.png`,
    "-vf",`scale=${WIDTH}:${HEIGHT},format=rgba`,
    "-c:v","png","-an",outPath,
  ],{stdio:["ignore","pipe","pipe"]});
  if (r.status !== 0) { console.error("❌ frames→mov failed"); process.exit(1); }
  return outPath;
}

function overlayOnBackground(bgMp4, captionMov, outPath) {
  const r = spawnSync("ffmpeg",[
    "-y","-i",bgMp4,"-i",captionMov,
    "-filter_complex","[1:v]format=rgba[cap];[0:v][cap]overlay=0:0:format=auto,format=yuv420p[out]",
    "-map","[out]","-c:v","libx264","-preset","fast","-crf","20","-pix_fmt","yuv420p","-an",outPath,
  ],{stdio:["ignore","pipe","pipe"]});
  if (r.status !== 0) { console.error("❌ Overlay failed"); process.exit(1); }
  return outPath;
}

function xfadeConcat(clipPaths, clipDurations) {
  if (clipPaths.length === 1) return clipPaths[0];
  
  const TRANSITIONS = ["fade", "fadeblack", "dissolve", "wiperight", "slideleft"];
  const XFADE = 0.35;
  
  const filters = [];
  let offset = 0, last = "[0:v]";
  
  for (let i = 1; i < clipPaths.length; i++) {
    offset += clipDurations[i-1] - XFADE;
    const out = i === clipPaths.length-1 ? "[vout]" : `[v${i}]`;
    const trans = TRANSITIONS[(i-1) % TRANSITIONS.length];
    filters.push(`${last}[${i}:v]xfade=transition=${trans}:duration=${XFADE}:offset=${offset.toFixed(3)}${out}`);
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
    console.log(`⚠️  Video shorter - looping...`);
    const looped = `${TMP}/video_looped.mp4`;
    const r = spawnSync("ffmpeg", [
      "-y", "-stream_loop", "-1",
      "-i", videoPath,
      "-t", aDur.toFixed(3),
      "-c:v", "libx264", "-preset", "fast", "-crf", "22",
      "-pix_fmt", "yuv420p", "-an",
      looped,
    ], { stdio: ["ignore", "pipe", "pipe"] });
    
    if (r.status === 0) {
      finalVideo = looped;
    }
  }
  
  const r = spawnSync("ffmpeg", [
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
  
  if (r.status !== 0) { 
    console.error("❌ Merge failed:", r.stderr.toString().slice(-300));
    process.exit(1); 
  }
  console.log(`✅ Final: ${aDur.toFixed(3)}s → ${outPath}`);
}

// ═════════════════════════════════════════════════════════════════════════════
// 🎯 MAIN
// ═════════════════════════════════════════════════════════════════════════════

async function main() {
  console.log("\n🚀 Starting KCS Renderer + Per-Word Sync\n");

  const frameStateMap = buildFrameStateMap();

  const browser = await chromium.launch({
    headless: true,
    args: [
      "--no-sandbox","--disable-setuid-sandbox","--disable-dev-shm-usage",
      "--disable-gpu","--no-zygote","--font-render-hinting=none","--lang=ar,en"
    ],
  });
  const context = await browser.newContext({
    viewport: { width: WIDTH, height: HEIGHT },
    deviceScaleFactor: 1,
    locale: "ar-SA",
  });
  const page = await context.newPage();

  console.log("🖼️  Rendering PNGs...");
  const pngCache = await renderAllPNGs(page, frameStateMap);
  await browser.close();
  console.log(`✅ ${pngCache.size} PNGs done\n`);

  const totalClips = Math.max(1, Math.floor(effectiveDuration / clip_duration));
  const actualClipDuration = effectiveDuration / totalClips;
  
  console.log(`\n📊 Splitting into ${totalClips} clips × ${actualClipDuration.toFixed(2)}s each`);
  console.log(`🎥 Available videos: ${videos.length}`);
  
  const finalClips = [], clipDurations = [];

  console.log("\n🎬 Processing clips...");
  
  for (let i = 0; i < totalClips; i++) {
    const clipStart = i * actualClipDuration;
    const clipEnd   = Math.min((i + 1) * actualClipDuration, effectiveDuration);
    const clipDur   = clipEnd - clipStart;
    const nFrames   = Math.ceil(clipDur * FPS);
    const startF    = Math.floor(clipStart * FPS);
    const clipMap   = frameStateMap.slice(startF, startF + nFrames);
    
    const isHook = (i === 0 && has_hook);
    const videoIdx = i % videos.length;
    const videoSrc = videos[videoIdx];
    
    const clipLabel = isHook ? "🔥 HOOK" : `clip ${i+1}`;
    process.stdout.write(`  [${i+1}/${totalClips}] ${clipDur.toFixed(2)}s ${clipLabel}... `);

    const frameDir   = buildFrameDir(clipMap, pngCache, i);
    const captionMov = `${TMP}/caption_${i}.mov`;
    framesToMov(frameDir, captionMov);

    const bgMp4 = `${TMP}/bg_${String(i).padStart(3,"0")}.mp4`;
    processBackground(videoSrc, clipDur, bgMp4, i, isHook);

    const finalClip = `${TMP}/final_${String(i).padStart(3,"0")}.mp4`;
    overlayOnBackground(bgMp4, captionMov, finalClip);
    finalClips.push(finalClip);
    clipDurations.push(clipDur);
    process.stdout.write("✓\n");
  }

  console.log(`\n✨ Concatenating...`);
  const dissolved = xfadeConcat(finalClips, clipDurations);

  console.log("🎵 Merging audio...");
  mergeAudio(dissolved, audio, outputPath);
  console.log(`\n🎉 Final video → ${outputPath}\n`);
}

main().catch((err) => {
  console.error("\n❌ Fatal error in render.mjs:");
  console.error(err);
  process.exit(1);
});
