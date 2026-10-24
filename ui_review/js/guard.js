// Installed before any page script runs. The review never submits a form: submit events
// are cancelled in the capture phase and the programmatic submit methods do nothing.
// Non-GET requests are also aborted by the browser-level route, so this is a second wall.
(() => {
  const cancel = (event) => {
    event.preventDefault();
    event.stopImmediatePropagation();
  };
  window.addEventListener("submit", cancel, true);
  const noop = function () {};
  Object.defineProperty(HTMLFormElement.prototype, "submit", { value: noop });
  Object.defineProperty(HTMLFormElement.prototype, "requestSubmit", { value: noop });
  window.open = () => null;
})();
