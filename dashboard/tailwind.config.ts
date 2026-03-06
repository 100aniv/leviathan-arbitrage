import type { Config } from "tailwindcss";
import forms from "@tailwindcss/forms";

const config: Config = {
  darkMode: "class",
  content: [
    "./src/pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/components/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        terminal: {
          bg:      "#0a0c0f",
          surface: "#111419",
          border:  "#1e2329",
          muted:   "#2a303a",
          text:    "#c9d1d9",
          subtle:  "#6e7681",
        },
        profit: {
          DEFAULT: "#00ff88",
          dim:     "#00cc6a",
          glow:    "rgba(0,255,136,0.15)",
        },
        loss: {
          DEFAULT: "#ff4d4d",
          dim:     "#cc3333",
          glow:    "rgba(255,77,77,0.15)",
        },
        accent: {
          DEFAULT: "#3b82f6",
          dim:     "#2563eb",
          glow:    "rgba(59,130,246,0.15)",
        },
        warn: {
          DEFAULT: "#f59e0b",
          dim:     "#d97706",
        },
      },
      fontFamily: {
        mono: ["'JetBrains Mono'", "'Fira Code'", "monospace"],
        sans: ["'Inter'", "system-ui", "sans-serif"],
      },
      boxShadow: {
        profit: "0 0 12px rgba(0,255,136,0.3)",
        loss:   "0 0 12px rgba(255,77,77,0.3)",
        accent: "0 0 12px rgba(59,130,246,0.3)",
      },
      keyframes: {
        blink: {
          "0%, 100%": { opacity: "1" },
          "50%":      { opacity: "0" },
        },
      },
      animation: {
        blink: "blink 1s step-end infinite",
      },
    },
  },
  plugins: [forms],
};

export default config;
