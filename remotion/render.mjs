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

// ── CRITICAL FIX: Unique TMP per render job ───────────────────────────────────
// Prevents EN + AR parallel renders from overwriting each other's temp files
const safeOut = outputPath.replace(/[^a-zA-Z0-9]/g, "_").replace(/_+/g, "_").slice(-22);
const TMP     = `/tmp/vsg_${safeOut}`;
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

console.log(`📋 Sentences   : ${sentences.length}`);
console.log(`🎵 Audio       : ${realAudioDuration.toFixed(3)}s`);
console.log(`⏱️  Effective   : ${effectiveDuration.toFixed(3)}s`);
console.log(`🎞️  Frames      : ${totalFrames}`);
console.log(`📁 TMP         : ${TMP}`);

// ── Visual config ─────────────────────────────────────────────────────────────
const ACCENT_COLORS  = ["#FFE600","#FF3366","#00F5FF","#FF6B35","#7FFF00","#FF1493"];
const CAPTION_STYLES = ["word_pop","karaoke","impact","minimal_cap","stacked","neon_word"];
const TRANSITIONS    = ["fade","slideleft","slideright","slideup","smoothleft","smoothright",
                        "circleopen","radial","pixelize","dissolve"];

const getTransition = i => TRANSITIONS[i % TRANSITIONS.length];
const isArabic      = t => /[\u0600-\u06FF]/.test(t);
const esc           = s => (s || "").toString()
  .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

function getSentenceStyle(idx) {
  return { name: CAPTION_STYLES[idx % CAPTION_STYLES.length], accent: ACCENT_COLORS[idx % ACCENT_COLORS.length] };
}

function getEmojis(t) {
  t = (t || "").toLowerCase();
  if (t.includes("success") || t.includes("نجاح"))  return ["🏆","🔥"];
  if (t.includes("mind")    || t.includes("عقل"))   return ["🧠","⚡"];
  if (t.includes("money")   || t.includes("مال"))   return ["💰","🚀"];
  if (t.includes("health")  || t.includes("صح"))    return ["🌿","💚"];
  if (t.includes("sleep")   || t.includes("نوم"))   return ["😴","🌙"];
  if (t.includes("power")   || t.includes("قوة"))   return ["⚡","🔥"];
  if (t.includes("life")    || t.includes("حياة"))  return ["🌟","💫"];
  if (t.includes("work")    || t.includes("عمل"))   return ["💼","🚀"];
  if (t.includes("fit")     || t.includes("رياض"))  return ["🏋️","🔥"];
  if (t.includes("love")    || t.includes("حب"))    return ["💔","❤️"];
  if (t.includes("fear")    || t.includes("خوف"))   return ["😰","🌑"];
  if (t.includes("secret")  || t.includes("سر"))    return ["🤫","👁️"];
  return ["🎯","✨"];
}

