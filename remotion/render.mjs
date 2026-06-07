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
  display_title  = title,
  emoji_left     = "🔥",
  emoji_right    = "💥",
  sentences      = [],
  audio,
  videos         = [],
  duration_s     = 0,
  power_words    = [],
  aligned        = [],
  lang           = "ar",
  clip_duration  = 3.0,
  clip_durations = [],
  has_hook       = false,
  custom_hook    = "",
  analysis       = {},
  mode           = "words_only",
} = props;

const FPS               = 30;
const WIDTH             = 1080;
const HEIGHT            = 1920;
const TITLE_SLIDE_FRAMES = Math.floor(0.6 * FPS);
const HOOK_FRAMES       = Math.floor(3.0 * FPS);
const HOOK_INTRO_FRAMES = Math.floor(0.4 * FPS);

const safeOut = outputPath
  .replace(/[^a-zA-Z0-9]/g, "_")
  .replace(/_+/g, "_")
  .slice(-22);
const TMP = `/tmp/vsg_${safeOut}`;
mkdirSync(TMP, { recursive: true });

console.log(`📌 ${emoji_left} ${display_title} ${emoji_right}`);
console.log(`🌐 Lang: ${lang.toUpperCase()} | Mode: ${mode}`);


// ═══════════════════════════════════════════════════════════════════════════
// EMOTION COLORS — من الـ analysis
// ═══════════════════════════════════════════════════════════════════════════

const EMOTION_COLORS = {
  curiosity: { word: "#FFD700", glow: "rgba(255,215,0,0.5)",   power: "#FF1744" },
  fear:      { word: "#FF4444", glow: "rgba(255,68,68,0.5)",   power: "#FFD700" },
  hope:      { word: "#00E676", glow: "rgba(0,230,118,0.5)",   power: "#FFFFFF" },
  joy:       { word: "#FF9100", glow: "rgba(255,145,0,0.5)",   power: "#FFFFFF" },
  awe:       { word: "#E040FB", glow: "rgba(224,64,251,0.5)",  power: "#FFD700" },
  surprise:  { word: "#40C4FF", glow: "rgba(64,196,255,0.5)",  power: "#FFD700" },
  desire:    { word: "#FF1744", glow: "rgba(255,23,68,0.5)",   power: "#FFD700" },
  anger:     { word: "#FF1744", glow: "rgba(255,23,68,0.5)",   power: "#FFD700" },
  sadness:   { word: "#82B1FF", glow: "rgba(130,177,255,0.5)", power: "#FFFFFF" },
  default:   { word: "#FFFFFF", glow: "rgba(255,255,255,0.4)", power: "#FF1744" },
};

const emotion = (analysis.primary_emotion || "").toLowerCase();
const COLORS  = EMOTION_COLORS[emotion] || EMOTION_COLORS.default;


// ═══════════════════════════════════════════════════════════════════════════
// ✅ TAG WORD STYLES — شكل الكلمة حسب الـ tag
// ═══════════════════════════════════════════════════════════════════════════

