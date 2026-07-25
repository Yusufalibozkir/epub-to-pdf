(() => {
  const state = {
    activeJobId: null,
    pollTimer: null,
  };

  const $ = (id) => document.getElementById(id);

  function boolField(id) {
    return $(id).checked;
  }

  async function fetchJSON(url, options) {
    const res = await fetch(url, options);
    if (!res.ok) {
      let detail = res.statusText;
      try {
        const body = await res.json();
        detail = body.detail || JSON.stringify(body);
      } catch (_) {}
      throw new Error(detail);
    }
    return res.json();
  }

  async function loadConfigs() {
    const data = await fetchJSON("/api/configs");
    const select = $("config");
    select.innerHTML = "";
    const configs = data.configs.length ? data.configs : [{ name: "A4.yaml", path: "A4.yaml" }];
    for (const cfg of configs) {
      const opt = document.createElement("option");
      opt.value = cfg.path;
      opt.textContent = cfg.name;
      if (cfg.name === "A4.yaml") opt.selected = true;
      select.appendChild(opt);
    }
  }

  function renderJobList(jobs) {
    const list = $("job-list");
    if (!jobs.length) {
      list.innerHTML = '<p class="fineprint">No jobs yet.</p>';
      return;
    }
    list.innerHTML = "";
    for (const job of jobs) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "job-item" + (job.id === state.activeJobId ? " active" : "");
      btn.innerHTML = `<span class="jid">${job.status}</span><span class="jname">${job.epub_name || job.id}</span>`;
      btn.addEventListener("click", () => selectJob(job.id));
      list.appendChild(btn);
    }
  }

  function renderDetail(job) {
    $("job-stage").textContent = job.stage || "—";
    $("job-meta").textContent = [
      job.title || job.epub_name,
      job.page_count ? `${job.page_count} pages` : null,
      job.error || null,
    ].filter(Boolean).join(" · ");

    const status = $("job-status");
    status.textContent = job.status;
    status.className = job.status;

    $("progress-bar").style.width = `${Math.round((job.progress || 0) * 100)}%`;
    $("job-log").textContent = (job.log_tail || []).join("\n");
    $("job-log").scrollTop = $("job-log").scrollHeight;

    const actions = $("review-actions");
    const grid = $("qa-grid");
    if (job.status !== "succeeded" && job.status !== "failed") {
      actions.innerHTML = "<p>Build in progress…</p>";
      grid.innerHTML = "";
      return;
    }

    const bits = [];
    if (job.has_pdf) {
      bits.push(`<a class="cta solid" href="/api/jobs/${job.id}/pdf">Download PDF</a>`);
    }
    if (job.has_qa_report) {
      bits.push(`<a class="cta" href="/api/jobs/${job.id}/qa-report">QA report</a>`);
    }
    actions.innerHTML = bits.join("") || "<p>No artifacts yet.</p>";

    grid.innerHTML = "";
    for (const name of job.qa_images || []) {
      const a = document.createElement("a");
      a.href = `/api/jobs/${job.id}/qa/${encodeURIComponent(name)}`;
      a.target = "_blank";
      a.rel = "noopener";
      a.innerHTML = `<img src="/api/jobs/${job.id}/qa/${encodeURIComponent(name)}" alt="${name}" /><span>${name}</span>`;
      grid.appendChild(a);
    }
  }

  async function selectJob(jobId) {
    state.activeJobId = jobId;
    const job = await fetchJSON(`/api/jobs/${jobId}`);
    const jobs = (await fetchJSON("/api/jobs")).jobs;
    renderJobList(jobs);
    renderDetail(job);
    if (job.status === "running" || job.status === "queued") startPolling();
  }

  async function refreshJobs() {
    const data = await fetchJSON("/api/jobs");
    renderJobList(data.jobs);
    if (!state.activeJobId && data.jobs[0]) state.activeJobId = data.jobs[0].id;
    if (state.activeJobId) {
      const active = data.jobs.find((j) => j.id === state.activeJobId);
      if (active) {
        const full = await fetchJSON(`/api/jobs/${state.activeJobId}`);
        renderDetail(full);
        if (full.status === "running" || full.status === "queued") startPolling();
        else stopPolling();
      }
    }
  }

  function startPolling() {
    stopPolling();
    state.pollTimer = setInterval(refreshJobs, 1500);
  }

  function stopPolling() {
    if (state.pollTimer) {
      clearInterval(state.pollTimer);
      state.pollTimer = null;
    }
  }

  $("btn-refresh-jobs").addEventListener("click", () => {
    refreshJobs().catch((err) => alert(err.message));
  });

  const dropzone = $("dropzone");
  const epubInput = $("epub");
  epubInput.addEventListener("change", () => {
    $("epub-label").textContent = epubInput.files[0]?.name || "Choose a file or drop it here";
  });
  ["dragenter", "dragover"].forEach((evt) => {
    dropzone.addEventListener(evt, (e) => {
      e.preventDefault();
      dropzone.classList.add("drag");
    });
  });
  ["dragleave", "drop"].forEach((evt) => {
    dropzone.addEventListener(evt, (e) => {
      e.preventDefault();
      dropzone.classList.remove("drag");
    });
  });
  dropzone.addEventListener("drop", (e) => {
    const file = e.dataTransfer.files?.[0];
    if (!file) return;
    const dt = new DataTransfer();
    dt.items.add(file);
    epubInput.files = dt.files;
    $("epub-label").textContent = file.name;
  });

  $("job-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const file = epubInput.files?.[0];
    if (!file) {
      alert("Choose an EPUB first.");
      return;
    }
    const fd = new FormData();
    fd.append("epub", file);
    fd.append("title", $("title").value);
    fd.append("author", $("author").value);
    fd.append("config", $("config").value);
    fd.append("toc_mode", $("toc_mode").value);
    fd.append("volume_mode", $("volume_mode").value);
    fd.append("sample_pages", $("sample_pages").value || "0");
    fd.append("section", $("section").value);
    fd.append("out_name", $("out_name").value);
    fd.append("ai_provider", $("ai_provider").value);
    fd.append("use_openai", boolField("use_openai") ? "true" : "false");
    fd.append("openai_qa", boolField("openai_qa") ? "true" : "false");
    fd.append("keep_all_images", boolField("keep_all_images") ? "true" : "false");
    fd.append("remove_all_images", boolField("remove_all_images") ? "true" : "false");
    fd.append("strict", boolField("strict") ? "true" : "false");
    fd.append("debug_html", boolField("debug_html") ? "true" : "false");

    const btn = $("btn-run");
    btn.disabled = true;
    $("form-hint").textContent = "Starting job…";
    try {
      const job = await fetchJSON("/api/jobs", { method: "POST", body: fd });
      state.activeJobId = job.id;
      $("form-hint").textContent = `Job ${job.id} running.`;
      document.getElementById("jobs").scrollIntoView({ behavior: "smooth" });
      await refreshJobs();
      startPolling();
    } catch (err) {
      alert(err.message || String(err));
      $("form-hint").textContent = "Build failed to start.";
    } finally {
      btn.disabled = false;
    }
  });

  loadConfigs()
    .then(refreshJobs)
    .catch((err) => {
      $("form-hint").textContent = `UI boot error: ${err.message}`;
    });
})();
