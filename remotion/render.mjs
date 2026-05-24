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
const {
  sentences, videos, audio,
  duration_s, title,
  word_timeline,   // NEW: [{time, sentence_idx, visible_word_count}]
  aligned,         // NEW: [{sentence, start, end, words}]
} = props;

const FPS    = 30;
const WIDTH  = 1080;
const HEIGHT = 1920;
const TMP    = "/tmp/vsg_render";

mkdirSync(TMP, { recursive: true });

console.log(`📋 Sentences : ${sentences.length}`);
console.log(`⏱️  Duration  : ${duration_s}s`);
console.log(`🎬 Title     : ${title}`);
console.log(`🔤 Timeline  : ${word_timeline?.length || 0} events`);

const TRANSITIONS = [
  "fade","slideleft","slideright","slideup",
  "smoothleft","smoothright","circleopen",
  "radial","pixelize","dissolve",
];
const getTransition = i => TRANSITIONS[i % TRANSITIONS.length];
const isArabic      = t => /[\u0600-\u06FF]/.test(t);

// ── Build frame→state map from word_timeline ──────────────────────────────────
function buildFrameStateMap(timeline, totalFrames) {
  // For each frame, what sentence + visible word count?
  const map = new Array(totalFrames).fill(null).map((_, f) => ({
    sentence_idx:       0,
    visible_word_count: 0,
    time:               f / FPS,
  }));

  for (let f = 0; f < totalFrames; f++) {
    const t = f / FPS;
    // Find last timeline event at or before this time
    let best = null;
    for (const event of timeline) {
      if (event.time <= t + 0.001) {
        best = event;
      } else {
        break;
      }
    }
    if (best) {
      map[f].sentence_idx       = best.sentence_idx;
      map[f].visible_word_count = best.visible_word_count;
    }
  }
  return map;
}

