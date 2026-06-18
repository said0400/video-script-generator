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
  content_mode   = "short",
  platform       = "yt",
  width          = null,
  height         = null,
} = props;

// ═══════════════════════════════════════════════════════════════════════════
// DIMENSIONS
// ═══════════════════════════════════════════════════════════════════════════

function getDimensions() {
  // ✅ أولوية للأبعاد القادمة من manifest
  if (width && height && width > 0 && height > 0) {
    return { width, height };
  }
  // Long YT = landscape
  if (content_mode === "long" && platform === "yt") {
    return { width: 1920, height: 1080 };
  }
  // كل شيء آخر = portrait
  return { width: 1080, height: 1920 };
}

const { width: WIDTH, height: HEIGHT } = getDimensions();

// ✅ isLong = أي long video (YT أو FB)
const isLong  = content_mode === "long";
const isShort = !isLong;

const FPS = 30;

const INTRO_FRAMES       = Math.floor(1.0 * FPS);
const OUTRO_FRAMES       = Math.floor(1.0 * FPS);
const HOOK_FRAMES        = Math.floor(3.0 * FPS);
const TITLE_SLIDE_FRAMES = Math.floor(0.6 * FPS);

// Short transitions
const MINOR_DUR = 0.28;
const MAJOR_DUR = 0.50;

const MINOR_XFADE_TYPES = [
  "fade", "smoothleft", "smoothright", "wipeleft", "fadeblack",
];

const BROWSER_ARGS = [
  "--no-sandbox", "--disable-setuid-sandbox",
  "--disable-dev-shm-usage", "--disable-gpu",
  "--no-zygote", "--font-render-hinting=none",
  "--lang=ar,fr,en",
];

// ═══════════════════════════════════════════════════════════════════════════
// ASS COLORS — للـ Long videos
// ═══════════════════════════════════════════════════════════════════════════

// ASS format: &HAABBGGRR
const TAG_ASS_COLORS = {
  shock:       "&H00FFFFFF",
  urgency:     "&H000022FF",
  intrigue:    "&H0000D7FF",
  emotional:   "&H00AB8FFF",
  confident:   "&H00FFFFFF",
  inspiration: "&H0000D7FF",
  wisdom:      "&H00FFB182",
  desire:      "&H0047B3FF",
  calm:        "&H00EADE80",
  information: "&H00FFFFFF",
  pause:       "&H00C5BEB0",
  whisper:     "&H00D893CE",
  curiosity:   "&H0076F1FF",
  storytelling:"&H0080CCFF",
  dramatic:    "&H009A9AEF",
  revelation:  "&H00C8C8FF",
  tension:     "&H004370FF",
  climax:      "&H00FFFFFF",
  powerful:    "&H00F1ECEC",
  default:     "&H00FFFFFF",
};

// ═══════════════════════════════════════════════════════════════════════════
// COLOR & STYLE — للـ Short (Playwright)
// ═══════════════════════════════════════════════════════════════════════════

const EMOTION_COLORS = {
  curiosity: { word:"#FFD700", glow:"rgba(255,215,0,0.5)",  power:"#FF1744" },
  fear:      { word:"#FF4444", glow:"rgba(255,68,68,0.5)",  power:"#FFD700" },
  hope:      { word:"#00E676", glow:"rgba(0,230,118,0.5)",  power:"#FFFFFF" },
  joy:       { word:"#FF9100", glow:"rgba(255,145,0,0.5)",  power:"#FFFFFF" },
  awe:       { word:"#E040FB", glow:"rgba(224,64,251,0.5)", power:"#FFD700" },
  surprise:  { word:"#40C4FF", glow:"rgba(64,196,255,0.5)", power:"#FFD700" },
  desire:    { word:"#FF1744", glow:"rgba(255,23,68,0.5)",  power:"#FFD700" },
  anger:     { word:"#FF1744", glow:"rgba(255,23,68,0.5)",  power:"#FFD700" },
  sadness:   { word:"#82B1FF", glow:"rgba(130,177,255,0.5)",power:"#FFFFFF" },
  default:   { word:"#FFFFFF", glow:"rgba(255,255,255,0.4)",power:"#FF1744" },
};
const emotion = (analysis.primary_emotion || "").toLowerCase();
const COLORS  = EMOTION_COLORS[emotion] || EMOTION_COLORS.default;

const TAG_WORD_STYLES = {
  shock:       { colorWord:"#FFFFFF", colorGlow:"rgba(255,50,50,0.9)",    scaleMult:1.30, glowSpread:80, strokeColor:"rgba(255,0,0,0.8)",   strokeWidth:5, brightness:1.4  },
  urgency:     { colorWord:"#FF2200", colorGlow:"rgba(255,34,0,0.8)",     scaleMult:1.20, glowSpread:60, strokeColor:"rgba(0,0,0,0.9)",      strokeWidth:4, brightness:1.3  },
  intrigue:    { colorWord:"#FFD700", colorGlow:"rgba(255,215,0,0.7)",    scaleMult:1.0,  glowSpread:50, strokeColor:"rgba(0,0,0,0.95)",     strokeWidth:4, brightness:1.0  },
  emotional:   { colorWord:"#FF8FAB", colorGlow:"rgba(255,143,171,0.7)",  scaleMult:0.95, glowSpread:45, strokeColor:"rgba(0,0,0,0.9)",      strokeWidth:4, brightness:1.0  },
  confident:   { colorWord:"#FFFFFF", colorGlow:"rgba(255,255,255,0.6)",  scaleMult:1.10, glowSpread:40, strokeColor:"rgba(0,0,0,0.95)",     strokeWidth:5, brightness:1.2  },
  inspiration: { colorWord:"#FFD700", colorGlow:"rgba(255,215,0,0.8)",    scaleMult:1.15, glowSpread:70, strokeColor:"rgba(0,0,0,0.9)",      strokeWidth:4, brightness:1.3  },
  wisdom:      { colorWord:"#82B1FF", colorGlow:"rgba(130,177,255,0.6)",  scaleMult:0.90, glowSpread:35, strokeColor:"rgba(0,0,0,0.9)",      strokeWidth:3, brightness:0.95 },
  desire:      { colorWord:"#FFB347", colorGlow:"rgba(255,179,71,0.7)",   scaleMult:1.0,  glowSpread:45, strokeColor:"rgba(0,0,0,0.9)",      strokeWidth:4, brightness:1.1  },
  calm:        { colorWord:"#80DEEA", colorGlow:"rgba(128,222,234,0.5)",  scaleMult:0.85, glowSpread:30, strokeColor:"rgba(0,0,0,0.85)",     strokeWidth:3, brightness:0.9  },
  information: { colorWord:"#FFFFFF", colorGlow:"rgba(255,255,255,0.35)", scaleMult:1.0,  glowSpread:30, strokeColor:"rgba(0,0,0,0.95)",     strokeWidth:4, brightness:1.0  },
  pause:       { colorWord:"#B0BEC5", colorGlow:"rgba(176,190,197,0.4)",  scaleMult:0.80, glowSpread:25, strokeColor:"rgba(0,0,0,0.8)",      strokeWidth:2, brightness:0.85 },
  whisper:     { colorWord:"#CE93D8", colorGlow:"rgba(206,147,216,0.6)",  scaleMult:0.88, glowSpread:35, strokeColor:"rgba(0,0,0,0.9)",      strokeWidth:3, brightness:0.9  },
  curiosity:   { colorWord:"#FFF176", colorGlow:"rgba(255,241,118,0.6)",  scaleMult:1.02, glowSpread:45, strokeColor:"rgba(0,0,0,0.9)",      strokeWidth:4, brightness:1.05 },
  storytelling:{ colorWord:"#FFCC80", colorGlow:"rgba(255,204,128,0.5)",  scaleMult:0.95, glowSpread:35, strokeColor:"rgba(0,0,0,0.9)",      strokeWidth:3, brightness:1.0  },
  dramatic:    { colorWord:"#EF9A9A", colorGlow:"rgba(239,154,154,0.7)",  scaleMult:1.12, glowSpread:55, strokeColor:"rgba(100,0,0,0.8)",    strokeWidth:4, brightness:1.15 },
  revelation:  { colorWord:"#FFFFFF", colorGlow:"rgba(255,255,200,0.9)",  scaleMult:1.25, glowSpread:75, strokeColor:"rgba(200,150,0,0.8)",  strokeWidth:5, brightness:1.45 },
  tension:     { colorWord:"#FF7043", colorGlow:"rgba(255,112,67,0.75)",  scaleMult:1.15, glowSpread:55, strokeColor:"rgba(0,0,0,0.9)",      strokeWidth:4, brightness:1.25 },
  climax:      { colorWord:"#FFFFFF", colorGlow:"rgba(255,100,50,0.95)",  scaleMult:1.35, glowSpread:90, strokeColor:"rgba(255,50,0,0.9)",   strokeWidth:6, brightness:1.5  },
  powerful:    { colorWord:"#ECEFF1", colorGlow:"rgba(236,239,241,0.65)", scaleMult:1.12, glowSpread:45, strokeColor:"rgba(0,0,0,0.95)",     strokeWidth:5, brightness:1.2  },
};
const DEFAULT_WORD_STYLE = TAG_WORD_STYLES.information;
const POWER_STYLE = {
  colorWord: COLORS.power, colorGlow: "rgba(255,23,68,0.9)",
  scaleMult: 1.15, glowSpread: 90,
  strokeColor: "rgba(0,0,0,0.5)", strokeWidth: 2, brightness: 1.5,
};
function getWordStyle(tag) { return TAG_WORD_STYLES[tag] || DEFAULT_WORD_STYLE; }

