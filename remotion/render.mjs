import { readFileSync, writeFileSync, mkdirSync } from "fs";
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

console.log(`📋 Sentences  : ${sentences.length}`);
console.log(`🎬 Title      : ${title}`);
console.log(`⏱️  Per clip   : ${CLIP_DURATION.toFixed(2)}s`);

// ── Transitions ───────────────────────────────────────────────────────────────
const TRANSITIONS = [
  "fade", "slideleft", "slideright", "slideup",
  "smoothleft", "smoothright", "circleopen",
  "radial", "pixelize", "dissolve",
];
const getTransition = i => TRANSITIONS[i % TRANSITIONS.length];

// ── Arabic detection ──────────────────────────────────────────────────────────
const isArabic = t => /[\u0600-\u06FF]/.test(t);

// ── TikTok-style: one word at a time, highlighted, centered ──────────────────
function buildTikTokFrame(sentence, visibleWordIndex, titleText) {
  const words   = sentence.split(" ");
  const dir     = isArabic(sentence) ? "rtl" : "ltr";
  const lang    = isArabic(sentence) ? "ar" : "en";
  const isAr    = isArabic(sentence);

  const fontFace = isAr
    ? `"Noto Naskh Arabic", "Amiri", serif`
    : `"Inter", "Helvetica Neue", Arial, sans-serif`;

  const titleFont = isArabic(titleText)
    ? `"Noto Naskh Arabic", "Amiri", serif`
    : `"Inter", "Helvetica Neue", Arial, sans-serif`;

  // Current word to highlight (TikTok style: show one word big + highlighted)
  const currentWord = words[visibleWordIndex] || "";
  const isTitle     = isArabic(titleText);

  return `<!DOCTYPE html>
<html lang="${lang}">
<head>
  <meta charset="UTF-8"/>
  <link href="https://fonts.googleapis.com/css2?family=Noto+Naskh+Arabic:wght@700&family=Amiri:wght@700&family=Inter:wght@700;800;900&display=swap" rel="stylesheet"/>
  <style>
    * { margin:0; padding:0; box-sizing:border-box; }
    html, body {
      width: ${WIDTH}px; height: ${HEIGHT}px;
      overflow: hidden; background: transparent;
    }

    /* ── TOP TITLE ── */
    .title-area {
      position: absolute;
      top: 80px;
      left: 0; right: 0;
      padding: 0 50px;
      text-align: center;
      direction: ${isArabic(titleText) ? "rtl" : "ltr"};
    }

    .title-badge {
      display: inline-block;
      background: rgba(255,255,255,0.12);
      border: 2px solid rgba(255,255,255,0.35);
      border-radius: 50px;
      padding: 16px 44px;
      backdrop-filter: blur(12px);
    }

    .title-text {
      font-family: ${titleFont};
      font-size: 38px;
      font-weight: 800;
      color: #ffffff;
      letter-spacing: ${isArabic(titleText) ? "0.02em" : "-0.02em"};
      text-shadow: 0 2px 12px rgba(0,0,0,0.8);
    }

    /* ── BOTTOM GRADIENT ── */
    .gradient {
      position: absolute;
      bottom: 0; left: 0; right: 0;
      height: 55%;
      background: linear-gradient(
        to top,
        rgba(0,0,0,0.88) 0%,
        rgba(0,0,0,0.45) 40%,
        transparent 100%
      );
    }

    /* ── TIKTOK WORD DISPLAY ── */
    .word-area {
      position: absolute;
      bottom: 140px;
      left: 0; right: 0;
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 0px;
      padding: 0 60px;
      direction: ${dir};
    }

    /* Current highlighted word — BIG */
    .current-word {
      font-family: ${fontFace};
      font-size: ${isAr ? "110px" : "108px"};
      font-weight: 900;
      color: #FFE600;
      text-align: center;
      line-height: 1.1;
      letter-spacing: ${isAr ? "0.02em" : "-0.03em"};
      text-shadow:
        0 0  25px rgba(255,230,0,0.6),
        0 4px 20px rgba(0,0,0,1),
        3px 3px 0px rgba(0,0,0,0.8);
      animation: wordPop 0.18s cubic-bezier(0.34,1.56,0.64,1) both;
    }

    @keyframes wordPop {
      0%   { transform: scale(0.6) translateY(15px); opacity:0; }
      70%  { transform: scale(1.12) translateY(-4px); }
      100% { transform: scale(1) translateY(0); opacity:1; }
    }

    /* Previous words — small, faded above */
    .prev-words {
      font-family: ${fontFace};
      font-size: ${isAr ? "46px" : "44px"};
      font-weight: 700;
      color: rgba(255,255,255,0.55);
      text-align: center;
      line-height: 1.4;
      letter-spacing: ${isAr ? "0.02em" : "-0.01em"};
      text-shadow: 0 2px 8px rgba(0,0,0,0.9);
      max-width: 960px;
      margin-bottom: 8px;
    }

    /* Next words — small, faded below */
    .next-words {
      font-family: ${fontFace};
      font-size: ${isAr ? "42px" : "40px"};
      font-weight: 600;
      color: rgba(255,255,255,0.28);
      text-align: center;
      line-height: 1.4;
      letter-spacing: ${isAr ? "0.02em" : "-0.01em"};
      max-width: 960px;
      margin-top: 8px;
    }

    /* Word counter dots */
    .dots {
      position: absolute;
      bottom: 92px;
      left: 0; right: 0;
      display: flex;
      justify-content: center;
      gap: 10px;
    }
    .dot {
      width: 10px; height: 10px;
      border-radius: 50%;
      background: rgba(255,255,255,0.25);
      transition: all 0.2s;
    }
    .dot.active {
      background: #FFE600;
      width: 28px;
      border-radius: 5px;
    }
  </style>
</head>
<body>
  <!-- Top title -->
  <div class="title-area">
    <div class="title-badge">
      <span class="title-text">${titleText}</span>
    </div>
  </div>

  <!-- Bottom gradient -->
  <div class="gradient"></div>

  <!-- TikTok word display -->
  <div class="word-area">
    ${visibleWordIndex > 0
      ? `<div class="prev-words">${words.slice(0, visibleWordIndex).join(" ")}</div>`
      : ""
    }
    <div class="current-word">${currentWord}</div>
    ${visibleWordIndex < words.length - 1
      ? `<div class="next-words">${words.slice(visibleWordIndex + 1).join(" ")}</div>`
      : ""
    }
  </div>

  <!-- Progress dots -->
  <div class="dots">
    ${words.map((_, i) =>
      `<div class="dot ${i === visibleWordIndex ? "active" : ""}"></div>`
    ).join("")}
  </div>
</body>
</html>`;
}

