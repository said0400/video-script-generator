// remotion/render.mjs — Visual Addiction System (VAS)
// ✨ يقرأ كل البيانات من manifest.json (من AI Enrichment)

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

// ✨ كل البيانات من AI
const {
  title,
  sentences,                  // نظيفة (بدون tags)
  tagged_sentences = [],      // مع tags
  audio,
  videos,
  duration_s,
  power_words = [],           // ✨ من Groq
  pattern_interrupts = {},    // ✨ من Groq
  engagement_questions = {},  // ✨ من Groq
  accent_colors = [],         // ✨ من Groq
  analysis = {},              // ✨ من Groq
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
// 🎯 VAS CONFIGURATION
// ═════════════════════════════════════════════════════════════════════════════

const VAS = {
  HOOK_ZONE_END:       3.0,
  MAX_STATIC_TIME:     1.5,
  SHOCK_INTERVAL:      4.0,
  PATTERN_INTERRUPT:   8.0,
  ENGAGEMENT_INTERVAL: 12.0,
  
  MEGA_SIZE:           240,
  POWER_SIZE:          180,
  NORMAL_SIZE:         110,
  SMALL_SIZE:          70,
  
  MAX_WORDS_PER_REVEAL: 2,
  SHAKE_INTENSITY:      8,
  FLASH_OPACITY:        0.5,
  ZOOM_PUNCH:           0.45,
};

// ═════════════════════════════════════════════════════════════════════════════
// 🎨 COLORS (من AI أو افتراضية)
// ═════════════════════════════════════════════════════════════════════════════

const DEFAULT_COLORS = [
  "#FF003C", "#00FFFF", "#FFD700", "#FF6B00",
  "#39FF14", "#A020F0", "#FF1493", "#00E5FF",
];

const COLOR_CYCLE = (accent_colors && accent_colors.length >= 2)
  ? [...accent_colors, ...DEFAULT_COLORS]
  : DEFAULT_COLORS;

function getAccent(idx) {
  return COLOR_CYCLE[idx % COLOR_CYCLE.length];
}

console.log(`🎨 Using ${accent_colors.length > 0 ? "AI" : "default"} colors: ${COLOR_CYCLE.slice(0, 4).join(", ")}`);

// ═════════════════════════════════════════════════════════════════════════════
// 🔥 POWER WORDS (من AI)
// ═════════════════════════════════════════════════════════════════════════════

function normalizeWord(word) {
  if (!word) return "";
  return word
    .toString()
    .replace(/[.,!?؟،;:"'(){}[\]<>]/g, "")
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
// 🎭 ANIMATION PATTERNS
// ═════════════════════════════════════════════════════════════════════════════

const PATTERNS = {
  MEGA_SHOCK:      "mega_shock",
  GLITCH_REVEAL:   "glitch_reveal",
  ZOOM_PUNCH:      "zoom_punch",
  WORD_EXPLOSION:  "word_explosion",
  SIDE_SLAM_L:     "side_slam_left",
  SIDE_SLAM_R:     "side_slam_right",
  STACK_BUILD:     "stack_build",
  SPLIT_FOCUS:     "split_focus",
  CORNER_BLAST:    "corner_blast",
  SCREEN_TAKEOVER: "screen_takeover",
  TRIPLE_FLASH:    "triple_flash",
  COLOR_STORM:     "color_storm",
};

function selectPattern(opts) {
  const { isHook, isPower, revealIdx } = opts;
  
  if (isHook) {
    if (revealIdx === 0) return PATTERNS.MEGA_SHOCK;
    if (isPower)         return PATTERNS.GLITCH_REVEAL;
    return PATTERNS.ZOOM_PUNCH;
  }
  
  if (isPower) {
    const choices = [
      PATTERNS.WORD_EXPLOSION,
      PATTERNS.SCREEN_TAKEOVER,
      PATTERNS.TRIPLE_FLASH,
      PATTERNS.COLOR_STORM,
    ];
    return choices[revealIdx % choices.length];
  }
  
  const bodyPatterns = [
    PATTERNS.SIDE_SLAM_L,
    PATTERNS.SIDE_SLAM_R,
    PATTERNS.STACK_BUILD,
    PATTERNS.SPLIT_FOCUS,
    PATTERNS.CORNER_BLAST,
    PATTERNS.WORD_EXPLOSION,
  ];
  return bodyPatterns[revealIdx % bodyPatterns.length];
}

// ═════════════════════════════════════════════════════════════════════════════
// 📍 POSITIONS
// ═════════════════════════════════════════════════════════════════════════════

const POSITIONS = {
  CENTER:       { x: "50%", y: "50%", tx: "-50%", ty: "-50%" },
  TOP_LEFT:     { x: "8%",  y: "20%", tx: "0",    ty: "0"    },
  TOP_RIGHT:    { x: "92%", y: "20%", tx: "-100%",ty: "0"    },
  TOP_CENTER:   { x: "50%", y: "20%", tx: "-50%", ty: "0"    },
  BOT_LEFT:     { x: "8%",  y: "75%", tx: "0",    ty: "-100%"},
  BOT_RIGHT:    { x: "92%", y: "75%", tx: "-100%",ty: "-100%"},
  BOT_CENTER:   { x: "50%", y: "75%", tx: "-50%", ty: "-100%"},
  MID_LEFT:     { x: "8%",  y: "50%", tx: "0",    ty: "-50%" },
  MID_RIGHT:    { x: "92%", y: "50%", tx: "-100%",ty: "-50%" },
};

function getPosition(revealIdx, pattern) {
  if (pattern === PATTERNS.MEGA_SHOCK || 
      pattern === PATTERNS.GLITCH_REVEAL ||
      pattern === PATTERNS.SCREEN_TAKEOVER ||
      pattern === PATTERNS.ZOOM_PUNCH ||
      pattern === PATTERNS.TRIPLE_FLASH ||
      pattern === PATTERNS.COLOR_STORM) {
    return POSITIONS.CENTER;
  }
  
  if (pattern === PATTERNS.CORNER_BLAST) {
    const corners = [POSITIONS.TOP_LEFT, POSITIONS.TOP_RIGHT, 
                     POSITIONS.BOT_LEFT, POSITIONS.BOT_RIGHT];
    return corners[revealIdx % 4];
  }
  
  if (pattern === PATTERNS.SIDE_SLAM_L) return POSITIONS.MID_LEFT;
  if (pattern === PATTERNS.SIDE_SLAM_R) return POSITIONS.MID_RIGHT;
  if (pattern === PATTERNS.STACK_BUILD) return POSITIONS.CENTER;
  
  const all = [POSITIONS.TOP_CENTER, POSITIONS.MID_LEFT, POSITIONS.BOT_CENTER,
               POSITIONS.MID_RIGHT, POSITIONS.CENTER];
  return all[revealIdx % all.length];
}

// ═════════════════════════════════════════════════════════════════════════════
// 💬 PATTERN INTERRUPTS & ENGAGEMENT (من AI)
// ═════════════════════════════════════════════════════════════════════════════

const isAr = lang === "ar";

const INTERRUPTS = isAr
  ? (pattern_interrupts.ar || ["انتبه! 🚨", "هذا خطير", "صادم!"])
  : (pattern_interrupts.en || ["WAIT!", "WARNING", "SHOCKING!"]);

const QUESTIONS = isAr
  ? (engagement_questions.ar || ["هل توافق؟ 💭", "اكتب رأيك 👇"])
  : (engagement_questions.en || ["Agree? 💭", "Comment below 👇"]);

console.log(`💬 Interrupts: ${INTERRUPTS.length} | Questions: ${QUESTIONS.length}`);

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

function splitIntoMicroUnits(sentence) {
  const words = sentence.trim().split(/\s+/).filter(Boolean);
  const units = [];
  
  for (let i = 0; i < words.length; i++) {
    const w = words[i];
    const wIsPower = isPowerWord(w);
    
    if (wIsPower) {
      units.push({ text: w, isPower: true, wordCount: 1 });
      continue;
    }
    
    if (w.length <= 3 && i + 1 < words.length) {
      const next = words[i + 1];
      if (!isPowerWord(next) && next.length <= 6) {
        units.push({ text: `${w} ${next}`, isPower: false, wordCount: 2 });
        i++;
        continue;
      }
    }
    
    units.push({ text: w, isPower: false, wordCount: 1 });
  }
  
  return units;
}
// ═════════════════════════════════════════════════════════════════════════════
// 🎨 HTML BUILDERS — Pattern Interrupt Screen
// ═════════════════════════════════════════════════════════════════════════════

function buildPatternInterruptHTML(message, accent) {
  const ar = isArabicText(message);
  const dir = ar ? "rtl" : "ltr";
  const font = ar
    ? `"Noto Naskh Arabic","Amiri",serif`
    : `"Inter","Helvetica Neue",Arial,sans-serif`;

  return `<!DOCTYPE html>
<html lang="${ar?"ar":"en"}">
<head>
  <meta charset="UTF-8"/>
  <link href="https://fonts.googleapis.com/css2?family=Noto+Naskh+Arabic:wght@800;900&family=Inter:wght@800;900&display=swap" rel="stylesheet"/>
  <style>
    *{margin:0;padding:0;box-sizing:border-box;}
    html,body{width:${WIDTH}px;height:${HEIGHT}px;overflow:hidden;background:transparent;}
    .overlay{position:absolute;inset:0;background:rgba(0,0,0,0.7);}
    .wrap{position:absolute;inset:0;display:flex;justify-content:center;align-items:center;}
    .box{
      background:${accent};border-radius:36px;
      padding:48px 80px;max-width:920px;
      direction:${dir};
      box-shadow:0 0 120px ${accent}cc,0 25px 80px rgba(0,0,0,0.95);
      transform: scale(1.08);
    }
    .text{
      font-family:${font};
      font-size:${ar?"82px":"78px"};
      font-weight:900;color:#000;
      text-align:center;line-height:1.15;
      text-transform:uppercase;
      letter-spacing:${ar?"0":"-0.02em"};
    }
  </style>
</head>
<body>
  <div class="overlay"></div>
  <div class="wrap">
    <div class="box"><div class="text">${esc(message)}</div></div>
  </div>
</body>
</html>`;
}

// ═════════════════════════════════════════════════════════════════════════════
// 🎨 HTML BUILDERS — Engagement Question Screen
// ═════════════════════════════════════════════════════════════════════════════

function buildEngagementHTML(question, accent) {
  const ar = isArabicText(question);
  const dir = ar ? "rtl" : "ltr";
  const font = ar
    ? `"Noto Naskh Arabic","Amiri",serif`
    : `"Inter","Helvetica Neue",Arial,sans-serif`;

  return `<!DOCTYPE html>
<html lang="${ar?"ar":"en"}">
<head>
  <meta charset="UTF-8"/>
  <link href="https://fonts.googleapis.com/css2?family=Noto+Naskh+Arabic:wght@800&family=Inter:wght@800&display=swap" rel="stylesheet"/>
  <style>
    *{margin:0;padding:0;box-sizing:border-box;}
    html,body{width:${WIDTH}px;height:${HEIGHT}px;overflow:hidden;background:transparent;}
    .wrap{position:absolute;bottom:280px;left:0;right:0;display:flex;justify-content:center;padding:0 60px;}
    .box{
      background:rgba(0,0,0,0.92);
      border:5px solid ${accent};
      border-radius:28px;padding:40px 70px;
      max-width:960px;direction:${dir};
      box-shadow:0 0 60px ${accent}88;
    }
    .text{
      font-family:${font};
      font-size:${ar?"64px":"60px"};
      font-weight:900;color:#fff;
      text-align:center;line-height:1.3;
      text-shadow:0 4px 16px rgba(0,0,0,0.95);
    }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="box"><div class="text">${esc(question)}</div></div>
  </div>
</body>
</html>`;
}

// ═════════════════════════════════════════════════════════════════════════════
// 🎨 HTML BUILDERS — Sentence Screen (12 patterns)
// ═════════════════════════════════════════════════════════════════════════════

function buildSentenceHTML(opts) {
  const {
    text, pattern, accent, position, wordFrameIdx,
    isPower, isHook, sentenceIdx, totalSentences,
  } = opts;

  const ar = isArabicText(text);
  const dir = ar ? "rtl" : "ltr";
  const font = ar
    ? `"Noto Naskh Arabic","Amiri",serif`
    : `"Inter","Helvetica Neue",Arial,sans-serif`;

  // Animation progress
  const wf = wordFrameIdx || 0;
  const prog = Math.min(wf / 5.0, 1.0);
  
  let scale = 1.0;
  let rotate = 0;
  let opacity = 1.0;
  let flashOpacity = 0;
  let blur = 0;
  let translateX = 0;
  let translateY = 0;

  // ── Pattern-specific animations ──────────────────────────────────────────
  switch (pattern) {
    case PATTERNS.MEGA_SHOCK:
      scale = 1.0 + Math.sin(prog * Math.PI) * 0.4;
      flashOpacity = Math.max(0, 0.6 * (1 - prog * 2));
      blur = Math.sin(prog * Math.PI) * 4;
      break;

    case PATTERNS.GLITCH_REVEAL:
      scale = 1.0 + Math.sin(prog * Math.PI) * 0.15;
      if (prog < 0.5) {
        translateX = Math.sin(wf * 5) * 6;
      }
      flashOpacity = Math.max(0, 0.3 * (1 - prog));
      break;

    case PATTERNS.ZOOM_PUNCH:
      scale = 1.5 - (prog * 0.5);
      opacity = prog;
      break;

    case PATTERNS.WORD_EXPLOSION:
      scale = 0.5 + (prog * 0.5);
      opacity = prog;
      break;

    case PATTERNS.SIDE_SLAM_L:
      translateX = (1 - prog) * -300;
      opacity = prog;
      break;

    case PATTERNS.SIDE_SLAM_R:
      translateX = (1 - prog) * 300;
      opacity = prog;
      break;

    case PATTERNS.STACK_BUILD:
      translateY = (1 - prog) * 50;
      opacity = prog;
      break;

    case PATTERNS.SPLIT_FOCUS:
      scale = 0.85 + (prog * 0.15);
      opacity = prog;
      break;

    case PATTERNS.CORNER_BLAST:
      scale = 0.6 + (prog * 0.4);
      rotate = (1 - prog) * 15;
      opacity = prog;
      break;

    case PATTERNS.SCREEN_TAKEOVER:
      scale = 0.3 + (prog * 0.7);
      flashOpacity = Math.max(0, 0.4 * (1 - prog));
      break;

    case PATTERNS.TRIPLE_FLASH:
      const flashCycle = (wf % 3) / 3;
      flashOpacity = flashCycle < 0.5 ? 0.5 : 0;
      scale = 1.0 + Math.sin(prog * Math.PI) * 0.2;
      break;

    case PATTERNS.COLOR_STORM:
      scale = 1.0 + Math.sin(prog * Math.PI) * 0.3;
      rotate = Math.sin(wf * 2) * 5;
      break;
  }

  const transform = `translate(calc(${position.tx} + ${translateX}px), calc(${position.ty} + ${translateY}px)) scale(${scale}) rotate(${rotate}deg)`;

  // ── Determine size based on pattern ──────────────────────────────────────
  let fontSize;
  if (isHook && pattern === PATTERNS.MEGA_SHOCK) {
    fontSize = ar ? `${VAS.MEGA_SIZE}px` : `${VAS.MEGA_SIZE - 30}px`;
  } else if (isPower || pattern === PATTERNS.SCREEN_TAKEOVER) {
    fontSize = ar ? `${VAS.POWER_SIZE}px` : `${VAS.POWER_SIZE - 20}px`;
  } else {
    fontSize = ar ? `${VAS.NORMAL_SIZE}px` : `${VAS.NORMAL_SIZE - 10}px`;
  }

  // ── Text color ───────────────────────────────────────────────────────────
  const textColor = isPower ? accent : "#FFFFFF";

  // ── Text shadow (heavy for impact) ───────────────────────────────────────
  const textShadow = isPower
    ? `0 0 60px ${accent}cc, 0 0 120px ${accent}88, 0 8px 30px rgba(0,0,0,1), 6px 6px 0 rgba(0,0,0,0.9)`
    : `0 0 40px rgba(0,0,0,0.9), 0 6px 25px rgba(0,0,0,1), 4px 4px 0 rgba(0,0,0,0.85)`;

  const stroke = isPower ? "5px" : "3px";

  return `<!DOCTYPE html>
<html lang="${ar?"ar":"en"}">
<head>
  <meta charset="UTF-8"/>
  <link href="https://fonts.googleapis.com/css2?family=Noto+Naskh+Arabic:wght@700;800;900&family=Inter:wght@700;800;900&display=swap" rel="stylesheet"/>
  <style>
    *{margin:0;padding:0;box-sizing:border-box;}
    html,body{width:${WIDTH}px;height:${HEIGHT}px;overflow:hidden;background:transparent;}
    
    .overlay-bottom{
      position:absolute;bottom:0;left:0;right:0;height:60%;
      background:linear-gradient(to top,
        rgba(0,0,0,0.85) 0%,
        rgba(0,0,0,0.5) 40%,
        transparent 100%
      );pointer-events:none;
    }
    
    .overlay-top{
      position:absolute;top:0;left:0;right:0;height:25%;
      background:linear-gradient(to bottom,
        rgba(0,0,0,0.6) 0%, transparent 100%
      );pointer-events:none;
    }
    
    .flash{
      position:absolute;inset:0;
      background:#fff;
      opacity:${flashOpacity};
      mix-blend-mode:overlay;
      pointer-events:none;
      z-index:50;
    }
    
    .text-container{
      position:absolute;
      left:${position.x};
      top:${position.y};
      transform:${transform};
      opacity:${opacity};
      filter:blur(${blur}px);
      direction:${dir};
      max-width:90vw;
      z-index:10;
    }
    
    .text{
      font-family:${font};
      font-size:${fontSize};
      font-weight:900;
      color:${textColor};
      line-height:1.0;
      text-align:center;
      text-shadow:${textShadow};
      -webkit-text-stroke:${stroke} rgba(0,0,0,0.95);
      paint-order:stroke fill;
      letter-spacing:${ar?"0":"-0.03em"};
      word-break:break-word;
      white-space:nowrap;
    }
    
    .progress-bar{
      position:absolute;
      bottom:60px;
      left:80px;
      right:80px;
      height:6px;
      background:rgba(255,255,255,0.2);
      border-radius:3px;
      overflow:hidden;
      z-index:5;
    }
    
    .progress-fill{
      height:100%;
      width:${((sentenceIdx + 1) / totalSentences) * 100}%;
      background:linear-gradient(90deg, ${accent}, ${accent}cc);
      border-radius:3px;
      box-shadow:0 0 15px ${accent};
    }
  </style>
</head>
<body>
  <div class="overlay-top"></div>
  <div class="overlay-bottom"></div>
  <div class="flash"></div>
  
  <div class="text-container">
    <div class="text">${esc(text)}</div>
  </div>
  
  <div class="progress-bar">
    <div class="progress-fill"></div>
  </div>
</body>
</html>`;
}

// ═════════════════════════════════════════════════════════════════════════════
// 🎬 FRAME STATE MAP — VAS Style
// ═════════════════════════════════════════════════════════════════════════════

const SCREEN_TYPE = {
  SENTENCE:   "sentence",
  INTERRUPT:  "interrupt",
  ENGAGEMENT: "engagement",
};

function buildFrameStateMap(timeline, nFrames, realDur) {
  // ── Build micro-units for each sentence ──────────────────────────────────
  const allUnits = [];
  
  for (let sIdx = 0; sIdx < sentences.length; sIdx++) {
    const sentence = sentences[sIdx];
    const units = splitIntoMicroUnits(sentence);
    
    units.forEach((unit, uIdx) => {
      allUnits.push({
        ...unit,
        sentence_idx: sIdx,
        unit_idx:     uIdx,
        global_idx:   allUnits.length,
      });
    });
  }
  
  console.log(`📦 Total micro-units: ${allUnits.length}`);
  
  // ── Calculate timing for each unit ───────────────────────────────────────
  const unitsPerSentence = sentences.map(s => splitIntoMicroUnits(s).length);
  const totalUnits = allUnits.length;
  
  // Time per unit
  const timePerUnit = realDur / totalUnits;
  
  // ── Add pattern interrupts every PATTERN_INTERRUPT seconds ──────────────
  const PI_INTERVAL_FRAMES = Math.round(VAS.PATTERN_INTERRUPT * FPS);
  const EQ_INTERVAL_FRAMES = Math.round(VAS.ENGAGEMENT_INTERVAL * FPS);
  const INTERRUPT_DURATION = Math.round(0.6 * FPS);  // 0.6s for each interrupt
  
  const patternFrames = new Set();
  const engagementFrames = new Set();
  
  for (let f = PI_INTERVAL_FRAMES; f < nFrames - FPS * 2; f += PI_INTERVAL_FRAMES) {
    patternFrames.add(f);
  }
  
  for (let f = EQ_INTERVAL_FRAMES; f < nFrames - FPS * 2; f += EQ_INTERVAL_FRAMES) {
    engagementFrames.add(f);
  }
  
  // ── Build frame map ─────────────────────────────────────────────────────
  const map = new Array(nFrames).fill(null).map(() => ({
    screen_type:   SCREEN_TYPE.SENTENCE,
    unit_idx:      0,
    sentence_idx:  0,
    text:          "",
    pattern:       PATTERNS.WORD_EXPLOSION,
    accent:        getAccent(0),
    is_power:      false,
    is_hook:       false,
    position:      POSITIONS.CENTER,
    word_frame_idx: 0,
    interrupt_idx: 0,
    engagement_idx: 0,
  }));
  
  // ── Fill sentence frames ────────────────────────────────────────────────
  const ULTRA_HOOK_FRAMES = Math.round(VAS.HOOK_ZONE_END * FPS);
  let lastUnitIdx = -1;
  let framesSinceReveal = 0;
  
  for (let f = 0; f < nFrames; f++) {
    const t = f / FPS;
    const unitIdx = Math.min(Math.floor(t / timePerUnit), totalUnits - 1);
    
    if (unitIdx !== lastUnitIdx) {
      framesSinceReveal = 0;
      lastUnitIdx = unitIdx;
    } else {
      framesSinceReveal++;
    }
    
    const unit = allUnits[unitIdx];
    if (!unit) continue;
    
    const isHook = f < ULTRA_HOOK_FRAMES;
    const pattern = selectPattern({
      isHook,
      isPower: unit.isPower,
      revealIdx: unitIdx,
    });
    
    const position = getPosition(unitIdx, pattern);
    const accentIdx = unit.sentence_idx;
    
    map[f] = {
      screen_type:    SCREEN_TYPE.SENTENCE,
      unit_idx:       unitIdx,
      sentence_idx:   unit.sentence_idx,
      text:           unit.text,
      pattern:        pattern,
      accent:         getAccent(accentIdx),
      is_power:       unit.isPower,
      is_hook:        isHook,
      position:       position,
      word_frame_idx: Math.min(framesSinceReveal, 5),
      interrupt_idx:  0,
      engagement_idx: 0,
    };
  }
  
  // ── Overlay pattern interrupts ──────────────────────────────────────────
  let piCounter = 0;
  for (const startFrame of patternFrames) {
    for (let f = startFrame; f < Math.min(startFrame + INTERRUPT_DURATION, nFrames); f++) {
      if (map[f].screen_type === SCREEN_TYPE.SENTENCE) {
        map[f] = {
          ...map[f],
          screen_type:   SCREEN_TYPE.INTERRUPT,
          interrupt_idx: piCounter,
          accent:        getAccent(piCounter + 2),
        };
      }
    }
    piCounter++;
  }
  
  // ── Overlay engagement questions ────────────────────────────────────────
  let eqCounter = 0;
  const ENGAGEMENT_DURATION = Math.round(1.2 * FPS);  // 1.2s
  for (const startFrame of engagementFrames) {
    for (let f = startFrame; f < Math.min(startFrame + ENGAGEMENT_DURATION, nFrames); f++) {
      if (map[f].screen_type === SCREEN_TYPE.SENTENCE) {
        map[f] = {
          ...map[f],
          screen_type:    SCREEN_TYPE.ENGAGEMENT,
          engagement_idx: eqCounter,
          accent:         getAccent(eqCounter + 1),
        };
      }
    }
    eqCounter++;
  }
  
  return map;
}

// ═════════════════════════════════════════════════════════════════════════════
// 🖼️ RENDER ALL UNIQUE PNGs
// ═════════════════════════════════════════════════════════════════════════════

async function renderAllPNGs(page, frameStateMap) {
  const uniqueStates = new Map();
  
  for (const state of frameStateMap) {
    let key;
    if (state.screen_type === SCREEN_TYPE.INTERRUPT) {
      key = `int_${state.interrupt_idx}`;
    } else if (state.screen_type === SCREEN_TYPE.ENGAGEMENT) {
      key = `eng_${state.engagement_idx}`;
    } else {
      key = `s_${state.unit_idx}_${state.word_frame_idx}_${state.pattern}`;
    }
    if (!uniqueStates.has(key)) uniqueStates.set(key, state);
  }
  
  console.log(`  📸 ${uniqueStates.size} unique states`);
  
  // Warm up fonts
  const initHtml = buildSentenceHTML({
    text: "تحميل",
    pattern: PATTERNS.WORD_EXPLOSION,
    accent: getAccent(0),
    position: POSITIONS.CENTER,
    wordFrameIdx: 0,
    isPower: false,
    isHook: false,
    sentenceIdx: 0,
    totalSentences: 1,
  });
  writeFileSync(`${TMP}/init.html`, initHtml, "utf-8");
  await page.goto(`file://${TMP}/init.html`, { waitUntil: "networkidle" });
  await page.waitForTimeout(2000);
  console.log("  ✅ Fonts loaded");
  
  const pngCache = new Map();
  let rendered = 0;
  
  for (const [key, state] of uniqueStates) {
    let html;
    
    if (state.screen_type === SCREEN_TYPE.INTERRUPT) {
      const msg = INTERRUPTS[state.interrupt_idx % INTERRUPTS.length];
      html = buildPatternInterruptHTML(msg, state.accent);
    } else if (state.screen_type === SCREEN_TYPE.ENGAGEMENT) {
      const q = QUESTIONS[state.engagement_idx % QUESTIONS.length];
      html = buildEngagementHTML(q, state.accent);
    } else {
      html = buildSentenceHTML({
        text:           state.text,
        pattern:        state.pattern,
        accent:         state.accent,
        position:       state.position,
        wordFrameIdx:   state.word_frame_idx,
        isPower:        state.is_power,
        isHook:         state.is_hook,
        sentenceIdx:    state.sentence_idx,
        totalSentences: sentences.length,
      });
    }
    
    const htmlPath = `${TMP}/${key}.html`;
    writeFileSync(htmlPath, html, "utf-8");
    await page.goto(`file://${htmlPath}`, { waitUntil: "load" });
    await page.waitForTimeout(30);
    
    const pngPath = `${TMP}/${key}.png`;
    await page.screenshot({ path: pngPath, type: "png", omitBackground: true });
    pngCache.set(key, pngPath);
    rendered++;
    
    if (rendered % 50 === 0 || rendered === uniqueStates.size) {
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
    let key;
    
    if (state.screen_type === SCREEN_TYPE.INTERRUPT) {
      key = `int_${state.interrupt_idx}`;
    } else if (state.screen_type === SCREEN_TYPE.ENGAGEMENT) {
      key = `eng_${state.engagement_idx}`;
    } else {
      key = `s_${state.unit_idx}_${state.word_frame_idx}_${state.pattern}`;
    }
    
    const src = pngCache.get(key);
    const dest = `${dir}/frame_${String(f).padStart(6,"0")}.png`;
    if (!src) continue;
    
    try { symlinkSync(src, dest); } 
    catch { copyFileSync(src, dest); }
  }
  
  return dir;
}

// ═════════════════════════════════════════════════════════════════════════════
// PROCESS BACKGROUND VIDEO
// ═════════════════════════════════════════════════════════════════════════════

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
  
  const TRANSITIONS = ["fade","slideleft","slideright","slideup","fadeblack","wipeleft","circleopen"];
  const XFADE = 0.30;
  
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
  console.log("\n🚀 Starting VAS Renderer (Visual Addiction System)\n");

  const frameStateMap = buildFrameStateMap(word_timeline, totalFrames, effectiveDuration);

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

  // Build clips per sentence
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

  console.log(`\n✨ Concatenating clips with transitions...`);
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