// ── HTML builder: 6 modern styles, ALL text in safe zone y=820–1520 ──────────
function buildWordHTML(sentence, visibleCount, titleText, sentenceIdx, totalSentences) {
  sentence = (sentence || " ").trim() || " ";

  const words = sentence.split(" ").filter(Boolean);
  const isAr  = isArabic(sentence);
  const isTAr = isArabic(titleText);
  const dir   = isAr ? "rtl" : "ltr";
  const lang  = isAr ? "ar" : "en";
  const { name: styleName, accent } = getSentenceStyle(sentenceIdx);

  const bFont = isAr ? `"Noto Naskh Arabic","Amiri",serif` : `"Inter","Helvetica Neue",Arial,sans-serif`;
  const tFont = isTAr ? `"Noto Naskh Arabic","Amiri",serif` : `"Inter","Helvetica Neue",Arial,sans-serif`;

  const vc    = Math.max(0, Math.min(visibleCount, words.length));
  const prev  = esc(words.slice(0, vc - 1).join(" "));
  const curr  = esc(vc > 0 ? (words[vc - 1] || "") : "");
  const next  = esc(words.slice(vc).join(" "));
  const pct   = words.length > 0 ? ((vc / words.length) * 100).toFixed(1) : "0";

  const isFirst = vc === 1;
  const isLast  = sentenceIdx === totalSentences - 1;
  const isDone  = words.length > 0 && vc >= words.length;

  const [e1, e2] = getEmojis(titleText);

  // Safe zone anchor: y=980 is the center of our text area (820–1140)
  const Y = 980;

  let css = "", html = "";

  // ── 0: word_pop — Classic TikTok word-by-word ────────────────────────────
  if (styleName === "word_pop") {
    css = `
      .wp{position:absolute;top:${Y-200}px;left:0;right:0;
          display:flex;flex-direction:column;align-items:center;
          padding:0 56px;direction:${dir};gap:6px;}
      .wp-p{font-family:${bFont};font-size:${isAr?"44px":"40px"};font-weight:700;
            color:rgba(255,255,255,0.40);text-align:center;line-height:1.35;
            text-shadow:0 2px 12px rgba(0,0,0,0.95);max-width:960px;
            min-height:52px;word-break:break-word;}
      .wp-c{font-family:${bFont};font-size:${isAr?"112px":"106px"};font-weight:900;
            color:${accent};text-align:center;line-height:1.0;
            letter-spacing:${isAr?"0.01em":"-0.03em"};
            text-shadow:0 0 50px ${accent}99,0 6px 32px rgba(0,0,0,1),
                        5px 5px 0 rgba(0,0,0,0.85),-3px -3px 0 rgba(0,0,0,0.7);
            min-height:116px;word-break:break-word;}
      .wp-n{font-family:${bFont};font-size:${isAr?"36px":"32px"};font-weight:600;
            color:rgba(255,255,255,0.18);text-align:center;line-height:1.3;
            max-width:960px;word-break:break-word;}`;
    html = `<div class="wp">
      <div class="wp-p">${prev}&nbsp;</div>
      <div class="wp-c">${curr}</div>
      <div class="wp-n">&nbsp;${next}</div>
    </div>`;

  // ── 1: karaoke — Full sentence, current word highlighted ─────────────────
  } else if (styleName === "karaoke") {
    const kWords = words.map((w, i) => {
      const we = esc(w);
      if (i === vc - 1)
        return `<span style="background:${accent};color:#000;padding:3px 14px;border-radius:10px;display:inline-block;line-height:1.2;margin:0 3px;">${we}</span>`;
      if (i < vc - 1)
        return `<span style="color:rgba(255,255,255,0.52);margin:0 3px;">${we}</span>`;
      return `<span style="color:rgba(255,255,255,0.16);margin:0 3px;">${we}</span>`;
    }).join("");
    css = `
      .kk{position:absolute;top:1200px;left:0;right:0;padding:0 48px;direction:${dir};}
      .kk-t{font-family:${bFont};font-size:${isAr?"50px":"46px"};font-weight:800;
            text-align:center;line-height:1.6;
            text-shadow:0 2px 12px rgba(0,0,0,0.95);word-break:break-word;}`;
    html = `<div class="kk"><div class="kk-t">${kWords}</div></div>`;

  // ── 2: impact — Single massive word, black stroke ─────────────────────────
  } else if (styleName === "impact") {
    css = `
      .im{position:absolute;top:${Y-150}px;left:0;right:0;
          display:flex;flex-direction:column;align-items:center;padding:0 36px;}
      .im-p{font-family:${bFont};font-size:${isAr?"34px":"30px"};font-weight:700;
            color:rgba(255,255,255,0.30);text-align:center;margin-bottom:10px;
            text-shadow:0 2px 8px rgba(0,0,0,0.9);}
      .im-c{font-family:${bFont};font-size:${isAr?"124px":"116px"};font-weight:900;
            color:#fff;text-align:center;line-height:0.95;
            letter-spacing:${isAr?"0.01em":"-0.04em"};
            -webkit-text-stroke:${isAr?"5px":"6px"} rgba(0,0,0,0.95);
            paint-order:stroke fill;
            text-shadow:0 6px 30px rgba(0,0,0,0.95);
            word-break:break-word;max-width:980px;}
      .im-n{font-family:${bFont};font-size:${isAr?"32px":"28px"};font-weight:600;
            color:rgba(255,255,255,0.16);text-align:center;margin-top:10px;
            text-shadow:0 2px 8px rgba(0,0,0,0.9);}`;
    html = `<div class="im">
      <div class="im-p">${prev}</div>
      <div class="im-c">${curr}</div>
      <div class="im-n">${next}</div>
    </div>`;

  // ── 3: minimal_cap — Clean bar, accent current word ───────────────────────
  } else if (styleName === "minimal_cap") {
    css = `
      .mc{position:absolute;top:1240px;left:0;right:0;direction:${dir};}
      .mc-p{font-family:${bFont};font-size:${isAr?"34px":"30px"};font-weight:600;
            color:rgba(255,255,255,0.36);text-align:center;padding:0 60px;
            margin-bottom:10px;text-shadow:0 2px 8px rgba(0,0,0,0.9);}
      .mc-bar{background:rgba(0,0,0,0.75);padding:20px 52px;
              border-${isAr?"right":"left"}:5px solid ${accent};
              display:flex;align-items:baseline;flex-wrap:wrap;
              gap:0;${isAr?"justify-content:flex-end;direction:rtl;":""}}
      .mc-c{font-family:${bFont};font-size:${isAr?"60px":"56px"};font-weight:900;
            color:${accent};line-height:1.2;
            text-shadow:0 0 20px ${accent}55,0 2px 8px rgba(0,0,0,0.8);}
      .mc-r{font-family:${bFont};font-size:${isAr?"46px":"42px"};font-weight:700;
            color:rgba(255,255,255,0.28);line-height:1.2;
            ${isAr?"margin-right:10px":"margin-left:10px"};}`;
    html = `<div class="mc">
      ${prev ? `<div class="mc-p">${prev}</div>` : ""}
      <div class="mc-bar">
        ${isAr
          ? `<span class="mc-r">${next}</span><span class="mc-c"> ${curr}</span>`
          : `<span class="mc-c">${curr}</span><span class="mc-r"> ${next}</span>`
        }
      </div>
    </div>`;

  // ── 4: stacked — Three stacked rows with glow on current ─────────────────
  } else if (styleName === "stacked") {
    css = `
      .st{position:absolute;top:${Y-210}px;left:0;right:0;
          display:flex;flex-direction:column;align-items:center;
          padding:0 52px;direction:${dir};gap:4px;}
      .st-p{font-family:${bFont};font-size:${isAr?"40px":"36px"};font-weight:700;
            color:rgba(255,255,255,0.32);text-align:center;line-height:1.35;
            text-shadow:0 2px 10px rgba(0,0,0,0.95);min-height:48px;
            max-width:960px;word-break:break-word;}
      .st-c{font-family:${bFont};font-size:${isAr?"104px":"98px"};font-weight:900;
            color:#fff;text-align:center;line-height:1.05;
            text-shadow:0 0 40px ${accent}bb,0 0 80px ${accent}55,
                        0 4px 24px rgba(0,0,0,1),4px 4px 0 rgba(0,0,0,0.85);
            min-height:108px;max-width:980px;word-break:break-word;}
      .st-n{font-family:${bFont};font-size:${isAr?"36px":"32px"};font-weight:600;
            color:rgba(255,255,255,0.16);text-align:center;line-height:1.35;
            min-height:44px;max-width:960px;word-break:break-word;}`;
    html = `<div class="st">
      <div class="st-p">${prev}&nbsp;</div>
      <div class="st-c">${curr}</div>
      <div class="st-n">&nbsp;${next}</div>
    </div>`;

  // ── 5: neon_word — White text with neon glow halo ─────────────────────────
  } else {
    css = `
      .nw{position:absolute;top:${Y-195}px;left:0;right:0;
          display:flex;flex-direction:column;align-items:center;
          padding:0 56px;direction:${dir};gap:8px;}
      .nw-p{font-family:${bFont};font-size:${isAr?"38px":"34px"};font-weight:700;
            color:rgba(255,255,255,0.26);text-align:center;line-height:1.3;
            max-width:960px;min-height:46px;word-break:break-word;}
      .nw-c{font-family:${bFont};font-size:${isAr?"110px":"104px"};font-weight:900;
            color:#fff;text-align:center;line-height:1.0;
            letter-spacing:${isAr?"0.01em":"-0.03em"};
            text-shadow:0 0 25px ${accent},0 0 50px ${accent}aa,
                        0 0 90px ${accent}44,0 4px 22px rgba(0,0,0,1),
                        4px 4px 0 rgba(0,0,0,0.9);
            min-height:114px;word-break:break-word;}
      .nw-n{font-family:${bFont};font-size:${isAr?"34px":"30px"};font-weight:600;
            color:rgba(255,255,255,0.12);text-align:center;line-height:1.3;
            max-width:960px;word-break:break-word;}`;
    html = `<div class="nw">
      <div class="nw-p">${prev}&nbsp;</div>
      <div class="nw-c">${curr}</div>
      <div class="nw-n">&nbsp;${next}</div>
    </div>`;
  }

  // ── Pattern interrupt flash (first word only) ─────────────────────────────
  const flashDiv = isFirst
    ? `<div style="position:absolute;inset:0;background:${accent};opacity:0.18;mix-blend-mode:overlay;pointer-events:none;"></div>`
    : "";

  // ── Engagement overlay (last sentence, last word) ─────────────────────────
  const saveLabel = isTAr ? "احفظ الفيديو 🔖" : "Save This 🔖";
  const engDiv    = (isLast && isDone) ? `
    <div style="position:absolute;inset:0;display:flex;justify-content:center;align-items:center;pointer-events:none;">
      <div style="background:${accent};border-radius:60px;padding:22px 52px;
                  display:flex;align-items:center;
                  box-shadow:0 0 60px ${accent}aa,0 10px 40px rgba(0,0,0,0.7);">
        <span style="font-family:${tFont};font-size:48px;font-weight:900;
                     color:#000;white-space:nowrap;">${saveLabel}</span>
      </div>
    </div>` : "";

  // ── Sentence progress dots ────────────────────────────────────────────────
  const MAX_D   = 9;
  const half    = Math.floor(MAX_D / 2);
  let   dStart  = Math.max(0, sentenceIdx - half);
  dStart        = Math.min(dStart, Math.max(0, totalSentences - MAX_D));
  const dEnd    = Math.min(totalSentences, dStart + MAX_D);
  const dotsDiv = `
    <div style="position:absolute;bottom:68px;left:0;right:0;
                display:flex;justify-content:center;align-items:center;gap:9px;">
      ${Array.from({length: dEnd - dStart}, (_, k) => {
        const i = dStart + k;
        return i === sentenceIdx
          ? `<div style="width:22px;height:7px;border-radius:4px;background:${accent};"></div>`
          : `<div style="width:7px;height:7px;border-radius:50%;background:rgba(255,255,255,0.20);"></div>`;
      }).join("")}
    </div>`;

  // ── Progress bar ──────────────────────────────────────────────────────────
  const progDiv = `
    <div style="position:absolute;bottom:44px;left:56px;right:56px;
                height:4px;background:rgba(255,255,255,0.12);border-radius:2px;overflow:hidden;">
      <div style="height:100%;width:${pct}%;
                  background:linear-gradient(90deg,${accent},${accent}66);border-radius:2px;"></div>
    </div>`;

  // ── Modern minimal title badge ────────────────────────────────────────────
  const titleDiv = `
    <div style="position:absolute;top:60px;left:0;right:0;
                display:flex;justify-content:center;padding:0 50px;">
      <div style="display:inline-flex;align-items:center;gap:10px;
                  background:rgba(0,0,0,0.62);border-radius:40px;
                  padding:10px 22px;border:1.5px solid rgba(255,255,255,0.18);
                  max-width:880px;">
        <span style="font-size:28px;line-height:1;">${e1}</span>
        <span style="font-family:${tFont};font-size:${isTAr?"25px":"23px"};font-weight:800;
                     color:rgba(255,255,255,0.90);line-height:1.2;
                     direction:${isTAr?"rtl":"ltr"};
                     text-shadow:0 1px 6px rgba(0,0,0,0.8);">${esc(titleText)}</span>
        <span style="font-size:28px;line-height:1;">${e2}</span>
      </div>
    </div>`;

  // ── Extended bottom overlay — covers center text area ─────────────────────
  const overlayDiv = `
    <div style="position:absolute;bottom:0;left:0;right:0;height:78%;
                background:linear-gradient(to top,
                  rgba(0,0,0,0.97) 0%,
                  rgba(0,0,0,0.80) 20%,
                  rgba(0,0,0,0.50) 38%,
                  rgba(0,0,0,0.20) 55%,
                  transparent 72%
                );pointer-events:none;"></div>`;

  return `<!DOCTYPE html>
<html lang="${lang}">
<head>
  <meta charset="UTF-8"/>
  <link href="https://fonts.googleapis.com/css2?family=Noto+Naskh+Arabic:wght@700;800&family=Amiri:wght@700&family=Inter:wght@700;800;900&display=swap" rel="stylesheet"/>
  <style>
    *{margin:0;padding:0;box-sizing:border-box;}
    html,body{width:${WIDTH}px;height:${HEIGHT}px;overflow:hidden;background:transparent;}
    ${css}
  </style>
</head>
<body>
  ${flashDiv}
  ${titleDiv}
  ${overlayDiv}
  ${html}
  ${dotsDiv}
  ${progDiv}
  ${engDiv}
</body>
</html>`;
}

