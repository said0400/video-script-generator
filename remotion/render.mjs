// remotion/render.mjs
import {
  readFileSync, writeFileSync, mkdirSync,
  copyFileSync, existsSync, symlinkSync,
} from "fs";
import { spawnSync } from "child_process";
import { tmpdir } from "os";
import { join } from "path";
import { chromium } from "playwright";

// ═══════════════════════════════════════════════════════════════════════════
// ARGS & MANIFEST
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
  display_title   = title,
  emoji_left      = "🔥",
  emoji_right     = "💥",
  sentences       = [],
  audio,
  videos          = [],
  duration_s      = 0,
  power_words     = [],
  aligned         = [],
  lang            = "ar",
  clip_duration   = 3.0,
  clip_durations  = [],
  has_hook        = false,
  custom_hook     = "",
  analysis        = {},
  mode            = "words_only",
  content_mode    = "short",
} = props;

// ═══════════════════════════════════════════════════════════════════════════
// CONSTANTS
// ═══════════════════════════════════════════════════════════════════════════

const FPS = 30;
const DIMENSIONS = {
  short: { width: 1080, height: 1920 },
  long:  { width: 1920, height: 1080 },
};
const { width: WIDTH, height: HEIGHT } =
  DIMENSIONS[content_mode] || DIMENSIONS.short;

const isLong  = content_mode === "long";
const isShort = !isLong;

const TITLE_SLIDE_FRAMES = Math.floor(0.6 * FPS);
const HOOK_FRAMES        = Math.floor(3.0 * FPS);
const INTRO_FRAMES       = Math.floor(1.0 * FPS);
const OUTRO_FRAMES       = Math.floor(1.0 * FPS);

// ── Transition Timing ──────────────────────────────────────────────────────
const MAJOR_TRANSITION_DURATION = isShort ? 0.5  : 0.7;
const MINOR_TRANSITION_DURATION = isShort ? 0.35 : 0.5;
const MAJOR_TRANSITION_FRAMES   = Math.floor(MAJOR_TRANSITION_DURATION * FPS);
const MINOR_TRANSITION_FRAMES   = Math.floor(MINOR_TRANSITION_DURATION * FPS);

const XFADE_TRANSITIONS = [
  "fade","fadeblack","fadegrays","smoothleft","smoothright",
];

const BROWSER_ARGS = [
  "--no-sandbox","--disable-setuid-sandbox",
  "--disable-dev-shm-usage","--disable-gpu",
  "--no-zygote","--font-render-hinting=none",
  "--lang=ar,fr,en",
];

// ═══════════════════════════════════════════════════════════════════════════
// TRANSITION TYPE LISTS
// ═══════════════════════════════════════════════════════════════════════════

const MAJOR_TRANSITIONS = [
  "slide_burst",
  "flash_slide",
  "glitch_push",
  "zoom_reveal",
  "flash_black_push",
  "burst_reveal",
  "cinema_crash",
  "double_flash",
];

const MINOR_TRANSITIONS = [
  "soft_fade",
  "gentle_slide",
  "subtle_zoom",
  "light_flash",
  "cross_dissolve",
  "soft_push",
  "blur_transition",
  "gentle_reveal",
];

// ═══════════════════════════════════════════════════════════════════════════
// COLOR & STYLE CONFIGS
// ═══════════════════════════════════════════════════════════════════════════

const TAG_FRAME_COLORS = {
  shock:"#FF1744", urgency:"#FF6E00", intrigue:"#FFD700",
  revelation:"#FFFFFF", inspiration:"#00E676", emotional:"#FF4081",
  confident:"#FFFFFF", wisdom:"#448AFF", calm:"#80DEEA",
  information:"#FFFFFF", desire:"#FF6EC7", curiosity:"#FFEB3B",
  storytelling:"#FFA726", dramatic:"#E91E63", tension:"#FF5722",
  climax:"#FFFFFF", powerful:"#F44336", whisper:"#9C27B0",
  pause:"#607D8B",
};

const EMOTION_COLORS = {
  curiosity:{ word:"#FFD700", glow:"rgba(255,215,0,0.5)",  power:"#FF1744" },
  fear:     { word:"#FF4444", glow:"rgba(255,68,68,0.5)",  power:"#FFD700" },
  hope:     { word:"#00E676", glow:"rgba(0,230,118,0.5)",  power:"#FFFFFF" },
  joy:      { word:"#FF9100", glow:"rgba(255,145,0,0.5)",  power:"#FFFFFF" },
  awe:      { word:"#E040FB", glow:"rgba(224,64,251,0.5)", power:"#FFD700" },
  surprise: { word:"#40C4FF", glow:"rgba(64,196,255,0.5)", power:"#FFD700" },
  desire:   { word:"#FF1744", glow:"rgba(255,23,68,0.5)",  power:"#FFD700" },
  anger:    { word:"#FF1744", glow:"rgba(255,23,68,0.5)",  power:"#FFD700" },
  sadness:  { word:"#82B1FF", glow:"rgba(130,177,255,0.5)",power:"#FFFFFF" },
  default:  { word:"#FFFFFF", glow:"rgba(255,255,255,0.4)",power:"#FF1744" },
};
const emotion = (analysis.primary_emotion || "").toLowerCase();
const COLORS  = EMOTION_COLORS[emotion] || EMOTION_COLORS.default;

const TAG_WORD_STYLES = {
  shock:       { colorWord:"#FFFFFF",  colorGlow:"rgba(255,50,50,0.9)",    scaleMult:1.30, glowSpread:80, strokeColor:"rgba(255,0,0,0.8)",     strokeWidth:5, brightness:1.4  },
  urgency:     { colorWord:"#FF2200",  colorGlow:"rgba(255,34,0,0.8)",     scaleMult:1.20, glowSpread:60, strokeColor:"rgba(0,0,0,0.9)",        strokeWidth:4, brightness:1.3  },
  intrigue:    { colorWord:"#FFD700",  colorGlow:"rgba(255,215,0,0.7)",    scaleMult:1.0,  glowSpread:50, strokeColor:"rgba(0,0,0,0.95)",       strokeWidth:4, brightness:1.0  },
  emotional:   { colorWord:"#FF8FAB",  colorGlow:"rgba(255,143,171,0.7)",  scaleMult:0.95, glowSpread:45, strokeColor:"rgba(0,0,0,0.9)",        strokeWidth:4, brightness:1.0  },
  confident:   { colorWord:"#FFFFFF",  colorGlow:"rgba(255,255,255,0.6)",  scaleMult:1.10, glowSpread:40, strokeColor:"rgba(0,0,0,0.95)",       strokeWidth:5, brightness:1.2  },
  inspiration: { colorWord:"#FFD700",  colorGlow:"rgba(255,215,0,0.8)",    scaleMult:1.15, glowSpread:70, strokeColor:"rgba(0,0,0,0.9)",        strokeWidth:4, brightness:1.3  },
  wisdom:      { colorWord:"#82B1FF",  colorGlow:"rgba(130,177,255,0.6)",  scaleMult:0.90, glowSpread:35, strokeColor:"rgba(0,0,0,0.9)",        strokeWidth:3, brightness:0.95 },
  desire:      { colorWord:"#FFB347",  colorGlow:"rgba(255,179,71,0.7)",   scaleMult:1.0,  glowSpread:45, strokeColor:"rgba(0,0,0,0.9)",        strokeWidth:4, brightness:1.1  },
  calm:        { colorWord:"#80DEEA",  colorGlow:"rgba(128,222,234,0.5)",  scaleMult:0.85, glowSpread:30, strokeColor:"rgba(0,0,0,0.85)",       strokeWidth:3, brightness:0.9  },
  information: { colorWord:"#FFFFFF",  colorGlow:"rgba(255,255,255,0.35)", scaleMult:1.0,  glowSpread:30, strokeColor:"rgba(0,0,0,0.95)",       strokeWidth:4, brightness:1.0  },
  pause:       { colorWord:"#B0BEC5",  colorGlow:"rgba(176,190,197,0.4)",  scaleMult:0.80, glowSpread:25, strokeColor:"rgba(0,0,0,0.8)",        strokeWidth:2, brightness:0.85 },
  whisper:     { colorWord:"#CE93D8",  colorGlow:"rgba(206,147,216,0.6)",  scaleMult:0.88, glowSpread:35, strokeColor:"rgba(0,0,0,0.9)",        strokeWidth:3, brightness:0.9  },
  curiosity:   { colorWord:"#FFF176",  colorGlow:"rgba(255,241,118,0.6)",  scaleMult:1.02, glowSpread:45, strokeColor:"rgba(0,0,0,0.9)",        strokeWidth:4, brightness:1.05 },
  storytelling:{ colorWord:"#FFCC80",  colorGlow:"rgba(255,204,128,0.5)",  scaleMult:0.95, glowSpread:35, strokeColor:"rgba(0,0,0,0.9)",        strokeWidth:3, brightness:1.0  },
  dramatic:    { colorWord:"#EF9A9A",  colorGlow:"rgba(239,154,154,0.7)",  scaleMult:1.12, glowSpread:55, strokeColor:"rgba(100,0,0,0.8)",      strokeWidth:4, brightness:1.15 },
  revelation:  { colorWord:"#FFFFFF",  colorGlow:"rgba(255,255,200,0.9)",  scaleMult:1.25, glowSpread:75, strokeColor:"rgba(200,150,0,0.8)",    strokeWidth:5, brightness:1.45 },
  tension:     { colorWord:"#FF7043",  colorGlow:"rgba(255,112,67,0.75)",  scaleMult:1.15, glowSpread:55, strokeColor:"rgba(0,0,0,0.9)",        strokeWidth:4, brightness:1.25 },
  climax:      { colorWord:"#FFFFFF",  colorGlow:"rgba(255,100,50,0.95)",  scaleMult:1.35, glowSpread:90, strokeColor:"rgba(255,50,0,0.9)",     strokeWidth:6, brightness:1.5  },
  powerful:    { colorWord:"#ECEFF1",  colorGlow:"rgba(236,239,241,0.65)", scaleMult:1.12, glowSpread:45, strokeColor:"rgba(0,0,0,0.95)",       strokeWidth:5, brightness:1.2  },
};
const DEFAULT_WORD_STYLE = TAG_WORD_STYLES.information;
const POWER_STYLE = {
  colorWord:"#FF1744", colorGlow:"rgba(255,23,68,0.9)",
  scaleMult:1.15, glowSpread:90,
  strokeColor:"rgba(0,0,0,0.5)", strokeWidth:2, brightness:1.5,
};
function getWordStyle(tag) { return TAG_WORD_STYLES[tag] || DEFAULT_WORD_STYLE; }

