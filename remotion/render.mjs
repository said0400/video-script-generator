import { readFileSync, writeFileSync, mkdirSync, copyFileSync, existsSync } from "fs";
import { spawnSync } from "child_process";
import { chromium } from "playwright";

const manifestPath = process.argv[2];
const outputPath   = process.argv[3];

if (!manifestPath || !outputPath) {
  console.error("Usage: node render.mjs <manifest.json> <output.mp4>");
  process.exit(1);
}

const props = JSON.parse(readFileSync(manifestPath, "utf-8"));
const { sentences, videos, audio, duration_s, title, word_timeline, aligned } = props;

const FPS    = 30;
const WIDTH  = 1080;
const HEIGHT = 1920;
const TMP    = "/tmp/vsg_render";

mkdirSync(TMP, { recursive: true });

// ── Audio duration ────────────────────────────────────────────────────────────
function probeDuration(filePath) {
  const r = spawnSync("ffprobe", [
    "-v", "error", "-show_entries", "format=duration",
    "-of", "default=noprint_wrappers=1:nokey=1", filePath,
  ], { stdio: ["ignore", "pipe", "pipe"] });
  return parseFloat(r.stdout.toString().trim()) || 0;
}

const realAudioDuration = probeDuration(audio);
const effectiveDuration = realAudioDuration > 5 ? realAudioDuration : duration_s;
const totalFrames       = Math.ceil(effectiveDuration * FPS);

console.log(`📋 Sentences      : ${sentences.length}`);
console.log(`🎵 Audio duration : ${realAudioDuration.toFixed(3)}s`);
console.log(`⏱️  Effective dur  : ${effectiveDuration.toFixed(3)}s`);
console.log(`🎞️  Total frames   : ${totalFrames}`);
console.log(`🔤 Timeline events: ${word_timeline?.length || 0}`);
console.log(`🎬 Title          : ${title}`);

// ── Visual config ─────────────────────────────────────────────────────────────
const ACCENT_COLORS   = ["#FFE600", "#FF3366", "#00F5FF", "#FF6B35", "#7FFF00", "#FF1493"];
const CAPTION_STYLES  = ["big_word", "subtitle_bar", "top_reveal", "split_color", "center_white", "side_accent"];
const TRANSITIONS     = ["fade","slideleft","slideright","slideup","smoothleft","smoothright","circleopen","radial","pixelize","dissolve"];

const getTransition   = i => TRANSITIONS[i % TRANSITIONS.length];
const isArabic        = t => /[\u0600-\u06FF]/.test(t);

function getSentenceStyle(idx) {
  return {
    name:   CAPTION_STYLES[idx % CAPTION_STYLES.length],
    accent: ACCENT_COLORS[idx % ACCENT_COLORS.length],
  };
}

// ── Emoji mapping ─────────────────────────────────────────────────────────────
function getEmojis(t) {
  t = (t || "").toLowerCase();
  if (t.includes("success") || t.includes("نجاح"))  return ["🏆", "🔥"];
  if (t.includes("mind")    || t.includes("عقل"))   return ["🧠", "⚡"];
  if (t.includes("money")   || t.includes("مال"))   return ["💰", "🚀"];
  if (t.includes("health")  || t.includes("صح"))    return ["🌿", "💚"];
  if (t.includes("sleep")   || t.includes("نوم"))   return ["😴", "🌙"];
  if (t.includes("power")   || t.includes("قوة"))   return ["⚡", "🔥"];
  if (t.includes("life")    || t.includes("حياة"))  return ["🌟", "💫"];
  if (t.includes("work")    || t.includes("عمل"))   return ["💼", "🚀"];
  if (t.includes("fit")     || t.includes("رياض"))  return ["🏋️", "🔥"];
  if (t.includes("love")    || t.includes("حب"))    return ["💔", "❤️"];
  if (t.includes("fear")    || t.includes("خوف"))   return ["😰", "🌑"];
  if (t.includes("secret")  || t.includes("سر"))    return ["🤫", "👁️"];
  return ["🎯", "✨"];
}