const TAG_TRANSITION = {
  shock:       { flashColor:"rgba(255,255,255,1.0)",  flashFrames:9,  shakeAmount:18, scaleBoost:1.12 },
  urgency:     { flashColor:"rgba(220,0,0,0.85)",     flashFrames:7,  shakeAmount:12, scaleBoost:1.08 },
  intrigue:    { flashColor:"rgba(0,0,0,0.6)",        flashFrames:10, shakeAmount:5,  scaleBoost:1.04 },
  emotional:   { flashColor:"rgba(255,100,150,0.35)", flashFrames:12, shakeAmount:3,  scaleBoost:1.02 },
  confident:   { flashColor:"rgba(255,255,255,0.55)", flashFrames:6,  shakeAmount:6,  scaleBoost:1.06 },
  inspiration: { flashColor:"rgba(255,215,0,0.6)",    flashFrames:8,  shakeAmount:4,  scaleBoost:1.07 },
  wisdom:      { flashColor:"rgba(130,177,255,0.3)",  flashFrames:14, shakeAmount:2,  scaleBoost:1.01 },
  desire:      { flashColor:"rgba(255,100,180,0.4)",  flashFrames:10, shakeAmount:4,  scaleBoost:1.03 },
  calm:        { flashColor:"rgba(100,200,255,0.2)",  flashFrames:16, shakeAmount:1,  scaleBoost:1.0  },
  information: { flashColor:"rgba(255,255,255,0.15)", flashFrames:6,  shakeAmount:0,  scaleBoost:1.0  },
  pause:       { flashColor:"rgba(0,0,0,0.7)",        flashFrames:18, shakeAmount:0,  scaleBoost:1.0  },
  whisper:     { flashColor:"rgba(100,0,150,0.4)",    flashFrames:12, shakeAmount:2,  scaleBoost:1.02 },
  curiosity:   { flashColor:"rgba(255,241,118,0.4)",  flashFrames:10, shakeAmount:3,  scaleBoost:1.03 },
  storytelling:{ flashColor:"rgba(255,200,100,0.25)", flashFrames:8,  shakeAmount:1,  scaleBoost:1.01 },
  dramatic:    { flashColor:"rgba(180,0,0,0.6)",      flashFrames:12, shakeAmount:10, scaleBoost:1.10 },
  revelation:  { flashColor:"rgba(255,255,200,0.9)",  flashFrames:10, shakeAmount:14, scaleBoost:1.15 },
  tension:     { flashColor:"rgba(255,100,0,0.5)",    flashFrames:8,  shakeAmount:10, scaleBoost:1.08 },
  climax:      { flashColor:"rgba(255,255,255,0.95)", flashFrames:11, shakeAmount:20, scaleBoost:1.18 },
  powerful:    { flashColor:"rgba(255,255,255,0.6)",  flashFrames:7,  shakeAmount:7,  scaleBoost:1.07 },
};
const DEFAULT_TRANSITION_CFG = {
  flashColor:"rgba(255,255,255,0.3)",
  flashFrames:7, shakeAmount:4, scaleBoost:1.02,
};

// ═══════════════════════════════════════════════════════════════════════════
// TMP DIR
// ═══════════════════════════════════════════════════════════════════════════

const safeOut = outputPath
  .replace(/[^a-zA-Z0-9]/g, "_")
  .replace(/_+/g, "_")
  .slice(-22);
const TMP = join(tmpdir(), `vsg_${safeOut}`);
mkdirSync(TMP, { recursive: true });

console.log(`📌 ${emoji_left} ${display_title} ${emoji_right}`);
console.log(
  `🌐 Lang:${lang.toUpperCase()} | Mode:${mode} | ` +
  `${content_mode.toUpperCase()}/${platform.toUpperCase()} | ` +
  `${WIDTH}×${HEIGHT}`
);
if (isLong) {
  console.log("  🚀 Long pipeline: FFmpeg (fast mode)");
} else {
  console.log("  🎨 Short pipeline: Playwright (full quality)");
}

// ═══════════════════════════════════════════════════════════════════════════
// GPS & METADATA
// ═══════════════════════════════════════════════════════════════════════════

const GPS_LOCATIONS = {
  ar: { city:"Riyadh",   country:"Saudi Arabia", lat:"24.7136", lon:"46.6753", latRef:"N", lonRef:"E", iso6709:"+24.7136+046.6753/" },
  fr: { city:"Paris",    country:"France",        lat:"48.8566", lon:"2.3522",  latRef:"N", lonRef:"E", iso6709:"+48.8566+002.3522/" },
  en: { city:"New York", country:"United States", lat:"40.7128", lon:"74.0060", latRef:"N", lonRef:"W", iso6709:"+40.7128-074.0060/" },
};
const location = GPS_LOCATIONS[lang] || GPS_LOCATIONS.ar;

function buildiPhoneMetadata() {
  const now     = new Date();
  const dateISO = now.toISOString();
  const dateStr = dateISO.replace(/[-:]/g, "").split(".")[0];
  const serial  = "F" + Math.random().toString(36).substring(2, 10).toUpperCase();
  const uuid    = [
    Math.random().toString(16).substring(2, 10),
    Math.random().toString(16).substring(2, 6),
    Math.random().toString(16).substring(2, 6),
    Math.random().toString(16).substring(2, 6),
    Math.random().toString(16).substring(2, 14),
  ].join("-").toUpperCase();
  const g = () => (Math.random() * 0.02 - 0.01).toFixed(6);
  const a = () => (Math.random() * 0.1  - 0.05).toFixed(6);
  const lonSign = location.lonRef === "W" ? "-" : "";
  return [
    "-map_metadata", "-1",
    "-metadata", "make=Apple",
    "-metadata", "model=iPhone 17 Pro Max",
    "-metadata", "software=Adobe Premiere Pro 25.0",
    "-metadata", "encoder=Adobe Premiere Pro 25.0",
    "-metadata", "handler_name=Core Media Data Handler",
    "-metadata", "com.apple.quicktime.make=Apple",
    "-metadata", "com.apple.quicktime.model=iPhone 17 Pro Max",
    "-metadata", "com.apple.quicktime.software=iOS 18.2",
    "-metadata", `com.apple.quicktime.creationdate=${dateISO}`,
    "-metadata", `com.apple.quicktime.location.ISO6709=${location.iso6709}`,
    "-metadata", `com.apple.quicktime.location.name=${location.city}, ${location.country}`,
    "-metadata", `com.apple.quicktime.content.identifier=${uuid}`,
    "-metadata", "com.apple.quicktime.fullframerate=1",
    "-metadata", `creation_time=${dateISO}`,
    "-metadata", `date=${dateStr}`,
    "-metadata", "focal_length=9",
    "-metadata", "aperture=f/2.8",
    "-metadata", "iso=64",
    "-metadata", "exposure_time=1/120",
    "-metadata", "white_balance=Auto",
    "-metadata", "flash=No Flash",
    "-metadata", "lens=Apple iPhone 17 Pro Max back camera 9mm f/2.8",
    "-metadata", "lens_make=Apple",
    "-metadata", "lens_serial_number=" + serial,
    "-metadata", `location=${location.iso6709}`,
    "-metadata", `GPS_latitude=${location.lat}`,
    "-metadata", `GPS_latitude_ref=${location.latRef}`,
    "-metadata", `GPS_longitude=${lonSign}${location.lon}`,
    "-metadata", `GPS_longitude_ref=${location.lonRef}`,
    "-metadata", "GPS_altitude=50",
    "-metadata", "GPS_map_datum=WGS-84",
    "-metadata", `GPS_date_stamp=${dateStr.substring(0, 8)}`,
    "-metadata", "media_type=Video",
    "-metadata", "hdr_format=Dolby Vision",
    "-metadata", "color_primaries=BT.2020",
    "-metadata", "stabilization=OIS",
    "-metadata", `gyroscope_x=${g()}`,
    "-metadata", `gyroscope_y=${g()}`,
    "-metadata", `gyroscope_z=${g()}`,
    "-metadata", `accelerometer_x=${a()}`,
    "-metadata", `accelerometer_y=${a()}`,
    "-metadata", `accelerometer_z=${(9.8 + parseFloat(a())).toFixed(6)}`,
    "-metadata", "comment=",
    "-metadata", "artist=",
    "-metadata", "copyright=",
    "-metadata", "description=",
    "-metadata", "album=",
    "-metadata", "genre=",
  ];
}

// ═══════════════════════════════════════════════════════════════════════════
// UTILITIES
// ═══════════════════════════════════════════════════════════════════════════

function safeKey(str, maxLen = 25) {
  return (str || "")
    .slice(0, maxLen * 2)
    .replace(/[^a-zA-Z0-9\u0600-\u06FF]/g, "_")
    .replace(/_+/g, "_")
    .slice(0, maxLen);
}

