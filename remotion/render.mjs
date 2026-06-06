// remotion/render.mjs
// ✅ يدعم وضعين:
//    1. bg_only    → إنتاج فيديو الخلفية الكامل بدون نص
//    2. words_only → Overlay الكلمات فوق الفيديو الكامل
// ✅ التزامن 100% لأن WhisperX يحلل الفيديو النهائي نفسه

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
  mode          = "words_only", // ✅ bg_only | words_only
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
// EMOTION COLORS
// ═══════════════════════════════════════════════════════════════════════════

const EMOTION_COLORS = {
  curiosity: { word: "#FFD700", glow: "rgba(255,215,0,0.6)",   shadow: "rgba(0,0,0,0.95)", power: "#FF1744" },
  fear:      { word: "#FF4444", glow: "rgba(255,68,68,0.6)",   shadow: "rgba(0,0,0,0.95)", power: "#FFD700" },
  hope:      { word: "#00E676", glow: "rgba(0,230,118,0.6)",   shadow: "rgba(0,0,0,0.95)", power: "#FFFFFF" },
  joy:       { word: "#FF9100", glow: "rgba(255,145,0,0.6)",   shadow: "rgba(0,0,0,0.95)", power: "#FFFFFF" },
  awe:       { word: "#AA00FF", glow: "rgba(170,0,255,0.6)",   shadow: "rgba(0,0,0,0.95)", power: "#FFD700" },
  surprise:  { word: "#00B0FF", glow: "rgba(0,176,255,0.6)",   shadow: "rgba(0,0,0,0.95)", power: "#FFD700" },
  desire:    { word: "#FF1744", glow: "rgba(255,23,68,0.6)",   shadow: "rgba(0,0,0,0.95)", power: "#FFD700" },
  anger:     { word: "#FF1744", glow: "rgba(255,23,68,0.6)",   shadow: "rgba(0,0,0,0.95)", power: "#FFD700" },
  sadness:   { word: "#82B1FF", glow: "rgba(130,177,255,0.6)", shadow: "rgba(0,0,0,0.95)", power: "#FFFFFF" },
  default:   { word: "#FFFFFF", glow: "rgba(255,255,255,0.5)", shadow: "rgba(0,0,0,0.95)", power: "#FF1744" },
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

  // Fallback فقط إذا لم يوجد alignment
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
    console.log(
      `   [0]  ${words[0].start.toFixed(3)}s → ${words[0].end.toFixed(3)}s "${words[0].word}"`
    );
    console.log(
      `   [-1] ${words[words.length - 1].start.toFixed(3)}s → ${words[words.length - 1].end.toFixed(3)}s "${words[words.length - 1].word}"`
    );
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
    // ✅ بداية الـ frame بالضبط
    const t = f / FPS;

    while (wi < words.length - 1 && t >= words[wi].end) {
      wi++;
    }

    const w = words[wi];

    // ✅ سماحية 5ms فقط
    if (t >= w.start - 0.005 && t < w.end) {
      map[f] = {
        word:     w.word,
        isPower:  w.isPower,
        progress: (t - w.start) / Math.max(w.end - w.start, 0.001),
      };
    }
  }

  const covered = map.filter(Boolean).length;
  console.log(`Coverage: ${covered}/${totalFrames} (${((covered / totalFrames) * 100).toFixed(1)}%)`);
  return map;
}


// ═══════════════════════════════════════════════════════════════════════════
// STATE KEY
// ═══════════════════════════════════════════════════════════════════════════

function stateKey(state, globalFrame) {
  const hook  = globalFrame < HOOK_FRAMES ? "h" : "n";
  const slide = globalFrame < TITLE_SLIDE_FRAMES ? `s${globalFrame}` : "sx";

  if (!state) return `empty_${hook}_${slide}`;

  // ✅ no progress-based cache split
  return `w_${state.word}_${state.isPower ? 1 : 0}_z_${hook}_${slide}`;
}


// ═══════════════════════════════════════════════════════════════════════════
// HTML BUILDER
// ═══════════════════════════════════════════════════════════════════════════

