// remotion/render.mjs
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
// EMOTION COLORS
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
        start,
        end,
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
// STATE KEY
// ═══════════════════════════════════════════════════════════════════════════

function stateKey(state, globalFrame) {
  // Title animation: أول ثانية + آخر ثانية
  const INTRO_FRAMES = Math.floor(1.0 * FPS);
  const OUTRO_FRAMES = Math.floor(1.0 * FPS);
  const isIntro = globalFrame < INTRO_FRAMES;
  const isOutro = globalFrame >= totalFrames - OUTRO_FRAMES;

  // خلال الـ intro/outro كل فريم مختلف (animation)
  if (isIntro) return `title_intro_f${globalFrame}`;
  if (isOutro) return `title_outro_f${globalFrame}`;

  const hook = globalFrame < HOOK_FRAMES ? "h" : "n";
  if (!state) return `empty_${hook}`;

  const p      = state.progress;
  const bucket = p < 0.15 ? "pop" : p > 0.85 ? "fade" : "hold";

  return `w_${state.word}_${state.isPower ? 1 : 0}_${hook}_${bucket}`;
}


// ═══════════════════════════════════════════════════════════════════════════
// HTML BUILDER
// ═══════════════════════════════════════════════════════════════════════════

function buildHTML({
  word,
  isPower     = false,
  isHook      = false,
  globalFrame = 0,
  progress    = 0.5,
}) {
  const ar       = word ? isArabic(word) : false;
  const dir      = word ? getDir(word) : "ltr";
  const font     = word ? getFontFamily(word) : `"Noto Sans", sans-serif`;
  const langAttr = word ? getLang(word) : "en";

  const titleAr   = isArabic(display_title);
  const titleDir  = getDir(display_title);
  const titleFont = getFontFamily(display_title);

  // ── Title animation ──────────────────────────────────────────────────────
  const INTRO_FRAMES = Math.floor(1.0 * FPS);
  const OUTRO_FRAMES = Math.floor(1.0 * FPS);
  const isIntro = globalFrame < INTRO_FRAMES;
  const isOutro = globalFrame >= totalFrames - OUTRO_FRAMES;

  let titleOpacity, titleTranslateY;

  if (isIntro) {
    // Slide down + fade in
    const t        = globalFrame / INTRO_FRAMES;
    const ease     = 1 - Math.pow(1 - t, 3);  // ease out cubic
    titleOpacity   = ease;
    titleTranslateY = (1 - ease) * -80;         // يأتي من الأعلى
  } else if (isOutro) {
    // Slide up + fade out
    const t        = (globalFrame - (totalFrames - OUTRO_FRAMES)) / OUTRO_FRAMES;
    const ease     = Math.pow(t, 2);            // ease in quad
    titleOpacity   = 1 - ease;
    titleTranslateY = ease * -60;               // يخرج لأعلى
  } else {
    titleOpacity   = 1.0;
    titleTranslateY = 0;
  }

  // ── Word animation ───────────────────────────────────────────────────────
  let wordScale, wordOpacity, wordTranslateY;

  if (!word) {
    wordScale      = 1.0;
    wordOpacity    = 0;
    wordTranslateY = 0;
  } else if (progress < 0.15) {
    const t        = progress / 0.15;
    const ease     = 1 - Math.pow(1 - t, 2);
    wordScale      = 0.6 + ease * 0.48;
    wordOpacity    = Math.min(1, t * 3);
    wordTranslateY = (1 - ease) * 30;
  } else if (progress > 0.85) {
    const t        = (progress - 0.85) / 0.15;
    wordScale      = 1.0 - t * 0.05;
    wordOpacity    = 1 - t * 0.3;
    wordTranslateY = 0;
  } else {
    wordScale      = isPower ? 1.06 : 1.0;
    wordOpacity    = 1.0;
    wordTranslateY = 0;
  }

  // ── Word sizing ──────────────────────────────────────────────────────────
  const wlen = word ? word.length : 0;
  let baseFontSize = 100;
  if (word) {
    if      (isPower)    baseFontSize = ar ? 190 : 180;
    else if (wlen <= 2)  baseFontSize = ar ? 170 : 160;
    else if (wlen <= 4)  baseFontSize = ar ? 150 : 140;
    else if (wlen <= 6)  baseFontSize = ar ? 130 : 120;
    else if (wlen <= 9)  baseFontSize = ar ? 110 : 102;
    else if (wlen <= 12) baseFontSize = ar ? 92  : 86;
    else                 baseFontSize = ar ? 76  : 72;
  }

  const wordColor = isPower ? COLORS.power : COLORS.word;
  const glowColor = COLORS.glow;

  // ── Hook ─────────────────────────────────────────────────────────────────
  const defaults = { ar: "🔴 لا تتجاوز هذا", fr: "🔴 Ne ratez pas ça", en: "🔴 Don't skip this" };
  const hookText = (custom_hook && custom_hook.trim()) || defaults[lang] || defaults.en;
  const hookAr   = isArabic(hookText);
  const hookDir  = hookAr ? "rtl" : "ltr";
  const hookFont = getFontFamily(hookText);

  // ── Title sizing ─────────────────────────────────────────────────────────
  // العنوان في الأعلى — نجعله أكبر وأكثر وضوحاً
  const titleArabic    = isArabic(display_title);
  const titleFontSize  = titleArabic ? 52 : 46;
  const emojiSize      = titleArabic ? 56 : 50;

  // ── Power word container ─────────────────────────────────────────────────
  const powerContainerStyle = isPower ? `
    background:linear-gradient(135deg,#FF1744 0%,#D50000 100%);
    padding:24px 60px;
    border-radius:9999px;
    border:3px solid rgba(255,255,255,0.3);
    box-shadow:0 0 60px rgba(255,23,68,0.8),0 0 120px rgba(255,23,68,0.4),inset 0 1px 0 rgba(255,255,255,0.2);
  ` : `background:transparent;padding:0;`;

  const wordTextStyle = isPower ? `
    font-family:${font};
    font-size:${baseFontSize}px;
    font-weight:900;
    color:#FFFFFF;
    line-height:1.15;
    letter-spacing:${ar ? "1px" : "3px"};
    display:block;
    word-break:break-word;
    -webkit-text-stroke:2px rgba(0,0,0,0.5);
    paint-order:stroke fill;
  ` : `
    font-family:${font};
    font-size:${baseFontSize}px;
    font-weight:900;
    color:${wordColor};
    line-height:1.15;
    letter-spacing:${ar ? "1px" : "3px"};
    display:block;
    word-break:break-word;
    -webkit-text-stroke:4px rgba(0,0,0,0.95);
    paint-order:stroke fill;
    text-shadow:0 0 40px ${glowColor},0 0 80px ${glowColor};
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
      position:absolute; top:0; left:0; right:0; height:40%;
      background:linear-gradient(to bottom,
        rgba(0,0,0,0.85) 0%,
        rgba(0,0,0,0.5) 50%,
        transparent 100%);
      pointer-events:none; z-index:1;
    }
    .ob {
      position:absolute; bottom:0; left:0; right:0; height:42%;
      background:linear-gradient(to top,
        rgba(0,0,0,0.88) 0%,
        rgba(0,0,0,0.45) 65%,
        transparent 100%);
      pointer-events:none; z-index:1;
    }

    /* ✅ العنوان في الأعلى — بارز وجذاب */
    .tc {
      position:absolute;
      top:410px;
      left:50%;
      width:92%;
      max-width:980px;
      direction:${titleDir};
      text-align:center;
      z-index:30;
      transform:translateX(-50%) translateY(${titleTranslateY.toFixed(2)}px);
      opacity:${titleOpacity.toFixed(4)};
    }

    /* الخط الأحمر تحت العنوان */
    .tc::after {
      content:'';
      display:block;
      margin:16px auto 0;
      width:120px;
      height:4px;
      border-radius:2px;
      background:linear-gradient(90deg,transparent,#FF1744,transparent);
      opacity:${titleOpacity.toFixed(4)};
    }

    .tt {
      font-family:${titleFont};
      font-size:${titleFontSize}px;
      font-weight:900;
      color:#FFFFFF;
      display:inline-flex;
      align-items:center;
      justify-content:center;
      gap:14px;
      line-height:1.3;
      text-align:center;
      direction:${titleDir};
      -webkit-text-stroke:2px rgba(0,0,0,0.8);
      paint-order:stroke fill;
      text-shadow:
        0 0 30px rgba(255,23,68,0.6),
        0 0 60px rgba(255,23,68,0.3),
        0 4px 20px rgba(0,0,0,0.9),
        2px 2px 0 rgba(0,0,0,0.8),
        -2px -2px 0 rgba(0,0,0,0.8);
    }
    .te { font-size:${emojiSize}px; -webkit-text-stroke:0; }

    /* Hook badge */
    .hb {
      position:absolute;
      top:${titleArabic ? "290px" : "270px"};
      left:50%;
      transform:translateX(-50%);
      background:linear-gradient(135deg,rgba(220,0,0,0.95),rgba(160,0,0,0.95));
      color:#fff;
      font-family:${hookFont};
      font-size:${hookAr ? "32px" : "28px"};
      font-weight:900;
      padding:12px 38px;
      border-radius:9999px;
      z-index:25;
      white-space:nowrap;
      direction:${hookDir};
      border:2px solid rgba(255,120,120,0.4);
      box-shadow:
        0 0 50px rgba(220,0,0,0.7),
        0 0 100px rgba(220,0,0,0.3),
        0 8px 24px rgba(0,0,0,0.5);
    }

    /* Word container */
    .wc {
      position:absolute;
      left:50%;
      top:54%;
      transform:translate(-50%, calc(-50% + ${wordTranslateY.toFixed(1)}px)) scale(${wordScale.toFixed(4)});
      opacity:${wordOpacity.toFixed(4)};
      direction:${dir};
      text-align:center;
      z-index:10;
      width:95%;
      max-width:1020px;
      transform-origin:center center;
    }
    .wp {
      display:inline-block;
      ${powerContainerStyle}
    }
    .wt { ${wordTextStyle} }
  </style>
</head>
<body>
  <div class="ot"></div>
  <div class="ob"></div>

  <!-- ✅ العنوان دائماً في الأعلى مع animation -->
  <div class="tc">
    <div class="tt">
      <span class="te">${emoji_left}</span>
      <span>${esc(display_title)}</span>
      <span class="te">${emoji_right}</span>
    </div>
  </div>

  ${isHook ? `<div class="hb">${esc(hookText)}</div>` : ""}

  ${word ? `
  <div class="wc">
    <div class="wp">
      <span class="wt">${esc(word)}</span>
    </div>
  </div>` : ""}

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
        word:        frameStateMap[f]?.word     ?? null,
        isPower:     frameStateMap[f]?.isPower  ?? false,
        isHook:      f < HOOK_FRAMES,
        globalFrame: f,
        progress:    frameStateMap[f]?.progress ?? 0.5,
      });
    }
  }

  console.log(`\n📸 ${unique.size} unique states to render`);

  // Font warmup
  for (const [w, l] of [["مرحبا", "ar"], ["Hello", "en"]]) {
    const html = buildHTML({
      word: w, isPower: false, isHook: false,
      globalFrame: TITLE_SLIDE_FRAMES, progress: 0.5,
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
      word:        s.word,
      isPower:     s.isPower,
      isHook:      s.isHook,
      globalFrame: s.globalFrame,
      progress:    s.progress,
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

function overlayOnBg(bgMp4, capMov, audioPth, outPath) {
  // المحاولة الأولى: overlay + صوت من bg
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

  // Fallback: صوت من ملف منفصل
  if (r.status !== 0 && audioPth) {
    console.log("  ⚠️  BG has no audio — using audio file directly");
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
      const isHookClip = i === 0 && has_hook;
      const vidSrc    = videos[i % videos.length];

      process.stdout.write(`  [${i+1}/${totalClips}] ${clipDur.toFixed(2)}s${isHookClip?" 🔥":""}... `);

      const bgMp4 = `${TMP}/bg_${String(i).padStart(3,"0")}.mp4`;
      processBackground(vidSrc, clipDur, bgMp4, i, isHookClip);
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
  // MODE: words_only
  // ════════════════════════════════════════════════════════════════════
  if (mode === "words_only") {
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

    // Empty frame fallback
    const emptyKey = `empty_n`;
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
