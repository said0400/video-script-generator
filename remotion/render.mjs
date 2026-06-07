// remotion/render.mjs — FIXED + VIRAL VISUAL UPGRADE
// ✅ إصلاح: overlayOnBg الآن تنقل الصوت
// ✅ إصلاح: stateKey لا يتغير بـ globalFrame إلا للـ TITLE SLIDE
// ✅ إصلاح: words_only يستخدم bgVideoPath بشكل صحيح
// ✅ تحسين: typography viral مع stroke + scale animation + gradient text

import {
  readFileSync,
  writeFileSync,
  mkdirSync,
  copyFileSync,
  symlinkSync,
} from "fs";
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
  sentences     = [],
  audio,
  videos        = [],
  duration_s    = 0,
  power_words   = [],
  aligned       = [],
  lang          = "ar",
  clip_duration = 3.0,
  has_hook      = false,
  custom_hook   = "",
  analysis      = {},
  mode          = "words_only",
} = props;

const FPS                = 30;
const WIDTH              = 1080;
const HEIGHT             = 1920;
const TITLE_SLIDE_FRAMES = Math.floor(0.6 * FPS);
const HOOK_FRAMES        = Math.floor(3.0 * FPS);

const safeOut = outputPath
  .replace(/[^a-zA-Z0-9]/g, "_")
  .replace(/_+/g, "_")
  .slice(-22);
const TMP = `/tmp/vsg_${safeOut}`;
mkdirSync(TMP, { recursive: true });

console.log(`📌 ${emoji_left} ${display_title} ${emoji_right}`);
console.log(`🌐 Lang: ${lang.toUpperCase()} | Mode: ${mode}`);


// ═══════════════════════════════════════════════════════════════════════════
// EMOTION COLORS — viral palette
// ═══════════════════════════════════════════════════════════════════════════

const EMOTION_COLORS = {
  curiosity: { word: "#FFD700", glow: "rgba(255,215,0,0.5)",   power: "#FF1744", gradient: ["#FFD700","#FF8F00"] },
  fear:      { word: "#FF4444", glow: "rgba(255,68,68,0.5)",   power: "#FFD700", gradient: ["#FF4444","#FF0000"] },
  hope:      { word: "#00E676", glow: "rgba(0,230,118,0.5)",   power: "#FFFFFF", gradient: ["#00E676","#00B248"] },
  joy:       { word: "#FF9100", glow: "rgba(255,145,0,0.5)",   power: "#FFFFFF", gradient: ["#FFD740","#FF6D00"] },
  awe:       { word: "#E040FB", glow: "rgba(224,64,251,0.5)",  power: "#FFD700", gradient: ["#E040FB","#AA00FF"] },
  surprise:  { word: "#40C4FF", glow: "rgba(64,196,255,0.5)",  power: "#FFD700", gradient: ["#40C4FF","#0091EA"] },
  desire:    { word: "#FF1744", glow: "rgba(255,23,68,0.5)",   power: "#FFD700", gradient: ["#FF6EC7","#FF1744"] },
  anger:     { word: "#FF1744", glow: "rgba(255,23,68,0.5)",   power: "#FFD700", gradient: ["#FF6D00","#FF1744"] },
  sadness:   { word: "#82B1FF", glow: "rgba(130,177,255,0.5)", power: "#FFFFFF", gradient: ["#82B1FF","#448AFF"] },
  default:   { word: "#FFFFFF", glow: "rgba(255,255,255,0.4)", power: "#FF1744", gradient: ["#FFFFFF","#E0E0E0"] },
};

const emotion = (analysis.primary_emotion || "").toLowerCase();
const COLORS  = EMOTION_COLORS[emotion] || EMOTION_COLORS.default;


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
const effectiveDuration = realAudioDuration > 1 ? realAudioDuration : duration_s;
const totalFrames       = Math.ceil(effectiveDuration * FPS);

console.log(`🎵 Audio: ${realAudioDuration.toFixed(3)}s | Frames: ${totalFrames}`);