// ── Build HTML for one word state ─────────────────────────────────────────────
function buildWordHTML(sentence, visibleCount, titleText, sentenceIdx, totalSentences) {
  sentence = (sentence || " ").trim() || " ";

  const words     = sentence.split(" ").filter(Boolean);
  const isAr      = isArabic(sentence);
  const isTitleAr = isArabic(titleText);
  const dir       = isAr ? "rtl" : "ltr";
  const lang      = isAr ? "ar" : "en";
  const { name: styleName, accent } = getSentenceStyle(sentenceIdx);

  const bodyFont  = isAr
    ? `"Noto Naskh Arabic","Amiri",serif`
    : `"Inter","Helvetica Neue",Arial,sans-serif`;
  const titleFont = isTitleAr
    ? `"Noto Naskh Arabic","Amiri",serif`
    : `"Inter","Helvetica Neue",Arial,sans-serif`;

  const vc        = Math.max(0, Math.min(visibleCount, words.length));
  const prevWords = words.slice(0, vc - 1).join(" ");
  const currWord  = vc > 0 ? (words[vc - 1] || "") : "";
  const nextWords = words.slice(vc).join(" ");
  const progress  = words.length > 0 ? ((vc / words.length) * 100).toFixed(1) : "0";

  const isFirstWord    = vc === 1;
  const isLastSentence = sentenceIdx === totalSentences - 1;
  const isLastWord     = words.length > 0 && vc >= words.length;

  const [emoji1, emoji2] = getEmojis(titleText);

  // ── 6 caption styles ─────────────────────────────────────────────────────────
  let captionCSS = "", captionHTML = "";

  if (styleName === "big_word") {
    captionCSS = `
      .wa{position:absolute;top:${Math.round(HEIGHT*0.50)}px;left:0;right:0;
          display:flex;flex-direction:column;align-items:center;
          padding:0 52px;direction:${dir};gap:12px;}
      .prev{font-family:${bodyFont};font-size:${isAr?"48px":"44px"};font-weight:700;
            color:rgba(255,255,255,0.45);text-align:center;line-height:1.35;
            text-shadow:0 2px 8px rgba(0,0,0,0.9);max-width:960px;min-height:54px;word-break:break-word;}
      .curr{font-family:${bodyFont};font-size:${isAr?"122px":"114px"};font-weight:900;
            color:${accent};text-align:center;line-height:1.0;
            letter-spacing:${isAr?"0.01em":"-0.04em"};
            text-shadow:0 0 50px ${accent}88,0 6px 30px rgba(0,0,0,1),4px 4px 0 rgba(0,0,0,0.75);
            min-height:128px;word-break:break-word;}
      .next{font-family:${bodyFont};font-size:${isAr?"40px":"36px"};font-weight:600;
            color:rgba(255,255,255,0.22);text-align:center;line-height:1.35;
            max-width:960px;word-break:break-word;}`;
    captionHTML = `
      <div class="wa">
        <div class="prev">${prevWords}</div>
        <div class="curr">${currWord}</div>
        <div class="next">${nextWords}</div>
      </div>`;

  } else if (styleName === "subtitle_bar") {
    captionCSS = `
      .sb{position:absolute;bottom:0;left:0;right:0;
          background:rgba(0,0,0,0.92);border-top:5px solid ${accent};
          padding:24px 52px 80px;direction:${dir};}
      .sb-prev{font-family:${bodyFont};font-size:${isAr?"34px":"30px"};font-weight:600;
               color:rgba(255,255,255,0.42);line-height:1.3;margin-bottom:8px;}
      .sb-row{display:flex;align-items:center;gap:0;direction:${dir};}
      .sb-bar{flex-shrink:0;width:7px;height:64px;background:${accent};border-radius:4px;
              ${isAr?"margin-left:20px":"margin-right:20px"};}
      .sb-text{font-family:${bodyFont};font-size:${isAr?"54px":"50px"};font-weight:800;
               color:#fff;line-height:1.2;text-shadow:0 2px 8px rgba(0,0,0,0.9);}
      .sb-curr{color:${accent};}
      .sb-next{color:rgba(255,255,255,0.28);}`;
    captionHTML = `
      <div class="sb">
        ${prevWords ? `<div class="sb-prev">${prevWords}</div>` : ""}
        <div class="sb-row">
          <div class="sb-bar"></div>
          <div class="sb-text">
            <span class="sb-curr">${currWord}</span>
            ${nextWords ? `<span class="sb-next"> ${nextWords}</span>` : ""}
          </div>
        </div>
      </div>`;

  } else if (styleName === "top_reveal") {
    captionCSS = `
      .tr{position:absolute;top:185px;left:44px;right:44px;
          background:linear-gradient(135deg,rgba(0,0,0,0.88),${accent}18);
          border:2px solid ${accent}55;border-radius:24px;padding:28px 48px;
          direction:${dir};box-shadow:0 8px 40px rgba(0,0,0,0.65),inset 0 1px 0 rgba(255,255,255,0.08);}
      .tr-prev{font-family:${bodyFont};font-size:${isAr?"38px":"34px"};font-weight:700;
               color:rgba(255,255,255,0.38);line-height:1.3;margin-bottom:6px;text-align:center;}
      .tr-curr{font-family:${bodyFont};font-size:${isAr?"72px":"66px"};font-weight:900;
               color:${accent};line-height:1.15;text-align:center;
               text-shadow:0 0 30px ${accent}66,0 2px 12px rgba(0,0,0,0.9);}
      .tr-next{font-family:${bodyFont};font-size:${isAr?"36px":"32px"};font-weight:600;
               color:rgba(255,255,255,0.20);line-height:1.3;margin-top:6px;text-align:center;}`;
    captionHTML = `
      <div class="tr">
        ${prevWords ? `<div class="tr-prev">${prevWords}</div>` : ""}
        <div class="tr-curr">${currWord}</div>
        ${nextWords ? `<div class="tr-next">${nextWords}</div>` : ""}
      </div>`;

  } else if (styleName === "split_color") {
    captionCSS = `
      .sc-outer{position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);
                background:${accent};padding:32px 56px;max-width:940px;width:88%;
                direction:${dir};box-shadow:0 0 80px ${accent}55,0 20px 60px rgba(0,0,0,0.7);}
      .sc-prev{font-family:${bodyFont};font-size:${isAr?"44px":"40px"};font-weight:700;
               color:rgba(0,0,0,0.55);text-align:center;line-height:1.2;margin-bottom:4px;}
      .sc-curr{font-family:${bodyFont};font-size:${isAr?"80px":"74px"};font-weight:900;
               color:#000;text-align:center;line-height:1.1;}
      .sc-next{font-family:${bodyFont};font-size:${isAr?"40px":"36px"};font-weight:600;
               color:rgba(0,0,0,0.45);text-align:center;line-height:1.2;margin-top:4px;}`;
    captionHTML = `
      <div class="sc-outer">
        ${prevWords ? `<div class="sc-prev">${prevWords}</div>` : ""}
        <div class="sc-curr">${currWord}</div>
        ${nextWords ? `<div class="sc-next">${nextWords}</div>` : ""}
      </div>`;

  } else if (styleName === "center_white") {
    captionCSS = `
      .cw{position:absolute;bottom:160px;left:0;right:0;
          display:flex;flex-direction:column;align-items:center;
          padding:0 52px;direction:${dir};gap:14px;}
      .cw-prev{font-family:${bodyFont};font-size:${isAr?"44px":"40px"};font-weight:700;
               color:rgba(255,255,255,0.38);text-align:center;line-height:1.3;
               text-shadow:0 2px 8px rgba(0,0,0,0.95);max-width:960px;}
      .cw-curr{font-family:${bodyFont};font-size:${isAr?"98px":"92px"};font-weight:900;
               color:#fff;text-align:center;line-height:1.05;
               text-shadow:0 0 50px rgba(255,255,255,0.45),0 4px 20px rgba(0,0,0,1),
                           3px 3px 0 ${accent},5px 5px 0 rgba(0,0,0,0.45);
               max-width:980px;word-break:break-word;}
      .cw-next{font-family:${bodyFont};font-size:${isAr?"38px":"34px"};font-weight:600;
               color:rgba(255,255,255,0.20);text-align:center;max-width:960px;}`;
    captionHTML = `
      <div class="cw">
        <div class="cw-prev">${prevWords}</div>
        <div class="cw-curr">${currWord}</div>
        <div class="cw-next">${nextWords}</div>
      </div>`;

  } else { // side_accent
    const flexDir   = isAr ? "row-reverse" : "row";
    const barMargin = isAr ? "margin-left:22px" : "margin-right:22px";
    const textPad   = isAr ? "padding-left:52px" : "padding-right:52px";
    captionCSS = `
      .sa{position:absolute;bottom:140px;left:44px;right:44px;
          display:flex;align-items:flex-start;flex-direction:${flexDir};}
      .sa-bar{width:8px;min-height:180px;flex-shrink:0;
              background:linear-gradient(to bottom,${accent},${accent}33);
              border-radius:4px;${barMargin};}
      .sa-text{flex:1;direction:${dir};${textPad};}
      .sa-prev{font-family:${bodyFont};font-size:${isAr?"42px":"38px"};font-weight:700;
               color:rgba(255,255,255,0.40);line-height:1.3;margin-bottom:8px;
               text-shadow:0 2px 8px rgba(0,0,0,0.9);}
      .sa-curr{font-family:${bodyFont};font-size:${isAr?"86px":"80px"};font-weight:900;
               color:${accent};line-height:1.1;margin-bottom:8px;
               text-shadow:0 0 40px ${accent}66,0 4px 20px rgba(0,0,0,1);word-break:break-word;}
      .sa-next{font-family:${bodyFont};font-size:${isAr?"36px":"32px"};font-weight:600;
               color:rgba(255,255,255,0.20);line-height:1.3;}`;
    captionHTML = `
      <div class="sa">
        <div class="sa-bar"></div>
        <div class="sa-text">
          <div class="sa-prev">${prevWords}</div>
          <div class="sa-curr">${currWord}</div>
          <div class="sa-next">${nextWords}</div>
        </div>
      </div>`;
  }

  // ── Pattern interrupt flash (first word of each sentence) ─────────────────
  const flashHTML = isFirstWord
    ? `<div style="position:absolute;inset:0;background:${accent};opacity:0.20;mix-blend-mode:overlay;pointer-events:none;"></div>`
    : "";

  // ── Engagement overlay (last word of last sentence) ───────────────────────
  const saveLabel = isTitleAr ? "احفظ الفيديو 🔖" : "Save This 🔖";
  const engHTML   = (isLastSentence && isLastWord) ? `
    <div style="position:absolute;inset:0;display:flex;justify-content:center;align-items:center;pointer-events:none;">
      <div style="background:${accent};border-radius:60px;padding:24px 56px;
                  display:flex;align-items:center;gap:20px;
                  box-shadow:0 0 60px ${accent}99,0 10px 40px rgba(0,0,0,0.7);">
        <span style="font-family:${isTitleAr?`"Noto Naskh Arabic",serif`:`"Inter",sans-serif`};
                     font-size:50px;font-weight:900;color:#000;white-space:nowrap;">
          ${saveLabel}
        </span>
      </div>
    </div>` : "";

  // ── Sentence progress dots ────────────────────────────────────────────────
  const maxDots  = Math.min(totalSentences, 9);
  const dotStart = Math.max(0, sentenceIdx - 4);
  const dotsHTML = `
    <div style="position:absolute;bottom:66px;left:0;right:0;
                display:flex;justify-content:center;align-items:center;gap:9px;">
      ${Array.from({length: maxDots}, (_, k) => {
        const idx = dotStart + k;
        if (idx >= totalSentences) return "";
        return idx === sentenceIdx
          ? `<div style="width:24px;height:7px;border-radius:4px;background:${accent};"></div>`
          : `<div style="width:7px;height:7px;border-radius:50%;background:rgba(255,255,255,0.22);"></div>`;
      }).join("")}
    </div>`;

  // ── Progress bar ──────────────────────────────────────────────────────────
  const progressHTML = `
    <div style="position:absolute;bottom:44px;left:56px;right:56px;
                height:4px;background:rgba(255,255,255,0.12);border-radius:2px;overflow:hidden;">
      <div style="height:100%;width:${progress}%;
                  background:linear-gradient(90deg,${accent},${accent}66);border-radius:2px;"></div>
    </div>`;

  // ── Title card ────────────────────────────────────────────────────────────
  const titleTop = Math.round(HEIGHT * 0.055);
  const titleHTML = `
    <div style="position:absolute;top:${titleTop}px;left:0;right:0;
                display:flex;justify-content:center;padding:0 44px;">
      <div style="display:inline-flex;align-items:center;gap:14px;
                  direction:${isTitleAr?"rtl":"ltr"};
                  background:linear-gradient(135deg,rgba(255,255,255,0.16),rgba(255,255,255,0.04));
                  border:1.5px solid rgba(255,255,255,0.28);border-radius:18px;
                  padding:14px 28px;backdrop-filter:blur(14px);
                  box-shadow:0 4px 20px rgba(0,0,0,0.5);max-width:960px;">
        <span style="font-size:36px;line-height:1;">${emoji1}</span>
        <span style="font-family:${titleFont};font-size:${isTitleAr?"35px":"31px"};
                     font-weight:800;color:#fff;line-height:1.2;text-align:center;
                     text-shadow:0 2px 14px rgba(0,0,0,0.8);">${titleText}</span>
        <span style="font-size:36px;line-height:1;">${emoji2}</span>
      </div>
    </div>`;

  // ── Bottom gradient overlay ───────────────────────────────────────────────
  const overlayGradient = styleName === "split_color"
    ? "linear-gradient(to top,rgba(0,0,0,0.55) 0%,transparent 35%)"
    : "linear-gradient(to top,rgba(0,0,0,0.95) 0%,rgba(0,0,0,0.72) 28%,rgba(0,0,0,0.22) 55%,transparent 100%)";

  return `<!DOCTYPE html>
<html lang="${lang}">
<head>
  <meta charset="UTF-8"/>
  <link href="https://fonts.googleapis.com/css2?family=Noto+Naskh+Arabic:wght@700;800&family=Amiri:wght@700&family=Inter:wght@700;800;900&display=swap" rel="stylesheet"/>
  <style>
    *{margin:0;padding:0;box-sizing:border-box;}
    html,body{width:${WIDTH}px;height:${HEIGHT}px;overflow:hidden;background:transparent;}
    ${captionCSS}
  </style>
</head>
<body>
  ${flashHTML}
  ${titleHTML}
  <div style="position:absolute;bottom:0;left:0;right:0;height:65%;background:${overlayGradient};pointer-events:none;"></div>
  ${captionHTML}
  ${dotsHTML}
  ${progressHTML}
  ${engHTML}
</body>
</html>`;
}

