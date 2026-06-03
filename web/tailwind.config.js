/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  // Dark mode via class so the root <html class="dark"> toggle works
  darkMode: "class",
  theme: {
    extend: {},
  },
  plugins: [],
};
