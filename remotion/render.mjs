import { readFileSync, writeFileSync, mkdirSync } from "fs";
import { spawnSync } from "child_process";
import { dirname, basename } from "path";
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
const TMP           = "/tmp/vsg_render";

mkdirSync(TMP, { recursive: true });

console.log(`📋 Sentences  : ${sentences.length}`);
console.log(`⏱️  Per clip   : ${CLIP_DURATION.toFixed(2)}s`);
console.log(`🎵 Audio      : ${audio}`);

// ── Detect Arabic ─────────────────────────────────────────────────────────────
function isArabic(text) {
  return /[\u0600-\u06FF]/.test(text);
}

// ── Build caption HTML (transparent background) ───────────────────────────────
function buildCaptionHTML(sentence) {
  const dir      = isArabic(sentence) ? "rtl" : "ltr";
  const fontFace = isArabic(sentence)
    ? `"Noto Naskh Arabic", "Amiri", serif`
    : `"Inter", "Helvetica Neue", Arial, sans-serif`;
  const fontSize = isArabic(sentence) ? "72px" : "66px";

  return `<!DOCTYPE html>
<html lang="${isArabic(sentence) ? "ar" : "en"}">
<head>
  <meta charset="UTF-8"/>
  <link
    href="https://fonts.googleapis.com/css2?family=Noto+Naskh+Arabic:wght@700&family=Amiri:wght@700&family=Inter:wght@700&display=swap"
    rel="stylesheet"
  />
  <style>
    * { margin: 0; padding: 0; box-sizing: border-box; }

    html, body {
      width: ${WIDTH}px;
      height: ${HEIGHT}px;
      overflow: hidden;
      background: transparent;
    }

    .gradient {
      position: absolute;
      bottom: 0;
      left: 0;
      right: 0;
      height: 55%;
      background: linear-gradient(
        to top,
        rgba(0,0,0,0.85) 0%,
        rgba(0,0,0,0.4)  50%,
        transparent      100%
      );
    }

    .caption-wrapper {
      position: absolute;
      bottom: 140px;
      left: 0;
      right: 0;
      padding: 0 64px;
      text-align: center;
      direction: ${dir};
    }

    .caption {
      display: inline-block;
      color: #ffffff;
      font-family: ${fontFace};
      font-size: ${fontSize};
      font-weight: 700;
      line-height: 1.45;
      letter-spacing: ${isArabic(sentence) ? "0.03em" : "-0.01em"};
      word-spacing: 4px;
      text-shadow:
        0px 3px 14px rgba(0,0,0,1),
        0px 0px 30px rgba(0,0,0,0.95),
        2px 2px 4px rgba(0,0,0,1),
        -1px -1px 3px rgba(0,0,0,1);
    }
  </style>
</head>
<body>
  <div class="gradient"></div>
  <div class="caption-wrapper">
    <span class="caption">${sentence}</span>
  </div>
</body>
</html>`;
}

// ── Render caption PNG via Playwright ─────────────────────────────────────────
async function renderCaptionPNG(page, sentence, index) {
  const html     = buildCaptionHTML(sentence);
  const htmlPath = `${TMP}/caption_${index}.html`;
  writeFileSync(htmlPath, html, "utf-8");

  await page.goto(`file://${htmlPath}`, { waitUntil: "load" });
  await page.waitForTimeout(1500);

  const pngPath = `${TMP}/caption_${index}.png`;
  await page.screenshot({
    path: pngPath,
    type: "png",
    omitBackground: true,
  });

  return pngPath;
}

// ── FFmpeg: overlay caption PNG on video ──────────────────────────────────────
function overlayCaption(videoPath, captionPng, duration, outPath) {
  const result = spawnSync("ffmpeg", [
    "-y",
    "-i", videoPath,
    "-i", captionPng,
    "-t", duration.toFixed(3),
    "-filter_complex", "[0:v][1:v]overlay=0:0[out]",
    "-map", "[out]",
    "-r", String(FPS),
    "-c:v", "libx264",
    "-preset", "fast",
    "-crf", "22",
    "-pix_fmt", "yuv420p",
    "-an",
    outPath,
  ], { stdio: ["ignore", "pipe", "pipe"] });

  if (result.status !== 0) {
    console.error("❌ overlay error:\n" + result.stderr.toString().slice(-1500));
    process.exit(1);
  }
  return outPath;
}

// ── Concatenate final clips ───────────────────────────────────────────────────
function concatClips(clipPaths) {
  const listFile = `${TMP}/final_list.txt`;
  writeFileSync(listFile, clipPaths.map(p => `file '${p}'`).join("\n"));

  const rawVideo = `${TMP}/raw_final.mp4`;
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
console.log("\n🚀 Starting render...\n");

// Step 1: Render caption PNGs with Playwright
const browser = await chromium.launch({
  headless: true,
  args: [
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--no-zygote",
    "--font-render-hinting=none",
    "--lang=ar,en",
  ],
});

const context = await browser.newContext({
  viewport:          { width: WIDTH, height: HEIGHT },
  deviceScaleFactor: 1,
  locale:            "ar-SA",
});

const page    = await context.newPage();
const captionPNGs = [];

console.log("🖼️  Rendering caption PNGs...");
for (let i = 0; i < sentences.length; i++) {
  const sentence = sentences[i];
  process.stdout.write(`  [${i + 1}/${sentences.length}] "${sentence.slice(0, 50)}"... `);
  const png = await renderCaptionPNG(page, sentence, i);
  captionPNGs.push(png);
  process.stdout.write("✓\n");
}

await browser.close();
console.log("✅ All captions rendered\n");

// Step 2: Overlay captions on videos with FFmpeg
console.log("🎬 Overlaying captions on videos...");
const finalClips = [];

for (let i = 0; i < sentences.length; i++) {
  const videoSrc  = videos[i] || videos[videos.length - 1];
  const captionPng = captionPNGs[i];
  const outClip   = `${TMP}/final_clip_${String(i).padStart(3, "0")}.mp4`;

  process.stdout.write(`  [${i + 1}/${sentences.length}] ${basename(videoSrc)}... `);
  overlayCaption(videoSrc, captionPng, CLIP_DURATION, outClip);
  finalClips.push(outClip);
  process.stdout.write("✓\n");
}

// Step 3: Concat all clips
console.log("\n🔗 Concatenating clips...");
const rawVideo = concatClips(finalClips);

// Step 4: Merge voiceover
console.log("🎵 Merging voiceover...");
mergeAudio(rawVideo, audio, outputPath);

console.log(`\n🎉 Final video → ${outputPath}`);
