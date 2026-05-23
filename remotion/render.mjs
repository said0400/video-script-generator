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

// ── Transitions ───────────────────────────────────────────────────────────────
const TRANSITIONS = [
  "fade", "slideleft", "slideright", "slideup",
  "smoothleft", "smoothright", "circleopen",
  "radial", "pixelize", "dissolve",
];
const getTransition = i => TRANSITIONS[i % TRANSITIONS.length];

// ── Arabic detection ──────────────────────────────────────────────────────────
const isArabic = t => /[\u0600-\u06FF]/.test(t);

// ── Build HTML showing N words visible (word-by-word state) ───────────────────
function buildWordHTML(sentence, visibleCount) {
  const dir      = isArabic(sentence) ? "rtl" : "ltr";
  const lang     = isArabic(sentence) ? "ar" : "en";
  const fontFace = isArabic(sentence)
    ? `"Noto Naskh Arabic", "Amiri", serif`
    : `"Inter", "Helvetica Neue", Arial, sans-serif`;
  const fontSize = isArabic(sentence) ? "74px" : "66px";

  const words = sentence.split(" ");

  const wordSpans = words.map((word, i) => {
    const visible = i < visibleCount;

    // Different entry animations cycling through words
    const animations = [
      `popIn`,       // bounce up
      `slideInLeft`, // slide from left
      `zoomIn`,      // zoom
      `dropIn`,      // drop from top
      `fadeIn`,      // simple fade
    ];
    const anim = animations[i % animations.length];

    return visible
      ? `<span class="word visible ${anim}">${word}</span>`
      : `<span class="word hidden">${word}</span>`;
  }).join(`<span class="sp"> </span>`);

  return `<!DOCTYPE html>
<html lang="${lang}">
<head>
  <meta charset="UTF-8"/>
  <link href="https://fonts.googleapis.com/css2?family=Noto+Naskh+Arabic:wght@700&family=Amiri:wght@700&family=Inter:wght@700;800&display=swap" rel="stylesheet"/>
  <style>
    * { margin:0; padding:0; box-sizing:border-box; }
    html, body {
      width: ${WIDTH}px; height: ${HEIGHT}px;
      overflow: hidden; background: transparent;
    }

    /* Bottom gradient */
    .gradient {
      position: absolute;
      bottom:0; left:0; right:0; height:62%;
      background: linear-gradient(
        to top,
        rgba(0,0,0,0.92) 0%,
        rgba(0,0,0,0.55) 38%,
        rgba(0,0,0,0.10) 65%,
        transparent 100%
      );
    }

    /* Caption area */
    .caption-wrapper {
      position: absolute;
      bottom: 155px; left:0; right:0;
      padding: 0 58px;
      text-align: center;
      direction: ${dir};
      line-height: 1.55;
    }

    .word {
      display: inline-block;
      font-family: ${fontFace};
      font-size: ${fontSize};
      font-weight: 800;
      color: #fff;
      letter-spacing: ${isArabic(sentence) ? "0.02em" : "-0.01em"};
      text-shadow:
        0 4px 18px rgba(0,0,0,1),
        0 0  45px rgba(0,0,0,0.95),
        2px 2px 8px rgba(0,0,0,1);
    }

    .hidden { opacity:0; }

    .sp { display:inline-block; width:0.27em; }

    /* ── Animations ── */
    .popIn {
      animation: popIn 0.28s cubic-bezier(0.34,1.56,0.64,1) both;
    }
    @keyframes popIn {
      from { opacity:0; transform: translateY(22px) scale(0.8); }
      70%  { transform: translateY(-5px) scale(1.08); }
      to   { opacity:1; transform: translateY(0) scale(1); }
    }

    .slideInLeft {
      animation: slideInLeft 0.30s cubic-bezier(0.22,1,0.36,1) both;
    }
    @keyframes slideInLeft {
      from { opacity:0; transform: translateX(${isArabic(sentence) ? "40px" : "-40px"}); }
      to   { opacity:1; transform: translateX(0); }
    }

    .zoomIn {
      animation: zoomIn 0.25s cubic-bezier(0.34,1.56,0.64,1) both;
    }
    @keyframes zoomIn {
      from { opacity:0; transform: scale(1.6); }
      to   { opacity:1; transform: scale(1); }
    }

    .dropIn {
      animation: dropIn 0.28s cubic-bezier(0.34,1.56,0.64,1) both;
    }
    @keyframes dropIn {
      from { opacity:0; transform: translateY(-30px) scale(0.9); }
      to   { opacity:1; transform: translateY(0) scale(1); }
    }

    .fadeIn {
      animation: fadeIn 0.30s ease both;
    }
    @keyframes fadeIn {
      from { opacity:0; }
      to   { opacity:1; }
    }
  </style>
</head>
<body>
  <div class="gradient"></div>
  <div class="caption-wrapper">${wordSpans}</div>
</body>
</html>`;
}

