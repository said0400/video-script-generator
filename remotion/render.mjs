// remotion/render.mjs — Full Paragraph Display
// ✨ خلفية صفراء واحدة تجمع كل النص
// ✨ نص أسود + كلمة حالية حمراء مشعة
// ✨ خط Cairo عصري
// ✨ مزامنة 100% من WhisperX

import { readFileSync, writeFileSync, mkdirSync, copyFileSync,
         symlinkSync, existsSync } from "fs";
import { spawnSync } from "child_process";
import { chromium } from "playwright";

// ═════════════════════════════════════════════════════════════════════════════
// READ MANIFEST
// ═════════════════════════════════════════════════════════════════════════════

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

// ✅ FIX: حد الصمت — فجوة أكبر من هذا بين جملتين = إخفاء النص
const SILENCE_THRESHOLD = 0.15;

const safeOut = outputPath.replace(/[^a-zA-Z0-9]/g, "_").replace(/_+/g, "_").slice(-22);
const TMP     = `/tmp/vsg_${safeOut}`;
mkdirSync(TMP, { recursive: true });

console.log(`📌 Title: ${emoji_left} ${display_title} ${emoji_right}`);
console.log(`🎬 Clip: ${clip_duration}s | Hook: ${has_hook ? "YES" : "NO"}`);
console.log(`🎯 Aligned: ${aligned.length} segments`);

// ═════════════════════════════════════════════════════════════════════════════
// HELPERS
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

console.log(`📋 Sentences: ${sentences.length}`);
console.log(`🎵 Audio: ${realAudioDuration.toFixed(3)}s`);
console.log(`🎞️  Frames: ${totalFrames}`);

const isArabicText = t => /[\u0600-\u06FF]/.test(t);
const esc = s => (s||"").toString()
  .replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");

// ═════════════════════════════════════════════════════════════════════════════
// POWER WORDS
// ═════════════════════════════════════════════════════════════════════════════

