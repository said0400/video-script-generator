import { renderMedia, selectComposition, openBrowser } from "@remotion/renderer";
import { bundle } from "@remotion/bundler";
import { readFileSync } from "fs";
import { resolve } from "path";
import { fileURLToPath } from "url";
import { dirname } from "path";
import { execSync } from "child_process";

const __dirname = dirname(fileURLToPath(import.meta.url));

const manifestPath = process.argv[2];
const outputPath = process.argv[3];

if (!manifestPath || !outputPath) {
  console.error("Usage: node render.mjs <manifest.json> <output.mp4>");
  process.exit(1);
}

const props = JSON.parse(readFileSync(manifestPath, "utf-8"));
console.log("📋 Props loaded successfully");
console.log("🎬 Output:", outputPath);

// Find Chrome executable
function findChrome() {
  const candidates = [
    "/usr/bin/google-chrome-stable",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium-browser",
    "/usr/bin/chromium",
  ];
  for (const c of candidates) {
    try {
      execSync(`test -f ${c}`);
      console.log("✅ Chrome found at:", c);
      return c;
    } catch {}
  }
  throw new Error("Chrome not found. Install google-chrome-stable.");
}

const chromiumExecutable = findChrome();

const chromiumOptions = {
  disableWebSecurity: true,
  gl: "angle",
  headless: true,
  ignoreCertificateErrors: true,
  chromiumFlags: [
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--no-zygote",
    "--single-process",
    "--disable-extensions",
    "--disable-background-networking",
    "--disable-default-apps",
    "--disable-sync",
    "--disable-translate",
    "--hide-scrollbars",
    "--metrics-recording-only",
    "--mute-audio",
    "--no-first-run",
    "--safebrowsing-disable-auto-update",
    "--ignore-gpu-blacklist",
    "--use-gl=angle",
    "--use-angle=swiftshader-webgl",
  ],
};

console.log("🚀 Starting Remotion render...");

try {
  console.log("📦 Bundling...");
  const bundled = await bundle({
    entryPoint: resolve(__dirname, "src/index.ts"),
    webpackOverride: (config) => config,
  });
  console.log("✅ Bundle done:", bundled);

  console.log("🌐 Opening browser...");
  const browser = await openBrowser("chrome", {
    browserExecutable: chromiumExecutable,
    chromiumOptions,
    shouldDumpIo: false,
  });
  console.log("✅ Browser opened");

  console.log("🎭 Selecting composition...");
  const composition = await selectComposition({
    serveUrl: bundled,
    id: "VideoComposition",
    inputProps: props,
    browserExecutable: chromiumExecutable,
    chromiumOptions,
  });
  console.log(`✅ Composition: ${composition.durationInFrames} frames @ ${composition.fps}fps`);

  console.log("🎞️  Rendering...");
  await renderMedia({
    composition,
    serveUrl: bundled,
    codec: "h264",
    outputLocation: outputPath,
    inputProps: props,
    browserExecutable: chromiumExecutable,
    chromiumOptions,
    concurrency: 1,
    onProgress: ({ progress }) => {
      process.stdout.write(`\r  ⏳ Progress: ${Math.round(progress * 100)}%   `);
    },
  });

  await browser.close();

  console.log("\n✅ Render complete →", outputPath);
} catch (err) {
  console.error("\n❌ Render error:", err.message || err);
  console.error(err.stack);
  process.exit(1);
}
