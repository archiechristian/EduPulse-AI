import path from "path";
import type { Config } from "tailwindcss";

const v = (name: string) => `rgb(var(--${name}) / <alpha-value>)`;

const config: Config = {
  content: ["app", "components", "lib"].map((d) => path.join(__dirname, d, "**/*.{ts,tsx}").replace(/\\/g, "/")),
  theme: {
    extend: {
      colors: {
        bg: v("bg"),
        surface: v("surface"),
        surface2: v("surface2"),
        fg: v("fg"),
        muted: v("muted"),
        line: v("line"),
        brand: { DEFAULT: v("brand"), soft: v("brand-soft"), fg: v("brand-fg") },
        accent: v("accent"),
        good: v("good"),
        warn: v("warn"),
        bad: v("bad"),
      },
      fontFamily: {
        sans: ["var(--font-sans)", "system-ui", "sans-serif"],
        display: ["var(--font-display)", "var(--font-sans)", "system-ui", "sans-serif"],
      },
      boxShadow: {
        card: "0 1px 2px rgb(15 23 42 / 0.04), 0 4px 16px rgb(15 23 42 / 0.05)",
      },
    },
  },
  plugins: [],
};
export default config;
