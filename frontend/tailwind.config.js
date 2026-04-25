/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        bg: {
          base: "#0a0d14",
          panel: "#0f131c",
          elev: "#141927",
          subtle: "#1a2030",
        },
        line: {
          DEFAULT: "#1f2738",
          strong: "#2a3349",
        },
        ink: {
          DEFAULT: "#e6e9f2",
          mute: "#9aa3b8",
          dim: "#6b748c",
        },
        accent: {
          DEFAULT: "#5eb1ff",
          glow: "#7dc4ff",
          deep: "#2c6fbf",
        },
        ok: "#3ddc97",
        warn: "#f3b95f",
        bad: "#ff6b8a",
        asset: {
          equities: "#7dc4ff",
          futures: "#c39bff",
          commodities: "#f3b95f",
          fx: "#3ddc97",
          rates: "#ff9aa9",
          macro: "#9aa3b8",
        },
      },
      fontFamily: {
        sans: [
          "Inter",
          "ui-sans-serif",
          "system-ui",
          "-apple-system",
          "Segoe UI",
          "Roboto",
          "sans-serif",
        ],
        mono: [
          "JetBrains Mono",
          "ui-monospace",
          "SFMono-Regular",
          "Menlo",
          "Consolas",
          "monospace",
        ],
      },
      boxShadow: {
        glow: "0 0 0 1px rgba(94,177,255,0.3), 0 6px 32px -8px rgba(94,177,255,0.35)",
        card: "0 1px 0 rgba(255,255,255,0.04) inset, 0 8px 24px -12px rgba(0,0,0,0.6)",
      },
      keyframes: {
        pulseSoft: {
          "0%,100%": { opacity: "0.55" },
          "50%": { opacity: "1" },
        },
        flow: {
          to: { strokeDashoffset: "-20" },
        },
      },
      animation: {
        pulseSoft: "pulseSoft 1.6s ease-in-out infinite",
        flow: "flow 1.4s linear infinite",
      },
    },
  },
  plugins: [],
};