// ── Build PNG HTML for one word state ────────────────────────────────────────
function buildWordPNG(sentence, visibleCount, titleText) {
  const words     = sentence.split(" ");
  const isAr      = isArabic(sentence);
  const isTitleAr = isArabic(titleText);
  const dir       = isAr ? "rtl" : "ltr";
  const lang      = isAr ? "ar" : "en";

  const bodyFont  = isAr
    ? `"Noto Naskh Arabic","Amiri",serif`
    : `"Inter","Helvetica Neue",Arial,sans-serif`;
  const titleFont = isTitleAr
    ? `"Noto Naskh Arabic","Amiri",serif`
    : `"Inter","Helvetica Neue",Arial,sans-serif`;

  const prevWords = words.slice(0, visibleCount - 1).join(" ");
  const currWord  = visibleCount > 0 ? (words[visibleCount - 1] || "") : "";
  const nextWords = words.slice(visibleCount).join(" ");
  const progress  = words.length > 0
    ? ((visibleCount / words.length) * 100).toFixed(1)
    : "0";

  return `<!DOCTYPE html>
<html lang="${lang}">
<head>
  <meta charset="UTF-8"/>
  <link href="https://fonts.googleapis.com/css2?family=Noto+Naskh+Arabic:wght@700&family=Amiri:wght@700&family=Inter:wght@700;800;900&display=swap" rel="stylesheet"/>
  <style>
    *{margin:0;padding:0;box-sizing:border-box;}
    html,body{width:${WIDTH}px;height:${HEIGHT}px;overflow:hidden;background:transparent;}

    .title-wrap{
      position:absolute;
      top:${Math.round(HEIGHT*0.10)}px;
      left:0;right:0;
      display:flex;justify-content:center;
      padding:0 48px;
    }
    .title-pill{
      display:inline-flex;align-items:center;gap:14px;
      background:linear-gradient(135deg,rgba(255,255,255,0.18),rgba(255,255,255,0.08));
      border:1.5px solid rgba(255,255,255,0.40);
      border-radius:60px;padding:18px 48px;
      backdrop-filter:blur(16px);
      box-shadow:0 8px 32px rgba(0,0,0,0.4);
      direction:${isTitleAr?"rtl":"ltr"};
      max-width:920px;
    }
    .title-icon{font-size:32px;flex-shrink:0;}
    .title-text{
      font-family:${titleFont};
      font-size:${isTitleAr?"40px":"36px"};
      font-weight:800;color:#ffffff;
      letter-spacing:${isTitleAr?"0.02em":"-0.02em"};
      text-shadow:0 2px 16px rgba(0,0,0,0.7);
      line-height:1.3;text-align:center;
    }

    .bottom-overlay{
      position:absolute;bottom:0;left:0;right:0;height:60%;
      background:linear-gradient(to top,rgba(0,0,0,0.93) 0%,rgba(0,0,0,0.60) 38%,rgba(0,0,0,0.15) 65%,transparent 100%);
    }

    .word-area{
      position:absolute;
      bottom:${Math.round(HEIGHT*0.14)}px;
      left:0;right:0;
      display:flex;flex-direction:column;align-items:center;
      padding:0 56px;direction:${dir};gap:12px;
    }

    .prev{
      font-family:${bodyFont};
      font-size:${isAr?"50px":"46px"};
      font-weight:700;
      color:rgba(255,255,255,0.45);
      text-align:center;line-height:1.35;
      text-shadow:0 2px 8px rgba(0,0,0,0.9);
      max-width:950px;
      min-height:${isAr?"60px":"56px"};
    }

    .curr{
      font-family:${bodyFont};
      font-size:${isAr?"120px":"114px"};
      font-weight:900;
      color:#FFE600;
      text-align:center;line-height:1.05;
      letter-spacing:${isAr?"0.01em":"-0.03em"};
      text-shadow:
        0 0 35px rgba(255,230,0,0.55),
        0 5px 28px rgba(0,0,0,1),
        3px 3px 0 rgba(0,0,0,0.7),
        -1px -1px 0 rgba(0,0,0,0.5);
      min-height:${isAr?"130px":"120px"};
    }

    .next{
      font-family:${bodyFont};
      font-size:${isAr?"44px":"40px"};
      font-weight:600;
      color:rgba(255,255,255,0.22);
      text-align:center;line-height:1.35;
      max-width:950px;
      min-height:${isAr?"54px":"48px"};
    }

    .progress-wrap{
      position:absolute;
      bottom:58px;left:56px;right:56px;
      height:5px;
      background:rgba(255,255,255,0.15);
      border-radius:3px;overflow:hidden;
    }
    .progress-fill{
      height:100%;width:${progress}%;
      background:linear-gradient(90deg,#FFE600,#FF8C00);
      border-radius:3px;
    }
    .word-count{
      position:absolute;bottom:70px;right:60px;
      font-family:${bodyFont};
      font-size:26px;font-weight:700;
      color:rgba(255,255,255,0.40);
    }
  </style>
</head>
<body>
  <div class="title-wrap">
    <div class="title-pill">
      <span class="title-icon">&#9654;</span>
      <span class="title-text">${titleText}</span>
    </div>
  </div>
  <div class="bottom-overlay"></div>
  <div class="word-area">
    <div class="prev">${prevWords}</div>
    <div class="curr">${currWord}</div>
    <div class="next">${nextWords}</div>
  </div>
  <div class="progress-wrap"><div class="progress-fill"></div></div>
  <div class="word-count">${visibleCount}/${words.length}</div>
</body>
</html>`;
}

// ── Pre-render all unique PNG states ──────────────────────────────────────────
async function renderAllPNGs(page, sentences, title, frameStateMap) {
  // Find all unique (sentence_idx, visible_word_count) combinations
  const uniqueStates = new Map();
  for (const state of frameStateMap) {
    const key = `${state.sentence_idx}_${state.visible_word_count}`;
    if (!uniqueStates.has(key)) {
      uniqueStates.set(key, {
        sentence_idx:       state.sentence_idx,
        visible_word_count: state.visible_word_count,
      });
    }
  }

  console.log(`  📸 Rendering ${uniqueStates.size} unique word states...`);

  // Load fonts once
  const initHTML  = buildWordPNG(sentences[0], 0, title);
  const initPath  = `${TMP}/init.html`;
  writeFileSync(initPath, initHTML, "utf-8");
  await page.goto(`file://${initPath}`, { waitUntil: "load" });
  await page.waitForTimeout(1200);

  const pngCache = new Map();

  let rendered = 0;
  for (const [key, state] of uniqueStates) {
    const sentence = sentences[state.sentence_idx] || "";
    const html     = buildWordPNG(sentence, state.visible_word_count, title);
    const htmlPath = `${TMP}/state_${key}.html`;
    writeFileSync(htmlPath, html, "utf-8");

    await page.goto(`file://${htmlPath}`, { waitUntil: "load" });
    await page.waitForTimeout(40);

    const pngPath = `${TMP}/state_${key}.png`;
    await page.screenshot({
      path: pngPath,
      type: "png",
      omitBackground: true,
    });
    pngCache.set(key, pngPath);
    rendered++;

    if (rendered % 10 === 0) {
      process.stdout.write(`    ${rendered}/${uniqueStates.size}...\n`);
    }
  }

  return pngCache;
}

