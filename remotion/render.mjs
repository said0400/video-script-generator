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
  sentences,
  videos,
  audio,
  duration_s,
  title,
  word_timeline,
  aligned,
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

// ── Transitions ───────────────────────────────────────────────────────────────
const TRANSITIONS = [
  "fade", "slideleft", "slideright", "slideup",
  "smoothleft", "smoothright", "circleopen",
  "radial", "pixelize", "dissolve",
];
const getTransition = i => TRANSITIONS[i % TRANSITIONS.length];
const isArabic      = t => /[\u0600-\u06FF]/.test(t);

// ── Auto emoji based on title ─────────────────────────────────────────────────
function getEmojis(titleText) {
  const t = titleText.toLowerCase();
  if (t.includes("fast")    || t.includes("صيام"))   return ["⚡", "💪"];
  if (t.includes("health")  || t.includes("صح"))     return ["🌿", "💚"];
  if (t.includes("money")   || t.includes("مال"))    return ["💰", "🚀"];
  if (t.includes("mind")    || t.includes("عقل"))    return ["🧠", "⚡"];
  if (t.includes("sleep")   || t.includes("نوم"))    return ["😴", "🌙"];
  if (t.includes("success") || t.includes("نجاح"))   return ["🏆", "🔥"];
  if (t.includes("food")    || t.includes("طعام"))   return ["🥗", "💪"];
  if (t.includes("fit")     || t.includes("رياض"))   return ["🏋️", "🔥"];
  if (t.includes("skin")    || t.includes("بشر"))    return ["✨", "🌸"];
  if (t.includes("life")    || t.includes("حياة"))   return ["🌟", "💫"];
  if (t.includes("work")    || t.includes("عمل"))    return ["💼", "🚀"];
  if (t.includes("learn")   || t.includes("تعلم"))   return ["📚", "🧠"];
  if (t.includes("water")   || t.includes("ماء"))    return ["💧", "✨"];
  if (t.includes("power")   || t.includes("قوة"))    return ["⚡", "🔥"];
  return ["🎯", "✨"];
}

// ── Build frame state map ─────────────────────────────────────────────────────
function buildFrameStateMap(timeline, totalFrames) {
  const map = new Array(totalFrames).fill(null).map(() => ({
    sentence_idx:       0,
    visible_word_count: 0,
  }));

  if (!timeline || timeline.length === 0) return map;

  for (let f = 0; f < totalFrames; f++) {
    const t = f / FPS;
    let best = null;
    for (const event of timeline) {
      if (event.time <= t + 0.001) best = event;
      else break;
    }
    if (best) {
      map[f] = {
        sentence_idx:       best.sentence_idx,
        visible_word_count: best.visible_word_count,
      };
    }
  }
  return map;
}

