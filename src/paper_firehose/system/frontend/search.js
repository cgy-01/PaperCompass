// Native form submission handles both the search button and IME-safe Enter.
export function bindSearch(input, commit) {
  input.form?.addEventListener("submit", event => {
    event.preventDefault();
    commit(input.value.trim());
  });
}
