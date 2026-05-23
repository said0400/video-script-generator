import { readFileSync, writeFileSync, mkdirSync, copyFileSync } from "fs";
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
const { sentences, videos, audio, duration_s, title } = props;

const FPS           = 30;
const WIDTH         = 1080;
const HEIGHT        = 1920;
const CLIP_DURATION = duration_s / sentences.length;
const FADE_DUR      = 0.35;
const XFADE_DUR     = 0.4;
const TMP           = "/tmp/vsg_render";

mkdirSync(TMP, { recursive: true });

console.log(`📋 Sentences : ${sentences.length}`);
console.log(`🎬 Title     : ${title}`);
console.log(`⏱️  Per clip  : ${CLIP_DURATION.toFixed(2)}s`);

const TRANSITIONS = [
  "fade","slideleft","slideright","slideup",
  "smoothleft","smoothright","circleopen",
  "radial","pixelize","dissolve",
];
const getTransition = i => TRANSITIONS[i % TRANSITIONS.length];
const isArabic      = t => /[\u0600-\u06FF]/.test(t);

// ── Build PNG HTML for one word state ────────────────────────────────────────
function buildWordPNG(sentence, currentWordIdx, titleText) {
  const words   = sentence.split(" ");
  const isAr    = isArabic(sentence);
  const isTitleAr = isArabic(titleText);
  const dir     = isAr ? "rtl" : "ltr";
  const lang    = isAr ? "ar" : "en";

  const bodyFont  = isAr
    ? `"Noto Naskh Arabic","Amiri",serif`
    : `"Inter","Helvetica Neue",Arial,sans-serif`;
  const titleFont = isTitleAr
    ? `"Noto Naskh Arabic","Amiri",serif`
    : `"Inter","Helvetica Neue",Arial,sans-serif`;

  const prevWords = words.slice(0, currentWordIdx).join(" ");
  const currWord  = words[currentWordIdx] || "";
  const nextWords = words.slice(currentWordIdx + 1).join(" ");

  // Progress bar
  const progress = ((currentWordIdx + 1) / words.length * 100).toFixed(1);

  return `<!DOCTYPE html>
<html lang="${lang}">
<head>
  <meta charset="UTF-8"/>
  <link href="https://fonts.googleapis.com/css2?family=Noto+Naskh+Arabic:wght@700&family=Amiri:wght@700&family=Inter:wght@700;800;900&display=swap" rel="stylesheet"/>
  <style>
    *{margin:0;padding:0;box-sizing:border-box;}
    html,body{
      width:${WIDTH}px;height:${HEIGHT}px;
      overflow:hidden;background:transparent;
    }

    /* ── TITLE — positioned at 12% from top ── */
    .title-wrap{
      position:absolute;
      top:${Math.round(HEIGHT * 0.12)}px;
      left:0;right:0;
      display:flex;
      justify-content:center;
      padding:0 48px;
    }
    .title-pill{
      display:inline-flex;
      align-items:center;
      gap:14px;
      background:linear-gradient(135deg,rgba(255,255,255,0.18),rgba(255,255,255,0.08));
      border:1.5px solid rgba(255,255,255,0.40);
      border-radius:60px;
      padding:18px 48px;
      backdrop-filter:blur(16px);
      -webkit-backdrop-filter:blur(16px);
      box-shadow:0 8px 32px rgba(0,0,0,0.4),inset 0 1px 0 rgba(255,255,255,0.2);
      direction:${isTitleAr?"rtl":"ltr"};
      max-width:900px;
    }
    .title-icon{
      font-size:34px;
      flex-shrink:0;
    }
    .title-text{
      font-family:${titleFont};
      font-size:${isTitleAr?"40px":"36px"};
      font-weight:800;
      color:#ffffff;
      letter-spacing:${isTitleAr?"0.02em":"-0.02em"};
      text-shadow:0 2px 16px rgba(0,0,0,0.7);
      line-height:1.3;
      text-align:center;
    }

    /* ── BOTTOM OVERLAY ── */
    .bottom-overlay{
      position:absolute;
      bottom:0;left:0;right:0;
      height:58%;
      background:linear-gradient(
        to top,
        rgba(0,0,0,0.92) 0%,
        rgba(0,0,0,0.60) 35%,
        rgba(0,0,0,0.15) 65%,
        transparent 100%
      );
    }

    /* ── WORD DISPLAY — centered in bottom 40% ── */
    .word-area{
      position:absolute;
      bottom:${Math.round(HEIGHT * 0.13)}px;
      left:0;right:0;
      display:flex;
      flex-direction:column;
      align-items:center;
      padding:0 56px;
      direction:${dir};
      gap:10px;
    }

    /* Previous words — faded small */
    .prev{
      font-family:${bodyFont};
      font-size:${isAr?"48px":"44px"};
      font-weight:700;
      color:rgba(255,255,255,0.50);
      text-align:center;
      line-height:1.35;
      text-shadow:0 2px 8px rgba(0,0,0,0.9);
      max-width:950px;
      min-height:${isAr?"58px":"54px"};
    }

    /* Current word — BIG YELLOW */
    .curr{
      font-family:${bodyFont};
      font-size:${isAr?"118px":"112px"};
      font-weight:900;
      color:#FFE600;
      text-align:center;
      line-height:1.05;
      letter-spacing:${isAr?"0.01em":"-0.03em"};
      text-shadow:
        0 0  30px rgba(255,230,0,0.55),
        0 5px 24px rgba(0,0,0,1),
        3px 3px 0 rgba(0,0,0,0.7),
        -1px -1px 0 rgba(0,0,0,0.5);
    }

    /* Next words — very faded */
    .next{
      font-family:${bodyFont};
      font-size:${isAr?"42px":"38px"};
      font-weight:600;
      color:rgba(255,255,255,0.22);
      text-align:center;
      line-height:1.35;
      max-width:950px;
      min-height:${isAr?"52px":"46px"};
    }

    /* ── PROGRESS BAR ── */
    .progress-wrap{
      position:absolute;
      bottom:60px;
      left:56px;right:56px;
      height:5px;
      background:rgba(255,255,255,0.18);
      border-radius:3px;
      overflow:hidden;
    }
    .progress-fill{
      height:100%;
      width:${progress}%;
      background:linear-gradient(90deg,#FFE600,#FF8C00);
      border-radius:3px;
    }

    /* Word count indicator */
    .word-count{
      position:absolute;
      bottom:72px;
      right:56px;
      font-family:${bodyFont};
      font-size:26px;
      font-weight:700;
      color:rgba(255,255,255,0.45);
      letter-spacing:0.05em;
    }
  </style>
</head>
<body>
  <!-- Title -->
  <div class="title-wrap">
    <div class="title-pill">
      <span class="title-icon">▶</span>
      <span class="title-text">${titleText}</span>
    </div>
  </div>

  <!-- Bottom overlay -->
  <div class="bottom-overlay"></div>

  <!-- Word display -->
  <div class="word-area">
    <div class="prev">${prevWords}</div>
    <div class="curr">${currWord}</div>
    <div class="next">${nextWords}</div>
  </div>

  <!-- Progress bar -->
  <div class="progress-wrap">
    <div class="progress-fill"></div>
  </div>
  <div class="word-count">${currentWordIdx + 1}/${words.length}</div>
</body>
</html>`;
}

