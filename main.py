from enum import Enum
from dataclasses import dataclass
from typing import List
import sys
import json
import requests
import re
import pytz
import math
import pandas as pd
from datetime import datetime
from urllib.parse import unquote
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QPushButton, QLineEdit,
    QVBoxLayout, QHBoxLayout, QMessageBox, QScrollArea, QGroupBox, QFormLayout,
    QSpinBox, QFileDialog, QProgressDialog, QTextEdit, QComboBox, QTableWidget,
    QStatusBar, QCheckBox
)
from PyQt6.QtGui import QIcon
from PyQt6.QtCore import Qt, QThread, pyqtSignal


class SortOrder(Enum):
    NEWEST_FIRST = (0, "排序：最新在前")
    OLDEST_FIRST = (1, "排序：最舊在前")

    def __init__(self, index, label):
        self.index = index
        self.label = label


@dataclass
class MasaLogEntry:
    timestamp: str
    post_params: dict
    user_agent: str
    ip_address: str


@dataclass
class FilterEntry:
    id: int  # 用於唯一識別篩選條件
    key: str
    value: str
    include: bool  # True for 包含, False for 排除
    blur: bool  # True for 模糊搜尋, False for 精確搜尋


class MasaLogAPIThread(QThread):
    data_fetched = pyqtSignal(list)
    error_occurred = pyqtSignal(str)

    def __init__(self, log_name, is_test_env: bool):
        super().__init__()
        self.log_name = log_name
        self.api_url = "https://uat-api.1111job.app/logs/{}" if is_test_env else "https://api.1111job.app/logs/{}"
        self.tz_taipei = pytz.timezone("Asia/Taipei")

    def run(self):
        try:
            # 發送 API 請求
            response = requests.post(
                url=self.api_url.format(self.log_name),
                timeout=30
            )
            # 檢查回應狀態
            response.raise_for_status()

            # 逐行解析 response 內容
            lines = response.text.splitlines()
            # 解析資料後的陣列
            parsed_list: List[MasaLogEntry] = []

            for line in lines:
                # 基於正則表達式解析每一行
                m = re.match(
                    r'^\[(.*?)\].*?POST Request Details (\{.*?\}) \[\]$', line)
                if not m:
                    continue

                # 取得時間戳和 JSON 字串
                timestamp, json_str = m.groups()

                # 嘗試解析 時間戳
                try:
                    ds = datetime.fromisoformat(
                        timestamp).astimezone(self.tz_taipei)
                    timestamp_formatted = ds.strftime("%Y-%m-%d %H:%M:%S")
                except Exception as e:
                    print(f"時間解析錯誤: {e}")
                    timestamp_formatted = "無效時間"

                # 嘗試解析 JSON 字串
                try:
                    json_data = json.loads(json_str)
                except json.JSONDecodeError as e:
                    print(f"JSON 解析錯誤: {e}")
                    json_data = {"error": "無效 JSON"}

                # 將解析後的資料加入列表
                parsed_list.append(MasaLogEntry(
                    timestamp=timestamp_formatted,
                    post_params=json_data.get("post_params", {}),
                    user_agent=json_data.get("user_agent", "未知"),
                    ip_address=json_data.get("ip_address", "未知")
                ))
            self.data_fetched.emit(parsed_list)
        except Exception as e:
            self.error_occurred.emit(f"API 請求失敗: {str(e)}")


class ExportToExcelThread(QThread):
    finished = pyqtSignal(bool, str)

    def __init__(self, data: List[MasaLogEntry], filename: str):
        super().__init__()
        self.data = data
        self.filename = filename

    def run(self):
        try:
            df = pd.DataFrame(self.data)
            df.to_excel(self.filename, index=False)
            self.finished.emit(True, f"匯出成功：{self.filename}")
        except Exception as e:
            self.finished.emit(False, f"匯出失敗：{str(e)}")


