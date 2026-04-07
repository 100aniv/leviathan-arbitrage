import type { Config } from "tailwindcss";
import forms from "@tailwindcss/forms";

const config: Config = {
  // darkMode disabled — DESIGN-kraken.md mandates light theme only
  content: [
    "./src/pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/components/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        // ── Kraken Design System — Light Theme (DESIGN-kraken.md SSOT) ──
        brand: {
          DEFAULT: "#7132F5",         // Kraken Purple — CTA, links
          hover:   "#5741D8",         // hover / active
          deep:    "#5B1ECF",         // deepest purple
          subtle:  "rgba(133,91,251,0.10)", // tag / badge bg
        },
        bg: {
          base:     "#FFFFFF",        // page background
          surface:  "#FAFAFB",        // card (whisper gray)
          elevated: "#FFFFFF",        // modal / popover
        },
        border: {
          DEFAULT: "#DEDEE5",                      // 1px divider
          subtle:  "rgba(104,107,130,0.12)",        // hairline
        },
        text: {
          primary:   "#101114",       // Near Black
          secondary: "#686B82",       // Cool Gray
          tertiary:  "#9497A9",       // Silver Blue
        },
        success: {
          DEFAULT: "#149E61",
          bg:      "rgba(20,158,97,0.12)",
          text:    "#026B3F",
        },
        danger: {
          DEFAULT: "#E5484D",
          bg:      "rgba(229,72,77,0.10)",
        },
        warning: {
          DEFAULT: "#F59E0B",
          bg:      "rgba(245,158,11,0.10)",
        },
        info: {
          DEFAULT: "#3B82F6",
          bg:      "rgba(59,130,246,0.10)",
        },
        // ── Semantic aliases (backward compat with existing components) ──
        profit: {
          DEFAULT: "#149E61",
          dim:     "#026B3F",
          glow:    "rgba(20,158,97,0.12)",
        },
        loss: {
          DEFAULT: "#E5484D",
          dim:     "#CC3344",
          glow:    "rgba(229,72,77,0.10)",
        },
        accent: {
          DEFAULT: "#7132F5",
          dim:     "#5741D8",
          glow:    "rgba(113,50,245,0.12)",
          subtle:  "rgba(133,91,251,0.10)",
        },
        warn: {
          DEFAULT: "#F59E0B",
          dim:     "#D97706",
        },
        // ── Legacy terminal aliases (for components not yet migrated) ──
        terminal: {
          bg:      "#FFFFFF",
          surface: "#FAFAFB",
          border:  "#DEDEE5",
          muted:   "#F5F5F7",
          text:    "#101114",
          subtle:  "#686B82",
        },
      },
      fontFamily: {
        sans:    ["'Pretendard Variable'", "Pretendard", "'IBM Plex Sans'", "system-ui", "sans-serif"],
        mono:    ["'IBM Plex Mono'", "'Fira Code'", "monospace"],
        display: ["'Pretendard Variable'", "Pretendard", "'IBM Plex Sans'", "sans-serif"],
      },
      fontSize: {
        "display": ["48px", { lineHeight: "1.17", letterSpacing: "-1px", fontWeight: "700" }],
        "heading":  ["36px", { lineHeight: "1.22", letterSpacing: "-0.5px", fontWeight: "700" }],
        "subhead":  ["28px", { lineHeight: "1.29", letterSpacing: "-0.5px", fontWeight: "700" }],
        "title":    ["22px", { lineHeight: "1.20" }],
        "body":     ["16px", { lineHeight: "1.38" }],
        "caption":  ["14px", { lineHeight: "1.43" }],
        "small":    ["12px", { lineHeight: "1.33" }],
      },
      boxShadow: {
        card:        "rgba(0,0,0,0.03) 0px 4px 24px",
        micro:       "rgba(16,24,40,0.04) 0px 1px 4px",
        brand:       "0 4px 24px rgba(113,50,245,0.15)",
        "card-hover":"0 8px 32px rgba(113,50,245,0.08)",
      },
      borderRadius: {
        button: "12px",
        badge:  "6px",
        card:   "16px",
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
        shimmer: {
          "0%":   { backgroundPosition: "-200% 0" },
          "100%": { backgroundPosition:  "200% 0" },
        },
        pulseGreen: {
          "0%, 100%": { boxShadow: "0 0 0 0 rgba(20,158,97,0)" },
          "50%":      { boxShadow: "0 0 0 6px rgba(20,158,97,0.15)" },
        },
      },
      animation: {
        blink:        "blink 1s step-end infinite",
        "fade-in":    "fadeIn 300ms ease",
        "count-up":   "countUp 300ms ease",
        shimmer:      "shimmer 1.5s infinite",
        "pulse-green":"pulseGreen 2s ease-in-out infinite",
      },
    },
  },
  plugins: [forms],
};

export default config;