// ── Frame state map ───────────────────────────────────────────────────────────
function buildFrameStateMap(timeline, nFrames, realDur) {
  const map = new Array(nFrames).fill(null).map(() => ({ sentence_idx: 0, visible_word_count: 0 }));
  if (!timeline || timeline.length === 0) return map;

  const timelineMax = timeline[timeline.length - 1].time;
  const scale       = timelineMax > 0.1 ? (realDur / timelineMax) : 1.0;
  if (Math.abs(scale - 1.0) > 0.01) console.log(`  📐 Timeline scale: ${scale.toFixed(4)}x`);

  const scaled = timeline.map(ev => ({ ...ev, time: ev.time * scale }));

  for (let f = 0; f < nFrames; f++) {
    const t = f / FPS;
    let lo = 0, hi = scaled.length - 1, best = null;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      if (scaled[mid].time <= t + 0.001) { best = scaled[mid]; lo = mid + 1; }
      else hi = mid - 1;
    }
    if (best) map[f] = { sentence_idx: best.sentence_idx, visible_word_count: best.visible_word_count };
  }
  return map;
}

// ── Render unique PNG states ──────────────────────────────────────────────────
async function renderAllPNGs(page, frameStateMap) {
  const uniqueStates = new Map();
  for (const state of frameStateMap) {
    const key = `${state.sentence_idx}_${state.visible_word_count}`;
    if (!uniqueStates.has(key)) uniqueStates.set(key, state);
  }
  console.log(`  📸 ${uniqueStates.size} unique word states`);

  // Warm-up — loads Google Fonts once
  const initHtml = buildWordHTML(sentences[0] || " ", 0, title, 0, sentences.length);
  writeFileSync(`${TMP}/init.html`, initHtml, "utf-8");
  await page.goto(`file://${TMP}/init.html`, { waitUntil: "load" });
  await page.waitForTimeout(1600);

  const pngCache = new Map();
  let   rendered = 0;

  for (const [key, state] of uniqueStates) {
    const sentence = sentences[state.sentence_idx] || " ";
    const html     = buildWordHTML(sentence, state.visible_word_count, title, state.sentence_idx, sentences.length);
    const htmlPath = `${TMP}/state_${key}.html`;

    writeFileSync(htmlPath, html, "utf-8");
    await page.goto(`file://${htmlPath}`, { waitUntil: "load" });
    await page.waitForTimeout(50);

    const pngPath = `${TMP}/state_${key}.png`;
    await page.screenshot({ path: pngPath, type: "png", omitBackground: true });
    pngCache.set(key, pngPath);
    rendered++;

    if (rendered % 20 === 0 || rendered === uniqueStates.size)
      process.stdout.write(`    ${rendered}/${uniqueStates.size} PNGs\n`);
  }
  return pngCache;
}