// ── Frame state map ────────────────────────────────────────────────────────────
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

// ── Render unique PNG states ───────────────────────────────────────────────────
async function renderAllPNGs(page, frameStateMap) {
  const uniqueStates = new Map();
  for (const state of frameStateMap) {
    const key = `${state.sentence_idx}_${state.visible_word_count}`;
    if (!uniqueStates.has(key)) uniqueStates.set(key, state);
  }
  console.log(`  📸 ${uniqueStates.size} unique word states`);

  const initHtml = buildWordHTML(sentences[0] || " ", 0, title, 0, sentences.length);
  writeFileSync(`${TMP}/init.html`, initHtml, "utf-8");
  await page.goto(`file://${TMP}/init.html`, { waitUntil: "load" });
  await page.waitForTimeout(1600);

  const pngCache = new Map();
  let rendered = 0;

  for (const [key, state] of uniqueStates) {
    const sentence = sentences[state.sentence_idx] || " ";
    const html     = buildWordHTML(sentence, state.visible_word_count, title, state.sentence_idx, sentences.length);
    const htmlPath = `${TMP}/s_${key}.html`;
    writeFileSync(htmlPath, html, "utf-8");
    await page.goto(`file://${htmlPath}`, { waitUntil: "load" });
    await page.waitForTimeout(50);
    const pngPath = `${TMP}/s_${key}.png`;
    await page.screenshot({ path: pngPath, type: "png", omitBackground: true });
    pngCache.set(key, pngPath);
    rendered++;
    if (rendered % 20 === 0 || rendered === uniqueStates.size)
      process.stdout.write(`    ${rendered}/${uniqueStates.size} PNGs\n`);
  }
  return pngCache;
}

