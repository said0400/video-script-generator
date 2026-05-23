import { readFileSync, writeFileSync, mkdirSync, existsSync } from "fs";
import { spawnSync } from "child_process";
import { resolve, dirname } from "path";
import { fileURLToPath } from "url";
import { chromium } from "playwright";

const __dirname = dirname(fileURLToPath(import.meta.url));

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
const CLIP_FRAMES   = Math.ceil(CLIP_DURATION * FPS);

const TMP = "/tmp/vsg_render";
mkdirSync(TMP, { recursive: true });

console.log(`📋 Sentences  : ${sentences.length}`);
console.log(`⏱️  Per clip   : ${CLIP_DURATION.toFixed(2)}s (${CLIP_FRAMES} frames)`);
console.log(`🎵 Audio      : ${audio}`);

// ── Detect text direction ────────────────────────────────────────────────────
function isArabic(text) {
  return /[\u0600-\u06FF]/.test(text);
}

function getDirection(text) {
  return isArabic(text) ? "rtl" : "ltr";
}

// ── Build HTML for one sentence ──────────────────────────────────────────────
function buildHTML(sentence, videoPath) {
  const dir      = getDirection(sentence);
  const fontFace = isArabic(sentence)
    ? `"Noto Naskh Arabic", "Amiri", "Scheherazade New", sans-serif`
    : `"Inter", "Helvetica Neue", Arial, sans-serif`;

  return `<!DOCTYPE html>
<html lang="${isArabic(sentence) ? "ar" : "en"}">
<head>
  <meta charset="UTF-8"/>
  <style>
    @import url('https://fonts.googleapis.com/css2?family=Noto+Naskh+Arabic:wght@700&family=Amiri:wght@700&family=Inter:wght@700&display=swap');

    * { margin: 0; padding: 0; box-sizing: border-box; }

    body {
      width: ${WIDTH}px;
      height: ${HEIGHT}px;
      overflow: hidden;
      background: #000;
      font-family: ${fontFace};
    }

    .video-bg {
      position: absolute;
      inset: 0;
      width: 100%;
      height: 100%;
      object-fit: cover;
    }

    .overlay {
      position: absolute;
      inset: 0;
      background: linear-gradient(
        to top,
        rgba(0,0,0,0.85) 0%,
        rgba(0,0,0,0.3)  50%,
        rgba(0,0,0,0.1)  100%
      );
    }

    .caption-wrapper {
      position: absolute;
      bottom: 160px;
      left: 0;
      right: 0;
      padding: 0 60px;
      text-align: center;
      direction: ${dir};
    }

    .caption {
      display: inline-block;
      color: #ffffff;
      font-size: 68px;
      font-weight: 700;
      line-height: 1.4;
      text-shadow:
        3px  3px 8px rgba(0,0,0,0.95),
        -2px -2px 6px rgba(0,0,0,0.9),
        0px  0px 20px rgba(0,0,0,0.8);
      letter-spacing: ${isArabic(sentence) ? "0.02em" : "-0.01em"};
      word-spacing: 4px;
    }

    .caption .highlight {
      color: #FFD700;
    }
  </style>
</head>
<body>
  <video class="video-bg" autoplay muted loop>
    <source src="file://${videoPath}" type="video/mp4"/>
  </video>
  <div class="overlay"></div>
  <div class="caption-wrapper">
    <span class="caption">${sentence}</span>
  </div>
</body>
</html>`;
}

// ── Screenshot frames for one clip ───────────────────────────────────────────
async function screenshotClip(page, sentence, videoSrc, clipIndex) {
  const html     = buildHTML(sentence, videoSrc);
  const htmlPath = `${TMP}/slide_${clipIndex}.html`;
  writeFileSync(htmlPath, html, "utf-8");

  await page.goto(`file://${htmlPath}`, { waitUntil: "networkidle" });

  // Wait for video to load
  await page.waitForTimeout(800);

  const frameDir = `${TMP}/frames_${clipIndex}`;
  mkdirSync(frameDir, { recursive: true });

  console.log(`  📸 Capturing ${CLIP_FRAMES} frames for clip ${clipIndex + 1}...`);

  for (let f = 0; f < CLIP_FRAMES; f++) {
    const framePath = `${frameDir}/frame_${String(f).padStart(6, "0")}.png`;
    await page.screenshot({ path: framePath });
  }

  return frameDir;
}

// ── Convert frames → silent video clip ───────────────────────────────────────
function framesToClip(frameDir, clipIndex) {
  const outClip = `${TMP}/clip_${String(clipIndex).padStart(3, "0")}.mp4`;

  const result = spawnSync("ffmpeg", [
    "-y",
    "-framerate", String(FPS),
    "-i", `${frameDir}/frame_%06d.png`,
    "-vf", `scale=${WIDTH}:${HEIGHT}`,
    "-c:v", "libx264",
    "-preset", "fast",
    "-crf", "22",
    "-pix_fmt", "yuv420p",
    "-an",
    outClip,
  ], { stdio: ["ignore", "pipe", "pipe"] });

  if (result.status !== 0) {
    console.error("❌ frames→clip error:\n" + result.stderr.toString().slice(-1500));
    process.exit(1);
  }
  return outClip;
}

// ── Concatenate clips ─────────────────────────────────────────────────────────
function concatClips(clipPaths) {
  const listFile = `${TMP}/concat.txt`;
  writeFileSync(listFile, clipPaths.map(p => `file '${p}'`).join("\n"));

  const rawVideo = `${TMP}/raw.mp4`;
  const result = spawnSync("ffmpeg", [
    "-y", "-f", "concat", "-safe", "0",
    "-i", listFile,
    "-c", "copy",
    rawVideo,
  ], { stdio: ["ignore", "pipe", "pipe"] });

  if (result.status !== 0) {
    console.error("❌ Concat error:\n" + result.stderr.toString().slice(-1500));
    process.exit(1);
  }
  return rawVideo;
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
    console.error("❌ Merge error:\n" + result.stderr.toString().slice(-1500));
    process.exit(1);
  }
}

// ── Main ──────────────────────────────────────────────────────────────────────
console.log("\n🚀 Starting render with Playwright...\n");

const browser = await chromium.launch({
  headless: true,
  args: [
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--no-zygote",
    "--font-render-hinting=none",
    "--disable-font-subpixel-positioning",
    "--lang=ar,en",
  ],
});

const context = await browser.newContext({
  viewport: { width: WIDTH, height: HEIGHT },
  deviceScaleFactor: 1,
  locale: "ar-SA",
});

const page = await context.newPage();

// Allow local file access
await context.grantPermissions([]);

const clipPaths = [];

for (let i = 0; i < sentences.length; i++) {
  const sentence = sentences[i];
  const videoSrc = videos[i] || videos[videos.length - 1];

  console.log(`\n[${i + 1}/${sentences.length}] "${sentence.slice(0, 60)}"`);
  console.log(`  🎬 Video: ${videoSrc.split("/").pop()}`);

  const frameDir = await screenshotClip(page, sentence, videoSrc, i);
  const clipPath = framesToClip(frameDir, i);
  clipPaths.push(clipPath);
  console.log(`  ✅ Clip ${i + 1} done`);
}

await browser.close();

console.log("\n🔗 Concatenating clips...");
const rawVideo = concatClips(clipPaths);

console.log("🎵 Merging voiceover...");
mergeAudio(rawVideo, audio, outputPath);

console.log(`\n🎉 Final video → ${outputPath}`);
