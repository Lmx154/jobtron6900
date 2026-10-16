(() => {
  const form = document.querySelector('[data-draft-form]');
  if (!form) return;
  const error = document.querySelector('[data-draft-error]');
  const result = document.querySelector('[data-draft-result]');
  const prompt = document.querySelector('#agent-request');
  form.addEventListener('submit', async event => {
    event.preventDefault();
    const button = form.querySelector('button');
    button.disabled = true; error.hidden = true; result.hidden = true;
    try {
      const response = await fetch(form.action, { method: 'POST', headers: { Accept: 'application/json' } });
      const plan = await response.json();
      if (!response.ok || !plan.valid) throw new Error((plan.errors || [plan.detail || 'Could not prepare the request.']).join(' '));
      prompt.value = plan.agent_prompt; result.hidden = false; prompt.focus();
    } catch (failure) {
      error.textContent = failure.message || 'Could not prepare the request. Try again.'; error.hidden = false;
    } finally { button.disabled = false; }
  });
  document.querySelector('[data-copy-request]').addEventListener('click', async () => {
    const status = document.querySelector('[data-copy-status]');
    try { await navigator.clipboard.writeText(prompt.value); status.textContent = 'Copied. Paste this into your connected agent conversation.'; }
    catch (_) { prompt.focus(); prompt.select(); status.textContent = 'Select and copy the request with your keyboard.'; }
  });
})();
