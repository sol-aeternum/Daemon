import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        daemon: {
          "bg-primary": "var(--daemon-bg-primary)",
          "bg-secondary": "var(--daemon-bg-secondary)",
          "bg-tertiary": "var(--daemon-bg-tertiary)",
          "bg-sidebar": "var(--daemon-bg-sidebar)",
          "text-primary": "var(--daemon-text-primary)",
          "text-secondary": "var(--daemon-text-secondary)",
          "text-muted": "var(--daemon-text-muted)",
          accent: "var(--daemon-accent)",
          "accent-hover": "var(--daemon-accent-hover)",
          "border-primary": "var(--daemon-border-primary)",
          "border-secondary": "var(--daemon-border-secondary)",
        },
      },
      keyframes: {
        "fade-in": {
          "0%": { opacity: "0" },
          "100%": { opacity: "1" },
        },
        "slide-up": {
          "0%": { transform: "translateY(10px)", opacity: "0" },
          "100%": { transform: "translateY(0)", opacity: "1" },
        },
        "scale": {
          "0%": { transform: "scale(0.95)", opacity: "0" },
          "100%": { transform: "scale(1)", opacity: "1" },
        },
      },
      animation: {
        "fade-in": "fade-in 0.3s ease-out",
        "slide-up": "slide-up 0.4s ease-out",
        "scale": "scale 0.2s ease-out",
      },
    },
  },
  plugins: [require("@tailwindcss/typography")],
};

export default config;
