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
          timeZone: me?.time_zone || "Asia/Seoul",
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
    return `<div class="tablewrap ticketTable"><table><thead><tr><th>요청 내용</th><th>요청자</th><th>상태</th><th>우선순위</th><th>담당자</th><th>접수</th></tr></thead><tbody>${tickets.map((t) => `<tr data-id="${t.id}"><td data-label="요청 내용"><a href="${ticketPath(t.id)}"><b>${esc(t.title)}</b></a><div class="muted">${esc(t.number)}</div></td><td data-label="요청자">${esc(t.requester.name)}<div class="muted">${esc(t.requester.organization)}</div></td><td data-label="상태">${pill(t.status_label, t.status)} ${t.overdue ? pill("기한 초과", "HIGH") : ""}</td><td data-label="우선순위">${pill(t.priority_label, t.priority)}</td><td data-label="담당자">${esc(t.assignee)}</td><td data-label="접수">${date(t.created_at)}</td></tr>`).join("") || '<tr><td colspan="6"><div class="emptyState">표시할 요청이 없습니다. 필요한 지원이 있으면 새 요청을 남겨주세요.</div></td></tr>'}</tbody></table></div>`;
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
    const parts = Object.fromEntries(new Intl.DateTimeFormat('en-CA', {timeZone:me?.time_zone || 'Asia/Seoul',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).formatToParts(new Date(s)).map(p=>[p.type,p.value]));
    return `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}`;
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
              )}</select></label><label>목표 처리일<input type="datetime-local" name="due_at" value="${localDateTime(t.due_at)}"></label><p class="fieldHelp">${esc(me.time_zone || "Asia/Seoul")} 기준으로 입력합니다.</p><div class="formError" role="alert"></div><div class="formActions"><button>처리 설정 저장</button></div></form>`
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
                ? data.get("due_at")
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
    if (params.get("asset")) return assetDetail(params.get("asset"));
    const category=params.get("category")||"", company=params.get("company")||"", status=params.get("status")||"", inspection=params.get("inspection")||"", q=params.get("q")||"";
    const query=new URLSearchParams({category,company,status,inspection,q,page:params.get("page")||"1"});
    const result=await call(`/${area}/assets?${query}`);
    const orgs=portal?[]:(await call("/admin/organizations?purpose=asset")).data;
    const companies=[...new Set(orgs.map(o=>o.company))].sort();
    const manage=!portal&&me.roles.includes("asset_manager"), counts=result.meta.categories||{};
    const link=(changes)=>"?"+new URLSearchParams({...Object.fromEntries(query),...changes});
    const options=(items,value)=>items.map(([key,label])=>`<option value="${esc(key)}" ${key===value?"selected":""}>${esc(label)}</option>`).join("");
    main.innerHTML=`<div class="assetListPage"><div class="assetBreadcrumb">자산 / ${portal?"내 자산":"자산 목록"}</div>
      <div class="assetListHeading"><div><h1>${portal?"내 자산":"자산 목록"}</h1><p>장비를 검색하고 사용자 배정과 상태를 관리합니다.</p></div><div class="assetHeadingActions"><button class="secondary" data-reload>새로고침</button>${manage?'<a class="secondary assetExportLink" href="/api/v1/admin/assets/export">Excel 다운로드</a><button data-new-asset>＋ 자산 등록</button>':""}</div></div>
      <div class="assetTypeTabs">${[["","전체 자산"],["PC","PC"],["모니터","모니터"]].map(([key,label])=>`<a href="${link({category:key,page:"1"})}" class="${category===key?"selected":""}">${label} <span>${counts[key||"all"]||0}</span></a>`).join("")}</div>
      <form class="figmaAssetFilters" method="get"><input type="hidden" name="category" value="${esc(category)}"><label>자산 검색<input name="q" placeholder="관리번호, 사용자, 모델, 일련번호 검색" value="${esc(q)}"></label><label>사업부<select name="company">${options([["","전체 사업부"],...companies.map(c=>[c,c])],company)}</select></label><label>상태<select name="status">${options([["","전체 상태"],["IN_USE","사용"],["IN_STOCK","보관"],["REPAIR","수리"],["LOST","분실"],["DISPOSED","폐기"]],status)}</select></label><label>점검 항목<select name="inspection">${options([["","전체 점검 항목"],["aged","5년 이상 노후 자산"],["missing","기준정보 누락"]],inspection)}</select></label><button class="secondary">검색</button><a href="/${area}/assets">초기화</a></form>
      <div class="assetListTools"><div>${manage?'<label class="assetSelectAll"><input type="checkbox" data-select-all-assets> 현재 페이지 전체 선택</label>':""}<b>${result.meta.total}개 자산</b><span>자산을 선택하면 QR 라벨을 인쇄할 수 있습니다.</span></div><div>${manage?'<button class="secondary" data-label-selected disabled>선택 라벨 PDF</button>':""}<button class="secondary" data-column-settings>열 설정</button></div></div>
      <div class="tablewrap figmaAssetTable mobileCards"><table><thead><tr>${manage?'<th>선택</th>':""}<th>관리번호</th><th>사업부 / 부서</th><th>사용자</th><th>장비 / 모델</th><th data-column="spec">사양</th><th>상태</th><th data-column="grade">노후 등급</th></tr></thead><tbody>${result.data.map(a=>`<tr>${manage?`<td data-label="선택"><input class="assetSelect" type="checkbox" value="${a.id}" aria-label="${esc(a.number)} 선택"></td>`:""}<td data-label="관리번호"><a href="?asset=${a.id}">${esc(a.number)}</a></td><td data-label="사업부 / 부서">${esc(a.company)} · ${esc(a.organization)}</td><td data-label="사용자">${esc(a.user)}</td><td data-label="장비 / 모델">${esc(a.category)} · ${esc(a.maker)} / ${esc(a.model)}</td><td data-label="사양" data-column="spec" class="muted">${esc(a.specification||"—")}</td><td data-label="상태"><span class="assetStatus ${a.status}">${esc(a.status_label)}</span></td><td data-label="노후 등급" data-column="grade" class="${a.grade==="C"?"aged":""}">${esc(a.grade)}${a.grade==="C"?" · 5년 이상":""}</td></tr>`).join("")||`<tr><td colspan="${manage?8:7}">표시할 자산이 없습니다.</td></tr>`}</tbody></table></div>
      <div class="assetListPagination"><span>${result.meta.total?((result.meta.page-1)*result.meta.page_size+1):0}–${Math.min(result.meta.page*result.meta.page_size,result.meta.total)} / ${result.meta.total}개</span><div><span>페이지당 ${result.meta.page_size}개</span>${result.meta.page>1?`<a href="${link({page:result.meta.page-1})}">이전</a>`:""}<b>${result.meta.page}</b>${result.meta.page*result.meta.page_size<result.meta.total?`<a href="${link({page:result.meta.page+1})}">다음</a>`:""}</div></div></div>`;
    main.querySelector("[data-column-settings]").onclick=()=>{
      showDialog("열 설정", '<label><input type="checkbox" data-toggle-column="spec" checked> 사양</label><label><input type="checkbox" data-toggle-column="grade" checked> 노후 등급</label><button data-close>닫기</button>');
      dialog.querySelectorAll("[data-toggle-column]").forEach(input=>input.onchange=()=>main.querySelectorAll('[data-column="'+input.dataset.toggleColumn+'"]').forEach(cell=>cell.hidden=!input.checked));
    };
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
      ["비트", meta.bits || "미등록"],
      ["메모리", meta.ram || "미등록"],
      ["저장장치", [meta.ssd && `SSD ${meta.ssd}`, meta.hdd && `HDD ${meta.hdd}`].filter(Boolean).join(" · ") || "미등록"],
      ["그래픽", meta.gpu || "미등록"],
      ["운영체제", [meta.os, meta.os_edition].filter(Boolean).join(" · ") || "미등록"],
      ...(a.category === "모니터"
        ? [["화면 크기", meta.size || "미등록"], ["제조일", meta.manufacture_date || "미등록"]]
        : [["구입일", meta.purchase_date || "미등록"]]),
      ["비고", meta.note || "—"],
    ];
    const softwareRows = [
      ["Office", software.office], ["한글", software.hangul], ["SAP", software.sap],
      ["백신", software.antivirus], ["Docuone", software.docuone],
      ["OfficeKeeper", software.officekeeper], ["기타", software.other],
    ];
    const tickets = a.tickets || [];
    const history = a.history || [];
    const historyLabel = {"asset.created":"자산 등록","asset.updated":"자산 정보 수정","asset.assigned":"직원 배정","asset.returned":"자산 반납"};
    main.innerHTML =
      `<div class="assetBreadcrumb"><a href="/${area}/assets">${portal ? "내 자산" : "자산 목록"}</a><span>›</span><span>자산 상세</span></div>` +
      heading(a.number, `${a.company} · ${a.category} · ${a.model}`, `<span class="pill" data-state="${esc(a.status)}">${esc(a.status_label)}</span>`) +
      `<div class="figmaAssetDetail"><section class="assetPrimary"><section class="assetInfoPanel"><h2 class="sectionTitle">장비 정보</h2><p class="detailPanelDescription">자산 기본 정보와 사양을 확인합니다.</p><div class="assetSpecRows">${deviceRows.map(([k,v]) => `<div class="assetSpecRow"><span>${esc(k)}</span><b>${esc(v)}</b></div>`).join("")}</div></section><section class="assetInfoPanel softwareSection"><h2 class="sectionTitle">소프트웨어</h2><p class="detailPanelDescription">자산에 기록된 소프트웨어 정보입니다.</p><div class="assetSoftwareGrid">${softwareRows.map(([k,v]) => `<div class="assetSoftwareRow"><span>${esc(k)}</span><b>${esc(v || "미기재")}</b></div>`).join("")}</div></section></section><aside class="assetAside"><section class="assetOwnerCard"><h2>현재 사용자</h2><div class="assetOwnerBody"><strong class="ownerName">${esc(a.user)}</strong><p class="ownerMeta">${esc(a.organization)}</p><div class="assetActionStack"><button data-request-asset="${a.id}">이 자산으로 지원 요청</button>${!portal && me.roles.includes("asset_manager") ? `<button class="secondary" data-edit-asset="${a.id}">자산 정보 수정</button>${a.status === "IN_STOCK" ? `<button class="secondary" data-assign-asset="${a.id}" data-version="${a.version}">직원에게 배정</button>` : `<button class="secondary" data-return-asset="${a.id}" data-version="${a.version}">수거 · 반납</button>`}` : ""}</div></div></section>${!portal && me.roles.includes("asset_manager") ? `<section class="assetQrCard"><h2>QR 코드</h2><img src="/api/v1/admin/assets/${a.id}/qr" alt="${esc(a.number)} QR 코드"><button class="secondary" data-label-asset="${a.id}">40mm 라벨 PDF</button></section>` : ""}<section class="assetHistoryCard"><h2>최근 변경 이력</h2>${history.length ? history.map(item => `<div class="assetHistoryItem"><b>${esc(historyLabel[item.action] || item.action)}</b><time>${esc(date(item.created_at))}</time></div>`).join("") : '<p class="muted">기록된 변경 이력이 없습니다.</p>'}</section><section class="assetHistoryCard"><h2>관련 티켓</h2>${tickets.length ? tickets.map(t => `<a class="assetTicketLink" href="/admin/tickets?ticket=${encodeURIComponent(t.id)}"><b>${esc(t.number)}</b><span>${esc(t.title)}</span>${pill(t.status_label, t.status)}</a>`).join("") : '<p class="muted">연결된 티켓이 없습니다.</p>'}</section></aside></div><div class="assetDetailFooter"><a class="secondary" href="/${area}/assets">← 자산 목록</a></div>`;
  }
  async function assetAnalysis() {
    const first = await call("/admin/assets?page=1&page_size=100");
    const assets = [...first.data];
    for (let page = 2; page <= Math.ceil(first.meta.total / first.meta.page_size); page++) {
      const next = await call(`/admin/assets?page=${page}&page_size=100`);
      assets.push(...next.data);
    }
    const statusLabels = {IN_USE:"사용", IN_STOCK:"보관", REPAIR:"수리", LOST:"분실", DISPOSED:"폐기"};
    const statusCounts = Object.fromEntries(Object.keys(statusLabels).map(k => [k, assets.filter(a => a.status===k).length]));
    const pc = assets.filter(a => a.category==="PC").length;
    const monitors = assets.filter(a => a.category==="모니터").length;
    const incomplete = assets.filter(a => (a.category!=="모니터" && !a.maker) || !a.model || !a.serial);
    const bars = Object.entries(statusLabels).map(([key,label]) => {
      const count=statusCounts[key]||0, width=assets.length?Math.round(count/assets.length*100):0;
      return `<div class="analysisBarRow"><span>${label}</span><div class="analysisBarTrack"><i style="width:${width}%"></i></div><b>${count}</b></div>`;
    }).join("");
    main.innerHTML = heading("분석 및 점검", "자산 보유 현황과 기준정보 누락 항목을 확인합니다.", '<a class="secondary" href="/admin/assets">자산 목록 보기</a>') +
      `<div class="cards"><div class="card"><span>전체 자산</span><strong>${assets.length}</strong></div><div class="card"><span>PC</span><strong>${pc}</strong></div><div class="card"><span>모니터</span><strong>${monitors}</strong></div><div class="card"><span>기준정보 점검 필요</span><strong>${incomplete.length}</strong></div></div>
      <div class="demoOverviewGrid"><section class="insightCard"><h3>상태별 자산</h3>${bars}</section><section class="insightCard"><h3>기준정보 누락</h3><p class="muted">제조사, 모델, 시리얼번호 중 하나라도 비어 있는 자산입니다.</p><div class="analysisMissingList">${incomplete.slice(0,20).map(a => `<div><a href="/admin/assets?asset=${a.id}"><b>${esc(a.number)}</b></a><span>${esc(a.company)} · ${esc(a.model || "모델 미등록")}</span><small>${[a.category!=="모니터"&&!a.maker&&"제조사",!a.model&&"모델",!a.serial&&"시리얼"].filter(Boolean).join(" · ")} 누락</small></div>`).join("") || '<p class="muted">점검이 필요한 자산이 없습니다.</p>'}</div>${incomplete.length>20?'<p class="muted">상위 20개만 표시했습니다. 자산 목록에서 나머지를 확인하세요.</p>':""}</section></div>
      <section class="insightCard"><h3>상태별 목록</h3><div class="demoTabs">${Object.entries(statusLabels).map(([key,label])=>`<a class="pill" href="/admin/assets?status=${key}">${label} ${statusCounts[key]||0}</a>`).join("")}</div></section>`;
  }
  async function maintenance() {
    const q = params.get("q") || "";
    const active = params.get("active") || "";
    const query = new URLSearchParams({q, active});
    const result = await call(`/admin/maintenance?${query}`);
    const money = (value) => value === undefined || value === null || value === ""
      ? "—"
      : new Intl.NumberFormat("ko-KR").format(Number(value)) + "원";
    const day = (value) => value
      ? new Date(value + "T00:00:00").toLocaleDateString("ko-KR")
      : "—";
    main.innerHTML = heading(
      "전산 유지보수",
      "유지보수 업체, 계약 기간, 연도별 비용과 만료 예정 계약을 관리합니다.",
      '<button data-new-maintenance>＋ 계약 등록</button>',
    ) + `<form class="toolbar assetFilters" method="get" action="/admin/maintenance">
      <input name="q" placeholder="업체, 서비스, 담당자 검색" value="${esc(q)}" aria-label="계약 검색">
      <select name="active" aria-label="계약 상태"><option value="">전체 상태</option><option value="true" ${active==="true"?"selected":""}>사용 중</option><option value="false" ${active==="false"?"selected":""}>종료</option></select>
      <button class="secondary">검색</button><a class="secondary filterReset" href="/admin/maintenance">초기화</a>
      <span class="muted">${result.meta.total}건</span>
    </form><div class="tablewrap mobileCards"><table><thead><tr><th>업체 · 서비스</th><th>법인 · 조직</th><th>수량</th><th>계약 기간</th><th>연도별 비용</th><th>종료일</th><th>상태</th><th></th></tr></thead><tbody>${result.data.map(c => {
      const expiring = c.remaining !== null && c.remaining <= 60 && c.remaining >= 0;
      const expired = c.remaining !== null && c.remaining < 0;
      const costs = Object.entries(c.costs || {}).sort(([a],[b]) => a.localeCompare(b)).map(([year,amount]) => `${esc(year)}: ${money(amount)}`).join("<br>") || "—";
      return `<tr><td data-label="업체 · 서비스"><b>${esc(c.vendor)}</b><div class="muted">${esc(c.service)}</div></td><td data-label="법인 · 조직">${esc(c.company)} · ${esc(c.organization || "공통")}</td><td data-label="수량">${esc(c.quantity)}</td><td data-label="계약 기간">${day(c.starts_on)} ~ ${day(c.ends_on)}</td><td data-label="연도별 비용">${costs}</td><td data-label="종료일">${day(c.ends_on)}<div class="muted">${c.remaining === null ? "" : expired ? `만료 ${Math.abs(c.remaining)}일 경과` : `D-${c.remaining}`}</div></td><td data-label="상태">${pill(!c.active ? "종료" : expired ? "만료" : expiring ? "만료 예정" : "사용 중", !c.active || expired ? "CLOSED" : expiring ? "HIGH" : "IN_PROGRESS")}</td><td data-label="관리"><button class="secondary" data-edit-contract="${c.id}">수정</button></td></tr>`;
    }).join("") || '<tr><td colspan="8"><div class="emptyState">등록된 유지보수 계약이 없습니다.</div></td></tr>'}</tbody></table></div>`;
  }
  async function maintenanceEditor(contract) {
    const orgs = (await call("/admin/organizations?purpose=asset")).data;
    if (!orgs.length) return toast("계약을 등록할 법인·조직 범위를 먼저 설정해주세요.");
    const year = new Date().getFullYear();
    const years = [...new Set([year-1, year, year+1, ...Object.keys(contract?.costs || {}).map(Number)])].sort();
    const orgValue = contract?.organization_id || orgs[0].id;
    showDialog(contract ? "유지보수 계약 수정" : "유지보수 계약 등록", `<form id="maintenanceForm">
      <fieldset class="formSection"><legend>계약 정보</legend><div class="grid">
        <label>관리 조직<select name="organization_id">${orgs.map(o => `<option value="${o.id}" ${o.id===orgValue?"selected":""}>${esc(o.company)} / ${esc(o.name)}</option>`).join("")}</select></label>
        <label>업체명 *<input name="vendor" required maxlength="160" value="${esc(contract?.vendor || "")}"></label>
        <label>서비스명 *<input name="service" required maxlength="200" value="${esc(contract?.service || "")}"></label>
        <label>수량<input type="number" name="quantity" min="1" max="100000" value="${esc(contract?.quantity || 1)}"></label>
        <label>계약 주기<input name="period" maxlength="120" placeholder="예: 연간, 월간" value="${esc(contract?.period || "")}"></label>
        <label>시작일<input type="date" name="starts_on" value="${esc(contract?.starts_on || "")}"></label>
        <label>종료일<input type="date" name="ends_on" value="${esc(contract?.ends_on || "")}"></label>
        <label>담당 연락처<input name="contact" maxlength="200" value="${esc(contract?.contact || "")}"></label>
        <label class="fullRow">비고<textarea name="note" maxlength="5000">${esc(contract?.note || "")}</textarea></label>
      </div></fieldset>
      <fieldset class="formSection"><legend>연도별 비용</legend><div class="grid">${years.map(y => `<label>${y}년 비용<input type="number" min="0" step="0.01" name="cost_${y}" value="${esc(contract?.costs?.[y] || "")}" placeholder="원 단위"></label>`).join("")}</div></fieldset>
      ${contract ? `<label class="checkRow"><input type="checkbox" name="active" ${contract.active?"checked":""}> 계약 사용 중</label>` : ""}
      <div class="formError" role="alert"></div><div class="formActions"><button type="button" class="secondary" data-close>취소</button><button>${contract?"변경 저장":"계약 등록"}</button></div>
    </form>`);
    const form = document.getElementById("maintenanceForm");
    form.onsubmit = (event) => {
      event.preventDefault();
      submit(form, async () => {
        const values = Object.fromEntries(new FormData(form));
        const costs = {};
        years.forEach(y => { if (values[`cost_${y}`] !== "") costs[String(y)] = values[`cost_${y}`]; });
        const payload = {
          organization_id: values.organization_id, vendor: values.vendor, service: values.service,
          quantity: Number(values.quantity), period: values.period, starts_on: values.starts_on || null,
          ends_on: values.ends_on || null, costs, contact: values.contact, note: values.note,
        };
        if (contract) {
          payload.expected_version = contract.version;
          payload.active = form.elements.active.checked;
          await call(`/admin/maintenance/${contract.id}`, {method:"PATCH", body:payload});
        } else {
          await call("/admin/maintenance", {method:"POST", body:payload});
        }
        closeDialog(); await maintenance(); toast(contract ? "계약을 수정했습니다." : "계약을 등록했습니다.");
      });
    };
  }
  async function people() {
    const employment = ["active", "retired", "all"].includes(params.get("employment")) ? params.get("employment") : "active";
    const [orgResult, first] = await Promise.all([
      call("/admin/organizations?directory=1&employment=" + employment),
      call("/admin/employees?directory=1&page_size=100&employment=" + employment),
    ]);
    const employees = [...first.data];
    if (first.meta) {
      for (let page = 2; page <= Math.ceil(first.meta.total / first.meta.page_size); page++) {
        employees.push(...(await call(`/admin/employees?directory=1&page_size=100&employment=${employment}&page=${page}`)).data);
      }
    }
    const orgs = orgResult.data;
    const byId = new Map(orgs.map(o => [o.id, o]));
    const children = new Map(orgs.map(o => [o.id, []]));
    const roots = new Map();
    const companies = [...new Set([...orgs.map(o => o.company), ...employees.map(e => e.company)])].sort();
    companies.forEach(code => roots.set(code, []));
    // Invalid, cross-company, or cyclic parent chains become visible roots.
    function validParent(o) {
      let parent = byId.get(o.parent_id);
      if (!parent || parent.company !== o.company) return null;
      const seen = new Set([o.id]);
      let cursor = parent;
      while (cursor) {
        if (seen.has(cursor.id)) return null;
        seen.add(cursor.id);
        cursor = byId.get(cursor.parent_id);
      }
      return parent;
    }
    for (const o of orgs) {
      const parent = validParent(o);
      if (parent) children.get(parent.id).push(o);
      else roots.get(o.company).push(o);
    }
    function breadcrumb(id) {
      const labels = [], seen = new Set();
      let node = byId.get(id);
      while (node && !seen.has(node.id)) {
        seen.add(node.id); labels.unshift(node.name); node = validParent(node);
      }
      return labels.join(" / ");
    }
    const sortOrg = (a, b) => (a.sort_order || 0) - (b.sort_order || 0) || a.name.localeCompare(b.name, "ko");
    children.forEach(list => list.sort(sortOrg));
    roots.forEach(list => list.sort(sortOrg));
    const descendants = new Map();
    function subtree(id) {
      if (descendants.has(id)) return descendants.get(id);
      const ids = new Set([id]);
      descendants.set(id, ids);
      (children.get(id) || []).forEach(child => subtree(child.id).forEach(key => ids.add(key)));
      return ids;
    }
    orgs.forEach(o => subtree(o.id));
    const directPeople = new Map();
    employees.forEach(e => {
      if (!directPeople.has(e.organization_id)) directPeople.set(e.organization_id, []);
      directPeople.get(e.organization_id).push(e);
    });
    directPeople.forEach(list => list.sort((a,b) => a.name.localeCompare(b.name,"ko")));
    let selected = byId.has(params.get("organization"))
      ? {kind:"org", id:params.get("organization")} : {kind:"all", id:""};
    let search = "", company = "";
    const expanded = new Set(companies.map(c => "company:" + c));
    if (selected.kind === "org") {
      let node = byId.get(selected.id);
      const seen = new Set();
      while (node && !seen.has(node.id)) {
        seen.add(node.id); expanded.add("org:" + node.id);
        node = byId.get(node.parent_id);
      }
    }
    main.innerHTML = heading("사용자 · 조직", "법인과 부서를 펼쳐 사용자를 찾고, 선택한 조직의 소속 사용자를 확인하세요.") +
      `<div class="directoryControls"><label><span class="sr-only">재직 상태</span><select id="directoryEmployment">${[["active","재직자"],["retired","퇴사자"],["all","전체 사용자"]].map(([value,label]) => `<option value="${value}" ${value===employment?"selected":""}>${label}</option>`).join("")}</select></label><label><span class="sr-only">조직 또는 사용자 검색</span><input id="directorySearch" type="search" placeholder="이름, 이메일, 부서 검색" autocomplete="off"></label><label><span class="sr-only">법인</span><select id="directoryCompany"><option value="">전체 법인</option>${companies.map(c => `<option value="${esc(c)}">${esc(c)}</option>`).join("")}</select></label></div>
      <div class="directoryLayout"><aside class="directoryTreePanel"><div class="directoryPanelHead"><h2>조직도</h2><div><button type="button" class="secondary" data-tree-expand>모두 펼치기</button><button type="button" class="secondary" data-tree-collapse>접기</button></div></div><nav id="directoryTree" aria-label="법인 및 부서별 사용자"></nav></aside><section class="directoryPeoplePanel" id="directoryPeople" aria-live="polite"></section></div>`;
    document.getElementById("directoryEmployment").onchange = event => {
      const url = new URL(location.href); url.searchParams.set("employment",event.target.value); url.searchParams.delete("organization"); location.href=url.toString();
    };
    const tree = document.getElementById("directoryTree");
    const panel = document.getElementById("directoryPeople");
    function organizationPath(employee) {
      const names = [];
      const seen = new Set();
      let node = byId.get(employee.organization_id);
      while (node && !seen.has(node.id)) {
        seen.add(node.id); names.push(node.name); node = byId.get(node.parent_id);
      }
      return names.join(" ");
    }
    const matches = e => !search || [e.name,e.email,e.organization,e.company,organizationPath(e)].join(" ").toLocaleLowerCase().includes(search);
    const branchMatch = id => !search || [...subtree(id)].some(key => {
      const org = byId.get(key);
      return [org?.name,org?.company].join(" ").toLocaleLowerCase().includes(search) ||
        (directPeople.get(key) || []).some(matches);
    });
    const isSelected = (kind,id) => selected.kind === kind && selected.id === id ? " isSelected" : "";
    function selectButton(kind,id,label,count,extra="") {
      return `<button type="button" class="directoryNode${isSelected(kind,id)} ${extra}" data-directory-kind="${kind}" data-directory-id="${esc(id)}"><span>${esc(label)}</span>${count === null ? "" : `<small>${count}</small>`}</button>`;
    }
    function orgMarkup(o) {
      if (!branchMatch(o.id)) return "";
      const own = directPeople.get(o.id) || [];
      const total = employees.filter(e => e.company === o.company && subtree(o.id).has(e.organization_id)).length;
      const label = o.name === o.company && !o.parent_id ? "법인 공통" : o.name;
      return `<details class="directoryBranch" data-tree-key="org:${o.id}" ${search || expanded.has("org:"+o.id) ? "open" : ""}><summary>${selectButton("org",o.id,label,total)}</summary><div class="directoryChildren">${(children.get(o.id) || []).map(orgMarkup).join("")}${own.filter(matches).map(e => selectButton("user",e.id,e.name,null,"directoryPerson")).join("")}${!own.length && !(children.get(o.id)||[]).length ? '<span class="directoryEmptyBranch">등록된 사용자 없음</span>' : ""}</div></details>`;
    }
    function drawTree() {
      tree.innerHTML = selectButton("all","","전체 사용자",employees.length) +
        companies.filter(c => !company || c===company).map(c => {
          const list = roots.get(c) || [];
          const visible = !search || c.toLocaleLowerCase().includes(search) || list.some(o => branchMatch(o.id)) || employees.some(e => e.company===c && matches(e));
          if (!visible) return "";
          const unplaced = employees.filter(e => e.company===c && !byId.has(e.organization_id));
          return `<details class="directoryCompanyBranch" data-tree-key="company:${esc(c)}" ${search || expanded.has("company:"+c) ? "open" : ""}><summary>${selectButton("company",c,c,employees.filter(e => e.company===c).length)}</summary><div class="directoryChildren">${list.map(orgMarkup).join("")}${unplaced.filter(matches).map(e => selectButton("user",e.id,e.name,null,"directoryPerson")).join("")}</div></details>`;
        }).join("");
    }
    function drawPeople() {
      let list = employees.filter(e => (!company || e.company===company) && matches(e));
      let title = employment === "retired" ? "퇴사자" : "전체 사용자", description = "그룹웨어 부서 매핑과 재직 상태를 기준으로 표시합니다.";
      if (selected.kind === "company") {
        list = list.filter(e => e.company===selected.id); title=selected.id;
        description="법인 전체 사용자를 표시합니다.";
      } else if (selected.kind === "org") {
        const o=byId.get(selected.id);
        list=list.filter(e => e.company===o.company && subtree(o.id).has(e.organization_id));
        title=o.name; description=o.company + " · 하위 조직 사용자를 포함합니다.";
      } else if (selected.kind === "user") {
        list=list.filter(e => e.id===selected.id);
        title=employees.find(e => e.id===selected.id)?.name || "사용자";
        description="선택한 사용자의 소속과 연락처입니다.";
      }
      list.sort((a,b)=>a.name.localeCompare(b.name,"ko"));
      panel.innerHTML=`<div class="directorySelectionHead"><div><h2>${esc(title)}</h2><p>${esc(description)}</p></div><span class="directoryCount">${list.length}명</span></div>${list.length ? `<div class="tablewrap mobileCards"><table class="directoryTable"><thead><tr><th>이름</th><th>계정 ID</th><th>상태</th><th>법인</th><th>소속 부서</th><th>이메일</th><th>LDAP 부서</th><th>관리</th></tr></thead><tbody>${list.map(e=>`<tr><td data-label="이름"><b>${esc(e.name)}</b></td><td data-label="계정 ID">${esc(e.groupware_uid || e.directory_uid || "미연결")}</td><td data-label="상태">${e.active === false ? "퇴사" : "재직"}</td><td data-label="법인">${esc(e.company_label || e.company)}</td><td data-label="소속 부서">${esc(e.organization)}</td><td data-label="이메일">${e.email ? `<a href="mailto:${esc(e.email)}">${esc(e.email)}</a>` : '<span class="muted">미등록</span>'}</td><td data-label="LDAP 부서">${esc(({synced:"반영 완료",pending:"반영 대기",failed:"반영 실패"})[e.ldap_department?.status] || "미반영")}${e.ldap_department?.message ? `<small class="muted">${esc(e.ldap_department.message)}</small>` : ""}</td><td data-label="관리">${e.active !== false ? `<button type="button" class="secondary" data-department-edit="${esc(e.id)}">부서 지정 / 재반영</button>` : "—"}</td></tr>`).join("")}</tbody></table></div>` : '<div class="directoryEmpty">조건에 맞는 사용자가 없습니다.<br><small>조직을 선택하거나 검색 조건을 변경해주세요.</small></div>'}`;
    }
    panel.onclick = event => {
      const button = event.target.closest("[data-department-edit]");
      if (!button) return;
      const employee = employees.find(e => e.id === button.dataset.departmentEdit);
      showDialog("부서 지정 · NAS LDAP 반영", `<form id="departmentForm"><p>${esc(employee.name)} · ${esc(employee.directory_uid || employee.groupware_uid)}</p><label>소속 부서<select name="organization_id" required>${orgs.map(o => `<option value="${esc(o.id)}" ${o.id === employee.organization_id ? "selected" : ""}>${esc(o.company + " · " + breadcrumb(o.id))}</option>`).join("")}</select></label><p class="muted">부서를 저장한 뒤 NAS LDAP 부서 칸에 반영합니다. 실패하면 부서는 저장되며, 같은 버튼으로 다시 반영할 수 있습니다.</p><div class="formError" role="alert"></div><button type="submit">저장 및 LDAP 반영</button></form>`);
      const form = document.getElementById("departmentForm");
      form.onsubmit = event => {
        event.preventDefault();
        submit(form, async () => {
          const result = await call(`/admin/employees/${employee.id}/department`, {method:"POST", body:{organization_id:form.elements.organization_id.value, expected_version:employee.version}});
          closeDialog(); await people();
          toast(result.data.ldap_department.status === "synced" ? "부서 저장 및 NAS LDAP 반영 완료" : "부서는 저장했습니다. LDAP 반영 실패 사유를 확인하세요.");
        });
      };
    };
    tree.addEventListener("toggle", event => {
      const key=event.target.dataset.treeKey;
      if (key) event.target.open ? expanded.add(key) : expanded.delete(key);
    }, true);
    tree.onclick = event => {
      const button=event.target.closest("[data-directory-kind]");
      if (!button) return;
      event.preventDefault(); event.stopPropagation();
      selected={kind:button.dataset.directoryKind,id:button.dataset.directoryId};
      tree.querySelectorAll(".isSelected").forEach(node=>node.classList.remove("isSelected"));
      button.classList.add("isSelected");
      const url=new URL(location.href);
      if (selected.kind==="org") url.searchParams.set("organization",selected.id);
      else url.searchParams.delete("organization");
      history.replaceState(null,"",url);
      drawPeople();
    };
    document.getElementById("directorySearch").oninput = event => {
      search=event.target.value.trim().toLocaleLowerCase();drawTree();drawPeople();
    };
    document.getElementById("directoryCompany").onchange = event => {
      company=event.target.value;selected={kind:"all",id:""};drawTree();drawPeople();
    };
    main.querySelector("[data-tree-expand]").onclick=()=>{
      companies.forEach(c=>expanded.add("company:"+c));orgs.forEach(o=>expanded.add("org:"+o.id));drawTree();
    };
    main.querySelector("[data-tree-collapse]").onclick=()=>{expanded.clear();drawTree();};
    drawTree();drawPeople();
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
  async function newAsset() { return assetEditor(null); }
  async function assetEditor(asset) {
    const orgs = (await call("/admin/organizations?purpose=asset")).data;
    if (!orgs.length) return toast("자산을 등록할 법인·조직 범위를 먼저 설정해주세요.");
    const meta = asset?.legacy_data || {}, sw = meta.software || {};
    const v = (key) => esc(meta[key] || "");
    const orgValue = asset?.organization_id || orgs[0].id;
    showDialog(asset ? "자산 정보 수정" : "자산 등록", `<form id="assetForm">
      <fieldset class="formSection"><legend>기본 정보</legend><div class="grid">
        <label>관리 조직<select name="organization_id">${orgs.map(o => `<option value="${o.id}" ${o.id === orgValue ? "selected" : ""}>${esc(o.company)} / ${esc(o.name)}</option>`).join("")}</select></label>
        <label>장비 구분<select name="category"><option value="PC" ${asset?.category === "PC" || !asset ? "selected" : ""}>PC</option><option value="모니터" ${asset?.category === "모니터" ? "selected" : ""}>모니터</option></select></label>
        <label>PC 유형<select name="device_type"><option value="NOTEBOOK" ${meta.device_type !== "DESKTOP" ? "selected" : ""}>노트북</option><option value="DESKTOP" ${meta.device_type === "DESKTOP" ? "selected" : ""}>데스크톱</option></select></label>
        <label>관리번호<input name="number" maxlength="80" value="${esc(asset?.number || "")}" ${asset ? "required" : ""} placeholder="비워두면 자동 채번"></label>
        <label>제조사<input name="maker" maxlength="80" value="${esc(asset?.maker || "")}"></label>
        <label>모델 *<input name="model" maxlength="160" value="${esc(asset?.model || "")}" required></label>
        <label>일련번호<input name="serial" maxlength="160" value="${esc(asset?.serial || "")}"></label>
        ${asset ? `<label>상태<select name="status">${[["IN_USE","사용"],["IN_STOCK","보관"],["REPAIR","수리"],["LOST","분실"],["DISPOSED","폐기"]].map(([key,label]) => `<option value="${key}" ${asset.status === key ? "selected" : ""}>${label}</option>`).join("")}</select></label>` : ""}
      </div></fieldset>
      <fieldset class="formSection"><legend>상세 사양</legend><div class="grid">
        <label>CPU<input name="cpu" maxlength="160" value="${v("cpu")}"></label><label>비트<input name="bits" maxlength="40" value="${v("bits")}"></label><label>RAM<input name="ram" maxlength="80" value="${v("ram")}"></label>
        <label>SSD<input name="ssd" maxlength="80" value="${v("ssd")}"></label><label>HDD<input name="hdd" maxlength="80" value="${v("hdd")}"></label>
        <label>그래픽<input name="gpu" maxlength="160" value="${v("gpu")}"></label><label>운영체제<input name="os" maxlength="120" value="${v("os")}"></label>
        <label>OS 에디션<input name="os_edition" maxlength="120" value="${v("os_edition")}"></label><label>화면 크기<input name="size" maxlength="40" value="${v("size")}"></label>
        <label>${asset?.category === "모니터" ? "제조일" : "구입일"}<input type="date" name="${asset?.category === "모니터" ? "manufacture_date" : "purchase_date"}" value="${v(asset?.category === "모니터" ? "manufacture_date" : "purchase_date")}"></label><label class="fullRow">비고<textarea name="note" maxlength="2000">${v("note")}</textarea></label>
      </div></fieldset>
      <fieldset class="formSection"><legend>소프트웨어</legend><div class="grid">
        <label>Office<input name="sw_office" maxlength="120" value="${esc(sw.office || "")}"></label><label>한글<input name="sw_hangul" maxlength="120" value="${esc(sw.hangul || "")}"></label>
        <label>SAP<input name="sw_sap" maxlength="120" value="${esc(sw.sap || "")}"></label><label>백신<input name="sw_antivirus" maxlength="120" value="${esc(sw.antivirus || "")}"></label>
        <label>Docuone<input name="sw_docuone" maxlength="120" value="${esc(sw.docuone || "")}"></label><label>OfficeKeeper<input name="sw_officekeeper" maxlength="120" value="${esc(sw.officekeeper || "")}"></label>
        <label class="fullRow">기타<input name="sw_other" maxlength="500" value="${esc(sw.other || "")}"></label>
      </div></fieldset>
      <p class="fieldHelp">${asset ? "수정 내용은 변경 이력에 기록됩니다." : "새 자산은 보관 상태로 등록됩니다. 저장 후 직원에게 배정할 수 있습니다."}</p>
      <div class="formError" role="alert"></div><div class="formActions"><button type="button" class="secondary" data-close>취소</button><button>${asset ? "변경 저장" : "자산 등록"}</button></div></form>`);
    const form = document.getElementById("assetForm");
    const dateField = form.querySelector('input[type="date"]');
    if (dateField && form.elements.category) {
      form.elements.category.onchange = () => {
        const isMonitor = form.elements.category.value === "모니터";
        dateField.name = isMonitor ? "manufacture_date" : "purchase_date";
        dateField.parentElement.firstChild.textContent = isMonitor ? "제조일" : "구입일";
      };
    }
    form.onsubmit = (event) => {
      event.preventDefault();
      submit(form, async () => {
        const values = Object.fromEntries(new FormData(form));
        const legacy_data = {
          device_type: values.device_type, cpu: values.cpu, bits: values.bits, ram: values.ram, ssd: values.ssd,
          hdd: values.hdd, gpu: values.gpu, os: values.os, os_edition: values.os_edition,
          size: values.size, purchase_date: values.purchase_date, manufacture_date: values.manufacture_date, note: values.note,
          software: {office: values.sw_office, hangul: values.sw_hangul, sap: values.sw_sap,
            antivirus: values.sw_antivirus, docuone: values.sw_docuone,
            officekeeper: values.sw_officekeeper, other: values.sw_other},
        };
        Object.keys(legacy_data).forEach(key => { if (legacy_data[key] === "") delete legacy_data[key]; });
        Object.keys(legacy_data.software).forEach(key => { if (!legacy_data.software[key]) delete legacy_data.software[key]; });
        const payload = {organization_id: values.organization_id, number: values.number, category: values.category,
          maker: values.maker, model: values.model, serial: values.serial, legacy_data};
        if (asset) {
          payload.expected_version = asset.version;
          payload.status = values.status;
          const result = await call(`/admin/assets/${asset.id}`, {method:"PATCH", body:payload});
          closeDialog(); await assetDetail(result.data.id); toast("자산 정보를 수정했습니다.");
        } else {
          payload.device_type = values.device_type;
          const result = await call("/admin/assets", {method:"POST", body:payload});
          closeDialog(); location.href = "/admin/assets?asset=" + result.data.id;
        }
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
      else if (button.hasAttribute("data-new-maintenance")) await maintenanceEditor(null);
      else if (button.dataset.editContract) {
        const contract = (await call("/admin/maintenance")).data.find(c => c.id === button.dataset.editContract);
        if (!contract) throw Error("계약 정보를 찾을 수 없습니다.");
        await maintenanceEditor(contract);
      }
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
      } else if (button.dataset.labelAsset || button.hasAttribute("data-label-selected")) {
        const assetIds = button.dataset.labelAsset
          ? [button.dataset.labelAsset]
          : [...document.querySelectorAll(".assetSelect:checked")].map(input => input.value);
        if (!assetIds.length) throw Error("라벨을 출력할 자산을 선택하세요.");
        const response = await fetch("/api/v1/admin/assets/labels", {
          method: "POST",
          credentials: "same-origin",
          headers: {
            "Content-Type": "application/json",
            "X-CSRFToken": document.querySelector('meta[name="csrf-token"]').content,
          },
          body: JSON.stringify({asset_ids: assetIds}),
        });
        if (!response.ok) {
          const result = await response.json().catch(() => ({}));
          throw Error(result.error?.message || "라벨 PDF를 만들지 못했습니다.");
        }
        const blob = await response.blob();
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = url; link.download = "Bumjin_QR_labels.pdf"; link.click();
        URL.revokeObjectURL(url);
      } else if (button.dataset.editAsset) {
        const asset = (await call(`/admin/assets/${button.dataset.editAsset}`)).data;
        await assetEditor(asset);
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
  document.addEventListener("change", event => {
    if (event.target.matches("[data-select-all-assets]")) {
      document.querySelectorAll(".assetSelect").forEach(input => { input.checked = event.target.checked; });
    }
    if (event.target.matches(".assetSelect, [data-select-all-assets]")) {
      const selected = document.querySelectorAll(".assetSelect:checked").length;
      const action = document.querySelector("[data-label-selected]");
      if (action) { action.disabled = selected === 0; action.textContent = `선택 라벨 PDF (${selected})`; }
      const all = document.querySelector("[data-select-all-assets]");
      if (all) all.checked = selected > 0 && selected === document.querySelectorAll(".assetSelect").length;
    }
  });
  async function adminSettings() {
    const [mailResult, ldapResult, systemResult, grantResult, accessResult] = await Promise.all([
      call('/admin/settings/mail'), call('/admin/settings/ldap'), call('/admin/settings/system'), call('/admin/settings/grants'), call('/admin/settings/access')
    ]);
    const labels = {
      ENABLED:'메일 알림 사용',PUBLIC_BASE_URL:'프로그램 접속 주소',FROM_EMAIL:'보내는 이메일',REPLY_ADDRESS:'답장 수신 이메일',
      SMTP_HOST:'SMTP 호스트',SMTP_PORT:'SMTP 포트',SMTP_USER:'SMTP 계정',SMTP_PASSWORD:'SMTP 비밀번호',SMTP_USE_TLS:'STARTTLS 사용 (587)',SMTP_USE_SSL:'SSL 사용 (465)',
      INBOUND_ENABLED:'메일 답장 자동 등록',IMAP_HOST:'IMAP 호스트',IMAP_PORT:'IMAP 포트',IMAP_USER:'IMAP 계정',IMAP_PASSWORD:'IMAP 비밀번호',IMAP_FOLDER:'수신 폴더',IMAP_CA_FILE:'IMAP CA 인증서 경로',AUTH_SERV_ID:'수신 인증 게이트웨이 ID',
      LDAP_HOST:'LDAP 호스트',LDAP_PORT:'LDAPS 포트',LDAP_BASE_DN:'Base DN',LDAP_BIND_DN:'연결 계정 Bind DN',LDAP_BIND_PASSWORD:'연결 비밀번호',LDAP_CA_FILE:'CA 인증서 경로',LDAP_DIRECTORY_SOURCE:'디렉터리 소스 이름',LDAP_ID_ATTRIBUTE:'사용자 고유 ID 속성',LDAP_USER_FILTER:'사용자 조회 필터',LDAP_DEPARTMENT_ATTRIBUTE:'NAS 부서 속성',LDAP_DEPARTMENT_WRITE_ENABLED:'NAS 부서 쓰기 사용'
    };
    Object.assign(labels,{VPN_WRITE_ENABLED:'VPN 그룹 쓰기 사용',PASSWORD_WRITE_ENABLED:'NAS 비밀번호 변경 사용',LDAP_GROUPS_DN:'LDAP 그룹 Base DN',GROUP_MEMBER_ATTRIBUTE:'그룹 멤버 속성 (member 또는 memberUid)',VPN_GROUPS:'관리할 그룹 (|로 구분)',GW_VPN_GROUP:'그룹웨어 VPN 대상 그룹',GW_URL:'그룹웨어 HTTPS 주소',GW_USERNAME:'그룹웨어 연동 계정',GW_PASSWORD:'그룹웨어 비밀번호',GW_COOKIE:'그룹웨어 세션 쿠키 (선택)',GW_CA_FILE:'그룹웨어 CA 인증서 경로',VPN_INTERNAL_URL:'VPN 내부 안내 주소 (선택)',VPN_EXTERNAL_URL:'VPN 외부 안내 주소 (선택)'});
    const booleans = new Set(['ENABLED','SMTP_USE_TLS','SMTP_USE_SSL','INBOUND_ENABLED','LDAP_DEPARTMENT_WRITE_ENABLED','VPN_WRITE_ENABLED','PASSWORD_WRITE_ENABLED']);
    function fields(group, result) {
      const d=result.data;
      return Object.keys(labels).filter(k => Object.hasOwn(d.values,k)||Object.hasOwn(d.secret_configured,k)).map(k => {
        const secret=k.endsWith('PASSWORD')||k==='GW_COOKIE', value=d.values[k] ?? '';
        return `<label>${esc(labels[k])}${booleans.has(k) ? `<select name="${k}"><option value="0" ${String(value)==='0'?'selected':''}>꺼짐</option><option value="1" ${String(value)==='1'?'selected':''}>켜짐</option></select>` : `<input name="${k}" type="${secret?'password':k.endsWith('PORT')?'number':'text'}" ${k.endsWith('PORT')?'min="1" max="65535"':''} value="${secret?'':esc(value)}" ${secret?'autocomplete="new-password"':''} placeholder="${secret?(d.secret_configured[k]?'저장됨 · 빈칸이면 유지':'새 비밀번호 입력'):''}">`}</label>`;
      }).join('');
    }
    const roleNames={system_admin:'시스템 관리자',asset_manager:'자산 관리자',support_agent:'티켓 담당자',organization_manager:'조직 관리자'};
    main.innerHTML = heading('관리자 설정', `로그 표시 시간대: ${me.time_zone}. 권한과 연결 설정 변경 이력을 기록합니다.`) + `
      <nav class="settingsTabs" aria-label="설정 종류">${[['permissions','사용자 권한'],['mail','SMTP · 수신 메일'],['ldap','LDAP 연결'],['access','VPN · NAS'],['system','시간대'],['history','변경 이력']].map(([id,label])=>`<button class="secondary" data-settings-tab="${id}" aria-pressed="${id==='permissions'}">${label}</button>`).join('')}</nav>
      <section data-settings-panel="permissions" class="settingsPanel"><h2>LDAP 사용자 관리자 권한</h2><p>LDAP 연결을 완료한 재직자를 검색하고 역할·법인 범위를 지정하세요.</p><form id="settingsUserSearch" class="toolbar"><input name="q" aria-label="LDAP 사용자 검색" placeholder="이름 또는 LDAP 계정"><button type="submit">검색</button></form><div id="settingsAccounts"></div>
      <form id="grantForm"><h3 id="selectedAccount">사용자를 선택하세요</h3><input type="hidden" name="employee_id"><div class="grid"><label>역할<select name="role">${grantResult.data.roles.map(r=>`<option value="${r}">${esc(roleNames[r]||r)}</option>`).join('')}</select></label><label>권한 범위<select name="scope_type"><option value="COMPANY">선택 법인</option><option value="GLOBAL">전체 법인</option></select></label><label>법인<select name="company_id">${grantResult.data.companies.map(c=>`<option value="${c.id}">${esc(c.code)}</option>`).join('')}</select></label><label>지원팀 (티켓 담당자)<select name="team_id"><option value="">선택</option>${grantResult.data.teams.map(t=>`<option value="${t.id}">${esc(t.name)}</option>`).join('')}</select></label></div><p class="muted">시스템 관리자는 전체 범위를 선택합니다. 티켓 담당자는 지원팀도 선택해야 합니다.</p><div class="formError" role="alert"></div><button type="submit">권한 부여</button></form></section>
      ${[['mail','SMTP · 수신 메일',mailResult],['ldap','LDAP 연결',ldapResult],['access','VPN · NAS',accessResult]].map(([group,title,result])=>`<section data-settings-panel="${group}" class="settingsPanel" hidden><h2>${title}</h2><p>${group==='access'?'VPN 그룹과 비밀번호 쓰기는 기본 꺼짐입니다. LDAP 연결 테스트 후 테스트 계정으로 검증하세요. 그룹웨어 연동은 계정/비밀번호 또는 세션 쿠키를 사용합니다.':group==='mail'?'발송은 SMTP, 메일 답장 등록은 IMAP 설정을 사용합니다. 수신 게이트웨이 인증 설정을 확인한 뒤 자동 등록을 켜세요.':'기존 LDAP 계정 연결·로그인·부서 반영에 같은 설정을 사용합니다. LDAPS 인증서를 검증합니다.'}</p>${!result.data.encryption_ready?'<div class="notice">비밀번호 저장 전에 서버 암호화 키를 설정해야 합니다. ADMIN_SETTINGS.md를 확인하세요.</div>':''}<form data-settings-form="${group}" data-version="${result.data.version}"><div class="grid">${fields(group,result)}</div><p class="muted">비밀번호 빈칸은 기존 값을 유지합니다. 연결 테스트는 저장한 값으로 인증만 확인합니다.</p><div class="formError" role="alert"></div><div class="actions"><button type="submit">설정 저장</button>${group!=='access'?`<button type="button" class="secondary" data-connection-test="${group}">저장한 연결 테스트</button>`:''}</div></form><p data-test-result="${group}" role="status"></p></section>`).join('')}
      <section data-settings-panel="system" class="settingsPanel" hidden><h2>시간대 설정</h2><p>업무 처리 기록은 UTC로 저장하고 선택한 시간대로 표시합니다. 서버 시계는 운영체제의 NTP 설정을 사용합니다.</p><form data-settings-form="system" data-version="${systemResult.data.version}"><label>업무 표시 시간대<select name="TIME_ZONE">${systemResult.data.timezones.map(z=>`<option value="${esc(z)}" ${z===systemResult.data.values.TIME_ZONE?'selected':''}>${esc(z)}</option>`).join('')}</select></label><p>현재 시각: <b>${esc(date(new Date().toISOString()))}</b></p><div class="formError" role="alert"></div><button type="submit">시간대 저장</button></form></section>
      <section data-settings-panel="history" class="settingsPanel" hidden><h2>설정 · 권한 변경 이력</h2><div id="settingsHistory"></div></section>`;
    function tab(name) {
      main.querySelectorAll('[data-settings-panel]').forEach(p=>p.hidden=p.dataset.settingsPanel!==name);
      main.querySelectorAll('[data-settings-tab]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.settingsTab===name)));
    }
    main.querySelectorAll('[data-settings-tab]').forEach(b=>b.onclick=()=>tab(b.dataset.settingsTab));
    main.querySelectorAll('[data-settings-form]').forEach(form=>form.onsubmit=event=>{
      event.preventDefault();submit(form,async()=>{
        const group=form.dataset.settingsForm, values=Object.fromEntries(new FormData(form));
        const result=await call('/admin/settings/'+group,{method:'PATCH',body:{values,version:Number(form.dataset.version)}});
        form.dataset.version=result.data.version;
        form.querySelectorAll('input[type="password"]').forEach(input=>{input.value='';input.placeholder='저장됨 · 빈칸이면 유지';});
        if(group==='system') {me.time_zone=values.TIME_ZONE;await adminSettings();tab('system');}
        toast('설정을 저장했습니다.');await loadHistory();
      });
    });
    main.querySelectorAll('[data-connection-test]').forEach(button=>button.onclick=async()=>{
      const group=button.dataset.connectionTest;button.disabled=true;
      try {const r=await call(`/admin/settings/${group}/test`,{method:'POST',body:{}});main.querySelector(`[data-test-result="${group}"]`).textContent=r.data.message;await loadHistory();}
      catch(error){toast(error.message);}finally{button.disabled=false;}
    });
    let accountPage=1, query='';
    async function loadAccounts(page=1) {
      const result=await call('/admin/settings/accounts?'+new URLSearchParams({q:query,page,page_size:20}));accountPage=page;
      const target=document.getElementById('settingsAccounts');
      target.innerHTML=result.data.map(e=>`<article class="settingsAccount"><button type="button" class="secondary" data-pick-account="${e.id}">${esc(e.name)} · ${esc(e.uid)} · ${esc(e.company)}</button><div>${e.grants.map(g=>`<span>${esc(roleNames[g.role]||g.role)} · ${g.scope_type==='GLOBAL'?'전체':esc(grantResult.data.companies.find(c=>c.id===g.company_id)?.code||'법인')} <button type="button" class="secondary" data-revoke-grant="${g.id}">해제</button></span>`).join('')||'<small>관리 권한 없음</small>'}</div></article>`).join('')||'<p>검색된 LDAP 사용자가 없습니다. 계정 동기화를 먼저 실행하세요.</p>';
      target.innerHTML+=`<div class="page"><span>${result.meta.total}명 · ${page}페이지</span>${page>1?'<button type="button" class="secondary" data-account-prev>이전</button>':''}${page*result.meta.page_size<result.meta.total?'<button type="button" class="secondary" data-account-next>다음</button>':''}</div>`;
      target.querySelectorAll('[data-pick-account]').forEach(button=>button.onclick=()=>{
        const e=result.data.find(e=>e.id===button.dataset.pickAccount);document.getElementById('grantForm').elements.employee_id.value=e.id;document.getElementById('selectedAccount').textContent=e.name+' · '+e.uid;
      });
      target.querySelectorAll('[data-revoke-grant]').forEach(button=>button.onclick=async()=>{
        if(!confirm('이 관리 권한을 해제할까요?'))return;
        try {await call('/admin/settings/grants',{method:'DELETE',body:{grant_id:Number(button.dataset.revokeGrant)}});await loadAccounts(accountPage);await loadHistory();toast('권한을 해제했습니다.');}catch(error){toast(error.message);}
      });
      const prev=target.querySelector('[data-account-prev]'),next=target.querySelector('[data-account-next]');
      if(prev)prev.onclick=()=>loadAccounts(page-1).catch(e=>toast(e.message));if(next)next.onclick=()=>loadAccounts(page+1).catch(e=>toast(e.message));
    }
    document.getElementById('settingsUserSearch').onsubmit=event=>{event.preventDefault();query=new FormData(event.target).get('q');loadAccounts().catch(e=>toast(e.message));};
    const grantForm=document.getElementById('grantForm');
    grantForm.onsubmit=event=>{event.preventDefault();submit(grantForm,async()=>{
      const payload=Object.fromEntries(new FormData(grantForm));if(payload.scope_type==='GLOBAL')delete payload.company_id;
      await call('/admin/settings/grants',{method:'POST',body:payload});await loadAccounts(accountPage);await loadHistory();toast('권한을 부여했습니다.');
    });};
    async function loadHistory(page=1) {
      const result=await call('/admin/settings/history?page='+page);
      const target=document.getElementById('settingsHistory');
      target.innerHTML=result.data.map(e=>`<article class="settingsAudit"><time datetime="${esc(e.created_at)}">${esc(date(e.created_at))}</time><b>${esc(e.action)}</b><span>변경자 ID: ${esc(e.actor)}</span><small>${esc(JSON.stringify(e.data))}</small></article>`).join('')||'<p>변경 이력이 없습니다.</p>';
      target.innerHTML+=`<div class="page">${page>1?'<button class="secondary" data-history-prev>이전</button>':''}${page*result.meta.page_size<result.meta.total?'<button class="secondary" data-history-next>다음</button>':''}</div>`;
      const prev=target.querySelector('[data-history-prev]'),next=target.querySelector('[data-history-next]');if(prev)prev.onclick=()=>loadHistory(page-1).catch(e=>toast(e.message));if(next)next.onclick=()=>loadHistory(page+1).catch(e=>toast(e.message));
    }
    await Promise.all([loadAccounts(),loadHistory()]);
  }

  async function directoryManagement() {
    const [leaseResult, approvalsResult, historyResult] = await Promise.all([call('/admin/directory/leases?page_size=30'),call('/admin/directory/approvals'),call('/admin/directory/history')]);
    let leasePage=1,accountPage=1,accountQuery='',leaseQuery='',approvalPage=1,historyPage=1;
    let leases=leaseResult.data,accounts=[],approvals=approvalsResult.data;
    const groups=leaseResult.groups||[];
    main.innerHTML=heading('VPN · NAS 계정','VPN 사용 기간과 NAS 비밀번호를 통합 관리합니다. 변경 이력은 서버 시각으로 기록합니다.')+
      `<nav class="settingsTabs">${[['leases','VPN 권한'],['accounts','NAS 비밀번호 초기화'],['approvals','그룹웨어 VPN 결재'],['history','처리 이력']].map(([id,label])=>`<button class="secondary" data-directory-tab="${id}" aria-pressed="${id==='leases'}">${label}</button>`).join('')}</nav>
      <div class="notice">${leaseResult.write_enabled?'VPN 쓰기 사용 중':'VPN 쓰기 꺼짐'} · <a href="/admin/settings">관리자 설정 → VPN·NAS</a>에서 연결·쓰기 설정을 확인하세요. 시작/만료 자동 처리는 별도 작업자가 실행되어야 합니다.</div>
      <section class="settingsPanel" data-directory-panel="leases"><form id="vpnSearch" class="toolbar"><input name="q" placeholder="계정·이름·그룹 검색" aria-label="VPN 검색"><button>검색</button><button class="secondary" type="button" data-go-accounts>사용자 선택 후 권한 등록</button></form><div id="vpnLeases"></div></section>
      <section class="settingsPanel" data-directory-panel="accounts" hidden><h2>연결된 LDAP 사용자</h2><form id="nasSearch" class="toolbar"><input name="q" placeholder="이름 또는 LDAP 계정" aria-label="NAS 계정 검색"><button>검색</button></form><div id="nasAccounts"></div></section>
      <section class="settingsPanel" data-directory-panel="approvals" hidden><h2>그룹웨어 VPN 결재</h2><p>결재 완료 문서를 가져온 뒤 사용자와 기간을 확인하여 등록합니다. 이름만으로 자동 권한을 부여하지 않습니다.</p><button type="button" data-gw-fetch>최신 결재 가져오기</button><div id="vpnApprovals"></div></section>
      <section class="settingsPanel" data-directory-panel="history" hidden><h2>VPN · 비밀번호 처리 이력</h2><div id="directoryHistory"></div></section>`;
    const tab=name=>{main.querySelectorAll('[data-directory-panel]').forEach(p=>p.hidden=p.dataset.directoryPanel!==name);main.querySelectorAll('[data-directory-tab]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.directoryTab===name)));};
    main.querySelectorAll('[data-directory-tab]').forEach(b=>b.onclick=()=>tab(b.dataset.directoryTab));
    main.querySelector('[data-go-accounts]').onclick=()=>tab('accounts');
    function pagination(meta,name){return `<div class="page"><span>${meta.total}건 · ${meta.page}페이지</span>${meta.page>1?`<button class="secondary" type="button" data-${name}-prev>이전</button>`:''}${meta.page*meta.page_size<meta.total?`<button class="secondary" type="button" data-${name}-next>다음</button>`:''}</div>`;}
    function nav(target,name,page,loader){const prev=target.querySelector(`[data-${name}-prev]`),next=target.querySelector(`[data-${name}-next]`);if(prev)prev.onclick=()=>loader(page-1).catch(e=>toast(e.message));if(next)next.onclick=()=>loader(page+1).catch(e=>toast(e.message));}
    async function loadLeases(page=1,ready=null){const r=ready||await call('/admin/directory/leases?'+new URLSearchParams({page,q:leaseQuery,page_size:30}));leases=r.data;leasePage=page;const target=document.getElementById('vpnLeases');target.innerHTML=leases.map(l=>`<article class="directoryAccessCard"><div><b>${esc(l.name)} · ${esc(l.uid)}</b><span>${esc(l.group)}</span><span>${esc(l.starts_on)} ~ ${l.permanent?'상시':esc(l.expires_on)}</span><small>${esc(l.status)} · ${esc(l.sync_state)} ${esc(l.error_code)}</small><small>${esc(l.description)}</small></div><div class="directoryAccessActions"><button type="button" class="secondary" data-lease-edit="${l.id}">기간/계정 수정</button><button type="button" data-lease-apply="${l.id}">NAS 반영 / 재시도</button>${l.enabled?`<button type="button" class="secondary" data-lease-revoke="${l.id}">권한 해제</button>`:''}</div></article>`).join('')||'<p>등록된 VPN 권한이 없습니다.</p>';target.innerHTML+=pagination(r.meta,'lease');nav(target,'lease',page,loadLeases);}
    async function loadAccounts(page=1){const r=await call('/admin/directory/accounts?'+new URLSearchParams({page,q:accountQuery,page_size:20}));accounts=r.data;accountPage=page;const target=document.getElementById('nasAccounts');target.innerHTML=accounts.map(e=>`<article class="directoryAccessCard"><div><b>${esc(e.name)} · ${esc(e.uid)}</b><span>${esc(e.company)} · ${esc(e.email||'메일 미등록')}</span></div><div class="directoryAccessActions"><button type="button" class="secondary" data-new-lease="${e.id}">VPN 권한 등록</button><button type="button" data-reset-nas="${e.id}">비밀번호 초기화</button></div></article>`).join('')||'<p>연결된 LDAP 사용자가 없습니다. 그룹웨어/LDAP 계정 동기화를 먼저 실행하세요.</p>';target.innerHTML+=pagination(r.meta,'account');nav(target,'account',page,loadAccounts);}
    async function loadApprovals(page=1,ready=null){const r=ready||await call('/admin/directory/approvals?page='+page);approvals=r.data;approvalPage=page;const target=document.getElementById('vpnApprovals');target.innerHTML=r.data.map(a=>`<article class="directoryAccessCard"><div><b>${esc(a.number||a.id)} · ${esc(a.drafter)}</b><span>${esc(a.starts_on||'시작일 미확인')} ~ ${esc(a.expires_on||'종료일 미확인')}</span><small>${esc(a.status)} · ${esc(a.reason)}</small></div>${!['applied','legacy_processed'].includes(a.status)?`<div class="directoryAccessActions"><button data-review-approval="${esc(a.id)}">사용자 확인 후 등록</button><button class="secondary" data-exclude-approval="${esc(a.id)}">제외</button></div>`:''}</article>`).join('')||'<p>가져온 결재가 없습니다.</p>';target.innerHTML+=pagination(r.meta,'approval');nav(target,'approval',page,loadApprovals);}
    async function loadHistory(page=1,ready=null){const r=ready||await call('/admin/directory/history?page='+page);historyPage=page;const target=document.getElementById('directoryHistory');target.innerHTML=r.data.map(e=>`<article class="settingsAudit"><time>${esc(date(e.created_at))}</time><b>${esc(e.action)}</b><span>변경자: ${esc(e.actor||'작업자/이관')}</span><small>${esc(JSON.stringify(e.data))}</small></article>`).join('')||'<p>처리 이력이 없습니다.</p>';target.innerHTML+=pagination(r.meta,'dirhistory');nav(target,'dirhistory',page,loadHistory);}
    function userPicker(form,initial){const search=form.querySelector('[data-picker-search]'),button=form.querySelector('[data-picker-load]'),select=form.elements.employee_id;button.onclick=async()=>{try{const r=await call('/admin/directory/accounts?'+new URLSearchParams({q:search.value,page_size:100}));select.innerHTML=r.data.map(e=>`<option value="${e.id}">${esc(e.name)} · ${esc(e.uid)}</option>`).join('');if(initial&&r.data.some(e=>e.id===initial.id))select.value=initial.id;if(r.meta.total>100)toast('검색어를 더 구체적으로 입력하세요.');}catch(e){toast(e.message);}};if(initial)select.innerHTML=`<option value="${initial.id}">${esc(initial.name)} · ${esc(initial.uid)}</option>`;}
    const pickerMarkup=`<div class="toolbar"><input data-picker-search placeholder="이름/계정으로 사용자 검색" aria-label="사용자 검색"><button type="button" class="secondary" data-picker-load>사용자 검색</button></div><label>LDAP 사용자<select name="employee_id" required></select></label>`;
    function leaseEditor(employee=null,lease=null){showDialog(lease?'VPN 권한 수정':'VPN 권한 등록',`<form id="vpnEditor">${pickerMarkup}<div class="grid"><label>그룹<select name="group">${groups.map(g=>`<option ${g===lease?.group?'selected':''}>${esc(g)}</option>`).join('')}</select></label><label>권한 종류<select name="permanent"><option value="0">기간 권한</option><option value="1" ${lease?.permanent?'selected':''}>상시 권한</option></select></label><label>시작일<input type="date" name="starts_on" required value="${esc(lease?.starts_on||'')}"></label><label>만료일<input type="date" name="expires_on" value="${esc(lease?.expires_on||'')}"></label><label>설명<input name="description" maxlength="500" value="${esc(lease?.description||'')}"></label></div><p class="muted">저장 후 NAS 반영을 실행하세요. 만료일 당일은 사용 가능하며 다음 날 해제됩니다.</p><div class="formError" role="alert"></div><button type="submit">권한 저장</button></form>`);const form=document.getElementById('vpnEditor');userPicker(form,employee||(lease?.employee_id?{id:lease.employee_id,name:lease.name,uid:lease.uid}:null));form.onsubmit=e=>{e.preventDefault();submit(form,async()=>{const payload=Object.fromEntries(new FormData(form));payload.permanent=payload.permanent==='1';if(lease)payload.expected_version=lease.version;await call('/admin/directory/leases',{method:'POST',body:payload});closeDialog();await loadLeases();tab('leases');await loadHistory();toast('권한을 저장했습니다. NAS 반영을 실행하세요.');});};}
    main.onclick=async event=>{const button=event.target.closest('button');if(!button)return;try{
      if(button.dataset.newLease)leaseEditor(accounts.find(e=>e.id===button.dataset.newLease));
      else if(button.dataset.leaseEdit)leaseEditor(null,leases.find(l=>l.id===button.dataset.leaseEdit));
      else if(button.dataset.leaseApply||button.dataset.leaseRevoke){const revoke=!!button.dataset.leaseRevoke;const lease=leases.find(l=>l.id===(button.dataset.leaseApply||button.dataset.leaseRevoke));if(!confirm(revoke?'이 VPN 그룹 권한을 해제할까요?':'저장한 기간에 맞춰 NAS VPN 그룹을 반영할까요?'))return;button.disabled=true;const r=await call(`/admin/directory/leases/${lease.id}/${revoke?'revoke':'apply'}`,{method:'POST',body:{expected_version:lease.version}});await loadLeases(leasePage);await loadHistory();toast(r.data.sync_state==='synced'?'NAS 반영 완료':'반영 실패. 설정/오류 코드를 확인하고 재시도하세요.');}
      else if(button.dataset.resetNas){const employee=accounts.find(e=>e.id===button.dataset.resetNas);showDialog('NAS 비밀번호 초기화',`<form id="nasReset"><p>${esc(employee.name)} · ${esc(employee.uid)}</p><label>초기화 사유<input name="reason" required maxlength="500"></label><label>현재 관리자 비밀번호<input name="operator_password" type="password" required autocomplete="current-password"></label><p>임시 비밀번호는 한 번만 표시됩니다. 사용자에게 안전하게 전달하세요.</p><div class="formError" role="alert"></div><button type="submit">비밀번호 초기화 실행</button></form>`);const form=document.getElementById('nasReset');form.onsubmit=e=>{e.preventDefault();submit(form,async()=>{const payload=Object.fromEntries(new FormData(form));const r=await call(`/admin/directory/accounts/${employee.id}/reset-password`,{method:'POST',body:payload});form.elements.operator_password.value='';showDialog('임시 비밀번호 · 한 번만 표시',`<p>${esc(r.data.uid)} · ${esc(r.data.message)}</p><label>임시 비밀번호<input id="temporaryNasPassword" type="password" readonly autocomplete="off"></label><button class="secondary" type="button" id="toggleTemporaryPassword">표시 / 숨김</button><button type="button" data-close>확인 후 닫기</button>`);const field=document.getElementById('temporaryNasPassword');field.value=r.data.temporary_password;document.getElementById('toggleTemporaryPassword').onclick=()=>field.type=field.type==='password'?'text':'password';await loadHistory();});};}
      else if(button.hasAttribute('data-gw-fetch')){button.disabled=true;const r=await call('/admin/directory/approvals',{method:'POST',body:{action:'fetch'}});await loadApprovals();await loadHistory();toast(`결재 ${r.data.fetched}건 조회 · 신규 ${r.data.new}건`);}
      else if(button.dataset.excludeApproval){const a=approvals.find(a=>a.id===button.dataset.excludeApproval);await call('/admin/directory/approvals',{method:'POST',body:{action:'exclude',document_id:a.id,expected_version:a.version}});await loadApprovals(approvalPage);await loadHistory();}
      else if(button.dataset.reviewApproval){const a=approvals.find(a=>a.id===button.dataset.reviewApproval);showDialog('VPN 결재 사용자 확인',`<form id="vpnReview"><p>${esc(a.number)} · ${esc(a.drafter)}</p><p>${esc(a.starts_on)} ~ ${esc(a.expires_on)} · ${esc(a.reason)}</p>${pickerMarkup}<div class="formError" role="alert"></div><button type="submit">확인한 사용자로 VPN 권한 등록</button></form>`);const form=document.getElementById('vpnReview');userPicker(form,null);form.onsubmit=e=>{e.preventDefault();submit(form,async()=>{await call('/admin/directory/approvals',{method:'POST',body:{action:'approve',document_id:a.id,expected_version:a.version,employee_id:form.elements.employee_id.value}});closeDialog();await loadApprovals();await loadLeases();await loadHistory();toast('검토 결과를 저장했습니다. VPN 권한 탭에서 NAS에 반영하세요.');});};}
    }catch(error){toast(error.message);}finally{if(button.isConnected)button.disabled=false;}};
    document.getElementById('vpnSearch').onsubmit=e=>{e.preventDefault();leaseQuery=new FormData(e.target).get('q');loadLeases().catch(e=>toast(e.message));};document.getElementById('nasSearch').onsubmit=e=>{e.preventDefault();accountQuery=new FormData(e.target).get('q');loadAccounts().catch(e=>toast(e.message));};
    await Promise.all([loadLeases(1,leaseResult),loadAccounts(),loadApprovals(1,approvalsResult),loadHistory(1,historyResult)]);
  }

  async function myDirectoryAccount(){const r=(await call('/portal/account')).data;main.innerHTML=heading('내 NAS 계정 · VPN','현재 비밀번호를 확인한 뒤 NAS 비밀번호를 변경할 수 있습니다.')+`<section class="settingsPanel"><h2>${esc(r.uid||'LDAP 미연결')}</h2>${(r.vpn_links||[]).map(link=>`<p><a href="${esc(link.url)}" target="_blank" rel="noopener noreferrer">${esc(link.label)}</a></p>`).join('')}${r.leases.map(l=>`<article class="directoryAccessCard"><div><b>${esc(l.group)}</b><span>${esc(l.starts_on)} ~ ${l.permanent?'상시':esc(l.expires_on)}</span><small>${esc(l.status)} · ${esc(l.sync_state)}</small></div></article>`).join('')||'<p>등록된 VPN 사용 기간이 없습니다.</p>'}${r.linked&&r.password_enabled?`<form id="myNasPassword"><label>현재 비밀번호<input type="password" name="current_password" required autocomplete="current-password"></label><label>새 비밀번호 (12자 이상)<input type="password" name="new_password" required minlength="12" maxlength="128" autocomplete="new-password"></label><label>새 비밀번호 확인<input type="password" name="confirm_password" required autocomplete="new-password"></label><div class="formError" role="alert"></div><button type="submit">NAS 비밀번호 변경</button></form>`:'<div class="notice">LDAP 계정 연결과 비밀번호 쓰기 설정을 관리자에게 확인하세요.</div>'}</section>`;const form=document.getElementById('myNasPassword');if(form)form.onsubmit=e=>{e.preventDefault();submit(form,async()=>{const result=await call('/portal/account',{method:'POST',body:Object.fromEntries(new FormData(form))});form.reset();toast(result.data.message);});};}

  async function render() {
    try {
      me = (await call("/auth/me")).data;
      document
        .querySelectorAll(".navItem")
        .forEach((a) => {
          const target = new URL(a.href);
          const category = target.searchParams.get("category");
          a.classList.toggle(
            "active",
            target.pathname === location.pathname &&
              (category ? category === params.get("category") : !params.has("category")),
          );
        });
      const create = document.querySelector(".sidebarCreate");
      if (!portal && !me.roles.includes("support_agent")) create.hidden = true;
      if (params.get("ticket")) await ticketDetail(params.get("ticket"));
      else if (location.pathname.endsWith("/assets")) await assets();
      else if (location.pathname.endsWith("/maintenance")) await maintenance();
      else if (location.pathname.endsWith("/asset-analysis")) await assetAnalysis();
      else if (location.pathname.endsWith("/organizations")) await people();
      else if (location.pathname.endsWith("/directory")) await directoryManagement();
      else if (location.pathname.endsWith("/account")) await myDirectoryAccount();
      else if (location.pathname.endsWith("/settings")) await adminSettings();
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
  dialog.addEventListener('close',()=>{dialog.querySelectorAll('input[type="password"],#temporaryNasPassword').forEach(field=>field.value='');});
  document.getElementById("refreshPage").onclick = render;
  render();
})();