function normalizeWord(word) {
  if (!word) return "";
  return word.toString()
    .replace(/[.,!?؟،;:"'(){}[\]<>«»…]/g, "")
    .trim().toLowerCase();
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

// ═════════════════════════════════════════════════════════════════════════════
// BUILD FRAME STATE MAP — فقرة كاملة لكل segment
// ═════════════════════════════════════════════════════════════════════════════

function buildFrameStateMap() {
  let segments = [];

  if (aligned && aligned.length > 0) {
    segments = aligned.map(seg => ({
      sentence: seg.sentence || "",
      start:    seg.start || 0,
      end:      seg.end || 0,
      words:    (seg.words || []).map(w => ({
        word:  w.word || "",
        start: w.start || 0,
        end:   w.end || 0,
      })),
    }));
  }

  // Fallback: إذا لم يوجد aligned
  if (segments.length === 0) {
    console.log("⚠️  No aligned segments - using equal split");
    const perSentence = effectiveDuration / Math.max(sentences.length, 1);

    for (let i = 0; i < sentences.length; i++) {
      const words = sentences[i].split(/\s+/).filter(Boolean);
      const start = i * perSentence;
      const end   = (i + 1) * perSentence;
      const wordDur = words.length > 0 ? (end - start) / words.length : 1;

      segments.push({
        sentence: sentences[i],
        start:    start,
        end:      end,
        words:    words.map((w, j) => ({
          word:  w,
          start: start + j * wordDur,
          end:   start + (j + 1) * wordDur,
        })),
      });
    }
  }

  // Log
  console.log(`\n📊 Segments (${segments.length}):`);
  segments.forEach((seg, i) => {
    const preview = seg.sentence ? seg.sentence.substring(0, 50) : "";
    const wc = seg.words ? seg.words.length : 0;
    console.log(`  ${i+1}. [${seg.start.toFixed(2)}s → ${seg.end.toFixed(2)}s] ${wc}w: "${preview}..."`);
  });

  // Build frame map
  const map = new Array(totalFrames).fill(null);

  for (let f = 0; f < totalFrames; f++) {
    const t = f / FPS;

    // ابحث عن الـ segment الحالي
    let currentSeg    = null;
    let currentSegIdx = -1;

    for (let i = 0; i < segments.length; i++) {
      if (t >= segments[i].start && t < segments[i].end) {
        currentSeg    = segments[i];
        currentSegIdx = i;
        break;
      }
    }

    // ✅ FIX: منطق الـ fallback المُصحَّح مع دعم الصمت
    if (!currentSeg) {

      // قبل بداية أول جملة — شاشة فارغة
      if (segments.length > 0 && t < segments[0].start) {
        map[f] = {
          segment_idx:      -1,
          segment:          null,
          current_word_idx: -1,
          fade_progress:    0,
        };
        continue;
      }

      // بعد نهاية آخر جملة — أبقِ آخر جملة ظاهرة
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

      // ✅ FIX: بين جملتين — تحقق من حجم الفجوة
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

      const gapSize = (prevSeg && nextSeg)
        ? nextSeg.start - prevSeg.end
        : 999;

      if (gapSize > SILENCE_THRESHOLD) {
        // ✅ صمت حقيقي — أخفِ النص
        map[f] = {
          segment_idx:      -1,
          segment:          null,
          current_word_idx: -1,
          fade_progress:    0,
        };
      } else {
        // فجوة صغيرة — استمر بآخر جملة
        const fallbackSeg = prevSeg || segments[0];
        const fallbackIdx = prevSeg ? segments.indexOf(prevSeg) : 0;
        map[f] = {
          segment_idx:      fallbackIdx,
          segment:          fallbackSeg,
          current_word_idx: (fallbackSeg.words?.length ?? 1) - 1,
          fade_progress:    1.0,
        };
      }
      continue;
    }

    // ابحث عن الكلمة الحالية
    let currentWordIdx = -1;
    const segWords = currentSeg.words || [];

    for (let i = 0; i < segWords.length; i++) {
      if (t >= segWords[i].start && t <= segWords[i].end) {
        currentWordIdx = i;
        break;
      }
      if (t > segWords[i].end) {
        currentWordIdx = i;
      }
    }

    // Fade in
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

  // Debug
  console.log("\n📊 Sample sync:");
  [0, 0.25, 0.5, 0.75].forEach(pct => {
    const f = Math.floor(totalFrames * pct);
    if (map[f]) {
      const t    = (f / FPS).toFixed(2);
      const seg  = map[f].segment;
      const wIdx = map[f].current_word_idx;
      const word = (seg && seg.words && wIdx >= 0 && seg.words[wIdx])
        ? seg.words[wIdx].word
        : (seg ? "---" : "SILENCE");
      console.log(`  ${t}s → seg ${map[f].segment_idx} | word "${word}"`);
    }
  });

  return map;
}

// ═════════════════════════════════════════════════════════════════════════════
// HTML BUILDER — خلفية صفراء واحدة + نص أسود + كلمة حمراء مشعة
// ═════════════════════════════════════════════════════════════════════════════

function buildHTML(opts) {
  const { segment, currentWordIdx, fadeProgress } = opts;

  // ✅ FIX: segment = null يعني صمت — شاشة شفافة فارغة
  if (!segment || !segment.words || segment.words.length === 0) {
    return `<!DOCTYPE html><html><head><meta charset="UTF-8"/></head>` +
      `<body style="width:${WIDTH}px;height:${HEIGHT}px;background:transparent;margin:0;padding:0;"></body></html>`;
  }

  const segWords     = segment.words || [];
  const allWordsText = segWords.map(w => w.word);

  const ar        = isArabicText(allWordsText.join(" "));
  const dir       = ar ? "rtl" : "ltr";
  const font      = ar ? `"Cairo", sans-serif` : `"Inter", sans-serif`;

  const titleAr   = isArabicText(display_title);
  const titleFont = titleAr ? `"Cairo", sans-serif` : `"Inter", sans-serif`;
  const titleDir  = titleAr ? "rtl" : "ltr";

  // حجم الخط حسب عدد الكلمات
  let fontSize;
  const wc = allWordsText.length;
  if (wc <= 5)       fontSize = ar ? 85 : 80;
  else if (wc <= 10) fontSize = ar ? 72 : 68;
  else if (wc <= 15) fontSize = ar ? 60 : 56;
  else if (wc <= 20) fontSize = ar ? 52 : 48;
  else               fontSize = ar ? 44 : 40;

  // Power word solo check
  const showPowerSolo = allWordsText.length === 1 && isPowerWord(allWordsText[0]);

  // بناء الكلمات مع karaoke
  let wordIdx = 0;

  const wordsHTML = allWordsText.map(word => {
    const isCurrent = wordIdx === currentWordIdx;
    const isPast    = wordIdx < currentWordIdx;

    let color;
    let textShadow;
    let opacity;

    if (isCurrent) {
      // ✨ الكلمة الحالية: حمراء فاتحة مشعة مضيئة
      color      = "#FF1744";
      textShadow = "0 0 20px rgba(255,23,68,0.9), 0 0 40px rgba(255,23,68,0.6), 0 0 60px rgba(255,23,68,0.3)";
      opacity    = 1.0;
    } else if (isPast) {
      // الكلمات السابقة: أسود
      color      = "#000000";
      textShadow = "none";
      opacity    = 1.0;
    } else {
      // الكلمات القادمة: رمادي غامق
      color      = "#555555";
      textShadow = "none";
      opacity    = 0.75;
    }

    wordIdx++;

    return `<span class="word" style="color:${color};opacity:${opacity};text-shadow:${textShadow};">${esc(word)}</span>`;
  }).join(" ");

  // بناء mainContent
  let mainContent;

  if (showPowerSolo) {
    mainContent = `
      <div class="power-word-container">
        <span class="power-word-text">${esc(allWordsText[0])}</span>
      </div>
    `;
  } else {
    mainContent = `
      <div class="text-container">
        <div class="text-bg">${wordsHTML}</div>
      </div>
    `;
  }

  return `<!DOCTYPE html>
<html lang="${ar?"ar":"en"}">
<head>
  <meta charset="UTF-8"/>
  <link href="https://fonts.googleapis.com/css2?family=Cairo:wght@700;800;900&family=Inter:wght@700;800;900&display=swap" rel="stylesheet"/>
  <style>
    *{margin:0;padding:0;box-sizing:border-box;}
    html,body{
      width:${WIDTH}px;height:${HEIGHT}px;
      overflow:hidden;background:transparent;
    }

    /* تدرج علوي */
    .overlay-top{
      position:absolute;top:0;left:0;right:0;height:35%;
      background:linear-gradient(to bottom,rgba(0,0,0,0.7) 0%,rgba(0,0,0,0.3) 60%,transparent 100%);
      pointer-events:none;z-index:1;
    }

    /* تدرج سفلي */
    .overlay-bottom{
      position:absolute;bottom:0;left:0;right:0;height:45%;
      background:linear-gradient(to top,rgba(0,0,0,0.7) 0%,rgba(0,0,0,0.3) 60%,transparent 100%);
      pointer-events:none;z-index:1;
    }

    /* ══════════════════════════════════════════════════════ */
    /* العنوان                                               */
    /* ══════════════════════════════════════════════════════ */
    .title-container{
      position:absolute;top:400px;left:50%;transform:translateX(-50%);
      width:90%;max-width:980px;direction:${titleDir};text-align:center;z-index:20;
    }
    .title-box{
      display:inline-block;
      background:#FF0000;
      padding:20px 45px;
      border-radius:9999px;
      box-shadow:0 0 50px rgba(255,0,0,0.7),0 10px 30px rgba(0,0,0,0.5);
    }
    .title-text{
      font-family:${titleFont};
      font-size:${titleAr?"48px":"44px"};
      font-weight:900;
      color:#FFFFFF;
      display:inline-flex;align-items:center;gap:16px;white-space:nowrap;
      text-shadow:0 2px 6px rgba(0,0,0,0.4);
    }
    .title-emoji{font-size:${titleAr?"54px":"50px"};}

    /* ══════════════════════════════════════════════════════ */
    /* ✨ النص — خلفية صفراء واحدة تجمع كل النص            */
    /* ══════════════════════════════════════════════════════ */
    .text-container{
      position:absolute;
      left:50%;
      top:62%;
      transform:translate(-50%,-50%);
      width:85%;
      max-width:920px;
      direction:${dir};
      text-align:center;
      z-index:10;
      opacity:${fadeProgress};
    }

    /* ✨ خلفية واحدة (inline-block = مربع واحد يجمع كل النص) */
    .text-bg{
      display:inline-block;
      background:rgba(255, 215, 0, 0.95);
      font-family:${font};
      font-size:${fontSize}px;
      font-weight:900;
      line-height:1.6;
      padding:25px 35px;
      border-radius:24px;
      box-shadow:0 8px 30px rgba(0,0,0,0.3);
      max-width:100%;
      word-wrap:break-word;
      overflow-wrap:break-word;
    }

    .word{
      display:inline;
      transition:color 0.1s ease-out, opacity 0.1s ease-out, text-shadow 0.15s ease-out;
    }

    /* ══════════════════════════════════════════════════════ */
    /* الكلمة القوية وحدها                                   */
    /* ══════════════════════════════════════════════════════ */
    .power-word-container{
      position:absolute;left:50%;top:62%;
      transform:translate(-50%,-50%);
      direction:${dir};text-align:center;
      z-index:10;opacity:${fadeProgress};
    }
    .power-word-text{
      display:inline-block;
      background:#FF0000;
      color:#FFD700;
      font-family:${font};
      font-size:${ar?"150px":"140px"};
      font-weight:900;
      line-height:1.5;
      padding:15px 40px;
      border-radius:9999px;
      box-shadow:0 0 80px rgba(255,0,0,0.8),0 15px 40px rgba(0,0,0,0.6);
    }
  </style>
</head>
<body>
  <div class="overlay-top"></div>
  <div class="overlay-bottom"></div>

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

// ═════════════════════════════════════════════════════════════════════════════
// RENDER PNGs
// ═════════════════════════════════════════════════════════════════════════════

async function renderAllPNGs(page, frameStateMap) {
  const uniqueStates = new Map();

  for (const state of frameStateMap) {
    if (!state) continue;

    // ✅ FIX: segment=null (صمت) له key خاص
    if (!state.segment) {
      const key = "silence";
      if (!uniqueStates.has(key)) uniqueStates.set(key, state);
      continue;
    }

    const fadeStage = state.fade_progress >= 1.0 ? "full" : Math.floor(state.fade_progress * 3);
    const key = `s${state.segment_idx}_w${state.current_word_idx}_f${fadeStage}`;
    if (!uniqueStates.has(key)) uniqueStates.set(key, state);
  }

  console.log(`\n  📸 ${uniqueStates.size} unique states`);

  // Warmup fonts
  const initHtml = buildHTML({
    segment:       { words: [{ word: "تحميل", start: 0, end: 1 }], start: 0, end: 1 },
    currentWordIdx: 0,
    fadeProgress:  1.0,
  });
  writeFileSync(`${TMP}/init.html`, initHtml, "utf-8");
  await page.goto(`file://${TMP}/init.html`, { waitUntil: "networkidle" });
  await page.waitForTimeout(2500);
  console.log("  ✅ Fonts loaded");

  const pngCache = new Map();
  let rendered   = 0;

  for (const [key, state] of uniqueStates) {
    const html = buildHTML({
      segment:       state.segment,
      currentWordIdx: state.current_word_idx,
      fadeProgress:  state.fade_progress,
    });

    const htmlPath = `${TMP}/${key}.html`;
    writeFileSync(htmlPath, html, "utf-8");
    await page.goto(`file://${htmlPath}`, { waitUntil: "load" });
    await page.waitForTimeout(40);

    const pngPath = `${TMP}/${key}.png`;
    await page.screenshot({ path: pngPath, type: "png", omitBackground: true });
    pngCache.set(key, pngPath);
    rendered++;

    if (rendered % 30 === 0 || rendered === uniqueStates.size) {
      process.stdout.write(`    ${rendered}/${uniqueStates.size} PNGs\n`);
    }
  }

  return pngCache;
}

// ═════════════════════════════════════════════════════════════════════════════
// BUILD FRAME DIR
// ═════════════════════════════════════════════════════════════════════════════

function buildFrameDir(clipFrameMap, pngCache, idx) {
  const dir = `${TMP}/frames_${idx}`;
  mkdirSync(dir, { recursive: true });

  for (let f = 0; f < clipFrameMap.length; f++) {
    const state = clipFrameMap[f];
    if (!state) continue;

    // ✅ FIX: صمت → استخدم key "silence"
    let key;
    if (!state.segment) {
      key = "silence";
    } else {
      const fadeStage = state.fade_progress >= 1.0 ? "full" : Math.floor(state.fade_progress * 3);
      key = `s${state.segment_idx}_w${state.current_word_idx}_f${fadeStage}`;
    }

    const src  = pngCache.get(key);
    const dest = `${dir}/frame_${String(f).padStart(6,"0")}.png`;
    if (!src) continue;

    try { symlinkSync(src, dest); }
    catch { copyFileSync(src, dest); }
  }

  return dir;
}

// ═════════════════════════════════════════════════════════════════════════════
// PROCESS BACKGROUND — Zoom + Cinematic Filters
// ═════════════════════════════════════════════════════════════════════════════

function processBackground(videoPath, duration, outPath, idx, isHook = false) {
  const probeResult = spawnSync("ffprobe", [
    "-v", "error", "-show_entries", "format=duration",
    "-of", "default=noprint_wrappers=1:nokey=1", videoPath,
  ], { stdio: ["ignore", "pipe", "pipe"] });

  const sourceDuration = parseFloat(probeResult.stdout.toString().trim()) || 0;

  let inputArgs;
  if (sourceDuration >= duration + 0.5) {
    inputArgs = ["-i", videoPath];
  } else {
    inputArgs = ["-stream_loop", "-1", "-i", videoPath];
  }

  const startScale = 1.0;
  const endScale   = isHook ? 1.4 : 1.15;
  const scaleStep  = (endScale - startScale) / Math.max(duration, 0.1);

  const zoomFilter =
    `scale=w='trunc((iw*(${startScale}+${scaleStep.toFixed(6)}*t))/2)*2':` +
    `h='trunc((ih*(${startScale}+${scaleStep.toFixed(6)}*t))/2)*2':eval=frame`;

  const cinematicFilters = [
    "curves=r='0/0 0.3/0.25 0.7/0.78 1/0.92':g='0/0 0.3/0.27 0.7/0.80 1/0.95':b='0/0.05 0.3/0.32 0.7/0.85 1/1.0'",
    "hue=s=0.85",
    isHook ? "eq=contrast=1.20:brightness=0.00:saturation=1.05"
           : "eq=contrast=1.10:brightness=-0.02:saturation=0.95",
    "vignette=PI/4.5",
    "unsharp=5:5:0.6:5:5:0.0",
  ].join(",");

  const safeDur    = Math.max(duration, 0.5);
  const fadeFilter = isHook
    ? `fade=t=out:st=${(safeDur-0.2).toFixed(3)}:d=0.2`
    : `fade=t=in:st=0:d=0.3,fade=t=out:st=${(safeDur-0.3).toFixed(3)}:d=0.3`;

  const videoFilter =
    `${zoomFilter},crop=${WIDTH}:${HEIGHT}:(iw-${WIDTH})/2:(ih-${HEIGHT})/2,` +
    `setsar=1,${cinematicFilters},${fadeFilter}`;

  let r = spawnSync("ffmpeg", [
    "-y", ...inputArgs, "-t", duration.toFixed(3),
    "-vf", videoFilter, "-r", String(FPS),
    "-c:v", "libx264", "-preset", "fast", "-crf", isHook ? "18" : "20",
    "-pix_fmt", "yuv420p", "-an", outPath,
  ], { stdio: ["ignore", "pipe", "pipe"] });

  if (r.status !== 0) {
    // Fallback بسيط
    const basicFilter =
      `scale=${Math.round(WIDTH*1.1)}:${Math.round(HEIGHT*1.1)}:force_original_aspect_ratio=increase,` +
      `crop=${WIDTH}:${HEIGHT},setsar=1,${cinematicFilters},${fadeFilter}`;

    r = spawnSync("ffmpeg", [
      "-y", "-stream_loop", "-1", "-i", videoPath,
      "-t", duration.toFixed(3), "-vf", basicFilter,
      "-r", String(FPS), "-c:v", "libx264", "-preset", "fast", "-crf", "22",
      "-pix_fmt", "yuv420p", "-an", outPath,
    ], { stdio: ["ignore", "pipe", "pipe"] });

    if (r.status !== 0) {
      console.error(`❌ BG failed for clip ${idx}`);
      // Last resort: فقط scale
      spawnSync("ffmpeg", [
        "-y", "-stream_loop", "-1", "-i", videoPath,
        "-t", duration.toFixed(3),
        "-vf", `scale=${WIDTH}:${HEIGHT}:force_original_aspect_ratio=increase,crop=${WIDTH}:${HEIGHT},setsar=1`,
        "-r", String(FPS), "-c:v", "libx264", "-preset", "fast", "-crf", "25",
        "-pix_fmt", "yuv420p", "-an", outPath,
      ], { stdio: ["ignore", "pipe", "pipe"] });
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

  if (r.status !== 0) {
    console.error("❌ framesToMov failed");
    console.error(r.stderr ? r.stderr.toString().slice(-200) : "");
  }
  return outPath;
}

function overlayOnBackground(bgMp4, captionMov, outPath) {
  const r = spawnSync("ffmpeg",[
    "-y","-i",bgMp4,"-i",captionMov,
    "-filter_complex","[1:v]format=rgba[cap];[0:v][cap]overlay=0:0:format=auto,format=yuv420p[out]",
    "-map","[out]","-c:v","libx264","-preset","fast","-crf","20","-pix_fmt","yuv420p","-an",outPath,
  ],{stdio:["ignore","pipe","pipe"]});

  if (r.status !== 0) {
    console.error("❌ overlay failed");
    console.error(r.stderr ? r.stderr.toString().slice(-200) : "");
  }
  return outPath;
}

function xfadeConcat(clipPaths, clipDurations) {
  if (clipPaths.length === 0) return "";
  if (clipPaths.length === 1) return clipPaths[0];

  const TRANSITIONS = ["fade","fadeblack","dissolve","wiperight","slideleft"];
  const XFADE = 0.35;

  const filters = [];
  let offset = 0, last = "[0:v]";

  for (let i = 1; i < clipPaths.length; i++) {
    offset += clipDurations[i-1] - XFADE;
    if (offset < 0) offset = 0;
    const out   = i === clipPaths.length-1 ? "[vout]" : `[v${i}]`;
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
    console.error("⚠️  xfade failed - using concat");
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

  // إذا الفيديو أقصر من الصوت → loop
  if (vDur < aDur - 0.3) {
    console.log(`⚠️  Video shorter - looping...`);
    const looped = `${TMP}/video_looped.mp4`;
    const r = spawnSync("ffmpeg", [
      "-y", "-stream_loop", "-1", "-i", videoPath,
      "-t", aDur.toFixed(3), "-c:v", "libx264", "-preset", "fast", "-crf", "22",
      "-pix_fmt", "yuv420p", "-an", looped,
    ], { stdio: ["ignore", "pipe", "pipe"] });
    if (r.status === 0) {
      finalVideo = looped;
      console.log(`  ✅ Looped to ${aDur.toFixed(2)}s`);
    }
  }

  // دمج
  const r = spawnSync("ffmpeg", [
    "-y", "-i", finalVideo, "-i", audioPath,
    "-map", "0:v:0", "-map", "1:a:0",
    "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
    "-t", aDur.toFixed(3), outPath,
  ], { stdio: ["ignore", "pipe", "pipe"] });

  if (r.status !== 0) {
    console.error("❌ Merge failed");
    console.error(r.stderr ? r.stderr.toString().slice(-200) : "");
    process.exit(1);
  }

  console.log(`✅ Final: ${aDur.toFixed(3)}s → ${outPath}`);
}

// ═════════════════════════════════════════════════════════════════════════════
// 🎯 MAIN
// ═════════════════════════════════════════════════════════════════════════════

async function main() {
  console.log("\n🚀 Starting Renderer — Yellow BG + Red Karaoke\n");

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

  // حساب عدد المقاطع
  const totalClips        = Math.max(1, Math.floor(effectiveDuration / clip_duration));
  const actualClipDuration = effectiveDuration / totalClips;

  console.log(`📊 ${totalClips} clips × ${actualClipDuration.toFixed(2)}s`);
  console.log(`🎥 Videos: ${videos.length}`);

  const finalClips   = [];
  const clipDurations = [];

  console.log("\n🎬 Processing clips...");

  for (let i = 0; i < totalClips; i++) {
    const clipStart = i * actualClipDuration;
    const clipEnd   = Math.min((i + 1) * actualClipDuration, effectiveDuration);
    const clipDur   = Math.max(clipEnd - clipStart, 0.5);
    const nFrames   = Math.ceil(clipDur * FPS);
    const startF    = Math.floor(clipStart * FPS);
    const clipMap   = frameStateMap.slice(startF, startF + nFrames);

    const isHook   = (i === 0 && has_hook);
    const videoIdx = i % videos.length;
    const videoSrc = videos[videoIdx];

    process.stdout.write(`  [${i+1}/${totalClips}] ${clipDur.toFixed(2)}s ${isHook?"🔥HOOK":""}... `);

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
