from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QTextEdit, QFileDialog, QProgressBar
)
from PySide6.QtCore import QThread, Signal, Qt
import pathlib
import traceback

class DicomConvertWorker(QThread):
    log = Signal(str)
    done = Signal(bool, str)
    progress = Signal(int, int)  # current, total
    result_dir = Signal(str)  # output directory used

    def __init__(self, root_dir: str, out_dir: str | None = None):
        super().__init__()
        self.root_dir = root_dir
        self.out_dir = out_dir
        self._stop = False

    def run(self):
        try:
            self._convert(self.root_dir)
            if self._stop or self.isInterruptionRequested():
                self.done.emit(False, "已取消")
            else:
                self.done.emit(True, "转换完成")
        except Exception as e:
            self.log.emit(f"错误: {e}\n{traceback.format_exc()}")
            self.done.emit(False, str(e))

    def _convert(self, root_dir: str):
        import SimpleITK as sitk
        root = pathlib.Path(root_dir)
        if not root.exists():
            raise FileNotFoundError(f"目录不存在: {root_dir}")
        self.log.emit(f"开始转换，根目录: {root_dir}")

        # 预扫描可转换序列目录
        candidates = []
        for folder_path in root.rglob("*"):
            if folder_path.is_dir():
                dicom_reader = sitk.ImageSeriesReader()
                try:
                    dicom_names = dicom_reader.GetGDCMSeriesFileNames(str(folder_path))
                except RuntimeError:
                    dicom_names = []
                if dicom_names:
                    candidates.append((folder_path, dicom_names))

        total = len(candidates)
        self.progress.emit(0, total)
        count = 0
        # 输出路径：同级可自定义，默认 root.parent / 'nii_outputs'
        out_base = pathlib.Path(self.out_dir) if self.out_dir else (root.parent / 'nii_outputs')
        out_base.mkdir(parents=True, exist_ok=True)
        try:
            self.result_dir.emit(str(out_base))
        except Exception:
            pass
        for idx, (folder_path, dicom_names) in enumerate(candidates, start=1):
            if self._stop or self.isInterruptionRequested():
                self.log.emit("[跳过] 用户取消，停止后续转换")
                break
            try:
                dicom_reader = sitk.ImageSeriesReader()
                dicom_reader.SetFileNames(dicom_names)
                image = dicom_reader.Execute()
                nii_path = out_base / (folder_path.name + ".nii.gz")
                sitk.WriteImage(image, str(nii_path))
                count += 1
                self.log.emit(f"[完成] {folder_path.name} → {nii_path}")
            except Exception as e:
                self.log.emit(f"[失败] {folder_path}: {e}")
            self.progress.emit(idx, total)

        self.log.emit(f"总计转换序列: {count}")

    def stop(self):
        try:
            self._stop = True
            self.requestInterruption()
        except Exception:
            self._stop = True

