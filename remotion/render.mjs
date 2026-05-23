import { readFileSync, writeFileSync, mkdirSync, existsSync } from "fs";
import { spawnSync } from "child_process";
import { dirname, basename } from "path";
import { fileURLToPath } from "url";
import { chromium } from "playwright";

const manifestPath = process.argv[2];
const outputPath   = process.argv[3];

if (!manifestPath || !outputPath) {
  console.error("Usage: node render.mjs <manifest.json> <output.mp4>");
  process.exit(1);
}

const props = JSON.parse(readFileSync(manifestPath, "utf-8"));
const { sentences, videos, audio, duration_s } = props;

const FPS           = 30;
const WIDTH         = 1080;
const HEIGHT        = 1920;
const CLIP_DURATION = duration_s / sentences.length;
const FADE_DUR      = 0.35;
const XFADE_DUR     = 0.4;
const TMP           = "/tmp/vsg_render";

mkdirSync(TMP, { recursive: true });

console.log(`📋 Sentences  : ${sentences.length}`);
console.log(`⏱️  Per clip   : ${CLIP_DURATION.toFixed(2)}s`);

// ── Transitions pool ──────────────────────────────────────────────────────────
const TRANSITIONS = [
  "fade",
  "slideleft",
  "slideright",
  "slideup",
  "smoothleft",
  "smoothright",
  "circleopen",
  "radial",
  "pixelize",
  "dissolve",
];

function getTransition(i) {
  return TRANSITIONS[i % TRANSITIONS.length];
}

// ── Detect Arabic ─────────────────────────────────────────────────────────────
function isArabic(text) {
  return /[\u0600-\u06FF]/.test(text);
}

// ── Build word-by-word caption HTML ──────────────────────────────────────────
function buildCaptionHTML(sentence) {
  const dir      = isArabic(sentence) ? "rtl" : "ltr";
  const lang     = isArabic(sentence) ? "ar" : "en";
  const fontFace = isArabic(sentence)
    ? `"Noto Naskh Arabic", "Amiri", serif`
    : `"Inter", "Helvetica Neue", Arial, sans-serif`;
  const fontSize = isArabic(sentence) ? "74px" : "66px";

  // Split into words, each animated with staggered delay
  const words = sentence.split(" ");
  const wordSpans = words.map((word, i) => {
    const delay = (i * 0.08).toFixed(2);
    return `<span class="word" style="animation-delay:${delay}s">${word}</span>`;
  }).join('<span class="space"> </span>');

  return `<!DOCTYPE html>
<html lang="${lang}">
<head>
  <meta charset="UTF-8"/>
  <link href="https://fonts.googleapis.com/css2?family=Noto+Naskh+Arabic:wght@400;700&family=Amiri:wght@700&family=Inter:wght@700;800&display=swap" rel="stylesheet"/>
  <style>
    * { margin:0; padding:0; box-sizing:border-box; }

    html, body {
      width: ${WIDTH}px;
      height: ${HEIGHT}px;
      overflow: hidden;
      background: transparent;
    }

    /* Bottom gradient */
    .gradient {
      position: absolute;
      bottom: 0; left: 0; right: 0;
      height: 60%;
      background: linear-gradient(
        to top,
        rgba(0,0,0,0.90) 0%,
        rgba(0,0,0,0.50) 40%,
        rgba(0,0,0,0.10) 70%,
        transparent 100%
      );
    }

    /* Caption area */
    .caption-wrapper {
      position: absolute;
      bottom: 150px;
      left: 0; right: 0;
      padding: 0 60px;
      text-align: center;
      direction: ${dir};
      line-height: 1.5;
    }

    /* Each word animates in */
    .word {
      display: inline-block;
      color: #ffffff;
      font-family: ${fontFace};
      font-size: ${fontSize};
      font-weight: 800;
      letter-spacing: ${isArabic(sentence) ? "0.02em" : "-0.01em"};
      text-shadow:
        0 4px 16px rgba(0,0,0,1),
        0 0  40px rgba(0,0,0,0.9),
        2px 2px 6px rgba(0,0,0,1);
      opacity: 0;
      animation: popIn 0.35s cubic-bezier(0.34, 1.56, 0.64, 1) forwards;
    }

    .space {
      display: inline-block;
      width: 0.28em;
    }

    @keyframes popIn {
      0%   { opacity: 0; transform: translateY(20px) scale(0.85); }
      60%  { opacity: 1; transform: translateY(-4px) scale(1.05); }
      100% { opacity: 1; transform: translateY(0)   scale(1); }
    }
  </style>
</head>
<body>
  <div class="gradient"></div>
  <div class="caption-wrapper">
    ${wordSpans}
  </div>
</body>
</html>`;
}

