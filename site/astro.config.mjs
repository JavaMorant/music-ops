// @ts-check
import { defineConfig } from "astro/config";
import sitemap from "@astrojs/sitemap";

// `site` is used for absolute URLs (OG tags, sitemap, canonical). Change it to
// your real domain once deployed; everything else is content-driven (content/).
export default defineConfig({
  site: "https://dibsss.live",
  build: { format: "directory" },
  integrations: [sitemap()],
});
