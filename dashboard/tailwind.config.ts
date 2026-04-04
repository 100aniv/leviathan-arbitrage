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
        // ── Terminal tokens — DARK THEME (XXX STUDIO × LEVIATHAN) ──
        terminal: {
          bg:      "#0A0B0E",   // near-black main background
          surface: "#12141A",   // card / panel
          border:  "rgba(255,255,255,0.07)",
          muted:   "#1C1F28",   // hover / tertiary
          text:    "#E8EAF0",   // primary text (near-white)
          subtle:  "#6B7280",   // muted text
        },
        // ── Semantic tokens (XXX STUDIO × LEVIATHAN) ──
        profit: {
          DEFAULT: "#00C896",
          dim:     "#009E78",
          glow:    "rgba(0,200,150,0.15)",
        },
        loss: {
          DEFAULT: "#FF4757",
          dim:     "#CC3344",
          glow:    "rgba(255,71,87,0.15)",
        },
        accent: {
          DEFAULT: "#00B8FF",   // XXX STUDIO brand blue
          dim:     "#0090CC",
          glow:    "rgba(0,184,255,0.15)",
          subtle:  "rgba(0,184,255,0.10)",
        },
        warn: {
          DEFAULT: "#F59E0B",   // unchanged
          dim:     "#D97706",
        },
        // ── Aliases for new semantic names ──
        success: {
          DEFAULT: "#00C896",
          glow:    "rgba(0,200,150,0.15)",
        },
        danger: {
          DEFAULT: "#FF4757",
          glow:    "rgba(255,71,87,0.15)",
        },
      },
      fontFamily: {
        mono: ["'IBM Plex Mono'", "'Fira Code'", "monospace"],
        sans: ["'IBM Plex Sans'", "system-ui", "sans-serif"],
      },
      boxShadow: {
        profit:  "0 0 12px rgba(0,200,150,0.3)",
        loss:    "0 0 12px rgba(255,71,87,0.3)",
        accent:  "0 0 12px rgba(0,184,255,0.3)",
        // Hover glow for cards
        "accent-hover": "0 8px 32px rgba(0,184,255,0.12)",
        // Glassmorphism
        glass:   "0 4px 24px rgba(0,0,0,0.6)",
      },
      keyframes: {
        blink: {
          "0%, 100%": { opacity: "1" },
          "50%":      { opacity: "0" },
        },
        fadeIn: {
          from: { opacity: "0", transform: "translateY(4px)" },
          to:   { opacity: "1", transform: "translateY(0)" },
        },
        countUp: {
          from: { opacity: "0.4" },
          to:   { opacity: "1" },
        },
        pulseAccent: {
          "0%, 100%": { boxShadow: "0 0 0 0 rgba(0,184,255,0)" },
          "50%":      { boxShadow: "0 0 12px 2px rgba(0,184,255,0.25)" },
        },
        shimmer: {
          "0%":   { backgroundPosition: "-200% 0" },
          "100%": { backgroundPosition:  "200% 0" },
        },
      },
      animation: {
        blink:        "blink 1s step-end infinite",
        "fade-in":    "fadeIn 300ms ease",
        "count-up":   "countUp 300ms ease",
        "pulse-accent": "pulseAccent 2s ease-in-out infinite",
        shimmer:      "shimmer 1.5s infinite",
      },
    },
  },
  plugins: [forms],
};

export default config;
