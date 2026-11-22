// The temporary address page. The setup interface replaces it.
const form = document.getElementById('address-form');
const input = document.getElementById('address');
const connect = document.getElementById('connect');
const problem = document.getElementById('problem');
const back = document.getElementById('back');

void window.panels.storedServerAddress().then((address) => {
  if (!address) return;
  input.value = address;
  back.hidden = false;
});

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  connect.disabled = true;
  problem.hidden = true;
  // On success the native side opens the screens.
  const result = await window.panels.useServerAddress(input.value);
  if (result.ok) return;
  problem.textContent = result.problem;
  problem.hidden = false;
  connect.disabled = false;
});
