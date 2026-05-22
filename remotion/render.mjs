import { renderMedia, selectComposition } from "@remotion/renderer";
import { readFileSync } from "fs";
import { resolve } from "path";
import { fileURLToPath } from "url";
import { dirname } from "path";

const __dirname = dirname(fileURLToPath(import.meta.url));

const manifestPath = process.argv[2];
const outputPath = process.argv[3];

if (!manifestPath || !outputPath) {
  console.error("Usage: node render.mjs <manifest.json> <output.mp4>");
  process.exit(1);
}

const props = JSON.parse(readFileSync(manifestPath, "utf-8"));

console.log("📋 Props loaded:", JSON.stringify(props, null, 2));
console.log("🎬 Output:", outputPath);

const bundleLocation = resolve(__dirname, "src/index.ts");

const chromiumOptions = {
  disableWebSecurity: true,
  gl: "swiftshader",
  userAgent: undefined,
  ignoreCertificateErrors: false,
  headless: true,
  chromiumFlags: [
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--single-process",
    "--no-zygote",
  ],
};

console.log("🚀 Starting Remotion render...");

try {
  const { bundle } = await import("@remotion/bundler");

  console.log("📦 Bundling...");
  const bundled = await bundle({
    entryPoint: bundleLocation,
    webpackOverride: (config) => config,
  });

  console.log("🎭 Selecting composition...");
  const composition = await selectComposition({
    serveUrl: bundled,
    id: "VideoComposition",
    inputProps: props,
    chromiumOptions,
  });

  console.log(`⏱️  Duration: ${composition.durationInFrames} frames @ ${composition.fps}fps`);

  console.log("🎞️  Rendering...");
  await renderMedia({
    composition,
    serveUrl: bundled,
    codec: "h264",
    outputLocation: outputPath,
    inputProps: props,
    chromiumOptions,
    onProgress: ({ progress }) => {
      process.stdout.write(`\r  Progress: ${Math.round(progress * 100)}%`);
    },
  });

  console.log("\n✅ Render complete →", outputPath);
} catch (err) {
  console.error("\n❌ Render error:", err);
  process.exit(1);
}