// ── Render caption PNG — wait for all words to appear ────────────────────────
async function renderCaptionPNG(page, sentence, index) {
  const html     = buildCaptionHTML(sentence);
  const htmlPath = `${TMP}/caption_${index}.html`;
  writeFileSync(htmlPath, html, "utf-8");

  await page.goto(`file://${htmlPath}`, { waitUntil: "load" });

  // Wait: fonts (800ms) + last word animation delay
  const wordCount  = sentence.split(" ").length;
  const waitMs     = 900 + wordCount * 85;
  await page.waitForTimeout(Math.min(waitMs, 2200));

  const pngPath = `${TMP}/caption_${index}.png`;
  await page.screenshot({ path: pngPath, type: "png", omitBackground: true });
  return pngPath;
}

// ── Apply Ken Burns + cinematic blue filter + fade ────────────────────────────
function applyEffectsAndColor(videoPath, duration, outPath, clipIndex) {
  const totalFrames = Math.ceil(duration * FPS);

  // Ken Burns: alternate zoom in / zoom out
  const zoomIn  =
    `zoompan=z='min(zoom+0.0004,1.09)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=${totalFrames}:s=${WIDTH}x${HEIGHT}:fps=${FPS}`;
  const zoomOut =
    `zoompan=z='if(eq(on\\,1)\\,1.09\\,max(zoom-0.0004\\,1.0))':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=${totalFrames}:s=${WIDTH}x${HEIGHT}:fps=${FPS}`;

  const kenBurns = clipIndex % 2 === 0 ? zoomIn : zoomOut;

  // Cinematic blue/teal color grade
  const colorGrade = [
    // Boost shadows toward blue-teal
    `colorbalance=ss=-0.05:ms=0.05:hs=0.12`,
    // Slight cool tone in mids
    `curves=r='0/0 0.5/0.45 1/0.9':g='0/0 0.5/0.5 1/1':b='0/0.05 0.5/0.55 1/1'`,
    // Slightly desaturate for cinematic look
    `hue=s=0.85`,
    // Gentle vignette
    `vignette=PI/5`,
  ].join(",");

  // Fade in + fade out
  const fade =
    `fade=t=in:st=0:d=${FADE_DUR},fade=t=out:st=${(duration - FADE_DUR).toFixed(3)}:d=${FADE_DUR}`;

  // Scale first, then Ken Burns, then color, then fade
  const fullFilter =
    `scale=${WIDTH * 1.1}:${HEIGHT * 1.1}:force_original_aspect_ratio=increase,` +
    `crop=${WIDTH * 1.1}:${HEIGHT * 1.1},` +
    `${kenBurns},${colorGrade},${fade}`;

  const result = spawnSync("ffmpeg", [
    "-y",
    "-i", videoPath,
    "-t", duration.toFixed(3),
    "-vf", fullFilter,
    "-r", String(FPS),
    "-c:v", "libx264",
    "-preset", "fast",
    "-crf", "22",
    "-pix_fmt", "yuv420p",
    "-an",
    outPath,
  ], { stdio: ["ignore", "pipe", "pipe"] });

  if (result.status !== 0) {
    console.error("⚠️  Effects error — using simple fallback");
    const simpleFallback = [
      `scale=${WIDTH}:${HEIGHT}:force_original_aspect_ratio=increase,crop=${WIDTH}:${HEIGHT},setsar=1`,
      colorGrade,
      fade,
    ].join(",");

    spawnSync("ffmpeg", [
      "-y", "-i", videoPath,
      "-t", duration.toFixed(3),
      "-vf", simpleFallback,
      "-r", String(FPS),
      "-c:v", "libx264", "-preset", "fast", "-crf", "22",
      "-pix_fmt", "yuv420p", "-an",
      outPath,
    ], { stdio: "inherit" });
  }

  return outPath;
}

// ── Overlay caption PNG on video ──────────────────────────────────────────────
function overlayCaption(videoPath, captionPng, outPath) {
  const result = spawnSync("ffmpeg", [
    "-y",
    "-i", videoPath,
    "-i", captionPng,
    "-filter_complex", "[0:v][1:v]overlay=0:0[out]",
    "-map", "[out]",
    "-c:v", "libx264",
    "-preset", "fast",
    "-crf", "22",
    "-pix_fmt", "yuv420p",
    "-an",
    outPath,
  ], { stdio: ["ignore", "pipe", "pipe"] });

  if (result.status !== 0) {
    console.error("❌ Overlay error:\n" + result.stderr.toString().slice(-1000));
    process.exit(1);
  }
  return outPath;
}

