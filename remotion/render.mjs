import { readFileSync, writeFileSync, mkdirSync } from "fs";
import { spawnSync } from "child_process";
import { dirname, basename } from "path";
import { fileURLToPath } from "url";
import { chromium } from "playwright";
import { createServer } from "http";
import { readFile } from "fs/promises";
import { extname } from "path";

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
const TMP           = "/tmp/vsg_render";

mkdirSync(TMP, { recursive: true });

console.log(`📋 Sentences  : ${sentences.length}`);
console.log(`⏱️  Per clip   : ${CLIP_DURATION.toFixed(2)}s (${CLIP_FRAMES} frames)`);
console.log(`🎵 Audio      : ${audio}`);

// ── HTTP server to serve local files to Chromium ─────────────────────────────
function startFileServer(port = 7788) {
  const MIME = {
    ".mp4":  "video/mp4",
    ".webm": "video/webm; codecs=vp9",
    ".png":  "image/png",
    ".jpg":  "image/jpeg",
    ".html": "text/html; charset=utf-8",
    ".wav":  "audio/wav",
  };

  const server = createServer(async (req, res) => {
    const url      = new URL(req.url, `http://localhost:${port}`);
    const filePath = url.searchParams.get("path") || url.pathname;

    try {
      const data = await readFile(filePath);
      const mime = MIME[extname(filePath).toLowerCase()] || "application/octet-stream";
      res.writeHead(200, {
        "Content-Type":   mime,
        "Content-Length": data.length,
        "Accept-Ranges":  "bytes",
        "Cache-Control":  "no-cache",
        "Access-Control-Allow-Origin": "*",
      });
      res.end(data);
    } catch {
      res.writeHead(404);
      res.end("Not found");
    }
  });

  server.listen(port);
  console.log(`🌐 File server started on http://localhost:${port}`);
  return server;
}

function fileUrl(filePath, port = 7788) {
  return `http://localhost:${port}/file?path=${encodeURIComponent(filePath)}`;
}

// ── Detect text direction ─────────────────────────────────────────────────────
function isArabic(text) {
  return /[\u0600-\u06FF]/.test(text);
}

// ── Build HTML for one sentence ───────────────────────────────────────────────
function buildHTML(sentence, videoUrl) {
  const dir      = isArabic(sentence) ? "rtl" : "ltr";
  const fontFace = isArabic(sentence)
    ? `"Noto Naskh Arabic", "Amiri", serif`
    : `"Inter", "Helvetica Neue", Arial, sans-serif`;

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
      background: #000;
    }

    .video-bg {
      position: absolute;
      inset: 0;
      width: 100%;
      height: 100%;
      object-fit: cover;
      object-position: center;
      z-index: 0;
    }

    .overlay {
      position: absolute;
      inset: 0;
      z-index: 1;
      background: linear-gradient(
        to top,
        rgba(0,0,0,0.80) 0%,
        rgba(0,0,0,0.25) 50%,
        rgba(0,0,0,0.05) 100%
      );
    }

    .caption-wrapper {
      position: absolute;
      bottom: 160px;
      left: 0;
      right: 0;
      z-index: 2;
      padding: 0 64px;
      text-align: center;
      direction: ${dir};
    }

    .caption {
      display: inline-block;
      color: #ffffff;
      font-family: ${fontFace};
      font-size: 66px;
      font-weight: 700;
      line-height: 1.45;
      letter-spacing: ${isArabic(sentence) ? "0.03em" : "-0.01em"};
      word-spacing: 4px;
      text-shadow:
        0px 2px 12px rgba(0,0,0,1),
        0px 0px 30px rgba(0,0,0,0.9),
        2px 2px 4px rgba(0,0,0,1);
    }
  </style>
</head>
<body>
  <video
    class="video-bg"
    autoplay
    muted
    loop
    playsinline
    preload="auto"
  >
    <source src="${videoUrl}" type="video/webm; codecs=vp9"/>
    <source src="${videoUrl}" type="video/mp4"/>
  </video>
  <div class="overlay"></div>
  <div class="caption-wrapper">
    <span class="caption">${sentence}</span>
  </div>
  <script>
    const vid = document.querySelector('video');
    vid.addEventListener('loadeddata', () => {
      console.log('Video loaded OK, readyState:', vid.readyState);
    });
    vid.addEventListener('error', () => {
      console.error('Video error:', vid.error?.message);
    });
    vid.play().catch(e => console.error('Play error:', e));
  </script>
