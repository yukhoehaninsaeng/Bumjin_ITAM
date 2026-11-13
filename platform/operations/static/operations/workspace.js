(() => {
  "use strict";
  const main = document.getElementById("workspace"),
    dialog = document.getElementById("editor");
  const portal = document.body.dataset.portal === "true",
    area = portal ? "portal" : "admin";
  const esc = (s) =>
    String(s ?? "").replace(
      /[&<>"']/g,
      (c) =>
        ({
          "&": "&amp;",
          "<": "&lt;",
          ">": "&gt;",
          '"': "&quot;",
          "'": "&#39;",
        })[c],
    );
  const date = (s) =>
    s
      ? new Date(s).toLocaleString("ko-KR", {
          dateStyle: "short",
          timeStyle: "short",
        })
      : "—";
  const params = new URLSearchParams(location.search);
  let me = null,
    currentTicket = null,
    composer = null;
  const states = {
    NEW: "접수",
    IN_PROGRESS: "처리 중",
    WAITING_REQUESTER: "요청자 답변 대기",
    WAITING_VENDOR: "외부 업체 대기",
    RESOLVED: "해결",
    CLOSED: "종료",
    CANCELLED: "취소",
  };
  const help = {
    NEW: "지원팀이 요청을 확인하고 있어요.",
    IN_PROGRESS: "담당자가 요청을 처리하고 있어요.",
    WAITING_REQUESTER:
      "담당자가 추가 정보를 요청했어요. 아래에서 답변해주세요.",
    WAITING_VENDOR: "외부 업체의 확인을 기다리고 있어요.",
    RESOLVED:
      "해결 내용을 확인해주세요. 문제가 남았다면 다시 요청할 수 있어요.",
    CLOSED: "확인이 완료된 요청입니다.",
    CANCELLED: "취소된 요청입니다.",
  };
  const pill = (text, code = "") =>
    `<span class="pill ${code.includes("WAITING") ? "wait" : code === "IN_PROGRESS" ? "progress" : code === "HIGH" || code === "URGENT" ? "danger" : ""}">${esc(text)}</span>`;
  async function call(path, options = {}) {
    const headers = {
      "X-CSRFToken": document.querySelector('meta[name="csrf-token"]').content,
      ...options.headers,
    };
    let body = options.body;
    if (body !== undefined && !(body instanceof FormData)) {
      headers["Content-Type"] = "application/json";
      body = JSON.stringify(body);
    }
    const response = await fetch("/api/v1" + path, {
      ...options,
      headers,
      body,
      credentials: "same-origin",
    });
    if (response.status === 401) {
      location.href =
        "/login?next=" +
        encodeURIComponent(location.pathname + location.search);
      throw Error("다시 로그인해주세요.");
    }
    if (response.status === 204) return {};
    const result = await response.json().catch(() => ({
      error: { message: "서버 응답을 확인하지 못했습니다." },
    }));
    if (!response.ok) {
      const fields = result.error?.fields;
      let message = result.error?.message || "처리하지 못했습니다.";
      if (fields && Object.keys(fields).length)
        message = Object.values(fields).flat().join(" ");
      const error = Error(message);
      error.status = response.status;
      throw error;
    }
    return result;
  }
  function toast(message) {
    const node = document.getElementById("toast");
    node.textContent = message;
    node.style.display = "block";
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => (node.style.display = "none"), 4000);
  }
  function heading(title, description, action = "") {
    return `<div class="heading"><div><h1>${esc(title)}</h1><p>${esc(description)}</p></div>${action}</div>`;
  }
  function errorPanel(error) {
    return `<div class="notice error" role="alert">${esc(error.message)} <button class="secondary" data-reload>다시 시도</button></div>`;
  }
  function ticketPath(id) {
    return `/${portal ? "portal/requests" : "admin/tickets"}?ticket=${encodeURIComponent(id)}`;
  }
  function rows(tickets) {
    return `<div class="tablewrap"><table><thead><tr><th>요청 내용</th><th>요청자</th><th>상태</th><th>우선순위</th><th>담당자</th><th>접수</th></tr></thead><tbody>${tickets.map((t) => `<tr data-id="${t.id}"><td data-label="요청 내용"><a href="${ticketPath(t.id)}"><b>${esc(t.title)}</b></a><div class="muted">${esc(t.number)}</div></td><td data-label="요청자">${esc(t.requester.name)}<div class="muted">${esc(t.requester.organization)}</div></td><td data-label="상태">${pill(t.status_label, t.status)} ${t.overdue ? pill("기한 초과", "HIGH") : ""}</td><td data-label="우선순위">${pill(t.priority_label, t.priority)}</td><td data-label="담당자">${esc(t.assignee)}</td><td data-label="접수">${date(t.created_at)}</td></tr>`).join("") || '<tr><td colspan="6"><div class="emptyState">표시할 요청이 없습니다. 필요한 지원이 있으면 새 요청을 남겨주세요.</div></td></tr>'}</tbody></table></div>`;
  }
  async function dashboard() {
    const d = (await call("/admin/dashboard")).data;
    main.innerHTML =
      heading(
        "통합 운영 대시보드",
        `${me.name}님, 오늘 처리할 요청과 자산 업무를 확인하세요.`,
        me.roles.includes("support_agent")
          ? "<button data-new-ticket>＋ 티켓 등록</button>"
          : "",
      ) +
      `<div class="cards">${[
        ["진행 중 티켓", d.counts.open, "open"],
        ["미배정 티켓", d.counts.unassigned, "unassigned"],
        ["기한 초과", d.counts.overdue, "overdue"],
        ["답변 대기", d.counts.waiting, "waiting"],
      ]
        .map(
          ([l, n, q]) =>
            `<button class="card" data-href="/admin/tickets?queue=${q}"><span>${l} ↗</span><strong>${n}<small class="muted"> 건</small></strong></button>`,
        )
        .join(
          "",
        )}</div><div class="demoOverviewGrid"><section class="insightCard"><h3>지금 처리할 업무</h3>${[
        ["기한이 지난 요청을 먼저 확인하세요", d.counts.overdue, "overdue"],
        ["담당자를 기다리는 요청", d.counts.unassigned, "unassigned"],
        ["내가 맡은 요청 이어서 처리하기", "내 티켓", "mine"],
      ]
        .map(
          ([l, n, q]) =>
            `<button class="demoTaskRow" data-href="/admin/tickets?queue=${q}"><span><b>${l}</b><small>${n}${typeof n === "number" ? "건" : ""}</small></span><span class="demoTaskArrow">→</span></button>`,
        )
        .join(
          "",
        )}</section><section class="insightCard"><h3>자산 · 조직 요약</h3><div class="demoMiniMetrics">${[
        ["자산", d.assets.total],
        ["보관", d.assets.stock],
        ["수리", d.assets.repair],
        ["직원", d.people],
        ["조직", d.organizations],
      ]
        .map(([l, n]) => `<div><span>${l}</span><strong>${n}</strong></div>`)
        .join(
          "",
        )}</div><p class="muted">내 역할·법인·지원팀 범위의 정보입니다.</p></section></div><section class="insightCard"><div class="demoSectionHead"><h3>최근 접수 · 진행 요청</h3><a href="/admin/tickets">전체 티켓 보기 →</a></div>${rows(d.recent)}</section><p class="muted">갱신 시각 ${date(d.as_of)}</p>`;
  }
  async function tickets() {
    const queue = params.get("queue") || "all",
      q = params.get("q") || "",
      state = params.get("status") || "",
      page = params.get("page") || "1";
    const query = new URLSearchParams({ queue, q, status: state, page });
    const result = await call(`/${area}/tickets?${query}`);
    main.innerHTML =
      heading(
        portal ? "내 요청" : "티켓 관리",
        portal
          ? "요청 진행 상황을 확인하고 담당자에게 답변하세요."
          : "팀과 함께 요청을 접수하고 해결하세요.",
        "<button data-new-ticket>＋ 지원 요청</button>",
      ) +
      `<section class="filterPanel">${
        !portal
          ? `<div class="demoTabs">${[
              ["전체", "all"],
              ["진행 중", "open"],
              ["내 티켓", "mine"],
              ["미배정", "unassigned"],
              ["답변 대기", "waiting"],
              ["기한 초과", "overdue"],
              ["해결·종료", "done"],
            ]
              .map(
                ([l, v]) =>
                  `<a class="pill" href="?queue=${v}" ${queue === v ? 'aria-current="page"' : ""}>${l}</a>`,
              )
              .join("")}</div>`
          : ""
      }<form class="toolbar" id="searchForm"><input name="q" aria-label="티켓 검색" placeholder="번호, 제목, 요청자 검색" value="${esc(q)}"><input type="hidden" name="queue" value="${esc(queue)}"><select name="status" aria-label="상태"><option value="">전체 상태</option>${Object.entries(
        states,
      )
        .map(
          ([k, l]) =>
            `<option value="${k}" ${k === state ? "selected" : ""}>${l}</option>`,
        )
        .join(
          "",
        )}</select><button class="secondary searchButton">검색</button></form><span class="muted">${result.meta.total}건</span></section>${rows(result.data)}<div class="page">${Number(page) > 1 ? `<a href="?${new URLSearchParams({ ...Object.fromEntries(query), page: String(Number(page) - 1) })}">이전</a>` : ""}${Number(page) * 30 < result.meta.total ? `<a href="?${new URLSearchParams({ ...Object.fromEntries(query), page: String(Number(page) + 1) })}">다음</a>` : ""}</div>`;
  }
  function localDateTime(s) {
    if (!s) return "";
    const d = new Date(s);
    return new Date(d.getTime() - d.getTimezoneOffset() * 60000)
      .toISOString()
      .slice(0, 16);
  }
  function filesMarkup(files) {
    return !files.length
      ? ""
      : `<h3 class="sectionTitle">첨부파일 ${files.length}개</h3><div class="fileCards">${files.map((f) => `<div class="fileCard">${f.is_image ? `<a href="${esc(f.url)}" target="_blank" rel="noopener"><img src="${esc(f.url)}" alt="${esc(f.name)}"></a>` : '<span aria-hidden="true">▤</span>'}<div class="fileInfo"><strong>${esc(f.name)}</strong><small>${(f.size / 1024).toFixed(1)} KB</small><a href="${esc(f.url)}" download>다운로드</a></div></div>`).join("")}</div>`;
  }
  async function ticketDetail(id) {
    const t = (await call(`/${area}/tickets/${id}`)).data;
    currentTicket = t;
    main.innerHTML =
      heading(
        t.title,
        `${t.number} · ${date(t.created_at)}`,
        `<a href="/${portal ? "portal/requests" : "admin/tickets"}">← 목록으로</a>`,
      ) +
      `<p>${pill(t.status_label, t.status)} ${pill(t.priority_label, t.priority)} ${t.overdue ? pill("기한 초과", "HIGH") : ""}</p><div class="ticketSplit"><section class="insightCard"><div class="progressSteps">${esc(help[t.status])}</div><h3 class="sectionTitle">요청 내용</h3><p class="description">${esc(t.body)}</p>${filesMarkup(t.attachments)}<h3 class="sectionTitle">대화 및 처리 기록</h3>${t.messages.map((m) => `<article class="messageCard ${m.internal ? "internal" : ""}"><b>${esc(m.author)}</b>${m.internal ? " " + pill("IT 내부 메모", "WAITING_VENDOR") : ""}<span class="muted">${date(m.created_at)}</span><p class="description">${esc(m.body)}</p></article>`).join("") || '<p class="muted">아직 답변이 없습니다.</p>'}<form id="replyForm"><label>${portal ? "답변 내용" : "공개 범위"}${!portal ? '<select name="internal"><option value="false">공개 답변 · 요청자가 볼 수 있어요</option><option value="true">내부 메모 · IT 담당자만 볼 수 있어요</option></select>' : ""}</label><label><span class="sr-only">답변 내용</span><textarea name="body" required maxlength="20000" rows="4" placeholder="추가 정보나 처리 내용을 입력하세요."></textarea></label><div class="formActions"><button>답변 등록</button></div><div class="formError" role="alert"></div></form></section><section><article class="insightCard"><h3>처리 정보</h3>${[
        ["요청자", t.requester.name],
        ["소속", t.requester.organization],
        ["지원팀", t.team],
        ["담당자", t.assignee],
      ]
        .map(
          ([k, v]) =>
            `<div class="kv"><span class="muted">${k}</span><span>${esc(v)}</span></div>`,
        )
        .join(
          "",
        )}<div class="actions">${!portal && ["NEW", "IN_PROGRESS", "WAITING_REQUESTER", "WAITING_VENDOR"].includes(t.status) ? '<button class="secondary" data-claim>내가 담당하기</button>' : ""}${portal ? (t.status === "RESOLVED" ? '<button data-transition="CLOSED">해결 확인</button><button class="secondary" data-transition="IN_PROGRESS">아직 해결되지 않았어요</button>' : t.status === "CLOSED" ? '<button class="secondary" data-transition="IN_PROGRESS">다시 요청하기</button>' : "") : `${["IN_PROGRESS", "WAITING_REQUESTER", "WAITING_VENDOR"].includes(t.status) ? '<button data-transition="RESOLVED">해결 내용 기록</button>' : ""}${t.status === "IN_PROGRESS" ? '<button class="secondary" data-transition="WAITING_REQUESTER">추가 답변 요청</button><button class="secondary" data-transition="WAITING_VENDOR">업체 확인 대기</button>' : ""}${["WAITING_REQUESTER", "WAITING_VENDOR", "RESOLVED", "CLOSED"].includes(t.status) ? '<button class="secondary" data-transition="IN_PROGRESS">처리 재개</button>' : ""}${t.status === "RESOLVED" ? '<button data-transition="CLOSED">종료 확인</button>' : ""}`}</div>${
        !portal
          ? `<form id="metadataForm"><label>우선순위<select name="priority">${[
              ["URGENT", "긴급"],
              ["HIGH", "높음"],
              ["NORMAL", "보통"],
              ["LOW", "낮음"],
            ]
              .map(
                ([v, l]) =>
                  `<option value="${v}" ${t.priority === v ? "selected" : ""}>${l}</option>`,
              )
              .join(
                "",
              )}</select></label><label>목표 처리일<input type="datetime-local" name="due_at" value="${localDateTime(t.due_at)}"></label><p class="fieldHelp">현재 브라우저 시간대 기준으로 입력합니다.</p><div class="formError" role="alert"></div><div class="formActions"><button>처리 설정 저장</button></div></form>`
          : ""
      }</article><article class="insightCard sectionGap"><h3>관련 자산</h3>${t.assets.map((a) => `<p><a href="/${area}/assets?asset=${a.id}"><b>${esc(a.model)}</b><small class="muted"> ${esc(a.number)} →</small></a></p>`).join("") || '<p class="muted">연결된 자산이 없습니다.</p>'}</article></section></div>`;
    const metadataForm = document.getElementById("metadataForm");
    if (metadataForm)
      metadataForm.onsubmit = (e) => {
        e.preventDefault();
        submit(metadataForm, async () => {
          const data = new FormData(metadataForm);
          await call(`/admin/tickets/${id}`, {
            method: "PATCH",
            body: {
              expected_version: currentTicket.version,
              priority: data.get("priority"),
              due_at: data.get("due_at")
                ? new Date(data.get("due_at")).toISOString()
                : null,
            },
          });
          await ticketDetail(id);
          toast("처리 설정을 저장했습니다.");
        });
      };
    const form = document.getElementById("replyForm");
    form.onsubmit = async (e) => {
      e.preventDefault();
      await submit(form, async () => {
        const data = new FormData(form),
          payload = { body: data.get("body") };
        if (!portal) payload.internal = data.get("internal") === "true";
        await call(`/${area}/tickets/${id}/messages`, {
          method: "POST",
          body: payload,
        });
        await ticketDetail(id);
        toast("답변을 등록했습니다.");
      });
    };
  }
  async function assets() {
    const id = params.get("asset");
    if (id) return assetDetail(id);
    const filters = new URLSearchParams();
    for (const key of ["q", "page", "category", "status"]) {
      const value = params.get(key);
      if (value) filters.set(key, value);
    }
    const result = await call(`/${area}/assets?${filters.toString()}`);
    const category = params.get("category") || "";
    const status = params.get("status") || "";
    const q = params.get("q") || "";
    const query = new URLSearchParams({ q, category, status });
    const pageLink = (page) => {
      const next = new URLSearchParams(query);
      if (page > 1) next.set("page", page);
      return `?${next.toString()}`;
    };
    main.innerHTML =
      heading(
        portal ? "내 자산" : "자산 현황 · 목록",
        portal
          ? "지급받은 장비를 확인하고 문제가 있으면 지원을 요청하세요."
          : "자산의 상세 사양과 배정 상태를 확인하고 관리합니다.",
        !portal && me.roles.includes("asset_manager")
          ? "<button data-new-asset>＋ 자산 등록</button>"
          : "",
      ) +
      `<form class="toolbar assetFilters" method="get" action="/${area}/assets"><input name="q" aria-label="자산 검색" placeholder="관리번호, 모델, 시리얼번호 검색" value="${esc(q)}"><select name="category" aria-label="장비 구분"><option value="">전체 장비</option><option value="PC" ${category === "PC" ? "selected" : ""}>PC</option><option value="모니터" ${category === "모니터" ? "selected" : ""}>모니터</option></select><select name="status" aria-label="자산 상태"><option value="">전체 상태</option><option value="IN_USE" ${status === "IN_USE" ? "selected" : ""}>사용</option><option value="IN_STOCK" ${status === "IN_STOCK" ? "selected" : ""}>보관</option><option value="REPAIR" ${status === "REPAIR" ? "selected" : ""}>수리</option><option value="LOST" ${status === "LOST" ? "selected" : ""}>분실</option><option value="DISPOSED" ${status === "DISPOSED" ? "selected" : ""}>폐기</option></select><button class="secondary">검색</button><a class="secondary filterReset" href="/${area}/assets">초기화</a></form><div class="tablewrap"><table><thead><tr><th>관리번호</th><th>사업부 · 장비</th><th>사양 · 모델</th><th>사용자</th><th>소속</th><th>상태</th></tr></thead><tbody>${result.data.map((a) => `<tr data-id="${a.id}"><td data-label="관리번호"><a href="?asset=${a.id}"><b>${esc(a.number)}</b></a></td><td data-label="사업부 · 장비">${esc(a.company)} · ${esc(a.category)}</td><td data-label="사양 · 모델">${esc(a.maker)} ${esc(a.model)}<div class="muted">${esc(a.serial || "시리얼 미등록")}</div></td><td data-label="사용자">${esc(a.user)}</td><td data-label="소속">${esc(a.organization)}</td><td data-label="상태">${pill(a.status_label, a.status)}</td></tr>`).join("") || '<tr><td colspan="6">표시할 자산이 없습니다.</td></tr>'}</tbody></table></div><p class="muted">${result.meta.total}건 · 권한 범위 내 자산</p><div class="page">${result.meta.page > 1 ? `<a href="${pageLink(result.meta.page - 1)}">이전</a>` : ""}${result.meta.page * result.meta.page_size < result.meta.total ? `<a href="${pageLink(result.meta.page + 1)}">다음</a>` : ""}</div>`;
  }
  async function assetDetail(id) {
    const a = (await call(`/${area}/assets/${id}`)).data;
    const meta = a.legacy_data || {};
    const software = meta.software || {};
    const deviceRows = [
      ["관리번호", a.number],
      ["사업부 · 조직", `${a.company} · ${a.organization}`],
      ["장비 구분", a.category === "모니터" ? "모니터" : (meta.device_type === "DESKTOP" ? "데스크톱" : "노트북")],
      ["제조사 · 모델", [a.maker, a.model].filter(Boolean).join(" · ")],
      ["일련번호", a.serial || "미등록"],
      ["CPU", meta.cpu || "미등록"],
      ["메모리", meta.ram || "미등록"],
      ["저장장치", [meta.ssd && `SSD ${meta.ssd}`, meta.hdd && `HDD ${meta.hdd}`].filter(Boolean).join(" · ") || "미등록"],
      ["그래픽", meta.gpu || "미등록"],
      ["운영체제", [meta.os, meta.os_edition].filter(Boolean).join(" · ") || "미등록"],
      ...(a.category === "모니터" ? [["화면 크기", meta.size || "미등록"]] : []),
      ["구입일", meta.purchase_date || "미등록"],
      ["비고", meta.note || "—"],
    ];
    const softwareRows = [
      ["Office", software.office], ["한글", software.hangul], ["SAP", software.sap],
      ["백신", software.antivirus], ["Docuone", software.docuone],
      ["OfficeKeeper", software.officekeeper], ["기타", software.other],
    ];
    const tickets = a.tickets || [];
    main.innerHTML =
      `<div class="assetBreadcrumb"><a href="/${area}/assets">${portal ? "내 자산" : "자산 목록"}</a><span>›</span><span>자산 상세</span></div>` +
      heading(a.number, `${a.company} · ${a.category} · ${a.model}`, `<span class="pill" data-state="${esc(a.status)}">${esc(a.status_label)}</span>`) +
      `<div class="figmaAssetDetail"><section class="assetPrimary"><section class="assetInfoPanel"><h2 class="sectionTitle">장비 정보</h2><p class="detailPanelDescription">자산 기본 정보와 사양을 확인합니다.</p><div class="assetSpecRows">${deviceRows.map(([k,v]) => `<div class="assetSpecRow"><span>${esc(k)}</span><b>${esc(v)}</b></div>`).join("")}</div></section><section class="assetInfoPanel softwareSection"><h2 class="sectionTitle">소프트웨어</h2><p class="detailPanelDescription">자산에 기록된 소프트웨어 정보입니다.</p><div class="assetSoftwareGrid">${softwareRows.map(([k,v]) => `<div class="assetSoftwareRow"><span>${esc(k)}</span><b>${esc(v || "미기재")}</b></div>`).join("")}</div></section></section><aside class="assetAside"><section class="assetOwnerCard"><h2>현재 사용자</h2><div class="assetOwnerBody"><strong class="ownerName">${esc(a.user)}</strong><p class="ownerMeta">${esc(a.organization)}</p><div class="assetActionStack"><button data-request-asset="${a.id}">이 자산으로 지원 요청</button>${!portal && me.roles.includes("asset_manager") ? `<button class="secondary" data-edit-asset="${a.id}">자산 정보 수정</button>${a.status === "IN_STOCK" ? `<button class="secondary" data-assign-asset="${a.id}" data-version="${a.version}">직원에게 배정</button>` : `<button class="secondary" data-return-asset="${a.id}" data-version="${a.version}">수거 · 반납</button>`}` : ""}</div></div></section><section class="assetHistoryCard"><h2>관련 티켓</h2>${tickets.length ? tickets.map(t => `<a class="assetTicketLink" href="/admin/tickets?ticket=${encodeURIComponent(t.id)}"><b>${esc(t.number)}</b><span>${esc(t.title)}</span>${pill(t.status_label, t.status)}</a>`).join("") : '<p class="muted">연결된 티켓이 없습니다.</p>'}</section></aside></div><div class="assetDetailFooter"><a class="secondary" href="/${area}/assets">← 자산 목록</a></div>`;
  }
  async function people() {
    const [orgs, employees] = await Promise.all([
      call("/admin/organizations"),
      call("/admin/employees"),
    ]);
    const org = params.get("organization") || "";
    main.innerHTML =
      heading(
        "사용자 · 조직 관리",
        "조직별 직원을 찾고 지급 자산과 소속을 확인하세요.",
      ) +
      `<div class="orgLayout"><aside class="insightCard"><h3>조직도</h3><a class="orgItem" href="/admin/organizations">전체 직원</a>${orgs.data.map((o) => `<a class="orgItem ${o.id === org ? "active" : ""}" href="?organization=${o.id}">${esc(o.company)} / ${esc(o.name)}</a>`).join("")}</aside><section><div class="userDirectory">${
        employees.data
          .filter((e) => !org || e.organization_id === org)
          .map(
            (e) =>
              `<article class="insightCard"><h3>${esc(e.name)}</h3><p>${esc(e.company)} / ${esc(e.organization)}</p><p class="muted">${esc(e.email || "이메일 미등록")}</p></article>`,
          )
          .join("") || "<p>직원이 없습니다.</p>"
      }</div></section></div>`;
  }
  async function portalHome() {
    const [ticketResult, assetResult] = await Promise.all([
      call("/portal/tickets"),
      call("/portal/assets"),
    ]);
    main.innerHTML =
      heading(
        `${me.name}님, 어떤 도움이 필요하신가요?`,
        "오류 화면을 붙여넣어 지원을 요청하면 담당자가 확인합니다.",
        "<button data-new-ticket>＋ 지원 요청</button>",
      ) +
      `<div class="cards"><div class="card"><span>내 자산</span><strong>${assetResult.meta.total}</strong></div><div class="card"><span>내 요청</span><strong>${ticketResult.meta.total}</strong></div></div><h2>내 요청</h2>${rows(ticketResult.data.slice(0, 6))}<h2 class="sectionGap">내 자산</h2><div class="userDirectory">${
        assetResult.data
          .slice(0, 6)
          .map(
            (a) =>
              `<article class="insightCard">${pill(a.status_label)}<h3 class="sectionGap">${esc(a.model)}</h3><p class="muted">${esc(a.number)}</p><div class="actions"><a href="/portal/assets?asset=${a.id}">상세 보기</a><button data-request-asset="${a.id}">문제 신고</button></div></article>`,
          )
          .join("") || '<p class="muted">현재 배정된 자산이 없습니다.</p>'
      }</div>`;
  }
  async function submit(form, fn) {
    const button = form.querySelector(
      'button[type="submit"],button:not([type])',
    );
    if (button) button.disabled = true;
    try {
      await fn();
    } catch (error) {
      const node = form.querySelector(".formError");
      if (node) {
        node.className = "formError notice error";
        node.textContent = error.message;
      } else toast(error.message);
    } finally {
      if (button && button.isConnected) button.disabled = false;
    }
  }
  function closeDialog(force = false) {
    const form = dialog.querySelector("#ticketForm");
    if (
      !force &&
      form?.dataset.dirty === "true" &&
      !confirm("작성 중인 요청을 닫을까요? 임시 첨부는 정리됩니다.")
    )
      return;
    dialog.close();
  }
  dialog.addEventListener("input", () => {
    const form = dialog.querySelector("#ticketForm");
    if (form) form.dataset.dirty = "true";
  });
  dialog.addEventListener("cancel", (e) => {
    if (
      dialog.querySelector("#ticketForm")?.dataset.dirty === "true" &&
      !confirm("작성 중인 요청을 닫을까요?")
    )
      e.preventDefault();
  });
  function showDialog(title, body) {
    if (dialog.open) dialog.close();
    dialog.innerHTML = `<div class="modalHead"><h2 id="editorTitle">${esc(title)}</h2><button type="button" class="secondary" data-close>닫기 ×</button></div>${body}`;
    dialog.showModal();
    dialog.querySelector("input,textarea,select,button")?.focus();
  }
  async function newTicket(assetId = "") {
    const people = portal
      ? []
      : (await call("/admin/employees?purpose=request")).data;
    if (!portal && !people.length) {
      toast("접수할 수 있는 직원·지원팀 범위를 설정해주세요.");
      return;
    }
    showDialog(
      "새 지원 요청",
      `<form id="ticketForm"><fieldset class="formSection"><legend>무엇을 도와드릴까요?</legend><div class="grid"><label class="fullRow">제목 *<input name="title" required maxlength="200" placeholder="예: 노트북 화면이 깜빡여요"></label>${!portal ? `<label>요청자 *<select name="requester_id">${people.map((p) => `<option value="${p.id}">${esc(p.name)} · ${esc(p.organization)}</option>`).join("")}</select></label>` : `<label>요청자<input value="${esc(me.name)}" readonly></label>`}<label>요청 유형<select name="type"><option value="INCIDENT">장애</option><option value="REQUEST">서비스 요청</option><option value="QUESTION">문의</option></select></label><label>카테고리<select name="category"><option value="DEVICE">PC·주변기기</option><option value="ACCOUNT">계정·권한</option><option value="SOFTWARE">소프트웨어</option><option value="NETWORK">네트워크</option><option value="OTHER">기타</option></select></label><label class="fullRow">관련 자산 (선택)<select name="asset_ids" multiple size="3" aria-describedby="assetHelp"></select><p class="fieldHelp" id="assetHelp">문제가 있는 자산을 선택하세요. 일반 문의는 선택하지 않아도 됩니다.</p></label><label class="fullRow">상세 내용 *<textarea name="body" required maxlength="20000" rows="5" placeholder="언제부터 어떤 문제가 발생했나요? 오류 화면을 복사해 붙여넣으면 첨부됩니다."></textarea></label></div></fieldset>${ItamUploads.markup()}<div class="formError" role="alert"></div><div class="formActions"><button type="button" class="secondary" data-close>취소</button><button type="submit">지원 요청 보내기</button></div></form>`,
    );
    const form = document.getElementById("ticketForm");
    form.onsubmit = (event) => event.preventDefault();
    const sendButton = form.querySelector('[type="submit"]');
    sendButton.disabled = true;
    composer = new ItamUploads(form, call);
    const localComposer = composer;
    const key = crypto.randomUUID();
    let committed = false;
    dialog.addEventListener("close", () => localComposer.dispose(committed), {
      once: true,
    });
    async function loadAssets() {
      const data = portal
        ? await call("/portal/assets")
        : await call(
            `/admin/employees/${form.elements.requester_id.value}/assets`,
          );
      form.elements.asset_ids.innerHTML = data.data
        .map(
          (a) =>
            `<option value="${a.id}" ${a.id === assetId ? "selected" : ""}>${esc(a.number)} · ${esc(a.model)}</option>`,
        )
        .join("");
    }
    if (!portal)
      form.elements.requester_id.onchange = () =>
        loadAssets().catch((e) => toast(e.message));
    try {
      await loadAssets();
    } catch (error) {
      const help = form.querySelector(".formError");
      help.className = "formError notice error";
      help.textContent =
        "자산 목록을 불러오지 못했습니다. 자산을 선택하지 않는 일반 문의는 등록할 수 있습니다.";
    }
    sendButton.disabled = false;
    form.onsubmit = (e) => {
      e.preventDefault();
      submit(form, async () => {
        const f = new FormData(form),
          files = await localComposer.ready();
        const payload = {
          title: f.get("title"),
          body: f.get("body"),
          type: f.get("type"),
          category: f.get("category"),
          asset_ids: f.getAll("asset_ids"),
          ...files,
        };
        if (!portal) payload.requester_id = f.get("requester_id");
        const result = await call(`/${area}/tickets`, {
          method: "POST",
          body: payload,
          headers: { "Idempotency-Key": key },
        });
        committed = true;
        closeDialog(true);
        location.href = ticketPath(result.data.id);
      });
    };
  }
  function transitionDialog(status) {
    showDialog(
      status === "RESOLVED"
        ? "해결 내용 기록"
        : status === "CLOSED"
          ? "해결 확인"
          : "처리 상태 변경",
      `<form id="transitionForm"><p>${esc(states[status])} 상태로 변경합니다. 처리 내용은 요청자에게 공개됩니다.</p><label>내용 · 사유 *<textarea name="reason" required rows="4" maxlength="20000"></textarea></label><div class="formError" role="alert"></div><div class="formActions"><button type="button" class="secondary" data-close>취소</button><button>확인</button></div></form>`,
    );
    const form = document.getElementById("transitionForm");
    form.onsubmit = (e) => {
      e.preventDefault();
      submit(form, async () => {
        await call(`/${area}/tickets/${currentTicket.id}/transition`, {
          method: "POST",
          body: {
            status,
            reason: new FormData(form).get("reason"),
            expected_version: currentTicket.version,
          },
        });
        closeDialog();
        await ticketDetail(currentTicket.id);
        toast("처리 상태를 변경했습니다.");
      });
    };
  }
  async function newAsset() {
    const orgs = (await call("/admin/organizations?purpose=asset")).data;
    if (!orgs.length) {
      toast("자산을 등록할 법인·조직 범위를 먼저 설정해주세요.");
      return;
    }
    showDialog(
      "자산 등록",
      `<form id="assetForm"><div class="grid"><label>관리 조직<select name="organization_id">${orgs.map((o) => `<option value="${o.id}">${esc(o.company)} / ${esc(o.name)}</option>`).join("")}</select></label><label>장비 구분<select name="category"><option>PC</option><option>모니터</option></select></label><label>PC 유형<select name="device_type"><option value="NOTEBOOK">노트북</option><option value="DESKTOP">데스크톱</option></select></label><label>관리번호<input name="number" maxlength="80" placeholder="비워두면 저장 시 자동 채번"></label><label>제조사<input name="maker" maxlength="80"></label><label>모델 *<input name="model" maxlength="160" required></label><label>일련번호<input name="serial" maxlength="160"></label></div><p class="fieldHelp">새 자산은 보관 상태로 등록됩니다. 상세에서 직원에게 배정할 수 있습니다.</p><div class="formError" role="alert"></div><div class="formActions"><button type="button" class="secondary" data-close>취소</button><button>자산 등록</button></div></form>`,
    );
    const form = document.getElementById("assetForm");
    form.onsubmit = (e) => {
      e.preventDefault();
      submit(form, async () => {
        const payload = Object.fromEntries(new FormData(form));
        const result = await call("/admin/assets", {
          method: "POST",
          body: payload,
        });
        closeDialog();
        location.href = "/admin/assets?asset=" + result.data.id;
      });
    };
  }
  async function assignDialog(id, version) {
    const people = (await call("/admin/employees?purpose=assignment")).data;
    showDialog(
      "직원에게 자산 배정",
      `<form id="assignForm"><label>사용 직원<select name="employee_id" required>${people.map((e) => `<option value="${e.id}">${esc(e.name)} · ${esc(e.company)} / ${esc(e.organization)}</option>`).join("")}</select></label><p class="fieldHelp">같은 법인의 재직 직원에게만 배정할 수 있습니다.</p><div class="formError" role="alert"></div><div class="formActions"><button type="button" class="secondary" data-close>취소</button><button>배정 확인</button></div></form>`,
    );
    const form = document.getElementById("assignForm");
    form.onsubmit = (e) => {
      e.preventDefault();
      submit(form, async () => {
        await call(`/admin/assets/${id}/assign`, {
          method: "POST",
          body: {
            expected_version: version,
            employee_id: new FormData(form).get("employee_id"),
          },
        });
        closeDialog();
        await assetDetail(id);
        toast("자산을 배정했습니다.");
      });
    };
  }
  document.addEventListener("click", async (event) => {
    const button = event.target.closest("button");
    if (!button) return;
    try {
      if (button.hasAttribute("data-new-ticket")) await newTicket();
      else if (button.hasAttribute("data-new-asset")) await newAsset();
      else if (button.dataset.requestAsset)
        await newTicket(button.dataset.requestAsset);
      else if (button.dataset.href) location.href = button.dataset.href;
      else if (button.hasAttribute("data-close")) closeDialog();
      else if (button.hasAttribute("data-reload")) await render();
      else if (button.dataset.transition)
        transitionDialog(button.dataset.transition);
      else if (button.hasAttribute("data-claim")) {
        button.disabled = true;
        await call(`/admin/tickets/${currentTicket.id}/claim`, {
          method: "POST",
          body: { expected_version: currentTicket.version },
        });
        await ticketDetail(currentTicket.id);
        toast("요청을 담당하게 되었습니다.");
      } else if (button.dataset.assignAsset)
        await assignDialog(
          button.dataset.assignAsset,
          Number(button.dataset.version),
        );
      else if (
        button.dataset.returnAsset &&
        confirm("현재 사용자 배정을 해제하고 보관 상태로 반납할까요?")
      ) {
        await call(`/admin/assets/${button.dataset.returnAsset}/return`, {
          method: "POST",
          body: { expected_version: Number(button.dataset.version) },
        });
        await assetDetail(button.dataset.returnAsset);
      }
    } catch (error) {
      toast(error.message);
      button.disabled = false;
    }
  });
  async function render() {
    try {
      me = (await call("/auth/me")).data;
      document
        .querySelectorAll(".navItem")
        .forEach((a) =>
          a.classList.toggle(
            "active",
            new URL(a.href).pathname === location.pathname,
          ),
        );
      const create = document.querySelector(".sidebarCreate");
      if (!portal && !me.roles.includes("support_agent")) create.hidden = true;
      if (params.get("ticket")) await ticketDetail(params.get("ticket"));
      else if (location.pathname.endsWith("/assets")) await assets();
      else if (location.pathname.endsWith("/organizations")) await people();
      else if (location.pathname.endsWith("/settings"))
        main.innerHTML =
          heading(
            "시스템 설정",
            "개인 계정·LDAP 설정 UI는 다음 개발 단계에서 연결합니다.",
          ) +
          '<div class="notice">현재 연결 설정은 환경변수와 관리 명령으로 구성합니다. 비밀번호·LDAP 비밀값을 이 화면에 표시하지 않습니다.</div>';
      else if (
        location.pathname.endsWith("/tickets") ||
        location.pathname.endsWith("/requests")
      )
        await tickets();
      else if (portal) await portalHome();
      else await dashboard();
    } catch (error) {
      main.innerHTML = errorPanel(error);
    }
  }
  document.getElementById("refreshPage").onclick = render;
  render();
})();