// ── Build frame dir ────────────────────────────────────────────────────────────
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

// ── PNG frames → MOV with alpha ────────────────────────────────────────────────
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
  const fade    = `fade=t=in:st=0:d=0.28,fade=t=out:st=${(duration - 0.28).toFixed(3)}:d=0.28`;
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
  const XFADE = 0.30;
  const filters = [];
  let offset = 0, last = "[0:v]";
  for (let i = 1; i < clipPaths.length; i++) {
    offset += clipDurations[i-1] - XFADE;
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
    const lst = `${TMP}/list.txt`;
    writeFileSync(lst, clipPaths.map(p => `file '${p}'`).join("\n"));
    const raw = `${TMP}/raw.mp4`;
    spawnSync("ffmpeg", ["-y","-f","concat","-safe","0","-i",lst,"-c","copy",raw], { stdio: "inherit" });
    return raw;
  }
  return outPath;
}

// ── Merge audio ────────────────────────────────────────────────────────────────
function mergeAudio(videoPath, audioPath, outPath) {
  const aDur = probeDuration(audioPath);
  const vDur = probeDuration(videoPath);
  console.log(`🎵 Audio: ${aDur.toFixed(3)}s | 🎬 Video: ${vDur.toFixed(3)}s`);

  let finalVideo = videoPath;
  if (vDur < aDur - 0.3) {
    console.log(`⚠️  Extending video by ${(aDur - vDur).toFixed(2)}s`);
    const ext = `${TMP}/video_ext.mp4`;
    let r = spawnSync("ffmpeg", [
      "-y","-i",videoPath,
      "-vf",`tpad=stop_mode=clone:stop_duration=${(aDur-vDur+0.5).toFixed(3)}`,
      "-c:v","libx264","-preset","fast","-crf","22","-pix_fmt","yuv420p","-an",ext,
    ], { stdio:["ignore","pipe","pipe"] });
    if (r.status === 0) {
      finalVideo = ext;
    } else {
      const lp = `${TMP}/video_loop.mp4`;
      spawnSync("ffmpeg",["-y","-stream_loop","-1","-i",videoPath,"-t",aDur.toFixed(3),"-c","copy",lp],{stdio:["ignore","pipe","pipe"]});
      if (existsSync(lp)) finalVideo = lp;
    }
  }

  const r = spawnSync("ffmpeg", [
    "-y","-i",finalVideo,"-i",audioPath,
    "-map","0:v:0","-map","1:a:0",
    "-c:v","copy","-c:a","aac","-b:a","192k",
    "-t",aDur.toFixed(3),outPath,
  ], { stdio:["ignore","pipe","pipe"] });
  if (r.status !== 0) {
    console.error("❌ Merge:\n" + r.stderr.toString().slice(-400));
    process.exit(1);
  }
  console.log(`✅ Final: ${aDur.toFixed(3)}s → ${outPath}`);
}

