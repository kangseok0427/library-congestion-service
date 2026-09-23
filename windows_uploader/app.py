"""Windows Tkinter operator UI."""
import os
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .core import process
from .storage import Credentials, LocalStore
from .upload import PRODUCTION_UPLOAD_ENDPOINT, UploadClient, validate_endpoint


class UploaderApp:
    def __init__(self, root):
        self.root = root
        self.store = LocalStore()
        self.credentials = Credentials()
        self.busy = False
        self.events = queue.Queue()
        root.title("용산꿈나무도서관 Excel 변환·전송")
        root.geometry("690x350")
        frame = ttk.Frame(root, padding=18)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)
        self.source = tk.StringVar()
        self.endpoint = tk.StringVar(value=self.store.read_config().get(
            "endpoint", PRODUCTION_UPLOAD_ENDPOINT))
        self.token = tk.StringVar()
        self.status = tk.StringVar(value="대기 중 · Excel 파일과 인증 토큰을 확인하세요")
        self.last = tk.StringVar(value=self.store.last_success() or "없음")
        self.token_state = tk.StringVar(value="저장됨" if self.credentials.exists() else "없음")
        ttk.Label(frame, text="Excel 파일").grid(row=0, column=0, sticky="w", pady=7)
        ttk.Entry(frame, textvariable=self.source).grid(row=0, column=1, sticky="ew")
        ttk.Button(frame, text="선택", command=self.pick).grid(row=0, column=2, padx=5)
        ttk.Label(frame, text="HTTPS 업로드 주소").grid(row=1, column=0, sticky="w", pady=7)
        ttk.Entry(frame, textvariable=self.endpoint).grid(row=1, column=1, columnspan=2, sticky="ew")
        ttk.Label(frame, text="인증 토큰").grid(row=2, column=0, sticky="w", pady=7)
        ttk.Entry(frame, textvariable=self.token, show="●").grid(row=2, column=1, sticky="ew")
        ttk.Button(frame, text="안전하게 저장", command=self.save_token).grid(row=2, column=2, padx=5)
        ttk.Label(frame, text="토큰 상태").grid(row=3, column=0, sticky="w")
        ttk.Label(frame, textvariable=self.token_state).grid(row=3, column=1, sticky="w")
        ttk.Label(frame, text="최근 성공").grid(row=4, column=0, sticky="w", pady=7)
        ttk.Label(frame, textvariable=self.last).grid(row=4, column=1, sticky="w")
        ttk.Label(frame, textvariable=self.status, wraplength=630).grid(row=5, column=0, columnspan=3, sticky="w", pady=10)
        self.run_button = ttk.Button(frame, text="변환·백업·전송", command=self.start)
        self.run_button.grid(row=6, column=0, columnspan=3, sticky="ew", pady=7)
        ttk.Button(frame, text="로그 폴더 열기", command=lambda: os.startfile(self.store.logs)).grid(row=7, column=0, sticky="w")
        ttk.Button(frame, text="백업 폴더 열기", command=lambda: os.startfile(self.store.backups)).grid(row=7, column=1, sticky="w")

    def pick(self):
        selected = filedialog.askopenfilename(filetypes=[("Excel 통합 문서", "*.xlsx")])
        if selected:
            self.source.set(selected)

    def save_token(self):
        try:
            self.credentials.set(self.token.get())
            self.token.set("")
            self.token_state.set("저장됨")
            self.status.set("인증 토큰을 Windows Credential Manager에 저장했습니다.")
        except Exception:
            self.token.set("")
            messagebox.showerror("저장 실패", "Windows Credential Manager에 토큰을 저장할 수 없습니다.")

    def start(self):
        if self.busy:
            return
        try:
            source = str(self.source.get())
            endpoint = validate_endpoint(self.endpoint.get().strip())
            if not self.credentials.exists():
                raise ValueError("인증 토큰을 먼저 저장하세요.")
            if not source.lower().endswith(".xlsx"):
                raise ValueError(".xlsx Excel 파일을 선택하세요.")
            UploadClient(endpoint)  # Production policy must approve T12 before any work begins.
            self.store.save_config(endpoint)
        except Exception:
            messagebox.showerror("입력 확인", "Excel 파일, HTTPS 주소와 저장된 토큰을 확인하세요. T12 API 계약 확정 후 실제 endpoint 연결 필요")
            return
        self.busy = True
        self.run_button.configure(state="disabled")
        self.status.set("작업 시작")
        threading.Thread(target=self._worker, args=(source, endpoint), daemon=True).start()
        self.root.after(50, self._drain_events)

    def _worker(self, source, endpoint):
        try:
            result = process(source, self.store, self.credentials,
                             UploadClient(endpoint), lambda message: self.events.put(("progress", message)))
        except Exception as exc:
            # Core returns sanitized errors only; never display transport exception text.
            self.events.put(("finish", False, str(exc), None))
        else:
            self.events.put(("finish", True, "전송 완료", result["last_success"]))

    def _drain_events(self):
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            if event[0] == "progress":
                self.status.set(event[1])
            else:
                self._finish(*event[1:])
        if self.busy:
            self.root.after(50, self._drain_events)

    def _finish(self, success, message, when):
        self.busy = False
        self.run_button.configure(state="normal")
        self.status.set(message)
        if success:
            self.last.set(when)
        else:
            messagebox.showerror("처리 실패", message)


def main():
    root = tk.Tk()
    try:
        UploaderApp(root)
    except Exception:
        root.destroy()
        raise SystemExit("Windows Credential Manager 또는 로컬 저장소를 초기화할 수 없습니다.") from None
    root.mainloop()


if __name__ == "__main__":
    main()
