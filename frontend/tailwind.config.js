/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    extend: {
      colors: {
        canvas: "#f5f5f5",
        ink: "#292524",
        "ink-soft": "#4e4e4e",
        muted: "#777169",
        hairline: "#e7e5e4",
        surface: "#ffffff",
      },
      fontFamily: {
        display: ["EB Garamond", "Georgia", "serif"],
        body: ["Inter", "sans-serif"],
      },
    },
  },
  plugins: [],
}

