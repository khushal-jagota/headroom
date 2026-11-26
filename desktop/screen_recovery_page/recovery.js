// This page belongs to the native shell, outside the failed screen copy.
const retry = document.getElementById('retry');
const result = document.getElementById('result');
retry.addEventListener('click', async () => {
  retry.disabled = true;
  result.textContent = 'Opening the updated Panels…';
  try {
    if (await window.panels.retryScreens()) return;
    result.textContent = 'The updated screens are not ready. Try again.';
  } catch {
    result.textContent = 'Panels could not check the updated screens. Try again.';
  }
  retry.disabled = false;
});