const TAG_WORD_STYLES = {
  shock: {
    colorWord:   "#FFFFFF",
    colorGlow:   "rgba(255,50,50,0.9)",
    scaleMult:   1.30,
    glowSpread:  80,
    strokeColor: "rgba(255,0,0,0.8)",
    strokeWidth: 5,
    brightness:  1.4,
  },
  urgency: {
    colorWord:   "#FF2200",
    colorGlow:   "rgba(255,34,0,0.8)",
    scaleMult:   1.20,
    glowSpread:  60,
    strokeColor: "rgba(0,0,0,0.9)",
    strokeWidth: 4,
    brightness:  1.3,
  },
  intrigue: {
    colorWord:   "#FFD700",
    colorGlow:   "rgba(255,215,0,0.7)",
    scaleMult:   1.0,
    glowSpread:  50,
    strokeColor: "rgba(0,0,0,0.95)",
    strokeWidth: 4,
    brightness:  1.0,
  },
  emotional: {
    colorWord:   "#FF8FAB",
    colorGlow:   "rgba(255,143,171,0.7)",
    scaleMult:   0.95,
    glowSpread:  45,
    strokeColor: "rgba(0,0,0,0.9)",
    strokeWidth: 4,
    brightness:  1.0,
  },
  confident: {
    colorWord:   "#FFFFFF",
    colorGlow:   "rgba(255,255,255,0.6)",
    scaleMult:   1.10,
    glowSpread:  40,
    strokeColor: "rgba(0,0,0,0.95)",
    strokeWidth: 5,
    brightness:  1.2,
  },
  inspiration: {
    colorWord:   "#FFD700",
    colorGlow:   "rgba(255,215,0,0.8)",
    scaleMult:   1.15,
    glowSpread:  70,
    strokeColor: "rgba(0,0,0,0.9)",
    strokeWidth: 4,
    brightness:  1.3,
  },
  wisdom: {
    colorWord:   "#82B1FF",
    colorGlow:   "rgba(130,177,255,0.6)",
    scaleMult:   0.90,
    glowSpread:  35,
    strokeColor: "rgba(0,0,0,0.9)",
    strokeWidth: 3,
    brightness:  0.95,
  },
  desire: {
    colorWord:   "#FFB347",
    colorGlow:   "rgba(255,179,71,0.7)",
    scaleMult:   1.0,
    glowSpread:  45,
    strokeColor: "rgba(0,0,0,0.9)",
    strokeWidth: 4,
    brightness:  1.1,
  },
  calm: {
    colorWord:   "#80DEEA",
    colorGlow:   "rgba(128,222,234,0.5)",
    scaleMult:   0.85,
    glowSpread:  30,
    strokeColor: "rgba(0,0,0,0.85)",
    strokeWidth: 3,
    brightness:  0.9,
  },
  information: {
    colorWord:   "#FFFFFF",
    colorGlow:   "rgba(255,255,255,0.35)",
    scaleMult:   1.0,
    glowSpread:  30,
    strokeColor: "rgba(0,0,0,0.95)",
    strokeWidth: 4,
    brightness:  1.0,
  },
};

const DEFAULT_WORD_STYLE = TAG_WORD_STYLES.information;

function getWordStyle(tag) {
  return TAG_WORD_STYLES[tag] || DEFAULT_WORD_STYLE;
}


// ═══════════════════════════════════════════════════════════════════════════
// TAG TRANSITION CONFIG — تأثير عند نهاية كل جملة
// ═══════════════════════════════════════════════════════════════════════════

const TAG_TRANSITION = {
  shock: {
    flashColor:  "rgba(255,255,255,1.0)",
    flashFrames: 9,
    shakeAmount: 18,
    scaleBoost:  1.12,
    label:       "SHOCK",
  },
  urgency: {
    flashColor:  "rgba(220,0,0,0.85)",
    flashFrames: 7,
    shakeAmount: 12,
    scaleBoost:  1.08,
    label:       "URGENCY",
  },
  intrigue: {
    flashColor:  "rgba(0,0,0,0.6)",
    flashFrames: 10,
    shakeAmount: 5,
    scaleBoost:  1.04,
    label:       "INTRIGUE",
  },
  emotional: {
    flashColor:  "rgba(255,100,150,0.35)",
    flashFrames: 12,
    shakeAmount: 3,
    scaleBoost:  1.02,
    label:       "EMOTIONAL",
  },
  confident: {
    flashColor:  "rgba(255,255,255,0.55)",
    flashFrames: 6,
    shakeAmount: 6,
    scaleBoost:  1.06,
    label:       "CONFIDENT",
  },
  inspiration: {
    flashColor:  "rgba(255,215,0,0.6)",
    flashFrames: 8,
    shakeAmount: 4,
    scaleBoost:  1.07,
    label:       "INSPIRATION",
  },
  wisdom: {
    flashColor:  "rgba(130,177,255,0.3)",
    flashFrames: 14,
    shakeAmount: 2,
    scaleBoost:  1.01,
    label:       "WISDOM",
  },
  desire: {
    flashColor:  "rgba(255,100,180,0.4)",
    flashFrames: 10,
    shakeAmount: 4,
    scaleBoost:  1.03,
    label:       "DESIRE",
  },
  calm: {
    flashColor:  "rgba(100,200,255,0.2)",
    flashFrames: 16,
    shakeAmount: 1,
    scaleBoost:  1.0,
    label:       "CALM",
  },
  information: {
    flashColor:  "rgba(255,255,255,0.15)",
    flashFrames: 6,
    shakeAmount: 0,
    scaleBoost:  1.0,
    label:       "INFORMATION",
  },
};