// ── Build frame dir ───────────────────────────────────────────────────────────
function buildFrameDir(clipFrameMap, pngCache, idx) {
  const dir = `${TMP}/frames_${idx}`;
  mkdirSync(dir, { recursive: true });
  for (let f = 0; f < clipFrameMap.length; f++) {
    const state = clipFrameMap[f];
    const key   = `${state.sentence_idx}_${state.visible_word_count}`;
    const src   = pngCache.get(key);
    const dest  = `${dir}/frame_${String(f).padStart(6, "0")}.png`;
    if (src) copyFileSync(src, dest);
  }
  return dir;
}

// ── PNG frames → MOV with alpha ───────────────────────────────────────────────
function framesToMov(frameDir, outPath) {
  const r = spawnSync("ffmpeg", [
    "-y", "-framerate", String(FPS),
    "-i", `${frameDir}/frame_%06d.png`,
    "-vf", `scale=${WIDTH}:${HEIGHT},format=rgba`,
    "-c:v", "png", "-an", outPath,
  ], { stdio: ["ignore", "pipe", "pipe"] });

  if (r.status !== 0) {
    console.error("❌ frames→mov:\n" + r.stderr.toString().slice(-400));
    process.exit(1);
  }
  return outPath;
}

// ── Ken Burns + color grade + fade ────────────────────────────────────────────
function processBackground(videoPath, duration, outPath, idx) {
  const n       = Math.ceil(duration * FPS);
  const zoomIn  = `zoompan=z='min(zoom+0.0004,1.09)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=${n}:s=${WIDTH}x${HEIGHT}:fps=${FPS}`;
  const zoomOut = `zoompan=z='if(eq(on\\,1)\\,1.09\\,max(zoom-0.0004\\,1.0))':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=${n}:s=${WIDTH}x${HEIGHT}:fps=${FPS}`;
  const kb      = idx % 2 === 0 ? zoomIn : zoomOut;
  const color   = `curves=r='0/0 0.5/0.46 1/0.88':g='0/0 0.5/0.50 1/0.97':b='0/0.04 0.5/0.56 1/1.0',hue=s=0.82,vignette=PI/5`;
  const fade    = `fade=t=in:st=0:d=0.30,fade=t=out:st=${(duration - 0.30).toFixed(3)}:d=0.30`;
  const full    = `scale=${Math.round(WIDTH*1.1)}:${Math.round(HEIGHT*1.1)}:force_original_aspect_ratio=increase,`
                + `crop=${Math.round(WIDTH*1.1)}:${Math.round(HEIGHT*1.1)},${kb},${color},${fade}`;

  let r = spawnSync("ffmpeg", [
    "-y", "-i", videoPath, "-t", duration.toFixed(3),
    "-vf", full, "-r", String(FPS),
    "-c:v", "libx264", "-preset", "fast", "-crf", "22", "-pix_fmt", "yuv420p", "-an", outPath,
  ], { stdio: ["ignore", "pipe", "pipe"] });

  if (r.status !== 0) {
    const simple = `scale=${WIDTH}:${HEIGHT}:force_original_aspect_ratio=increase,`
                 + `crop=${WIDTH}:${HEIGHT},setsar=1,${color},${fade}`;
    r = spawnSync("ffmpeg", [
      "-y", "-i", videoPath, "-t", duration.toFixed(3),
      "-vf", simple, "-r", String(FPS),
      "-c:v", "libx264", "-preset", "fast", "-crf", "22", "-pix_fmt", "yuv420p", "-an", outPath,
    ], { stdio: ["ignore", "pipe", "pipe"] });

    if (r.status !== 0) {
      console.error("❌ BG failed:\n" + r.stderr.toString().slice(-300));
      process.exit(1);
    }
  }
  return outPath;
}