const isArabic = (t) => /[\u0600-\u06FF]/.test(t);
const isFrench = (t) => /[àâçéèêëîïôùûüÿœæ]/i.test(t);

function getFontFamily(text) {
  return isArabic(text)
    ? `"Noto Naskh Arabic", "Amiri", serif`
    : `"Noto Sans", "DejaVu Sans", sans-serif`;
}

function getDir(text)  { return isArabic(text) ? "rtl" : "ltr"; }
function getLang(text) {
  if (isArabic(text)) return "ar";
  if (isFrench(text)) return "fr";
  return "en";
}

const esc = (s) =>
  (s || "").toString()
    .replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");

function normalizeWord(w) {
  return (w || "").toString()
    .replace(/[.,!?؟،;:"'(){}[\]<>«»…]/g, "")
    .trim().toLowerCase();
}

function isPowerWord(w) {
  if (!power_words.length) return false;
  const n = normalizeWord(w);
  if (n.length < 2) return false;
  return power_words.some((pw) => {
    const p = normalizeWord(pw);
    return p && (n === p || (p.length >= 3 && n.includes(p)) || (n.length >= 3 && p.includes(n)));
  });
}


// ═══════════════════════════════════════════════════════════════════════════
// WORD LIST
// ═══════════════════════════════════════════════════════════════════════════

function buildWordList() {
  const words = [];

  for (const seg of aligned) {
    if (!seg.words || seg.words.length === 0) continue;
    for (const w of seg.words) {
      if (!w.word) continue;
      const start = parseFloat(w.start);
      const end   = parseFloat(w.end);
      if (isNaN(start) || isNaN(end) || start < 0 || end <= start) continue;
      words.push({
        word:    w.word.trim(),
        start:   start,
        end:     end,
        isPower: isPowerWord(w.word),
      });
    }
  }

  if (words.length === 0 && sentences.length > 0) {
    console.log("⚠️  No word alignment — equal split fallback");
    const allText = sentences.join(" ").split(/\s+/).filter(Boolean);
    const perWord = effectiveDuration / Math.max(allText.length, 1);
    for (let i = 0; i < allText.length; i++) {
      words.push({
        word:    allText[i],
        start:   i * perWord,
        end:     (i + 1) * perWord,
        isPower: isPowerWord(allText[i]),
      });
    }
  }

  words.sort((a, b) => a.start - b.start);

  console.log(`📊 Words: ${words.length}`);
  if (words.length > 0) {
    console.log(`   [0]  ${words[0].start.toFixed(3)}s → ${words[0].end.toFixed(3)}s "${words[0].word}"`);
    console.log(`   [-1] ${words[words.length-1].start.toFixed(3)}s → ${words[words.length-1].end.toFixed(3)}s "${words[words.length-1].word}"`);
  }

  return words;
}


// ═══════════════════════════════════════════════════════════════════════════
// FRAME STATE MAP
// ═══════════════════════════════════════════════════════════════════════════

function buildFrameStateMap(words) {
  const map = new Array(totalFrames).fill(null);
  if (!words.length) return map;

  let wi = 0;

  for (let f = 0; f < totalFrames; f++) {
    const t = f / FPS;

    while (wi < words.length - 1 && t >= words[wi].end) wi++;

    const w = words[wi];

    if (t >= w.start - 0.005 && t < w.end) {
      map[f] = {
        word:     w.word,
        isPower:  w.isPower,
        progress: (t - w.start) / Math.max(w.end - w.start, 0.001),
      };
    }
  }

  const covered = map.filter(Boolean).length;
  console.log(`Coverage: ${covered}/${totalFrames} (${((covered/totalFrames)*100).toFixed(1)}%)`);
  return map;
}


// ═══════════════════════════════════════════════════════════════════════════
// ✅ FIX: STATE KEY — لا يتغير بـ globalFrame بعد انتهاء Title Slide
// ═══════════════════════════════════════════════════════════════════════════

function stateKey(state, globalFrame) {
  // Title slide: كل فريم مختلف (animation)
  if (globalFrame < TITLE_SLIDE_FRAMES) {
    return `title_slide_f${globalFrame}`;
  }

  const hook = globalFrame < HOOK_FRAMES ? "h" : "n";

  if (!state) return `empty_${hook}`;

  return `w_${state.word}_${state.isPower ? 1 : 0}_${hook}`;
}


// ═══════════════════════════════════════════════════════════════════════════
// HTML BUILDER — VIRAL VISUAL UPGRADE
// ═══════════════════════════════════════════════════════════════════════════

function buildHTML({
  word,
  isPower     = false,
  isHook      = false,
  globalFrame = 0,
}) {
  // Empty frame → transparent PNG بدون أي محتوى
  if (!word) {
    return (
      `<!DOCTYPE html><html><head><meta charset="UTF-8"/></head>` +
      `<body style="width:${WIDTH}px;height:${HEIGHT}px;background:transparent;margin:0;padding:0;"></body></html>`
    );
  }

  const ar       = isArabic(word);
  const dir      = getDir(word);
  const font     = getFontFamily(word);
  const langAttr = getLang(word);

  const titleAr   = isArabic(display_title);
  const titleDir  = getDir(display_title);
  const titleFont = getFontFamily(display_title);

  // ── Word sizing ──────────────────────────────────────────────────────────
  const wlen = word.length;
  let fontSize;
  if      (isPower)    fontSize = ar ? 190 : 180;
  else if (wlen <= 2)  fontSize = ar ? 170 : 160;
  else if (wlen <= 4)  fontSize = ar ? 150 : 140;
  else if (wlen <= 6)  fontSize = ar ? 130 : 120;
  else if (wlen <= 9)  fontSize = ar ? 110 : 102;
  else if (wlen <= 12) fontSize = ar ? 92  : 86;
  else                 fontSize = ar ? 76  : 72;

  const wordColor  = isPower ? COLORS.power : COLORS.word;
  const glowColor  = COLORS.glow;
  const gradStart  = COLORS.gradient[0];
  const gradEnd    = COLORS.gradient[1];

  // ── Title slide-in animation ─────────────────────────────────────────────
  const slideP  = globalFrame < TITLE_SLIDE_FRAMES
    ? globalFrame / TITLE_SLIDE_FRAMES
    : 1.0;
  const eased   = 1 - Math.pow(1 - slideP, 3);
  const slideX  = (titleDir === "rtl" ? 120 : -120) * (1 - eased);
  const titleOp = eased;
  const titleSz = titleAr ? 38 : 34;

  // ── Hook text ────────────────────────────────────────────────────────────
  const defaults = {
    ar: "🔴 لا تتجاوز هذا",
    fr: "🔴 Ne ratez pas ça",
    en: "🔴 Don't skip this",
  };
  const hookText = (custom_hook && custom_hook.trim()) || defaults[lang] || defaults.en;
  const hookAr   = isArabic(hookText);
  const hookDir  = hookAr ? "rtl" : "ltr";
  const hookFont = getFontFamily(hookText);

  // ── Power word pill / regular word ───────────────────────────────────────
  // Power: gradient background pill + scale up
  // Regular: gradient text + heavy stroke outline + glow
  const powerStyles = isPower ? `
    background: linear-gradient(135deg, #FF1744 0%, #D50000 100%);
    padding: 24px 60px;
    border-radius: 9999px;
    border: 3px solid rgba(255,255,255,0.3);
    box-shadow:
      0 0 60px rgba(255,23,68,0.8),
      0 0 120px rgba(255,23,68,0.4),
      inset 0 1px 0 rgba(255,255,255,0.2);
    transform: scale(1.05);
  ` : `
    background: transparent;
    padding: 0;
  `;

  // للكلمات العادية: gradient text عبر SVG filter أو -webkit-background-clip
  const regularTextStyle = isPower ? `
    font-family: ${font};
    font-size: ${fontSize}px;
    font-weight: 900;
    color: #FFFFFF;
    line-height: 1.15;
    letter-spacing: ${ar ? "1px" : "3px"};
    display: block;
    word-break: break-word;
    -webkit-text-stroke: 3px rgba(0,0,0,0.6);
    text-shadow:
      0 0 0px transparent;
    paint-order: stroke fill;
  ` : `
    font-family: ${font};
    font-size: ${fontSize}px;
    font-weight: 900;
    color: ${wordColor};
    line-height: 1.15;
    letter-spacing: ${ar ? "1px" : "3px"};
    display: block;
    word-break: break-word;
    -webkit-text-stroke: 4px rgba(0,0,0,0.95);
    paint-order: stroke fill;
    text-shadow:
      0 0 40px ${glowColor},
      0 0 80px ${glowColor},
      0 0 120px rgba(0,0,0,0.3);
  `;

  return `<!DOCTYPE html>
<html lang="${langAttr}">
<head>
  <meta charset="UTF-8"/>
  <style>
    * { margin:0; padding:0; box-sizing:border-box; }
    html,body {
      width:${WIDTH}px; height:${HEIGHT}px;
      overflow:hidden; background:transparent;
    }

    /* Cinematic overlays */
    .ot {
      position:absolute; top:0; left:0; right:0; height:35%;
      background:linear-gradient(to bottom,
        rgba(0,0,0,0.75) 0%,
        rgba(0,0,0,0.3) 60%,
        transparent 100%);
      pointer-events:none; z-index:1;
    }
    .ob {
      position:absolute; bottom:0; left:0; right:0; height:40%;
      background:linear-gradient(to top,
        rgba(0,0,0,0.85) 0%,
        rgba(0,0,0,0.4) 60%,
        transparent 100%);
      pointer-events:none; z-index:1;
    }

    /* Hook badge */
    .hb {
      position:absolute; top:155px; left:50%;
      transform:translateX(-50%);
      background:linear-gradient(135deg,rgba(220,0,0,0.95),rgba(170,0,0,0.95));
      color:#fff;
      font-family:${hookFont};
      font-size:${hookAr ? "34px" : "30px"};
      font-weight:900;
      padding:14px 40px;
      border-radius:9999px;
      z-index:25;
      white-space:nowrap;
      direction:${hookDir};
      border:2px solid rgba(255,100,100,0.4);
      box-shadow:
        0 0 50px rgba(220,0,0,0.7),
        0 0 100px rgba(220,0,0,0.3),
        0 8px 24px rgba(0,0,0,0.5);
    }

    /* Title container */
    .tc {
      position:absolute;
      top:${isHook ? "278px" : "338px"};
      left:50%;
      width:90%; max-width:960px;
      direction:${titleDir}; text-align:center;
      z-index:20;
      transform:translateX(calc(-50% + ${slideX.toFixed(2)}px));
      opacity:${titleOp.toFixed(4)};
    }
    .tb {
      display:inline-block;
      background:linear-gradient(135deg,rgba(220,0,0,0.94),rgba(160,0,0,0.94));
      padding:14px 34px;
      border-radius:9999px;
      border:1px solid rgba(255,120,120,0.3);
      box-shadow:
        0 0 40px rgba(220,0,0,0.5),
        0 8px 24px rgba(0,0,0,0.4);
    }
    .tt {
      font-family:${titleFont};
      font-size:${titleSz}px;
      font-weight:800;
      color:#fff;
      display:inline-flex;
      align-items:center;
      gap:10px;
      white-space:nowrap;
      text-shadow:0 2px 8px rgba(0,0,0,0.4);
      -webkit-text-stroke: 0.5px rgba(0,0,0,0.3);
    }
    .te { font-size:${titleAr ? "40px" : "36px"}; }

    /* Word container — centered vertically at 50% */
    .wc {
      position:absolute;
      left:50%; top:52%;
      transform:translate(-50%,-50%);
      direction:${dir};
      text-align:center;
      z-index:10;
      width:95%;
      max-width:1020px;
    }
    .wp {
      display:inline-block;
      ${powerStyles}
    }
    .wt {
      ${regularTextStyle}
    }

    /* Progress dot indicator — viral retention hack */
    .pd {
      position:absolute;
      bottom:120px; left:50%;
      transform:translateX(-50%);
      z-index:20;
      display:flex;
      gap:8px;
      align-items:center;
    }
    .pd-bar {
      width:48px; height:4px;
      border-radius:2px;
      background:rgba(255,255,255,0.25);
    }
    .pd-bar.active {
      background:rgba(255,255,255,0.9);
      box-shadow:0 0 8px rgba(255,255,255,0.5);
    }
  </style>
</head>
<body>
  <div class="ot"></div>
  <div class="ob"></div>

  ${isHook ? `<div class="hb">${esc(hookText)}</div>` : ""}

  <div class="tc">
    <div class="tb">
      <div class="tt">
        <span class="te">${emoji_left}</span>
        <span>${esc(display_title)}</span>
        <span class="te">${emoji_right}</span>
      </div>
    </div>
  </div>

  <div class="wc">
    <div class="wp">
      <span class="wt">${esc(word)}</span>
    </div>
  </div>
</body>
</html>`;
}


// ═══════════════════════════════════════════════════════════════════════════
// RENDER PNGs
// ═══════════════════════════════════════════════════════════════════════════

async function renderAllPNGs(page, frameStateMap) {
  const unique = new Map();

  for (let f = 0; f < frameStateMap.length; f++) {
    const key = stateKey(frameStateMap[f], f);
    if (!unique.has(key)) {
      unique.set(key, {
        word:        frameStateMap[f]?.word    ?? null,
        isPower:     frameStateMap[f]?.isPower ?? false,
        isHook:      f < HOOK_FRAMES,
        globalFrame: f,
      });
    }
  }

  console.log(`\n📸 ${unique.size} unique states to render`);

  // Font warmup
  for (const [w, l] of [["مرحبا", "ar"], ["Hello", "en"]]) {
    const html = buildHTML({ word: w, isPower: false, isHook: false, globalFrame: TITLE_SLIDE_FRAMES });
    const p = `${TMP}/init_${l}.html`;
    writeFileSync(p, html, "utf-8");
    await page.goto(`file://${p}`, { waitUntil: "networkidle" });
    await page.waitForTimeout(l === "ar" ? 1000 : 500);
  }
  console.log("✅ Fonts loaded");

  const cache = new Map();
  let done = 0;

  for (const [key, s] of unique) {
    const html = buildHTML({
      word:        s.word,
      isPower:     s.isPower,
      isHook:      s.isHook,
      globalFrame: s.globalFrame,
    });

    const hp = `${TMP}/${key}.html`;
    writeFileSync(hp, html, "utf-8");
    await page.goto(`file://${hp}`, { waitUntil: "load" });
    await page.waitForTimeout(35);

    const pp = `${TMP}/${key}.png`;
    await page.screenshot({ path: pp, type: "png", omitBackground: true });

    cache.set(key, pp);
    done++;
    if (done % 50 === 0 || done === unique.size) {
      process.stdout.write(`  ${done}/${unique.size} PNGs\n`);
    }
  }

  return cache;
}


// ═══════════════════════════════════════════════════════════════════════════
// BACKGROUND PROCESSING
// ═══════════════════════════════════════════════════════════════════════════

function processBackground(videoPath, duration, outPath, idx, isHook = false) {
  const srcDur = parseFloat(
    spawnSync("ffprobe", [
      "-v", "error",
      "-show_entries", "format=duration",
      "-of", "default=noprint_wrappers=1:nokey=1",
      videoPath,
    ], { stdio: ["ignore", "pipe", "pipe"] }).stdout.toString().trim()
  ) || 0;

  const loop   = srcDur < duration + 0.5 ? ["-stream_loop", "-1"] : [];
  const frames = Math.ceil(duration * FPS);
  const mtype  = idx % 4;

  const zooms = [
    `scale=w='trunc((iw*1.3)/2)*2':h='trunc((ih*1.3)/2)*2',zoompan=z='min(zoom+0.0008,1.3)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=${frames}:s=${WIDTH}x${HEIGHT}:fps=${FPS}`,
    `scale=w='trunc((iw*1.3)/2)*2':h='trunc((ih*1.3)/2)*2',zoompan=z='max(zoom-0.0008,1.0)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=${frames}:s=${WIDTH}x${HEIGHT}:fps=${FPS}`,
    `scale=w='trunc((iw*1.2)/2)*2':h='trunc((ih*1.2)/2)*2',zoompan=z='1.1':x='if(gte(x,iw/10),x-0.5,iw/10)':y='ih/2-(ih/zoom/2)':d=${frames}:s=${WIDTH}x${HEIGHT}:fps=${FPS}`,
    `scale=w='trunc((iw*1.2)/2)*2':h='trunc((ih*1.2)/2)*2',zoompan=z='1.1':x='if(lte(x,iw-iw/10),x+0.5,iw-iw/10)':y='ih/2-(ih/zoom/2)':d=${frames}:s=${WIDTH}x${HEIGHT}:fps=${FPS}`,
  ];
  const hookZoom =
    `scale=w='trunc((iw*1.5)/2)*2':h='trunc((ih*1.5)/2)*2',zoompan=` +
    `z='min(zoom+0.0015,1.5)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':` +
    `d=${frames}:s=${WIDTH}x${HEIGHT}:fps=${FPS}`;

  const zf = isHook ? hookZoom : zooms[mtype];
  const d  = Math.max(duration, 0.5);
  const fi = Math.min(0.4, d * 0.1);
  const fo = Math.min(0.4, d * 0.1);

  const cin = [
    "curves=r='0/0 0.3/0.28 0.7/0.76 1/0.92':g='0/0 0.3/0.28 0.7/0.78 1/0.94':b='0/0.02 0.3/0.30 0.7/0.82 1/0.98'",
    "hue=s=0.9",
    isHook
      ? "eq=contrast=1.15:brightness=0.02:saturation=1.1"
      : "eq=contrast=1.08:brightness=-0.01:saturation=0.95",
    "vignette=PI/5:eval=frame",
    "unsharp=3:3:0.4:3:3:0.0",
    ...(isHook ? [] : ["noise=alls=2:allf=t+u"]),
  ].join(",");

  const vf =
    `${zf},${cin},` +
    `fade=t=in:st=0:d=${fi.toFixed(3)},` +
    `fade=t=out:st=${(d - fo).toFixed(3)}:d=${fo.toFixed(3)}`;

  let r = spawnSync("ffmpeg", [
    "-y", ...loop, "-i", videoPath,
    "-t", duration.toFixed(3),
    "-vf", vf,
    "-r", String(FPS),
    "-c:v", "libx264", "-preset", "fast",
    "-crf", isHook ? "17" : "19",
    "-pix_fmt", "yuv420p", "-an", outPath,
  ], { stdio: ["ignore", "pipe", "pipe"] });

  if (r.status !== 0) {
    r = spawnSync("ffmpeg", [
      "-y", "-stream_loop", "-1", "-i", videoPath,
      "-t", duration.toFixed(3),
      "-vf",
      `scale=${WIDTH}:${HEIGHT}:force_original_aspect_ratio=increase,` +
      `crop=${WIDTH}:${HEIGHT},setsar=1,${cin},` +
      `fade=t=in:st=0:d=${fi.toFixed(3)},` +
      `fade=t=out:st=${(d - fo).toFixed(3)}:d=${fo.toFixed(3)}`,
      "-r", String(FPS),
      "-c:v", "libx264", "-preset", "fast", "-crf", "21",
      "-pix_fmt", "yuv420p", "-an", outPath,
    ], { stdio: ["ignore", "pipe", "pipe"] });
  }

  if (r.status !== 0) {
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

// ✅ FIX: overlayOnBg الآن تنقل الصوت من الـ bgMp4
function overlayOnBg(bgMp4, capMov, audioPth, outPath) {
  // المحاولة الأولى: overlay مع الصوت من الـ bg video
  let r = spawnSync("ffmpeg", [
    "-y",
    "-i", bgMp4,
    "-i", capMov,
    "-filter_complex",
    "[1:v]format=rgba[cap];[0:v][cap]overlay=0:0:format=auto,format=yuv420p[out]",
    "-map", "[out]",
    "-map", "0:a:0",
    "-c:v", "libx264", "-preset", "fast", "-crf", "19",
    "-c:a", "aac", "-b:a", "192k",
    "-pix_fmt", "yuv420p",
    outPath,
  ], { stdio: ["ignore", "pipe", "pipe"] });

  // ✅ FIX: إذا فشل (bg بدون صوت)، نستخدم ملف الصوت المنفصل
  if (r.status !== 0 && audioPth) {
    console.log("  ⚠️  BG has no audio stream — using audio file directly");
    r = spawnSync("ffmpeg", [
      "-y",
      "-i", bgMp4,
      "-i", capMov,
      "-i", audioPth,
      "-filter_complex",
      "[1:v]format=rgba[cap];[0:v][cap]overlay=0:0:format=auto,format=yuv420p[out]",
      "-map", "[out]",
      "-map", "2:a:0",
      "-c:v", "libx264", "-preset", "fast", "-crf", "19",
      "-c:a", "aac", "-b:a", "192k",
      "-pix_fmt", "yuv420p",
      outPath,
    ], { stdio: ["ignore", "pipe", "pipe"] });
  }

  if (r.status !== 0) {
    console.error("❌ overlayOnBg failed:", r.stderr?.toString().slice(-300));
  }

  return outPath;
}

function xfadeConcat(clips, durs) {
  if (clips.length === 0) return "";
  if (clips.length === 1) return clips[0];

  const TRANS = ["fade","fadeblack","fadegrays","smoothleft","smoothright","circlecrop"];
  const XFADE = 0.5;
  const filters = [];
  let offset = 0, last = "[0:v]";

  for (let i = 1; i < clips.length; i++) {
    offset += durs[i - 1] - XFADE;
    if (offset < 0) offset = 0;
    const out   = i === clips.length - 1 ? "[vout]" : `[v${i}]`;
    const trans = TRANS[(i - 1) % TRANS.length];
    filters.push(
      `${last}[${i}:v]xfade=transition=${trans}:duration=${XFADE}:offset=${offset.toFixed(3)}${out}`
    );
    last = out;
  }

  const outPath = `${TMP}/xfaded.mp4`;
  const r = spawnSync("ffmpeg", [
    "-y", ...clips.flatMap(p => ["-i", p]),
    "-filter_complex", filters.join(";"),
    "-map", "[vout]",
    "-c:v", "libx264", "-preset", "fast", "-crf", "19",
    "-pix_fmt", "yuv420p", "-an", outPath,
  ], { stdio: ["ignore", "pipe", "pipe"] });

  if (r.status !== 0) {
    const lst = `${TMP}/list.txt`;
    writeFileSync(lst, clips.map(p => `file '${p}'`).join("\n"));
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
  console.log(`🎵 Audio: ${aDur.toFixed(3)}s | Video: ${vDur.toFixed(3)}s`);

  let vid = videoPath;
  if (vDur < aDur - 0.3) {
    const looped = `${TMP}/looped.mp4`;
    const r = spawnSync("ffmpeg", [
      "-y", "-stream_loop", "-1", "-i", videoPath,
      "-t", aDur.toFixed(3),
      "-c:v", "libx264", "-preset", "fast", "-crf", "21",
      "-pix_fmt", "yuv420p", "-an", looped,
    ], { stdio: ["ignore", "pipe", "pipe"] });
    if (r.status === 0) vid = looped;
  }

  spawnSync("ffmpeg", [
    "-y", "-i", vid, "-i", audioPath,
    "-map", "0:v:0", "-map", "1:a:0",
    "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
    "-t", aDur.toFixed(3), outPath,
  ], { stdio: ["ignore", "pipe", "pipe"] });

  console.log(`✅ Done → ${outPath}`);
}


// ═══════════════════════════════════════════════════════════════════════════
// MAIN
// ═══════════════════════════════════════════════════════════════════════════

async function main() {
  console.log(`\n🚀 Mode: ${mode}\n`);

  // ════════════════════════════════════════════════════════════════════
  // MODE: bg_only
  // ════════════════════════════════════════════════════════════════════
  if (mode === "bg_only") {
    const totalClips    = Math.max(1, Math.floor(effectiveDuration / clip_duration));
    const actualClipDur = effectiveDuration / totalClips;

    console.log(`📊 ${totalClips} clips × ${actualClipDur.toFixed(2)}s`);

    const finalClips = [];
    const clipDurs   = [];

    for (let i = 0; i < totalClips; i++) {
      const clipStart = i * actualClipDur;
      const clipEnd   = Math.min((i + 1) * actualClipDur, effectiveDuration);
      const clipDur   = Math.max(clipEnd - clipStart, 0.5);
      const isHook    = i === 0 && has_hook;
      const vidSrc    = videos[i % videos.length];

      process.stdout.write(`  [${i+1}/${totalClips}] ${clipDur.toFixed(2)}s${isHook?" 🔥":""}... `);

      const bgMp4 = `${TMP}/bg_${String(i).padStart(3,"0")}.mp4`;
      processBackground(vidSrc, clipDur, bgMp4, i, isHook);
      finalClips.push(bgMp4);
      clipDurs.push(clipDur);
      process.stdout.write("✓\n");
    }

    console.log(`\n✨ Concat ${finalClips.length} clips...`);
    const dissolved = xfadeConcat(finalClips, clipDurs);

    console.log("🎵 Merging audio...");
    mergeAudio(dissolved, audio, outputPath);
    console.log(`\n🎉 BG Video → ${outputPath}\n`);
    return;
  }

  // ════════════════════════════════════════════════════════════════════
  // MODE: words_only — ✅ FIXED
  // ════════════════════════════════════════════════════════════════════
  if (mode === "words_only") {
    // ✅ FIX: التحقق من وجود الفيديو الخلفي
    const bgVideoPath = videos[0];
    if (!bgVideoPath) {
      console.error("❌ words_only mode requires videos[0] as background video path");
      process.exit(1);
    }

    const words         = buildWordList();
    const frameStateMap = buildFrameStateMap(words);

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
    console.log(`✅ ${pngCache.size} PNGs\n`);

    // Build frame directory
    const frameDir = `${TMP}/frames_words`;
    mkdirSync(frameDir, { recursive: true });

    // ✅ FIX: نتأكد أن كل فريم له ملف — فراغات تُملأ بـ empty PNG
    const emptyKey = stateKey(null, HOOK_FRAMES + 1);
    const emptyPng = pngCache.get(emptyKey);

    for (let f = 0; f < totalFrames; f++) {
      const key  = stateKey(frameStateMap[f], f);
      const src  = pngCache.get(key) || emptyPng;
      const dest = `${frameDir}/frame_${String(f).padStart(6, "0")}.png`;
      if (!src) continue;
      try { symlinkSync(src, dest); }
      catch { copyFileSync(src, dest); }
    }

    const capMov = `${TMP}/cap_words.mov`;
    framesToMov(frameDir, capMov);

    console.log("🔧 Overlaying words on BG video...");
    // ✅ FIX: نمرر audio كـ fallback لضمان وجود الصوت
    overlayOnBg(bgVideoPath, capMov, audio, outputPath);

    console.log(`\n🎉 Final → ${outputPath}\n`);
    return;
  }

  console.error(`❌ Unknown mode: ${mode}`);
  process.exit(1);
}

main().catch((err) => {
  console.error("❌", err);
  process.exit(1);
});