// ── Render word-by-word frames ────────────────────────────────────────────────
async function renderWordFrames(page, sentence, clipIndex, titleText) {
  const words       = sentence.split(" ");
  const totalFrames = Math.ceil(CLIP_DURATION * FPS);
  const frameDir    = `${TMP}/wframes_${clipIndex}`;
  mkdirSync(frameDir, { recursive: true });

  // Frames per word — distribute evenly
  const framesPerWord = Math.floor((totalFrames * 0.85) / words.length);
  const holdFrames    = Math.max(8, framesPerWord);

  // Load fonts first
  const initHTML  = buildTikTokFrame(sentence, 0, titleText);
  const initPath  = `${TMP}/init_${clipIndex}.html`;
  writeFileSync(initPath, initHTML, "utf-8");
  await page.goto(`file://${initPath}`, { waitUntil: "load" });
  await page.waitForTimeout(1000);

  let frameIdx = 0;

  for (let w = 0; w < words.length; w++) {
    const html     = buildTikTokFrame(sentence, w, titleText);
    const htmlPath = `${TMP}/w_${clipIndex}_${w}.html`;
    writeFileSync(htmlPath, html, "utf-8");

    await page.goto(`file://${htmlPath}`, { waitUntil: "load" });
    await page.waitForTimeout(80); // animation settle

    const framesThisWord = w === words.length - 1
      ? Math.max(holdFrames, totalFrames - frameIdx)
      : holdFrames;

    for (let f = 0; f < framesThisWord && frameIdx < totalFrames; f++, frameIdx++) {
      const framePath = `${frameDir}/frame_${String(frameIdx).padStart(6, "0")}.png`;
      await page.screenshot({ path: framePath, type: "png", omitBackground: true });
    }
  }

  // Fill remaining
  while (frameIdx < totalFrames) {
    const last = `${frameDir}/frame_${String(frameIdx - 1).padStart(6, "0")}.png`;
    const cur  = `${frameDir}/frame_${String(frameIdx).padStart(6, "0")}.png`;
    const data = readFileSync(last);
    writeFileSync(cur, data);
    frameIdx++;
  }

  return frameDir;
}

