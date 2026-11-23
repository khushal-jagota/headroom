// This shell page uses the ordinary server API. It exposes no native action.
const status = document.getElementById('status');
const button = document.getElementById('update');
let answer;
async function refresh() {
  try {
    const response = await fetch('/api/updates', { cache: 'no-store' });
    if (!response.ok) throw new Error(`The server answered ${response.status}.`);
    answer = await response.json();
    const retry = answer.attempt && answer.allowed_actions?.includes('retry');
    button.disabled = !(answer.available && answer.allowed_actions?.includes('update')) && !retry;
    button.textContent = retry ? 'Try again' : 'Update server';
    status.textContent = button.disabled ? 'No server update is ready. Try again later.' : 'A server update is ready.';
  } catch (error) { status.textContent = error.message; }
}
document.getElementById('retry').addEventListener('click', () => { void refresh(); });
button.addEventListener('click', async () => {
  button.disabled = true;
  try {
    const retry = answer.attempt && answer.allowed_actions?.includes('retry');
    const path = retry ? `/api/updates/attempts/${encodeURIComponent(answer.attempt.attempt_id)}/retry` : '/api/updates/install';
    const response = await fetch(path, {
      method: 'POST',
      headers: { 'content-type': 'application/json', 'X-Panels-App-SHA': answer.app_sha },
      body: JSON.stringify(retry ? {} : { app_sha: answer.available.app_sha })
    });
    if (!response.ok) {
      const failure = await response.json().catch(() => ({}));
      throw new Error(typeof failure.detail === 'string' ? failure.detail : `The server answered ${response.status}.`);
    }
    status.textContent = 'The server is updating. Panels will reconnect.';
  } catch (error) { status.textContent = error.message; button.disabled = false; }
});
void refresh();