// ── Render one PNG per word ───────────────────────────────────────────────────
async function renderWordPNGs(page, sentence, clipIndex, titleText) {
  const words    = sentence.split(" ");
  const pngPaths = [];

  // Load fonts on first page load
  const initHTML = buildWordPNG(sentence, 0, titleText);
  const initPath = `${TMP}/init_${clipIndex}.html`;
  writeFileSync(initPath, initHTML, "utf-8");
  await page.goto(`file://${initPath}`, { waitUntil: "load" });
  await page.waitForTimeout(1200); // font load

  for (let w = 0; w < words.length; w++) {
    const html = buildWordPNG(sentence, w, titleText);
    const htmlPath = `${TMP}/wpng_${clipIndex}_${w}.html`;
    writeFileSync(htmlPath, html, "utf-8");

    await page.goto(`file://${htmlPath}`, { waitUntil: "load" });
    await page.waitForTimeout(60);

    const pngPath = `${TMP}/word_${clipIndex}_${w}.png`;
    await page.screenshot({
      path: pngPath,
      type: "png",
      omitBackground: true,
    });
    pngPaths.push(pngPath);
  }

  return pngPaths;
}

// ── Build frame list: each word PNG repeated for its duration ─────────────────
function buildFrameList(wordPNGs, totalFrames, clipIndex) {
  const n             = wordPNGs.length;
  const framesPerWord = Math.floor((totalFrames * 0.88) / n);
  const holdLast      = totalFrames - framesPerWord * (n - 1);

  const frameDir  = `${TMP}/frames_${clipIndex}`;
  mkdirSync(frameDir, { recursive: true });

  let frameIdx = 0;

  for (let w = 0; w < n; w++) {
    const count = w === n - 1 ? holdLast : framesPerWord;
    for (let f = 0; f < count; f++) {
      const dest = `${frameDir}/frame_${String(frameIdx).padStart(6, "0")}.png`;
      copyFileSync(wordPNGs[w], dest);
      frameIdx++;
    }
  }

  return frameDir;
}

