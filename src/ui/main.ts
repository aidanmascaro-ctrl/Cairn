import App from "./App.svelte";

const target = document.getElementById("app");
if (!target) {
  throw new Error("Cairn: #app root element missing from index.html");
}

// The macOS window has no title bar (see seamlessChrome in main.js), so the page leaves room for the traffic lights.
if (navigator.userAgent.includes("Macintosh")) {
  document.documentElement.classList.add("mac");
}

const app = new App({ target });

export default app;