// ── Build word PNG HTML ───────────────────────────────────────────────────────
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

  const [emoji1, emoji2] = getEmojis(titleText);

  const titleFontSize = isTitleAr ? "42px" : "38px";
  const titleLS       = isTitleAr ? "0.02em" : "-0.02em";
  const prevFS        = isAr ? "48px" : "44px";
  const prevMinH      = isAr ? "58px" : "54px";
  const currFS        = isAr ? "128px" : "122px";
  const currMinH      = isAr ? "138px" : "128px";
  const currLS        = isAr ? "0.01em" : "-0.04em";
  const nextFS        = isAr ? "42px" : "38px";
  const nextMinH      = isAr ? "52px" : "46px";
  const titleDir      = isTitleAr ? "rtl" : "ltr";

  const wordAreaTop   = Math.round(HEIGHT * 0.50);
  const titleTop      = Math.round(HEIGHT * 0.09);

  return `<!DOCTYPE html>
<html lang="${lang}">
<head>
  <meta charset="UTF-8"/>
  <link href="https://fonts.googleapis.com/css2?family=Noto+Naskh+Arabic:wght@700&family=Amiri:wght@700&family=Inter:wght@700;800;900&display=swap" rel="stylesheet"/>
  <style>
    * { margin:0; padding:0; box-sizing:border-box; }
    html, body {
      width: ${WIDTH}px;
      height: ${HEIGHT}px;
      overflow: hidden;
      background: transparent;
    }

    /* ── TITLE ── */
    .title-wrap {
      position: absolute;
      top: ${titleTop}px;
      left: 0; right: 0;
      display: flex;
      justify-content: center;
      padding: 0 44px;
    }
    .title-card {
      display: inline-flex;
      align-items: center;
      gap: 16px;
      direction: ${titleDir};
      background: linear-gradient(
        135deg,
        rgba(255,255,255,0.22) 0%,
        rgba(255,255,255,0.08) 100%
      );
      border: 1.5px solid rgba(255,255,255,0.45);
      border-radius: 24px;
      padding: 20px 40px;
      backdrop-filter: blur(20px);
      -webkit-backdrop-filter: blur(20px);
      box-shadow:
        0 8px 32px rgba(0,0,0,0.45),
        0 2px 8px rgba(0,0,0,0.3),
        inset 0 1px 0 rgba(255,255,255,0.25);
      max-width: 960px;
    }
    .emoji {
      font-size: 48px;
      line-height: 1;
      flex-shrink: 0;
      filter: drop-shadow(0 2px 8px rgba(0,0,0,0.5));
    }
    .title-text {
      font-family: ${titleFont};
      font-size: ${titleFontSize};
      font-weight: 800;
      color: #ffffff;
      line-height: 1.25;
      letter-spacing: ${titleLS};
      text-align: center;
      text-shadow:
        0 2px 20px rgba(0,0,0,0.8),
        0 1px 3px rgba(0,0,0,0.6);
    }

    /* ── BOTTOM OVERLAY ── */
    .bottom-overlay {
      position: absolute;
      bottom: 0; left: 0; right: 0;
      height: 68%;
      background: linear-gradient(
        to top,
        rgba(0,0,0,0.96)  0%,
        rgba(0,0,0,0.75) 28%,
        rgba(0,0,0,0.30) 55%,
        transparent      100%
      );
    }

    /* ── WORD AREA — starts at 50% from top ── */
    .word-area {
      position: absolute;
      top: ${wordAreaTop}px;
      left: 0; right: 0;
      display: flex;
      flex-direction: column;
      align-items: center;
      padding: 0 52px;
      direction: ${dir};
      gap: 10px;
    }

    .prev {
      font-family: ${bodyFont};
      font-size: ${prevFS};
      font-weight: 700;
      color: rgba(255,255,255,0.42);
      text-align: center;
      line-height: 1.35;
      text-shadow: 0 2px 8px rgba(0,0,0,0.9);
      max-width: 960px;
      min-height: ${prevMinH};
      word-break: break-word;
    }

    .curr {
      font-family: ${bodyFont};
      font-size: ${currFS};
      font-weight: 900;
      color: #FFE600;
      text-align: center;
      line-height: 1.0;
      letter-spacing: ${currLS};
      text-shadow:
        0 0  40px rgba(255,230,0,0.60),
        0 6px 30px rgba(0,0,0,1),
        4px 4px 0 rgba(0,0,0,0.75),
        -2px -2px 0 rgba(0,0,0,0.5);
      min-height: ${currMinH};
      word-break: break-word;
    }

    .next {
      font-family: ${bodyFont};
      font-size: ${nextFS};
      font-weight: 600;
      color: rgba(255,255,255,0.20);
      text-align: center;
      line-height: 1.35;
      max-width: 960px;
      min-height: ${nextMinH};
      word-break: break-word;
    }

    /* ── PROGRESS BAR ── */
    .progress-wrap {
      position: absolute;
      bottom: 72px;
      left: 60px; right: 60px;
      height: 6px;
      background: rgba(255,255,255,0.15);
      border-radius: 3px;
      overflow: hidden;
    }
    .progress-fill {
      height: 100%;
      width: ${progress}%;
      background: linear-gradient(90deg, #FFE600, #FF8C00);
      border-radius: 3px;
    }
    .progress-label {
      position: absolute;
      bottom: 84px;
      right: 62px;
      font-family: ${bodyFont};
      font-size: 24px;
      font-weight: 700;
      color: rgba(255,255,255,0.38);
      letter-spacing: 0.05em;
    }
  </style>
</head>
<body>
  <div class="title-wrap">
    <div class="title-card">
      <span class="emoji">${emoji1}</span>
      <span class="title-text">${titleText}</span>
      <span class="emoji">${emoji2}</span>
    </div>
  </div>

  <div class="bottom-overlay"></div>

  <div class="word-area">
    <div class="prev">${prevWords}</div>
    <div class="curr">${currWord}</div>
    <div class="next">${nextWords}</div>
  </div>

  <div class="progress-wrap">
    <div class="progress-fill"></div>
  </div>
  <div class="progress-label">${visibleCount}/${words.length}</div>
</body>
</html>`;
}

