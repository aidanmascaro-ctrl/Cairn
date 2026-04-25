import App from "./App.svelte";

const target = document.getElementById("app");
if (!target) {
  throw new Error("Cairn: #app root element missing from index.html");
}

const app = new App({ target });

export default app;