const TAG_TRANSITION = {
  shock:       { flashColor:"rgba(255,255,255,1.0)",   flashFrames:9,  shakeAmount:18, scaleBoost:1.12 },
  urgency:     { flashColor:"rgba(220,0,0,0.85)",      flashFrames:7,  shakeAmount:12, scaleBoost:1.08 },
  intrigue:    { flashColor:"rgba(0,0,0,0.6)",         flashFrames:10, shakeAmount:5,  scaleBoost:1.04 },
  emotional:   { flashColor:"rgba(255,100,150,0.35)",  flashFrames:12, shakeAmount:3,  scaleBoost:1.02 },
  confident:   { flashColor:"rgba(255,255,255,0.55)",  flashFrames:6,  shakeAmount:6,  scaleBoost:1.06 },
  inspiration: { flashColor:"rgba(255,215,0,0.6)",     flashFrames:8,  shakeAmount:4,  scaleBoost:1.07 },
  wisdom:      { flashColor:"rgba(130,177,255,0.3)",   flashFrames:14, shakeAmount:2,  scaleBoost:1.01 },
  desire:      { flashColor:"rgba(255,100,180,0.4)",   flashFrames:10, shakeAmount:4,  scaleBoost:1.03 },
  calm:        { flashColor:"rgba(100,200,255,0.2)",   flashFrames:16, shakeAmount:1,  scaleBoost:1.0  },
  information: { flashColor:"rgba(255,255,255,0.15)",  flashFrames:6,  shakeAmount:0,  scaleBoost:1.0  },
  pause:       { flashColor:"rgba(0,0,0,0.7)",         flashFrames:18, shakeAmount:0,  scaleBoost:1.0  },
  whisper:     { flashColor:"rgba(100,0,150,0.4)",     flashFrames:12, shakeAmount:2,  scaleBoost:1.02 },
  curiosity:   { flashColor:"rgba(255,241,118,0.4)",   flashFrames:10, shakeAmount:3,  scaleBoost:1.03 },
  storytelling:{ flashColor:"rgba(255,200,100,0.25)",  flashFrames:8,  shakeAmount:1,  scaleBoost:1.01 },
  dramatic:    { flashColor:"rgba(180,0,0,0.6)",       flashFrames:12, shakeAmount:10, scaleBoost:1.10 },
  revelation:  { flashColor:"rgba(255,255,200,0.9)",   flashFrames:10, shakeAmount:14, scaleBoost:1.15 },
  tension:     { flashColor:"rgba(255,100,0,0.5)",     flashFrames:8,  shakeAmount:10, scaleBoost:1.08 },
  climax:      { flashColor:"rgba(255,255,255,0.95)",  flashFrames:11, shakeAmount:20, scaleBoost:1.18 },
  powerful:    { flashColor:"rgba(255,255,255,0.6)",   flashFrames:7,  shakeAmount:7,  scaleBoost:1.07 },
};
const DEFAULT_TRANSITION_CFG = {
  flashColor:"rgba(255,255,255,0.3)", flashFrames:7,
  shakeAmount:4, scaleBoost:1.02,
};

// ═══════════════════════════════════════════════════════════════════════════
// TMP DIR
// ═══════════════════════════════════════════════════════════════════════════

const safeOut = outputPath.replace(/[^a-zA-Z0-9]/g,"_")
                          .replace(/_+/g,"_").slice(-22);
const TMP = join(tmpdir(), `vsg_${safeOut}`);
mkdirSync(TMP, { recursive:true });

console.log(`📌 ${emoji_left} ${display_title} ${emoji_right}`);
console.log(`🌐 Lang:${lang.toUpperCase()} | Mode:${mode} | Content:${content_mode.toUpperCase()} | ${WIDTH}×${HEIGHT}`);

// ═══════════════════════════════════════════════════════════════════════════
// GPS & METADATA
// ═══════════════════════════════════════════════════════════════════════════

const GPS_LOCATIONS = {
  ar:{ city:"Riyadh",   country:"Saudi Arabia",  lat:"24.7136", lon:"46.6753", latRef:"N", lonRef:"E", iso6709:"+24.7136+046.6753/" },
  fr:{ city:"Paris",    country:"France",         lat:"48.8566", lon:"2.3522",  latRef:"N", lonRef:"E", iso6709:"+48.8566+002.3522/" },
  en:{ city:"New York", country:"United States",  lat:"40.7128", lon:"74.0060", latRef:"N", lonRef:"W", iso6709:"+40.7128-074.0060/" },
};
const location = GPS_LOCATIONS[lang] || GPS_LOCATIONS.ar;

function buildiPhoneMetadata() {
  const now     = new Date();
  const dateISO = now.toISOString();
  const dateStr = dateISO.replace(/[-:]/g,"").split(".")[0];
  const serial  = "F"+Math.random().toString(36).substring(2,10).toUpperCase();
  const uuid    = [
    Math.random().toString(16).substring(2,10),
    Math.random().toString(16).substring(2,6),
    Math.random().toString(16).substring(2,6),
    Math.random().toString(16).substring(2,6),
    Math.random().toString(16).substring(2,14),
  ].join("-").toUpperCase();
  const g  = () => (Math.random()*0.02-0.01).toFixed(6);
  const a  = () => (Math.random()*0.1-0.05).toFixed(6);
  const lonSign = location.lonRef === "W" ? "-" : "";
  return [
    "-map_metadata","-1",
    "-metadata","make=Apple",
    "-metadata","model=iPhone 17 Pro Max",
    "-metadata","software=Adobe Premiere Pro 25.0",
    "-metadata","encoder=Adobe Premiere Pro 25.0",
    "-metadata","handler_name=Core Media Data Handler",
    "-metadata","com.apple.quicktime.make=Apple",
    "-metadata","com.apple.quicktime.model=iPhone 17 Pro Max",
    "-metadata","com.apple.quicktime.software=iOS 18.2",
    "-metadata",`com.apple.quicktime.creationdate=${dateISO}`,
    "-metadata",`com.apple.quicktime.location.ISO6709=${location.iso6709}`,
    "-metadata",`com.apple.quicktime.location.name=${location.city}, ${location.country}`,
    "-metadata",`com.apple.quicktime.content.identifier=${uuid}`,
    "-metadata","com.apple.quicktime.fullframerate=1",
    "-metadata",`creation_time=${dateISO}`,
    "-metadata",`date=${dateStr}`,
    "-metadata","focal_length=9",
    "-metadata","aperture=f/2.8",
    "-metadata","iso=64",
    "-metadata","exposure_time=1/120",
    "-metadata","white_balance=Auto",
    "-metadata","flash=No Flash",
    "-metadata","lens=Apple iPhone 17 Pro Max back camera 9mm f/2.8",
    "-metadata","lens_make=Apple",
    "-metadata","lens_serial_number="+serial,
    "-metadata",`location=${location.iso6709}`,
    "-metadata",`GPS_latitude=${location.lat}`,
    "-metadata",`GPS_latitude_ref=${location.latRef}`,
    "-metadata",`GPS_longitude=${lonSign}${location.lon}`,
    "-metadata",`GPS_longitude_ref=${location.lonRef}`,
    "-metadata","GPS_altitude=50",
    "-metadata","GPS_map_datum=WGS-84",
    "-metadata",`GPS_date_stamp=${dateStr.substring(0,8)}`,
    "-metadata","media_type=Video",
    "-metadata","hdr_format=Dolby Vision",
    "-metadata","color_primaries=BT.2020",
    "-metadata","stabilization=OIS",
    "-metadata",`gyroscope_x=${g()}`,
    "-metadata",`gyroscope_y=${g()}`,
    "-metadata",`gyroscope_z=${g()}`,
    "-metadata",`accelerometer_x=${a()}`,
    "-metadata",`accelerometer_y=${a()}`,
    "-metadata",`accelerometer_z=${(9.8+parseFloat(a())).toFixed(6)}`,
    "-metadata","comment=",
    "-metadata","artist=",
    "-metadata","copyright=",
    "-metadata","description=",
    "-metadata","album=",
    "-metadata","genre=",
  ];
}

// ═══════════════════════════════════════════════════════════════════════════
// UTILITY FUNCTIONS
// ═══════════════════════════════════════════════════════════════════════════

