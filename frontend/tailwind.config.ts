import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      /* ==========================================================================
         COLOR TOKENS - New Design System
         ========================================================================== */
      colors: {
        // Background colors
        "bg-primary": "var(--color-bg-primary)",
        "bg-secondary": "var(--color-bg-secondary)",
        "bg-tertiary": "var(--color-bg-tertiary)",
        "bg-inverse": "var(--color-bg-inverse)",
        "bg-sidebar": "var(--color-bg-sidebar)",
        "bg-input": "var(--color-bg-input)",
        "bg-hover": "var(--color-bg-hover)",
        "bg-active": "var(--color-bg-active)",
        "bg-overlay": "var(--color-bg-overlay)",
        "bg-tooltip": "var(--color-bg-tooltip)",

        // Text colors
        "text-primary": "var(--color-text-primary)",
        "text-secondary": "var(--color-text-secondary)",
        "text-muted": "var(--color-text-muted)",
        "text-inverse": "var(--color-text-inverse)",
        "text-accent": "var(--color-text-accent)",
        "text-link": "var(--color-text-link)",
        "text-link-hover": "var(--color-text-link-hover)",

        // Border colors
        "border-primary": "var(--color-border-primary)",
        "border-secondary": "var(--color-border-secondary)",
        "border-muted": "var(--color-border-muted)",
        "border-focus": "var(--color-border-focus)",
        "border-accent": "var(--color-border-accent)",

        // Accent colors
        "accent-primary": "var(--color-accent-primary)",
        "accent-hover": "var(--color-accent-hover)",
        "accent-active": "var(--color-accent-active)",
        "accent-muted": "var(--color-accent-muted)",
        "accent-subtle": "var(--color-accent-subtle)",

        // Status colors
        "status-success": "var(--color-status-success)",
        "status-success-bg": "var(--color-status-success-bg)",
        "status-warning": "var(--color-status-warning)",
        "status-warning-bg": "var(--color-status-warning-bg)",
        "status-error": "var(--color-status-error)",
        "status-error-bg": "var(--color-status-error-bg)",
        "status-info": "var(--color-status-info)",
        "status-info-bg": "var(--color-status-info-bg)",

        /* ==========================================================================
           BACKWARD COMPATIBILITY - Legacy daemon-* namespace
           ========================================================================== */
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

      /* ==========================================================================
         BORDER RADIUS TOKENS
         ========================================================================== */
      borderRadius: {
        sm: "var(--radius-sm)",
        md: "var(--radius-md)",
        lg: "var(--radius-lg)",
        xl: "var(--radius-xl)",
        "2xl": "var(--radius-2xl)",
        full: "var(--radius-full)",
      },

      /* ==========================================================================
         BOX SHADOW TOKENS
         ========================================================================== */
      boxShadow: {
        sm: "var(--shadow-sm)",
        md: "var(--shadow-md)",
        lg: "var(--shadow-lg)",
        xl: "var(--shadow-xl)",
        glow: "var(--shadow-glow)",
        inner: "var(--shadow-inner)",
      },

      /* ==========================================================================
         SPACING TOKENS
         ========================================================================== */
      spacing: {
        "1": "var(--space-1)",
        "2": "var(--space-2)",
        "3": "var(--space-3)",
        "4": "var(--space-4)",
        "5": "var(--space-5)",
        "6": "var(--space-6)",
        "8": "var(--space-8)",
        "10": "var(--space-10)",
        "12": "var(--space-12)",
        "16": "var(--space-16)",
        "20": "var(--space-20)",
        "24": "var(--space-24)",
      },

      /* ==========================================================================
         FONT FAMILY TOKENS
         ========================================================================== */
      fontFamily: {
        sans: ["var(--font-sans)"],
        display: ["var(--font-display)"],
        mono: ["var(--font-mono)"],
      },

      /* ==========================================================================
         FONT SIZE TOKENS
         ========================================================================== */
      fontSize: {
        xs: "var(--font-size-xs)",
        sm: "var(--font-size-sm)",
        base: "var(--font-size-base)",
        lg: "var(--font-size-lg)",
        xl: "var(--font-size-xl)",
        "2xl": "var(--font-size-2xl)",
        "3xl": "var(--font-size-3xl)",
      },

      /* ==========================================================================
         TRANSITION TOKENS
         ========================================================================== */
      transitionDuration: {
        fast: "150ms",
        base: "200ms",
        slow: "300ms",
      },

      /* ==========================================================================
         Z-INDEX TOKENS
         ========================================================================== */
      zIndex: {
        base: "0",
        dropdown: "100",
        sticky: "200",
        fixed: "300",
        "modal-backdrop": "400",
        modal: "500",
        popover: "600",
        tooltip: "700",
      },

      /* ==========================================================================
         KEYFRAME ANIMATIONS
         ========================================================================== */
      keyframes: {
        "fade-in": {
          "0%": { opacity: "0" },
          "100%": { opacity: "1" },
        },
        "slide-up": {
          "0%": { transform: "translateY(10px)", opacity: "0" },
          "100%": { transform: "translateY(0)", opacity: "1" },
        },
        scale: {
          "0%": { transform: "scale(0.95)", opacity: "0" },
          "100%": { transform: "scale(1)", opacity: "1" },
        },
        shimmer: {
          "0%": { backgroundPosition: "200% 0" },
          "100%": { backgroundPosition: "-200% 0" },
        },
        "pulse-subtle": {
          "0%, 100%": { opacity: "1" },
          "50%": { opacity: "0.7" },
        },
      },

      /* ==========================================================================
         ANIMATION UTILITIES
         ========================================================================== */
      animation: {
        "fade-in": "fade-in 0.3s ease-out",
        "slide-up": "slide-up 0.4s ease-out",
        scale: "scale 0.2s ease-out",
        shimmer: "shimmer 2s infinite linear",
        "pulse-subtle": "pulse-subtle 2s ease-in-out infinite",
      },
    },
  },
  plugins: [require("@tailwindcss/typography")],
};

export default config;
