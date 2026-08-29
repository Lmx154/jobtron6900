(() => {
  const form = document.querySelector("#asset-upload");
  const input = document.querySelector("#asset-file");
  if (form && input) {
    const title = form.querySelector('[name="title"]');
    const setFile = (file) => {
      const transfer = new DataTransfer(); transfer.items.add(file); input.files = transfer.files;
      if (!title.value) title.value = file.name || `Screenshot ${new Date().toLocaleString()}`;
      form.classList.add("has-file");
    };
    document.addEventListener("paste", (event) => {
      const file = [...(event.clipboardData?.files || [])].find(item => item.type.startsWith("image/"));
      if (file) { event.preventDefault(); setFile(new File([file], `screenshot-${Date.now()}.png`, {type:file.type})); }
    });
    form.addEventListener("dragover", event => { event.preventDefault(); form.classList.add("dragging"); });
    form.addEventListener("dragleave", () => form.classList.remove("dragging"));
    form.addEventListener("drop", event => { event.preventDefault(); form.classList.remove("dragging"); if (event.dataTransfer.files[0]) setFile(event.dataTransfer.files[0]); });
  }

  const dialog = document.querySelector("#autofill-dialog");
  const wizard = document.querySelector("#autofill-form");
  const openWizard = document.querySelector("#autofill-open");
  if (!dialog || !wizard || !openWizard) return;

  const showStep = (name) => {
    wizard.querySelectorAll("[data-wizard-step]").forEach(step => { step.hidden = step.dataset.wizardStep !== name; });
    wizard.querySelectorAll(".wizard-error").forEach(error => { error.hidden = true; error.textContent = ""; });
  };
  const showError = (step, message) => {
    const error = wizard.querySelector(`[data-wizard-step="${step}"] .wizard-error`);
    error.textContent = message; error.hidden = false;
  };

  openWizard.addEventListener("click", () => { showStep("sources"); dialog.showModal(); });
  wizard.querySelectorAll("[data-wizard-close]").forEach(button => button.addEventListener("click", () => dialog.close()));
  wizard.querySelector("[data-wizard-next]").addEventListener("click", () => {
    if (!wizard.querySelector('[name="sources"]:checked')) showError("sources", "Select at least one source.");
    else showStep("targets");
  });
  wizard.querySelector("[data-wizard-back]").addEventListener("click", () => showStep("sources"));
  wizard.addEventListener("submit", async (event) => {
    event.preventDefault();
    const submit = wizard.querySelector('[type="submit"]');
    submit.disabled = true;
    try {
      const response = await fetch(wizard.action, {method: "POST", body: new FormData(wizard)});
      const plan = await response.json();
      if (!plan.valid) { showError("targets", (plan.errors || ["Complete the required selections."]).join(" ")); return; }
      wizard.querySelector("#autofill-agent-request").value = plan.agent_prompt;
      showStep("result");
    } catch (_) {
      showError("targets", "The autofill plan could not be prepared. Try again.");
    } finally {
      submit.disabled = false;
    }
  });
  wizard.querySelector("[data-copy-agent-request]").addEventListener("click", async (event) => {
    const request = wizard.querySelector("#autofill-agent-request");
    await navigator.clipboard.writeText(request.value);
    event.currentTarget.textContent = "Copied";
  });
})();