function buildHTML({
  word,
  isPower = false,
  isHook  = false,
  globalFrame = 0,
}) {
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

  // Word size
  const wlen = word.length;
  let fontSize;
  if      (isPower)  fontSize = ar ? 185 : 175;
  else if (wlen <= 2)  fontSize = ar ? 165 : 155;
  else if (wlen <= 4)  fontSize = ar ? 145 : 135;
  else if (wlen <= 6)  fontSize = ar ? 125 : 115;
  else if (wlen <= 9)  fontSize = ar ? 105 : 98;
  else if (wlen <= 12) fontSize = ar ? 88  : 82;
  else                 fontSize = ar ? 72  : 68;

  const wordColor = isPower ? COLORS.power : COLORS.word;
  const glowColor = COLORS.glow;
  const shadowClr = COLORS.shadow;
  const glowSize  = isPower ? "90px"  : "45px";
  const glowSize2 = isPower ? "130px" : "70px";
  const pillBg    = isPower
    ? `background:rgba(200,0,0,0.88);padding:22px 55px;border-radius:9999px;`
    : `background:transparent;padding:0;`;

  // Title slide-in
  const slideP  = globalFrame < TITLE_SLIDE_FRAMES
    ? globalFrame / TITLE_SLIDE_FRAMES
    : 1.0;
  const eased   = 1 - Math.pow(1 - slideP, 3);
  const slideX  = (titleDir === "rtl" ? 120 : -120) * (1 - eased);
  const titleOp = eased;
  const titleSz = titleAr ? 38 : 34;

  // Hook
  const defaults = {
    ar: "🔴 لا تتجاوز هذا",
    fr: "🔴 Ne ratez pas ça",
    en: "🔴 Don't skip this",
  };
  const hookText = (custom_hook && custom_hook.trim()) || defaults[lang] || defaults.en;
  const hookAr   = isArabic(hookText);
  const hookDir  = hookAr ? "rtl" : "ltr";
  const hookFont = getFontFamily(hookText);

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

    .ot {
      position:absolute; top:0; left:0; right:0; height:28%;
      background:linear-gradient(to bottom,rgba(0,0,0,0.7),transparent);
      pointer-events:none; z-index:1;
    }
    .ob {
      position:absolute; bottom:0; left:0; right:0; height:32%;
      background:linear-gradient(to top,rgba(0,0,0,0.7),transparent);
      pointer-events:none; z-index:1;
    }

    .hb {
      position:absolute; top:155px; left:50%;
      transform:translateX(-50%);
      background:rgba(210,0,0,0.93);
      color:#fff;
      font-family:${hookFont};
      font-size:${hookAr ? "34px" : "30px"};
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
      background:rgba(220,0,0,0.92);
      padding:13px 32px;
      border-radius:9999px;
      box-shadow:
        0 0 32px rgba(220,0,0,0.55),
        0 6px 22px rgba(0,0,0,0.45);
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
      text-shadow:0 2px 6px rgba(0,0,0,0.45);
    }
    .te { font-size:${titleAr ? "40px" : "36px"}; }

    .wc {
      position:absolute;
      left:50%; top:52%;
      transform:translate(-50%,-50%) scale(1);
      direction:${dir};
      text-align:center;
      z-index:10;
      width:95%;
      max-width:1000px;
    }
    .wp {
      display:inline-block;
      ${pillBg}
      box-shadow:${isPower
        ? "0 0 70px rgba(200,0,0,0.75),0 14px 40px rgba(0,0,0,0.65)"
        : "none"};
    }
    .wt {
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
        -2px -2px 0px rgba(0,0,0,0.8);
      letter-spacing:${ar ? "1px" : "3px"};
      display:block;
      word-break:break-word;
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

  // Warmup fonts
  for (const [w, l] of [["مرحبا", "ar"], ["Hello", "en"]]) {
    const html = buildHTML({
      word: w, isPower: false, isHook: false,
      globalFrame: TITLE_SLIDE_FRAMES,
    });
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
      word: s.word,
      isPower: s.isPower,
      isHook: s.isHook,
      globalFrame: s.globalFrame,
    });

    const hp = `${TMP}/${key}.html`;
    writeFileSync(hp, html, "utf-8");
    await page.goto(`file://${hp}`, { waitUntil: "load" });
    await page.waitForTimeout(35);

    const pp = `${TMP}/${key}.png`;
    await page.screenshot({
      path: pp, type: "png", omitBackground: true,
    });

    cache.set(key, pp);
    done++;
    if (done % 50 === 0 || done === unique.size) {
      process.stdout.write(`  ${done}/${unique.size} PNGs\n`);
    }
  }

  return cache;
}


// ═══════════════════════════════════════════════════════════════════════════
// BUILD FRAME DIR
// ═══════════════════════════════════════════════════════════════════════════

function buildFrameDir(clipMap, cache, idx, clipStartFrame) {
  const dir = `${TMP}/frames_${idx}`;
  mkdirSync(dir, { recursive: true });

  for (let f = 0; f < clipMap.length; f++) {
    const gf   = clipStartFrame + f;
    const key  = stateKey(clipMap[f], gf);
    const src  = cache.get(key);
    const dest = `${dir}/frame_${String(f).padStart(6, "0")}.png`;

    if (!src) continue;

    try { symlinkSync(src, dest); }
    catch { copyFileSync(src, dest); }
  }

  return dir;
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

function overlayOnBg(bgMp4, capMov, outPath) {
  spawnSync("ffmpeg", [
    "-y", "-i", bgMp4, "-i", capMov,
    "-filter_complex",
    "[1:v]format=rgba[cap];[0:v][cap]overlay=0:0:format=auto,format=yuv420p[out]",
    "-map", "[out]",
    "-c:v", "libx264", "-preset", "fast", "-crf", "19",
    "-pix_fmt", "yuv420p", "-an", outPath,
  ], { stdio: ["ignore", "pipe", "pipe"] });
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
      `${last}[${i}:v]xfade=transition=${trans}:` +
      `duration=${XFADE}:offset=${offset.toFixed(3)}${out}`
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
  // MODE: bg_only — إنتاج فيديو الخلفية الكامل بدون نص
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

      process.stdout.write(
        `  [${i+1}/${totalClips}] ${clipDur.toFixed(2)}s${isHook?" 🔥":""}... `
      );

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
  // MODE: words_only — Overlay الكلمات فوق الفيديو الخلفي الكامل
  // ════════════════════════════════════════════════════════════════════
  if (mode === "words_only") {
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

    // ✅ الفيديو الخلفي الكامل — يجب أن يكون videos[0] فقط
    const bgVideoPath = videos[0];
    const frameDir    = `${TMP}/frames_words`;
    mkdirSync(frameDir, { recursive: true });

    for (let f = 0; f < totalFrames; f++) {
      const key  = stateKey(frameStateMap[f], f);
      const src  = pngCache.get(key);
      const dest = `${frameDir}/frame_${String(f).padStart(6, "0")}.png`;
      if (!src) continue;
      try { symlinkSync(src, dest); }
      catch { copyFileSync(src, dest); }
    }

    const capMov = `${TMP}/cap_words.mov`;
    framesToMov(frameDir, capMov);

    console.log("🔧 Overlaying words on BG video...");
    overlayOnBg(bgVideoPath, capMov, outputPath);

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