// ── Render word-by-word frames for one sentence ───────────────────────────────
async function renderWordFrames(page, sentence, clipIndex) {
  const words        = sentence.split(" ");
  const totalFrames  = Math.ceil(CLIP_DURATION * FPS);

  // How many frames to hold each word state
  // First word appears at frame 3, then one word per ~8 frames
  const holdFrames   = Math.max(6, Math.floor((totalFrames * 0.75) / words.length));
  const frameDir     = `${TMP}/wframes_${clipIndex}`;
  mkdirSync(frameDir, { recursive: true });

  let frameIdx = 0;

  // Load Google Fonts once
  const initHTML = buildWordHTML(sentence, 0);
  const initPath = `${TMP}/init_${clipIndex}.html`;
  writeFileSync(initPath, initHTML, "utf-8");
  await page.goto(`file://${initPath}`, { waitUntil: "load" });
  await page.waitForTimeout(900); // wait for fonts

  // Phase 1: word-by-word appearance
  for (let w = 1; w <= words.length; w++) {
    const html     = buildWordHTML(sentence, w);
    const htmlPath = `${TMP}/w_${clipIndex}_${w}.html`;
    writeFileSync(htmlPath, html, "utf-8");

    await page.goto(`file://${htmlPath}`, { waitUntil: "load" });
    await page.waitForTimeout(120); // let animation play

    const framesForThisWord = w === words.length
      ? Math.max(holdFrames, totalFrames - frameIdx) // last word fills remaining
      : holdFrames;

    for (let f = 0; f < framesForThisWord && frameIdx < totalFrames; f++, frameIdx++) {
      const framePath = `${frameDir}/frame_${String(frameIdx).padStart(6, "0")}.png`;
      await page.screenshot({ path: framePath, type: "png", omitBackground: true });
    }
  }

  // Phase 2: fill remaining frames with full sentence (hold)
  while (frameIdx < totalFrames) {
    const framePath = `${frameDir}/frame_${String(frameIdx).padStart(6, "0")}.png`;
    await page.screenshot({ path: framePath, type: "png", omitBackground: true });
    frameIdx++;
  }

  return frameDir;
}

// ── Convert caption frames → transparent video ────────────────────────────────
function framesToVideo(frameDir, outPath) {
  const result = spawnSync("ffmpeg", [
    "-y",
    "-framerate", String(FPS),
    "-i", `${frameDir}/frame_%06d.png`,
    "-vf", `scale=${WIDTH}:${HEIGHT}`,
    "-c:v", "libvpx-vp9",
    "-pix_fmt", "yuva420p",   // with alpha channel
    "-b:v", "0",
    "-crf", "20",
    "-an",
    outPath,
  ], { stdio: ["ignore", "pipe", "pipe"] });

  if (result.status !== 0) {
    console.error("❌ frames→video error:\n"
      + result.stderr.toString().slice(-1000));
    process.exit(1);
  }
  return outPath;
}

