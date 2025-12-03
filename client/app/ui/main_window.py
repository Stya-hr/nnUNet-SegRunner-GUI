import os
import sys
import zipfile

from PySide6.QtCore import QThread, Signal, QSize, Qt
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import (
    QMainWindow,
    QWidget,
    QFileDialog,
    QVBoxLayout,
    QHBoxLayout,
    QComboBox,
    QListWidget,
    QCheckBox,
    QPushButton,
    QLineEdit,
    QLabel,
    QProgressBar,
    QMessageBox,
    QFrame,
    QInputDialog,
    QDialog,
    QDialogButtonBox,
    QStyle,
    QFormLayout,
    QComboBox,
    QLineEdit,
)

from app.model.task import Task, TaskStatus
from app.model.tasktag import TaskTagSpec, PRESET_TASK_TAGS
from app.service.nnunet_service import NnUNetService
from app.meta import APP_NAME, APP_VERSION, format_about


class SegWorker(QThread):
    finished_success = Signal(str)  # output_path
    finished_failed = Signal(str)   # error_message
    progress_changed = Signal(int)  # percent 0-100
    case_done = Signal(str, str)    # case_id, output_path

    def __init__(self, task: Task, task_tag: str | TaskTagSpec = "101", per_case: bool = True, use_test_endpoints: bool = False):
        super().__init__()
        self.task = task
        self.task_tag = task_tag
        self.per_case = per_case
        self.service = NnUNetService(use_test_endpoints=use_test_endpoints)

    def run(self):
        def _cb(pct: int, _line: str):
            self.progress_changed.emit(int(pct))

        if self.per_case:
            def _case(cid: str, outp: str):
                self.case_done.emit(cid, outp)
            task = self.service.run_io_split_per_case(self.task, self.task_tag, on_progress=_cb, on_case_done=_case)
        else:
            task = self.service.run_io_split(self.task, self.task_tag, on_progress=_cb)
        if task.status == TaskStatus.SUCCESS:
            self.finished_success.emit(task.output_path or "")
        else:
            self.finished_failed.emit(task.error_message or "Unknown error")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(820, 520)
        # 配置文件路径（每用户持久化）
        self._config_path = os.path.normpath(os.path.join(os.path.expanduser("~"), ".ixcell_post_process_config.json"))

        # Menu: 测试模式开关（使用模拟器，不调用真实nnUNet）
        menubar = self.menuBar()
        menu_test = menubar.addMenu("测试")
        # 使用 QWidgetAction + QCheckBox，让样式与界面复选框一致
        from PySide6.QtWidgets import QWidgetAction
        self.chk_use_sim = QCheckBox("模拟器")
        self.chk_use_sim.setChecked(os.environ.get("USE_NNUNET_SIM", "0") == "1")
        self.chk_use_sim.toggled.connect(self.on_toggle_simulator)
        act_chk_sim = QWidgetAction(self)
        act_chk_sim.setDefaultWidget(self.chk_use_sim)
        menu_test.addAction(act_chk_sim)

        # 测试端点开关（服务端 /test/*），移至菜单栏
        self._use_test_endpoints = False
        self.act_use_test_endpoints = QAction("使用测试端点", self)
        self.act_use_test_endpoints.setCheckable(True)
        self.act_use_test_endpoints.setChecked(False)
        def _on_toggle_test_endpoints(checked: bool):
            self._use_test_endpoints = bool(checked)
            # 保存配置
            self._save_current_config_safe()
        self.act_use_test_endpoints.toggled.connect(_on_toggle_test_endpoints)
        menu_test.addAction(self.act_use_test_endpoints)

        # 工具菜单：DICOM 转 NIfTI 与 Conda 环境选择
        menu_tools = menubar.addMenu("工具")
        act_dicom2nii = QAction("DICOM->NIfTI", self)
        act_dicom2nii.triggered.connect(self.open_dicom_convert_window)
        menu_tools.addAction(act_dicom2nii)

        act_select_conda = QAction("选择 Conda 环境", self)
        act_select_conda.triggered.connect(self.select_conda_env)
        menu_tools.addAction(act_select_conda)

        # 帮助菜单：程序信息
        menu_help = menubar.addMenu("帮助")
        self.act_about = QAction("程序信息", self)
        self.act_about.triggered.connect(self.on_show_about)
        menu_help.addAction(self.act_about)
        try:
            menubar.setVisible(True)
        except Exception:
            pass

        self.input_edit = QLineEdit()
        self.input_edit.setPlaceholderText("选择输入图像所在文件夹（-i）")
        self.btn_browse_input = QPushButton("\u2002\u2002选择输入目录")
        self.btn_browse_input.setObjectName("BrowseInputBtn")
        # 图标左侧（默认），保持统一icon尺寸
        self.btn_browse_input.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.btn_browse_input.setIconSize(QSize(18, 18))

        self.output_edit = QLineEdit()
        self.output_edit.setPlaceholderText("选择输出目录（-o，默认与输入同级seg）")
        self.btn_browse_output = QPushButton("\u2002\u2002选择输出目录")
        self.btn_browse_output.setObjectName("BrowseOutputBtn")
        self.btn_browse_output.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.btn_browse_output.setIconSize(QSize(18, 18))

        # 任务标签预设与详细配置
        self.tag_preset_combo = QComboBox()
        self.tag_preset_combo.addItem("自定义")
        for name in PRESET_TASK_TAGS.keys():
            self.tag_preset_combo.addItem(name)
        # 默认选择 IO Split (101)
        _default_preset = "IO Split (101)"
        _idx = self.tag_preset_combo.findText(_default_preset)
        if _idx != -1:
            self.tag_preset_combo.setCurrentIndex(_idx)

        self.tag_id_edit = QLineEdit()
        self.tag_id_edit.setPlaceholderText("-d:默认101")
        self.tag_id_edit.setText("101")

        self.tag_config_edit = QLineEdit()
        self.tag_config_edit.setPlaceholderText("-c:默认3d_fullres")
        self.tag_config_edit.setText("3d_fullres")

        self.tag_folds_edit = QLineEdit()
        self.tag_folds_edit.setPlaceholderText("-f:默认0")
        self.tag_folds_edit.setText("0")

        # 强制逐例处理模式：不再提供选项开关

        # 远程服务设置
        self.chk_use_remote = QCheckBox("远程服务")
        # 仅填写 IP 和 端口，自动拼接为 http://IP:PORT
        self.remote_ip_edit = QLineEdit()
        self.remote_ip_edit.setPlaceholderText("远程IP，例如 192.168.1.10")
        self.remote_port_edit = QLineEdit()
        self.remote_port_edit.setPlaceholderText("端口，例如 8000")
        try:
            from PySide6.QtGui import QIntValidator
            self.remote_port_edit.setValidator(QIntValidator(1, 65535, self))
        except Exception:
            pass
        self.btn_test_remote = QPushButton("连接")
        self.btn_test_remote.setObjectName("PrimaryButton")
        # 连接状态灯（灰=未知，绿=正常，红=异常）
        self.remote_status_led = QLabel()
        try:
            self.remote_status_led.setFixedSize(12, 12)
        except Exception:
            pass
        self._set_remote_status_led("unknown", "未测试")
        # 初始值从环境变量读取
        _remote = os.environ.get("NNUNET_REMOTE_API", "").strip()
        if _remote.startswith("http://") or _remote.startswith("https://"):
            try:
                # 简单解析 http://host:port
                base = _remote.split("//",1)[1]
                host, port = base.split(":",1)
                self.remote_ip_edit.setText(host)
                self.remote_port_edit.setText(port)
                self.chk_use_remote.setChecked(True)
            except Exception:
                pass
        self.chk_use_remote.toggled.connect(self.on_toggle_remote)
        self.remote_ip_edit.editingFinished.connect(self.on_remote_api_changed)
        self.remote_port_edit.editingFinished.connect(self.on_remote_api_changed)
        self.btn_test_remote.clicked.connect(self.on_connect_remote)

        self.btn_run = QPushButton("\u2002\u2002开始分割")
        # 图标左侧，保持统一icon尺寸
        self.btn_run.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.btn_run.setIconSize(QSize(18, 18))
        self.btn_export_zip = QPushButton("\u2002\u2002导出结果ZIP")
        self.btn_export_zip.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.btn_export_zip.setIconSize(QSize(18, 18))
        self.btn_export_zip.setEnabled(False)

        self.status_label = QLabel("就绪")
        # 状态文本不应撑大窗口：开启换行、限制最大宽度，并对超长文本进行省略
        try:
            self.status_label.setWordWrap(True)
            self.status_label.setMaximumHeight(40)
        except Exception:
            pass
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)  # 初始为不定进度，收到百分比后切换
        self.progress.setVisible(False)
        self.progress.setTextVisible(False)  # 使用细进度条，文字由状态栏显示
        try:
            self.progress.setFixedHeight(6)
        except Exception:
            pass

        # Layouts
        top = QHBoxLayout()
        top.addWidget(self.input_edit)
        top.addWidget(self.btn_browse_input)

        top2 = QHBoxLayout()
        top2.addWidget(self.output_edit)
        top2.addWidget(self.btn_browse_output)

        preset_layout = QHBoxLayout()
        preset_layout.addWidget(QLabel("任务预设:"))
        preset_layout.addWidget(self.tag_preset_combo)
        preset_layout.addStretch(1)

        param_layout = QHBoxLayout()
        param_layout.addWidget(QLabel("Dataset:"))
        param_layout.addWidget(self.tag_id_edit)
        param_layout.addWidget(QLabel("Config:"))
        param_layout.addWidget(self.tag_config_edit)
        param_layout.addWidget(QLabel("Fold:"))
        param_layout.addWidget(self.tag_folds_edit)
        param_layout.addStretch(1)

        # 输入/已处理清单
        self.input_list = QListWidget()
        self.processed_list = QListWidget()
        left = QVBoxLayout(); right = QVBoxLayout()
        lbl_in = QLabel("输入清单（病例）")
        self.input_count_label = QLabel("总病例: 0，总图像: 0")
        lbl_out = QLabel("已处理清单")
        in_header = QHBoxLayout()
        in_header.addWidget(lbl_in)
        in_header.addStretch(1)
        in_header.addWidget(self.input_count_label)

        # 左侧：输入相关控件与开始按钮
        left_controls = QVBoxLayout()
        left_controls.addLayout(top)
        left_controls.addLayout(top2)
        left_controls.addLayout(preset_layout)
        left_controls.addLayout(param_layout)
        # 远程服务行
        remote_row = QHBoxLayout()
        remote_row.addWidget(self.chk_use_remote)
        remote_row.addWidget(self.remote_ip_edit)
        remote_row.addWidget(QLabel(":"))
        remote_row.addWidget(self.remote_port_edit)
        remote_row.addWidget(self.btn_test_remote)
        left_controls.addLayout(remote_row)
        left_controls.addWidget(self.btn_run)

        # 右侧：结果清单与导出按钮
        right_header = QHBoxLayout()
        right_header.addWidget(lbl_out)
        right_header.addStretch(1)
        right_actions = QHBoxLayout()
        right_actions.addWidget(self.btn_export_zip)
        right_actions.addStretch(1)
        self.output_count_label = QLabel("已生成: 0")
        self.output_count_label.setObjectName("OutputCountLabel")
        right_actions.addWidget(self.output_count_label)
        right.addLayout(right_header)
        right.addWidget(self.processed_list)
        right.addLayout(right_actions)

        # 中间分割：左（导入区）| 竖线 | 右（结果区）
        center = QHBoxLayout()
        left_panel = QVBoxLayout()
        left_panel.addLayout(left_controls)
        left_panel.addLayout(in_header)
        left_panel.addWidget(self.input_list)

        vline = QFrame()
        vline.setFrameShape(QFrame.Shape.VLine)
        vline.setFrameShadow(QFrame.Shadow.Sunken)

        center.addLayout(left_panel, 1)
        center.addWidget(vline)
        center.addLayout(right, 1)

        root = QVBoxLayout()
        root.addLayout(center)
        # 底部状态行：左侧状态文字，右侧状态灯
        status_row = QHBoxLayout()
        status_row.addWidget(self.status_label)
        status_row.addStretch(1)
        status_row.addWidget(self.remote_status_led)
        root.addLayout(status_row)
        root.addWidget(self.progress)

        container = QWidget()
        container.setLayout(root)
        self.setCentralWidget(container)

        # 取消模式状态栏标签（应用户要求移除）

        # Signals
        self.btn_browse_input.clicked.connect(self.on_browse_input)
        self.btn_run.clicked.connect(self.on_run)
        self.btn_browse_output.clicked.connect(self.on_browse_output)
        self.btn_export_zip.clicked.connect(self.on_export_zip)
        self.tag_preset_combo.currentIndexChanged.connect(self.on_tag_preset_changed)

        self.worker: SegWorker | None = None
        self.output_path: str | None = None
        # 初始化预设状态（默认“自定义”可编辑）
        self.on_tag_preset_changed(self.tag_preset_combo.currentIndex())
        # Apply style and icons at the end
        self._apply_style_and_icons()
        # 初始化输出计数
        self._update_output_count_label()
        # 删除底部日志区域后的初始化无需路径提示
        # 加载上次配置（若存在）
        try:
            self._load_last_config()
        except Exception:
            pass

    # ------- DICOM 转换子窗口 -------
    def open_dicom_convert_window(self):
        try:
            from .dicom_convert_window import DicomConvertWindow
        except Exception:
            # 兼容不同包结构
            from app.ui.dicom_convert_window import DicomConvertWindow
        win = DicomConvertWindow(self)
        # 转换完成后自动刷新输入清单
        try:
            # 回填主窗口的输入/输出路径，并刷新清单
            def _on_converted(root_dir: str, out_dir: str):
                if root_dir:
                    self.input_edit.setText(os.path.normpath(root_dir))
                    # 默认输出设为同级 seg，但若提供专用输出目录则使用其路径
                    if out_dir:
                        self.output_edit.setText(os.path.normpath(out_dir))
                    else:
                        default_out = os.path.join(os.path.dirname(root_dir), "seg")
                        self.output_edit.setText(os.path.normpath(default_out))
                    self._populate_input_list(root_dir)
            win.converted.connect(_on_converted)
        except Exception:
            pass
        win.show()
        if not hasattr(self, "_child_windows"):
            self._child_windows = []
        self._child_windows.append(win)

    def on_toggle_simulator(self, checked: bool):
        os.environ["USE_NNUNET_SIM"] = "1" if checked else "0"

    def on_toggle_remote(self, checked: bool):
        if checked:
            ip = self.remote_ip_edit.text().strip()
            port = self.remote_port_edit.text().strip()
            if self._validate_remote(ip, port):
                os.environ["NNUNET_REMOTE_API"] = f"http://{ip}:{port}"
            else:
                self._show_warning("提示", "远程地址不合法，请检查 IP 与端口")
                self.chk_use_remote.setChecked(False)
            # 切换后状态灯置为未知，待测试
            self._set_remote_status_led("unknown", "未测试")
            # 保存配置
            self._save_current_config_safe()
        else:
            # 关闭远程模式
            os.environ.pop("NNUNET_REMOTE_API", None)
            self._set_remote_status_led("unknown", "未测试")
            # 保存配置
            self._save_current_config_safe()

    def on_remote_api_changed(self):
        ip = self.remote_ip_edit.text().strip()
        port = self.remote_port_edit.text().strip()
        if self.chk_use_remote.isChecked() and self._validate_remote(ip, port):
            os.environ["NNUNET_REMOTE_API"] = f"http://{ip}:{port}"
        else:
            # 任一为空则关闭远程模式
            self.chk_use_remote.setChecked(False)
            os.environ.pop("NNUNET_REMOTE_API", None)
        # 用户编辑后，状态灯回到未知
        self._set_remote_status_led("unknown", "未测试")
        self._save_current_config_safe()

    def on_connect_remote(self):
        ip = self.remote_ip_edit.text().strip()
        port = self.remote_port_edit.text().strip()
        if not self._validate_remote(ip, port):
            self._show_warning("提示", "远程地址不合法，请检查 IP 与端口")
            self._set_remote_status_led("fail", "地址不合法")
            return
        os.environ["NNUNET_REMOTE_API"] = f"http://{ip}:{port}"
        self.chk_use_remote.setChecked(True)
        # 无需进行网络探测，直接标记为启用
        self._set_remote_status_led("ok", "远程模式已启用（未验证连通性）")

    def _set_remote_status_led(self, state: str, tip: str = "") -> None:
        # state: unknown|ok|fail
        color = "#9e9e9e"  # grey
        if state == "ok":
            color = "#2ecc71"  # green
        elif state == "fail":
            color = "#e74c3c"  # red
        try:
            r = int(self.remote_status_led.height() / 2) if self.remote_status_led.height() > 0 else 6
        except Exception:
            r = 6
        style = f"background-color:{color}; border:1px solid #666; border-radius:{r}px;"
        try:
            self.remote_status_led.setStyleSheet(style)
            if tip:
                self.remote_status_led.setToolTip(tip)
        except Exception:
            pass

    def _validate_remote(self, ip: str, port: str) -> bool:
        # 允许 IPv4 或主机名（字母数字连字符点）；端口 1-65535
        if not ip or not port:
            return False
        try:
            p = int(port)
            if p < 1 or p > 65535:
                return False
        except Exception:
            return False
        import re
        ipv4 = re.compile(r"^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$")
        host = re.compile(r"^[A-Za-z0-9][A-Za-z0-9\-\.]{0,253}$")
        if ipv4.match(ip):
            parts = [int(x) for x in ip.split('.')]
            if any(x < 0 or x > 255 for x in parts):
                return False
            return True
        return bool(host.match(ip))

    def on_browse_input(self):
        directory = QFileDialog.getExistingDirectory(self, "选择输入目录")
        if directory:
            self.input_edit.setText(os.path.normpath(directory))
            # 自动设置默认输出目录到与输入同级的seg
            default_out = os.path.join(os.path.dirname(directory), "seg")
            self.output_edit.setText(os.path.normpath(default_out))
            # 列出输入病例清单
            self._populate_input_list(directory)
            self._save_current_config_safe()

    def on_browse_output(self):
        directory = QFileDialog.getExistingDirectory(self, "选择输出目录")
        if directory:
            self.output_edit.setText(os.path.normpath(directory))
            self._save_current_config_safe()

    def on_tag_preset_changed(self, idx: int):
        name = self.tag_preset_combo.currentText()
        if name == "自定义":
            # 允许自定义编辑
            self.tag_id_edit.setReadOnly(False)
            self.tag_config_edit.setReadOnly(False)
            self.tag_folds_edit.setReadOnly(False)
            self._save_current_config_safe()
            return
        # 选择了预设：填充值并禁止修改
        spec = PRESET_TASK_TAGS.get(name)
        if spec:
            self.tag_id_edit.setText(spec.id)
            self.tag_config_edit.setText(spec.config)
            self.tag_folds_edit.setText(spec.folds)
            self.tag_id_edit.setReadOnly(True)
            self.tag_config_edit.setReadOnly(True)
            self.tag_folds_edit.setReadOnly(True)
            self._save_current_config_safe()

    def on_run(self):
        images_path = self.input_edit.text().strip()
        if not images_path or not os.path.isdir(images_path):
            self._show_warning("提示", "请输入有效的图像文件夹路径")
            return
        self._save_current_config_safe()

        self._set_status_text("运行中…")
        self.progress.setVisible(True)
        self.btn_run.setEnabled(False)
        self.btn_export_zip.setEnabled(False)
        # 新一轮运行前，清空“已处理清单”
        self.processed_list.clear()
        self._update_output_count_label()

        desired_output_dir = os.path.normpath(self.output_edit.text().strip()) if self.output_edit.text().strip() else ""
        task = Task(images_path=images_path, desired_output_dir=desired_output_dir or None)

        # 构造任务标签规格：优先预设，否则使用自定义
        name = self.tag_preset_combo.currentText()
        if name != "自定义" and name in PRESET_TASK_TAGS:
            tag_spec = PRESET_TASK_TAGS[name]
        else:
            tag_spec = TaskTagSpec(
                id=(self.tag_id_edit.text().strip() or "101"),
                config=(self.tag_config_edit.text().strip() or "3d_fullres"),
                folds=(self.tag_folds_edit.text().strip() or "0"),
            )

        # 远程模式下强制整批运行（逐例处理在远端不划分临时目录）
        # 强制逐例处理
        per_case = True
        # 将测试端点选择传递到服务
        use_test = self._use_test_endpoints
        self.worker = SegWorker(task, task_tag=tag_spec, per_case=per_case, use_test_endpoints=use_test)
        self.worker.finished_success.connect(self.on_success)
        self.worker.finished_failed.connect(self.on_failed)
        self.worker.progress_changed.connect(self.on_progress)
        self.worker.case_done.connect(self.on_case_done)
        self.worker.start()

    def on_success(self, output_path: str):
        normalized = os.path.normpath(output_path) if output_path else ""
        self.output_path = normalized
        # 显示简要完成信息，避免超长路径导致窗口扩展
        self._set_status_text(f"完成")
        self.progress.setVisible(False)
        self.btn_run.setEnabled(True)
        self.btn_export_zip.setEnabled(bool(output_path))
        # 若非逐例模式或未逐例回调，补充扫描输出目录填充处理清单
        if self.processed_list.count() == 0 and output_path and os.path.isdir(output_path):
            outs = []
            for name in sorted(os.listdir(output_path)):
                lower = name.lower()
                if lower.endswith('.nii') or lower.endswith('.nii.gz'):
                    outs.append(name)
            for name in outs:
                self.processed_list.addItem(name)
        self._update_output_count_label()

    def on_failed(self, msg: str):
        # 失败信息可能很长，进行省略显示
        self._set_status_text("失败")
        self.progress.setVisible(False)
        self.btn_run.setEnabled(True)
        self.btn_export_zip.setEnabled(False)
        try:
            self._show_message("error", "错误", msg)
        except Exception:
            pass

    def on_progress(self, pct: int):
        # 第一次收到进度，切换到确定型
        if self.progress.maximum() == 0:
            self.progress.setRange(0, 100)
        pct = max(0, min(100, int(pct)))
        self.progress.setValue(pct)
        self._set_status_text(f"运行中… {pct}%")

    def on_case_done(self, case_id: str, out_path: str):
        text = case_id
        if out_path:
            text += f" -> {os.path.basename(out_path)}"
        self.processed_list.addItem(text)
        self.processed_list.scrollToBottom()
        self._update_output_count_label()

    def _populate_input_list(self, images_dir: str):
        # 使用服务收集病例清单
        try:
            service = NnUNetService()
            cases = service.collect_cases(images_dir)
        except Exception:
            cases = []
        self.input_list.clear()
        total_images = 0
        for cid, flist in cases:
            count = len(flist)
            total_images += count
            item_text = f"{cid} ({count})"
            self.input_list.addItem(item_text)
        self.input_count_label.setText(f"总病例: {len(cases)}, 总图像: {total_images}")
        # 清空已处理清单
        self.processed_list.clear()
        self._update_output_count_label()

    def on_export_zip(self):
        if not self.output_path or not os.path.isdir(self.output_path):
            self._show_warning("提示", "没有可导出的结果")
            return
        save_path, _ = QFileDialog.getSaveFileName(
            self, "导出结果ZIP", "result.zip", "Zip (*.zip)"
        )
        if not save_path:
            return
        try:
            with zipfile.ZipFile(save_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for root, _, files in os.walk(self.output_path):
                    for f in files:
                        fp = os.path.join(root, f)
                        arcname = os.path.relpath(fp, self.output_path)
                        zf.write(fp, arcname)
            self._show_message("info", "成功", f"已导出：{save_path}")
        except Exception as e:
            self._show_message("warning", "失败", f"导出失败：{e}")

    def on_show_about(self):
        try:
            import PySide6
            from PySide6.QtCore import qVersion
            import shutil as _shutil
            py_ver = sys.version.split(" ")[0]
            qt_ver = qVersion()
            pyside_ver = getattr(PySide6, "__version__", "?")
            sim = os.environ.get("USE_NNUNET_SIM", "0") == "1"
            nnunet_ok = _shutil.which("nnUNetv2_predict") is not None
            remote_api = os.environ.get("NNUNET_REMOTE_API", "").strip()
            if remote_api:
                mode = f"运行模式: 远程推理 ({remote_api})"
            else:
                mode = "运行模式: 本地推理" + ("（模拟器已启用）" if sim else "")
            nnunet = f"nnUNetv2_predict: {'可用' if nnunet_ok else '未发现'}"
            runtime = [
                f"Python: {py_ver}",
                f"Qt: {qt_ver}",
                f"PySide6: {pyside_ver}",
                "",
                mode,
                nnunet,
            ]
            text = format_about(runtime)
            self._show_message("info", f"关于 {APP_NAME}", text)
        except Exception:
            self._show_message("info", f"关于 {APP_NAME}", f"{APP_NAME}\n版本: {APP_VERSION}")

    def _assets_path(self, *names: str) -> str:
        base = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "assets", "icons"))
        return os.path.normpath(os.path.join(base, *names))

    def _set_status_text(self, text: str):
        """设置状态文本时进行长度控制，避免窗口因文本过长而扩展。"""
        try:
            # 简单省略策略：超过 100 字符进行截断
            t = text or ""
            if len(t) > 100:
                t = t[:97] + "..."
            self.status_label.setText(t)
        except Exception:
            # 回退
            try:
                self.status_label.setText(text)
            except Exception:
                pass

    def _apply_style_and_icons(self) -> None:
        # Load QSS
        qss_path = os.path.join(os.path.dirname(__file__), "style.qss")
        if os.path.isfile(qss_path):
            try:
                with open(qss_path, "r", encoding="utf-8") as f:
                    self.setStyleSheet(f.read())
            except Exception:
                pass
        # Set icons
        try:
            self.btn_browse_input.setIcon(QIcon(self._assets_path("input-svgrepo-com.svg")))
            self.btn_browse_output.setIcon(QIcon(self._assets_path("output-svgrepo-com.svg")))
            self.btn_run.setIcon(QIcon(self._assets_path("play-svgrepo-com.svg")))
            self.btn_export_zip.setIcon(QIcon(self._assets_path("download-2-svgrepo-com.svg")))
            # 在菜单中显示勾选状态更直观，隐藏图标以保留checkmark
            # 使用 QCheckBox 作为菜单项，无需图标可见性控制
        except Exception:
            pass
        # Mark primary action for QSS styling
        self.btn_run.setObjectName("PrimaryButton")
        # 统一按钮颜色样式为与开始分割相同
        try:
            self.btn_browse_input.setObjectName("PrimaryButton")
            self.btn_browse_output.setObjectName("PrimaryButton")
            self.btn_export_zip.setObjectName("PrimaryButton")
        except Exception:
            pass

    # 删除底部日志相关方法

    def select_conda_env(self):
        """选择 Conda 环境：支持下拉选择或手动输入，列表文本可复制。"""
        try:
            from app.tools.conda_env import list_conda_envs, resolve_nnunet_exe
        except Exception:
            self._show_warning("错误", "缺少工具模块 app.tools.conda_env")
            return

        envs = list_conda_envs()

        dlg = QDialog(self)
        dlg.setWindowTitle("选择 Conda 环境")
        try:
            icon = self.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)
            dlg.setWindowIcon(icon)
        except Exception:
            pass

        form = QFormLayout(dlg)
        form.setContentsMargins(12, 10, 12, 10)
        form.setSpacing(8)

        # 可复制的环境列表
        list_text = []
        for idx, (name, prefix) in enumerate(envs):
            label = name if name else "(未命名)"
            list_text.append(f"[{idx}] {label} -> {prefix}")
        from PySide6.QtWidgets import QTextEdit as _QTextEdit
        txt = _QTextEdit()
        txt.setReadOnly(True)
        txt.setText("检测到以下 Conda 环境:\n" + ("\n".join(list_text) if list_text else "(未检测到环境，仍可手动输入前缀路径)"))
        txt.setMinimumHeight(140)
        form.addRow("环境列表:", txt)

        # 下拉选择 + 手动输入
        combo = QComboBox()
        combo.addItem("(不选择，改用下方输入)", "")
        for idx, (name, prefix) in enumerate(envs):
            label = name if name else "(未命名)"
            combo.addItem(f"[{idx}] {label}", prefix)
        form.addRow("选择环境:", combo)

        edit = QLineEdit()
        edit.setPlaceholderText("或手动输入前缀路径，如 D:\\Env\\miniconda\\envs\\your_env")
        # 默认值使用当前环境变量
        default_prefix = os.environ.get("NNUNET_CONDA_PREFIX") or os.environ.get("CONDA_PREFIX") or ""
        edit.setText(default_prefix)
        form.addRow("输入前缀:", edit)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        form.addRow(buttons)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        chosen = combo.currentData() or edit.text().strip()
        if not chosen:
            self._show_warning("提示", "未选择或输入前缀路径")
            return

        exe = resolve_nnunet_exe(chosen)
        if not exe:
            self._show_warning("未找到", f"在前缀下未找到 nnUNetv2_predict: {chosen}")
            return

        os.environ["NNUNET_CONDA_PREFIX"] = chosen
        os.environ["NNUNET_EXE"] = exe
        try:
            self._show_message("info", "已选择 Conda 环境", f"前缀: {chosen}\n可执行: {exe}")
        except Exception:
            pass
        self._save_current_config_safe()

    # ------------------ 配置持久化 ------------------
    def _load_last_config(self) -> None:
        import json
        if not os.path.isfile(self._config_path):
            return
        with open(self._config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        # 路径
        self.input_edit.setText(cfg.get("input_dir", ""))
        self.output_edit.setText(cfg.get("output_dir", ""))
        # 预设/参数
        preset = cfg.get("preset", None)
        if preset:
            idx = self.tag_preset_combo.findText(preset)
            if idx != -1:
                self.tag_preset_combo.setCurrentIndex(idx)
        self.tag_id_edit.setText(cfg.get("tag_id", self.tag_id_edit.text()))
        self.tag_config_edit.setText(cfg.get("tag_config", self.tag_config_edit.text()))
        self.tag_folds_edit.setText(cfg.get("tag_folds", self.tag_folds_edit.text()))
        # 远程
        remote_enabled = bool(cfg.get("remote_enabled", False))
        self.chk_use_remote.setChecked(remote_enabled)
        self.remote_ip_edit.setText(cfg.get("remote_ip", ""))
        self.remote_port_edit.setText(str(cfg.get("remote_port", "")))
        if remote_enabled and self.remote_ip_edit.text() and self.remote_port_edit.text():
            os.environ["NNUNET_REMOTE_API"] = f"http://{self.remote_ip_edit.text()}:{self.remote_port_edit.text()}"
        # 测试端点
        self._use_test_endpoints = bool(cfg.get("use_test_endpoints", False))
        try:
            self.act_use_test_endpoints.setChecked(self._use_test_endpoints)
        except Exception:
            pass
        # Conda/nnUNet
        prefix = cfg.get("conda_prefix", None)
        exe = cfg.get("nnunet_exe", None)
        if prefix:
            os.environ["NNUNET_CONDA_PREFIX"] = prefix
        if exe:
            os.environ["NNUNET_EXE"] = exe

    def _save_current_config_safe(self) -> None:
        try:
            self._save_current_config()
        except Exception:
            pass

    def _save_current_config(self) -> None:
        import json
        cfg = {
            "input_dir": self.input_edit.text().strip(),
            "output_dir": self.output_edit.text().strip(),
            "preset": self.tag_preset_combo.currentText(),
            "tag_id": self.tag_id_edit.text().strip(),
            "tag_config": self.tag_config_edit.text().strip(),
            "tag_folds": self.tag_folds_edit.text().strip(),
            "remote_enabled": self.chk_use_remote.isChecked(),
            "remote_ip": self.remote_ip_edit.text().strip(),
            "remote_port": self.remote_port_edit.text().strip(),
            "conda_prefix": os.environ.get("NNUNET_CONDA_PREFIX"),
            "nnunet_exe": os.environ.get("NNUNET_EXE"),
            "use_test_endpoints": bool(getattr(self, "_use_test_endpoints", False)),
        }
        # 写入到用户家目录
        with open(self._config_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)

    # 在窗口关闭时保存配置
    def closeEvent(self, event):
        self._save_current_config_safe()
        try:
            super().closeEvent(event)
        except Exception:
            pass

    # 模式状态标签功能已移除
    def _update_output_count_label(self):
        try:
            count = self.processed_list.count() if hasattr(self, 'processed_list') else 0
        except Exception:
            count = 0
        if hasattr(self, 'output_count_label') and self.output_count_label is not None:
            self.output_count_label.setText(f"已生成: {count}")

    def _show_warning(self, title: str, text: str):
        # 自定义警告对话框：标题栏图标为警告，内容左侧图标 + 右侧文字，底部 OK 居中
        from PySide6.QtWidgets import QDialog, QDialogButtonBox
        from PySide6.QtGui import QPixmap
        from PySide6.QtWidgets import QStyle

        dlg = QDialog(self)
        dlg.setWindowTitle(title)
        # 使用标准警告图标作为标题栏图标
        try:
            warn_icon = self.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxWarning)
            dlg.setWindowIcon(warn_icon)
        except Exception:
            pass

        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(8)

        row = QHBoxLayout()
        row.setSpacing(10)
        # 左侧警告图标
        try:
            warn_pix = self.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxWarning).pixmap(32, 32)
            icon_lbl = QLabel()
            icon_lbl.setPixmap(warn_pix)
            row.addWidget(icon_lbl, 0, Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        except Exception:
            row.addSpacing(0)
        # 右侧文字，顶部对齐
        text_lbl = QLabel(text)
        text_lbl.setWordWrap(True)
        row.addWidget(text_lbl, 1)
        lay.addLayout(row)

        # 底部按钮居中
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        try:
            btns.setCenterButtons(True)
        except Exception:
            pass
        # 兼容方案：添加左右拉伸以居中
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        btn_row.addWidget(btns)
        btn_row.addStretch(1)
        lay.addLayout(btn_row)
        btns.accepted.connect(dlg.accept)

        dlg.setModal(True)
        dlg.exec()

    def _show_message(self, kind: str, title: str, text: str):
        # 统一样式的消息框：左侧图标 + 右侧文字，OK 居中；标题栏图标与类型一致
        from PySide6.QtWidgets import QDialog, QDialogButtonBox, QStyle

        kind = (kind or "info").lower()
        icon_map = {
            "info": QStyle.StandardPixmap.SP_MessageBoxInformation,
            "information": QStyle.StandardPixmap.SP_MessageBoxInformation,
            "success": QStyle.StandardPixmap.SP_MessageBoxInformation,
            "warning": QStyle.StandardPixmap.SP_MessageBoxWarning,
            "error": QStyle.StandardPixmap.SP_MessageBoxCritical,
            "critical": QStyle.StandardPixmap.SP_MessageBoxCritical,
            "question": QStyle.StandardPixmap.SP_MessageBoxQuestion,
        }
        pm = icon_map.get(kind, QStyle.StandardPixmap.SP_MessageBoxInformation)

        dlg = QDialog(self)
        dlg.setWindowTitle(title)
        try:
            win_icon = self.style().standardIcon(pm)
            dlg.setWindowIcon(win_icon)
        except Exception:
            pass

        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(8)

        row = QHBoxLayout()
        row.setSpacing(10)
        # 左侧类型图标
        try:
            pix = self.style().standardIcon(pm).pixmap(32, 32)
            icon_lbl = QLabel()
            icon_lbl.setPixmap(pix)
            row.addWidget(icon_lbl, 0, Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        except Exception:
            row.addSpacing(0)

        text_lbl = QLabel(text)
        text_lbl.setWordWrap(True)
        row.addWidget(text_lbl, 1)
        lay.addLayout(row)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        try:
            btns.setCenterButtons(True)
        except Exception:
            pass

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        btn_row.addWidget(btns)
        btn_row.addStretch(1)
        lay.addLayout(btn_row)
        btns.accepted.connect(dlg.accept)

        dlg.setModal(True)
        dlg.exec()
