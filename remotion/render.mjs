// remotion/render.mjs — Karaoke Cinematic System (KCS)
// ✨ نظام نظيف: نص فقط + ألوان للكلمات القوية + فلاتر سينمائية

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
  sentences,
  audio,
  videos,
  duration_s,
  power_words = [],
  accent_colors = [],
  word_timeline = [],
  aligned = [],
  lang = "ar",
} = props;

const FPS    = 30;
const WIDTH  = 1080;
const HEIGHT = 1920;

const safeOut = outputPath.replace(/[^a-zA-Z0-9]/g, "_").replace(/_+/g, "_").slice(-22);
const TMP     = `/tmp/vsg_${safeOut}`;
mkdirSync(TMP, { recursive: true });

// ═════════════════════════════════════════════════════════════════════════════
// 🎯 KCS CONFIGURATION (Karaoke Cinematic System)
// ═════════════════════════════════════════════════════════════════════════════

const KCS = {
  // كلمات لكل عرض (chunk)
  WORDS_PER_CHUNK_MIN: 3,
  WORDS_PER_CHUNK_MAX: 4,
  
  // التقسيم على سطرين
  MAX_WORDS_PER_LINE: 2,
  
  // الأحجام (موزونة - ليست عملاقة)
  NORMAL_SIZE_AR:  78,    // كلمة عادية عربي
  NORMAL_SIZE_EN:  74,    // كلمة عادية إنجليزي
  POWER_SIZE_AR:   110,   // كلمة قوية عربي (أكبر بـ 40%)
  POWER_SIZE_EN:   105,   // كلمة قوية إنجليزي
  
  // الحركات (سلسة وسريعة)
  FADE_IN_FRAMES:  4,     // 0.13s
  
  // المسافات
  LINE_HEIGHT:     1.4,
  WORD_GAP:        18,    // px بين الكلمات
};

// ═════════════════════════════════════════════════════════════════════════════
// 🎨 COLORS
// ═════════════════════════════════════════════════════════════════════════════

const DEFAULT_COLORS = [
  "#FFD700",  // ذهبي (الافتراضي للكلمات القوية)
  "#00E5FF",  // سماوي
  "#FF6B00",  // برتقالي
  "#39FF14",  // أخضر نيون
];

const POWER_COLORS = (accent_colors && accent_colors.length >= 2)
  ? accent_colors
  : DEFAULT_COLORS;

// لون مختلف لكل جملة (تنويع بصري)
function getPowerColorForSentence(sIdx) {
  return POWER_COLORS[sIdx % POWER_COLORS.length];
}

console.log(`🎨 Power colors: ${POWER_COLORS.slice(0, 4).join(", ")}`);

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
    // مطابقة جزئية للجذور العربية
    if (pwNorm.length >= 3 && normalized.includes(pwNorm)) return true;
    if (normalized.length >= 3 && pwNorm.includes(normalized)) return true;
    return false;
  });
}

console.log(`🔥 Power Words (${power_words.length}): ${power_words.slice(0, 8).join(", ")}${power_words.length > 8 ? "..." : ""}`);

// ═════════════════════════════════════════════════════════════════════════════
// 📦 CHUNKING — تقسيم الجملة إلى مجموعات من 3-4 كلمات
// ═════════════════════════════════════════════════════════════════════════════

function chunkSentence(sentence) {
  const words = sentence.trim().split(/\s+/).filter(Boolean);
  if (words.length === 0) return [];
  
  const chunks = [];
  let i = 0;
  
  while (i < words.length) {
    // كم كلمة في هذا الـ chunk؟
    const remaining = words.length - i;
    let chunkSize;
    
    if (remaining <= KCS.WORDS_PER_CHUNK_MAX) {
      // باقي قليل - خذه كله
      chunkSize = remaining;
    } else if (remaining <= KCS.WORDS_PER_CHUNK_MAX + 1) {
      // لو أخذنا 4، يبقى 1 (سيء)
      // خذ 3 ليبقى 2
      chunkSize = KCS.WORDS_PER_CHUNK_MIN;
    } else {
      // عادي - 3 أو 4 حسب وجود كلمات قوية
      const slice = words.slice(i, i + KCS.WORDS_PER_CHUNK_MAX);
      const hasPower = slice.some(w => isPowerWord(w));
      
      if (hasPower) {
        // إذا فيها كلمة قوية، خذ 3 فقط (للتركيز عليها)
        chunkSize = 3;
      } else {
        chunkSize = 4;
      }
    }
    
    const chunkWords = words.slice(i, i + chunkSize);
    chunks.push({
      words: chunkWords,
      hasPower: chunkWords.some(w => isPowerWord(w)),
    });
    
    i += chunkSize;
  }
  
  return chunks;
}

