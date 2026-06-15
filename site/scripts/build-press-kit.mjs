// Bundles everything in content/press-kit/ into public/press-kit.zip so the site
// can offer a single press-kit download. Runs as the `prebuild` step (before
// `astro build`) and is CI/Vercel-safe — uses the `archiver` lib, not a system
// `zip` binary. Run manually with: node scripts/build-press-kit.mjs
import { createWriteStream, existsSync, mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import archiver from "archiver";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const srcDir = resolve(root, "content/press-kit");
const outFile = resolve(root, "public/press-kit.zip");

if (!existsSync(srcDir)) {
  console.warn(`[press-kit] no ${srcDir} — skipping zip`);
  process.exit(0);
}
mkdirSync(dirname(outFile), { recursive: true });

const output = createWriteStream(outFile);
const archive = archiver("zip", { zlib: { level: 9 } });

output.on("close", () =>
  console.log(`[press-kit] wrote ${outFile} (${archive.pointer()} bytes)`),
);
archive.on("warning", (err) => {
  if (err.code === "ENOENT") console.warn("[press-kit]", err);
  else throw err;
});
archive.on("error", (err) => {
  throw err;
});

archive.pipe(output);
archive.directory(srcDir, "Dibsss-press-kit");
await archive.finalize();
