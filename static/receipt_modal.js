(function () {
  const root = document.querySelector("[data-receipt-modal-root]");
  if (!root) {
    return;
  }

  const STORAGE_KEY = "receiptModalActiveJobV2";
  const TOAST_KEY = "receiptModalToastV2";
  const POLL_INTERVAL_MS = 1500;
  const startUrl = root.dataset.startUrl;
  const statusUrlTemplate = root.dataset.statusUrlTemplate;
  const createUrlTemplate = root.dataset.createUrlTemplate;
  const openBtn = root.querySelector("[data-receipt-open]");
  const shell = root.querySelector("[data-receipt-shell]");
  const closeButtons = root.querySelectorAll("[data-receipt-close]");
  const fileInput = root.querySelector("[data-receipt-input]");
  const selectionNote = root.querySelector("[data-receipt-selection-note]");
  const selectedFileList = root.querySelector("[data-receipt-selected-files]");
  const startBtn = root.querySelector("[data-receipt-start]");
  const resetBtn = root.querySelector("[data-receipt-reset]");
  const summary = root.querySelector("[data-receipt-summary]");
  const stepNodes = root.querySelectorAll("[data-receipt-step]");
  const stepBadges = root.querySelectorAll("[data-receipt-step-badge]");
  const parseList = root.querySelector("[data-receipt-parse-list]");
  const detailPane = root.querySelector("[data-receipt-detail-pane]");
  const goResultBtn = root.querySelector("[data-receipt-go-result]");
  const backToUploadBtn = root.querySelector("[data-receipt-back-upload]");
  const backToParsingBtn = root.querySelector("[data-receipt-back-parsing]");
  const accountWrap = root.querySelector("[data-receipt-account-wrap]");
  const accountSelect = root.querySelector("[data-receipt-account-select]");
  const resultSummary = root.querySelector("[data-receipt-result-summary]");
  const resultList = root.querySelector("[data-receipt-result-list]");
  const createBtn = root.querySelector("[data-receipt-create]");
  const toast = root.querySelector("[data-receipt-toast]");
  const toastMessage = root.querySelector("[data-receipt-toast-message]");
  const toastOpen = root.querySelector("[data-receipt-toast-open]");
  const toastClose = root.querySelector("[data-receipt-toast-close]");

  const state = {
    files: [],
    jobId: null,
    job: null,
    items: [],
    accounts: [],
    activeItemId: null,
    selectedAccountId: "",
    localEdits: {},
    result: null,
    busy: false,
    currentStep: 1,
    pollTimer: null,
  };

  function storageGet(key) {
    try {
      return window.localStorage.getItem(key);
    } catch (_error) {
      return null;
    }
  }

  function storageSet(key, value) {
    try {
      window.localStorage.setItem(key, value);
    } catch (_error) {
      // ignore
    }
  }

  function storageRemove(key) {
    try {
      window.localStorage.removeItem(key);
    } catch (_error) {
      // ignore
    }
  }

  function escapeHtml(value) {
    return String(value || "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function replaceJobId(template, jobId) {
    return String(template || "").replace("__JOB_ID__", jobId);
  }

  function normalizeText(value) {
    const text = String(value || "").trim();
    return text || "알수없음";
  }

  function normalizeAmount(value) {
    const raw = String(value ?? "").trim();
    if (!raw) {
      return "알수없음";
    }
    const num = Number(String(raw).replaceAll(",", ""));
    if (!Number.isFinite(num) || num <= 0) {
      return "알수없음";
    }
    return `${num.toLocaleString("ko-KR")}원`;
  }

  function normalizeDatetime(item) {
    const date = String(item?.occurred_on || "").trim();
    const time = String(item?.occurred_time || "").trim();
    if (!date && !time) {
      return "알수없음";
    }
    if (!date) {
      return time;
    }
    if (!time) {
      return date;
    }
    return `${date} ${time}`;
  }

  function openModal() {
    shell.hidden = false;
    document.body.classList.add("receipt-modal-open");
  }

  function closeModal() {
    shell.hidden = true;
    document.body.classList.remove("receipt-modal-open");
  }

  function showStep(step) {
    state.currentStep = step;
    stepNodes.forEach((node) => {
      node.hidden = Number(node.dataset.receiptStep) !== step;
    });
    stepBadges.forEach((badge) => {
      badge.classList.toggle("is-active", Number(badge.dataset.receiptStepBadge) === step);
    });
  }

  function setSummary(message, tone) {
    if (!message) {
      summary.hidden = true;
      summary.textContent = "";
      summary.className = "receipt-summary-banner";
      return;
    }
    summary.hidden = false;
    summary.textContent = message;
    summary.className = `receipt-summary-banner ${tone || ""}`.trim();
  }

  function setBusy(value) {
    state.busy = value;
    startBtn.disabled = value || !state.files.length;
    createBtn.disabled = value || !getCreatableItems().length;
  }

  function clearPolling() {
    if (state.pollTimer) {
      window.clearInterval(state.pollTimer);
      state.pollTimer = null;
    }
  }

  function schedulePolling() {
    clearPolling();
    if (!state.jobId) {
      return;
    }
    state.pollTimer = window.setInterval(() => {
      void pollJob(false);
    }, POLL_INTERVAL_MS);
  }

  function showToast(message) {
    toast.hidden = false;
    toastMessage.textContent = message;
  }

  function hideToast() {
    toast.hidden = true;
    toastMessage.textContent = "";
  }

  function renderSelectedFiles() {
    if (!state.files.length) {
      selectionNote.textContent = "선택된 파일이 없습니다.";
      selectedFileList.innerHTML = "<div class=\"small-note muted2\">파일을 올리면 파일명.형식 목록이 여기에 표시됩니다.</div>";
      startBtn.disabled = true;
      return;
    }

    selectionNote.textContent = `${state.files.length}개 파일 선택됨`;
    selectedFileList.innerHTML = state.files
      .map((file) => `<div class="receipt-uploaded-file">${escapeHtml(file.name)}</div>`)
      .join("");
    startBtn.disabled = state.busy;
  }

  function resetModalState() {
    state.files = [];
    state.jobId = null;
    state.job = null;
    state.items = [];
    state.accounts = [];
    state.activeItemId = null;
    state.selectedAccountId = "";
    state.localEdits = {};
    state.result = null;
    fileInput.value = "";
    accountSelect.innerHTML = '<option value="">계좌 미지정으로 생성</option>';
    accountWrap.hidden = true;
    parseList.innerHTML = '<div class="receipt-empty-state"><div class="strong">파싱이 시작되면 이미지 목록이 여기에 표시됩니다.</div><div class="small-note muted2">목록을 누르면 매장 명, 날짜와 시간, 금액 같은 값을 확인할 수 있습니다.</div></div>';
    detailPane.innerHTML = '<div class="receipt-empty-state"><div class="strong">이미지 항목을 선택해 주세요.</div><div class="small-note muted2">파싱 전에는 파일명만 보이고, 완료되면 값이 채워집니다.</div></div>';
    resultSummary.innerHTML = '<div class="receipt-empty-state"><div class="strong">파싱이 끝나면 입력 결과를 요약해서 보여줍니다.</div></div>';
    resultList.innerHTML = "";
    goResultBtn.disabled = true;
    hideToast();
    setSummary("", "");
    renderSelectedFiles();
    showStep(1);
    clearPolling();
    storageRemove(STORAGE_KEY);
  }

  function mergeLocalEdits(items) {
    return items.map((item) => ({
      ...item,
      ...(state.localEdits[item.item_id] || {}),
    }));
  }

  function getActiveItem() {
    return state.items.find((item) => item.item_id === state.activeItemId) || null;
  }

  function getCreatableItems() {
    return state.items.filter((item) => item.status === "ready");
  }

  function renderAccounts() {
    if (!state.accounts.length) {
      accountWrap.hidden = true;
      accountSelect.innerHTML = '<option value="">계좌 미지정으로 생성</option>';
      return;
    }

    accountWrap.hidden = false;
    accountSelect.innerHTML = '<option value="">계좌 미지정으로 생성</option>';
    state.accounts.forEach((account) => {
      const option = document.createElement("option");
      option.value = String(account.id);
      option.textContent = account.label;
      if (String(account.id) === String(state.selectedAccountId || "")) {
        option.selected = true;
      }
      accountSelect.appendChild(option);
    });
  }

  function renderParseList() {
    if (!state.items.length) {
      parseList.innerHTML = '<div class="receipt-empty-state"><div class="strong">파싱 중인 영수증이 없습니다.</div></div>';
      return;
    }

    parseList.innerHTML = state.items
      .map((item) => {
        const statusClass = `receipt-status-${item.status}`;
        const activeClass = item.item_id === state.activeItemId ? "is-active" : "";
        const metaText = item.status === "error"
          ? "파싱 실패"
          : item.status === "created"
            ? "거래 생성 완료"
            : item.status === "ready"
              ? "파싱 완료"
              : "파싱 중";

        return `
          <button type="button" class="receipt-parse-item ${activeClass}" data-receipt-item="${escapeHtml(item.item_id)}">
            <div class="receipt-parse-item-copy">
              <div class="strong">${escapeHtml(item.filename)}</div>
              <div class="small-note muted2">${escapeHtml(metaText)}</div>
            </div>
            <span class="receipt-status-indicator ${statusClass}" aria-hidden="true"></span>
          </button>
        `;
      })
      .join("");
  }

  function renderDetail() {
    const item = getActiveItem();
    if (!item) {
      detailPane.innerHTML = '<div class="receipt-empty-state"><div class="strong">이미지 항목을 선택해 주세요.</div></div>';
      return;
    }

    if (item.status === "error") {
      detailPane.innerHTML = `
        <div class="receipt-detail-card">
          <div class="receipt-detail-head">
            <div>
              <div class="strong">${escapeHtml(item.filename)}</div>
              <div class="small-note muted2">파싱 실패 항목</div>
            </div>
            <span class="badge bad">실패</span>
          </div>
          <div class="receipt-inline-error">${escapeHtml(item.error || "파싱에 실패했습니다.")}</div>
        </div>
      `;
      return;
    }

    const disabled = item.status !== "ready" ? "disabled" : "";
    const note = item.status === "ready"
      ? "알수없는 값은 비워 두었고, 필요하면 직접 수정할 수 있습니다."
      : "현재 파싱 중입니다. 완료되면 값이 채워지고 수정할 수 있습니다.";

    detailPane.innerHTML = `
      <div class="receipt-detail-card">
        <div class="receipt-detail-head">
          <div>
            <div class="strong">${escapeHtml(item.filename)}</div>
            <div class="small-note muted2">${escapeHtml(note)}</div>
          </div>
          <span class="badge ${item.status === "ready" ? "ok" : "warn"}">${item.status === "ready" ? "완료" : "대기"}</span>
        </div>

        <div class="receipt-form-grid receipt-form-grid-single">
          <label class="field">
            <span class="label">매장 명</span>
            <input type="text" data-field="counterparty" value="${escapeHtml(item.counterparty || "")}" placeholder="알수없음" ${disabled}>
          </label>
          <div class="receipt-date-time-grid">
            <label class="field">
              <span class="label">날짜</span>
              <input type="date" data-field="occurred_on" value="${escapeHtml(item.occurred_on || "")}" ${disabled}>
            </label>
            <label class="field">
              <span class="label">시간</span>
              <input type="time" data-field="occurred_time" value="${escapeHtml(item.occurred_time || "")}" ${disabled}>
            </label>
          </div>
          <label class="field">
            <span class="label">결제 금액</span>
            <input type="number" min="1" step="1" data-field="amount_krw" value="${escapeHtml(item.amount_krw || "")}" placeholder="알수없음" ${disabled}>
          </label>
          <label class="field">
            <span class="label">결제 항목</span>
            <input type="text" data-field="payment_item" value="${escapeHtml(item.payment_item || "")}" placeholder="알수없음" ${disabled}>
          </label>
          <label class="field">
            <span class="label">결제 카드 및 계좌번호</span>
            <input type="text" data-field="payment_method" value="${escapeHtml(item.payment_method || "")}" placeholder="알수없음" ${disabled}>
          </label>
          <label class="field">
            <span class="label">메모</span>
            <input type="text" data-field="memo" value="${escapeHtml(item.memo || "")}" placeholder="알수없음" ${disabled}>
          </label>
          <label class="field">
            <span class="label">업무용 여부</span>
            <select data-field="usage" ${disabled}>
              <option value="business" ${item.usage === "business" ? "selected" : ""}>업무용</option>
              <option value="personal" ${item.usage === "personal" ? "selected" : ""}>개인용</option>
              <option value="unknown" ${item.usage === "unknown" ? "selected" : ""}>나중에 검토</option>
            </select>
          </label>
        </div>

        ${Array.isArray(item.warnings) && item.warnings.length ? `<div class="receipt-warning-list">${item.warnings.map((warning) => `<div class="receipt-warning-item">${escapeHtml(warning)}</div>`).join("")}</div>` : ""}
      </div>
    `;
  }

  function renderResult() {
    if (!state.job) {
      resultSummary.innerHTML = '<div class="receipt-empty-state"><div class="strong">진행 중인 영수증 작업이 없습니다.</div></div>';
      resultList.innerHTML = "";
      createBtn.disabled = true;
      return;
    }

    const readyCount = state.items.filter((item) => item.status === "ready").length;
    const processingCount = state.items.filter((item) => item.status === "queued" || item.status === "processing").length;
    const errorCount = state.items.filter((item) => item.status === "error").length;
    const createdCount = state.items.filter((item) => item.status === "created").length;

    resultSummary.innerHTML = `
      <div class="receipt-result-grid">
        <div class="receipt-result-stat"><span class="small-note muted2">업로드</span><strong>${state.items.length}건</strong></div>
        <div class="receipt-result-stat"><span class="small-note muted2">파싱 완료</span><strong>${readyCount + createdCount}건</strong></div>
        <div class="receipt-result-stat"><span class="small-note muted2">확인 필요</span><strong>${processingCount}건</strong></div>
        <div class="receipt-result-stat"><span class="small-note muted2">실패</span><strong>${errorCount}건</strong></div>
      </div>
      <div class="small-note muted2" style="margin-top:10px;">이 화면은 파싱 후 입력 결과를 요약해서 보여줍니다. 생성 전에 정리하기 화면에서 바로 이어질 항목만 다시 확인하면 됩니다.</div>
    `;

    const resultItems = state.items.filter((item) => item.status !== "error");
    if (!resultItems.length) {
      resultList.innerHTML = '<div class="receipt-empty-state"><div class="strong">생성 가능한 항목이 없습니다.</div><div class="small-note muted2">파싱 실패 항목을 제외하고 다시 업로드해 주세요.</div></div>';
    } else {
      resultList.innerHTML = resultItems
        .map((item) => `
          <article class="receipt-result-card ${item.status === "created" ? "is-created" : ""}">
            <div class="receipt-result-card-head">
              <div>
                <div class="strong">${escapeHtml(item.filename)}</div>
                <div class="small-note muted2">${escapeHtml(normalizeText(item.counterparty))}</div>
              </div>
              <span class="badge ${item.status === "created" ? "ok" : item.status === "ready" ? "warn" : "ghost"}">${item.status === "created" ? "생성됨" : item.status === "ready" ? "생성 대기" : "진행 중"}</span>
            </div>
            <div class="receipt-result-meta">
              <span>날짜 및 시간: ${escapeHtml(normalizeDatetime(item))}</span>
              <span>결제 금액: ${escapeHtml(normalizeAmount(item.amount_krw))}</span>
              <span>결제 항목: ${escapeHtml(normalizeText(item.payment_item))}</span>
              <span>결제 카드/계좌: ${escapeHtml(normalizeText(item.payment_method))}</span>
            </div>
          </article>
        `)
        .join("");
    }

    renderAccounts();

    if (state.result && state.result.created_count) {
      const failedCount = Number(state.result.failed_count || 0);
      setSummary(
        failedCount
          ? `${state.result.created_count}건을 생성했고 ${failedCount}건은 다시 확인이 필요합니다.`
          : `${state.result.created_count}건 거래 생성과 증빙 첨부를 마쳤습니다.`,
        failedCount ? "warn" : "good"
      );
    }

    createBtn.disabled = state.busy || !getCreatableItems().length;
  }

  function applyJobSnapshot(job) {
    const previousComplete = Boolean(state.job && state.job.is_complete);
    state.job = job;
    state.result = job.last_result || state.result;
    state.items = mergeLocalEdits(Array.isArray(job.items) ? job.items : []);

    if (!state.activeItemId || !state.items.some((item) => item.item_id === state.activeItemId)) {
      const preferred = state.items.find((item) => item.status === "processing")
        || state.items.find((item) => item.status === "ready")
        || state.items[0]
        || null;
      state.activeItemId = preferred ? preferred.item_id : null;
    }

    renderParseList();
    renderDetail();
    renderResult();

    goResultBtn.disabled = !job.is_complete;

    if (!job.is_complete) {
      setSummary("영수증을 백그라운드에서 파싱하고 있습니다. 다른 화면으로 이동해도 완료되면 알림을 보여줍니다.", "info");
    } else if (!previousComplete) {
      setSummary("파싱이 끝났습니다. 결과 확인 단계에서 한 번 더 검토한 뒤 생성할 수 있습니다.", "good");
      const notifiedJobId = storageGet(TOAST_KEY);
      if (notifiedJobId !== job.job_id) {
        storageSet(TOAST_KEY, job.job_id);
        showToast("영수증 파싱이 끝났습니다. 결과를 확인해 주세요.");
      }
    }

    if (job.last_result && job.last_result.created_count) {
      storageRemove(STORAGE_KEY);
    }
  }

  async function readApiPayload(response) {
    const contentType = response.headers.get("content-type") || "";
    if (contentType.includes("application/json")) {
      return response.json();
    }

    const text = await response.text();
    if (response.status === 413) {
      return {
        ok: false,
        error: "업로드 전체 용량이 너무 큽니다. 파일 수를 줄이거나 나눠서 올려 주세요.",
      };
    }

    throw new Error(text || "요청 처리 중 문제가 발생했습니다.");
  }

  async function requestStart() {
    if (!state.files.length || state.busy) {
      return;
    }
    setBusy(true);
    setSummary("파일을 올리고 파싱 작업을 시작하는 중입니다...", "info");

    const formData = new FormData();
    state.files.forEach((file) => formData.append("files", file));

    try {
      const response = await fetch(startUrl, {
        method: "POST",
        body: formData,
        credentials: "same-origin",
      });
      const data = await readApiPayload(response);
      if (!response.ok || !data || !data.job) {
        throw new Error((data && data.error) || "영수증 작업을 시작하지 못했습니다.");
      }

      state.jobId = data.job.job_id;
      state.accounts = Array.isArray(data.accounts) ? data.accounts : [];
      state.result = null;
      storageSet(STORAGE_KEY, state.jobId);
      hideToast();
      applyJobSnapshot(data.job);
      renderAccounts();
      showStep(2);
      schedulePolling();
    } catch (error) {
      setSummary(error.message || "영수증 작업을 시작하지 못했습니다.", "bad");
    } finally {
      setBusy(false);
      renderResult();
    }
  }

  async function pollJob(showMissingError) {
    if (!state.jobId) {
      return;
    }

    try {
      const response = await fetch(replaceJobId(statusUrlTemplate, state.jobId), {
        credentials: "same-origin",
      });
      const data = await readApiPayload(response);
      if (response.status === 404) {
        storageRemove(STORAGE_KEY);
        clearPolling();
        if (showMissingError) {
          setSummary((data && data.error) || "진행 중인 영수증 작업을 찾지 못했습니다.", "warn");
        }
        return;
      }
      if (!response.ok || !data || !data.job) {
        throw new Error((data && data.error) || "영수증 상태를 불러오지 못했습니다.");
      }
      state.accounts = Array.isArray(data.accounts) ? data.accounts : state.accounts;
      applyJobSnapshot(data.job);
      renderAccounts();
      if (data.job.is_complete) {
        clearPolling();
      }
    } catch (error) {
      clearPolling();
      if (showMissingError) {
        setSummary(error.message || "영수증 상태를 불러오지 못했습니다.", "bad");
      }
    }
  }

  function buildCreatePayload() {
    return getCreatableItems().map((item) => ({
      item_id: item.item_id,
      filename: item.filename,
      occurred_on: item.occurred_on || "",
      occurred_time: item.occurred_time || "",
      amount_krw: item.amount_krw || "",
      counterparty: item.counterparty || "",
      payment_item: item.payment_item || "",
      payment_method: item.payment_method || "",
      memo: item.memo || "",
      usage: item.usage || "unknown",
    }));
  }

  async function requestCreate() {
    if (!state.jobId || state.busy) {
      return;
    }

    const payload = buildCreatePayload();
    if (!payload.length) {
      setSummary("생성할 수 있는 영수증 항목이 없습니다.", "warn");
      return;
    }

    const invalid = payload.some((item) => !item.occurred_on || !item.occurred_time || !Number(item.amount_krw));
    if (invalid) {
      setSummary("날짜, 시간, 금액이 비어 있는 항목을 먼저 확인해 주세요.", "warn");
      showStep(2);
      return;
    }

    setBusy(true);
    setSummary("거래 생성과 증빙 첨부를 진행하는 중입니다...", "info");

    const formData = new FormData();
    formData.append("items_json", JSON.stringify(payload));
    if (state.selectedAccountId) {
      formData.append("bank_account_link_id", state.selectedAccountId);
    }

    try {
      const response = await fetch(replaceJobId(createUrlTemplate, state.jobId), {
        method: "POST",
        body: formData,
        credentials: "same-origin",
      });
      const data = await readApiPayload(response);
      if (!response.ok || !data) {
        throw new Error((data && data.error) || "거래 생성에 실패했습니다.");
      }

      state.result = data;
      if (data.job) {
        applyJobSnapshot(data.job);
      }
      showStep(3);
      hideToast();
      storageRemove(STORAGE_KEY);
    } catch (error) {
      setSummary(error.message || "거래 생성에 실패했습니다.", "bad");
    } finally {
      setBusy(false);
      renderResult();
    }
  }

  function bindDetailEvents() {
    detailPane.addEventListener("input", (event) => {
      const target = event.target;
      if (!(target instanceof HTMLInputElement || target instanceof HTMLSelectElement || target instanceof HTMLTextAreaElement)) {
        return;
      }
      const field = target.dataset.field;
      if (!field) {
        return;
      }
      const item = getActiveItem();
      if (!item || item.status !== "ready") {
        return;
      }
      if (!state.localEdits[item.item_id]) {
        state.localEdits[item.item_id] = {};
      }
      state.localEdits[item.item_id][field] = target.value;
      item[field] = target.value;
      renderResult();
    });

    detailPane.addEventListener("change", (event) => {
      const target = event.target;
      if (!(target instanceof HTMLInputElement || target instanceof HTMLSelectElement || target instanceof HTMLTextAreaElement)) {
        return;
      }
      const field = target.dataset.field;
      if (!field) {
        return;
      }
      const item = getActiveItem();
      if (!item || item.status !== "ready") {
        return;
      }
      if (!state.localEdits[item.item_id]) {
        state.localEdits[item.item_id] = {};
      }
      state.localEdits[item.item_id][field] = target.value;
      item[field] = target.value;
      renderResult();
    });
  }

  function bindListEvents() {
    parseList.addEventListener("click", (event) => {
      const button = event.target.closest("[data-receipt-item]");
      if (!button) {
        return;
      }
      state.activeItemId = button.dataset.receiptItem || null;
      renderParseList();
      renderDetail();
    });
  }

  function bindEvents() {
    openBtn.addEventListener("click", () => {
      openModal();
      if (state.jobId && state.job && state.job.is_complete) {
        showStep(3);
      }
    });

    closeButtons.forEach((button) => {
      button.addEventListener("click", closeModal);
    });

    fileInput.addEventListener("change", () => {
      state.files = Array.from(fileInput.files || []);
      if (state.files.length > 50) {
        state.files = [];
        fileInput.value = "";
        setSummary("한 번에 최대 50개까지만 올릴 수 있습니다.", "bad");
      } else {
        setSummary(state.files.length ? "파일을 확인한 뒤 파싱을 시작해 주세요." : "", state.files.length ? "info" : "");
      }
      renderSelectedFiles();
    });

    resetBtn.addEventListener("click", resetModalState);
    startBtn.addEventListener("click", () => {
      void requestStart();
    });
    goResultBtn.addEventListener("click", () => {
      showStep(3);
      renderResult();
    });
    backToUploadBtn.addEventListener("click", () => {
      showStep(1);
    });
    backToParsingBtn.addEventListener("click", () => {
      showStep(2);
    });
    createBtn.addEventListener("click", () => {
      void requestCreate();
    });
    accountSelect.addEventListener("change", () => {
      state.selectedAccountId = accountSelect.value || "";
    });
    toastOpen.addEventListener("click", () => {
      openModal();
      showStep(3);
      hideToast();
    });
    toastClose.addEventListener("click", hideToast);

    bindDetailEvents();
    bindListEvents();
  }

  async function resumeActiveJobIfPresent() {
    const storedJobId = storageGet(STORAGE_KEY);
    if (!storedJobId) {
      return;
    }
    state.jobId = storedJobId;
    await pollJob(false);
    if (state.job && !state.job.is_complete) {
      schedulePolling();
    }
  }

  bindEvents();
  resetModalState();
  void resumeActiveJobIfPresent();
})();