// ═════════════════════════════════════════════════════════════════════════════
// 📐 LINE SPLITTING — توزيع الكلمات على سطرين إذا كثيرة
// ═════════════════════════════════════════════════════════════════════════════

function splitChunkIntoLines(words) {
  if (words.length <= KCS.MAX_WORDS_PER_LINE) {
    return [words];  // سطر واحد
  }
  
  // سطرين: قسّم بالنصف
  const mid = Math.ceil(words.length / 2);
  return [
    words.slice(0, mid),
    words.slice(mid),
  ];
}

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
// 🎨 HTML BUILDER — Karaoke Style
// ═════════════════════════════════════════════════════════════════════════════

function buildKaraokeHTML(opts) {
  const {
    chunk,              // {words: [...], hasPower: bool}
    currentWordIdx,     // -1 = none yet, 0 = first word active
    accent,             // لون الكلمات القوية
    fadeProgress,       // 0-1 (للـ fade in)
    sentenceIdx,
    totalSentences,
  } = opts;
  
  const allWords = chunk.words;
  const ar = isArabicText(allWords.join(" "));
  const dir = ar ? "rtl" : "ltr";
  const font = ar
    ? `"Noto Naskh Arabic","Amiri",serif`
    : `"Inter","Helvetica Neue",Arial,sans-serif`;
  
  // قسّم الكلمات على سطر واحد أو سطرين
  const lines = splitChunkIntoLines(allWords);
  
  // بناء HTML لكل سطر
  let wordCounter = 0;
  const linesHTML = lines.map(lineWords => {
    const wordsHTML = lineWords.map(word => {
      const isCurrent = wordCounter === currentWordIdx;
      const isPast    = wordCounter < currentWordIdx;
      const isPower   = isPowerWord(word);
      
      // تحديد الحجم
      const fontSize = isPower
        ? (ar ? `${KCS.POWER_SIZE_AR}px` : `${KCS.POWER_SIZE_EN}px`)
        : (ar ? `${KCS.NORMAL_SIZE_AR}px` : `${KCS.NORMAL_SIZE_EN}px`);
      
      // تحديد اللون
      let color;
      let opacity;
      let textShadow;
      
      if (isCurrent) {
        // الكلمة الحالية - مضيئة
        if (isPower) {
          color = accent;
          textShadow = `
            0 0 30px ${accent},
            0 0 60px ${accent}cc,
            0 0 90px ${accent}88,
            0 6px 25px rgba(0,0,0,1),
            4px 4px 0 rgba(0,0,0,0.9)
          `;
        } else {
          color = "#FFFFFF";
          textShadow = `
            0 0 25px rgba(255,255,255,0.6),
            0 6px 20px rgba(0,0,0,1),
            3px 3px 0 rgba(0,0,0,0.9)
          `;
        }
        opacity = 1;
      } else if (isPast) {
        // الكلمات السابقة (تم نطقها)
        color = isPower ? accent : "#FFFFFF";
        opacity = isPower ? 0.95 : 0.85;
        textShadow = isPower
          ? `0 0 15px ${accent}aa, 0 4px 15px rgba(0,0,0,0.9), 2px 2px 0 rgba(0,0,0,0.8)`
          : `0 4px 15px rgba(0,0,0,0.9), 2px 2px 0 rgba(0,0,0,0.8)`;
      } else {
        // الكلمات القادمة (لم تنطق بعد)
        color = isPower ? `${accent}` : "#FFFFFF";
        opacity = isPower ? 0.55 : 0.40;
        textShadow = `0 4px 12px rgba(0,0,0,0.85), 2px 2px 0 rgba(0,0,0,0.7)`;
      }
      
      const stroke = isPower ? "4px" : "3px";
      
      wordCounter++;
      
      return `<span class="word" style="
        font-size: ${fontSize};
        color: ${color};
        opacity: ${opacity};
        text-shadow: ${textShadow};
        -webkit-text-stroke: ${stroke} rgba(0,0,0,0.95);
        paint-order: stroke fill;
        font-weight: 900;
        line-height: ${KCS.LINE_HEIGHT};
        display: inline-block;
        margin: 0 ${KCS.WORD_GAP / 2}px;
        transition: all 0.15s ease-out;
      ">${esc(word)}</span>`;
    }).join("");
    
    return `<div class="line">${wordsHTML}</div>`;
  }).join("");
  
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
    
    /* تدرج معتم من الأعلى والأسفل */
    .overlay-top{
      position:absolute;
      top:0;left:0;right:0;
      height:30%;
      background:linear-gradient(to bottom,
        rgba(0,0,0,0.75) 0%,
        rgba(0,0,0,0.3) 60%,
        transparent 100%
      );
      pointer-events:none;
      z-index:1;
    }
    
    .overlay-bottom{
      position:absolute;
      bottom:0;left:0;right:0;
      height:50%;
      background:linear-gradient(to top,
        rgba(0,0,0,0.90) 0%,
        rgba(0,0,0,0.6) 40%,
        rgba(0,0,0,0.2) 80%,
        transparent 100%
      );
      pointer-events:none;
      z-index:1;
    }
    
    /* النص في المنتصف */
    .text-container{
      position:absolute;
      left:50%;
      top:50%;
      transform:translate(-50%, -50%);
      width:90%;
      max-width:960px;
      direction:${dir};
      text-align:center;
      z-index:10;
      opacity:${fadeProgress};
    }
    
    .line{
      display:block;
      text-align:center;
      margin-bottom:8px;
    }
    
    .line:last-child{
      margin-bottom:0;
    }
    
    .word{
      vertical-align:middle;
    }
  </style>
</head>
<body>
  <div class="overlay-top"></div>
  <div class="overlay-bottom"></div>
  
  <div class="text-container">
    ${linesHTML}
  </div>
</body>
</html>`;
}

// ═════════════════════════════════════════════════════════════════════════════
// 🎬 BUILD CHUNKS TIMELINE
// ═════════════════════════════════════════════════════════════════════════════

function buildChunksTimeline() {
  const allChunks = [];
  
  sentences.forEach((sentence, sIdx) => {
    const sentenceChunks = chunkSentence(sentence);
    sentenceChunks.forEach((chunk, cIdx) => {
      allChunks.push({
        ...chunk,
        sentence_idx: sIdx,
        chunk_idx:    cIdx,
        global_idx:   allChunks.length,
      });
    });
  });
  
  console.log(`📦 Total chunks: ${allChunks.length}`);
  return allChunks;
}

// ═════════════════════════════════════════════════════════════════════════════
// 🎬 BUILD FRAME STATE MAP
// ═════════════════════════════════════════════════════════════════════════════

function buildFrameStateMap(realDur) {
  const allChunks = buildChunksTimeline();
  const totalChunks = allChunks.length;
  
  if (totalChunks === 0) {
    return [];
  }
  
  // الوقت لكل chunk
  const timePerChunk = realDur / totalChunks;
  
  // الوقت لكل كلمة داخل الـ chunk (للـ karaoke effect)
  const map = new Array(totalFrames).fill(null);
  
  for (let f = 0; f < totalFrames; f++) {
    const t = f / FPS;
    const chunkIdx = Math.min(Math.floor(t / timePerChunk), totalChunks - 1);
    const chunk = allChunks[chunkIdx];
    
    if (!chunk) continue;
    
    // الوقت داخل الـ chunk (0 إلى timePerChunk)
    const chunkStartT = chunkIdx * timePerChunk;
    const tInChunk = t - chunkStartT;
    
    // أي كلمة نطقها الآن؟
    const timePerWord = timePerChunk / chunk.words.length;
    const currentWordIdx = Math.min(
      Math.floor(tInChunk / timePerWord),
      chunk.words.length - 1
    );
    
    // Fade in في أول الـ chunk
    const framesSinceChunkStart = f - Math.floor(chunkStartT * FPS);
    const fadeProgress = Math.min(framesSinceChunkStart / KCS.FADE_IN_FRAMES, 1.0);
    
    map[f] = {
      chunk_idx:        chunkIdx,
      chunk:            chunk,
      current_word_idx: currentWordIdx,
      accent:           getPowerColorForSentence(chunk.sentence_idx),
      fade_progress:    fadeProgress,
      sentence_idx:     chunk.sentence_idx,
    };
  }
  
  return map;
}

// ═════════════════════════════════════════════════════════════════════════════
// 🖼️ RENDER UNIQUE PNGs
// ═════════════════════════════════════════════════════════════════════════════

async function renderAllPNGs(page, frameStateMap) {
  // اجمع الحالات الفريدة فقط
  const uniqueStates = new Map();
  
  for (const state of frameStateMap) {
    if (!state) continue;
    
    // المفتاح يجمع: chunk + current word + fade stage
    const fadeStage = state.fade_progress >= 1.0 ? "full" : Math.floor(state.fade_progress * 4);
    const key = `c${state.chunk_idx}_w${state.current_word_idx}_f${fadeStage}`;
    
    if (!uniqueStates.has(key)) {
      uniqueStates.set(key, state);
    }
  }
  
  console.log(`  📸 ${uniqueStates.size} unique states (Karaoke)`);
  
  // Warm up fonts
  const initHtml = buildKaraokeHTML({
    chunk:           { words: ["تحميل"], hasPower: false },
    currentWordIdx:  0,
    accent:          POWER_COLORS[0],
    fadeProgress:    1.0,
    sentenceIdx:     0,
    totalSentences:  1,
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
      accent:          state.accent,
      fadeProgress:    state.fade_progress,
      sentenceIdx:     state.sentence_idx,
      totalSentences:  sentences.length,
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
    
    const fadeStage = state.fade_progress >= 1.0 ? "full" : Math.floor(state.fade_progress * 4);
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
// 🎬 PROCESS BACKGROUND (CINEMATIC FILTERS)
// ═════════════════════════════════════════════════════════════════════════════

function processBackground(videoPath, duration, outPath, idx) {
  const n = Math.ceil(duration * FPS);
  
  // ✨ زووم سينمائي بطيء وأنيق
  const ZOOM_PATTERNS = [
    // Zoom In بطيء
    `zoompan=z='min(zoom+0.0008,1.25)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=${n}:s=${WIDTH}x${HEIGHT}:fps=${FPS}`,
    // Zoom Out بطيء
    `zoompan=z='if(eq(on,1),1.25,max(zoom-0.0008,1.05))':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=${n}:s=${WIDTH}x${HEIGHT}:fps=${FPS}`,
    // Pan يميناً مع zoom خفيف
    `zoompan=z='min(zoom+0.0005,1.18)':x='iw/2-(iw/zoom/2)+on*0.5':y='ih/2-(ih/zoom/2)':d=${n}:s=${WIDTH}x${HEIGHT}:fps=${FPS}`,
    // Pan يساراً مع zoom خفيف
    `zoompan=z='min(zoom+0.0005,1.18)':x='iw/2-(iw/zoom/2)-on*0.5':y='ih/2-(ih/zoom/2)':d=${n}:s=${WIDTH}x${HEIGHT}:fps=${FPS}`,
  ];
  
  const kb = ZOOM_PATTERNS[idx % ZOOM_PATTERNS.length];
  
  // ✨ فلاتر سينمائية احترافية
  const filters = [
    // 1. تصحيح الألوان (Cinematic Color Grading)
    `curves=r='0/0 0.3/0.25 0.7/0.78 1/0.92':g='0/0 0.3/0.27 0.7/0.80 1/0.95':b='0/0.05 0.3/0.32 0.7/0.85 1/1.0'`,
    
    // 2. تشبع متوسط (لون سينمائي دافئ)
    `hue=s=0.75`,
    
    // 3. تباين أعلى قليلاً
    `eq=contrast=1.08:brightness=-0.02:saturation=0.9`,
    
    // 4. حواف داكنة (Vignette)
    `vignette=PI/4.5`,
    
    // 5. حدّة خفيفة (Sharpness)
    `unsharp=5:5:0.5:5:5:0.0`,
    
    // 6. حبيبية سينمائية خفيفة جداً (Film Grain)
    `noise=alls=4:allf=t`,
  ].join(",");
  
  const fade = `fade=t=in:st=0:d=0.4,fade=t=out:st=${(duration-0.4).toFixed(3)}:d=0.4`;
  
  const full = `scale=${Math.round(WIDTH*1.15)}:${Math.round(HEIGHT*1.15)}:force_original_aspect_ratio=increase,`
             + `crop=${Math.round(WIDTH*1.15)}:${Math.round(HEIGHT*1.15)},${kb},${filters},${fade}`;

  let r = spawnSync("ffmpeg",[
    "-y","-i",videoPath,"-t",duration.toFixed(3),
    "-vf",full,"-r",String(FPS),
    "-c:v","libx264","-preset","fast","-crf","20","-pix_fmt","yuv420p","-an",outPath,
  ],{stdio:["ignore","pipe","pipe"]});

  if (r.status !== 0) {
    // Fallback أبسط
    const simple = `scale=${WIDTH}:${HEIGHT}:force_original_aspect_ratio=increase,`
                 + `crop=${WIDTH}:${HEIGHT},setsar=1,${filters},${fade}`;
    r = spawnSync("ffmpeg",[
      "-y","-i",videoPath,"-t",duration.toFixed(3),
      "-vf",simple,"-r",String(FPS),
      "-c:v","libx264","-preset","fast","-crf","20","-pix_fmt","yuv420p","-an",outPath,
    ],{stdio:["ignore","pipe","pipe"]});
    if (r.status !== 0) { console.error("❌ BG failed"); process.exit(1); }
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
  
  // انتقالات سلسة (لا تشتيت)
  const TRANSITIONS = ["fade", "fadeblack", "dissolve"];
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
    const ext = `${TMP}/video_ext.mp4`;
    const r = spawnSync("ffmpeg",[
      "-y","-i",videoPath,
      "-vf",`tpad=stop_mode=clone:stop_duration=${(aDur-vDur+0.5).toFixed(3)}`,
      "-c:v","libx264","-preset","fast","-crf","22","-pix_fmt","yuv420p","-an",ext,
    ],{stdio:["ignore","pipe","pipe"]});
    if (r.status === 0) finalVideo = ext;
  }
  
  const r = spawnSync("ffmpeg",[
    "-y","-i",finalVideo,"-i",audioPath,
    "-map","0:v:0","-map","1:a:0",
    "-c:v","copy","-c:a","aac","-b:a","192k",
    "-t",aDur.toFixed(3),outPath,
  ],{stdio:["ignore","pipe","pipe"]});
  
  if (r.status !== 0) { console.error("❌ Merge failed"); process.exit(1); }
  console.log(`✅ Final: ${aDur.toFixed(3)}s → ${outPath}`);
}

// ═════════════════════════════════════════════════════════════════════════════
// 🎯 MAIN
// ═════════════════════════════════════════════════════════════════════════════

async function main() {
  console.log("\n🚀 Starting KCS Renderer (Karaoke Cinematic System)\n");

  const frameStateMap = buildFrameStateMap(effectiveDuration);

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

  const sentenceData = (aligned && aligned.length > 0)
    ? aligned
    : sentences.map((s,i) => ({
        sentence: s,
        start:    (effectiveDuration / sentences.length) * i,
        end:      (effectiveDuration / sentences.length) * (i + 1),
      }));

  const finalClips = [], clipDurations = [];

  console.log("🎬 Processing clips with cinematic filters...");
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

  console.log(`\n✨ Concatenating with smooth transitions...`);
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