// ── Frames → WebM with alpha ──────────────────────────────────────────────────
function framesToWebm(frameDir, outPath) {
  const result = spawnSync("ffmpeg", [
    "-y",
    "-framerate", String(FPS),
    "-i", `${frameDir}/frame_%06d.png`,
    "-vf", `scale=${WIDTH}:${HEIGHT}`,
    "-c:v", "libvpx-vp9",
    "-pix_fmt", "yuva420p",
    "-b:v", "0", "-crf", "18",
    "-an",
    outPath,
  ], { stdio: ["ignore", "pipe", "pipe"] });

  if (result.status !== 0) {
    console.error("❌ frames→webm:\n" + result.stderr.toString().slice(-800));
    process.exit(1);
  }
  return outPath;
}

// ── Ken Burns + blue grade + fade ─────────────────────────────────────────────
function applyEffectsAndColor(videoPath, duration, outPath, clipIndex) {
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

  const colorGrade = [
    `curves=r='0/0 0.5/0.46 1/0.88':g='0/0 0.5/0.50 1/0.97':b='0/0.04 0.5/0.56 1/1.0'`,
    `hue=s=0.82`,
    `vignette=PI/5`,
  ].join(",");

  const fade =
    `fade=t=in:st=0:d=${FADE_DUR},` +
    `fade=t=out:st=${(duration - FADE_DUR).toFixed(3)}:d=${FADE_DUR}`;

  const fullFilter =
    `scale=${WIDTH * 1.1}:${HEIGHT * 1.1}:` +
    `force_original_aspect_ratio=increase,` +
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
    const fallback =
      `scale=${WIDTH}:${HEIGHT}:force_original_aspect_ratio=increase,` +
      `crop=${WIDTH}:${HEIGHT},setsar=1,${colorGrade},${fade}`;

    result = spawnSync("ffmpeg", [
      "-y", "-i", videoPath,
      "-t", duration.toFixed(3),
      "-vf", fallback,
      "-r", String(FPS),
      "-c:v", "libx264", "-preset", "fast", "-crf", "22",
      "-pix_fmt", "yuv420p", "-an",
      outPath,
    ], { stdio: ["ignore", "pipe", "pipe"] });

    if (result.status !== 0) {
      console.error("❌ Effects failed\n"
        + result.stderr.toString().slice(-500));
      process.exit(1);
    }
  }
  return outPath;
}

