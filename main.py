import sys
import re
import json
import math
import pytz
import requests
import pandas as pd
from urllib.parse import unquote
from datetime import datetime
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QPushButton, QLineEdit,
    QVBoxLayout, QHBoxLayout, QMessageBox, QScrollArea, QGroupBox, QFormLayout,
    QSpinBox, QFileDialog, QProgressDialog, QTextEdit
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal

API_URL_TEMPLATE = "https://api.1111job.app/logs/{}"
tz_taipei = pytz.timezone("Asia/Taipei")


class APIFetchThread(QThread):
    result_ready = pyqtSignal(list)
    error_occurred = pyqtSignal(str)

    def __init__(self, log_name):
        super().__init__()
        self.log_name = log_name

    def run(self):
        try:
            resp = requests.post(
                API_URL_TEMPLATE.format(self.log_name), timeout=30)
            resp.raise_for_status()
            lines = resp.text.splitlines()
            parsed = []
            for line in lines:
                m = re.match(
                    r'^\[(.*?)\].*?POST Request Details (\{.*?\}) \[\]$', line)
                if not m:
                    continue
                ts_raw, js = m.groups()
                try:
                    dt = datetime.fromisoformat(ts_raw).astimezone(tz_taipei)
                    ts_fmt = dt.strftime('%Y-%m-%d %H:%M:%S')
                except:
                    ts_fmt = ts_raw
                try:
                    data = json.loads(js)
                except:
                    continue
                parsed.append({
                    "timestamp": ts_fmt,
                    "post_params": data.get("post_params", {}),
                    "user_agent": unquote(data.get("user_agent", "")),
                    "ip_address": data.get("ip_address", "")
                })
            self.result_ready.emit(parsed)
        except Exception as e:
            self.error_occurred.emit(str(e))


class ExcelExportThread(QThread):
    finished = pyqtSignal(bool, str)

    def __init__(self, data, filename):
        super().__init__()
        self.data = data
        self.filename = filename

    def run(self):
        try:
            df = pd.DataFrame(self.data)
            df.to_excel(self.filename, index=False)
            self.finished.emit(True, self.filename)
        except Exception as e:
            self.finished.emit(False, str(e))


class LogViewerApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Masa API Log 檢視工具")
        self.page_size = 10
        self.current_page = 1
        self.sort_reverse = True
        self.parsed = []
        self.filtered = []
        self.filter_entries = []

        self._setup_ui()

    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        top_layout = QHBoxLayout()
        self.log_input = QLineEdit()
        self.log_input.setPlaceholderText("輸入 Log Name")
        query_btn = QPushButton("查詢")
        query_btn.clicked.connect(self.query_logs)
        self.sort_btn = QPushButton("排序：最新在前")
        self.sort_btn.clicked.connect(self.toggle_sort)
        export_btn = QPushButton("Excel 匯出")
        export_btn.clicked.connect(self.export_excel)

        top_layout.addWidget(QLabel("Log Name:"))
        top_layout.addWidget(self.log_input)
        top_layout.addWidget(query_btn)
        top_layout.addWidget(export_btn)
        top_layout.addWidget(self.sort_btn)
        layout.addLayout(top_layout)

        self.filter_container = QVBoxLayout()
        filter_btns = QHBoxLayout()
        self.add_filter_btn = QPushButton("新增篩選")
        self.add_filter_btn.clicked.connect(self.add_filter_entry)
        self.apply_filter_btn = QPushButton("套用")
        self.apply_filter_btn.clicked.connect(self.apply_filter)
        self.clear_filter_btn = QPushButton("清除")
        self.clear_filter_btn.clicked.connect(self.clear_filter)
        filter_btns.addWidget(self.add_filter_btn)
        filter_btns.addWidget(self.apply_filter_btn)
        filter_btns.addWidget(self.clear_filter_btn)
        self.filter_container.addLayout(filter_btns)
        layout.addLayout(self.filter_container)

        self.scroll_area = QScrollArea()
        self.scroll_content = QWidget()
        self.scroll_layout = QVBoxLayout()
        self.scroll_content.setLayout(self.scroll_layout)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setWidget(self.scroll_content)
        layout.addWidget(self.scroll_area)

        page_layout = QHBoxLayout()
        self.page_spin = QSpinBox()
        self.page_spin.setMinimum(1)
        self.page_spin.valueChanged.connect(self.go_to_page)
        self.page_label = QLabel("第 1 頁")
        page_layout.addWidget(QLabel("跳至："))
        page_layout.addWidget(self.page_spin)
        page_layout.addWidget(self.page_label)
        layout.addLayout(page_layout)

    def toggle_sort(self):
        self.sort_reverse = not self.sort_reverse
        label = "最新在前" if self.sort_reverse else "最舊在前"
        self.sort_btn.setText(f"排序：{label}")
        self.parsed.sort(
            key=lambda r: r["timestamp"], reverse=self.sort_reverse)
        if self.filtered:
            self.filtered.sort(
                key=lambda r: r["timestamp"], reverse=self.sort_reverse)
        self.refresh_data()

    def query_logs(self):
        name = self.log_input.text().strip()
        if not name:
            QMessageBox.warning(self, "提示", "請輸入 Log Name")
            return

        self.loading = QProgressDialog("正在查詢 API...", None, 0, 0, self)
        self.loading.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.loading.setCancelButton(None)
        self.loading.setMinimumDuration(0)
        self.loading.show()

        self.api_thread = APIFetchThread(name)
        self.api_thread.result_ready.connect(self.on_result_ready)
        self.api_thread.error_occurred.connect(self.on_fetch_error)
        self.api_thread.start()

    def on_result_ready(self, parsed):
        self.loading.close()
        self.parsed = sorted(
            parsed, key=lambda r: r["timestamp"], reverse=self.sort_reverse)
        self.filtered.clear()
        self.current_page = 1
        self.apply_filter()

    def on_fetch_error(self, error):
        self.loading.close()
        QMessageBox.critical(self, "API 錯誤", f"無法取得資料: {error}")

    def refresh_data(self):
        if self.filtered or any(k.text().strip() and v.text().strip() for _, k, v, _ in self.filter_entries):
            data = self.filtered
        else:
            data = self.parsed

        total_pages = max(1, math.ceil(len(data) / self.page_size))
        self.page_spin.setMaximum(total_pages)
        self.page_spin.setValue(self.current_page)
        self.page_label.setText(f"第 {self.current_page} / {total_pages} 頁")
        self.show_page(data)

    def show_page(self, data):
        for i in reversed(range(self.scroll_layout.count())):
            widget = self.scroll_layout.itemAt(i).widget()
            if widget:
                widget.setParent(None)

        start = (self.current_page - 1) * self.page_size
        end = start + self.page_size

        for idx, rec in enumerate(data[start:end], start=start + 1):
            box = QGroupBox(f"#{idx} - {rec['timestamp']}")
            layout = QVBoxLayout(box)
            form = QFormLayout()
            for k, v in rec["post_params"].items():
                val = str(v)
                matched = False
                fuzzy_matched = False
                for _, k_input, v_input, fuzzy_check in self.filter_entries:
                    fk = k_input.text().strip()
                    fv = v_input.text().strip()
                    fuzzy = fuzzy_check.isChecked()
                    if not fk or not fv:
                        continue
                    if k == fk:
                        if (not fuzzy and val == fv) or (fuzzy and fv in val):
                            matched = True
                            fuzzy_matched = fuzzy
                            break

                key_edit = QLineEdit(k)
                key_edit.setReadOnly(True)
                if matched:
                    key_edit.setStyleSheet("background-color: yellow")
                text = QTextEdit()
                text.setPlainText(val)
                text.setReadOnly(True)
                text.setMaximumHeight(60 if "\n" in val or "\t" in val else 28)
                if matched and fuzzy_matched:
                    start_idx = val.find(fv)
                    if start_idx != -1:
                        fmt_val = (val[:start_idx] + '<span style="color: goldenrod; font-weight: bold">' +
                                   val[start_idx:start_idx+len(fv)] + '</span>' + val[start_idx+len(fv):])
                        text.setHtml(fmt_val)
                elif matched:
                    text.setStyleSheet("color: goldenrod")
                form.addRow(key_edit, text)
            layout.addLayout(form)
            layout.addWidget(QLabel(f"User Agent: {rec['user_agent']}"))
            layout.addWidget(QLabel(f"IP: {rec['ip_address']}"))
            self.scroll_layout.addWidget(box)

    def go_to_page(self, page):
        self.current_page = page
        self.refresh_data()

    def add_filter_entry(self):
        row_layout = QHBoxLayout()
        key_input = QLineEdit()
        key_input.setPlaceholderText("輸入要篩選的 Key")
        val_input = QLineEdit()
        val_input.setPlaceholderText("輸入要篩選的 Value")
        fuzzy_checkbox = QPushButton("❌ 模糊")
        fuzzy_checkbox.setCheckable(True)
        fuzzy_checkbox.setToolTip("勾選表示啟用模糊比對")
        fuzzy_checkbox.setChecked(False)
        fuzzy_checkbox.toggled.connect(
            lambda checked, btn=fuzzy_checkbox: btn.setText(
                "✅ 模糊" if checked else "❌ 模糊")
        )
        remove_btn = QPushButton("移除")
        remove_btn.clicked.connect(
            lambda: self.remove_filter_entry(row_layout))
        row_layout.addWidget(key_input)
        row_layout.addWidget(val_input)
        row_layout.addWidget(fuzzy_checkbox)
        row_layout.addWidget(remove_btn)
        self.filter_container.addLayout(row_layout)
        self.filter_entries.append(
            (row_layout, key_input, val_input, fuzzy_checkbox))

    def remove_filter_entry(self, layout):
        for entry in self.filter_entries:
            if entry[0] == layout:
                while layout.count():
                    item = layout.takeAt(0)
                    widget = item.widget()
                    if widget:
                        widget.deleteLater()
                self.filter_entries.remove(entry)
                break

    def apply_filter(self):
        conditions = []
        for _, k_input, v_input, fuzzy_check in self.filter_entries:
            k = k_input.text().strip()
            v = v_input.text().strip()
            is_fuzzy = fuzzy_check.isChecked()
            if k and v:
                conditions.append((k, v, is_fuzzy))

        if not conditions:
            self.filtered = []
        else:
            self.filtered = [
                r for r in self.parsed
                if all(
                    v in str(r["post_params"].get(k, "")) if fuzzy else str(
                        r["post_params"].get(k, "")) == v
                    for k, v, fuzzy in conditions
                )
            ]
        self.current_page = 1
        self.refresh_data()

    def clear_filter(self):
        for layout, *_ in self.filter_entries:
            while layout.count():
                item = layout.takeAt(0)
                widget = item.widget()
                if widget:
                    widget.deleteLater()
        self.filter_entries.clear()
        self.filtered = []
        self.refresh_data()

    def export_excel(self):
        data = self.filtered if self.filtered else self.parsed
        if not data:
            QMessageBox.information(self, "提示", "沒有資料可導出")
            return

        rows = []
        for rec in data:
            row = {
                "timestamp": rec["timestamp"],
                "user_agent": rec["user_agent"],
                "ip_address": rec["ip_address"]
            }
            row.update(rec["post_params"])
            rows.append(row)

        filename, _ = QFileDialog.getSaveFileName(
            self, "儲存 Excel", "", "Excel 檔案 (*.xlsx)")
        if not filename:
            return

        self.loading = QProgressDialog("正在匯出 Excel...", None, 0, 0, self)
        self.loading.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.loading.setCancelButton(None)
        self.loading.setMinimumDuration(0)
        self.loading.show()

        self.export_thread = ExcelExportThread(rows, filename)
        self.export_thread.finished.connect(self.on_export_done)
        self.export_thread.start()

    def on_export_done(self, success, message):
        self.loading.close()
        if success:
            QMessageBox.information(self, "完成", f"成功導出至 {message}")
        else:
            QMessageBox.critical(self, "錯誤", f"導出失敗：{message}")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = LogViewerApp()
    window.resize(1000, 700)
    window.show()
    sys.exit(app.exec())