// ── Overlay caption on background ─────────────────────────────────────────────
function overlayOnBackground(bgMp4, captionMov, outPath) {
  const r = spawnSync("ffmpeg", [
    "-y", "-i", bgMp4, "-i", captionMov,
    "-filter_complex", "[1:v]format=rgba[cap];[0:v][cap]overlay=0:0:format=auto,format=yuv420p[out]",
    "-map", "[out]",
    "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p", "-an", outPath,
  ], { stdio: ["ignore", "pipe", "pipe"] });

  if (r.status !== 0) {
    console.error("❌ Overlay:\n" + r.stderr.toString().slice(-500));
    process.exit(1);
  }
  return outPath;
}

// ── xfade concat ──────────────────────────────────────────────────────────────
function xfadeConcat(clipPaths, clipDurations) {
  if (clipPaths.length === 1) return clipPaths[0];

  const XFADE = 0.35;
  const filters = [];
  let offset = 0, last = "[0:v]";

  for (let i = 1; i < clipPaths.length; i++) {
    offset += clipDurations[i - 1] - XFADE;
    const out = i === clipPaths.length - 1 ? "[vout]" : `[v${i}]`;
    filters.push(`${last}[${i}:v]xfade=transition=${getTransition(i-1)}:duration=${XFADE}:offset=${offset.toFixed(3)}${out}`);
    last = out;
  }

  const outPath = `${TMP}/xfaded.mp4`;
  const r = spawnSync("ffmpeg", [
    "-y", ...clipPaths.flatMap(p => ["-i", p]),
    "-filter_complex", filters.join(";"),
    "-map", "[vout]",
    "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p", "-an", outPath,
  ], { stdio: ["ignore", "pipe", "pipe"] });

  if (r.status !== 0) {
    // Fallback: simple concat
    const listFile = `${TMP}/list.txt`;
    writeFileSync(listFile, clipPaths.map(p => `file '${p}'`).join("\n"));
    const raw = `${TMP}/raw.mp4`;
    spawnSync("ffmpeg", ["-y", "-f", "concat", "-safe", "0", "-i", listFile, "-c", "copy", raw],
      { stdio: "inherit" });
    return raw;
  }
  return outPath;
}