const DEFAULT_TRANSITION = {
  flashColor:  "rgba(255,255,255,0.3)",
  flashFrames: 7,
  shakeAmount: 4,
  scaleBoost:  1.02,
  label:       "DEFAULT",
};


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
const effectiveDuration = realAudioDuration > 1
  ? realAudioDuration
  : duration_s;
const totalFrames = Math.ceil(effectiveDuration * FPS);

console.log(
  `🎵 Audio: ${realAudioDuration.toFixed(3)}s | ` +
  `Frames: ${totalFrames}`
);

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
    .replace(/&/g, "&amp;")
    .replace(/</g,  "&lt;")
    .replace(/>/g,  "&gt;")
    .replace(/"/g,  "&quot;")
    .replace(/'/g,  "&#039;");

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
    return p && (
      n === p ||
      (p.length >= 3 && n.includes(p)) ||
      (n.length >= 3 && p.includes(n))
    );
  });
}


// ═══════════════════════════════════════════════════════════════════════════
// SENTENCE BOUNDARY MAP
// ═══════════════════════════════════════════════════════════════════════════

function buildSentenceBoundaryMap() {
  if (!aligned || aligned.length === 0) {
    return new Array(totalFrames).fill(null);
  }

  const boundaries = [];
  for (let i = 0; i < aligned.length - 1; i++) {
    const seg     = aligned[i];
    const endTime = parseFloat(seg.end || 0);
    if (endTime <= 0) continue;

    // ✅ يأخذ tag من الـ segment
    const tag    = seg.tag || "information";
    const config = TAG_TRANSITION[tag] || DEFAULT_TRANSITION;

    boundaries.push({
      endTime,
      endFrame: Math.floor(endTime * FPS),
      tag,
      config,
    });
  }

  const map = new Array(totalFrames).fill(null);

  for (const boundary of boundaries) {
    const { endFrame, tag, config } = boundary;
    for (let f = 0; f < config.flashFrames; f++) {
      const frame    = endFrame + f;
      if (frame < 0 || frame >= totalFrames) continue;
      const progress = f / Math.max(config.flashFrames - 1, 1);
      if (map[frame] === null) {
        map[frame] = { tag, config, progress };
      }
    }
  }

  console.log(`\n🎬 Sentence boundaries: ${boundaries.length}`);
  boundaries.forEach(b =>
    console.log(
      `   [${b.tag}] @ ${b.endTime.toFixed(3)}s ` +
      `(frame ${b.endFrame}) — ${b.config.label}`
    )
  );

  return map;
}


// ═══════════════════════════════════════════════════════════════════════════
// CLIP PLAN
// ═══════════════════════════════════════════════════════════════════════════

function buildClipPlan() {
  if (clip_durations && clip_durations.length > 0) {
    let offset = 0;
    const plan = clip_durations.map((dur, i) => {
      const entry = {
        index:     i,
        start:     parseFloat(offset.toFixed(3)),
        duration:  parseFloat(Math.max(dur, 0.5).toFixed(3)),
        videoPath: videos[i % videos.length],
        isHook:    i === 0 && has_hook,
      };
      offset += entry.duration;
      return entry;
    });

    console.log(`\n📋 Clip plan: ${plan.length} clips`);
    plan.forEach(c =>
      console.log(
        `   [${c.index + 1}] ${c.start.toFixed(2)}s → ` +
        `${(c.start + c.duration).toFixed(2)}s ` +
        `(${c.duration.toFixed(2)}s)${c.isHook ? " 🔥" : ""}`
      )
    );
    return plan;
  }

  const totalClips    = Math.max(
    1, Math.floor(effectiveDuration / clip_duration)
  );
  const actualClipDur = effectiveDuration / totalClips;

  console.log(
    `\n📋 Clip plan fallback: ` +
    `${totalClips} × ${actualClipDur.toFixed(2)}s`
  );

  return Array.from({ length: totalClips }, (_, i) => ({
    index:     i,
    start:     parseFloat((i * actualClipDur).toFixed(3)),
    duration:  parseFloat(actualClipDur.toFixed(3)),
    videoPath: videos[i % videos.length],
    isHook:    i === 0 && has_hook,
  }));
}


// ═══════════════════════════════════════════════════════════════════════════
// ✅ WORD LIST — يضيف tag لكل كلمة من الـ segment
// ═══════════════════════════════════════════════════════════════════════════

