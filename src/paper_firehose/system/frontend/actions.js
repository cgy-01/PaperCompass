// Pages request application actions without importing the application entrypoint.
// The shell injects these handlers once, before mounting any page.
let handlers;
export function configureActions(value) {
  handlers = value;
}
export function commitRoute(options) {
  return handlers.commitRoute(options);
}
export function saveStores() {
  return handlers.saveStores();
}
export function render() {
  return handlers.render();
}
export function openDiscover(filters) {
  return handlers.openDiscover(filters);
}
export function refreshCatalog() {
  return handlers.refreshCatalog();
}
