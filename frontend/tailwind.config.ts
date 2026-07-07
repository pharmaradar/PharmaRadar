import type { Config } from "tailwindcss";

const config: Config = {
  darkMode: ["class"],
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        pharma: {
          blue:  "#003087",
          light: "#0066cc",
        },
        dark: {
          bg:      "#0a0f1e",
          sidebar: "#070c19",
          card:    "#111827",
          border:  "#1e3a5f",
          muted:   "#1e2d4a",
          accent:  "#2563eb",
          gold:    "#f59e0b",
          text:    "#e2e8f0",
          sub:     "#94a3b8",
        },
      },
      fontFamily: {
        sans: ["Inter", "system-ui", "sans-serif"],
      },
    },
  },
  plugins: [],
};

export default config;