// ── Merge audio ────────────────────────────────────────────────────────────────
function mergeAudio(videoPath, audioPath, outPath) {
  const audioDur = probeDuration(audioPath);
  const videoDur = probeDuration(videoPath);
  console.log(`🎵 Audio: ${audioDur.toFixed(3)}s | 🎬 Video: ${videoDur.toFixed(3)}s`);

  let finalVideo = videoPath;

  if (videoDur < audioDur - 0.3) {
    console.log(`⚠️  Extending video by ${(audioDur - videoDur).toFixed(2)}s`);
    const extended = `${TMP}/video_extended.mp4`;
    let r = spawnSync("ffmpeg", [
      "-y", "-i", videoPath,
      "-vf", `tpad=stop_mode=clone:stop_duration=${(audioDur - videoDur + 0.5).toFixed(3)}`,
      "-c:v", "libx264", "-preset", "fast", "-crf", "22", "-pix_fmt", "yuv420p", "-an", extended,
    ], { stdio: ["ignore", "pipe", "pipe"] });

    if (r.status === 0) {
      finalVideo = extended;
      console.log(`✅ Extended to ${probeDuration(extended).toFixed(2)}s`);
    } else {
      const looped = `${TMP}/video_looped.mp4`;
      spawnSync("ffmpeg", ["-y", "-stream_loop", "-1", "-i", videoPath,
        "-t", audioDur.toFixed(3), "-c", "copy", looped],
        { stdio: ["ignore", "pipe", "pipe"] });
      if (existsSync(looped)) finalVideo = looped;
    }
  }

  const r = spawnSync("ffmpeg", [
    "-y", "-i", finalVideo, "-i", audioPath,
    "-map", "0:v:0", "-map", "1:a:0",
    "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
    "-t", audioDur.toFixed(3), outPath,
  ], { stdio: ["ignore", "pipe", "pipe"] });

  if (r.status !== 0) {
    console.error("❌ Merge:\n" + r.stderr.toString().slice(-400));
    process.exit(1);
  }
  console.log(`✅ Final: ${audioDur.toFixed(3)}s → ${outPath}`);
}

