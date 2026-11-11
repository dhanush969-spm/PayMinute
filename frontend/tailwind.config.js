/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: { jakarta: ['"Plus Jakarta Sans"', "system-ui", "sans-serif"] },
      boxShadow: { xs: "0 1px 2px rgba(15, 23, 42, 0.05)" },
      backdropBlur: { xs: "2px" },
    },
  },
  // Tailwind's reset is off: the app's own stylesheet (styles.css) provides the base styles.
  corePlugins: { preflight: false },
  plugins: [],
};
