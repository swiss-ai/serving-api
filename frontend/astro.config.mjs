import { defineConfig } from "astro/config";
import mdx from "@astrojs/mdx";
import sitemap from "@astrojs/sitemap";
import tailwind from "@astrojs/tailwind";

import svelte from "@astrojs/svelte";

import auth from "auth-astro";

import node from "@astrojs/node";

export default defineConfig({
  site: "https://research.computer",
  integrations: [svelte(), mdx(), sitemap(), tailwind(), auth()],

  experimental: {
  },

  adapter: node({
    mode: "standalone",
  }),
});