(function () {
  const root = document.querySelector("[data-receipt-modal-root]");
  if (!root) {
    return;
  }

  const previewUrl = root.dataset.previewUrl;
  const createUrl = root.dataset.createUrl;
  const openBtn = root.querySelector("[data-receipt-open]");
  const shell = root.querySelector("[data-receipt-shell]");
  const closeButtons = root.querySelectorAll("[data-receipt-close]");
  const fileInput = root.querySelector("[data-receipt-input]");
  const selectionNote = root.querySelector("[data-receipt-selection-note]");
  const previewBtn = root.querySelector("[data-receipt-preview]");
  const resetBtn = root.querySelector("[data-receipt-reset]");
  const createBtn = root.querySelector("[data-receipt-create]");
  const previewList = root.querySelector("[data-receipt-preview-list]");
  const summary = root.querySelector("[data-receipt-summary]");
  const accountWrap = root.querySelector("[data-receipt-account-wrap]");
  const accountSelect = root.querySelector("[data-receipt-account-select]");

  const state = {
    files: [],
    items: [],
    accounts: [],
    busy: false,
  };

  function escapeHtml(value) {
    return String(value || "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function openModal() {
    shell.hidden = false;
    document.body.classList.add("receipt-modal-open");
  }

  function closeModal() {
    shell.hidden = true;
    document.body.classList.remove("receipt-modal-open");
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

  function resetState() {
    state.files = [];
    state.items = [];
    state.accounts = [];
    state.busy = false;
    fileInput.value = "";
    selectionNote.textContent = "선택된 파일이 없습니다.";
    previewBtn.disabled = true;
    createBtn.disabled = true;
    accountWrap.hidden = true;
    accountSelect.innerHTML = '<option value="">계좌 미지정으로 생성</option>';
    previewList.innerHTML = `
      <div class="receipt-empty-state">
        <div class="strong">영수증 초안을 여기서 확인합니다.</div>
        <div class="small-note muted2">사용일시, 금액, 상호가 비어 있으면 직접 확인하고 수정한 뒤 생성하면 됩니다.</div>
      </div>
    `;
    setSummary("", "");
  }

  function syncSelectionNote() {
    if (!state.files.length) {
      selectionNote.textContent = "선택된 파일이 없습니다.";
      return;
    }
    selectionNote.textContent = `${state.files.length}개 파일 선택됨`;
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
      accountSelect.appendChild(option);
    });
  }

  function renderItems() {
    if (!state.items.length) {
      previewList.innerHTML = `
        <div class="receipt-empty-state">
          <div class="strong">업로드한 영수증 초안이 없습니다.</div>
          <div class="small-note muted2">이미지를 고른 뒤 “파싱 초안 보기”를 눌러 주세요.</div>
        </div>
      `;
      createBtn.disabled = true;
      return;
    }

    previewList.innerHTML = state.items
      .map((item, index) => {
        if (item.status === "error") {
          return `
            <article class="receipt-preview-card receipt-preview-card-error">
              <div class="receipt-preview-head">
                <div>
                  <div class="strong">${escapeHtml(item.filename)}</div>
                  <div class="small-note muted2">업로드할 수 없는 파일</div>
                </div>
                <span class="badge bad">실패</span>
              </div>
              <div class="receipt-inline-error">${escapeHtml(item.error || "파일을 확인해 주세요.")}</div>
            </article>
          `;
        }

        const warnings = Array.isArray(item.warnings) ? item.warnings : [];
        return `
          <article class="receipt-preview-card" data-receipt-item-index="${index}">
            <div class="receipt-preview-head">
              <div>
                <div class="strong">${escapeHtml(item.filename)}</div>
                <div class="small-note muted2">이미지 초안입니다. 생성 전에 실제 영수증과 비교해 주세요.</div>
              </div>
              <span class="badge warn">확인 필요</span>
            </div>

            ${warnings.length ? `<div class="receipt-warning-list">${warnings.map((warning) => `<div class="receipt-warning-item">${escapeHtml(warning)}</div>`).join("")}</div>` : ""}

            <div class="receipt-form-grid">
              <label class="field">
                <span class="label">사용일자</span>
                <input type="date" value="${escapeHtml(item.occurred_on)}" data-field="occurred_on">
              </label>
              <label class="field">
                <span class="label">사용시각</span>
                <input type="time" value="${escapeHtml(item.occurred_time || "12:00")}" data-field="occurred_time">
              </label>
              <label class="field">
                <span class="label">금액</span>
                <input type="number" min="1" step="1" value="${item.amount_krw || ""}" placeholder="예: 12500" data-field="amount_krw">
              </label>
              <label class="field">
                <span class="label">가맹점 / 상호</span>
                <input type="text" value="${escapeHtml(item.counterparty || "")}" placeholder="예: 카페 이름" data-field="counterparty">
              </label>
              <label class="field receipt-field-wide">
                <span class="label">메모</span>
                <input type="text" value="${escapeHtml(item.memo || "")}" placeholder="필요하면 거래 설명을 남겨 주세요." data-field="memo">
              </label>
              <label class="field">
                <span class="label">업무용 여부</span>
                <select data-field="usage">
                  <option value="business" ${item.usage === "business" ? "selected" : ""}>업무용</option>
                  <option value="personal" ${item.usage === "personal" ? "selected" : ""}>개인용</option>
                  <option value="unknown" ${item.usage === "unknown" ? "selected" : ""}>나중에 검토</option>
                </select>
              </label>
            </div>
            <div class="receipt-inline-error" data-receipt-item-error hidden></div>
          </article>
        `;
      })
      .join("");

    createBtn.disabled = !state.items.some((item) => item.status === "ready");
  }

  function collectItems() {
    const cards = previewList.querySelectorAll("[data-receipt-item-index]");
    const payload = [];
    let hasError = false;

    cards.forEach((card) => {
      const index = Number(card.dataset.receiptItemIndex);
      const baseItem = state.items[index];
      const errorEl = card.querySelector("[data-receipt-item-error]");
      errorEl.hidden = true;
      errorEl.textContent = "";

      const item = {
        occurred_on: card.querySelector('[data-field="occurred_on"]').value,
        occurred_time: card.querySelector('[data-field="occurred_time"]').value || "12:00",
        amount_krw: card.querySelector('[data-field="amount_krw"]').value,
        counterparty: card.querySelector('[data-field="counterparty"]').value,
        memo: card.querySelector('[data-field="memo"]').value,
        usage: card.querySelector('[data-field="usage"]').value || "unknown",
      };

      const amount = Number(String(item.amount_krw || "").replaceAll(",", ""));
      if (!item.occurred_on || !Number.isFinite(amount) || amount <= 0) {
        errorEl.hidden = false;
        errorEl.textContent = "사용일자와 금액은 확인 후 생성해 주세요.";
        hasError = true;
      }

      payload.push({
        ...item,
        client_index: baseItem.client_index,
        filename: baseItem.filename,
      });
    });

    return { payload, hasError };
  }

  async function requestPreview() {
    if (!state.files.length || state.busy) {
      return;
    }
    state.busy = true;
    previewBtn.disabled = true;
    createBtn.disabled = true;
    setSummary("영수증 초안을 만드는 중입니다...", "info");

    const formData = new FormData();
    state.files.forEach((file) => formData.append("files", file));

    try {
      const response = await fetch(previewUrl, {
        method: "POST",
        body: formData,
        credentials: "same-origin",
      });
      const data = await response.json();
      if (!response.ok || !data) {
        throw new Error((data && data.error) || "초안 생성에 실패했습니다.");
      }

      state.accounts = Array.isArray(data.accounts) ? data.accounts : [];
      state.items = Array.isArray(data.items) ? data.items : [];
      renderAccounts();
      renderItems();

      const readyCount = state.items.filter((item) => item.status === "ready").length;
      const errorCount = state.items.filter((item) => item.status === "error").length;
      if (!readyCount) {
        setSummary("생성 가능한 영수증 초안이 없습니다. 파일 형식과 개수를 확인해 주세요.", "bad");
      } else if (errorCount) {
        setSummary(`${readyCount}개 초안을 만들었고 ${errorCount}개는 제외했습니다.`, "warn");
      } else {
        setSummary(`${readyCount}개 영수증 초안을 준비했습니다.`, "good");
      }
    } catch (error) {
      setSummary(error.message || "초안 생성에 실패했습니다.", "bad");
      state.items = [];
      renderAccounts();
      renderItems();
    } finally {
      state.busy = false;
      previewBtn.disabled = !state.files.length;
    }
  }

  async function requestCreate() {
    if (!state.items.length || state.busy) {
      return;
    }

    const readyItems = state.items.filter((item) => item.status === "ready");
    if (!readyItems.length) {
      setSummary("생성할 수 있는 영수증 초안이 없습니다.", "bad");
      return;
    }

    const { payload, hasError } = collectItems();
    if (hasError) {
      setSummary("비어 있는 필드를 확인한 뒤 다시 생성해 주세요.", "warn");
      return;
    }

    state.busy = true;
    createBtn.disabled = true;
    previewBtn.disabled = true;
    setSummary("거래 생성과 증빙 첨부를 처리하는 중입니다...", "info");

    const formData = new FormData();
    payload.forEach((item) => {
      const file = state.files[item.client_index];
      if (file) {
        formData.append("files", file);
      }
    });
    formData.append("items_json", JSON.stringify(payload));
    if (accountSelect.value) {
      formData.append("bank_account_link_id", accountSelect.value);
    }

    try {
      const response = await fetch(createUrl, {
        method: "POST",
        body: formData,
        credentials: "same-origin",
      });
      const data = await response.json();
      if (!response.ok || !data) {
        throw new Error((data && data.error) || "거래 생성에 실패했습니다.");
      }

      const createdCount = Number(data.created_count || 0);
      const failedCount = Number(data.failed_count || 0);
      if (!createdCount) {
        throw new Error("거래 생성에 실패했습니다. 입력값을 다시 확인해 주세요.");
      }

      if (failedCount) {
        setSummary(`${createdCount}건 생성, ${failedCount}건 실패했습니다. 잠시 후 현재 화면을 새로고침합니다.`, "warn");
      } else {
        setSummary(`${createdCount}건 생성했습니다. 잠시 후 현재 화면을 새로고침합니다.`, "good");
      }
      window.setTimeout(() => window.location.reload(), 1200);
    } catch (error) {
      setSummary(error.message || "거래 생성에 실패했습니다.", "bad");
      createBtn.disabled = false;
      previewBtn.disabled = !state.files.length;
      state.busy = false;
      return;
    }
  }

  openBtn.addEventListener("click", () => {
    openModal();
  });

  closeButtons.forEach((button) => {
    button.addEventListener("click", () => {
      closeModal();
    });
  });

  window.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !shell.hidden) {
      closeModal();
    }
  });

  fileInput.addEventListener("change", () => {
    const selected = Array.from(fileInput.files || []);
    if (selected.length > 50) {
      resetState();
      setSummary("한 번에 최대 50개까지 올릴 수 있습니다.", "bad");
      return;
    }
    state.files = selected;
    state.items = [];
    syncSelectionNote();
    renderItems();
    previewBtn.disabled = !state.files.length;
    createBtn.disabled = true;
    setSummary(state.files.length ? "영수증을 고른 뒤 파싱 초안 보기를 눌러 주세요." : "", state.files.length ? "info" : "");
  });

  previewBtn.addEventListener("click", requestPreview);
  createBtn.addEventListener("click", requestCreate);
  resetBtn.addEventListener("click", resetState);

  resetState();
})();