function buildWordList() {
  const words = [];

  for (const seg of aligned) {
    if (!seg.words || seg.words.length === 0) continue;

    // ✅ أخذ الـ tag من الـ segment
    const segTag = seg.tag || "information";

    for (const w of seg.words) {
      if (!w.word) continue;
      const start = parseFloat(w.start);
      const end   = parseFloat(w.end);
      if (isNaN(start) || isNaN(end) || start < 0 || end <= start)
        continue;

      words.push({
        word:    w.word.trim(),
        start,
        end,
        tag:     segTag,          // ✅ tag لكل كلمة
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
        tag:     "information",
        isPower: isPowerWord(allText[i]),
      });
    }
  }

  words.sort((a, b) => a.start - b.start);

  console.log(`📊 Words: ${words.length}`);
  if (words.length > 0) {
    console.log(
      `   [0]  ${words[0].start.toFixed(3)}s → ` +
      `${words[0].end.toFixed(3)}s ` +
      `"${words[0].word}" [${words[0].tag}]`
    );
    console.log(
      `   [-1] ${words[words.length-1].start.toFixed(3)}s → ` +
      `${words[words.length-1].end.toFixed(3)}s ` +
      `"${words[words.length-1].word}" [${words[words.length-1].tag}]`
    );
  }

  return words;
}


// ═══════════════════════════════════════════════════════════════════════════
// ✅ FRAME STATE MAP — يحفظ tag في كل frame
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
        tag:      w.tag,          // ✅ tag في كل frame
        isPower:  w.isPower,
        progress: (t - w.start) / Math.max(w.end - w.start, 0.001),
      };
    }
  }

  const covered = map.filter(Boolean).length;
  console.log(
    `Coverage: ${covered}/${totalFrames} ` +
    `(${((covered / totalFrames) * 100).toFixed(1)}%)`
  );
  return map;
}


// ═══════════════════════════════════════════════════════════════════════════
// STATE KEY — يأخذ tag بعين الاعتبار
// ═══════════════════════════════════════════════════════════════════════════

function stateKey(state, globalFrame, transitionState) {
  const INTRO_FRAMES = Math.floor(1.0 * FPS);
  const OUTRO_FRAMES = Math.floor(1.0 * FPS);

  if (globalFrame < INTRO_FRAMES) return `title_intro_f${globalFrame}`;
  if (globalFrame >= totalFrames - OUTRO_FRAMES)
    return `title_outro_f${globalFrame}`;

  if (transitionState) {
    const pb = transitionState.progress < 0.5 ? "in" : "out";
    return (
      `tr_${transitionState.tag}_${pb}_` +
      `${state ? state.word : "empty"}_` +
      `${state?.isPower ? 1 : 0}`
    );
  }

  const hook = globalFrame < HOOK_FRAMES ? "h" : "n";
  if (!state) return `empty_${hook}`;

  const p      = state.progress;
  const bucket = p < 0.15 ? "pop" : p > 0.85 ? "fade" : "hold";

  // ✅ نضيف الـ tag في الـ key حتى تتغير الـ PNG
  return `w_${state.word}_${state.tag}_${state.isPower ? 1 : 0}_${hook}_${bucket}`;
}

function hookIntroKey(f) {
  return `hook_intro_f${f}`;
}


// ═══════════════════════════════════════════════════════════════════════════
// ✅ HTML BUILDER — يغير شكل الكلمة حسب الـ tag
// ═══════════════════════════════════════════════════════════════════════════