// ── Frames → MP4 (opaque PNG → x264) ─────────────────────────────────────────
// We use PNG with transparency, overlay via FFmpeg filter_complex
function framesToMp4(frameDir, outPath) {
  const result = spawnSync("ffmpeg", [
    "-y",
    "-framerate", String(FPS),
    "-i", `${frameDir}/frame_%06d.png`,
    "-vf", `scale=${WIDTH}:${HEIGHT},format=rgba`,
    "-c:v", "png",
    "-an",
    outPath.replace(".mp4", ".mov"), // keep alpha in MOV/PNG codec
  ], { stdio: ["ignore", "pipe", "pipe"] });

  if (result.status !== 0) {
    console.error("❌ frames→mov:\n" + result.stderr.toString().slice(-600));
    process.exit(1);
  }
  return outPath.replace(".mp4", ".mov");
}

// ── Ken Burns + blue cinematic grade + fade ───────────────────────────────────
function processBackground(videoPath, duration, outPath, clipIndex) {
  const totalFrames = Math.ceil(duration * FPS);

  const zoomIn =
    `zoompan=z='min(zoom+0.0004,1.09)':` +
    `x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':` +
    `d=${totalFrames}:s=${WIDTH}x${HEIGHT}:fps=${FPS}`;

  const zoomOut =
    `zoompan=z='if(eq(on\\,1)\\,1.09\\,max(zoom-0.0004\\,1.0))':` +
    `x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':` +
    `d=${totalFrames}:s=${WIDTH}x${HEIGHT}:fps=${FPS}`;

  const kenBurns = clipIndex % 2 === 0 ? zoomIn : zoomOut;

  const colorGrade =
    `curves=r='0/0 0.5/0.46 1/0.88':g='0/0 0.5/0.50 1/0.97':b='0/0.04 0.5/0.56 1/1.0',` +
    `hue=s=0.82,vignette=PI/5`;

  const fade =
    `fade=t=in:st=0:d=${FADE_DUR},` +
    `fade=t=out:st=${(duration - FADE_DUR).toFixed(3)}:d=${FADE_DUR}`;

  const fullFilter =
    `scale=${WIDTH * 1.1}:${HEIGHT * 1.1}:force_original_aspect_ratio=increase,` +
    `crop=${WIDTH * 1.1}:${HEIGHT * 1.1},` +
    `${kenBurns},${colorGrade},${fade}`;

  let result = spawnSync("ffmpeg", [
    "-y", "-i", videoPath,
    "-t", duration.toFixed(3),
    "-vf", fullFilter,
    "-r", String(FPS),
    "-c:v", "libx264", "-preset", "fast", "-crf", "22",
    "-pix_fmt", "yuv420p", "-an",
    outPath,
  ], { stdio: ["ignore", "pipe", "pipe"] });

  if (result.status !== 0) {
    // Fallback: no Ken Burns
    const simple =
      `scale=${WIDTH}:${HEIGHT}:force_original_aspect_ratio=increase,` +
      `crop=${WIDTH}:${HEIGHT},setsar=1,${colorGrade},${fade}`;

    result = spawnSync("ffmpeg", [
      "-y", "-i", videoPath,
      "-t", duration.toFixed(3),
      "-vf", simple,
      "-r", String(FPS),
      "-c:v", "libx264", "-preset", "fast", "-crf", "22",
      "-pix_fmt", "yuv420p", "-an",
      outPath,
    ], { stdio: ["ignore", "pipe", "pipe"] });

    if (result.status !== 0) {
      console.error("❌ BG failed:\n" + result.stderr.toString().slice(-400));
      process.exit(1);
    }
  }
  return outPath;
}

// ── Overlay MOV (PNG alpha) on MP4 background ─────────────────────────────────
function overlayOnBackground(bgMp4, captionMov, outPath) {
  const result = spawnSync("ffmpeg", [
    "-y",
    "-i", bgMp4,
    "-i", captionMov,
    "-filter_complex",
    // overlay with alpha compositing
    "[1:v]format=rgba[cap];[0:v][cap]overlay=0:0:format=auto,format=yuv420p[out]",
    "-map", "[out]",
    "-c:v", "libx264",
    "-preset", "fast",
    "-crf", "20",
    "-pix_fmt", "yuv420p",
    "-an",
    outPath,
  ], { stdio: ["ignore", "pipe", "pipe"] });

  if (result.status !== 0) {
    console.error("❌ Overlay:\n" + result.stderr.toString().slice(-1000));
    process.exit(1);
  }
  return outPath;
}