function safeKey(str, maxLen=25) {
  return (str||"").slice(0,maxLen*2)
    .replace(/[^a-zA-Z0-9\u0600-\u06FF]/g,"_")
    .replace(/_+/g,"_").slice(0,maxLen);
}
const esc = (s) => (s||"").toString()
  .replace(/&/g,"&amp;").replace(/</g,"&lt;")
  .replace(/>/g,"&gt;").replace(/"/g,"&quot;")
  .replace(/'/g,"&#039;");

function normalizeWord(w) {
  return (w||"").toString()
    .replace(/[.,!?؟،;:"'(){}[\]<>«»…]/g,"")
    .trim().toLowerCase();
}

const isArabic = (t) => /[\u0600-\u06FF]/.test(t);
const isFrench = (t) => /[àâçéèêëîïôùûüÿœæ]/i.test(t);
function getFontFamily(text) {
  return isArabic(text)
    ? `"Noto Naskh Arabic","Amiri",serif`
    : `"Noto Sans","DejaVu Sans",sans-serif`;
}
function getDir(text)  { return isArabic(text) ? "rtl" : "ltr"; }
function getLang(text) {
  if (isArabic(text)) return "ar";
  if (isFrench(text)) return "fr";
  return "en";
}

function probeDuration(fp) {
  const r = spawnSync("ffprobe",[
    "-v","error","-show_entries","format=duration",
    "-of","default=noprint_wrappers=1:nokey=1", fp,
  ],{ stdio:["ignore","pipe","pipe"] });
  return parseFloat(r.stdout.toString().trim()) || 0;
}

function hasAudioStream(filePath) {
  const r = spawnSync("ffprobe",[
    "-v","error","-select_streams","a",
    "-show_entries","stream=codec_type",
    "-of","csv=p=0", filePath,
  ],{ stdio:["ignore","pipe","pipe"] });
  return r.stdout.toString().trim().includes("audio");
}

function runFFmpeg(args, opts={}) {
  return spawnSync("ffmpeg", args, {
    stdio:["ignore","pipe","pipe"], ...opts,
  });
}

function seededRandom(seed) {
  const x = Math.sin(seed+1)*10000;
  return x - Math.floor(x);
}

function isPowerWord(w) {
  if (!power_words.length) return false;
  const n = normalizeWord(w);
  if (n.length < 2) return false;
  return power_words.some(pw => {
    const p = normalizeWord(pw);
    return p && (
      n===p ||
      (p.length>=3 && n.includes(p)) ||
      (n.length>=3 && p.includes(n))
    );
  });
}

// ✅ linkFrame — بدون أي dynamic import
function linkFrame(src, dst) {
  if (!src || !existsSync(src)) {
    console.warn(`⚠️ Missing frame: ${src}`);
    return;
  }
  try {
    symlinkSync(src, dst);
  } catch {
    try { copyFileSync(src, dst); }
    catch (e) { console.error(`❌ linkFrame failed: ${e.message}`); }
  }
}

// ═══════════════════════════════════════════════════════════════════════════
// AUDIO & TIMING
// ═══════════════════════════════════════════════════════════════════════════

const realAudioDuration = probeDuration(audio);
const effectiveDuration = realAudioDuration > 1 ? realAudioDuration : duration_s;
const totalFrames       = Math.ceil(effectiveDuration * FPS);
console.log(`🎵 Audio: ${realAudioDuration.toFixed(3)}s | Frames: ${totalFrames}`);

// ═══════════════════════════════════════════════════════════════════════════
// SECTION DETECTION
// ═══════════════════════════════════════════════════════════════════════════

const CTA_TAGS = ["confident","inspiration","powerful"];

function detectVideoSections() {
  if (!aligned || aligned.length===0) return [];
  const sections = [];
  const total    = aligned.length;
  for (let i=0; i<total; i++) {
    const seg = aligned[i];
    const tag = seg.tag || "information";
    let sectionType;
    if      (i === 0)                                           sectionType = "hook";
    else if (i === total-1)                                     sectionType = "cta";
    else if (i === total-2 && CTA_TAGS.includes(tag))          sectionType = "cta";
    else                                                        sectionType = "body";
    sections.push({
      type:  sectionType,
      start: parseFloat(seg.start||0),
      end:   parseFloat(seg.end||0),
      idx:   i,
      tag,
    });
  }
  return sections;
}

function detectTransitionPoints(sections) {
  const major = [];
  const minor = [];
  for (let i=0; i<sections.length-1; i++) {
    const cur  = sections[i];
    const next = sections[i+1];
    const pt   = {
      time:     cur.end,
      fromType: cur.type,
      toType:   next.type,
      tag:      next.tag,
      idx:      i,
    };
    if (cur.type !== next.type) major.push({ ...pt, level:"major" });
    else                        minor.push({ ...pt, level:"minor" });
  }
  console.log(`\n🎬 Transitions: ${major.length} major | ${minor.length} minor`);
  major.forEach(p=>console.log(`   💥 @${p.time.toFixed(2)}s: ${p.fromType}→${p.toType} [${p.tag}]`));
  minor.forEach(p=>console.log(`   ✨ @${p.time.toFixed(2)}s: [${p.tag}]`));
  return { major, minor };
}

// ═══════════════════════════════════════════════════════════════════════════
// SENTENCE BOUNDARY MAP
// ═══════════════════════════════════════════════════════════════════════════

function buildSentenceBoundaryMap() {
  if (!aligned || aligned.length===0) return new Map();
  const map = new Map();
  for (let i=0; i<aligned.length-1; i++) {
    const seg = aligned[i];
    const et  = parseFloat(seg.end||0);
    if (et<=0) continue;
    const tag = seg.tag || "information";
    const cfg = TAG_TRANSITION[tag] || DEFAULT_TRANSITION_CFG;
    const ef  = Math.floor(et*FPS);
    for (let f=0; f<cfg.flashFrames; f++) {
      const fr = ef+f;
      if (fr>=0 && fr<totalFrames && !map.has(fr)) {
        map.set(fr,{ tag, config:cfg, progress:f/Math.max(cfg.flashFrames-1,1) });
      }
    }
  }
  const count = aligned.slice(0,-1).filter(s=>parseFloat(s.end||0)>0).length;
  console.log(`\n🎬 Sentence boundaries: ${count}`);
  return map;
}

// ═══════════════════════════════════════════════════════════════════════════
// CLIP PLAN
// ═══════════════════════════════════════════════════════════════════════════

function buildClipPlan() {
  if (clip_durations && clip_durations.length>0) {
    let offset = 0;
    const plan = clip_durations.map((d,i) => {
      const entry = {
        index:     i,
        start:     parseFloat(offset.toFixed(3)),
        duration:  parseFloat(Math.max(d,0.5).toFixed(3)),
        videoPath: videos[i % videos.length],
        isHook:    i===0 && has_hook && isShort,
      };
      offset += entry.duration;
      return entry;
    });
    logClipPlan(plan);
    return plan;
  }
  const totalClips = Math.max(1, Math.floor(effectiveDuration/clip_duration));
  const avgDur     = effectiveDuration/totalClips;
  const plan = Array.from({length:totalClips},(_,i)=>({
    index:    i,
    start:    parseFloat((i*avgDur).toFixed(3)),
    duration: parseFloat(avgDur.toFixed(3)),
    videoPath:videos[i % videos.length],
    isHook:   i===0 && has_hook && isShort,
  }));
  logClipPlan(plan);
  return plan;
}

function logClipPlan(plan) {
  console.log(`\n📋 Clip plan: ${plan.length} clips [${content_mode.toUpperCase()}]`);
  plan.forEach(c=>{
    const end = (c.start+c.duration).toFixed(2);
    console.log(`   [${c.index+1}] ${c.start.toFixed(2)}s→${end}s (${c.duration.toFixed(2)}s)${c.isHook?" 🔥":""}`);
  });
}

// ═══════════════════════════════════════════════════════════════════════════
// WORD LIST & FRAME STATE
// ═══════════════════════════════════════════════════════════════════════════

function buildWordList() {
  const words = [];
  for (const seg of aligned) {
    if (!seg.words||seg.words.length===0) continue;
    const segTag = seg.tag||"information";
    for (const x of seg.words) {
      if (!x.word) continue;
      const s = parseFloat(x.start), e = parseFloat(x.end);
      if (isNaN(s)||isNaN(e)||s<0||e<=s) continue;
      words.push({ word:x.word.trim(), start:s, end:e, tag:segTag, isPower:isPowerWord(x.word) });
    }
  }
  if (words.length===0 && sentences.length>0) {
    console.log("⚠️ No word alignment — equal split");
    const allWords = sentences.join(" ").split(/\s+/).filter(Boolean);
    const perWord  = effectiveDuration/Math.max(allWords.length,1);
    for (let i=0; i<allWords.length; i++) {
      words.push({ word:allWords[i], start:i*perWord, end:(i+1)*perWord, tag:"information", isPower:isPowerWord(allWords[i]) });
    }
  }
  words.sort((a,b)=>a.start-b.start);
  console.log(`📊 Words: ${words.length}`);
  if (words.length>0) {
    const f=words[0], l=words[words.length-1];
    console.log(`   [0]  ${f.start.toFixed(3)}s→${f.end.toFixed(3)}s "${f.word}" [${f.tag}]`);
    console.log(`   [-1] ${l.start.toFixed(3)}s→${l.end.toFixed(3)}s "${l.word}" [${l.tag}]`);
  }
  return words;
}

function findWordAtTime(words, t) {
  let lo=0, hi=words.length-1;
  while (lo<=hi) {
    const mid = Math.floor((lo+hi)/2);
    if      (t < words[mid].start) hi=mid-1;
    else if (t >= words[mid].end)  lo=mid+1;
    else return words[mid];
  }
  return null;
}

function buildFrameStateMap(words) {
  const map = new Array(totalFrames).fill(null);
  if (!words.length) return map;
  for (let f=0; f<totalFrames; f++) {
    const t = f/FPS;
    const w = findWordAtTime(words,t);
    if (w) map[f]={ word:w.word, tag:w.tag, isPower:w.isPower, progress:(t-w.start)/Math.max(w.end-w.start,0.001) };
  }
  const cov = map.filter(Boolean).length;
  console.log(`Coverage: ${cov}/${totalFrames} (${((cov/totalFrames)*100).toFixed(1)}%)`);
  return map;
}

function buildSentenceMap() {
  if (!aligned||aligned.length===0) return new Array(totalFrames).fill(null);
  const map = new Array(totalFrames).fill(null);
  for (const seg of aligned) {
    const ss = Math.floor(parseFloat(seg.start||0)*FPS);
    const se = Math.ceil(parseFloat(seg.end||0)*FPS);
    const sn = seg.sentence||"";
    for (let f=ss; f<se&&f<totalFrames; f++) map[f]=sn;
  }
  return map;
}

// ═══════════════════════════════════════════════════════════════════════════
// ANIMATION COMPUTATIONS
// ═══════════════════════════════════════════════════════════════════════════

function computeTitleAnimation(gf) {
  if (gf<INTRO_FRAMES) {
    const t=gf/INTRO_FRAMES, e=1-Math.pow(1-t,3);
    return { opacity:e, translateY:(1-e)*-80 };
  }
  if (gf>=totalFrames-OUTRO_FRAMES) {
    const t=(gf-(totalFrames-OUTRO_FRAMES))/OUTRO_FRAMES;
    return { opacity:1-Math.pow(t,2), translateY:Math.pow(t,2)*-60 };
  }
  return { opacity:1.0, translateY:0 };
}

function computeWordAnimation(progress, scaleMult) {
  if (progress<0.15) {
    const t=progress/0.15, e=1-Math.pow(1-t,2);
    return { scale:0.6+e*0.48, opacity:Math.min(1,t*3), translateY:(1-e)*30 };
  }
  if (progress>0.85) {
    const t=(progress-0.85)/0.15;
    return { scale:1.0-t*0.05, opacity:1-t*0.3, translateY:0 };
  }
  return { scale:scaleMult, opacity:1.0, translateY:0 };
}

function computeTransitionEffect(transState, gf) {
  if (!transState) return { flashOpacity:0, flashColor:"rgba(0,0,0,0)", shakeX:0, shakeY:0, transScale:1.0 };
  const { config:c, progress:tp } = transState;
  let fo = tp<0.3 ? tp/0.3 : 1-(tp-0.3)/0.7;
  fo = Math.max(0,Math.min(1,fo));
  let sx=0, sy=0;
  if (c.shakeAmount>0) {
    const s=c.shakeAmount*(1-tp);
    sx=Math.sin(gf*2.3)*s; sy=Math.cos(gf*1.7)*s;
  }
  let ts=1.0;
  if (c.scaleBoost>1.0&&tp<0.5) ts=1.0+(c.scaleBoost-1.0)*(1-tp*2);
  return { flashOpacity:fo, flashColor:c.flashColor, shakeX:sx, shakeY:sy, transScale:ts };
}

// ═══════════════════════════════════════════════════════════════════════════
// FONT SIZE
// ═══════════════════════════════════════════════════════════════════════════

const SHORT_FONT_SIZES = [
  {maxLen:2,ar:170,en:160},{maxLen:4,ar:150,en:140},
  {maxLen:6,ar:130,en:120},{maxLen:9,ar:110,en:102},
  {maxLen:12,ar:92,en:86},{maxLen:99,ar:76,en:72},
];
const LONG_FONT_SIZES = [
  {maxLen:2,ar:130,en:120},{maxLen:4,ar:110,en:100},
  {maxLen:6,ar:95,en:86}, {maxLen:9,ar:80,en:72},
  {maxLen:12,ar:68,en:62},{maxLen:99,ar:56,en:52},
];

function computeFontSize(word, isAr, scaleMult, isLongMode) {
  if (!word) return 100;
  const wl    = word.length;
  const table = isLongMode ? LONG_FONT_SIZES : SHORT_FONT_SIZES;
  const minSz = isLongMode ? 48 : 60;
  const maxSz = isLongMode ? 160 : 220;
  let base    = isLongMode ? 80 : 100;
  for (const {maxLen,ar,en} of table) {
    if (wl<=maxLen) { base=isAr?ar:en; break; }
  }
  return Math.max(minSz, Math.min(maxSz, Math.round(base*scaleMult)));
}

// ═══════════════════════════════════════════════════════════════════════════
// HOOK TEXT
// ═══════════════════════════════════════════════════════════════════════════

const HOOK_DEFAULTS = {
  ar:"🔴 لا تتجاوز هذا",
  fr:"🔴 Ne ratez pas ça",
  en:"🔴 Don't skip this",
};
function getHookText() {
  return (custom_hook&&custom_hook.trim()) || HOOK_DEFAULTS[lang] || HOOK_DEFAULTS.en;
}

// ═══════════════════════════════════════════════════════════════════════════
// STATE KEY FUNCTIONS
// ═══════════════════════════════════════════════════════════════════════════

function stateKey(state, gf, ts) {
  if (gf<INTRO_FRAMES)              return `intro_f${gf}`;
  if (gf>=totalFrames-OUTRO_FRAMES) return `outro_f${gf}`;
  if (ts) {
    const pb = ts.progress<0.5?"in":"out";
    return `tr_${ts.tag}_${pb}_${safeKey(state?state.word:"empty",15)}_${state?.isPower?1:0}`;
  }
  const h = gf<HOOK_FRAMES?"h":"n";
  if (!state) return `empty_${h}`;
  const p = state.progress;
  const b = p<0.15?"pop":p>0.85?"fade":"hold";
  return `w_${safeKey(state.word,15)}_${state.tag}_${state.isPower?1:0}_${h}_${b}`;
}

function longStateKey(ws, ts, sn) {
  const wk  = ws?`${safeKey(ws.word,15)}_${ws.tag}_${ws.isPower?1:0}`:"empty";
  const sk  = safeKey(sn,25);
  const tk  = ts?`tr_${ts.tag}_${Math.floor(ts.progress*4)}`:"n";
  const pb  = ws?(ws.progress<0.15?"pop":ws.progress>0.85?"fade":"hold"):"hold";
  return `long_${wk}_${tk}_${pb}_${sk}`;
}

// ═══════════════════════════════════════════════════════════════════════════
// HTML BUILDER — SHORT
// ═══════════════════════════════════════════════════════════════════════════

function buildHTMLShort({
  word, tag="information", isPower=false, isHook=false,
  globalFrame=0, progress=0.5, transitionState=null,
}) {
  const ar   = word?isArabic(word):false;
  const dir  = word?getDir(word):"ltr";
  const font = word?getFontFamily(word):`"Noto Sans",sans-serif`;
  const la   = word?getLang(word):"en";
  const td   = getDir(display_title);
  const tf   = getFontFamily(display_title);
  const ts   = isPower?POWER_STYLE:getWordStyle(tag);
  const ta   = computeTitleAnimation(globalFrame);
  const wa   = word?computeWordAnimation(progress,ts.scaleMult):{scale:1,opacity:0,translateY:0};
  const tr   = computeTransitionEffect(transitionState,globalFrame);
  const fs   = computeFontSize(word,ar,ts.scaleMult,false);
  const fsc  = wa.scale*tr.transScale;
  const fo   = word?wa.opacity:0;
  const wt   = `translate(-50%,calc(-50% + ${wa.translateY.toFixed(1)}px)) translate(${tr.shakeX.toFixed(2)}px,${tr.shakeY.toFixed(2)}px) scale(${fsc.toFixed(4)})`;
  const ht   = getHookText();
  const ha   = isArabic(ht);
  const hd   = ha?"rtl":"ltr";
  const hf   = getFontFamily(ht);
  const tia  = isArabic(display_title);
  const tfs  = tia?52:46;
  const es   = tia?56:50;
  const pcs  = isPower
    ? `background:linear-gradient(135deg,#FF1744,#D50000);padding:24px 60px;border-radius:9999px;border:3px solid rgba(255,255,255,0.3);box-shadow:0 0 60px rgba(255,23,68,0.8),0 0 120px rgba(255,23,68,0.4);`
    : `background:transparent;padding:0;`;
  const wts  = isPower
    ? `font-family:${font};font-size:${fs}px;font-weight:900;color:#FFF;line-height:1.15;letter-spacing:${ar?"1px":"3px"};display:block;word-break:break-word;-webkit-text-stroke:${ts.strokeWidth}px ${ts.strokeColor};paint-order:stroke fill;`
    : `font-family:${font};font-size:${fs}px;font-weight:900;color:${ts.colorWord};line-height:1.15;letter-spacing:${ar?"1px":"3px"};display:block;word-break:break-word;-webkit-text-stroke:${ts.strokeWidth}px ${ts.strokeColor};paint-order:stroke fill;text-shadow:0 0 ${ts.glowSpread}px ${ts.colorGlow},0 0 ${ts.glowSpread*1.5}px ${ts.colorGlow};filter:brightness(${ts.brightness});`;

  return `<!DOCTYPE html><html lang="${la}"><head><meta charset="UTF-8"/><style>
*{margin:0;padding:0;box-sizing:border-box;}
html,body{width:${WIDTH}px;height:${HEIGHT}px;overflow:hidden;background:transparent;}
.ot{position:absolute;top:0;left:0;right:0;height:40%;background:linear-gradient(to bottom,rgba(0,0,0,0.85) 0%,rgba(0,0,0,0.5) 50%,transparent 100%);pointer-events:none;z-index:1;}
.ob{position:absolute;bottom:0;left:0;right:0;height:42%;background:linear-gradient(to top,rgba(0,0,0,0.88) 0%,rgba(0,0,0,0.45) 65%,transparent 100%);pointer-events:none;z-index:1;}
.flash{position:absolute;inset:0;background:${tr.flashColor};opacity:${tr.flashOpacity.toFixed(4)};pointer-events:none;z-index:50;}
.tc{position:absolute;top:410px;left:50%;width:92%;max-width:980px;direction:${td};text-align:center;z-index:30;transform:translateX(-50%) translateY(${ta.translateY.toFixed(2)}px);opacity:${ta.opacity.toFixed(4)};}
.tc::after{content:'';display:block;margin:16px auto 0;width:120px;height:4px;border-radius:2px;background:linear-gradient(90deg,transparent,#FF1744,transparent);opacity:${ta.opacity.toFixed(4)};}
.tt{font-family:${tf};font-size:${tfs}px;font-weight:900;color:#FFF;display:inline-flex;align-items:center;justify-content:center;gap:14px;line-height:1.3;direction:${td};-webkit-text-stroke:2px rgba(0,0,0,0.8);paint-order:stroke fill;text-shadow:0 0 30px rgba(255,23,68,0.6),0 4px 20px rgba(0,0,0,0.9),2px 2px 0 rgba(0,0,0,0.8);}
.te{font-size:${es}px;-webkit-text-stroke:0;}
.hb{position:absolute;top:${tia?"290px":"270px"};left:50%;transform:translateX(-50%);background:linear-gradient(135deg,rgba(220,0,0,0.95),rgba(160,0,0,0.95));color:#fff;font-family:${hf};font-size:${ha?"32px":"28px"};font-weight:900;padding:12px 38px;border-radius:9999px;z-index:25;white-space:nowrap;direction:${hd};border:2px solid rgba(255,120,120,0.4);box-shadow:0 0 50px rgba(220,0,0,0.7),0 8px 24px rgba(0,0,0,0.5);}
.wc{position:absolute;left:50%;top:54%;transform:${wt};opacity:${fo.toFixed(4)};direction:${dir};text-align:center;z-index:10;width:95%;max-width:1020px;}
.wp{display:inline-block;${pcs}}
.wt{${wts}}
</style></head><body>
<div class="ot"></div><div class="ob"></div><div class="flash"></div>
<div class="tc"><div class="tt"><span class="te">${emoji_left}</span><span>${esc(display_title)}</span><span class="te">${emoji_right}</span></div></div>
${isHook?`<div class="hb">${esc(ht)}</div>`:""}
${word?`<div class="wc"><div class="wp"><span class="wt">${esc(word)}</span></div></div>`:""}
</body></html>`;
}

// ═══════════════════════════════════════════════════════════════════════════
// HTML BUILDER — LONG
// ═══════════════════════════════════════════════════════════════════════════

function buildHTMLLong({
  word, tag="information", isPower=false,
  globalFrame=0, progress=0.5, transitionState=null,
  currentSentence="", highlightedWord="",
}) {
  const ar   = word?isArabic(word):false;
  const dir  = word?getDir(word):"ltr";
  const font = word?getFontFamily(word):`"Noto Sans",sans-serif`;
  const la   = word?getLang(word):"en";
  const td   = getDir(display_title);
  const tf   = getFontFamily(display_title);
  const sd   = currentSentence?getDir(currentSentence):"ltr";
  const sf   = currentSentence?getFontFamily(currentSentence):`"Noto Sans",sans-serif`;
  const ts   = getWordStyle(tag);
  let titleOpacity = 1.0;
  if      (globalFrame<INTRO_FRAMES)              titleOpacity=globalFrame/INTRO_FRAMES;
  else if (globalFrame>=totalFrames-OUTRO_FRAMES) titleOpacity=(totalFrames-globalFrame)/OUTRO_FRAMES;
  let ws=ts.scaleMult, wo=word?1.0:0;
  if (word&&progress<0.15) {
    const t=progress/0.15; ws=0.6+(1-Math.pow(1-t,2))*(ts.scaleMult-0.6); wo=Math.min(1,t*3);
  } else if (word&&progress>0.85) { wo=1-((progress-0.85)/0.15)*0.3; }
  let flashOpacity=0, flashColor="rgba(0,0,0,0)", sx=0, sy=0;
  if (transitionState) {
    const {config:c,progress:tp}=transitionState;
    flashOpacity=tp<0.3?tp/0.3:1-(tp-0.3)/0.7;
    flashOpacity=Math.max(0,Math.min(1,flashOpacity));
    flashColor=c.flashColor;
    if (c.shakeAmount>0) {
      const s=c.shakeAmount*0.5*(1-tp);
      sx=Math.sin(globalFrame*2.3)*s; sy=Math.cos(globalFrame*1.7)*s;
    }
  }
  const fs  = computeFontSize(word,ar,ts.scaleMult,true);
  const wtr = `translate(-50%,-50%) translate(${sx.toFixed(2)}px,${sy.toFixed(2)}px) scale(${ws.toFixed(4)})`;
  const wts = `font-family:${font};font-size:${fs}px;font-weight:900;color:${ts.colorWord};line-height:1.2;letter-spacing:${ar?"1px":"2px"};display:block;word-break:break-word;-webkit-text-stroke:${ts.strokeWidth}px ${ts.strokeColor};paint-order:stroke fill;text-shadow:0 0 ${ts.glowSpread}px ${ts.colorGlow},0 0 ${ts.glowSpread*1.5}px ${ts.colorGlow};filter:brightness(${ts.brightness});`;
  const sentHTML = currentSentence
    ? currentSentence.split(/\s+/).map(w=>{
        const h=normalizeWord(w)===normalizeWord(highlightedWord);
        return h?`<span class="sh">${esc(w)}</span>`:`<span class="sw">${esc(w)}</span>`;
      }).join(" ")
    : "";
  const tia=isArabic(display_title), tfs=tia?36:32, es=tia?38:34;

  return `<!DOCTYPE html><html lang="${la}"><head><meta charset="UTF-8"/><style>
*{margin:0;padding:0;box-sizing:border-box;}
html,body{width:${WIDTH}px;height:${HEIGHT}px;overflow:hidden;background:transparent;}
.ot{position:absolute;top:0;left:0;right:0;height:35%;background:linear-gradient(to bottom,rgba(0,0,0,0.8) 0%,transparent 100%);pointer-events:none;z-index:1;}
.ob{position:absolute;bottom:0;left:0;right:0;height:38%;background:linear-gradient(to top,rgba(0,0,0,0.92) 0%,rgba(0,0,0,0.5) 60%,transparent 100%);pointer-events:none;z-index:1;}
.flash{position:absolute;inset:0;background:${flashColor};opacity:${flashOpacity.toFixed(4)};pointer-events:none;z-index:50;}
.tc{position:absolute;top:28px;${td==="rtl"?"right:40px":"left:40px"};direction:${td};text-align:${td==="rtl"?"right":"left"};z-index:30;opacity:${titleOpacity.toFixed(4)};}
.tt{font-family:${tf};font-size:${tfs}px;font-weight:900;color:#FFF;display:inline-flex;align-items:center;gap:10px;line-height:1.2;direction:${td};-webkit-text-stroke:1px rgba(0,0,0,0.8);paint-order:stroke fill;text-shadow:0 0 20px rgba(255,23,68,0.5),0 2px 10px rgba(0,0,0,0.9);}
.te{font-size:${es}px;-webkit-text-stroke:0;}
.tline{display:block;margin-top:8px;width:80px;height:3px;border-radius:2px;background:#FF1744;}
.wc{position:absolute;left:50%;top:46%;transform:${wtr};opacity:${wo.toFixed(4)};direction:${dir};text-align:center;z-index:10;width:80%;max-width:1400px;}
.wt{${wts}}
.subtitle{position:absolute;bottom:48px;left:60px;right:60px;direction:${sd};text-align:center;z-index:20;font-family:${sf};font-size:${ar?"34px":"30px"};font-weight:700;line-height:1.6;}
.sw{color:rgba(255,255,255,0.65);-webkit-text-stroke:1px rgba(0,0,0,0.6);paint-order:stroke fill;display:inline;}
.sh{color:#FFD700;-webkit-text-stroke:1px rgba(0,0,0,0.8);paint-order:stroke fill;display:inline;text-shadow:0 0 20px rgba(255,215,0,0.8);font-weight:900;}
</style></head><body>
<div class="ot"></div><div class="ob"></div><div class="flash"></div>
<div class="tc"><div class="tt">${td==="rtl"?`<span>${esc(display_title)}</span><span class="te">${emoji_left}</span>`:`<span class="te">${emoji_left}</span><span>${esc(display_title)}</span>`}</div><span class="tline"></span></div>
${word?`<div class="wc"><span class="wt">${esc(word)}</span></div>`:""}
${sentHTML?`<div class="subtitle">${sentHTML}</div>`:""}
</body></html>`;
}

// ═══════════════════════════════════════════════════════════════════════════
// MAJOR TRANSITION HTML
// ═══════════════════════════════════════════════════════════════════════════

function buildMajorTransitionHTML(transType, progress, frameColor, idx) {
  const t = progress;
  const easeOutCubic = x => 1-Math.pow(1-x,3);
  const easeOutBack  = x => 1+2.70158*Math.pow(x-1,3)+1.70158*Math.pow(x-1,2);

  let oldX=0, newX=WIDTH, flashOp=0, frameW=0, glitch=0;
  let scaleOld=1, scaleNew=1, burstOp=0;

  switch (transType) {
    case "slide_burst": {
      if (t<0.1)      { flashOp=t/0.1*0.4; scaleOld=1+t*0.3; }
      else if (t<0.5) { const p=(t-0.1)/0.4,ep=easeOutCubic(p); oldX=-ep*WIDTH; newX=WIDTH*(1-ep); flashOp=0.4*(1-p); frameW=20+p*30; scaleNew=0.9+ep*0.1; }
      else if (t<0.8) { const p=(t-0.5)/0.3; oldX=-WIDTH; newX=0; scaleNew=1+(1-p)*0.02; frameW=50*(1-p); burstOp=(1-p)*0.5; }
      else            { oldX=-WIDTH; newX=0; frameW=Math.max(0,50*(1-t)/0.2); }
      break;
    }
    case "flash_slide": {
      if (t<0.2)      { flashOp=easeOutCubic(t/0.2)*0.9; }
      else if (t<0.35){ flashOp=0.9; oldX=-easeOutCubic((t-0.2)/0.15)*WIDTH*0.3; }
      else if (t<0.7) { const p=(t-0.35)/0.35; flashOp=0.9*(1-p); oldX=-WIDTH*0.3-easeOutCubic(p)*WIDTH*0.7; newX=WIDTH*(1-easeOutBack(p)); frameW=25*(1-p); }
      else            { oldX=-WIDTH; newX=0; flashOp=Math.max(0,0.1*(1-(t-0.7)/0.3)); }
      break;
    }
    case "glitch_push": {
      const gi=t<0.5?t*2:(1-t)*2;
      glitch=Math.sin(t*80+idx)*25*gi;
      if (t<0.15)     { flashOp=t/0.15*0.3; }
      else if (t<0.55){ const p=(t-0.15)/0.4,ep=easeOutCubic(p); oldX=-ep*WIDTH; newX=WIDTH*(1-ep); flashOp=0.3*Math.abs(Math.sin(t*40)); frameW=15+Math.abs(Math.sin(t*30))*20; }
      else            { oldX=-WIDTH; newX=0; flashOp=0.1*(1-(t-0.55)/0.45); }
      break;
    }
    case "zoom_reveal": {
      if (t<0.25)     { scaleOld=1+easeOutCubic(t/0.25)*0.4; flashOp=t/0.25*0.6; }
      else if (t<0.45){ flashOp=0.6+(t-0.25)/0.2*0.4; scaleOld=1.4; }
      else if (t<0.75){ const p=(t-0.45)/0.3; flashOp=1*(1-easeOutCubic(p)); scaleNew=0.5+easeOutBack(p)*0.5; oldX=-WIDTH; newX=0; }
      else            { scaleNew=1+(1-(t-0.75)/0.25)*0.02; oldX=-WIDTH; newX=0; }
      break;
    }
    case "flash_black_push": {
      if (t<0.3)      { flashOp=easeOutCubic(t/0.3)*0.95; frameW=t/0.3*40; }
      else if (t<0.5) { flashOp=0.95; oldX=-WIDTH; }
      else if (t<0.8) { const p=(t-0.5)/0.3; flashOp=0.95*(1-easeOutCubic(p)); newX=WIDTH*(1-easeOutBack(p)); frameW=40*(1-p); }
      else            { newX=0; oldX=-WIDTH; }
      break;
    }
    case "burst_reveal": {
      if (t<0.15)     { scaleOld=1+t/0.15*0.5; flashOp=t/0.15*0.8; }
      else if (t<0.4) { flashOp=0.8; scaleOld=1.5; oldX=-easeOutCubic((t-0.15)/0.25)*WIDTH*1.2; burstOp=easeOutCubic((t-0.15)/0.25); }
      else if (t<0.7) { const p=(t-0.4)/0.3; flashOp=0.8*(1-p); oldX=-WIDTH*1.2; newX=WIDTH*(1-easeOutBack(p)); scaleNew=0.7+easeOutBack(p)*0.3; burstOp=1-p; }
      else            { newX=0; oldX=-WIDTH*1.2; scaleNew=1+(1-(t-0.7)/0.3)*0.02; }
      break;
    }
    case "cinema_crash": {
      if (t<0.3)      { flashOp=t/0.3*0.5; scaleOld=1-t/0.3*0.15; }
      else if (t<0.6) { const p=(t-0.3)/0.3; oldX=-easeOutCubic(p)*WIDTH; newX=WIDTH*(1-easeOutCubic(p)); flashOp=0.5*(1-p*0.5); frameW=30; }
      else            { const p=(t-0.6)/0.4; oldX=-WIDTH; newX=0; flashOp=0.25*(1-p); frameW=30*(1-p); }
      break;
    }
    case "double_flash": {
      if (t<0.15)     { flashOp=easeOutCubic(t/0.15)*0.9; }
      else if (t<0.25){ flashOp=0.9*(1-(t-0.15)/0.1); }
      else if (t<0.35){ flashOp=easeOutCubic((t-0.25)/0.1)*1.0; }
      else if (t<0.55){ const p=(t-0.35)/0.2; flashOp=1*(1-p*0.7); oldX=-easeOutCubic(p)*WIDTH; }
      else if (t<0.85){ const p=(t-0.55)/0.3; flashOp=0.3*(1-p); oldX=-WIDTH; newX=WIDTH*(1-easeOutBack(p)); }
      else            { newX=0; oldX=-WIDTH; }
      break;
    }
  }

  let particles="";
  if (burstOp>0||(t>0.2&&t<0.8)) {
    const pc=burstOp>0?25:10;
    for (let i=0; i<pc; i++) {
      const seed=idx*1000+Math.floor(t*100)+i;
      const px=seededRandom(seed)*WIDTH;
      const py=seededRandom(seed+0.5)*HEIGHT;
      const sz=2+seededRandom(seed+1)*6;
      const op=burstOp>0?burstOp*0.8:0.4;
      particles+=`<div style="position:absolute;left:${px.toFixed(0)}px;top:${py.toFixed(0)}px;width:${sz.toFixed(1)}px;height:${sz.toFixed(1)}px;background:${frameColor};border-radius:50%;box-shadow:0 0 ${(sz*3).toFixed(0)}px ${frameColor};opacity:${op.toFixed(3)};z-index:60;"></div>`;
    }
  }

  return `<!DOCTYPE html><html><head><meta charset="UTF-8"/><style>
*{margin:0;padding:0;box-sizing:border-box;}
html,body{width:${WIDTH}px;height:${HEIGHT}px;overflow:hidden;background:transparent;}
.flash{position:absolute;inset:0;background:${frameColor};opacity:${flashOp.toFixed(4)};pointer-events:none;z-index:50;}
.frame{position:absolute;inset:0;border:${frameW.toFixed(1)}px solid ${frameColor};opacity:${frameW>0?0.8:0};box-shadow:0 0 ${(frameW*2).toFixed(0)}px ${frameColor},inset 0 0 ${frameW.toFixed(0)}px ${frameColor};pointer-events:none;z-index:55;}
.glitch{position:absolute;inset:0;transform:translateX(${glitch.toFixed(1)}px);pointer-events:none;z-index:45;}
</style></head><body>
<div class="flash"></div><div class="frame"></div><div class="glitch"></div>
${particles}
</body></html>`;
}

// ═══════════════════════════════════════════════════════════════════════════
// MINOR TRANSITION HTML
// ═══════════════════════════════════════════════════════════════════════════

function buildMinorTransitionHTML(transType, progress, frameColor, idx) {
  const t = progress;
  let flashOp=0, frameW=0, slideOff=0, zoomSc=1, blurAmt=0;

  switch (transType) {
    case "soft_fade":     { if(t<0.4)flashOp=t/0.4*0.25; else if(t<0.6)flashOp=0.25; else flashOp=0.25*(1-(t-0.6)/0.4); break; }
    case "gentle_slide":  { if(t<0.5){slideOff=t/0.5*30;flashOp=t/0.5*0.15;}else{slideOff=30*(1-(t-0.5)/0.5);flashOp=0.15*(1-(t-0.5)/0.5);} break; }
    case "subtle_zoom":   { if(t<0.5){zoomSc=1+t/0.5*0.04;flashOp=t/0.5*0.15;}else{zoomSc=1.04-(t-0.5)/0.5*0.04;flashOp=0.15*(1-(t-0.5)/0.5);} break; }
    case "light_flash":   { if(t<0.3)flashOp=t/0.3*0.35; else if(t<0.5)flashOp=0.35; else flashOp=0.35*(1-(t-0.5)/0.5); break; }
    case "cross_dissolve":{ flashOp=Math.sin(t*Math.PI)*0.2; break; }
    case "soft_push":     { const pa=20; if(t<0.5){slideOff=t/0.5*pa;flashOp=0.1*t/0.5;}else{slideOff=pa*(1-(t-0.5)/0.5);flashOp=0.1*(1-(t-0.5)/0.5);} break; }
    case "blur_transition":{ blurAmt=Math.sin(t*Math.PI)*5; flashOp=Math.sin(t*Math.PI)*0.12; break; }
    case "gentle_reveal": { if(t<0.3){frameW=t/0.3*8;flashOp=t/0.3*0.15;}else if(t<0.7){frameW=8;flashOp=0.15;}else{frameW=8*(1-(t-0.7)/0.3);flashOp=0.15*(1-(t-0.7)/0.3);} break; }
  }

  return `<!DOCTYPE html><html><head><meta charset="UTF-8"/><style>
*{margin:0;padding:0;box-sizing:border-box;}
html,body{width:${WIDTH}px;height:${HEIGHT}px;overflow:hidden;background:transparent;}
.flash{position:absolute;inset:0;background:${frameColor};opacity:${flashOp.toFixed(4)};pointer-events:none;z-index:50;}
.frame{position:absolute;inset:0;border:${frameW.toFixed(1)}px solid ${frameColor};opacity:${frameW>0?0.6:0};pointer-events:none;z-index:55;}
.slide{position:absolute;inset:0;transform:translateX(${slideOff.toFixed(1)}px) scale(${zoomSc.toFixed(4)});${blurAmt>0?`filter:blur(${blurAmt.toFixed(1)}px);`:""}pointer-events:none;z-index:45;}
</style></head><body>
<div class="flash"></div><div class="frame"></div><div class="slide"></div>
</body></html>`;
}

// ═══════════════════════════════════════════════════════════════════════════
// BROWSER
// ═══════════════════════════════════════════════════════════════════════════

async function launchBrowser() {
  const browser = await chromium.launch({ headless:true, args:BROWSER_ARGS });
  const context = await browser.newContext({
    viewport:{ width:WIDTH, height:HEIGHT },
    deviceScaleFactor:1, locale:"ar-SA",
  });
  const page = await context.newPage();
  return { browser, page };
}

async function warmupFonts(page, builder) {
  const cases=[
    {word:"مرحبا",lang:"ar",tag:"shock",isPower:true},
    {word:"Hello",lang:"en",tag:"information",isPower:false},
    {word:"Bonjour",lang:"fr",tag:"inspiration",isPower:false},
  ];
  for (const tc of cases) {
    const html=builder({ word:tc.word, tag:tc.tag, isPower:tc.isPower, isHook:false, globalFrame:TITLE_SLIDE_FRAMES, progress:0.5, transitionState:null, currentSentence:tc.word, highlightedWord:tc.word });
    const p=join(TMP,`init_${tc.lang}.html`);
    writeFileSync(p,html,"utf-8");
    await page.goto(`file://${p}`,{ waitUntil:"networkidle", timeout:10000 });
    await page.waitForTimeout(tc.lang==="ar"?1000:500);
  }
  console.log("✅ Fonts loaded");
}

// ═══════════════════════════════════════════════════════════════════════════
// RENDER PNGs — SHORT
// ═══════════════════════════════════════════════════════════════════════════

function collectUniqueShortStates(fsm, bm) {
  const unique=new Map();
  for (let f=0; f<fsm.length; f++) {
    const ts=bm.get(f)||null;
    const k=stateKey(fsm[f],f,ts);
    if (!unique.has(k)) unique.set(k,{ word:fsm[f]?.word??null, tag:fsm[f]?.tag??"information", isPower:fsm[f]?.isPower??false, isHook:f<HOOK_FRAMES, globalFrame:f, progress:fsm[f]?.progress??0.5, transitionState:ts });
  }
  return unique;
}

async function renderAllPNGsShort(page, fsm, bm) {
  const unique=collectUniqueShortStates(fsm,bm);
  console.log(`\n📸 ${unique.size} unique states [SHORT]`);
  await warmupFonts(page,buildHTMLShort);
  const cache=new Map(); let done=0;
  for (const [key,state] of unique) {
    const html=buildHTMLShort(state);
    const hp=join(TMP,`${key}.html`);
    writeFileSync(hp,html,"utf-8");
    await page.goto(`file://${hp}`,{ waitUntil:"load", timeout:5000 });
    await page.waitForTimeout(35);
    const pp=join(TMP,`${key}.png`);
    await page.screenshot({ path:pp, type:"png", omitBackground:true });
    cache.set(key,pp); done++;
    if (done%50===0||done===unique.size) process.stdout.write(`  ${done}/${unique.size} PNGs\n`);
  }
  return cache;
}

// ═══════════════════════════════════════════════════════════════════════════
// RENDER PNGs — LONG
// ═══════════════════════════════════════════════════════════════════════════

function collectUniqueLongStates(fsm, bm, sm) {
  const unique=new Map();
  for (let f=0; f<fsm.length; f++) {
    const ts=bm.get(f)||null, ws=fsm[f], sn=sm[f]||"";
    const k=longStateKey(ws,ts,sn);
    if (!unique.has(k)) unique.set(k,{ word:ws?.word??null, tag:ws?.tag??"information", isPower:ws?.isPower??false, globalFrame:f, progress:ws?.progress??0.5, transitionState:ts, currentSentence:sn, highlightedWord:ws?.word??"" });
  }
  return unique;
}

async function renderAllPNGsLong(page, fsm, bm, sm) {
  const unique=collectUniqueLongStates(fsm,bm,sm);
  console.log(`\n📸 ${unique.size} unique states [LONG]`);
  await warmupFonts(page,buildHTMLLong);
  const cache=new Map(); let done=0;
  for (const [key,state] of unique) {
    const html=buildHTMLLong(state);
    const hp=join(TMP,`${key}.html`);
    writeFileSync(hp,html,"utf-8");
    await page.goto(`file://${hp}`,{ waitUntil:"load", timeout:5000 });
    await page.waitForTimeout(35);
    const pp=join(TMP,`${key}.png`);
    await page.screenshot({ path:pp, type:"png", omitBackground:true });
    cache.set(key,pp); done++;
    if (done%50===0||done===unique.size) process.stdout.write(`  ${done}/${unique.size} PNGs\n`);
  }
  return cache;
}

// ═══════════════════════════════════════════════════════════════════════════
// VIDEO FILTERS
// ═══════════════════════════════════════════════════════════════════════════

const buildDramaticLightingFilter = () =>
  `geq=r='clip(r(X,Y)+if(lte(X,W/2),180*(1-X/(W/2)),0),0,255)':g='g(X,Y)':b='clip(b(X,Y)+if(gte(X,W/2),180*((X-W/2)/(W/2)),0),0,255)'`;

const buildZoomOutFilter = (dur,idx) => {
  const fr=Math.ceil(dur*FPS), sz=(1.25+(idx%3)*0.05).toFixed(3);
  return `scale=w='trunc((iw*(${sz}-(${sz}-1.02)*min(on,${fr})/${fr}))/2)*2':h='trunc((ih*(${sz}-(${sz}-1.02)*min(on,${fr})/${fr}))/2)*2'`;
};
const buildCameraShakeFilter = idx => {
  const f1=(0.8+(idx%3)*0.3).toFixed(2), f2=(0.5+(idx%2)*0.4).toFixed(2);
  const ax=3+(idx%2), ay=2+(idx%2);
  return `crop=${WIDTH}:${HEIGHT}:'(iw-${WIDTH})/2+${ax}*sin(2*PI*${f1}*t)':'(ih-${HEIGHT})/2+${ay}*sin(2*PI*${f2}*t+1)'`;
};
const buildBreathingFilter = idx => {
  const hz=(0.3+(idx%2)*0.1).toFixed(2);
  return `scale=w='iw*(1+0.006*sin(2*PI*${hz}*t))':h='ih*(1+0.006*sin(2*PI*${hz}*t))',crop=${WIDTH}:${HEIGHT}`;
};
const buildFilmLookFilter    = () => `curves=r='0/0 0.25/0.20 0.5/0.55 0.75/0.80 1/0.95':g='0/0 0.25/0.22 0.5/0.50 0.75/0.78 1/0.92':b='0/0.05 0.25/0.28 0.5/0.55 0.75/0.82 1/1.0'`;
const buildSplitToningFilter = () => `curves=r='0/0.02 0.5/0.52 1/0.98':g='0/0 0.5/0.50 1/1.0':b='0/0.05 0.5/0.48 1/0.95'`;
const buildVignetteFilter    = () => `vignette=PI/5:eval=frame`;
const buildFilmGrainFilter   = idx => `noise=alls=${4+(idx%3)}:allf=t+u`;
const buildFlickerFilter     = idx => `lutyuv=y='val*(1+0.015*sin(2*PI*${(8+(idx%4)).toFixed(1)}*t))'`;
const buildColorGrading      = h  => h ? `eq=contrast=1.2:brightness=-0.04:saturation=0.85` : `eq=contrast=1.12:brightness=-0.02:saturation=0.88`;
const buildOriginalityFilters= idx => {
  const h=idx%2===0?3:-3, s=(1.03+(idx%3)*0.02).toFixed(2), sh=(0.35+(idx%2)*0.1).toFixed(2);
  return `hue=h=${h}:s=${s},unsharp=3:3:${sh}:3:3:0.0`;
};

// ═══════════════════════════════════════════════════════════════════════════
// PROCESS BACKGROUND
// ═══════════════════════════════════════════════════════════════════════════

function processBackground(videoPath, dur, outputFile, idx, isHookClip=false) {
  const d      = Math.max(dur,0.5);
  const fadeIn = Math.min(0.3,d*0.08);
  const fadeOut= Math.min(0.3,d*0.08);
  const srcDur = probeDuration(videoPath);
  const loopArgs = srcDur>0&&srcDur<d*1.4?["-stream_loop","-1"]:[];
  const stage1 = join(TMP,`s1_${String(idx).padStart(3,"0")}.mp4`);

  try {
    const vf=[
      "setpts=1.333*PTS","hflip",
      buildZoomOutFilter(d,idx), buildBreathingFilter(idx),
      buildCameraShakeFilter(idx), buildColorGrading(isHookClip),
      buildFilmLookFilter(), buildSplitToningFilter(),
      buildVignetteFilter(), buildFlickerFilter(idx),
      buildFilmGrainFilter(idx), buildOriginalityFilters(idx),
      `fade=t=in:st=0:d=${fadeIn.toFixed(3)}`,
      `fade=t=out:st=${(d-fadeOut).toFixed(3)}:d=${fadeOut.toFixed(3)}`,
    ].join(",");

    let r=runFFmpeg(["-y",...loopArgs,"-i",videoPath,"-t",(d*1.4).toFixed(3),"-vf",vf,"-r",String(FPS),"-c:v","libx264","-preset","fast","-crf",isHookClip?"16":"18","-pix_fmt","yuv420p","-an",stage1]);

    if (r.status!==0) {
      console.log(`  ⚠️ S1 fail [${idx}] — fallback`);
      const vfS=[
        "setpts=1.333*PTS","hflip",
        `scale=${WIDTH}:${HEIGHT}:force_original_aspect_ratio=increase`,
        `crop=${WIDTH}:${HEIGHT}`,"setsar=1",
        buildColorGrading(isHookClip), buildFilmGrainFilter(idx),
        buildOriginalityFilters(idx),
        `fade=t=in:st=0:d=${fadeIn.toFixed(3)}`,
        `fade=t=out:st=${(d-fadeOut).toFixed(3)}:d=${fadeOut.toFixed(3)}`,
      ].join(",");
      r=runFFmpeg(["-y",...loopArgs,"-i",videoPath,"-t",(d*1.4).toFixed(3),"-vf",vfS,"-r",String(FPS),"-c:v","libx264","-preset","fast","-crf","21","-pix_fmt","yuv420p","-an",stage1]);
      if (r.status!==0)
        runFFmpeg(["-y","-stream_loop","-1","-i",videoPath,"-t",d.toFixed(3),"-vf",`scale=${WIDTH}:${HEIGHT}:force_original_aspect_ratio=increase,crop=${WIDTH}:${HEIGHT},setsar=1`,"-r",String(FPS),"-c:v","libx264","-preset","fast","-crf","23","-pix_fmt","yuv420p","-an",stage1]);
    }

    const r2=runFFmpeg(["-y","-i",stage1,"-vf",buildDramaticLightingFilter(),"-c:v","libx264","-preset","fast","-crf",isHookClip?"16":"18","-pix_fmt","yuv420p","-an",outputFile]);
    if (r2.status!==0) { console.log(`  ⚠️ Light fail [${idx}]`); copyFileSync(stage1,outputFile); }
    else console.log(`  ✅ 🔴🔵 Light [${idx}]`);

  } finally {
    try { spawnSync("rm",["-f",stage1],{stdio:"ignore"}); } catch {}
  }
  return outputFile;
}

// ═══════════════════════════════════════════════════════════════════════════
// RENDER TRANSITION OVERLAYS
// ═══════════════════════════════════════════════════════════════════════════

async function renderMajorTransitionOverlay(page, transType, frameColor, idx) {
  const dir=join(TMP,`major_trans_${idx}`);
  mkdirSync(dir,{recursive:true});
  console.log(`  💥 Rendering MAJOR [${transType}] (${MAJOR_TRANSITION_FRAMES}f)...`);
  for (let f=0; f<MAJOR_TRANSITION_FRAMES; f++) {
    const p=f/(MAJOR_TRANSITION_FRAMES-1);
    const html=buildMajorTransitionHTML(transType,p,frameColor,idx);
    const hp=join(dir,`t${f}.html`);
    writeFileSync(hp,html,"utf-8");
    await page.goto(`file://${hp}`,{waitUntil:"load",timeout:5000});
    await page.waitForTimeout(15);
    await page.screenshot({path:join(dir,`frame_${String(f).padStart(6,"0")}.png`),type:"png",omitBackground:true});
  }
  const mov=join(TMP,`major_trans_${idx}.mov`);
  runFFmpeg(["-y","-framerate",String(FPS),"-i",`${dir}/frame_%06d.png`,"-c:v","png","-an",mov]);
  return mov;
}

async function renderMinorTransitionOverlay(page, transType, frameColor, idx) {
  const dir=join(TMP,`minor_trans_${idx}`);
  mkdirSync(dir,{recursive:true});
  console.log(`  ✨ Rendering MINOR [${transType}] (${MINOR_TRANSITION_FRAMES}f)...`);
  for (let f=0; f<MINOR_TRANSITION_FRAMES; f++) {
    const p=f/(MINOR_TRANSITION_FRAMES-1);
    const html=buildMinorTransitionHTML(transType,p,frameColor,idx);
    const hp=join(dir,`t${f}.html`);
    writeFileSync(hp,html,"utf-8");
    await page.goto(`file://${hp}`,{waitUntil:"load",timeout:5000});
    await page.waitForTimeout(15);
    await page.screenshot({path:join(dir,`frame_${String(f).padStart(6,"0")}.png`),type:"png",omitBackground:true});
  }
  const mov=join(TMP,`minor_trans_${idx}.mov`);
  runFFmpeg(["-y","-framerate",String(FPS),"-i",`${dir}/frame_%06d.png`,"-c:v","png","-an",mov]);
  return mov;
}

// ═══════════════════════════════════════════════════════════════════════════
// APPLY ALL TRANSITIONS
// ═══════════════════════════════════════════════════════════════════════════

async function applyAllTransitions(page, bgVideo, transitionPoints, outputPath) {
  const { major, minor } = transitionPoints;
  const all = [
    ...major.map((p,i)=>({...p, type:MAJOR_TRANSITIONS[i%MAJOR_TRANSITIONS.length], duration:MAJOR_TRANSITION_DURATION, isMajor:true})),
    ...minor.map((p,i)=>({...p, type:MINOR_TRANSITIONS[i%MINOR_TRANSITIONS.length], duration:MINOR_TRANSITION_DURATION, isMajor:false})),
  ].sort((a,b)=>a.time-b.time);

  if (all.length===0) {
    console.log("  ℹ️ No transitions");
    copyFileSync(bgVideo, outputPath);
    return outputPath;
  }

  console.log(`\n  🎬 Applying ${major.length} major + ${minor.length} minor transitions...`);
  let current = bgVideo;
  const temps = [];

  for (let i=0; i<all.length; i++) {
    const pt=all[i];
    const fc=TAG_FRAME_COLORS[pt.tag]||"#FF1744";
    const label=pt.isMajor?"💥 MAJOR":"✨ minor";
    console.log(`     [${i+1}/${all.length}] ${label} @${pt.time.toFixed(2)}s — ${pt.type} [${pt.tag}]`);

    const mov = pt.isMajor
      ? await renderMajorTransitionOverlay(page,pt.type,fc,i)
      : await renderMinorTransitionOverlay(page,pt.type,fc,i);

    const next=join(TMP,`trans_out_${i}.mp4`);
    temps.push(next);
    const st=Math.max(0,pt.time-pt.duration/2);
    const et=st+pt.duration;

    const r=runFFmpeg([
      "-y","-i",current,"-i",mov,
      "-filter_complex",
      `[1:v]format=rgba,setpts=PTS+${st.toFixed(3)}/TB[ov];[0:v][ov]overlay=0:0:enable='between(t,${st.toFixed(3)},${et.toFixed(3)})':format=auto[out]`,
      "-map","[out]","-map","0:a?",
      "-c:v","libx264","-preset","fast","-crf","19",
      "-c:a","copy","-pix_fmt","yuv420p",next,
    ]);
    if (r.status===0) current=next;
    else console.log(`  ⚠️ Transition ${i} failed — skip`);
  }

  copyFileSync(current, outputPath);
  temps.forEach(f=>{ try{spawnSync("rm",["-f",f],{stdio:"ignore"});}catch{} });
  console.log(`  ✅ Transitions done`);
  return outputPath;
}

// ═══════════════════════════════════════════════════════════════════════════
// FFMPEG OPS
// ═══════════════════════════════════════════════════════════════════════════

function framesToMov(frameDir, outputMov) {
  runFFmpeg(["-y","-framerate",String(FPS),"-i",`${frameDir}/frame_%06d.png`,"-vf",`scale=${WIDTH}:${HEIGHT},format=rgba`,"-c:v","png","-an",outputMov]);
  return outputMov;
}

function overlayOnBg(bgVideo, captionMov, audioPath, outputFile) {
  const fc="[1:v]format=rgba[cap];[0:v][cap]overlay=0:0:format=auto,format=yuv420p[out]";
  const bgHasAudio=hasAudioStream(bgVideo);
  const r=runFFmpeg(bgHasAudio
    ? ["-y","-i",bgVideo,"-i",captionMov,"-filter_complex",fc,"-map","[out]","-map","0:a:0","-c:v","libx264","-preset","fast","-crf","19","-c:a","aac","-b:a","192k","-pix_fmt","yuv420p",outputFile]
    : ["-y","-i",bgVideo,"-i",captionMov,"-i",audioPath,"-filter_complex",fc,"-map","[out]","-map","2:a:0","-c:v","libx264","-preset","fast","-crf","19","-c:a","aac","-b:a","192k","-pix_fmt","yuv420p",outputFile]
  );
  if (r.status!==0) console.error("❌ overlayOnBg failed:",r.stderr?.toString().slice(-200));
  return outputFile;
}

function xfadeConcat(clips, durations) {
  if (clips.length===0) return "";
  if (clips.length===1) return clips[0];
  const X=isLong?0.5:0.3, fl=[];
  let offset=0, lastLabel="[0:v]";
  for (let i=1; i<clips.length; i++) {
    const clipDur=Math.max(durations[i-1],X+0.1);
    offset+=clipDur-X;
    offset=Math.max(0.001,offset);
    const outLabel=i===clips.length-1?"[vout]":`[v${i}]`;
    const tr=XFADE_TRANSITIONS[(i-1)%XFADE_TRANSITIONS.length];
    fl.push(`${lastLabel}[${i}:v]xfade=transition=${tr}:duration=${X}:offset=${offset.toFixed(3)}${outLabel}`);
    lastLabel=outLabel;
  }
  const out=join(TMP,"xfaded.mp4");
  const r=runFFmpeg(["-y",...clips.flatMap(p=>["-i",p]),"-filter_complex",fl.join(";"),"-map","[vout]","-c:v","libx264","-preset","fast","-crf","18","-pix_fmt","yuv420p","-an",out]);
  if (r.status!==0) {
    console.log("  ⚠️ xfade failed — concat");
    const ls=join(TMP,"list.txt");
    writeFileSync(ls,clips.map(p=>`file '${p}'`).join("\n"));
    const rw=join(TMP,"raw.mp4");
    spawnSync("ffmpeg",["-y","-f","concat","-safe","0","-i",ls,"-c","copy",rw],{stdio:"inherit"});
    return rw;
  }
  return out;
}

function applyMetadata(inp, out) {
  const m=buildiPhoneMetadata();
  const r=runFFmpeg(["-y","-i",inp,"-c","copy",...m,out]);
  if (r.status!==0) { console.log("  ⚠️ Metadata fail"); copyFileSync(inp,out); }
  else console.log(`  ✅ Metadata: 📱 iPhone 17 Pro Max | 📍 ${location.city} | 📅 ${new Date().toLocaleDateString()}`);
}

function mergeAudio(videoPath, audioPath, outputFile) {
  const ad=probeDuration(audioPath), vd=probeDuration(videoPath);
  console.log(`🎵 Audio:${ad.toFixed(3)}s | Video:${vd.toFixed(3)}s`);
  let v=videoPath;
  if (vd<ad-0.3) {
    const lp=join(TMP,"looped.mp4");
    const r=runFFmpeg(["-y","-stream_loop","-1","-i",videoPath,"-t",ad.toFixed(3),"-c:v","libx264","-preset","fast","-crf","21","-pix_fmt","yuv420p","-an",lp]);
    if (r.status===0) v=lp;
  }
  const tmp=join(TMP,"merged_temp.mp4");
  runFFmpeg(["-y","-i",v,"-i",audioPath,"-map","0:v:0","-map","1:a:0","-c:v","copy","-c:a","aac","-b:a","192k","-t",Math.max(ad,1).toFixed(3),"-shortest",tmp]);
  applyMetadata(tmp,outputFile);
  console.log(`✅ Done → ${outputFile}`);
}

// ═══════════════════════════════════════════════════════════════════════════
// MODE HANDLERS
// ═══════════════════════════════════════════════════════════════════════════

async function handleBgOnlyMode() {
  const plan=buildClipPlan(), clips=[], durs=[];
  console.log(`📊 ${plan.length} clips [${content_mode.toUpperCase()}]`);
  for (const {index:i,duration:d,videoPath:v,isHook:h} of plan) {
    process.stdout.write(`  [${i+1}/${plan.length}] ${d.toFixed(2)}s${h?" 🔥":""}... `);
    const bg=join(TMP,`bg_${String(i).padStart(3,"0")}.mp4`);
    processBackground(v,d,bg,i,h);
    clips.push(bg); durs.push(d);
    process.stdout.write("✓\n");
  }
  console.log(`\n✨ Concat ${clips.length} clips...`);
  const cv=xfadeConcat(clips,durs);
  console.log("🎵 Merging audio...");
  mergeAudio(cv,audio,outputPath);
  console.log(`\n🎉 BG [${content_mode.toUpperCase()}] → ${outputPath}\n`);
}

async function handleWordsOnlyMode() {
  const bv=videos[0];
  if (!bv) { console.error("❌ words_only requires videos[0]"); process.exit(1); }

  const words=buildWordList(), fsm=buildFrameStateMap(words), bm=buildSentenceBoundaryMap();
  const sections=detectVideoSections(), tp=detectTransitionPoints(sections);
  console.log(`\n📊 Sections: ${sections.length}`);
  sections.forEach(s=>console.log(`   [${s.start.toFixed(2)}s] ${s.type.toUpperCase()} (${s.tag})`));

  const {browser,page}=await launchBrowser();
  try {
    console.log("\n🖼️ Rendering PNGs [SHORT]...");
    const cache=await renderAllPNGsShort(page,fsm,bm);
    console.log(`✅ ${cache.size} PNGs\n`);

    const fd=join(TMP,"frames_words");
    mkdirSync(fd,{recursive:true});
    const ep=cache.get("empty_n")||cache.get("intro_f0");
    for (let f=0; f<totalFrames; f++) {
      const ts=bm.get(f)||null, k=stateKey(fsm[f],f,ts);
      const src=cache.get(k)||ep;
      linkFrame(src, join(fd,`frame_${String(f).padStart(6,"0")}.png`));
    }

    const cm=join(TMP,"cap_words.mov");
    framesToMov(fd,cm);
    console.log("🔧 Overlaying words...");
    const wv=join(TMP,"with_words.mp4");
    overlayOnBg(bv,cm,audio,wv);
    const wt=join(TMP,"with_trans.mp4");
    await applyAllTransitions(page,wv,tp,wt);
    console.log("\n📱 Applying metadata...");
    applyMetadata(wt,outputPath);
    console.log(`\n🎉 Final [SHORT] → ${outputPath}\n`);
  } finally {
    try { if (browser&&browser.isConnected()) await browser.close(); }
    catch (e) { console.warn("Browser close error:",e.message); }
  }
}

async function handleLongWordsOnlyMode() {
  const bv=videos[0];
  if (!bv) { console.error("❌ long_words_only requires videos[0]"); process.exit(1); }

  const words=buildWordList(), fsm=buildFrameStateMap(words);
  const bm=buildSentenceBoundaryMap(), sm=buildSentenceMap();
  const sections=detectVideoSections(), tp=detectTransitionPoints(sections);
  console.log(`\n📊 Sections: ${sections.length}`);
  sections.forEach(s=>console.log(`   [${s.start.toFixed(2)}s] ${s.type.toUpperCase()} (${s.tag})`));

  const {browser,page}=await launchBrowser();
  try {
    console.log("\n🖼️ Rendering PNGs [LONG]...");
    const cache=await renderAllPNGsLong(page,fsm,bm,sm);
    console.log(`✅ ${cache.size} PNGs [LONG]\n`);

    const fd=join(TMP,"frames_long");
    mkdirSync(fd,{recursive:true});
    const ep=cache.get("long_empty_n_hold_")||[...cache.values()][0];
    for (let f=0; f<totalFrames; f++) {
      const ts=bm.get(f)||null, ws=fsm[f], sn=sm[f]||"";
      const k=longStateKey(ws,ts,sn);
      const src=cache.get(k)||ep;
      linkFrame(src, join(fd,`frame_${String(f).padStart(6,"0")}.png`));
    }

    const cm=join(TMP,"cap_long.mov");
    framesToMov(fd,cm);
    console.log("🔧 Overlaying words [LONG]...");
    const wv=join(TMP,"with_words_long.mp4");
    overlayOnBg(bv,cm,audio,wv);
    const wt=join(TMP,"with_trans_long.mp4");
    await applyAllTransitions(page,wv,tp,wt);
    console.log("\n📱 Applying metadata...");
    applyMetadata(wt,outputPath);
    console.log(`\n🎉 Final [LONG] → ${outputPath}\n`);
  } finally {
    try { if (browser&&browser.isConnected()) await browser.close(); }
    catch (e) { console.warn("Browser close error:",e.message); }
  }
}

// ═══════════════════════════════════════════════════════════════════════════
// MAIN
// ═══════════════════════════════════════════════════════════════════════════

const MODE_HANDLERS = {
  bg_only:          handleBgOnlyMode,
  long_bg_only:     handleBgOnlyMode,
  words_only:       handleWordsOnlyMode,
  long_words_only:  handleLongWordsOnlyMode,
};

async function main() {
  console.log(`\n🚀 Mode: ${mode} | Content: ${content_mode.toUpperCase()}\n`);
  const handler = MODE_HANDLERS[mode];
  if (!handler) { console.error(`❌ Unknown mode: ${mode}`); process.exit(1); }
  await handler();
}

main().catch(e => { console.error("❌", e); process.exit(1); });