// ── Render all unique PNG states ──────────────────────────────────────────────
async function renderAllPNGs(page, sentences, title, frameStateMap) {
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
  const initHTML = buildWordPNG(sentences[0] || "", 0, title);
  const initPath = `${TMP}/init.html`;
  writeFileSync(initPath, initHTML, "utf-8");
  await page.goto(`file://${initPath}`, { waitUntil: "load" });
  await page.waitForTimeout(1200);

  const pngCache = new Map();
  let rendered   = 0;

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

    if (rendered % 10 === 0 || rendered === uniqueStates.size) {
      process.stdout.write(`    ${rendered}/${uniqueStates.size} PNGs\n`);
    }
  }

  return pngCache;
}

// ── Build frame directory from state map ─────────────────────────────────────
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

  const kenBurns = clipIndex % 2 === 0 ? zoomIn : zoomOut;

  const colorGrade =
    `curves=r='0/0 0.5/0.46 1/0.88':` +
    `g='0/0 0.5/0.50 1/0.97':` +
    `b='0/0.04 0.5/0.56 1/1.0',` +
    `hue=s=0.82,vignette=PI/5`;

  const fadeDur = 0.35;
  const fade =
    `fade=t=in:st=0:d=${fadeDur},` +
    `fade=t=out:st=${(duration - fadeDur).toFixed(3)}:d=${fadeDur}`;

  const fullFilter =
    `scale=${Math.round(WIDTH * 1.1)}:${Math.round(HEIGHT * 1.1)}:` +
    `force_original_aspect_ratio=increase,` +
    `crop=${Math.round(WIDTH * 1.1)}:${Math.round(HEIGHT * 1.1)},` +
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

  const XFADE   = 0.4;
  const filters = [];
  let offset    = 0;
  let lastLabel = "[0:v]";

  for (let i = 1; i < clipPaths.length; i++) {
    offset += clipDurations[i - 1] - XFADE;
    const outLabel = i === clipPaths.length - 1 ? "[vout]" : `[v${i}]`;
    filters.push(
      `${lastLabel}[${i}:v]xfade=transition=${getTransition(i - 1)}` +
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
    console.error("⚠️  xfade failed — simple concat\n"
      + result.stderr.toString().slice(-400));
    const listFile = `${TMP}/list.txt`;
    writeFileSync(listFile, clipPaths.map(p => `file '${p}'`).join("\n"));
    const raw = `${TMP}/raw.mp4`;
    spawnSync("ffmpeg", [
      "-y", "-f", "concat", "-safe", "0",
      "-i", listFile, "-c", "copy", raw,
    ], { stdio: "inherit" });
    return raw;
  }
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
    "-c:a", "aac", "-b:a", "192k",
    "-shortest",
    outPath,
  ], { stdio: ["ignore", "pipe", "pipe"] });

  if (result.status !== 0) {
    console.error("❌ Audio merge:\n" + result.stderr.toString().slice(-500));
    process.exit(1);
  }
}