// ── xfade concat ─────────────────────────────────────────────────────────────
function xfadeConcat(clipPaths) {
  if (clipPaths.length === 1) return clipPaths[0];

  const filterParts = [];
  let offset        = 0;
  let lastLabel     = "[0:v]";

  for (let i = 1; i < clipPaths.length; i++) {
    offset += CLIP_DURATION - XFADE_DUR;
    const outLabel = i === clipPaths.length - 1 ? "[vout]" : `[v${i}]`;
    filterParts.push(
      `${lastLabel}[${i}:v]xfade=transition=${getTransition(i-1)}` +
      `:duration=${XFADE_DUR}:offset=${offset.toFixed(3)}${outLabel}`
    );
    lastLabel = outLabel;
  }

  const outPath = `${TMP}/xfaded.mp4`;
  const result  = spawnSync("ffmpeg", [
    "-y",
    ...clipPaths.flatMap(p => ["-i", p]),
    "-filter_complex", filterParts.join(";"),
    "-map", "[vout]",
    "-c:v", "libx264", "-preset", "fast", "-crf", "20",
    "-pix_fmt", "yuv420p", "-an",
    outPath,
  ], { stdio: ["ignore", "pipe", "pipe"] });

  if (result.status !== 0) {
    // Simple concat fallback
    const listFile = `${TMP}/list.txt`;
    writeFileSync(listFile, clipPaths.map(p=>`file '${p}'`).join("\n"));
    const raw = `${TMP}/raw_final.mp4`;
    spawnSync("ffmpeg",["-y","-f","concat","-safe","0",
      "-i",listFile,"-c","copy",raw],{stdio:"inherit"});
    return raw;
  }
  return outPath;
}

// ── Merge audio ───────────────────────────────────────────────────────────────
function mergeAudio(videoPath, audioPath, outPath) {
  const result = spawnSync("ffmpeg", [
    "-y",
    "-i", videoPath, "-i", audioPath,
    "-map","0:v:0","-map","1:a:0",
    "-c:v","copy","-c:a","aac","-b:a","192k",
    "-shortest", outPath,
  ], { stdio: ["ignore", "pipe", "pipe"] });

  if (result.status !== 0) {
    console.error("❌ Audio:\n" + result.stderr.toString().slice(-500));
    process.exit(1);
  }
}

// ── Main ──────────────────────────────────────────────────────────────────────
console.log("\n🚀 TikTok cinematic render...\n");

const browser = await chromium.launch({
  headless: true,
  args: [
    "--no-sandbox","--disable-setuid-sandbox",
    "--disable-dev-shm-usage","--disable-gpu",
    "--no-zygote","--font-render-hinting=none",
    "--lang=ar,en",
  ],
});

const context = await browser.newContext({
  viewport:{ width:WIDTH, height:HEIGHT },
  deviceScaleFactor:1,
  locale:"ar-SA",
});

const page       = await context.newPage();
const finalClips = [];

console.log("🖼️  Rendering word PNGs + building caption videos...");

for (let i = 0; i < sentences.length; i++) {
  const s     = sentences[i];
  const words = s.split(" ");

  process.stdout.write(
    `  [${i+1}/${sentences.length}] "${s.slice(0,50)}" (${words.length}w)... `
  );

  // 1. Render one PNG per word
  const wordPNGs = await renderWordPNGs(page, s, i, title);

  // 2. Build frame directory (copy PNGs as frames)
  const totalFrames = Math.ceil(CLIP_DURATION * FPS);
  const frameDir    = buildFrameList(wordPNGs, totalFrames, i);

  // 3. Frames → MOV with alpha
  const captionMov  = `${TMP}/caption_${i}.mov`;
  framesToMp4(frameDir, `${TMP}/caption_${i}.mp4`); // output is .mov

  // 4. Process background video
  const videoSrc = videos[i] || videos[videos.length - 1];
  const bgMp4    = `${TMP}/bg_${String(i).padStart(3,"0")}.mp4`;
  processBackground(videoSrc, CLIP_DURATION, bgMp4, i);

  // 5. Overlay caption on background
  const finalClip = `${TMP}/final_${String(i).padStart(3,"0")}.mp4`;
  overlayOnBackground(bgMp4, captionMov, finalClip);
  finalClips.push(finalClip);

  process.stdout.write("✓\n");
}

await browser.close();

// Step 6: Transitions
const transNames = finalClips.slice(0,-1).map((_,i)=>getTransition(i));
console.log(`\n✨ Transitions: ${transNames.join(" → ")}`);
const dissolved = xfadeConcat(finalClips);

// Step 7: Audio
console.log("🎵 Merging voiceover...");
mergeAudio(dissolved, audio, outputPath);

console.log(`\n🎉 Final video → ${outputPath}`);