// ── Apply Ken Burns + blue grade + fade ──────────────────────────────────────
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
    // Fallback without Ken Burns
    const fallbackFilter =
      `scale=${WIDTH}:${HEIGHT}:force_original_aspect_ratio=increase,` +
      `crop=${WIDTH}:${HEIGHT},setsar=1,${colorGrade},${fade}`;

    result = spawnSync("ffmpeg", [
      "-y", "-i", videoPath,
      "-t", duration.toFixed(3),
      "-vf", fallbackFilter,
      "-r", String(FPS),
      "-c:v", "libx264", "-preset", "fast", "-crf", "22",
      "-pix_fmt", "yuv420p", "-an",
      outPath,
    ], { stdio: ["ignore", "pipe", "pipe"] });

    if (result.status !== 0) {
      console.error("❌ Both effects failed\n"
        + result.stderr.toString().slice(-600));
      process.exit(1);
    }
  }
  return outPath;
}

// ── Overlay animated caption WebM (with alpha) on video ──────────────────────
function overlayAnimatedCaption(videoPath, captionWebm, outPath) {
  const result = spawnSync("ffmpeg", [
    "-y",
    "-i", videoPath,
    "-i", captionWebm,
    "-filter_complex",
    "[0:v][1:v]overlay=0:0:format=auto[out]",
    "-map", "[out]",
    "-c:v", "libx264",
    "-preset", "fast",
    "-crf", "22",
    "-pix_fmt", "yuv420p",
    "-an",
    outPath,
  ], { stdio: ["ignore", "pipe", "pipe"] });

  if (result.status !== 0) {
    console.error("❌ Overlay error:\n"
      + result.stderr.toString().slice(-1000));
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
    const transition = getTransition(i - 1);
    const outLabel   = i === clipPaths.length - 1 ? "[vout]" : `[v${i}]`;
    filterParts.push(
      `${lastLabel}[${i}:v]xfade=transition=${transition}` +
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
    console.error("⚠️  xfade failed — simple concat\n"
      + result.stderr.toString().slice(-400));
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
    "-i", videoPath, "-i", audioPath,
    "-map", "0:v:0", "-map", "1:a:0",
    "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
    "-shortest", outPath,
  ], { stdio: ["ignore", "pipe", "pipe"] });

  if (result.status !== 0) {
    console.error("❌ Merge error:\n"
      + result.stderr.toString().slice(-800));
    process.exit(1);
  }
}

// ── Main ──────────────────────────────────────────────────────────────────────
console.log("\n🚀 Starting cinematic render...\n");

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

const page           = await context.newPage();
const captionWebms   = [];
const finalClips     = [];

// Step 1: Render word-by-word frames + convert to WebM with alpha
console.log("🖼️  Rendering animated word-by-word captions...");
for (let i = 0; i < sentences.length; i++) {
  const s = sentences[i];
  const words = s.split(" ").length;
  process.stdout.write(
    `  [${i + 1}/${sentences.length}] "${s.slice(0, 50)}" (${words} words)... `
  );

  const frameDir   = await renderWordFrames(page, s, i);
  const webmPath   = `${TMP}/caption_${i}.webm`;
  framesToVideo(frameDir, webmPath);
  captionWebms.push(webmPath);
  process.stdout.write("✓\n");
}
await browser.close();
console.log("✅ Animated captions done\n");

// Step 2: Effects + overlay
console.log("🎬 Applying Ken Burns + blue grade + animated captions...");
for (let i = 0; i < sentences.length; i++) {
  const videoSrc   = videos[i] || videos[videos.length - 1];
  const captionWbm = captionWebms[i];
  const effected   = `${TMP}/effected_${String(i).padStart(3, "0")}.mp4`;
  const final      = `${TMP}/final_clip_${String(i).padStart(3, "0")}.mp4`;

  process.stdout.write(
    `  [${i + 1}/${sentences.length}] ${basename(videoSrc)}... `
  );
  applyEffectsAndColor(videoSrc, CLIP_DURATION, effected, i);
  overlayAnimatedCaption(effected, captionWbm, final);
  finalClips.push(final);
  process.stdout.write("✓\n");
}

// Step 3: Transitions
const transNames = finalClips.slice(0, -1).map((_, i) => getTransition(i));
console.log(`\n✨ Transitions: ${transNames.join(" → ")}`);
const dissolved = xfadeConcat(finalClips);

// Step 4: Audio
console.log("🎵 Merging voiceover...");
mergeAudio(dissolved, audio, outputPath);

console.log(`\n🎉 Final video → ${outputPath}`);