// ── Main ──────────────────────────────────────────────────────────────────────
console.log("\n🚀 Starting TikTok cinematic render...\n");

const totalFrames = Math.ceil(duration_s * FPS);

// Build frame state map
let frameStateMap;
if (word_timeline && word_timeline.length > 0) {
  console.log("🔄 Building synced frame map...");
  frameStateMap = buildFrameStateMap(word_timeline, totalFrames);
} else {
  console.log("⚠️  No timeline — even distribution fallback");
  const clipDur = duration_s / sentences.length;
  frameStateMap = new Array(totalFrames).fill(null).map((_, f) => {
    const t        = f / FPS;
    const sIdx     = Math.min(Math.floor(t / clipDur), sentences.length - 1);
    const words    = sentences[sIdx].split(" ");
    const localT   = t - sIdx * clipDur;
    const wordIdx  = Math.min(
      Math.floor((localT / clipDur) * words.length),
      words.length - 1
    );
    return { sentence_idx: sIdx, visible_word_count: wordIdx + 1 };
  });
}

// Render PNGs
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

const page = await context.newPage();

console.log("🖼️  Rendering word PNGs...");
const pngCache = await renderAllPNGs(page, sentences, title, frameStateMap);
await browser.close();
console.log(`✅ ${pngCache.size} PNGs done\n`);

// Per-sentence processing
const sentenceData = (aligned && aligned.length > 0)
  ? aligned
  : sentences.map((s, i) => ({
      sentence: s,
      start:    (duration_s / sentences.length) * i,
      end:      (duration_s / sentences.length) * (i + 1),
    }));

const finalClips    = [];
const clipDurations = [];

console.log("🎬 Processing clips...");
for (let i = 0; i < sentences.length; i++) {
  const sentInfo  = sentenceData[i] || {};
  const clipStart = sentInfo.start ?? (duration_s / sentences.length) * i;
  const clipEnd   = sentInfo.end   ?? (duration_s / sentences.length) * (i + 1);
  const clipDur   = Math.max(clipEnd - clipStart, 0.5);
  const clipFrames = Math.ceil(clipDur * FPS);

  const startFrame = Math.floor(clipStart * FPS);
  const clipFrameMap = frameStateMap.slice(startFrame, startFrame + clipFrames);

  process.stdout.write(
    `  [${i + 1}/${sentences.length}] ${clipDur.toFixed(2)}s `
    + `"${sentences[i].slice(0, 40)}"... `
  );

  // Caption MOV
  const frameDir   = buildFrameDir(clipFrameMap, pngCache, i);
  const captionMov = `${TMP}/caption_${i}.mov`;
  framesToMov(frameDir, captionMov);

  // Background
  const videoSrc = videos[i] || videos[videos.length - 1];
  const bgMp4    = `${TMP}/bg_${String(i).padStart(3, "0")}.mp4`;
  processBackground(videoSrc, clipDur, bgMp4, i);

  // Composite
  const finalClip = `${TMP}/final_${String(i).padStart(3, "0")}.mp4`;
  overlayOnBackground(bgMp4, captionMov, finalClip);
  finalClips.push(finalClip);
  clipDurations.push(clipDur);

  process.stdout.write("✓\n");
}

// Transitions
const transNames = finalClips.slice(0, -1).map((_, i) => getTransition(i));
console.log(`\n✨ Transitions: ${transNames.join(" → ")}`);
const dissolved = xfadeConcat(finalClips, clipDurations);

// Merge audio
console.log("🎵 Merging voiceover...");
mergeAudio(dissolved, audio, outputPath);

console.log(`\n🎉 Final video → ${outputPath}`);
