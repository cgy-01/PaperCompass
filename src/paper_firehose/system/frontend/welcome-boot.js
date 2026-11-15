// Keep the first screen independent of the workspace module graph and APIs.
// Use the installed Lucide library directly; no custom icon geometry.
import { refreshIcons } from "./components/ui.js";
import { renderWelcomeDesk } from "./pages/welcome-desk.js";

refreshIcons(document.querySelector("#welcomeNav"));
refreshIcons(document.querySelector("#welcomeHero"));
renderWelcomeDesk();

// Visibility handling must also work while workspace modules are downloading.
const syncVisibility = () => document.body.classList.toggle("welcome-motion-paused", document.hidden);
document.addEventListener("visibilitychange", syncVisibility);
syncVisibility();