// ── Overlay animated WebM (alpha) on video ────────────────────────────────────
function overlayAnimated(videoPath, captionWebm, outPath) {
  const result = spawnSync("ffmpeg", [
    "-y",
    "-i", videoPath,
    "-i", captionWebm,
    "-filter_complex", "[0:v][1:v]overlay=0:0:format=auto[out]",
    "-map", "[out]",
    "-c:v", "libx264", "-preset", "fast", "-crf", "22",
    "-pix_fmt", "yuv420p", "-an",
    outPath,
  ], { stdio: ["ignore", "pipe", "pipe"] });

  if (result.status !== 0) {
    console.error("❌ Overlay:\n" + result.stderr.toString().slice(-800));
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
      `${lastLabel}[${i}:v]xfade=transition=${getTransition(i - 1)}` +
      `:duration=${XFADE_DUR}:offset=${offset.toFixed(3)}${outLabel}`
    );
    lastLabel = outLabel;
  }

  const inputs  = clipPaths.flatMap(p => ["-i", p]);
  const outPath = `${TMP}/xfaded.mp4`;

  const result = spawnSync("ffmpeg", [
    "-y", ...inputs,
    "-filter_complex", filterParts.join(";"),
    "-map", "[vout]",
    "-c:v", "libx264", "-preset", "fast", "-crf", "22",
    "-pix_fmt", "yuv420p", "-an",
    outPath,
  ], { stdio: ["ignore", "pipe", "pipe"] });

  if (result.status !== 0) {
    const listFile = `${TMP}/list.txt`;
    writeFileSync(listFile, clipPaths.map(p => `file '${p}'`).join("\n"));
    const fallback = `${TMP}/raw_final.mp4`;
    spawnSync("ffmpeg", [
      "-y", "-f", "concat", "-safe", "0",
      "-i", listFile, "-c", "copy", fallback,
    ], { stdio: "inherit" });
    return fallback;
  }
  return outPath;
}

// ── Merge audio ───────────────────────────────────────────────────────────────
function mergeAudio(videoPath, audioPath, outPath) {
  const result = spawnSync("ffmpeg", [
    "-y",
    "-i", videoPath, "-i", audioPath,
    "-map", "0:v:0", "-map", "1:a:0",
    "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
    "-shortest", outPath,
  ], { stdio: ["ignore", "pipe", "pipe"] });

  if (result.status !== 0) {
    console.error("❌ Audio merge:\n" + result.stderr.toString().slice(-600));
    process.exit(1);
  }
}

// ── Main ──────────────────────────────────────────────────────────────────────
console.log("\n🚀 Starting TikTok-style cinematic render...\n");

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
  viewport:          { width: WIDTH, height: HEIGHT },
  deviceScaleFactor: 1,
  locale:            "ar-SA",
});

const page         = await context.newPage();
const captionWebms = [];
const finalClips   = [];

// Step 1: Word-by-word frames
console.log("🖼️  Rendering TikTok word-by-word captions...");
for (let i = 0; i < sentences.length; i++) {
  const s     = sentences[i];
  const words = s.split(" ").length;
  process.stdout.write(
    `  [${i + 1}/${sentences.length}] "${s.slice(0, 50)}" (${words}w)... `
  );
  const frameDir = await renderWordFrames(page, s, i, title);
  const webm     = `${TMP}/caption_${i}.webm`;
  framesToWebm(frameDir, webm);
  captionWebms.push(webm);
  process.stdout.write("✓\n");
}
await browser.close();
console.log("✅ Captions done\n");

// Step 2: Effects + overlay
console.log("🎬 Applying cinematic effects...");
for (let i = 0; i < sentences.length; i++) {
  const videoSrc = videos[i] || videos[videos.length - 1];
  const effected = `${TMP}/effected_${String(i).padStart(3, "0")}.mp4`;
  const final    = `${TMP}/final_clip_${String(i).padStart(3, "0")}.mp4`;

  process.stdout.write(
    `  [${i + 1}/${sentences.length}] ${basename(videoSrc)}... `
  );
  applyEffectsAndColor(videoSrc, CLIP_DURATION, effected, i);
  overlayAnimated(effected, captionWebms[i], final);
  finalClips.push(final);
  process.stdout.write("✓\n");
}

// Step 3: Transitions
console.log(`\n✨ Transitions: ${finalClips.slice(0,-1).map((_,i)=>getTransition(i)).join(" → ")}`);
const dissolved = xfadeConcat(finalClips);

// Step 4: Audio
console.log("🎵 Merging voiceover...");
mergeAudio(dissolved, audio, outputPath);

console.log(`\n🎉 Final video → ${outputPath}`);