</body>
</html>`;
}

// ── Screenshot frames for one clip ───────────────────────────────────────────
async function screenshotClip(page, sentence, videoSrc, clipIndex, port) {
  const vUrl     = fileUrl(videoSrc, port);
  const html     = buildHTML(sentence, vUrl);
  const htmlPath = `${TMP}/slide_${clipIndex}.html`;
  writeFileSync(htmlPath, html, "utf-8");

  const pageUrl = `http://localhost:${port}/file?path=${encodeURIComponent(htmlPath)}`;
  await page.goto(pageUrl, { waitUntil: "networkidle", timeout: 20000 });

  // Wait until video is playing
  await page.waitForFunction(() => {
    const v = document.querySelector("video");
    return v && v.readyState >= 3 && !v.paused;
  }, { timeout: 12000 }).catch(() => {
    console.log("  ⚠️  Video not ready in time — screenshot anyway");
  });

  await page.waitForTimeout(300);

  const frameDir = `${TMP}/frames_${clipIndex}`;
  mkdirSync(frameDir, { recursive: true });

  process.stdout.write(`  📸 Capturing ${CLIP_FRAMES} frames...`);

  for (let f = 0; f < CLIP_FRAMES; f++) {
    const framePath = `${frameDir}/frame_${String(f).padStart(6, "0")}.png`;
    await page.screenshot({ path: framePath, type: "png" });
    if (f % 30 === 0) process.stdout.write(` ${f}`);
  }

  process.stdout.write(" ✓\n");
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
    console.error("❌ frames→clip error:\n" + result.stderr.toString().slice(-2000));
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
    console.error("❌ Concat error:\n" + result.stderr.toString().slice(-2000));
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
    console.error("❌ Merge error:\n" + result.stderr.toString().slice(-2000));
    process.exit(1);
  }
}

// ── Main ──────────────────────────────────────────────────────────────────────
console.log("\n🚀 Starting render with Playwright...\n");

const PORT   = 7788;
const server = startFileServer(PORT);

const browser = await chromium.launch({
  headless: true,
  args: [
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--no-zygote",
    "--autoplay-policy=no-user-gesture-required",
    "--disable-web-security",
    "--allow-running-insecure-content",
    "--font-render-hinting=none",
    "--lang=ar,en",
  ],
});

const context = await browser.newContext({
  viewport:          { width: WIDTH, height: HEIGHT },
  deviceScaleFactor: 1,
  locale:            "ar-SA",
  extraHTTPHeaders:  { "Accept-Language": "ar,en;q=0.9" },
});

const page = await context.newPage();

page.on("console", msg => {
  const type = msg.type();
  if (type === "error") {
    console.log(`  🔴 ${msg.text()}`);
  } else if (type === "log" && msg.text().startsWith("Video loaded")) {
    console.log(`  ✅ ${msg.text()}`);
  }
});

const clipPaths = [];

for (let i = 0; i < sentences.length; i++) {
  const sentence = sentences[i];
  const videoSrc = videos[i] || videos[videos.length - 1];

  console.log(`\n[${i + 1}/${sentences.length}] "${sentence.slice(0, 65)}"`);
  console.log(`  🎬 ${basename(videoSrc)}`);

  const frameDir = await screenshotClip(page, sentence, videoSrc, i, PORT);
  const clipPath = framesToClip(frameDir, i);
  clipPaths.push(clipPath);
  console.log(`  ✅ Clip ${i + 1} done`);
}

await browser.close();
server.close();

console.log("\n🔗 Concatenating clips...");
const rawVideo = concatClips(clipPaths);

console.log("🎵 Merging voiceover...");
mergeAudio(rawVideo, audio, outputPath);

console.log(`\n🎉 Final video → ${outputPath}`);