class MasaLogViewer(QMainWindow):
    def __init__(self):
        super().__init__()
        # 主視窗設定
        self.resize(1000, 700)
        self.setWindowTitle("Masa API Log 檢視工具")
        self.setWindowIcon(QIcon("icon.ico"))

        # 參數設定
        self.page_size = 10  # 每頁顯示的項目數
        self.total_size = 0  # 總項目數
        self.current_page = 1  # 當前頁碼
        self.total_pages = 1  # 總頁數
        self.sort_order = SortOrder.NEWEST_FIRST  # 預設排序方式
        self.parsed_list: List[MasaLogEntry] = []  # 儲存解析後的資料
        self.filtered_list: List[MasaLogEntry] = []  # 儲存過濾後的資料
        self.filter_entries: List[FilterEntry] = []  # 儲存過濾條件
        self.filter_layout_map = {}  # id → QHBoxLayout

        # 初始化 UI
        self._setup_ui()

    def _setup_ui(self):
        # 中央主元件
        main_widget = QWidget()
        self.setCentralWidget(main_widget)

        # === 第一列：Log Name 搜尋區 ===
        log_name_label = QLabel("Log Name:")
        log_name_input = QLineEdit()
        log_name_input.setPlaceholderText("輸入 Log Name")

        self.test_env_checkbox = QCheckBox("測試環境")
        self.test_env_checkbox.setChecked(False)  # 預設為測試環境

        search_btn = QPushButton("查詢")
        search_btn.clicked.connect(
            lambda: self._query_masa_log(log_name_input.text().strip())
        )
        export_btn = QPushButton("Excel 匯出")
        export_btn.clicked.connect(self._export_to_excel)
        sort_combo = QComboBox()
        for order in SortOrder:
            sort_combo.addItem(order.label, userData=order)
        sort_combo.setCurrentIndex(self.sort_order.index)
        sort_combo.currentIndexChanged.connect(
            lambda index: self._toggle_sort_order(
                sort_combo.itemData(index)
            )
        )

        search_layout = QHBoxLayout()
        search_layout.addWidget(log_name_label)
        search_layout.addWidget(log_name_input, 1)
        search_layout.addWidget(self.test_env_checkbox)
        search_layout.addWidget(search_btn)
        search_layout.addWidget(export_btn)
        search_layout.addWidget(sort_combo)

        # === 第二列：篩選按鈕區 ===
        add_filter_btn = QPushButton("新增篩選")
        add_filter_btn.clicked.connect(self._add_filter_entry)
        apply_filter_btn = QPushButton("套用")
        apply_filter_btn.clicked.connect(self._apply_filters)
        clear_filter_btn = QPushButton("清除")
        clear_filter_btn.clicked.connect(self._clear_filters)

        filter_layout = QHBoxLayout()
        filter_layout.addWidget(add_filter_btn, 1)
        filter_layout.addWidget(apply_filter_btn, 1)
        filter_layout.addWidget(clear_filter_btn, 1)
        self.filter_entries_layout = QVBoxLayout()

        # === 第三列：資料表格 ===
        self.scroll_area = QScrollArea()
        scroll_content = QWidget()
        self.scroll_layout = QVBoxLayout()
        scroll_content.setLayout(self.scroll_layout)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setWidget(scroll_content)

        # === 組合所有 layout ===
        main_layout = QVBoxLayout()
        main_layout.addLayout(search_layout)
        main_layout.addLayout(filter_layout)
        main_layout.addLayout(self.filter_entries_layout)
        main_layout.addWidget(self.scroll_area)

        main_widget.setLayout(main_layout)

        # === 底部狀態列 Layout ===
        self.total_count_label = QLabel()
        self.page_label = QLabel()
        self.page_spin = QSpinBox()
        self.page_spin.setFixedWidth(100)
        self.page_spin.setRange(1, self.total_pages)
        self.page_spin.setValue(self.current_page)
        self.page_spin.valueChanged.connect(self._on_page_change)
        self._update_status_bar()

        status_bar = QStatusBar()
        status_bar.addWidget(self.total_count_label)
        status_bar.addPermanentWidget(self.page_label)
        status_bar.addPermanentWidget(self.page_spin)
        self.setStatusBar(status_bar)

    def _update_status_bar(self):
        self.total_count_label.setText(f"數量：{self.total_size} 項")
        self.total_pages = max(1, math.ceil(
            len(self.filtered_list) / self.page_size))
        self.page_label.setText(
            f"第 {self.current_page} 頁 / 共 {self.total_pages} 頁")
        self.page_spin.setRange(1, self.total_pages)
        self.page_spin.setValue(self.current_page)

    def _query_masa_log(self, log_name: str):
        if not log_name:
            QMessageBox.warning(self, "錯誤", "請輸入 Log Name")
            return

        # 顯示 Loading
        self.loading = QProgressDialog("正在查詢...", None, 0, 0, self)
        self.loading.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.loading.setCancelButton(None)
        self.loading.setMinimumDuration(0)
        self.loading.show()

        # 啟動 API 請求線程
        self.api_thread = MasaLogAPIThread(
            log_name=log_name, is_test_env=self.test_env_checkbox.isChecked())
        self.api_thread.data_fetched.connect(self._on_masa_log_api_fetched)
        self.api_thread.error_occurred.connect(self._on_masa_log_api_error)
        self.api_thread.start()

    def _on_masa_log_api_fetched(self, data: list[MasaLogEntry]):
        self.loading.close()
        self.parsed_list = data
        self._apply_filters()

    def _on_masa_log_api_error(self, error: str):
        self.loading.close()
        QMessageBox.critical(self, "錯誤", f"API 請求失敗: {error}")

    def _export_to_excel(self):
        data = self.filtered_list.copy()
        if not data:
            QMessageBox.warning(self, "錯誤", "沒有資料可以匯出")
            return

        rows = []
        for entry in data:
            row = {
                "timestamp": entry.timestamp,
                "ip_address": entry.ip_address,
                "user_agent": entry.user_agent,
            }
            row.update(entry.post_params)
            rows.append(row)

        filename, _ = QFileDialog.getSaveFileName(
            self, "儲存為 Excel 檔案", "", "Excel 檔案 (*.xlsx)"
        )
        if not filename:
            return

        # 顯示 Loading
        self.loading = QProgressDialog("正在匯出...", None, 0, 0, self)
        self.loading.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.loading.setCancelButton(None)
        self.loading.setMinimumDuration(0)
        self.loading.show()

        # 啟動匯出線程
        self.export_thread = ExportToExcelThread(data=rows, filename=filename)
        self.export_thread.finished.connect(self._on_export_finished)
        self.export_thread.start()

    def _on_export_finished(self, success: bool, message: str):
        self.loading.close()
        if success:
            QMessageBox.information(self, "成功", "匯出成功")
        else:
            QMessageBox.critical(self, "錯誤", f"匯出失敗: {message}")

    def _toggle_sort_order(self, order: SortOrder):
        self.sort_order = order
        self._apply_filters()

    def _add_filter_entry(self):
        new_id = len(self.filter_entries)

        row_layout = QHBoxLayout()
        key_input = QLineEdit()
        key_input.setPlaceholderText("輸入要篩選的 Key")
        val_input = QLineEdit()
        val_input.setPlaceholderText("輸入要篩選的 Value")

        include_checkbox = QCheckBox("包含")
        include_checkbox.setChecked(True)  # 預設為包含
        blur_checkbox = QCheckBox("模糊搜尋")
        blur_checkbox.setChecked(False)  # 預設為精確搜尋

        remove_btn = QPushButton("移除")
        remove_btn.clicked.connect(
            lambda: self._remove_filter_entry(new_id)
        )

        # 整合 Layout
        row_layout.addWidget(key_input, 1)
        row_layout.addWidget(val_input, 1)
        row_layout.addWidget(include_checkbox)
        row_layout.addWidget(blur_checkbox)
        row_layout.addWidget(remove_btn)
        self.filter_entries_layout.addLayout(row_layout)
        self.filter_layout_map[new_id] = row_layout

        # 新增 FilterEntry
        new_entry = FilterEntry(
            id=new_id,
            key=key_input.text(),
            value=val_input.text(),
            include=include_checkbox.isChecked(),
            blur=blur_checkbox.isChecked()
        )
        self.filter_entries.append(new_entry)

    def _remove_filter_entry(self, entry_id: int):
        # 刪除對應的資料條件
        self.filter_entries = [
            entry for entry in self.filter_entries if entry.id != entry_id]

        # 從畫面上移除對應的 layout
        layout = self.filter_layout_map.get(entry_id)
        if layout:
            while layout.count():
                item = layout.takeAt(0)
                widget = item.widget()
                if widget:
                    widget.setParent(None)
            self.filter_entries_layout.removeItem(layout)
            del self.filter_layout_map[entry_id]

        self._apply_filters()

    def _apply_filters(self):
        # 清除之前的篩選結果
        self.filtered_list.clear()
        conditions: List[FilterEntry] = []

        # 逐一讀取畫面上的所有條件列
        for i in range(self.filter_entries_layout.count()):
            layout = self.filter_entries_layout.itemAt(i)
            if isinstance(layout, QHBoxLayout):
                widgets = [layout.itemAt(j).widget()
                           for j in range(layout.count())]

                key_input = widgets[0]
                val_input = widgets[1]
                include_checkbox = widgets[2]
                blur_checkbox = widgets[3]

                if isinstance(key_input, QLineEdit) and isinstance(val_input, QLineEdit):
                    key = key_input.text().strip()
                    val = val_input.text().strip()
                    if key and val:
                        conditions.append(FilterEntry(
                            id=i,
                            key=key,
                            value=val,
                            include=include_checkbox.isChecked(),
                            blur=blur_checkbox.isChecked()
                        ))
        self.filter_entries = conditions

        # 如果沒有篩選條件，則顯示全部資料
        if not conditions:
            self.filtered_list = self.parsed_list.copy()
        else:
            self.filtered_list = [
                # 先取出每一筆資料
                record for record in self.parsed_list
                # 檢查是否符合所有條件
                if all(self._entry_matches_condition(cond, record) for cond in conditions)
            ]

        # 根據排序方式進行排序
        if self.sort_order == SortOrder.NEWEST_FIRST:
            self.filtered_list.sort(
                key=lambda x: x.timestamp, reverse=True)
        elif self.sort_order == SortOrder.OLDEST_FIRST:
            self.filtered_list.sort(
                key=lambda x: x.timestamp, reverse=False)

        # 刷新資料顯示
        self._refresh_data(1)

    def _entry_matches_condition(self, entry: FilterEntry, record: MasaLogEntry) -> bool:
        # 目標值
        target_value = str(record.post_params.get(entry.key, ""))
        # 根據模糊搜尋或精確搜尋進行比較
        if entry.blur:
            result = entry.value in target_value
        else:
            result = entry.value == target_value
        # 根據包含或排除進行最終判斷
        return result if entry.include else not result

    def _clear_filters(self):
        for i in reversed(range(self.filter_entries_layout.count())):
            item = self.filter_entries_layout.itemAt(i)
            if item is not None:
                layout = item.layout()
                if layout is not None:
                    # 移除該 layout 裡的所有 widget
                    while layout.count():
                        w = layout.takeAt(0).widget()
                        if w:
                            w.setParent(None)
                    # 移除該 layout 本身
                    self.filter_entries_layout.removeItem(layout)
        self.filter_entries.clear()
        self._apply_filters()

    def _on_page_change(self, page: int):
        self._refresh_data(page)

    def _refresh_data(self, page: int):
        # 更新參數
        self.total_size = len(self.filtered_list)
        self.current_page = page
        self.total_pages = max(1, math.ceil(
            len(self.filtered_list) / self.page_size))

        # 更新底部狀態列
        self._update_status_bar()

        # 顯示資料
        self._display_data()

    def _display_data(self):
        # 清除畫面元件
        for i in reversed(range(self.scroll_layout.count())):
            widget = self.scroll_layout.itemAt(i).widget()
            if widget is not None:
                widget.deleteLater()

        # 計算目前頁面的範圍
        start_index = (self.current_page - 1) * self.page_size
        end_index = min(start_index + self.page_size, len(self.filtered_list))

        # 顯示目前頁面的資料
        for idx, rec in enumerate(self.filtered_list[start_index:end_index], start=start_index + 1):
            groupBox = QGroupBox()
            layout = QVBoxLayout(groupBox)
            layout.setContentsMargins(10, 10, 10, 10)
            layout.setSpacing(6)
            formLayout = QFormLayout()
            formLayout.setHorizontalSpacing(10)
            formLayout.setVerticalSpacing(5)
            formLayout.setFieldGrowthPolicy(
                QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow
            )  # 若欄位過長，則自動擴展

            # 顯示項目編號
            title_label = QLabel(f"第 {idx} 項 - 時間：{rec.timestamp}")
            title_label.setStyleSheet("font-weight: bold; font-size: 16px;")
            formLayout.addRow(title_label)

            # 間隔行
            spacer = QLabel("")
            spacer.setFixedHeight(5)
            formLayout.addRow(spacer)

            # 處理欄位內容
            for key, value in rec.post_params.items():
                # 強制轉為 String
                str_value = str(value)

                # 判斷該欄位是否符合篩選條件
                included = False  # 是否是包含
                blurred = False  # 是否為模糊搜尋
                for entry in self.filter_entries:
                    if not entry.key or not entry.value:
                        continue
                    if entry.key == key:
                        notBlurCond = not entry.blur and entry.value == str_value
                        blurCond = entry.blur and entry.value in str_value
                        if notBlurCond or blurCond:
                            included = entry.include
                            blurred = entry.blur
                            break

                # Key 欄位
                key_edit = QLineEdit(key)
                key_edit.setReadOnly(True)
                key_edit.setMaximumHeight(30)
                if included:
                    key_edit.setStyleSheet("background-color: green;")

                # Value 欄位
                value_edit = QTextEdit(str_value)
                value_edit.setReadOnly(True)
                doc = value_edit.document()
                doc.setTextWidth(value_edit.viewport().width())
                text_height = doc.size().height()
                value_edit.setFixedHeight(
                    min(max(int(text_height) + 8, 30), 100))
                if included and blurred:
                    start_idx = str_value.find(entry.value)
                    if start_idx != -1:
                        value_edit.setHtml(
                            (
                                str_value[:start_idx] + '<span style="color: green; font-weight: bold">' +
                                str_value[start_idx:start_idx+len(entry.value)] +
                                '</span>' +
                                str_value[start_idx+len(entry.value):]
                            )
                        )
                elif included:
                    value_edit.setStyleSheet("background-color: green;")

                formLayout.addRow(key_edit, value_edit)

            # 整合 Layout
            layout.addLayout(formLayout)
            layout.addWidget(QLabel(f"IP 位址：{rec.ip_address}"))
            layout.addWidget(QLabel(f"User-Agent：{rec.user_agent}"))
            self.scroll_layout.addWidget(groupBox)
            self.scroll_area.verticalScrollBar().setValue(0)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MasaLogViewer()
    window.show()
    sys.exit(app.exec())