// ── Build frame directory from state map + PNG cache ─────────────────────────
function buildFrameDir(frameStateMap, pngCache, dirIndex) {
  const frameDir = `${TMP}/frames_${dirIndex}`;
  mkdirSync(frameDir, { recursive: true });

  for (let f = 0; f < frameStateMap.length; f++) {
    const state   = frameStateMap[f];
    const key     = `${state.sentence_idx}_${state.visible_word_count}`;
    const pngPath = pngCache.get(key);
    const dest    = `${frameDir}/frame_${String(f).padStart(6, "0")}.png`;

    if (pngPath) {
      copyFileSync(pngPath, dest);
    }
  }
  return frameDir;
}

// ── Frames → MOV with alpha ───────────────────────────────────────────────────
function framesToMov(frameDir, outPath) {
  const result = spawnSync("ffmpeg", [
    "-y",
    "-framerate", String(FPS),
    "-i", `${frameDir}/frame_%06d.png`,
    "-vf", `scale=${WIDTH}:${HEIGHT},format=rgba`,
    "-c:v", "png",
    "-an",
    outPath,
  ], { stdio: ["ignore", "pipe", "pipe"] });

  if (result.status !== 0) {
    console.error("❌ frames→mov:\n" + result.stderr.toString().slice(-600));
    process.exit(1);
  }
  return outPath;
}

// ── Ken Burns + blue grade + fade ─────────────────────────────────────────────
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

  const kenBurns   = clipIndex % 2 === 0 ? zoomIn : zoomOut;
  const colorGrade =
    `curves=r='0/0 0.5/0.46 1/0.88':g='0/0 0.5/0.50 1/0.97':b='0/0.04 0.5/0.56 1/1.0',` +
    `hue=s=0.82,vignette=PI/5`;
  const fade =
    `fade=t=in:st=0:d=0.35,` +
    `fade=t=out:st=${(duration - 0.35).toFixed(3)}:d=0.35`;

  const fullFilter =
    `scale=${Math.round(WIDTH*1.1)}:${Math.round(HEIGHT*1.1)}:` +
    `force_original_aspect_ratio=increase,` +
    `crop=${Math.round(WIDTH*1.1)}:${Math.round(HEIGHT*1.1)},` +
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
      console.error("❌ BG failed");
      process.exit(1);
    }
  }
  return outPath;
}

