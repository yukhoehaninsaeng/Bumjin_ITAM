/* Clipboard images and file selection share the same authenticated upload path. */
(() => {
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
  const types = new Set([
    "png",
    "jpg",
    "jpeg",
    "webp",
    "pdf",
    "txt",
    "csv",
    "xlsx",
    "docx",
  ]);
  class Uploads {
    static markup() {
      return `<fieldset class="formSection"><legend>사진 · 파일 첨부 <span class="muted">선택</span></legend><div class="uploadDrop" tabindex="0"><b>사진이나 파일을 끌어놓으세요</b><p>오류 화면을 복사한 뒤 요청 내용 또는 이 영역에서<br><strong>Ctrl+V / ⌘+V</strong>로 붙여넣을 수 있어요.</p><button type="button" class="secondary chooseFiles">사진 · 파일 선택</button><input class="fileInput" type="file" multiple accept=".png,.jpg,.jpeg,.webp,.pdf,.txt,.csv,.xlsx,.docx" hidden><small class="muted">최대 5개 · 파일당 20MB · PNG/JPG/WEBP, PDF/TXT/CSV/Word/Excel</small></div><p class="uploadErrors" role="alert"></p><p class="fileCount muted" aria-live="polite">첨부 0 / 5</p><div class="fileCards"></div></fieldset>`;
    }
    constructor(form, call) {
      this.form = form;
      this.call = call;
      this.files = [];
      this.draft = null;
      this.disposed = false;
      this.chain = Promise.resolve();
      this.drop = form.querySelector(".uploadDrop");
      this.input = form.querySelector(".fileInput");
      form.querySelector(".chooseFiles").onclick = () => this.input.click();
      this.input.onchange = () => {
        this.add(Array.from(this.input.files));
        this.input.value = "";
      };
      this.paste = (e) => {
        const items = Array.from(e.clipboardData?.items || []).filter(
          (i) => i.kind === "file" && i.type.startsWith("image/"),
        );
        if (!items.length) return;
        if (!e.clipboardData.getData("text/plain")) e.preventDefault();
        this.add(
          items
            .map((i, n) => {
              const file = i.getAsFile();
              if (!file) return null;
              const ext =
                {
                  "image/png": "png",
                  "image/jpeg": "jpg",
                  "image/webp": "webp",
                }[file.type] || "unsupported";
              return new File(
                [file],
                `스크린샷-${Date.now()}-${n + 1}.${ext}`,
                { type: file.type },
              );
            })
            .filter(Boolean),
        );
      };
      form.addEventListener("paste", this.paste);
      this.drop.ondragover = (e) => {
        e.preventDefault();
        this.drop.classList.add("dragging");
      };
      this.drop.ondragleave = () => this.drop.classList.remove("dragging");
      this.drop.ondrop = (e) => {
        e.preventDefault();
        this.drop.classList.remove("dragging");
        this.add(Array.from(e.dataTransfer.files));
      };
      this.preventNavigation = (e) => {
        if (
          e.dataTransfer?.types?.includes("Files") &&
          !this.drop.contains(e.target)
        ) {
          e.preventDefault();
          this.error("파일을 첨부 영역 안에 놓아주세요.");
        }
      };
      form.addEventListener("dragover", this.preventNavigation);
      form.addEventListener("drop", this.preventNavigation);
    }
    error(text) {
      if (!this.disposed)
        this.form.querySelector(".uploadErrors").textContent = text;
    }
    add(files) {
      for (const file of files) {
        const ext = file.name.split(".").pop().toLowerCase();
        if (this.files.length >= 5) {
          this.error("최대 5개까지 첨부할 수 있어요.");
          continue;
        }
        if (!file.size || file.size > 20 * 1024 * 1024 || !types.has(ext)) {
          this.error(`${file.name}: 지원 형식과 20MB 제한을 확인해주세요.`);
          continue;
        }
        const item = {
          file,
          name: file.name,
          url: file.type.startsWith("image/") ? URL.createObjectURL(file) : "",
          state: "pending",
          id: null,
        };
        this.files.push(item);
        this.render();
        this.chain = this.chain.then(async () => {
          if (this.disposed) return;
          try {
            if (!this.draft)
              this.draft = (
                await this.call("/uploads/drafts", { method: "POST", body: {} })
              ).data.id;
            const data = new FormData();
            data.append("file", file);
            const result = (
              await this.call(`/uploads/drafts/${this.draft}/files`, {
                method: "POST",
                body: data,
              })
            ).data;
            item.id = result.id;
            item.state = "ready";
            if (this.disposed) {
              await this.call(
                `/uploads/drafts/${this.draft}/files/${item.id}`,
                { method: "DELETE" },
              ).catch(() => {});
              return;
            }
          } catch (error) {
            item.state = "failed";
            this.error(error.message);
          }
          this.render();
        });
      }
    }
    render() {
      if (this.disposed) return;
      this.form.querySelector(".fileCount").textContent =
        `첨부 ${this.files.length} / 5`;
      this.form.querySelector(".fileCards").innerHTML = this.files
        .map(
          (f, i) =>
            `<div class="fileCard">${f.url ? `<img src="${f.url}" alt="${esc(f.name)} 미리보기">` : '<span aria-hidden="true">▤</span>'}<div class="fileInfo"><strong>${esc(f.name)}</strong><small>${(f.file.size / 1024).toFixed(1)} KB · ${f.state === "pending" ? "업로드 중…" : f.state === "failed" ? "실패 · 삭제 후 다시 첨부" : "첨부 준비 완료"}</small></div><button class="secondary" type="button" data-remove-file="${i}" ${f.state === "pending" ? "disabled" : ""} aria-label="${esc(f.name)} 삭제">×</button></div>`,
        )
        .join("");
      this.form.querySelectorAll("[data-remove-file]").forEach(
        (button) =>
          (button.onclick = async () => {
            const i = Number(button.dataset.removeFile),
              f = this.files[i];
            button.disabled = true;
            try {
              if (f.id)
                await this.call(`/uploads/drafts/${this.draft}/files/${f.id}`, {
                  method: "DELETE",
                });
              if (f.url) URL.revokeObjectURL(f.url);
              this.files.splice(i, 1);
              this.error("");
              this.render();
            } catch (error) {
              this.error(error.message);
              button.disabled = false;
            }
          }),
      );
    }
    async ready() {
      await this.chain;
      if (this.files.some((f) => f.state !== "ready"))
        throw Error("실패한 첨부를 삭제하거나 다시 첨부해주세요.");
      return this.draft
        ? { draft_id: this.draft, attachment_ids: this.files.map((f) => f.id) }
        : {};
    }
    dispose(committed = false) {
      this.disposed = true;
      this.form.removeEventListener("paste", this.paste);
      this.form.removeEventListener("dragover", this.preventNavigation);
      this.form.removeEventListener("drop", this.preventNavigation);
      for (const file of this.files) {
        if (file.url) URL.revokeObjectURL(file.url);
        if (!committed && file.id)
          this.call(`/uploads/drafts/${this.draft}/files/${file.id}`, {
            method: "DELETE",
          }).catch(() => {});
      }
    }
  }
  window.ItamUploads = Uploads;
})();
