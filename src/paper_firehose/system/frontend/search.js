// Search commits only after IME composition ends; Enter always respects IME.
export function bindSearch(input, commit, delay = 250) {
  let timer, composing = false;
  const flush = () => {
    clearTimeout(timer);
    if (!composing) commit(input.value.trim());
  };
  const schedule = () => {
    clearTimeout(timer);
    if (!composing) timer = setTimeout(flush, delay);
  };
  input.form?.addEventListener("submit", event => { event.preventDefault(); flush(); });
  input.addEventListener("compositionstart", () => { composing = true; clearTimeout(timer); });
  input.addEventListener("compositionend", () => { composing = false; schedule(); });
  input.addEventListener("input", event => {
    if (!event.isComposing) schedule();
  });
  input.addEventListener("keydown", event => {
    if (event.key === "Enter") {
      event.preventDefault();
      if (!composing && !event.isComposing && event.keyCode !== 229) flush();
      return;
    }
    if (composing || event.isComposing || event.keyCode === 229) return;
    if (event.key === "Escape" && input.value) {
      event.preventDefault(); input.value = ""; flush();
    }
  });
}