// ── MAIN ──────────────────────────────────────────────────────────────────────
console.log("\n🚀 Starting render...\n");

const frameStateMap = (word_timeline && word_timeline.length > 0)
  ? buildFrameStateMap(word_timeline, totalFrames, effectiveDuration)
  : (() => {
      console.log("⚠️  No timeline — even distribution");
      const clipDur = effectiveDuration / sentences.length;
      return new Array(totalFrames).fill(null).map((_, f) => {
        const t      = f / FPS;
        const sIdx   = Math.min(Math.floor(t / clipDur), sentences.length - 1);
        const ws     = (sentences[sIdx] || "").split(" ");
        const localT = t - sIdx * clipDur;
        const wIdx   = Math.min(Math.floor((localT / clipDur) * ws.length), ws.length - 1);
        return { sentence_idx: sIdx, visible_word_count: wIdx + 1 };
      });
    })();

const browser = await chromium.launch({
  headless: true,
  args: ["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage",
         "--disable-gpu", "--no-zygote", "--font-render-hinting=none", "--lang=ar,en"],
});
const context = await browser.newContext({
  viewport: { width: WIDTH, height: HEIGHT },
  deviceScaleFactor: 1,
  locale: "ar-SA",
});
const page = await context.newPage();

console.log("🖼️  Rendering word PNGs...");
const pngCache = await renderAllPNGs(page, frameStateMap);
await browser.close();
console.log(`✅ ${pngCache.size} PNGs done\n`);