function buildHTML({
  word,
  tag               = "information",
  isPower           = false,
  isHook            = false,
  globalFrame       = 0,
  progress          = 0.5,
  transitionState   = null,
  isHookIntro       = false,
  hookIntroProgress = 0,
}) {
  const ar       = word ? isArabic(word) : false;
  const dir      = word ? getDir(word) : "ltr";
  const font     = word ? getFontFamily(word) : `"Noto Sans", sans-serif`;
  const langAttr = word ? getLang(word) : "en";

  const titleDir  = getDir(display_title);
  const titleFont = getFontFamily(display_title);

  // ✅ الحصول على style الخاص بالـ tag
  const tagStyle = isPower
    ? {
        colorWord:   COLORS.power,
        colorGlow:   "rgba(255,23,68,0.9)",
        scaleMult:   1.15,
        glowSpread:  90,
        strokeColor: "rgba(0,0,0,0.5)",
        strokeWidth: 2,
        brightness:  1.5,
      }
    : getWordStyle(tag);

  // ── Title animation ──────────────────────────────────────────────────────
  const INTRO_FRAMES = Math.floor(1.0 * FPS);
  const OUTRO_FRAMES = Math.floor(1.0 * FPS);
  const isIntro = globalFrame < INTRO_FRAMES;
  const isOutro = globalFrame >= totalFrames - OUTRO_FRAMES;

  let titleOpacity, titleTranslateY;
  if (isIntro) {
    const t         = globalFrame / INTRO_FRAMES;
    const ease      = 1 - Math.pow(1 - t, 3);
    titleOpacity    = ease;
    titleTranslateY = (1 - ease) * -80;
  } else if (isOutro) {
    const t         = (globalFrame - (totalFrames - OUTRO_FRAMES)) / OUTRO_FRAMES;
    const ease      = Math.pow(t, 2);
    titleOpacity    = 1 - ease;
    titleTranslateY = ease * -60;
  } else {
    titleOpacity    = 1.0;
    titleTranslateY = 0;
  }

  // ── Word animation ───────────────────────────────────────────────────────
  let wordScale, wordOpacity, wordTranslateY;
  if (!word) {
    wordScale = 1.0; wordOpacity = 0; wordTranslateY = 0;
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
    wordScale      = tagStyle.scaleMult; // ✅ يستخدم scaleMult من الـ tag
    wordOpacity    = 1.0;
    wordTranslateY = 0;
  }

  // ── Hook intro ───────────────────────────────────────────────────────────
  let hookIntroScale = 1.0, hookIntroOpacity = 1.0;
  if (isHookIntro) {
    const ease       = 1 - Math.pow(1 - hookIntroProgress, 3);
    hookIntroScale   = 1.4 - ease * 0.4;
    hookIntroOpacity = ease;
  }

  // ── Transition effect ────────────────────────────────────────────────────
  let flashOpacity = 0;
  let flashColor   = "rgba(255,255,255,0)";
  let shakeX = 0, shakeY = 0;
  let transScale = 1.0;

  if (transitionState) {
    const { config, progress: tp } = transitionState;
    flashOpacity = tp < 0.3
      ? tp / 0.3
      : 1 - (tp - 0.3) / 0.7;
    flashOpacity = Math.max(0, Math.min(1, flashOpacity));
    flashColor   = config.flashColor;

    if (config.shakeAmount > 0) {
      const shake = config.shakeAmount * (1 - tp);
      shakeX = Math.sin(globalFrame * 2.3) * shake;
      shakeY = Math.cos(globalFrame * 1.7) * shake;
    }

    if (config.scaleBoost > 1.0 && tp < 0.5) {
      transScale = 1.0 + (config.scaleBoost - 1.0) * (1 - tp * 2);
    }
  }

  // ── Word sizing — يأخذ scaleMult من الـ tag ─────────────────────────────
  const wlen = word ? word.length : 0;
  let baseFontSize = 100;
  if (word) {
    if      (wlen <= 2)  baseFontSize = ar ? 170 : 160;
    else if (wlen <= 4)  baseFontSize = ar ? 150 : 140;
    else if (wlen <= 6)  baseFontSize = ar ? 130 : 120;
    else if (wlen <= 9)  baseFontSize = ar ? 110 : 102;
    else if (wlen <= 12) baseFontSize = ar ? 92  : 86;
    else                 baseFontSize = ar ? 76  : 72;

    // ✅ تعديل حجم الخط حسب الـ tag (shock أكبر، calm أصغر)
    baseFontSize = Math.round(baseFontSize * tagStyle.scaleMult);
    baseFontSize = Math.max(60, Math.min(220, baseFontSize));
  }

  const finalScale   = wordScale * transScale * (isHookIntro ? hookIntroScale : 1.0);
  const finalOpacity = word
    ? wordOpacity * (isHookIntro ? hookIntroOpacity : 1.0)
    : 0;

  const wordTransform = (
    `translate(-50%, calc(-50% + ${wordTranslateY.toFixed(1)}px)) ` +
    `translate(${shakeX.toFixed(2)}px, ${shakeY.toFixed(2)}px) ` +
    `scale(${finalScale.toFixed(4)})`
  );

  // ── Hook text ────────────────────────────────────────────────────────────
  const defaults  = {
    ar: "🔴 لا تتجاوز هذا",
    fr: "🔴 Ne ratez pas ça",
    en: "🔴 Don't skip this",
  };
  const hookText = (custom_hook && custom_hook.trim())
    || defaults[lang] || defaults.en;
  const hookAr   = isArabic(hookText);
  const hookDir  = hookAr ? "rtl" : "ltr";
  const hookFont = getFontFamily(hookText);

  // ── Title sizing ──────────────────────────────────────────────────────────
  const titleArabic   = isArabic(display_title);
  const titleFontSize = titleArabic ? 52 : 46;
  const emojiSize     = titleArabic ? 56 : 50;

  // ── Power word container ──────────────────────────────────────────────────
  const powerContainerStyle = isPower ? `
    background:linear-gradient(135deg,#FF1744 0%,#D50000 100%);
    padding:24px 60px;
    border-radius:9999px;
    border:3px solid rgba(255,255,255,0.3);
    box-shadow:
      0 0 60px rgba(255,23,68,0.8),
      0 0 120px rgba(255,23,68,0.4),
      inset 0 1px 0 rgba(255,255,255,0.2);
  ` : `background:transparent;padding:0;`;

  // ✅ Word text style يعتمد على الـ tagStyle
  const wordTextStyle = isPower ? `
    font-family:${font};
    font-size:${baseFontSize}px;
    font-weight:900;
    color:#FFFFFF;
    line-height:1.15;
    letter-spacing:${ar ? "1px" : "3px"};
    display:block;
    word-break:break-word;
    -webkit-text-stroke:${tagStyle.strokeWidth}px ${tagStyle.strokeColor};
    paint-order:stroke fill;
  ` : `
    font-family:${font};
    font-size:${baseFontSize}px;
    font-weight:900;
    color:${tagStyle.colorWord};
    line-height:1.15;
    letter-spacing:${ar ? "1px" : "3px"};
    display:block;
    word-break:break-word;
    -webkit-text-stroke:${tagStyle.strokeWidth}px ${tagStyle.strokeColor};
    paint-order:stroke fill;
    text-shadow:
      0 0 ${tagStyle.glowSpread}px ${tagStyle.colorGlow},
      0 0 ${tagStyle.glowSpread * 1.5}px ${tagStyle.colorGlow};
    filter:brightness(${tagStyle.brightness});
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
    .flash {
      position:absolute; inset:0;
      background:${flashColor};
      opacity:${flashOpacity.toFixed(4)};
      pointer-events:none; z-index:50;
    }
    .tc {
      position:absolute; top:410px; left:50%;
      width:92%; max-width:980px;
      direction:${titleDir}; text-align:center;
      z-index:30;
      transform:translateX(-50%) translateY(${titleTranslateY.toFixed(2)}px);
      opacity:${titleOpacity.toFixed(4)};
    }
    .tc::after {
      content:''; display:block;
      margin:16px auto 0; width:120px; height:4px;
      border-radius:2px;
      background:linear-gradient(90deg,transparent,#FF1744,transparent);
      opacity:${titleOpacity.toFixed(4)};
    }
    .tt {
      font-family:${titleFont};
      font-size:${titleFontSize}px;
      font-weight:900; color:#FFFFFF;
      display:inline-flex; align-items:center;
      justify-content:center; gap:14px;
      line-height:1.3; text-align:center;
      direction:${titleDir};
      -webkit-text-stroke:2px rgba(0,0,0,0.8);
      paint-order:stroke fill;
      text-shadow:
        0 0 30px rgba(255,23,68,0.6),
        0 4px 20px rgba(0,0,0,0.9),
        2px 2px 0 rgba(0,0,0,0.8),
        -2px -2px 0 rgba(0,0,0,0.8);
    }
    .te { font-size:${emojiSize}px; -webkit-text-stroke:0; }
    .hb {
      position:absolute;
      top:${titleArabic ? "290px" : "270px"};
      left:50%; transform:translateX(-50%);
      background:linear-gradient(
        135deg,rgba(220,0,0,0.95),rgba(160,0,0,0.95)
      );
      color:#fff; font-family:${hookFont};
      font-size:${hookAr ? "32px" : "28px"};
      font-weight:900; padding:12px 38px;
      border-radius:9999px; z-index:25;
      white-space:nowrap; direction:${hookDir};
      border:2px solid rgba(255,120,120,0.4);
      box-shadow:
        0 0 50px rgba(220,0,0,0.7),
        0 0 100px rgba(220,0,0,0.3),
        0 8px 24px rgba(0,0,0,0.5);
    }
    .wc {
      position:absolute; left:50%; top:54%;
      transform:${wordTransform};
      opacity:${finalOpacity.toFixed(4)};
      direction:${dir}; text-align:center;
      z-index:10; width:95%; max-width:1020px;
      transform-origin:center center;
    }
    .wp { display:inline-block; ${powerContainerStyle} }
    .wt { ${wordTextStyle} }
  </style>
</head>
<body>
  <div class="ot"></div>
  <div class="ob"></div>
  <div class="flash"></div>
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
// RENDER PNGs — يمرر tag
// ═══════════════════════════════════════════════════════════════════════════

async function renderAllPNGs(page, frameStateMap, boundaryMap) {
  const unique = new Map();

  for (let f = 0; f < frameStateMap.length; f++) {
    const transitionState   = boundaryMap[f] || null;
    const isHookIntro       = f < HOOK_INTRO_FRAMES;
    const hookIntroProgress = isHookIntro
      ? f / Math.max(HOOK_INTRO_FRAMES - 1, 1) : 0;

    const key = isHookIntro
      ? hookIntroKey(f)
      : stateKey(frameStateMap[f], f, transitionState);

    if (!unique.has(key)) {
      unique.set(key, {
        word:              frameStateMap[f]?.word     ?? null,
        tag:               frameStateMap[f]?.tag      ?? "information", // ✅
        isPower:           frameStateMap[f]?.isPower  ?? false,
        isHook:            f < HOOK_FRAMES,
        globalFrame:       f,
        progress:          frameStateMap[f]?.progress ?? 0.5,
        transitionState,
        isHookIntro,
        hookIntroProgress,
      });
    }
  }

  console.log(`\n📸 ${unique.size} unique states to render`);

  // Font warmup
  for (const [w, l] of [["مرحبا", "ar"], ["Hello", "en"]]) {
    const html = buildHTML({
      word: w, tag: "information", isPower: false, isHook: false,
      globalFrame: TITLE_SLIDE_FRAMES, progress: 0.5,
      transitionState: null, isHookIntro: false, hookIntroProgress: 0,
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
      word:              s.word,
      tag:               s.tag,        // ✅
      isPower:           s.isPower,
      isHook:            s.isHook,
      globalFrame:       s.globalFrame,
      progress:          s.progress,
      transitionState:   s.transitionState,
      isHookIntro:       s.isHookIntro,
      hookIntroProgress: s.hookIntroProgress,
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
// PROCESS BACKGROUND
// ═══════════════════════════════════════════════════════════════════════════

function processBackground(videoPath, duration, outPath, idx, isHook = false) {
  const d  = Math.max(duration, 0.5);
  const fi = Math.min(0.3, d * 0.08);
  const fo = Math.min(0.3, d * 0.08);

  const scaleAndCrop =
    `scale=${WIDTH}:${HEIGHT}:force_original_aspect_ratio=increase,` +
    `crop=${WIDTH}:${HEIGHT},setsar=1`;

  const grading = isHook
    ? `eq=contrast=1.15:brightness=-0.03:saturation=0.88`
    : `eq=contrast=1.08:brightness=-0.02:saturation=0.90`;

  const vf =
    `${scaleAndCrop},` +
    `${grading},` +
    `fade=t=in:st=0:d=${fi.toFixed(3)},` +
    `fade=t=out:st=${(d - fo).toFixed(3)}:d=${fo.toFixed(3)}`;

  const srcDur  = probeDuration(videoPath);
  const loopArg = srcDur > 0 && srcDur < d + 0.5
    ? ["-stream_loop", "-1"] : [];

  let r = spawnSync("ffmpeg", [
    "-y", ...loopArg, "-i", videoPath,
    "-t", d.toFixed(3),
    "-vf", vf,
    "-r", String(FPS),
    "-c:v", "libx264", "-preset", "fast",
    "-crf", isHook ? "16" : "18",
    "-pix_fmt", "yuv420p", "-an",
    outPath,
  ], { stdio: ["ignore", "pipe", "pipe"] });

  if (r.status !== 0) {
    spawnSync("ffmpeg", [
      "-y", "-stream_loop", "-1", "-i", videoPath,
      "-t", d.toFixed(3),
      "-vf", scaleAndCrop,
      "-r", String(FPS),
      "-c:v", "libx264", "-preset", "fast",
      "-crf", "21", "-pix_fmt", "yuv420p", "-an",
      outPath,
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
  let r = spawnSync("ffmpeg", [
    "-y",
    "-i", bgMp4, "-i", capMov,
    "-filter_complex",
    "[1:v]format=rgba[cap];" +
    "[0:v][cap]overlay=0:0:format=auto,format=yuv420p[out]",
    "-map", "[out]", "-map", "0:a:0",
    "-c:v", "libx264", "-preset", "fast", "-crf", "19",
    "-c:a", "aac", "-b:a", "192k",
    "-pix_fmt", "yuv420p", outPath,
  ], { stdio: ["ignore", "pipe", "pipe"] });

  if (r.status !== 0 && audioPth) {
    r = spawnSync("ffmpeg", [
      "-y",
      "-i", bgMp4, "-i", capMov, "-i", audioPth,
      "-filter_complex",
      "[1:v]format=rgba[cap];" +
      "[0:v][cap]overlay=0:0:format=auto,format=yuv420p[out]",
      "-map", "[out]", "-map", "2:a:0",
      "-c:v", "libx264", "-preset", "fast", "-crf", "19",
      "-c:a", "aac", "-b:a", "192k",
      "-pix_fmt", "yuv420p", outPath,
    ], { stdio: ["ignore", "pipe", "pipe"] });
  }

  if (r.status !== 0) {
    console.error("❌ overlayOnBg failed:",
      r.stderr?.toString().slice(-300));
  }
  return outPath;
}

function xfadeConcat(clips, durs) {
  if (clips.length === 0) return "";
  if (clips.length === 1) return clips[0];

  const TRANS  = ["fade","fadeblack","fadegrays","smoothleft","smoothright"];
  const XFADE  = 0.3;
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
    "-c:v", "libx264", "-preset", "fast", "-crf", "18",
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
  console.log(
    `🎵 Audio: ${aDur.toFixed(3)}s | Video: ${vDur.toFixed(3)}s`
  );

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
    const clipPlan   = buildClipPlan();
    const finalClips = [];
    const clipDurs   = [];

    console.log(`📊 ${clipPlan.length} clips total`);

    for (const clip of clipPlan) {
      const { index, duration, videoPath, isHook } = clip;
      process.stdout.write(
        `  [${index + 1}/${clipPlan.length}] ` +
        `${duration.toFixed(2)}s${isHook ? " 🔥" : ""}... `
      );
      const bgMp4 = `${TMP}/bg_${String(index).padStart(3,"0")}.mp4`;
      processBackground(videoPath, duration, bgMp4, index, isHook);
      finalClips.push(bgMp4);
      clipDurs.push(duration);
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
      console.error("❌ words_only requires videos[0]");
      process.exit(1);
    }

    const words         = buildWordList();
    const frameStateMap = buildFrameStateMap(words);
    const boundaryMap   = buildSentenceBoundaryMap();

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
    const pngCache = await renderAllPNGs(page, frameStateMap, boundaryMap);
    await browser.close();
    console.log(`✅ ${pngCache.size} PNGs\n`);

    const frameDir = `${TMP}/frames_words`;
    mkdirSync(frameDir, { recursive: true });

    const emptyPng = pngCache.get(`empty_n`);

    for (let f = 0; f < totalFrames; f++) {
      const transitionState   = boundaryMap[f] || null;
      const isHookIntroFrame  = f < HOOK_INTRO_FRAMES;
      const hookIntroProgress = isHookIntroFrame
        ? f / Math.max(HOOK_INTRO_FRAMES - 1, 1) : 0;

      const key = isHookIntroFrame
        ? hookIntroKey(f)
        : stateKey(frameStateMap[f], f, transitionState);

      const src  = pngCache.get(key) || emptyPng;
      const dest = `${frameDir}/frame_${String(f).padStart(6,"0")}.png`;
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