const esc = s => (s || "").toString()
  .replace(/&/g, "&amp;").replace(/</g, "&lt;")
  .replace(/>/g, "&gt;").replace(/"/g, "&quot;")
  .replace(/'/g, "&#039;");

function normalizeWord(w) {
  return (w || "").toString()
    .replace(/[.,!?؟،;:"'(){}[\]<>«»…]/g, "")
    .trim().toLowerCase();
}

const isArabic = t => /[\u0600-\u06FF]/.test(t);
const isFrench = t => /[àâçéèêëîïôùûüÿœæ]/i.test(t);

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
  if (!fp || !existsSync(fp)) return 0;
  const r = spawnSync("ffprobe", [
    "-v", "error",
    "-show_entries", "format=duration",
    "-of", "default=noprint_wrappers=1:nokey=1",
    fp,
  ], { stdio: ["ignore", "pipe", "pipe"] });
  return parseFloat(r.stdout.toString().trim()) || 0;
}

function hasAudioStream(fp) {
  if (!fp || !existsSync(fp)) return false;
  const r = spawnSync("ffprobe", [
    "-v", "error", "-select_streams", "a",
    "-show_entries", "stream=codec_type",
    "-of", "csv=p=0", fp,
  ], { stdio: ["ignore", "pipe", "pipe"] });
  return r.stdout.toString().trim().includes("audio");
}

function runFFmpeg(args) {
  return spawnSync("ffmpeg", args, { stdio: ["ignore", "pipe", "pipe"] });
}

function isPowerWord(w) {
  if (!power_words.length) return false;
  const n = normalizeWord(w);
  if (n.length < 2) return false;
  return power_words.some(pw => {
    const p = normalizeWord(pw);
    return p && (n === p || (p.length >= 3 && n.includes(p)) || (n.length >= 3 && p.includes(n)));
  });
}

function linkFrame(src, dst) {
  if (!src || !existsSync(src)) return;
  if (existsSync(dst)) return;
  try   { symlinkSync(src, dst); }
  catch { try { copyFileSync(src, dst); } catch {} }
}

function applyMetadata(inp, out) {
  const m = buildiPhoneMetadata();
  const r = runFFmpeg(["-y", "-i", inp, "-c", "copy", ...m, out]);
  if (r.status !== 0) {
    console.log("  ⚠️ Metadata fail — copying as-is");
    copyFileSync(inp, out);
  } else {
    console.log(`  ✅ Metadata: 📱 iPhone 17 Pro Max | 📍 ${location.city}`);
  }
}

// ═══════════════════════════════════════════════════════════════════════════
// AUDIO & TIMING
// ═══════════════════════════════════════════════════════════════════════════

const realAudioDuration = probeDuration(audio);
const effectiveDuration = realAudioDuration > 1 ? realAudioDuration : duration_s;
const totalFrames       = Math.ceil(effectiveDuration * FPS);
console.log(`🎵 Audio:${realAudioDuration.toFixed(3)}s | Frames:${totalFrames}`);

// ═══════════════════════════════════════════════════════════════════════════
// SECTION DETECTION (للـ Short فقط)
// ═══════════════════════════════════════════════════════════════════════════

const CTA_TAGS = ["confident", "inspiration", "powerful"];

function detectVideoSections() {
  if (!aligned || aligned.length === 0) {
    const totalClips = (clip_durations && clip_durations.length > 0)
      ? clip_durations.length
      : Math.max(1, Math.ceil(effectiveDuration / clip_duration));
    console.log(`  ℹ️ No aligned — fallback (${totalClips} clips)`);
    return Array.from({ length: totalClips }, (_, i) => ({
      type:  i === 0 ? "hook" : i === totalClips - 1 ? "cta" : "body",
      start: 0, end: 0, idx: i,
      tag:   i === 0 ? "intrigue" : i === totalClips - 1 ? "confident" : "information",
    }));
  }
  const total = aligned.length;
  return aligned.map((seg, i) => {
    const tag = seg.tag || "information";
    let sectionType;
    if      (i === 0)                                    sectionType = "hook";
    else if (i === total - 1)                            sectionType = "cta";
    else if (i === total - 2 && CTA_TAGS.includes(tag)) sectionType = "cta";
    else                                                 sectionType = "body";
    return { type: sectionType, start: parseFloat(seg.start || 0), end: parseFloat(seg.end || 0), idx: i, tag };
  });
}

// ═══════════════════════════════════════════════════════════════════════════
// TRANSITIONS (للـ Short فقط)
// ═══════════════════════════════════════════════════════════════════════════

function getTransitionBetween(fromSection, toSection, idx) {
  if (fromSection.type !== toSection.type) {
    if (fromSection.type === "hook" && toSection.type === "body")
      return { level: "major", type: "slide_flash", duration: MAJOR_DUR };
    if (fromSection.type === "body" && toSection.type === "cta")
      return { level: "major", type: "zoom_reveal", duration: MAJOR_DUR };
    return   { level: "major", type: "slide_clean", duration: MAJOR_DUR };
  }
  return { level: "minor", type: MINOR_XFADE_TYPES[idx % MINOR_XFADE_TYPES.length], duration: MINOR_DUR };
}

// ═══════════════════════════════════════════════════════════════════════════
// CLIP PLAN
// ═══════════════════════════════════════════════════════════════════════════

function buildClipPlan() {
  const sections = detectVideoSections();
  let durations = [];
  if (clip_durations && clip_durations.length > 0) {
    durations = clip_durations.map(d => Math.max(d, 0.5));
  } else {
    const n = Math.max(1, Math.floor(effectiveDuration / clip_duration));
    durations = Array.from({ length: n }, () => effectiveDuration / n);
  }

  const count = durations.length;
  const syncedSections = Array.from({ length: count }, (_, i) =>
    sections[i] || {
      type: i === 0 ? "hook" : i === count - 1 ? "cta" : "body",
      tag:  i === 0 ? "intrigue" : i === count - 1 ? "confident" : "information",
      start: 0, end: 0, idx: i,
    }
  );

  let offset = 0;
  const plan = durations.map((d, i) => {
    const sec     = syncedSections[i];
    const nextSec = syncedSections[i + 1] || null;
    const trans   = nextSec ? getTransitionBetween(sec, nextSec, i) : null;
    const entry   = {
      index: i,
      start: parseFloat(offset.toFixed(3)),
      duration: parseFloat(d.toFixed(3)),
      videoPath: videos[i % videos.length],
      isHook: i === 0 && has_hook && isShort,
      section: sec,
      transition: trans,
    };
    offset += d;
    return entry;
  });

  console.log(`\n📋 Clip plan: ${plan.length} clips [${content_mode.toUpperCase()}/${platform.toUpperCase()}]`);
  plan.slice(0, 5).forEach(c => {
    const tLabel = c.transition ? `→ [${c.transition.level.toUpperCase()}]` : "→ [END]";
    console.log(`   [${c.index + 1}] ${c.start.toFixed(2)}s (${c.duration.toFixed(2)}s) [${c.section.type}] ${tLabel}`);
  });
  if (plan.length > 5) console.log(`   ... and ${plan.length - 5} more clips`);

  return plan;
}

// ═══════════════════════════════════════════════════════════════════════════
// VIDEO FILTERS
// ═══════════════════════════════════════════════════════════════════════════

const buildZoomOutFilter = (dur, idx) => {
  const fr = Math.ceil(dur * FPS);
  const sz = (1.15 + (idx % 3) * 0.03).toFixed(3);
  return `scale=w='trunc((iw*(${sz}-(${sz}-1.01)*min(on,${fr})/${fr}))/2)*2':h='trunc((ih*(${sz}-(${sz}-1.01)*min(on,${fr})/${fr}))/2)*2'`;
};

const buildCameraShakeFilter = idx => {
  const f1 = (0.5 + (idx % 3) * 0.15).toFixed(2);
  const f2 = (0.3 + (idx % 2) * 0.2).toFixed(2);
  const ax = 2 + (idx % 2), ay = 1 + (idx % 2);
  return `crop=${WIDTH}:${HEIGHT}:'(iw-${WIDTH})/2+${ax}*sin(2*PI*${f1}*t)':'(ih-${HEIGHT})/2+${ay}*sin(2*PI*${f2}*t+1)'`;
};

const buildFilmLookFilter = () =>
  `curves=r='0/0 0.25/0.22 0.5/0.52 0.75/0.80 1/0.95':g='0/0 0.25/0.22 0.5/0.50 0.75/0.78 1/0.92':b='0/0.03 0.25/0.26 0.5/0.52 0.75/0.80 1/0.98'`;

const buildColorGrading = isHookClip =>
  isHookClip ? `eq=contrast=1.18:brightness=-0.03:saturation=0.82`
             : `eq=contrast=1.10:brightness=-0.01:saturation=0.88`;

const buildVignetteFilter    = ()   => `vignette=PI/5:eval=frame`;
const buildFilmGrainFilter   = idx => `noise=alls=${3 + (idx % 2)}:allf=t+u`;
const buildOriginalityFilter = idx => {
  const h = idx % 2 === 0 ? 2 : -2;
  const s = (1.02 + (idx % 3) * 0.01).toFixed(2);
  const sh = (0.20 + (idx % 2) * 0.06).toFixed(2);
  return `hue=h=${h}:s=${s},unsharp=3:3:${sh}:3:3:0.0`;
};
const buildDramaticLightingFilter = () =>
  `geq=r='clip(r(X,Y)+if(lte(X,W/2),100*(1-X/(W/2)),0),0,255)':g='g(X,Y)':b='clip(b(X,Y)+if(gte(X,W/2),100*((X-W/2)/(W/2)),0),0,255)'`;

// ═══════════════════════════════════════════════════════════════════════════
// ✅ LONG BG — normalize كل clip ثم concat واحد
// ═══════════════════════════════════════════════════════════════════════════

function buildLongBgVideo(clipPlan, audioPath, outputFile) {
  console.log(
    `\n🚀 Long BG: ${clipPlan.length} clips → ` +
    `normalize → concat → merge audio`
  );

  const normalizedClips = [];

  // ── Step 1: Normalize كل clip بشكل مستقل ─────────────────────
  // نطبق هنا: scale + fps + zoom + shake + color + film look
  // لكن بدون lighting (سنطبقه في Step 2)
  for (const clip of clipPlan) {
    const { index: i, duration: d, videoPath: v } = clip;
    const normOut = join(TMP, `ln_${String(i).padStart(3, "0")}.mp4`);
    const srcDur  = probeDuration(v);
    const loopArgs = srcDur > 0 && srcDur < d * 1.3
      ? ["-stream_loop", "-1"]
      : [];

    process.stdout.write(
      `  [${i + 1}/${clipPlan.length}] ` +
      `${d.toFixed(1)}s... `
    );

    const fadeIn  = Math.min(0.20, d * 0.06);
    const fadeOut = Math.min(0.20, d * 0.06);

    const vf = [
      `scale=${WIDTH}:${HEIGHT}:force_original_aspect_ratio=increase`,
      `crop=${WIDTH}:${HEIGHT}`,
      "setsar=1",
      `fps=${FPS}`,
      "setpts=1.25*PTS",
      buildZoomOutFilter(d, i),
      buildCameraShakeFilter(i),
      buildColorGrading(false),
      buildFilmLookFilter(),
      buildVignetteFilter(),
      buildFilmGrainFilter(i),
      buildOriginalityFilter(i),
      `fade=t=in:st=0:d=${fadeIn.toFixed(3)}`,
      `fade=t=out:st=${(d - fadeOut).toFixed(3)}:d=${fadeOut.toFixed(3)}`,
    ].join(",");

    const r = runFFmpeg([
      "-y", ...loopArgs, "-i", v,
      "-t", (d * 1.1).toFixed(3),
      "-vf", vf,
      "-r", String(FPS),
      "-c:v", "libx264", "-preset", "fast", "-crf", "20",
      "-pix_fmt", "yuv420p", "-an",
      normOut,
    ]);

    if (r.status === 0 && existsSync(normOut)) {
      // ✅ trim للمدة الصحيحة
      const trimOut = join(TMP, `lt_${String(i).padStart(3, "0")}.mp4`);
      const rTrim = runFFmpeg([
        "-y", "-i", normOut,
        "-t", d.toFixed(3),
        "-c", "copy",
        trimOut,
      ]);
      if (rTrim.status === 0 && existsSync(trimOut)) {
        normalizedClips.push(trimOut);
        try { spawnSync("rm", ["-f", normOut], { stdio: "ignore" }); } catch {}
      } else {
        normalizedClips.push(normOut);
      }
      process.stdout.write("✓\n");
    } else {
      // fallback بسيط
      console.log(`⚠️ → simple scale`);
      const fallOut = join(TMP, `lf_${String(i).padStart(3, "0")}.mp4`);
      runFFmpeg([
        "-y", "-stream_loop", "-1", "-i", v,
        "-t", d.toFixed(3),
        "-vf", `scale=${WIDTH}:${HEIGHT}:force_original_aspect_ratio=increase,crop=${WIDTH}:${HEIGHT},setsar=1,fps=${FPS}`,
        "-r", String(FPS),
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-pix_fmt", "yuv420p", "-an", fallOut,
      ]);
      normalizedClips.push(existsSync(fallOut) ? fallOut : v);
    }
  }

  // ── Step 2: Concat الـ clips في ملف واحد ─────────────────────
  // ✅ concat filter يضمن continuity صحيح
  console.log(`\n✨ Concat ${normalizedClips.length} clips...`);

  const listFile = join(TMP, "long_list.txt");
  writeFileSync(listFile, normalizedClips.map(f => `file '${f}'`).join("\n"));

  const concatRaw = join(TMP, "long_raw.mp4");
  const rConcat   = runFFmpeg([
    "-y", "-f", "concat", "-safe", "0", "-i", listFile,
    "-c:v", "libx264", "-preset", "fast", "-crf", "19",
    "-pix_fmt", "yuv420p", "-an",
    concatRaw,
  ]);

  if (rConcat.status !== 0) {
    console.log("  ⚠️ Concat failed — using first clip as fallback");
    copyFileSync(normalizedClips[0] || videos[0], concatRaw);
  } else {
    console.log("  ✅ Concat done");
  }

  // ── Step 3: Dramatic Lighting على الفيديو الكامل ─────────────
  // ✅ أسرع من تطبيقه على كل clip
  const concatLit = join(TMP, "long_lit.mp4");
  const rLit = runFFmpeg([
    "-y", "-i", concatRaw,
    "-vf", buildDramaticLightingFilter(),
    "-c:v", "libx264", "-preset", "fast", "-crf", "19",
    "-pix_fmt", "yuv420p", "-an",
    concatLit,
  ]);

  const videoSource = (rLit.status === 0 && existsSync(concatLit))
    ? concatLit
    : concatRaw;

  console.log(rLit.status === 0 ? "  ✅ Lighting applied" : "  ⚠️ Lighting skipped");

  // ── Step 4: دمج الصوت ────────────────────────────────────────
  const ad = probeDuration(audioPath);
  const vd = probeDuration(videoSource);
  console.log(`🎵 Audio:${ad.toFixed(1)}s | Video:${vd.toFixed(1)}s`);

  let videoInput = videoSource;
  if (vd < ad - 0.3) {
    // نكرر آخر clip إذا الفيديو أقصر من الصوت
    const looped = join(TMP, "long_looped.mp4");
    const r = runFFmpeg([
      "-y", "-stream_loop", "-1", "-i", videoSource,
      "-t", ad.toFixed(3),
      "-c:v", "libx264", "-preset", "fast", "-crf", "21",
      "-pix_fmt", "yuv420p", "-an", looped,
    ]);
    if (r.status === 0) videoInput = looped;
  }

  const tmpMerge = join(TMP, "long_merged.mp4");
  const rMerge   = runFFmpeg([
    "-y", "-i", videoInput, "-i", audioPath,
    "-map", "0:v:0", "-map", "1:a:0",
    "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
    "-t", Math.max(ad, 1).toFixed(3), "-shortest",
    tmpMerge,
  ]);

  if (rMerge.status !== 0) {
    console.log("  ⚠️ Merge failed — video only");
    copyFileSync(videoInput, tmpMerge);
  }

  // ── Step 5: Metadata ──────────────────────────────────────────
  applyMetadata(tmpMerge, outputFile);

  // ── Cleanup ───────────────────────────────────────────────────
  const toDelete = [listFile, concatRaw, concatLit, tmpMerge, ...normalizedClips];
  toDelete.forEach(f => {
    try { spawnSync("rm", ["-f", f], { stdio: "ignore" }); } catch {}
  });

  console.log(`\n✅ Long BG done → ${outputFile}`);
  return outputFile;
}

// ═══════════════════════════════════════════════════════════════════════════
// ✅ ASS SUBTITLES BUILDER — للـ Long videos
// ═══════════════════════════════════════════════════════════════════════════

function secondsToAssTime(s) {
  const h   = Math.floor(s / 3600);
  const m   = Math.floor((s % 3600) / 60);
  const sec = Math.floor(s % 60);
  const cs  = Math.floor((s % 1) * 100);
  return (
    `${h}:${String(m).padStart(2, "0")}:` +
    `${String(sec).padStart(2, "0")}.${String(cs).padStart(2, "0")}`
  );
}

function escapeAssText(text) {
  return (text || "")
    .toString()
    .replace(/\\/g, "\\\\")
    .replace(/\{/g, "\\{")
    .replace(/\}/g, "\\}")
    .replace(/\r?\n/g, "\\N");
}

function buildAssFile() {
  const isAr     = isArabic(display_title);
  const fontName = isAr ? "Noto Naskh Arabic" : "Noto Sans";

  // أحجام الخط حسب content_mode + platform
  const wordSize  = (content_mode === "long" && platform === "yt") ? 70 : 85;
  const titleSize = (content_mode === "long" && platform === "yt") ? 36 : 46;

  // موضع الكلمات: وسط الشاشة عمودياً
  const wordMarginV  = Math.floor(HEIGHT * 0.40);
  // موضع العنوان: أعلى الشاشة
  const titleMarginV = (content_mode === "long" && platform === "yt") ? 30 : 400;

  // alignment: 2 = center bottom, 8 = center top
  const wordAlignment  = 2;
  const titleAlignment = 8;

  const header = [
    "[Script Info]",
    "ScriptType: v4.00+",
    `PlayResX: ${WIDTH}`,
    `PlayResY: ${HEIGHT}`,
    "WrapStyle: 1",
    "ScaledBorderAndShadow: yes",
    "Collisions: Normal",
    "",
    "[V4+ Styles]",
    "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, " +
    "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, " +
    "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, " +
    "Alignment, MarginL, MarginR, MarginV, Encoding",

    // Word style
    `Style: Word,${fontName},${wordSize},` +
    `&H00FFFFFF,&H000000FF,&H00000000,&HB4000000,` +
    `-1,0,0,0,100,100,0,0,1,4,2,` +
    `${wordAlignment},80,80,${wordMarginV},1`,

    // Title style
    `Style: Title,${fontName},${titleSize},` +
    `&H00FFFFFF,&H000000FF,&H00000000,&H80000000,` +
    `-1,0,0,0,100,100,0,0,1,2,1,` +
    `${titleAlignment},40,40,${titleMarginV},1`,

    "",
    "[Events]",
    "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
  ].join("\n");

  const events = [];

  // ── العنوان — يظهر طوال الفيديو ──────────────────────────────
  const titleText = escapeAssText(`${emoji_left} ${display_title} ${emoji_right}`);
  events.push(
    `Dialogue: 0,${secondsToAssTime(0)},` +
    `${secondsToAssTime(effectiveDuration)},` +
    `Title,,0,0,0,,${titleText}`
  );

  // ── الكلمات من aligned ───────────────────────────────────────
  if (aligned && aligned.length > 0) {
    let wordCount = 0;
    for (const seg of aligned) {
      if (!seg.words || seg.words.length === 0) continue;
      const tag      = seg.tag || "information";
      const assColor = TAG_ASS_COLORS[tag] || TAG_ASS_COLORS.default;

      for (const w of seg.words) {
        if (!w.word || !w.word.trim()) continue;
        const s = parseFloat(w.start);
        const e = parseFloat(w.end);
        if (isNaN(s) || isNaN(e) || s < 0 || e <= s) continue;

        const wordText  = escapeAssText(w.word.trim());
        const isPower   = isPowerWord(w.word);
        const color     = isPower ? "&H0000D7FF" : assColor;

        // للـ Power words: أكبر وأصفر
        const styleOverride = isPower
          ? `{\\c${color}\\b1\\fscx115\\fscy115}`
          : `{\\c${color}}`;

        events.push(
          `Dialogue: 1,${secondsToAssTime(s)},` +
          `${secondsToAssTime(e)},` +
          `Word,,0,0,0,,${styleOverride}${wordText}`
        );
        wordCount++;
      }
    }
    console.log(`  ✅ ASS: ${wordCount} words from aligned`);

  } else if (sentences.length > 0) {
    // fallback: جمل كاملة موزعة بالتساوي
    console.log(`  ℹ️ ASS: fallback to sentences (no aligned)`);
    const perSent = effectiveDuration / Math.max(sentences.length, 1);

    sentences.forEach((sent, si) => {
      const sentWords = sent.split(/\s+/).filter(Boolean);
      const sentStart = si * perSent;
      const perWord   = perSent / Math.max(sentWords.length, 1);

      sentWords.forEach((w, wi) => {
        const s = sentStart + wi * perWord;
        const e = s + perWord;
        events.push(
          `Dialogue: 1,${secondsToAssTime(s)},` +
          `${secondsToAssTime(e)},` +
          `Word,,0,0,0,,${escapeAssText(w)}`
        );
      });
    });
  }

  return header + "\n" + events.join("\n") + "\n";
}

// ═══════════════════════════════════════════════════════════════════════════
// ✅ LONG WORDS OVERLAY — FFmpeg + ASS
// ═══════════════════════════════════════════════════════════════════════════

function buildLongWordsOverlay(bgVideoPath, audioPath, outputFile) {
  console.log("\n📝 Long Words: FFmpeg ASS overlay...");

  // ── بناء ASS ─────────────────────────────────────────────────
  const assContent = buildAssFile();
  const assFile    = join(TMP, "long_subs.ass");
  writeFileSync(assFile, assContent, "utf-8");
  console.log(`  ✅ ASS built: ${assContent.length} bytes`);

  // ── اختيار fonts dir ──────────────────────────────────────────
  const fontsDirs = [
    "/usr/share/fonts/truetype",
    "/usr/share/fonts",
    "/usr/local/share/fonts",
  ];
  const fontsDir = fontsDirs.find(d => existsSync(d)) || "/usr/share/fonts";

  // ── FFmpeg overlay ────────────────────────────────────────────
  // ✅ escape مسار الـ ASS file للـ vf
  const assPath    = assFile.replace(/\\/g, "/").replace(/:/g, "\\:");
  const assFilter  = `ass=${assPath}:fontsdir=${fontsDir}`;

  const bgHasAudio = hasAudioStream(bgVideoPath);
  const tmpOut     = join(TMP, "long_words_tmp.mp4");

  const ffArgs = bgHasAudio
    ? [
        "-y", "-i", bgVideoPath,
        "-vf", assFilter,
        "-map", "0:v:0", "-map", "0:a:0",
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        "-c:a", "aac", "-b:a", "192k",
        "-pix_fmt", "yuv420p",
        tmpOut,
      ]
    : [
        "-y", "-i", bgVideoPath, "-i", audioPath,
        "-vf", assFilter,
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        "-c:a", "aac", "-b:a", "192k",
        "-pix_fmt", "yuv420p",
        tmpOut,
      ];

  const r = runFFmpeg(ffArgs);

  if (r.status !== 0) {
    console.log("  ⚠️ ASS overlay failed");
    console.log(`  Error: ${r.stderr?.toString().slice(-300)}`);
    // fallback: فيديو بدون كلمات
    if (bgHasAudio) {
      copyFileSync(bgVideoPath, tmpOut);
    } else {
      // دمج الصوت فقط بدون كلمات
      runFFmpeg([
        "-y", "-i", bgVideoPath, "-i", audioPath,
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        tmpOut,
      ]);
    }
  } else {
    console.log("  ✅ Words overlay done");
  }

  // ── Metadata ──────────────────────────────────────────────────
  applyMetadata(tmpOut, outputFile);

  // ── Cleanup ───────────────────────────────────────────────────
  try { spawnSync("rm", ["-f", tmpOut, assFile], { stdio: "ignore" }); } catch {}

  console.log(`✅ Long words → ${outputFile}`);
  return outputFile;
}

// ═══════════════════════════════════════════════════════════════════════════
// SHORT: Process single clip
// ═══════════════════════════════════════════════════════════════════════════

function processBackground(videoPath, dur, outputFile, idx, isHookClip = false) {
  const d        = Math.max(dur, 0.5);
  const fadeIn   = Math.min(0.20, d * 0.06);
  const fadeOut  = Math.min(0.20, d * 0.06);
  const srcDur   = probeDuration(videoPath);
  const loopArgs = srcDur > 0 && srcDur < d * 1.3 ? ["-stream_loop", "-1"] : [];
  const normOut  = join(TMP, `norm_${String(idx).padStart(3, "0")}.mp4`);
  const stage1   = join(TMP, `s1_${String(idx).padStart(3, "0")}.mp4`);

  try {
    const rNorm = runFFmpeg([
      "-y", ...loopArgs, "-i", videoPath,
      "-t", (d * 1.4).toFixed(3),
      "-vf", [
        `scale=${WIDTH}:${HEIGHT}:force_original_aspect_ratio=increase`,
        `crop=${WIDTH}:${HEIGHT}`, "setsar=1", `fps=${FPS}`, "setpts=1.25*PTS",
      ].join(","),
      "-c:v", "libx264", "-preset", "fast", "-crf", "20",
      "-pix_fmt", "yuv420p", "-an", normOut,
    ]);

    const normalizeOk  = rNorm.status === 0 && existsSync(normOut);
    const effectsInput = normalizeOk ? normOut : videoPath;
    const rescale      = normalizeOk ? [] : [
      `scale=${WIDTH}:${HEIGHT}:force_original_aspect_ratio=increase`,
      `crop=${WIDTH}:${HEIGHT}`, "setsar=1",
    ];

    const vf = [
      ...rescale,
      buildZoomOutFilter(d, idx),
      buildCameraShakeFilter(idx),
      buildColorGrading(isHookClip),
      buildFilmLookFilter(),
      buildVignetteFilter(),
      buildFilmGrainFilter(idx),
      buildOriginalityFilter(idx),
      `fade=t=in:st=0:d=${fadeIn.toFixed(3)}`,
      `fade=t=out:st=${(d - fadeOut).toFixed(3)}:d=${fadeOut.toFixed(3)}`,
    ].join(",");

    const r = runFFmpeg([
      "-y", "-i", effectsInput, "-t", d.toFixed(3),
      "-vf", vf, "-r", String(FPS),
      "-c:v", "libx264", "-preset", "fast",
      "-crf", isHookClip ? "16" : "18",
      "-pix_fmt", "yuv420p", "-an", stage1,
    ]);

    if (r.status !== 0) {
      console.log(`  ⚠️ Effects fail [${idx}]`);
      if (existsSync(normOut)) copyFileSync(normOut, stage1);
      else {
        runFFmpeg([
          "-y", "-stream_loop", "-1", "-i", videoPath, "-t", d.toFixed(3),
          "-vf", `scale=${WIDTH}:${HEIGHT}:force_original_aspect_ratio=increase,crop=${WIDTH}:${HEIGHT},setsar=1`,
          "-r", String(FPS), "-c:v", "libx264", "-preset", "fast",
          "-crf", "23", "-pix_fmt", "yuv420p", "-an", stage1,
        ]);
      }
    }

    const r2 = runFFmpeg([
      "-y", "-i", stage1, "-vf", buildDramaticLightingFilter(),
      "-c:v", "libx264", "-preset", "fast",
      "-crf", isHookClip ? "16" : "18",
      "-pix_fmt", "yuv420p", "-an", outputFile,
    ]);

    if (r2.status !== 0) {
      if (existsSync(stage1)) copyFileSync(stage1, outputFile);
    } else {
      console.log(`  ✅ Clip [${idx}] ready`);
    }

  } finally {
    try { spawnSync("rm", ["-f", normOut], { stdio: "ignore" }); } catch {}
    try { spawnSync("rm", ["-f", stage1],  { stdio: "ignore" }); } catch {}
  }
  return outputFile;
}

// ═══════════════════════════════════════════════════════════════════════════
// SHORT: Major Transition
// ═══════════════════════════════════════════════════════════════════════════

function applyMajorTransition(clipA, clipB, durA, transType, transDur, outputFile) {
  const offset = Math.max(0.1, durA - transDur);
  const X = transDur.toFixed(3), O = offset.toFixed(3);
  let fc;
  switch (transType) {
    case "slide_flash":
      fc = `[0:v]format=yuv420p[v0];[1:v]format=yuv420p[v1];[v0][v1]xfade=transition=slideleft:duration=${X}:offset=${O}[out]`;
      break;
    case "zoom_reveal":
      fc = `[0:v]format=yuv420p[v0];[1:v]format=yuv420p[v1];[v0][v1]xfade=transition=fadeblack:duration=${X}:offset=${O}[out]`;
      break;
    default:
      fc = `[0:v]format=yuv420p[v0];[1:v]format=yuv420p[v1];[v0][v1]xfade=transition=smoothleft:duration=${X}:offset=${O}[out]`;
  }
  const r = runFFmpeg(["-y", "-i", clipA, "-i", clipB, "-filter_complex", fc, "-map", "[out]", "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-pix_fmt", "yuv420p", "-an", outputFile]);
  if (r.status !== 0) {
    const lf = join(TMP, "maj_list.txt");
    writeFileSync(lf, `file '${clipA}'\nfile '${clipB}'`);
    spawnSync("ffmpeg", ["-y", "-f", "concat", "-safe", "0", "-i", lf, "-c", "copy", outputFile], { stdio: "ignore" });
  } else {
    console.log(`  💥 Major [${transType}]`);
  }
  return outputFile;
}

// ═══════════════════════════════════════════════════════════════════════════
// SHORT: Concat with Transitions
// ═══════════════════════════════════════════════════════════════════════════

function concatClipsWithTransitions(processedClips, clipPlan) {
  if (processedClips.length === 0) return null;
  if (processedClips.length === 1) return processedClips[0];

  console.log(`\n✨ Merging ${processedClips.length} clips...`);

  const groups = [];
  let   grpBuf = [{ clip: processedClips[0], dur: clipPlan[0].duration }];

  for (let i = 0; i < processedClips.length - 1; i++) {
    const trans = clipPlan[i].transition;
    if (trans && trans.level === "major") {
      groups.push({ clips: grpBuf, nextTrans: trans });
      grpBuf = [{ clip: processedClips[i + 1], dur: clipPlan[i + 1].duration }];
    } else {
      grpBuf.push({ clip: processedClips[i + 1], dur: clipPlan[i + 1].duration });
    }
  }
  groups.push({ clips: grpBuf, nextTrans: null });

  const groupOutputs = [];
  let   gIdx         = 0;

  for (const group of groups) {
    const { clips } = group;
    if (clips.length === 1) {
      groupOutputs.push({ file: clips[0].clip, dur: clips[0].dur, trans: group.nextTrans });
      continue;
    }

    const X = MINOR_DUR;
    const fl = [];
    let cumOff = 0, lbl = "[0:v]";

    for (let i = 1; i < clips.length; i++) {
      cumOff += Math.max(clips[i - 1].dur, X + 0.05) - X;
      cumOff  = Math.max(0.001, cumOff);
      const xft = MINOR_XFADE_TYPES[(i - 1) % MINOR_XFADE_TYPES.length];
      const out = i === clips.length - 1 ? "[vout]" : `[v${i}]`;
      fl.push(`${lbl}[${i}:v]xfade=transition=${xft}:duration=${X.toFixed(3)}:offset=${cumOff.toFixed(3)}${out}`);
      lbl = out;
    }

    const groupOut = join(TMP, `grp_${gIdx}.mp4`);
    const totDur   = clips.reduce((s, c) => s + c.dur, 0) - X * (clips.length - 1);

    const r = runFFmpeg([
      "-y", ...clips.flatMap(c => ["-i", c.clip]),
      "-filter_complex", fl.join(";"),
      "-map", "[vout]", "-c:v", "libx264", "-preset", "fast", "-crf", "18",
      "-pix_fmt", "yuv420p", "-an", groupOut,
    ]);

    if (r.status !== 0) {
      const ls = join(TMP, `gls_${gIdx}.txt`);
      writeFileSync(ls, clips.map(c => `file '${c.clip}'`).join("\n"));
      spawnSync("ffmpeg", ["-y", "-f", "concat", "-safe", "0", "-i", ls, "-c", "copy", groupOut], { stdio: "ignore" });
    } else {
      console.log(`  ✨ Group[${groupOutputs.length + 1}]: ${clips.length} merged`);
    }

    groupOutputs.push({ file: groupOut, dur: Math.max(totDur, 0.5), trans: group.nextTrans });
    gIdx++;
  }

  if (groupOutputs.length === 1) return groupOutputs[0].file;

  let mergedFile = groupOutputs[0].file;
  let mergedDur  = groupOutputs[0].dur;

  for (let i = 1; i < groupOutputs.length; i++) {
    const trans    = groupOutputs[i - 1].trans;
    const nextFile = groupOutputs[i].file;
    const majorOut = join(TMP, `maj_${i}.mp4`);

    if (trans && trans.level === "major") {
      applyMajorTransition(mergedFile, nextFile, mergedDur, trans.type, trans.duration, majorOut);
    } else {
      const X = MINOR_DUR, O = Math.max(0.1, mergedDur - X);
      const r = runFFmpeg([
        "-y", "-i", mergedFile, "-i", nextFile,
        "-filter_complex", `[0:v][1:v]xfade=transition=${MINOR_XFADE_TYPES[i % MINOR_XFADE_TYPES.length]}:duration=${X.toFixed(3)}:offset=${O.toFixed(3)}[out]`,
        "-map", "[out]", "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-pix_fmt", "yuv420p", "-an", majorOut,
      ]);
      if (r.status !== 0) copyFileSync(nextFile, majorOut);
    }

    if (existsSync(majorOut)) {
      mergedFile = majorOut;
      mergedDur  = probeDuration(majorOut);
    } else {
      mergedFile = nextFile;
      mergedDur  = groupOutputs[i].dur;
    }
  }

  return mergedFile;
}

// ═══════════════════════════════════════════════════════════════════════════
// SHORT: Merge Audio
// ═══════════════════════════════════════════════════════════════════════════

function mergeAudio(videoPath, audioPath, outputFile) {
  const ad = probeDuration(audioPath);
  const vd = probeDuration(videoPath);
  console.log(`🎵 Audio:${ad.toFixed(3)}s | Video:${vd.toFixed(3)}s`);

  let v = videoPath;
  if (vd < ad - 0.3) {
    const lp = join(TMP, "looped.mp4");
    const r  = runFFmpeg([
      "-y", "-stream_loop", "-1", "-i", videoPath,
      "-t", ad.toFixed(3), "-c:v", "libx264", "-preset", "fast",
      "-crf", "21", "-pix_fmt", "yuv420p", "-an", lp,
    ]);
    if (r.status === 0) v = lp;
  }

  const tmp = join(TMP, "merged_temp.mp4");
  const r   = runFFmpeg([
    "-y", "-i", v, "-i", audioPath,
    "-map", "0:v:0", "-map", "1:a:0",
    "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
    "-t", Math.max(ad, 1).toFixed(3), "-shortest", tmp,
  ]);

  if (r.status !== 0) copyFileSync(v, tmp);
  applyMetadata(tmp, outputFile);
  try { spawnSync("rm", ["-f", tmp], { stdio: "ignore" }); } catch {}
  console.log(`✅ Done → ${outputFile}`);
}

// ═══════════════════════════════════════════════════════════════════════════
// SHORT: Overlay On BG
// ═══════════════════════════════════════════════════════════════════════════

function overlayOnBg(bgVideo, captionMov, audioPath, outputFile) {
  const fc         = "[1:v]format=rgba[cap];[0:v][cap]overlay=0:0:format=auto,format=yuv420p[out]";
  const bgHasAudio = hasAudioStream(bgVideo);
  const r = runFFmpeg(bgHasAudio
    ? ["-y", "-i", bgVideo, "-i", captionMov, "-filter_complex", fc, "-map", "[out]", "-map", "0:a:0", "-c:v", "libx264", "-preset", "fast", "-crf", "19", "-c:a", "aac", "-b:a", "192k", "-pix_fmt", "yuv420p", outputFile]
    : ["-y", "-i", bgVideo, "-i", captionMov, "-i", audioPath, "-filter_complex", fc, "-map", "[out]", "-map", "2:a:0", "-c:v", "libx264", "-preset", "fast", "-crf", "19", "-c:a", "aac", "-b:a", "192k", "-pix_fmt", "yuv420p", outputFile]
  );
  if (r.status !== 0) copyFileSync(bgVideo, outputFile);
  return outputFile;
}

function framesToMov(frameDir, outputMov) {
  runFFmpeg(["-y", "-framerate", String(FPS), "-i", `${frameDir}/frame_%06d.png`, "-vf", `scale=${WIDTH}:${HEIGHT},format=rgba`, "-c:v", "png", "-an", outputMov]);
  return outputMov;
}

// ═══════════════════════════════════════════════════════════════════════════
// SHORT: Word List & Frame State Map
// ═══════════════════════════════════════════════════════════════════════════

function buildWordList() {
  const words = [];
  for (const seg of aligned) {
    if (!seg.words || !seg.words.length) continue;
    const segTag = seg.tag || "information";
    for (const x of seg.words) {
      if (!x.word || !x.word.trim()) continue;
      const s = parseFloat(x.start), e = parseFloat(x.end);
      if (isNaN(s) || isNaN(e) || s < 0 || e <= s) continue;
      words.push({ word: x.word.trim(), start: s, end: e, tag: segTag, isPower: isPowerWord(x.word) });
    }
  }
  if (!words.length && sentences.length) {
    const all = sentences.join(" ").split(/\s+/).filter(Boolean);
    const pw  = effectiveDuration / Math.max(all.length, 1);
    all.forEach((w, i) => words.push({ word: w, start: i * pw, end: (i + 1) * pw, tag: "information", isPower: isPowerWord(w) }));
  }
  words.sort((a, b) => a.start - b.start);
  console.log(`📊 Words: ${words.length}`);
  return words;
}

function findWordAtTime(words, t) {
  let lo = 0, hi = words.length - 1;
  while (lo <= hi) {
    const mid = Math.floor((lo + hi) / 2);
    if      (t < words[mid].start) hi = mid - 1;
    else if (t >= words[mid].end)  lo = mid + 1;
    else return words[mid];
  }
  return null;
}

function buildFrameStateMap(words) {
  const map = new Array(totalFrames).fill(null);
  for (let f = 0; f < totalFrames; f++) {
    const t = f / FPS, w = findWordAtTime(words, t);
    if (w) map[f] = { word: w.word, tag: w.tag, isPower: w.isPower, progress: (t - w.start) / Math.max(w.end - w.start, 0.001) };
  }
  const cov = map.filter(Boolean).length;
  console.log(`Coverage: ${cov}/${totalFrames} (${((cov / totalFrames) * 100).toFixed(1)}%)`);
  return map;
}

function buildSentenceBoundaryMap() {
  if (!aligned || !aligned.length) return new Map();
  const map = new Map();
  for (let i = 0; i < aligned.length - 1; i++) {
    const seg = aligned[i], et = parseFloat(seg.end || 0);
    if (et <= 0) continue;
    const tag = seg.tag || "information";
    const cfg = TAG_TRANSITION[tag] || DEFAULT_TRANSITION_CFG;
    const ef  = Math.floor(et * FPS);
    for (let f = 0; f < cfg.flashFrames; f++) {
      const fr = ef + f;
      if (fr >= 0 && fr < totalFrames && !map.has(fr))
        map.set(fr, { tag, config: cfg, progress: f / Math.max(cfg.flashFrames - 1, 1) });
    }
  }
  return map;
}

// ═══════════════════════════════════════════════════════════════════════════
// SHORT: Animation
// ═══════════════════════════════════════════════════════════════════════════

function computeTitleAnimation(gf) {
  if (gf < INTRO_FRAMES) { const t = gf / INTRO_FRAMES, e = 1 - Math.pow(1 - t, 3); return { opacity: e, translateY: (1 - e) * -80 }; }
  if (gf >= totalFrames - OUTRO_FRAMES) { const t = (gf - (totalFrames - OUTRO_FRAMES)) / OUTRO_FRAMES; return { opacity: 1 - Math.pow(t, 2), translateY: Math.pow(t, 2) * -60 }; }
  return { opacity: 1.0, translateY: 0 };
}

function computeWordAnimation(progress, scaleMult) {
  if (progress < 0.15) { const t = progress / 0.15, e = 1 - Math.pow(1 - t, 2); return { scale: 0.6 + e * 0.48, opacity: Math.min(1, t * 3), translateY: (1 - e) * 30 }; }
  if (progress > 0.85) { const t = (progress - 0.85) / 0.15; return { scale: 1 - t * 0.05, opacity: 1 - t * 0.3, translateY: 0 }; }
  return { scale: scaleMult, opacity: 1.0, translateY: 0 };
}

function computeTransitionEffect(transState, gf) {
  if (!transState) return { flashOpacity: 0, flashColor: "rgba(0,0,0,0)", shakeX: 0, shakeY: 0, transScale: 1.0 };
  const { config: c, progress: tp } = transState;
  let fo = tp < 0.3 ? tp / 0.3 : 1 - (tp - 0.3) / 0.7;
  fo = Math.max(0, Math.min(1, fo));
  let sx = 0, sy = 0;
  if (c.shakeAmount > 0) { const s = c.shakeAmount * (1 - tp); sx = Math.sin(gf * 2.3) * s; sy = Math.cos(gf * 1.7) * s; }
  let ts = 1.0;
  if (c.scaleBoost > 1.0 && tp < 0.5) ts = 1 + (c.scaleBoost - 1) * (1 - tp * 2);
  return { flashOpacity: fo, flashColor: c.flashColor, shakeX: sx, shakeY: sy, transScale: ts };
}

// ═══════════════════════════════════════════════════════════════════════════
// SHORT: Font Size
// ═══════════════════════════════════════════════════════════════════════════

const SHORT_FONT_SIZES = [
  { maxLen:  2, ar: 170, en: 160 }, { maxLen:  4, ar: 150, en: 140 },
  { maxLen:  6, ar: 130, en: 120 }, { maxLen:  9, ar: 110, en: 102 },
  { maxLen: 12, ar:  92, en:  86 }, { maxLen: 99, ar:  76, en:  72 },
];

function computeFontSize(word, isAr, scaleMult) {
  if (!word) return 100;
  let base = 100;
  for (const { maxLen, ar, en } of SHORT_FONT_SIZES) {
    if (word.length <= maxLen) { base = isAr ? ar : en; break; }
  }
  return Math.max(60, Math.min(220, Math.round(base * scaleMult)));
}

// ═══════════════════════════════════════════════════════════════════════════
// SHORT: Hook + State Key + HTML
// ═══════════════════════════════════════════════════════════════════════════

const HOOK_DEFAULTS = { ar: "🔴 لا تتجاوز هذا", fr: "🔴 Ne ratez pas ça", en: "🔴 Don't skip this" };
const getHookText = () => (custom_hook && custom_hook.trim()) || HOOK_DEFAULTS[lang] || HOOK_DEFAULTS.en;

function stateKey(state, gf, ts) {
  if (gf < INTRO_FRAMES)                return `intro_f${gf}`;
  if (gf >= totalFrames - OUTRO_FRAMES) return `outro_f${gf}`;
  if (ts) {
    const pb = ts.progress < 0.5 ? "in" : "out";
    return `tr_${ts.tag}_${pb}_${safeKey(state ? state.word : "empty", 15)}_${state?.isPower ? 1 : 0}`;
  }
  const h = gf < HOOK_FRAMES ? "h" : "n";
  if (!state) return `empty_${h}`;
  const p = state.progress, b = p < 0.15 ? "pop" : p > 0.85 ? "fade" : "hold";
  return `w_${safeKey(state.word, 15)}_${state.tag}_${state.isPower ? 1 : 0}_${h}_${b}`;
}

function buildHTMLShort({ word, tag = "information", isPower = false, isHook = false, globalFrame = 0, progress = 0.5, transitionState = null }) {
  const ar   = word ? isArabic(word) : false;
  const dir  = word ? getDir(word) : "ltr";
  const font = word ? getFontFamily(word) : `"Noto Sans",sans-serif`;
  const la   = word ? getLang(word) : "en";
  const td   = getDir(display_title);
  const tf   = getFontFamily(display_title);
  const ts   = isPower ? POWER_STYLE : getWordStyle(tag);
  const ta   = computeTitleAnimation(globalFrame);
  const wa   = word ? computeWordAnimation(progress, ts.scaleMult) : { scale: 1, opacity: 0, translateY: 0 };
  const tr   = computeTransitionEffect(transitionState, globalFrame);
  const fs   = computeFontSize(word, ar, ts.scaleMult);
  const fsc  = wa.scale * tr.transScale;
  const fo   = word ? wa.opacity : 0;
  const wt   = `translate(-50%,calc(-50% + ${wa.translateY.toFixed(1)}px)) translate(${tr.shakeX.toFixed(2)}px,${tr.shakeY.toFixed(2)}px) scale(${fsc.toFixed(4)})`;
  const ht   = getHookText();
  const ha   = isArabic(ht);
  const hd   = ha ? "rtl" : "ltr";
  const hf   = getFontFamily(ht);
  const tia  = isArabic(display_title);
  const tfs  = tia ? 52 : 46;
  const es   = tia ? 56 : 50;

  const pcs = isPower
    ? `background:linear-gradient(135deg,#FF1744,#D50000);padding:24px 60px;border-radius:9999px;border:3px solid rgba(255,255,255,0.3);box-shadow:0 0 60px rgba(255,23,68,0.8),0 0 120px rgba(255,23,68,0.4);`
    : `background:transparent;padding:0;`;

  const wts = isPower
    ? `font-family:${font};font-size:${fs}px;font-weight:900;color:#FFF;line-height:1.15;letter-spacing:${ar ? "1px" : "3px"};display:block;word-break:break-word;-webkit-text-stroke:${ts.strokeWidth}px ${ts.strokeColor};paint-order:stroke fill;`
    : `font-family:${font};font-size:${fs}px;font-weight:900;color:${ts.colorWord};line-height:1.15;letter-spacing:${ar ? "1px" : "3px"};display:block;word-break:break-word;-webkit-text-stroke:${ts.strokeWidth}px ${ts.strokeColor};paint-order:stroke fill;text-shadow:0 0 ${ts.glowSpread}px ${ts.colorGlow},0 0 ${ts.glowSpread * 1.5}px ${ts.colorGlow};filter:brightness(${ts.brightness});`;

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
.hb{position:absolute;top:${tia ? "290px" : "270px"};left:50%;transform:translateX(-50%);background:linear-gradient(135deg,rgba(220,0,0,0.95),rgba(160,0,0,0.95));color:#fff;font-family:${hf};font-size:${ha ? "32px" : "28px"};font-weight:900;padding:12px 38px;border-radius:9999px;z-index:25;white-space:nowrap;direction:${hd};border:2px solid rgba(255,120,120,0.4);box-shadow:0 0 50px rgba(220,0,0,0.7),0 8px 24px rgba(0,0,0,0.5);}
.wc{position:absolute;left:50%;top:54%;transform:${wt};opacity:${fo.toFixed(4)};direction:${dir};text-align:center;z-index:10;width:95%;max-width:1020px;}
.wp{display:inline-block;${pcs}}
.wt{${wts}}
</style></head><body>
<div class="ot"></div><div class="ob"></div><div class="flash"></div>
<div class="tc"><div class="tt"><span class="te">${emoji_left}</span><span>${esc(display_title)}</span><span class="te">${emoji_right}</span></div></div>
${isHook ? `<div class="hb">${esc(ht)}</div>` : ""}
${word ? `<div class="wc"><div class="wp"><span class="wt">${esc(word)}</span></div></div>` : ""}
</body></html>`;
}

// ═══════════════════════════════════════════════════════════════════════════
// SHORT: Browser + Render PNGs
// ═══════════════════════════════════════════════════════════════════════════

async function launchBrowser() {
  const browser = await chromium.launch({ headless: true, args: BROWSER_ARGS });
  const context = await browser.newContext({ viewport: { width: WIDTH, height: HEIGHT }, deviceScaleFactor: 1, locale: "ar-SA" });
  const page    = await context.newPage();
  return { browser, page };
}

async function warmupFonts(page) {
  const cases = [
    { word: "مرحبا", lang: "ar", tag: "shock",      isPower: true  },
    { word: "Hello", lang: "en", tag: "information", isPower: false },
  ];
  for (const tc of cases) {
    const html = buildHTMLShort({ word: tc.word, tag: tc.tag, isPower: tc.isPower, isHook: false, globalFrame: TITLE_SLIDE_FRAMES, progress: 0.5, transitionState: null });
    const p    = join(TMP, `init_${tc.lang}.html`);
    writeFileSync(p, html, "utf-8");
    await page.goto(`file://${p}`, { waitUntil: "networkidle", timeout: 10000 });
    await page.waitForTimeout(tc.lang === "ar" ? 1000 : 500);
  }
  console.log("✅ Fonts loaded");
}

async function renderPNGsShort(page, fsm, bm) {
  const unique = new Map();
  for (let f = 0; f < fsm.length; f++) {
    const ts = bm.get(f) || null, k = stateKey(fsm[f], f, ts);
    if (!unique.has(k)) unique.set(k, { word: fsm[f]?.word ?? null, tag: fsm[f]?.tag ?? "information", isPower: fsm[f]?.isPower ?? false, isHook: f < HOOK_FRAMES, globalFrame: f, progress: fsm[f]?.progress ?? 0.5, transitionState: ts });
  }
  console.log(`\n📸 ${unique.size} unique states [SHORT]`);
  await warmupFonts(page);

  const cache = new Map();
  let done = 0;
  for (const [k, s] of unique) {
    const html = buildHTMLShort(s);
    const hp   = join(TMP, `${k}.html`);
    writeFileSync(hp, html, "utf-8");
    await page.goto(`file://${hp}`, { waitUntil: "load", timeout: 5000 });
    await page.waitForTimeout(30);
    const pp = join(TMP, `${k}.png`);
    await page.screenshot({ path: pp, type: "png", omitBackground: true });
    cache.set(k, pp);
    done++;
    if (done % 50 === 0 || done === unique.size) process.stdout.write(`  ${done}/${unique.size} PNGs\n`);
  }
  return cache;
}

// ═══════════════════════════════════════════════════════════════════════════
// MODE HANDLERS
// ═══════════════════════════════════════════════════════════════════════════

async function handleBgOnlyMode() {
  const plan = buildClipPlan();

  // ✅ Long (YT + FB): FFmpeg pipeline واحد
  if (isLong) {
    console.log(`\n🚀 Long BG: Single FFmpeg pipeline`);
    buildLongBgVideo(plan, audio, outputPath);
    console.log(`\n🎉 BG [${content_mode.toUpperCase()}/${platform.toUpperCase()}] → ${outputPath}\n`);
    return;
  }

  // ✅ Short: clip بـ clip مع transitions
  console.log(`\n📊 Processing ${plan.length} clips [SHORT]`);
  const processedClips = [];
  for (const clip of plan) {
    const { index: i, duration: d, videoPath: v, isHook: h } = clip;
    process.stdout.write(`  [${i + 1}/${plan.length}] ${d.toFixed(2)}s [${clip.section.type}]${h ? " 🔥" : ""}... `);
    const bg = join(TMP, `bg_${String(i).padStart(3, "0")}.mp4`);
    processBackground(v, d, bg, i, h);
    processedClips.push(bg);
    process.stdout.write("✓\n");
  }

  const merged = concatClipsWithTransitions(processedClips, plan);
  if (!merged || !existsSync(merged)) {
    console.error("❌ No merged video");
    process.exit(1);
  }

  console.log("\n🎵 Merging audio...");
  mergeAudio(merged, audio, outputPath);
  console.log(`\n🎉 BG [SHORT] → ${outputPath}\n`);
}

async function handleWordsOnlyMode() {
  const bgVideo = videos[0];
  if (!bgVideo || !existsSync(bgVideo)) {
    console.error("❌ words_only requires videos[0] (BG video)");
    process.exit(1);
  }

  // ✅ Long (YT + FB): FFmpeg ASS — بدون Playwright
  if (isLong) {
    console.log(`\n🚀 Long Words: FFmpeg ASS (no Playwright)`);
    buildLongWordsOverlay(bgVideo, audio, outputPath);
    console.log(`\n🎉 Final [${content_mode.toUpperCase()}/${platform.toUpperCase()}] → ${outputPath}\n`);
    return;
  }

  // ✅ Short: Playwright — كامل الجودة
  console.log(`\n🎨 Short Words: Playwright (full quality)`);
  const words = buildWordList();
  const fsm   = buildFrameStateMap(words);
  const bm    = buildSentenceBoundaryMap();

  const { browser, page } = await launchBrowser();
  try {
    console.log(`\n🖼️ Rendering PNGs...`);
    const cache = await renderPNGsShort(page, fsm, bm);
    console.log(`✅ ${cache.size} PNGs\n`);

    const fd = join(TMP, "frames_words");
    mkdirSync(fd, { recursive: true });
    const ep = cache.get("empty_n") || cache.get("intro_f0");

    for (let f = 0; f < totalFrames; f++) {
      const ts = bm.get(f) || null;
      const k  = stateKey(fsm[f], f, ts);
      linkFrame(cache.get(k) || ep, join(fd, `frame_${String(f).padStart(6, "0")}.png`));
    }

    const cm = join(TMP, "cap_words.mov");
    framesToMov(fd, cm);

    console.log("🔧 Overlaying words...");
    const wv = join(TMP, "with_words.mp4");
    overlayOnBg(bgVideo, cm, audio, wv);

    console.log("📱 Applying metadata...");
    applyMetadata(wv, outputPath);

    console.log(`\n🎉 Final [SHORT] → ${outputPath}\n`);

  } finally {
    try {
      if (browser && browser.isConnected()) await browser.close();
    } catch (e) {
      console.warn("Browser close error:", e.message);
    }
  }
}

// ═══════════════════════════════════════════════════════════════════════════
// MAIN
// ═══════════════════════════════════════════════════════════════════════════

const MODE_HANDLERS = {
  bg_only:         handleBgOnlyMode,
  long_bg_only:    handleBgOnlyMode,
  words_only:      handleWordsOnlyMode,
  long_words_only: handleWordsOnlyMode,
};

async function main() {
  console.log(
    `\n🚀 Mode:${mode} | ` +
    `${content_mode.toUpperCase()}/${platform.toUpperCase()}\n`
  );
  const handler = MODE_HANDLERS[mode];
  if (!handler) {
    console.error(`❌ Unknown mode: ${mode}`);
    process.exit(1);
  }
  await handler();
}

main().catch(e => {
  console.error("❌", e);
  process.exit(1);
});