// ── Overlay caption MOV on background ────────────────────────────────────────
function overlayOnBackground(bgMp4, captionMov, outPath) {
  const result = spawnSync("ffmpeg", [
    "-y",
    "-i", bgMp4,
    "-i", captionMov,
    "-filter_complex",
    "[1:v]format=rgba[cap];[0:v][cap]overlay=0:0:format=auto,format=yuv420p[out]",
    "-map", "[out]",
    "-c:v", "libx264", "-preset", "fast", "-crf", "20",
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
function xfadeConcat(clipPaths, clipDurations) {
  if (clipPaths.length === 1) return clipPaths[0];

  const XFADE    = 0.4;
  const filters  = [];
  let offset     = 0;
  let lastLabel  = "[0:v]";

  for (let i = 1; i < clipPaths.length; i++) {
    offset += clipDurations[i - 1] - XFADE;
    const outLabel = i === clipPaths.length - 1 ? "[vout]" : `[v${i}]`;
    filters.push(
      `${lastLabel}[${i}:v]xfade=transition=${getTransition(i-1)}` +
      `:duration=${XFADE}:offset=${offset.toFixed(3)}${outLabel}`
    );
    lastLabel = outLabel;
  }

  const outPath = `${TMP}/xfaded.mp4`;
  const result  = spawnSync("ffmpeg", [
    "-y",
    ...clipPaths.flatMap(p => ["-i", p]),
    "-filter_complex", filters.join(";"),
    "-map", "[vout]",
    "-c:v", "libx264", "-preset", "fast", "-crf", "20",
    "-pix_fmt", "yuv420p", "-an",
    outPath,
  ], { stdio: ["ignore", "pipe", "pipe"] });

  if (result.status !== 0) {
    const listFile = `${TMP}/list.txt`;
    writeFileSync(listFile, clipPaths.map(p=>`file '${p}'`).join("\n"));
    const raw = `${TMP}/raw.mp4`;
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
console.log("\n🚀 Synced TikTok render...\n");

// Build frame state map using word_timeline
const totalFrames = Math.ceil(duration_s * FPS);

let frameStateMap;
if (word_timeline && word_timeline.length > 0) {
  console.log("🔄 Building synced frame map from word timestamps...");
  frameStateMap = new Array(totalFrames).fill(null).map((_, f) => ({
    sentence_idx: 0, visible_word_count: 0,
  }));

  for (let f = 0; f < totalFrames; f++) {
    const t    = f / FPS;
    let best   = null;
    for (const event of word_timeline) {
      if (event.time <= t + 0.001) best = event;
      else break;
    }
    if (best) {
      frameStateMap[f] = {
        sentence_idx:       best.sentence_idx,
        visible_word_count: best.visible_word_count,
      };
    }
  }
} else {
  // Fallback: even distribution
  console.log("⚠️  No word timeline — using even distribution");
  const clipDur = duration_s / sentences.length;
  frameStateMap = new Array(totalFrames).fill(null).map((_, f) => {
    const t           = f / FPS;
    const sent_idx    = Math.min(Math.floor(t / clipDur), sentences.length - 1);
    const words       = sentences[sent_idx].split(" ");
    const localT      = t - sent_idx * clipDur;
    const wordIdx     = Math.min(
      Math.floor((localT / clipDur) * words.length),
      words.length - 1
    );
    return { sentence_idx: sent_idx, visible_word_count: wordIdx + 1 };
  });
}

// Render all PNGs
const browser = await chromium.launch({
  headless: true,
  args: [
    "--no-sandbox","--disable-setuid-sandbox",
    "--disable-dev-shm-usage","--disable-gpu",
    "--no-zygote","--font-render-hinting=none","--lang=ar,en",
  ],
});
const context = await browser.newContext({
  viewport: { width: WIDTH, height: HEIGHT },
  deviceScaleFactor: 1,
  locale: "ar-SA",
});
const page = await context.newPage();

console.log("🖼️  Rendering word PNGs...");
const pngCache = await renderAllPNGs(page, sentences, title, frameStateMap);
await browser.close();
console.log(`✅ ${pngCache.size} PNGs rendered\n`);

// Per-sentence processing
const sentenceData = aligned || sentences.map((s, i) => ({
  sentence: s,
  start:    (duration_s / sentences.length) * i,
  end:      (duration_s / sentences.length) * (i + 1),
}));

const finalClips    = [];
const clipDurations = [];

console.log("🎬 Processing clips...");
for (let i = 0; i < sentences.length; i++) {
  const sentInfo  = sentenceData[i] || {};
  const clipStart = sentInfo.start || (duration_s / sentences.length) * i;
  const clipEnd   = sentInfo.end   || (duration_s / sentences.length) * (i + 1);
  const clipDur   = Math.max(clipEnd - clipStart, 0.5);

  const clipFrames    = Math.ceil(clipDur * FPS);
  const clipFrameMap  = frameStateMap.slice(
    Math.floor(clipStart * FPS),
    Math.floor(clipStart * FPS) + clipFrames,
  );

  process.stdout.write(`  [${i+1}/${sentences.length}] ${clipDur.toFixed(2)}s... `);

  // Caption MOV
  const frameDir  = buildFrameDir(clipFrameMap, pngCache, i);
  const captionMov = `${TMP}/caption_${i}.mov`;
  framesToMov(frameDir, captionMov);

  // Background
  const videoSrc = videos[i] || videos[videos.length - 1];
  const bgMp4    = `${TMP}/bg_${String(i).padStart(3,"0")}.mp4`;
  processBackground(videoSrc, clipDur, bgMp4, i);

  // Composite
  const finalClip = `${TMP}/final_${String(i).padStart(3,"0")}.mp4`;
  overlayOnBackground(bgMp4, captionMov, finalClip);
  finalClips.push(finalClip);
  clipDurations.push(clipDur);

  process.stdout.write("✓\n");
}

// Transitions
const transNames = finalClips.slice(0,-1).map((_,i)=>getTransition(i));
console.log(`\n✨ Transitions: ${transNames.join(" → ")}`);
const dissolved = xfadeConcat(finalClips, clipDurations);

// Audio
console.log("🎵 Merging voiceover...");
mergeAudio(dissolved, audio, outputPath);

console.log(`\n🎉 Final synced video → ${outputPath}`);
