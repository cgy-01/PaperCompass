// Progressive enhancement: content stays readable without observers or animations.
const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)");
const seen = new WeakSet();
const running = new Set();
const pending = new Set();
let observer,
  active = false,
  initialized = false;

function animate(element, frames, options = {}) {
  if (!element?.animate || reduceMotion.matches || !active) return;
  const animation = element.animate(frames, {
    duration: 720,
    easing: "cubic-bezier(.22,1,.36,1)",
    ...options,
  });
  running.add(animation);
  // No persistent fill or inline transforms: hover styles take over after entry.
  animation.finished.catch(() => {}).finally(() => running.delete(animation));
  return animation;
}

function enter(element, delay = 0) {
  animate(
    element,
    [
      { opacity: 0, transform: "translateY(22px)" },
      { opacity: 1, transform: "translateY(0)" },
    ],
    { delay, fill: "backwards" },
  );
}

function stop() {
  observer?.disconnect();
  pending.clear();
  for (const animation of running) animation.cancel();
  running.clear();
}

export function setWelcomeMotionActive(value) {
  active = value && !document.hidden;
  document.body.classList.toggle("welcome-motion-paused", document.hidden);
  document.body.classList.toggle(
    "welcome-motion-active",
    active && !reduceMotion.matches,
  );
  if (!active || reduceMotion.matches) stop();
}

export function syncWelcomeMotion() {
  if (!initialized) {
    initialized = true;
    const resume = () => {
      setWelcomeMotionActive(document.body.classList.contains("is-welcome"));
      if (active) syncWelcomeMotion();
    };
    document.addEventListener("visibilitychange", resume);
    reduceMotion.addEventListener("change", resume);
  }
  setWelcomeMotionActive(document.body.classList.contains("is-welcome"));
  if (!active || reduceMotion.matches) return;
  if (!globalThis.IntersectionObserver) return;
  observer ??= new IntersectionObserver(
    (entries) => {
      const visible = entries.filter((entry) => entry.isIntersecting);
      visible.sort(
        (a, b) =>
          a.boundingClientRect.top - b.boundingClientRect.top ||
          a.boundingClientRect.left - b.boundingClientRect.left,
      );
      visible.forEach(({ target }, index) => {
        if (!seen.has(target)) {
          seen.add(target);
          enter(target, Math.min(index, 4) * 55);
        }
        observer.unobserve(target);
        pending.delete(target);
      });
    },
    { threshold: 0.08 },
  );
  // Catalog rendering can replace preview nodes while this page stays open.
  for (const element of pending) {
    if (!element.isConnected) {
      observer.unobserve(element);
      pending.delete(element);
    }
  }
  document
    .querySelectorAll(
      ".welcome-cluster-heading, .welcome-section-intro, .welcome-area, .welcome-paper, .welcome-reading-sheet, .welcome-library-note, .welcome-update-note, .welcome-mcp-terminal, .welcome-prompt-note, .welcome-final > *",
    )
    .forEach((element) => {
      // Already visible content replaced by a catalog refresh must not flash again.
      if (seen.has(element)) return;
      const top = element.getBoundingClientRect().top;
      if (element.dataset.motionKnown || top >= innerHeight) {
        element.dataset.motionKnown = "true";
        observer.observe(element);
        pending.add(element);
      } else {
        seen.add(element);
      }
    });
}

export function showWelcomeSticky(sticky, visible) {
  const changed = sticky.hidden === visible;
  sticky.hidden = !visible;
  if (changed && visible)
    animate(
      sticky,
      [
        { opacity: 0, transform: "translateY(100%)" },
        { opacity: 1, transform: "translateY(0)" },
      ],
      { duration: 350 },
    );
}