// ── Cross dissolve with varied transitions ────────────────────────────────────
function xfadeConcat(clipPaths) {
  if (clipPaths.length === 1) return clipPaths[0];

  let filterParts = [];
  let offset      = 0;
  let lastLabel   = "[0:v]";

  for (let i = 1; i < clipPaths.length; i++) {
    offset += CLIP_DURATION - XFADE_DUR;
    const transition = getTransition(i - 1);
    const outLabel   = i === clipPaths.length - 1 ? "[vout]" : `[v${i}]`;

    filterParts.push(
      `${lastLabel}[${i}:v]xfade=transition=${transition}:duration=${XFADE_DUR}:offset=${offset.toFixed(3)}${outLabel}`
    );
    lastLabel = outLabel;
  }

  const inputs  = clipPaths.flatMap(p => ["-i", p]);
  const outPath = `${TMP}/xfaded.mp4`;

  const result = spawnSync("ffmpeg", [
    "-y",
    ...inputs,
    "-filter_complex", filterParts.join(";"),
    "-map", "[vout]",
    "-c:v", "libx264",
    "-preset", "fast",
    "-crf", "22",
    "-pix_fmt", "yuv420p",
    "-an",
    outPath,
  ], { stdio: ["ignore", "pipe", "pipe"] });

  if (result.status !== 0) {
    console.error("⚠️  xfade failed — using simple concat\n"
      + result.stderr.toString().slice(-600));
    return simpleConcatClips(clipPaths);
  }

  return outPath;
}

function simpleConcatClips(clipPaths) {
  const listFile = `${TMP}/list.txt`;
  writeFileSync(listFile, clipPaths.map(p => `file '${p}'`).join("\n"));
  const outPath = `${TMP}/raw_final.mp4`;
  spawnSync("ffmpeg", [
    "-y", "-f", "concat", "-safe", "0",
    "-i", listFile, "-c", "copy", outPath,
  ], { stdio: "inherit" });
  return outPath;
}

// ── Merge audio ───────────────────────────────────────────────────────────────
function mergeAudio(videoPath, audioPath, outPath) {
  const result = spawnSync("ffmpeg", [
    "-y",
    "-i", videoPath,
    "-i", audioPath,
    "-map", "0:v:0",
    "-map", "1:a:0",
    "-c:v", "copy",
    "-c:a", "aac",
    "-b:a", "192k",
    "-shortest",
    outPath,
  ], { stdio: ["ignore", "pipe", "pipe"] });

  if (result.status !== 0) {
    console.error("❌ Merge error:\n" + result.stderr.toString().slice(-1000));
    process.exit(1);
  }
}

// ── Main ──────────────────────────────────────────────────────────────────────
console.log("\n🚀 Starting cinematic render...\n");

// Step 1: Caption PNGs
const browser = await chromium.launch({
  headless: true,
  args: [
    "--no-sandbox", "--disable-setuid-sandbox",
    "--disable-dev-shm-usage", "--disable-gpu",
    "--no-zygote", "--font-render-hinting=none",
    "--lang=ar,en",
  ],
});

const context = await browser.newContext({
  viewport: { width: WIDTH, height: HEIGHT },
  deviceScaleFactor: 1,
  locale: "ar-SA",
});

const page        = await context.newPage();
const captionPNGs = [];

console.log("🖼️  Rendering word-by-word captions...");
for (let i = 0; i < sentences.length; i++) {
  const s = sentences[i];
  process.stdout.write(`  [${i + 1}/${sentences.length}] "${s.slice(0, 55)}"... `);
  captionPNGs.push(await renderCaptionPNG(page, s, i));
  process.stdout.write("✓\n");
}
await browser.close();
console.log("✅ Captions done\n");

// Step 2: Effects + overlay
console.log("🎬 Applying cinematic effects + blue grade...");
const finalClips = [];

for (let i = 0; i < sentences.length; i++) {
  const videoSrc   = videos[i] || videos[videos.length - 1];
  const captionPng = captionPNGs[i];
  const effected   = `${TMP}/effected_${String(i).padStart(3, "0")}.mp4`;
  const final      = `${TMP}/final_clip_${String(i).padStart(3, "0")}.mp4`;

  process.stdout.write(`  [${i + 1}/${sentences.length}] ${basename(videoSrc)}... `);
  applyEffectsAndColor(videoSrc, CLIP_DURATION, effected, i);
  overlayCaption(effected, captionPng, final);
  finalClips.push(final);
  process.stdout.write("✓\n");
}

// Step 3: Transitions
console.log(`\n✨ Applying ${TRANSITIONS.slice(0, sentences.length - 1).join(", ")} transitions...`);
const dissolved = xfadeConcat(finalClips);

// Step 4: Audio
console.log("🎵 Merging voiceover...");
mergeAudio(dissolved, audio, outputPath);

console.log(`\n🎉 Final cinematic video → ${outputPath}`);
