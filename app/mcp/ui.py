from __future__ import annotations

from html import escape


APPLICATIONS_UI_URI = "ui://jobtron/applications.html"


_APPLICATIONS_UI_TEMPLATE = r"""
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <style>
    :root {
      color-scheme: light dark;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      --ink: light-dark(#172019, #f0f4ef);
      --muted: light-dark(#657068, #9ba89f);
      --paper: light-dark(#fffdf7, #18201b);
      --canvas: light-dark(#f3f1e9, #101511);
      --line: light-dark(#d7d4c8, #354139);
      --green: light-dark(#245b45, #78c6a3);
      --green-soft: light-dark(#e2ece6, #1d3b2d);
      --amber: light-dark(#8b5a15, #efbd68);
      --amber-soft: light-dark(#fff1d6, #3f301b);
    }
    * { box-sizing: border-box; }
    body { margin: 0; background: var(--canvas); color: var(--ink); }
    button { font: inherit; }
    .shell { max-width: 980px; margin: 0 auto; padding: 22px; }
    .topline { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 18px; }
    .eyebrow { color: var(--green); font-size: 11px; font-weight: 800; letter-spacing: .14em; text-transform: uppercase; }
    .agent-mark { display: inline-flex; align-items: center; gap: 7px; color: var(--muted); font-size: 12px; }
    .agent-mark::before { content: ""; width: 8px; height: 8px; border-radius: 50%; background: var(--green); box-shadow: 0 0 0 4px var(--green-soft); }
    h1 { margin: 0; font-size: clamp(25px, 5vw, 44px); line-height: 1.04; letter-spacing: -.035em; }
    h2 { margin: 0; font-size: 18px; }
    p { margin: 0; }
    .lede { color: var(--muted); margin-top: 8px; max-width: 680px; line-height: 1.5; }
    .panel { background: var(--paper); border: 1px solid var(--line); border-radius: 16px; padding: 20px; margin-top: 18px; }
    .stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(118px, 1fr)); gap: 9px; margin-top: 18px; }
    .stat { background: var(--paper); border: 1px solid var(--line); border-radius: 12px; padding: 13px; }
    .stat strong { display: block; font-size: 23px; }
    .stat span { color: var(--muted); font-size: 12px; }
    .list { display: grid; gap: 10px; margin-top: 14px; }
    .application { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 14px; align-items: center; border-top: 1px solid var(--line); padding: 15px 2px 5px; }
    .application:first-child { border-top: 0; padding-top: 4px; }
    .title { font-weight: 760; }
    .meta { display: flex; flex-wrap: wrap; gap: 7px 12px; color: var(--muted); font-size: 12px; margin-top: 5px; }
    .pill { display: inline-flex; align-items: center; width: max-content; padding: 5px 9px; border-radius: 999px; background: var(--green-soft); color: var(--green); font-size: 11px; font-weight: 750; }
    .tags { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 7px; }
    .tag { display: inline-flex; align-items: center; width: max-content; padding: 3px 8px; border-radius: 999px; border: 1px solid var(--line); color: var(--muted); font-size: 10px; font-weight: 700; }
    .button { border: 0; border-radius: 9px; padding: 10px 13px; background: var(--green); color: var(--canvas); cursor: pointer; font-weight: 750; }
    .button.secondary { background: var(--green-soft); color: var(--green); }
    .button.ghost { background: transparent; border: 1px solid var(--line); color: var(--ink); }
    .actions { display: flex; flex-wrap: wrap; gap: 9px; margin-top: 18px; }
    .hero { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 20px; align-items: start; }
    .progress-number { font-size: 42px; font-weight: 820; letter-spacing: -.04em; text-align: right; }
    .progress-number span { display: block; color: var(--muted); font-size: 11px; letter-spacing: .08em; text-transform: uppercase; }
    .meter { height: 8px; overflow: hidden; background: var(--green-soft); border-radius: 999px; margin-top: 18px; }
    .meter > div { height: 100%; background: var(--green); border-radius: inherit; }
    .grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; margin-top: 14px; }
    .next { border-left: 4px solid var(--amber); background: var(--amber-soft); }
    .next .eyebrow { color: var(--amber); }
    .next-question { margin-top: 7px; font-weight: 730; line-height: 1.45; }
    .checklist { display: grid; gap: 9px; margin-top: 14px; }
    .check { display: grid; grid-template-columns: 20px 1fr auto; gap: 9px; align-items: center; font-size: 13px; }
    .check-mark { width: 18px; height: 18px; display: grid; place-items: center; border-radius: 50%; background: var(--green-soft); color: var(--green); font-size: 11px; font-weight: 900; }
    .check.pending .check-mark { background: var(--amber-soft); color: var(--amber); }
    .check small { color: var(--muted); }
    .empty { color: var(--muted); font-style: italic; padding: 14px 0; }
    .today-row { display: flex; align-items: flex-start; gap: 10px; border-top: 1px solid var(--line); padding: 10px 2px; }
    .today-row:first-child { border-top: 0; padding-top: 0; }
    .today-row .pill { flex: 0 0 auto; }
    .today-row .today-reason { color: var(--muted); font-size: 12px; margin-top: 3px; }
    .notice { color: var(--muted); font-size: 12px; margin-top: 12px; line-height: 1.45; }
    .wizard-options { display: grid; gap: 9px; margin-top: 16px; }
    .wizard-option { display: grid; grid-template-columns: auto 1fr; gap: 10px; align-items: start; padding: 12px; border: 1px solid var(--line); border-radius: 12px; cursor: pointer; }
    .wizard-option:has(input:checked) { border-color: var(--green); background: var(--green-soft); }
    .wizard-option.unavailable { cursor: not-allowed; opacity: .58; }
    .wizard-option input { margin-top: 3px; accent-color: var(--green); }
    .wizard-option strong, .wizard-option small { display: block; }
    .wizard-option small { color: var(--muted); margin-top: 3px; line-height: 1.35; }
    .wizard-group { margin-top: 18px; color: var(--muted); font-size: 11px; font-weight: 800; letter-spacing: .1em; text-transform: uppercase; }
    .wizard-error { margin-top: 12px; color: #a33; font-weight: 700; }
    .wizard-text { width: 100%; min-height: 88px; margin-top: 12px; padding: 10px; border: 1px solid var(--line); border-radius: 10px; background: var(--paper); color: var(--ink); font: inherit; resize: vertical; }
    @media (max-width: 640px) {
      .shell { padding: 15px; }
      .application, .hero { grid-template-columns: 1fr; }
      .application .button { width: 100%; }
      .progress-number { text-align: left; }
      .grid { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <main class="shell" id="app" aria-live="polite">
    <div class="panel"><p class="empty">Jobtron is preparing the review surface…</p><div class="actions"><a class="button secondary" href="__JOBTRON_FRONTEND_URL__/applications" target="_blank" rel="noreferrer">Open the live Jobtron dashboard</a></div></div>
  </main>
  <script>
    const root = document.getElementById("app");
    const pendingRequests = new Map();
    let nextRequestId = 1;
    let latestApplicationData;

    function escapeHtml(value) {
      return String(value ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
    }

    function request(method, params) {
      const id = nextRequestId++;
      window.parent.postMessage({ jsonrpc: "2.0", id, method, params }, "*");
      return new Promise((resolve, reject) => pendingRequests.set(id, { resolve, reject }));
    }

    async function followUp(prompt) {
      const openai = typeof window !== "undefined" ? window.openai : undefined;
      if (openai?.sendFollowUpMessage) return openai.sendFollowUpMessage({ prompt });
      return request("ui/message", {
        role: "user",
        content: [{ type: "text", text: prompt }],
      });
    }

    async function callTool(name, args) {
      const openai = typeof window !== "undefined" ? window.openai : undefined;
      if (openai?.callTool) return openai.callTool(name, args);
      return request("tools/call", { name, arguments: args });
    }

    function structured(result) {
      return result?.structuredContent ?? result?.structured_content ?? result;
    }

    function tagChips(tags) {
      const list = String(tags ?? "").split(",").map((tag) => tag.trim()).filter(Boolean);
      if (!list.length) return "";
      return `<div class="tags">${list.map((tag) => `<span class="tag">${escapeHtml(tag)}</span>`).join("")}</div>`;
    }

    function groupedSourceOptions(options) {
      let group = "";
      return (options.sources ?? []).map((source) => {
        const groupHeading = source.group !== group ? `<div class="wizard-group">${escapeHtml(source.group)}</div>` : "";
        group = source.group;
        return `${groupHeading}<label class="wizard-option ${source.available ? "" : "unavailable"}"><input type="checkbox" name="source" value="${escapeHtml(source.id)}" ${source.recommended ? "checked" : ""} ${source.available ? "" : "disabled"}><span><strong>${escapeHtml(source.label)}</strong><small>${escapeHtml(source.description)}</small></span></label>`;
      }).join("");
    }

    function renderAutofillSources(options) {
      root.innerHTML = `
        ${header("Autofill · step 1 of 2", "Choose source material", "The agent will use only the sources you select.")}
        <section class="panel">
          <div class="wizard-options">${groupedSourceOptions(options) || `<p class="empty">No source material is available yet.</p>`}</div>
          <p class="wizard-error" hidden></p>
          <div class="actions">
            <button class="button" data-autofill-next>Next: choose outputs</button>
            <button class="button ghost" data-autofill-cancel>Cancel</button>
          </div>
        </section>`;
      root._autofillOptions = options;
    }

    function renderAutofillTargets(options, sourceIds) {
      const targets = (options.targets ?? []).map((target) => `
        <label class="wizard-option"><input type="checkbox" name="target" value="${escapeHtml(target.id)}" ${target.recommended ? "checked" : ""}><span><strong>${escapeHtml(target.label)}</strong><small>${escapeHtml(target.description)}</small></span></label>`).join("");
      root.innerHTML = `
        ${header("Autofill · step 2 of 2", "What should the agent complete?", "Choose one or more outputs. Drafts remain unapproved until you review them.")}
        <section class="panel">
          <div class="wizard-options">${targets}</div>
          <textarea class="wizard-text" name="custom_request" placeholder="If you selected another resource, describe it here."></textarea>
          <p class="wizard-error" hidden></p>
          <div class="actions">
            <button class="button" data-autofill-submit>Send plan to agent</button>
            <button class="button ghost" data-autofill-back>Back</button>
            <button class="button ghost" data-autofill-cancel>Cancel</button>
          </div>
        </section>`;
      root._autofillOptions = options;
      root._autofillSources = sourceIds;
    }

    function showWizardError(message) {
      const error = root.querySelector(".wizard-error");
      if (!error) return;
      error.textContent = message;
      error.hidden = false;
    }

    function header(kicker, title, lede) {
      return `
        <div class="topline">
          <span class="eyebrow">${escapeHtml(kicker)}</span>
          <span class="agent-mark">Presented by the agent</span>
        </div>
        <h1>${escapeHtml(title)}</h1>
        <p class="lede">${escapeHtml(lede)}</p>`;
    }

    function renderAttention(attention) {
      const buckets = [
        ["upcoming", "Upcoming"],
        ["follow_up_due", "Follow-up due"],
        ["stale", "Stale"],
        ["idle_drafts", "Idle drafts"],
      ];
      const rows = buckets.flatMap(([key, label]) => (attention?.[key] ?? []).map((row) => ({ ...row, bucketLabel: label })));
      if (!rows.length) return "";
      const items = rows.map((row) => `
        <div class="today-row">
          <span class="pill">${escapeHtml(row.bucketLabel)}</span>
          <div>
            <div class="title">${escapeHtml(row.company_name)} · ${escapeHtml(row.formal_title)}</div>
            <div class="today-reason">${escapeHtml(row.reason)}</div>
            ${tagChips(row.tags)}
          </div>
        </div>`).join("");
      return `<section class="panel"><h2>Today</h2><div class="list">${items}</div></section>`;
    }

    function renderList(data) {
      const applications = data.applications ?? [];
      const counts = data.counts ?? {};
      const stats = Object.entries(counts)
        .map(([status, count]) => `<div class="stat"><strong>${escapeHtml(count)}</strong><span>${escapeHtml(status)}</span></div>`)
        .join("");
      const rows = applications.length
        ? applications.map((item) => `
            <article class="application">
              <div>
                <div class="title">${escapeHtml(item.formal_title)}</div>
                <div class="meta">
                  <span>${escapeHtml(item.company_name)}</span>
                  ${item.location ? `<span>${escapeHtml(item.location)}</span>` : ""}
                  <span class="pill">${escapeHtml(item.status)}</span>
                </div>
                ${tagChips(item.tags)}
              </div>
              <button class="button secondary" data-review-id="${escapeHtml(item.id)}">Review with agent</button>
            </article>`).join("")
        : `<p class="empty">No applications match this view.</p>`;

      root.innerHTML = `
        ${header("Agent workspace", data.title ?? "Your applications", data.summary ?? "A reviewable snapshot of the work Jobtron is managing with you.")}
        ${data.frontend_url ? `<div class="actions"><a class="button secondary" href="${escapeHtml(data.frontend_url)}" target="_blank" rel="noreferrer">Open live frontend</a></div>` : ""}
        ${renderAttention(data.attention)}
        <div class="stats">${stats}</div>
        <section class="panel">
          <h2>${escapeHtml(data.filter_label ?? "Current work")}</h2>
          <div class="list">${rows}</div>
        </section>
        <p class="notice">This surface is for inspection and confirmation. Ask the agent to create, change, or continue an application.</p>`;
    }

    function renderApplication(data) {
      const item = data.application ?? {};
      const progress = data.progress ?? {};
      const next = progress.next_action ?? {};
      const checks = (progress.checks ?? []).filter((check) => check.required);
      const checklist = checks.map((check) => `
        <div class="check ${check.complete ? "" : "pending"}">
          <span class="check-mark">${check.complete ? "✓" : "·"}</span>
          <span>${escapeHtml(check.label)}</span>
          <small>${check.complete ? "Ready" : "Needed"}</small>
        </div>`).join("");
      const questionCount = data.question_count ?? 0;
      const documentCount = data.document_count ?? 0;
      const reusedAnswers = Number(data.reused_answer_count ?? 0);
      const percent = Number(progress.percent_complete ?? 0);
      const nextQuestion = next.question || next.instruction || "No further action is needed right now.";

      root.innerHTML = `
        ${header(data.reason_label ?? "Ready for review", item.formal_title ?? "Application", `${item.company_name ?? ""}${item.location ? ` · ${item.location}` : ""}`)}
        <section class="panel">
          <div class="hero">
            <div>
              <span class="pill">${escapeHtml(item.status ?? "Draft")}</span>
              ${tagChips(item.tags)}
              <p class="lede">${escapeHtml(data.review_summary ?? "Jobtron assembled the current authoritative state for your confirmation.")}</p>
            </div>
            <div class="progress-number">${escapeHtml(percent)}%<span>package ready</span></div>
          </div>
          <div class="meter" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${escapeHtml(percent)}"><div style="width:${Math.max(0, Math.min(100, percent))}%"></div></div>
        </section>
        <section class="panel next">
          <span class="eyebrow">Agent's next move</span>
          <p class="next-question">${escapeHtml(nextQuestion)}</p>
        </section>
        <div class="grid">
          <section class="panel">
            <h2>Readiness</h2>
            <div class="checklist">${checklist || `<p class="empty">No required checks.</p>`}</div>
          </section>
          <section class="panel">
            <h2>Package</h2>
            <div class="stats">
              <div class="stat"><strong>${escapeHtml(questionCount)}</strong><span>questions</span></div>
              <div class="stat"><strong>${escapeHtml(documentCount)}</strong><span>documents</span></div>
            </div>
            ${reusedAnswers ? `<p class="notice">${escapeHtml(reusedAnswers)} answer(s) reused from your question bank.</p>` : ""}
            <p class="notice">${escapeHtml((progress.blocking_items ?? []).length ? `${progress.blocking_items.length} item(s) still need attention.` : "All required package checks are complete.")}</p>
          </section>
        </div>
        <section class="panel">
          <h2>Your confirmation</h2>
          <p class="lede">Choose the direction; the agent will perform the next action and return an updated review surface.</p>
          <div class="actions">
            <button class="button" data-autofill-start="${escapeHtml(item.id)}">Autofill application</button>
            <button class="button" data-follow-up="Continue application ${escapeHtml(item.id)}. I reviewed this state and approve you taking the next proposed step.">Looks good — continue</button>
            <button class="button ghost" data-follow-up="I want to revise application ${escapeHtml(item.id)} before you continue. Ask me what should change.">Request a revision</button>
            <button class="button secondary" data-follow-up="Show me all of my Jobtron applications.">Show all applications</button>
            ${data.frontend_url ? `<a class="button secondary" href="${escapeHtml(data.frontend_url)}" target="_blank" rel="noreferrer">Open live frontend</a>` : ""}
          </div>
        </section>`;
    }

    function renderSetup(data) {
      const readiness = data.readiness ?? {};
      const items = [
        {
          label: "Master resume uploaded and made active",
          complete: Boolean(readiness.master_resume_available),
          hint: "Profile → Master resume → Upload new version",
        },
        {
          label: "Basic profile information filled in",
          complete: Boolean(readiness.profile_complete),
          hint: (readiness.missing_profile_fields ?? []).length
            ? `Still needed: ${(readiness.missing_profile_fields ?? []).join(", ")}`
            : "Name, email, phone, and location are saved",
        },
      ];
      const checklist = items.map((item) => `
        <div class="check ${item.complete ? "" : "pending"}">
          <span class="check-mark">${item.complete ? "✓" : "·"}</span>
          <span>${escapeHtml(item.label)}<br><small>${escapeHtml(item.hint)}</small></span>
          <small>${item.complete ? "Done" : "Needed"}</small>
        </div>`).join("");

      root.innerHTML = `
        ${header("Setup required", data.title ?? "Finish your Jobtron setup", data.summary ?? "")}
        <section class="panel next">
          <span class="eyebrow">Why this is blocking</span>
          <p class="next-question">Jobtron will only create drafts until these are done. It never invents facts about you, so it needs your master resume and contact details before it can write a tailored resume, a cover letter, or an application answer.</p>
        </section>
        <section class="panel">
          <h2>What Jobtron needs</h2>
          <div class="checklist">${checklist}</div>
          <div class="actions">
            ${data.frontend_url ? `<a class="button" href="${escapeHtml(data.frontend_url)}" target="_blank" rel="noreferrer">Open setup and upload</a>` : ""}
            <button class="button secondary" data-follow-up="I finished my Jobtron setup. Check candidate.readiness again and continue.">I'm done — check again</button>
          </div>
          <p class="notice">Upload happens in the browser; the agent cannot receive files directly. Once the page shows your resume as active, come back and tell the agent you are done.</p>
        </section>`;
    }

    function render(data) {
      if (!data) return;
      if (data.view === "setup") { renderSetup(data); }
      else if (data.view === "application") { latestApplicationData = data; renderApplication(data); }
      else renderList(data);
    }

    window.addEventListener("message", (event) => {
      if (event.source !== window.parent) return;
      const message = event.data;
      if (!message || message.jsonrpc !== "2.0") return;
      if (message.id !== undefined && pendingRequests.has(message.id)) {
        const pending = pendingRequests.get(message.id);
        pendingRequests.delete(message.id);
        if (message.error) pending.reject(message.error);
        else pending.resolve(message.result);
        return;
      }
      if (message.method === "ui/notifications/tool-result") {
        render(message.params?.structuredContent);
      }
    }, { passive: true });

    root.addEventListener("click", async (event) => {
      const button = event.target.closest("button");
      if (!button) return;
      button.disabled = true;
      try {
        if (button.dataset.reviewId) {
          await followUp(`Show me application ${button.dataset.reviewId} in Jobtron so I can review it.`);
        } else if (button.dataset.autofillStart) {
          root.innerHTML = `<section class="panel"><p class="empty">Loading current Jobtron sources…</p></section>`;
          const result = await callTool("applications.autofill_options", { application_id: Number(button.dataset.autofillStart) });
          renderAutofillSources(structured(result));
        } else if (button.hasAttribute("data-autofill-next")) {
          const sourceIds = [...root.querySelectorAll('[name="source"]:checked')].map((input) => input.value);
          if (!sourceIds.length) showWizardError("Select at least one source.");
          else renderAutofillTargets(root._autofillOptions, sourceIds);
        } else if (button.hasAttribute("data-autofill-back")) {
          renderAutofillSources(root._autofillOptions);
        } else if (button.hasAttribute("data-autofill-cancel")) {
          render(latestApplicationData);
        } else if (button.hasAttribute("data-autofill-submit")) {
          const targetIds = [...root.querySelectorAll('[name="target"]:checked')].map((input) => input.value);
          const result = await callTool("applications.prepare_autofill", {
            application_id: root._autofillOptions.application_id,
            source_ids: root._autofillSources,
            target_ids: targetIds,
            custom_request: root.querySelector('[name="custom_request"]').value,
          });
          const plan = structured(result);
          if (!plan.valid) showWizardError((plan.errors ?? ["Complete the required selections."]).join(" "));
          else {
            await followUp(plan.agent_prompt);
            root.innerHTML = `${header("Autofill requested", "The agent has your plan", "Jobtron will use the selected sources, save reviewable drafts, and return the updated application.")}<section class="panel"><p class="notice">You can continue in the conversation while the agent works.</p></section>`;
          }
        } else if (button.dataset.followUp) {
          await followUp(button.dataset.followUp);
        }
      } finally {
        button.disabled = false;
      }
    });

    if (window.openai?.toolOutput) render(window.openai.toolOutput);
    request("ui/initialize", {
      appInfo: { name: "jobtron6900", version: "0.1.0" },
      capabilities: {},
    }).catch(() => {});
  </script>
</body>
</html>
""".strip()


def build_applications_ui(frontend_base_url: str) -> str:
    return _APPLICATIONS_UI_TEMPLATE.replace(
        "__JOBTRON_FRONTEND_URL__", escape(frontend_base_url.rstrip("/"), quote=True)
    )


APPLICATIONS_UI = build_applications_ui("http://127.0.0.1:8765")
