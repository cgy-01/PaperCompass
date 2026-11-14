// Keep the first screen independent of the workspace module graph and APIs.
// Use the installed Lucide library directly; no custom icon geometry.
import replaceElement from "./vendor/lucide/replaceElement.js";
import ArrowUpRight from "./vendor/lucide/icons/arrow-up-right.js";
import ArrowRight from "./vendor/lucide/icons/arrow-right.js";
import ArrowDownRight from "./vendor/lucide/icons/arrow-down-right.js";
import ArrowDown from "./vendor/lucide/icons/arrow-down.js";
import Compass from "./vendor/lucide/icons/compass.js";
import Files from "./vendor/lucide/icons/files.js";
import TrendingDown from "./vendor/lucide/icons/trending-down.js";
import { renderWelcomeDesk } from "./pages/welcome-desk.js";

const icons = { ArrowUpRight, ArrowRight, ArrowDownRight, ArrowDown, Compass, Files, TrendingDown };
document.querySelectorAll("#welcomeNav i[data-lucide], #welcomeHero i[data-lucide]")
  .forEach((element) => replaceElement(element, {
    nameAttr: "data-lucide", icons,
    attrs: { width: 20, height: 20, "stroke-width": 1.75 },
  }));
renderWelcomeDesk();

// Visibility handling must also work while workspace modules are downloading.
const syncVisibility = () => document.body.classList.toggle("welcome-motion-paused", document.hidden);
document.addEventListener("visibilitychange", syncVisibility);
syncVisibility();
