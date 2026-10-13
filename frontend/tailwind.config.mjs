import colors from "tailwindcss/colors";

// Semantic colours read the CSS variables in src/styles/commons.css, so
// Tailwind utilities and the commons classes switch theme together. Opacity
// modifiers (bg-primary/10) go through color-mix because the variables hold
// plain hex values shared with the sibling apps.
const token = (name) => ({ opacityValue }) =>
  opacityValue === undefined || opacityValue === "1"
    ? `var(${name})`
    : `color-mix(in srgb, var(${name}) calc(${opacityValue} * 100%), transparent)`;

// Primary scale of the sibling apps (--primary #1a56db is 700,
// --primary-dark #1e429f is 800).
const primaryScale = {
  50: "#ebf5ff",
  100: "#e1effe",
  200: "#c3ddfd",
  300: "#a4cafe",
  400: "#76a9fa",
  500: "#3f83f8",
  600: "#1c64f2",
  700: "#1a56db",
  800: "#1e429f",
  900: "#233876",
  950: "#172554",
};

/** @type {import('tailwindcss').Config} */
export default {
  darkMode: ["class"],
  content: [
    "./src/**/*.{astro,html,js,jsx,md,mdx,svelte,ts,tsx,vue}",
  ],
  theme: {
    extend: {
      fontFamily: {
        sans: ["var(--font-sans)"],
        mono: ["var(--font-mono)"],
      },
      colors: {
        // Legacy class names are pointed at the commons palette so existing
        // markup follows the reskin. New code should use the semantic names.
        slate: colors.slate,
        gray: colors.slate,
        indigo: primaryScale,
        brand: primaryScale,

        bg: token("--bg"),
        card: token("--card"),
        ink: token("--text"),
        muted: token("--muted"),
        line: token("--border"),
        "line-strong": token("--border-strong"),
        subtle: token("--subtle"),
        primary: {
          DEFAULT: token("--primary"),
          dark: token("--primary-dark"),
          ink: token("--primary-ink"),
          soft: token("--primary-soft"),
        },
        accent: {
          DEFAULT: token("--accent"),
          dark: token("--accent-dark"),
        },
      },
      borderRadius: {
        card: "var(--radius-card)",
        input: "var(--radius-input)",
        btn: "var(--radius-btn)",
      },
      boxShadow: {
        menu: "var(--shadow-menu)",
      },
      maxWidth: {
        content: "var(--content-max)",
        reading: "var(--reading-max)",
      },
    },
  },
  plugins: [require("@tailwindcss/typography")],
};