class DicomConvertWindow(QDialog):
    converted = Signal(str, str)  # root_dir, out_dir
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("DICOM 转 NIfTI")
        try:
            self.resize(640, 420)
        except Exception:
            pass
        # 独立弹窗：非模态、关闭即销毁
        try:
            from PySide6.QtCore import Qt as _Qt
            self.setWindowModality(_Qt.WindowModality.NonModal)
            from PySide6.QtCore import Qt as _Qt2
            self.setAttribute(_Qt2.WidgetAttribute.WA_DeleteOnClose, True)
        except Exception:
            pass

        layout = QVBoxLayout(self)

        # 目录选择
        row = QHBoxLayout()
        row.addWidget(QLabel("根目录:"))
        self.edit_root = QLineEdit()
        btn_browse = QPushButton("选择...")
        row.addWidget(self.edit_root, 1)
        row.addWidget(btn_browse)
        layout.addLayout(row)

        # 输出目录（同级自定义）
        row_out = QHBoxLayout()
        row_out.addWidget(QLabel("输出目录:"))
        self.edit_out = QLineEdit()
        btn_browse_out = QPushButton("选择...")
        row_out.addWidget(self.edit_out, 1)
        row_out.addWidget(btn_browse_out)
        layout.addLayout(row_out)

        # 操作按钮
        row2 = QHBoxLayout()
        self.btn_start = QPushButton("开始转换")
        self.btn_start.setEnabled(True)
        # 同步主窗口按钮颜色样式
        try:
            self.btn_start.setObjectName("PrimaryButton")
        except Exception:
            pass
        # 计数显示：已完成/总计
        self.lbl_count = QLabel("已完成: 0 / 总计: 0")
        row2.addWidget(self.btn_start)
        row2.addStretch(1)
        row2.addWidget(self.lbl_count)
        layout.addLayout(row2)

        # 进度条（不显示数字）
        self.progress = QProgressBar()
        try:
            self.progress.setRange(0, 100)
            self.progress.setTextVisible(False)
            self.progress.setFixedHeight(6)
        except Exception:
            pass
        layout.addWidget(self.progress)

        # 日志输出（简化美化样式）
        self.txt_log = QTextEdit()
        self.txt_log.setReadOnly(True)
        try:
            self.txt_log.setStyleSheet(
                """
                QTextEdit {
                    font-size: 12px;
                    border: 1px solid #333;
                    background: #1e1e1e;
                    color: #cfcfcf;
                }
                """
            )
        except Exception:
            pass
        layout.addWidget(self.txt_log, 1)

        # 事件绑定
        btn_browse.clicked.connect(self.on_browse)
        btn_browse_out.clicked.connect(self.on_browse_out)
        self.btn_start.clicked.connect(self.on_start)

        self.worker = None

    def on_browse(self):
        d = QFileDialog.getExistingDirectory(self, "选择根目录", "")
        if d:
            self.edit_root.setText(d)
            # 自动建议同级输出目录
            try:
                root = pathlib.Path(d)
                suggested = root.parent / 'nii_outputs'
                self.edit_out.setText(str(suggested))
            except Exception:
                pass

    def on_browse_out(self):
        d = QFileDialog.getExistingDirectory(self, "选择输出目录", "")
        if d:
            self.edit_out.setText(d)

    def on_start(self):
        root = self.edit_root.text().strip()
        if not root:
            self.append_log("请先选择根目录")
            return
        out_dir = self.edit_out.text().strip()
        self.btn_start.setEnabled(False)
        self.append_log("启动后台转换任务...")
        self.worker = DicomConvertWorker(root, out_dir or None)
        self.worker.log.connect(self.append_log)
        self.worker.done.connect(self.on_done)
        self.worker.progress.connect(self.on_progress)
        # 运行中更新最终输出路径（用于回填主窗口）
        self.worker.result_dir.connect(self.on_result_dir_ready)
        self.worker.start()

    def on_done(self, ok: bool, msg: str):
        self.append_log(f"任务结束：{msg}")
        self.btn_start.setEnabled(True)
        # 完成后通知父窗口刷新输入清单
        try:
            self.converted.emit(self.edit_root.text().strip(), self._last_out_dir or (self.edit_out.text().strip() or ''))
        except Exception:
            pass
        self.worker = None

    def on_progress(self, current: int, total: int):
        # 优化进度显示：按目录数量进度
        try:
            pct = int((current / total) * 100) if total > 0 else 0
        except Exception:
            pct = 0
        try:
            self.progress.setValue(max(0, min(100, pct)))
        except Exception:
            pass
        # 更新计数标签
        try:
            self.lbl_count.setText(f"已完成: {current} / 总计: {total}")
        except Exception:
            pass

    def on_result_dir_ready(self, out_dir: str):
        # 记录最终输出目录，用于回填
        self._last_out_dir = out_dir

    def append_log(self, text: str):
        # 简化并美化日志：增加序号与时间，按类型着色
        try:
            from datetime import datetime
            ts = datetime.now().strftime('%H:%M:%S')
        except Exception:
            ts = ''
        try:
            self._log_idx = getattr(self, '_log_idx', 0) + 1
        except Exception:
            self._log_idx = 1
        idx = self._log_idx

        # 解析类型
        t = text.strip()
        kind = 'info'
        if t.startswith('[完成]'):
            kind = 'success'
            t = t.replace('[完成]', '').strip()
        elif t.startswith('[失败]'):
            kind = 'error'
            t = t.replace('[失败]', '').strip()
        elif t.startswith('错误:'):
            kind = 'error'
        elif t.startswith('[跳过]'):
            kind = 'skip'
            t = t.replace('[跳过]', '').strip()

        # 仅给序号与时间着色，其余文本使用默认颜色
        color = '#cfcfcf'
        if kind == 'success':
            color = '#2ecc71'  # 绿
        elif kind in ('error'):
            color = '#e74c3c'  # 红
        elif kind == 'skip':
            color = '#b0b0b0'  # 灰

        # 生成简化后的文本
        safe = t.replace('<', '&lt;').replace('>', '&gt;')
        line = f"<span style='color:{color}'>[{idx}] ({ts})</span> {safe}"
        try:
            self.txt_log.insertHtml(line + '<br/>')
            try:
                from PySide6.QtGui import QTextCursor
                self.txt_log.moveCursor(QTextCursor.MoveOperation.End)
            except Exception:
                pass
        except Exception:
            # 回退：纯文本
            self.txt_log.append(f"[{idx} {ts}] {t}")

    def closeEvent(self, event):
        # 子窗口关闭后中断处理
        try:
            if self.worker and self.worker.isRunning():
                self.worker.stop()
                self.worker.wait(1000)
        except Exception:
            pass
        super().closeEvent(event)