// ── MAIN ──────────────────────────────────────────────────────────────────────
console.log("\n🚀 Starting render...\n");

const frameStateMap = (word_timeline && word_timeline.length > 0)
  ? buildFrameStateMap(word_timeline, totalFrames, effectiveDuration)
  : (() => {
      console.log("⚠️  No timeline — even distribution");
      const cd = effectiveDuration / sentences.length;
      return new Array(totalFrames).fill(null).map((_, f) => {
        const t    = f / FPS;
        const si   = Math.min(Math.floor(t / cd), sentences.length - 1);
        const ws   = (sentences[si] || "").split(" ");
        const lt   = t - si * cd;
        const wi   = Math.min(Math.floor((lt / cd) * ws.length), ws.length - 1);
        return { sentence_idx: si, visible_word_count: wi + 1 };
      });
    })();

const browser = await chromium.launch({
  headless: true,
  args: ["--no-sandbox","--disable-setuid-sandbox","--disable-dev-shm-usage",
         "--disable-gpu","--no-zygote","--font-render-hinting=none","--lang=ar,en"],
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

  process.stdout.write(`  [${i+1}/${sentences.length}] ${clipDur.toFixed(2)}s "${(sentences[i]||"").slice(0,30)}"... `);

  const frameDir   = buildFrameDir(clipMap, pngCache, i);
  const captionMov = `${TMP}/caption_${i}.mov`;
  framesToMov(frameDir, captionMov);

  const videoSrc = videos[i] || videos[videos.length - 1];
  const bgMp4    = `${TMP}/bg_${String(i).padStart(3,"0")}.mp4`;
  processBackground(videoSrc, clipDur, bgMp4, i);

  const finalClip = `${TMP}/final_${String(i).padStart(3,"0")}.mp4`;
  overlayOnBackground(bgMp4, captionMov, finalClip);
  finalClips.push(finalClip);
  clipDurations.push(clipDur);
  process.stdout.write("✓\n");
}

const transNames = finalClips.slice(0,-1).map((_,i) => getTransition(i));
console.log(`\n✨ Transitions: ${transNames.join(" → ")}`);
const dissolved = xfadeConcat(finalClips, clipDurations);

console.log("🎵 Merging voiceover...");
mergeAudio(dissolved, audio, outputPath);
console.log(`\n🎉 Final video → ${outputPath}`);