const sentenceData = (aligned && aligned.length > 0)
  ? aligned
  : sentences.map((s, i) => ({
      sentence: s,
      start:    (effectiveDuration / sentences.length) * i,
      end:      (effectiveDuration / sentences.length) * (i + 1),
    }));

const finalClips = [], clipDurations = [];

console.log("🎬 Processing clips...");
for (let i = 0; i < sentences.length; i++) {
  const info      = sentenceData[i] || {};
  const clipStart = info.start ?? (effectiveDuration / sentences.length) * i;
  const clipEnd   = info.end   ?? (effectiveDuration / sentences.length) * (i + 1);
  const clipDur   = Math.max(clipEnd - clipStart, 0.5);
  const nFrames   = Math.ceil(clipDur * FPS);
  const startF    = Math.floor(clipStart * FPS);
  const clipMap   = frameStateMap.slice(startF, startF + nFrames);

  process.stdout.write(`  [${i+1}/${sentences.length}] ${clipDur.toFixed(2)}s "${(sentences[i]||"").slice(0,32)}"... `);

  const frameDir   = buildFrameDir(clipMap, pngCache, i);
  const captionMov = `${TMP}/caption_${i}.mov`;
  framesToMov(frameDir, captionMov);

  const videoSrc = videos[i] || videos[videos.length - 1];
  const bgMp4    = `${TMP}/bg_${String(i).padStart(3, "0")}.mp4`;
  processBackground(videoSrc, clipDur, bgMp4, i);

  const finalClip = `${TMP}/final_${String(i).padStart(3, "0")}.mp4`;
  overlayOnBackground(bgMp4, captionMov, finalClip);
  finalClips.push(finalClip);
  clipDurations.push(clipDur);
  process.stdout.write("✓\n");
}

const transNames = finalClips.slice(0, -1).map((_, i) => getTransition(i));
console.log(`\n✨ Transitions: ${transNames.join(" → ")}`);
const dissolved = xfadeConcat(finalClips, clipDurations);

console.log("🎵 Merging voiceover...");
mergeAudio(dissolved, audio, outputPath);
console.log(`\n🎉 Final video → ${outputPath}`);
