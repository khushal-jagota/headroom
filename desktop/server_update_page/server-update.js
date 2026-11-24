// This shell page uses the ordinary server API. It exposes no native action.
const status = document.getElementById('status');
const address = document.getElementById('server-address');
const errorIcon = '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" aria-hidden="true"><circle cx="8" cy="8" r="6.25"/><path d="M8 4.75v3.75M8 11h.01"/></svg>';
let answer;

// All server-provided text uses textContent, including refusal details and the address.
function render(line, { note = '', quiet = false, error = false, loading = false, action = null, updating = false } = {}) {
  status.replaceChildren();
  const content = document.createElement('div');
  const title = document.createElement('div');
  title.className = `update-line${quiet ? ' quiet' : ''}${error ? ' bad' : ''}`;
  if (error) title.innerHTML = errorIcon;
  if (loading) title.innerHTML = '<i class="spin" aria-hidden="true"></i>';
  title.append(document.createTextNode(line));
  content.append(title);
  if (note) {
    const detail = document.createElement('p');
    detail.className = 'update-note';
    detail.textContent = note;
    content.append(detail);
  }
  if (action) {
    const actions = document.createElement('div');
    actions.className = 'update-actions';
    const button = document.createElement('button');
    button.type = 'button';
    button.id = action.press ? 'update' : 'retry';
    button.className = `button button--main button--${action.press ? 'primary' : 'secondary'}`;
    button.textContent = action.label;
    button.addEventListener('click', action.press ? () => { void press(button); } : () => { void refresh(); });
    actions.append(button);
    content.append(actions);
  }
  if (updating) {
    const track = document.createElement('div');
    track.className = 'track';
    track.setAttribute('aria-hidden', 'true');
    track.append(document.createElement('i'));
    content.append(track);
  }
  status.append(content);
}

async function refresh() {
  render('Checking for an update…', { quiet: true, loading: true });
  answer = undefined;
  let response;
  try {
    response = await fetch('/api/updates', { cache: 'no-store' });
  } catch {
    render('No answer from the server.', { error: true, action: { label: 'Check again' } });
    return;
  }
  if (!response.ok) {
    render(`The server answered ${response.status}.`, { error: true, action: { label: 'Check again' } });
    return;
  }
  try {
    answer = await response.json();
  } catch {
    render('No answer from the server.', { error: true, action: { label: 'Check again' } });
    return;
  }
  if (answer.attempt && answer.allowed_actions?.includes('retry')) {
    render("The last update didn't install", { error: true, note: 'The server went back to its earlier version.', action: { label: 'Try again', press: true } });
  } else if (answer.available && answer.allowed_actions?.includes('update')) {
    render('An update is ready', { note: answer.interruption?.stops_agents ? 'This update stops your agents.' : 'Your agents keep running.', action: { label: 'Update server', press: true } });
  } else {
    render('No server update is ready yet.', { quiet: true, note: 'Check again later.', action: { label: 'Check again' } });
  }
}

async function press(button) {
  button.disabled = true;
  const label = button.textContent;
  button.innerHTML = '<i class="spin" aria-hidden="true"></i>';
  button.append(document.createTextNode(label));
  status.querySelector('.problem')?.remove();
  try {
    const retry = answer.attempt && answer.allowed_actions?.includes('retry');
    const path = retry ? `/api/updates/attempts/${encodeURIComponent(answer.attempt.attempt_id)}/retry` : '/api/updates/install';
    const response = await fetch(path, {
      method: 'POST',
      headers: { 'content-type': 'application/json', 'X-Panels-App-SHA': answer.app_sha },
      body: JSON.stringify(retry ? {} : { app_sha: answer.available.app_sha }),
    });
    if (!response.ok) {
      const failure = await response.json().catch(() => ({}));
      throw new Error(typeof failure.detail === 'string' ? failure.detail : `The server answered ${response.status}.`);
    }
    render('Updating the server…', { note: 'Panels opens by itself when the server is back.', updating: true });
  } catch (error) {
    const problem = document.createElement('p');
    problem.className = 'problem';
    problem.setAttribute('role', 'alert');
    problem.innerHTML = errorIcon;
    problem.append(document.createTextNode(error instanceof TypeError ? 'No answer from the server.' : error.message));
    status.firstElementChild.append(problem);
    button.disabled = false;
    button.textContent = label;
  }
}

void window.panels.storedServerAddress().then(value => {
  address.textContent = value ?? '';
  address.title = value ?? '';
});
void refresh();
