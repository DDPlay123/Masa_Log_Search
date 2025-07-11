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
from collections import defaultdict
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QPushButton, QLineEdit,
    QVBoxLayout, QHBoxLayout, QMessageBox, QScrollArea, QGroupBox, QFormLayout,
    QSpinBox, QFileDialog, QProgressDialog, QTextEdit, QComboBox, QDateTimeEdit,
    QStatusBar, QCheckBox, QLayout
)
from PyQt6.QtGui import QIcon, QTextCharFormat, QColor
from PyQt6.QtCore import Qt, QThread, pyqtSignal


class SortOrder(Enum):
    NEWEST_FIRST = (0, "排序：最新在前")
    OLDEST_FIRST = (1, "排序：最舊在前")

    def __init__(self, index, label):
        self.index = index
        self.label = label


class TimeFilter(Enum):
    ALL = (0, "全部時間")
    BEFORE_TIME = (1, "在此時間之前")
    AFTER_TIME = (2, "在此時間之後")
    TIME_RANGE = (3, "在此時間範圍內")

    def __init__(self, index, label):
        self.index = index
        self.label = label


@dataclass
class MasaLogEntry:
    timestamp: str
    post_params: dict
    user_agent: str
    ip_address: str
    raw_otd: str = ""  # 從 post_params 獨立出 otd (避免格式跑掉)


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

                # 抓取 otd 資料
                otd_match = re.search(
                    r'"otd"\s*:\s*"((?:\\.|[^"\\])*)"', json_str)
                if otd_match:
                    try:
                        raw_otd = json.loads(f'"{otd_match.group(1)}"')
                    except Exception as e:
                        print(f"otd JSON decode error: {e}")
                        raw_otd = ""
                else:
                    raw_otd = ""

                # 將解析後的資料加入列表
                parsed_list.append(MasaLogEntry(
                    timestamp=timestamp_formatted,
                    post_params=json_data.get("post_params", {}),
                    user_agent=json_data.get("user_agent", "未知"),
                    ip_address=json_data.get("ip_address", "未知"),
                    raw_otd=raw_otd
                ))
            self.data_fetched.emit(parsed_list)
        except Exception as e:
            self.error_occurred.emit(f"API 請求失敗: {str(e)}")


class ExportToExcelThread(QThread):
    finished = pyqtSignal(bool, str)

    def __init__(self, data: list, filename: str):  # 修正型態註解
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
        self.time_filter = TimeFilter.ALL  # 預設時間篩選方式
        self.parsed_list: List[MasaLogEntry] = []  # 儲存解析後的資料
        self.filtered_list: List[MasaLogEntry] = []  # 儲存過濾後的資料
        self.filter_entries: List[FilterEntry] = []  # 儲存過濾條件
        self._filter_id_counter = 0
        self.filter_layout_map = {}  # id → QHBoxLayout

        # 初始化 UI
        self._setup_ui()

    def _setup_ui(self):
        # 中央主元件
        main_widget = QWidget()
        self.setCentralWidget(main_widget)

        # === Log Name 搜尋區 ===
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

        time_filter_combo = QComboBox()
        for time_filter in TimeFilter:
            time_filter_combo.addItem(time_filter.label, userData=time_filter)
        time_filter_combo.setCurrentIndex(self.time_filter.index)
        time_filter_combo.currentIndexChanged.connect(
            lambda index: self._toggle_time_edit(
                time_filter_combo.itemData(index)
            )
        )

        search_layout = QHBoxLayout()
        search_layout.addWidget(log_name_label)
        search_layout.addWidget(log_name_input, 1)
        search_layout.addWidget(self.test_env_checkbox)
        search_layout.addWidget(search_btn)
        search_layout.addWidget(export_btn)
        search_layout.addWidget(sort_combo)
        search_layout.addWidget(time_filter_combo)

        # === 時間篩選區 ===
        before_time_label = QLabel("在此時間之前:")
        self.before_time_edit = QDateTimeEdit()
        self.before_time_edit.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        self.before_time_edit.setCalendarPopup(True)
        self.before_time_edit.setDateTimeRange(
            datetime(2000, 1, 1, 0, 0), datetime.now())
        self.before_time_edit.setDateTime(datetime.now())

        self.before_time_edit_layout = QHBoxLayout()
        self.before_time_edit_layout.addWidget(before_time_label)
        self.before_time_edit_layout.addWidget(self.before_time_edit, 1)

        after_time_label = QLabel("在此時間之後:")
        self.after_time_edit = QDateTimeEdit()
        self.after_time_edit.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        self.after_time_edit.setCalendarPopup(True)
        self.after_time_edit.setDateTimeRange(
            datetime(2000, 1, 1, 0, 0), datetime.now())
        self.after_time_edit.setDateTime(datetime.now())

        self.after_time_edit_layout = QHBoxLayout()
        self.after_time_edit_layout.addWidget(after_time_label)
        self.after_time_edit_layout.addWidget(self.after_time_edit, 1)

        time_range_label = QLabel("在此時間範圍內:")
        self.start_time_edit = QDateTimeEdit()
        self.start_time_edit.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        self.start_time_edit.setCalendarPopup(True)
        self.start_time_edit.setDateTimeRange(
            datetime(2000, 1, 1, 0, 0), datetime.now())
        self.start_time_edit.setDateTime(datetime.now())
        self.end_time_edit = QDateTimeEdit()
        self.end_time_edit.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        self.end_time_edit.setCalendarPopup(True)
        self.end_time_edit.setDateTimeRange(
            datetime(2000, 1, 1, 0, 0), datetime.now())
        self.end_time_edit.setDateTime(datetime.now())

        self.time_range_edit_layout = QHBoxLayout()
        self.time_range_edit_layout.addWidget(time_range_label)
        self.time_range_edit_layout.addWidget(self.start_time_edit, 1)
        self.time_range_edit_layout.addWidget(QLabel("至"))
        self.time_range_edit_layout.addWidget(self.end_time_edit, 1)

        # === 篩選按鈕區 ===
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

        # === 資料表格 ===
        self.scroll_area = QScrollArea()
        scroll_content = QWidget()
        self.scroll_layout = QVBoxLayout()
        scroll_content.setLayout(self.scroll_layout)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setWidget(scroll_content)

        # === 組合所有 layout ===
        main_layout = QVBoxLayout()
        main_layout.addLayout(search_layout)
        main_layout.addLayout(self.before_time_edit_layout)
        main_layout.addLayout(self.after_time_edit_layout)
        main_layout.addLayout(self.time_range_edit_layout)
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

        status_bar = QStatusBar()
        status_bar.addWidget(self.total_count_label)
        status_bar.addPermanentWidget(self.page_label)
        status_bar.addPermanentWidget(self.page_spin)
        self.setStatusBar(status_bar)

        # 初始化狀態
        self._toggle_time_edit(self.time_filter)
        self._update_status_bar()

    def _toggle_sort_order(self, order: SortOrder):
        self.sort_order = order
        self._apply_filters()

    def _toggle_time_edit(self, time_filter: TimeFilter):
        self.time_filter = time_filter
        self._toggle_layout_widgets_visible(
            self.before_time_edit_layout,
            time_filter == TimeFilter.BEFORE_TIME
        )
        self._toggle_layout_widgets_visible(
            self.after_time_edit_layout,
            time_filter == TimeFilter.AFTER_TIME
        )
        self._toggle_layout_widgets_visible(
            self.time_range_edit_layout,
            time_filter == TimeFilter.TIME_RANGE
        )
        self._apply_filters()

    def _toggle_layout_widgets_visible(self, layout: QLayout, visible: bool):
        for i in range(layout.count()):
            item = layout.itemAt(i)
            widget = item.widget()
            if widget:
                widget.setVisible(visible)

    def _update_status_bar(self):
        self.total_count_label.setText(f"數量：{self.total_size} 項")
        self.total_pages = max(1, math.ceil(
            len(self.filtered_list) / self.page_size))
        self.page_label.setText(
            f"第 {self.current_page} 頁 / 共 {self.total_pages} 頁")
        self.page_spin.blockSignals(True)
        self.page_spin.setRange(1, self.total_pages)
        self.page_spin.setValue(self.current_page)
        self.page_spin.blockSignals(False)

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

    def _on_masa_log_api_fetched(self, data: List[MasaLogEntry]):
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

    def _add_filter_entry(self):
        new_id = self._filter_id_counter
        self._filter_id_counter += 1

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

        data = self.parsed_list.copy()

        # 將條件依 key 分組
        grouped_conditions = defaultdict(list)
        for cond in conditions:
            grouped_conditions[cond.key].append(cond)

        # 比對每筆資料是否符合所有 key 的「任一條件」
        def record_matches(record: MasaLogEntry) -> bool:
            for key, cond_list in grouped_conditions.items():
                value = str(record.post_params.get(key, ""))
                if not any(self._entry_matches_condition(cond, value) for cond in cond_list):
                    return False
            return True

        # 條件過濾
        if conditions:
            data = [record for record in data if record_matches(record)]

        # 時間條件過濾
        def to_dt(val: str):
            try:
                return datetime.strptime(val, "%Y-%m-%d %H:%M:%S")
            except:
                return None

        if self.time_filter == TimeFilter.BEFORE_TIME:
            bound = self.before_time_edit.dateTime().toPyDateTime()
            data = [rec for rec in data if (
                dt := to_dt(rec.timestamp)) and dt <= bound]

        elif self.time_filter == TimeFilter.AFTER_TIME:
            bound = self.after_time_edit.dateTime().toPyDateTime()
            data = [rec for rec in data if (
                dt := to_dt(rec.timestamp)) and dt >= bound]

        elif self.time_filter == TimeFilter.TIME_RANGE:
            start = self.start_time_edit.dateTime().toPyDateTime()
            end = self.end_time_edit.dateTime().toPyDateTime()
            data = [rec for rec in data if (dt := to_dt(
                rec.timestamp)) and start <= dt <= end]

        self.filtered_list = data

        # 根據排序方式排序
        reverse = self.sort_order == SortOrder.NEWEST_FIRST
        self.filtered_list.sort(key=lambda x: x.timestamp, reverse=reverse)

        self._refresh_data(1)

    def _entry_matches_condition(self, entry: FilterEntry, value: str) -> bool:
        if entry.blur:
            result = entry.value in value
        else:
            result = entry.value == value
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
        self.filter_layout_map.clear()
        self._filter_id_counter = 0
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
                if key == "otd" and rec.raw_otd:
                    str_value = rec.raw_otd  # ← 使用原始 JSON otd
                else:
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
                    key_edit.setStyleSheet("background-color: lightgreen;")
                else:
                    key_edit.setStyleSheet("")

                # Value 欄位
                value_edit = QTextEdit()
                value_edit.setReadOnly(True)

                if included and blurred:
                    self._set_rich_text_with_auto_height(
                        value_edit, str_value, entry.value, color="lightgreen")
                elif included:
                    value_edit.setStyleSheet("background-color: lightgreen;")
                    self._set_text_with_auto_height(value_edit, str_value)
                else:
                    self._set_text_with_auto_height(value_edit, str_value)

                formLayout.addRow(key_edit, value_edit)

            # 整合 Layout
            layout.addLayout(formLayout)
            layout.addWidget(QLabel(f"IP 位址：{rec.ip_address}"))
            layout.addWidget(QLabel(f"User-Agent：{rec.user_agent}"))
            self.scroll_layout.addWidget(groupBox)
            self.scroll_area.verticalScrollBar().setValue(0)

    def _set_text_with_auto_height(self, text_edit: QTextEdit, text: str, max_height: int = 300):
        text_edit.clear()
        text_edit.setPlainText(text)

        doc = text_edit.document()
        doc.setTextWidth(text_edit.viewport().width())
        height = doc.size().height()
        text_edit.setFixedHeight(min(max(int(height) + 8, 30), max_height))

    def _set_rich_text_with_auto_height(self, text_edit: QTextEdit, full_text: str, keyword: str, color: str = "green", max_height: int = 300):
        text_edit.clear()
        cursor = text_edit.textCursor()

        fmt_normal = QTextCharFormat()
        fmt_highlight = QTextCharFormat()
        fmt_highlight.setBackground(QColor(color))

        last = 0
        while (idx := full_text.find(keyword, last)) != -1:
            cursor.insertText(full_text[last:idx], fmt_normal)
            cursor.insertText(keyword, fmt_highlight)
            last = idx + len(keyword)
        cursor.insertText(full_text[last:], fmt_normal)

        doc = text_edit.document()
        doc.setTextWidth(text_edit.viewport().width())
        height = doc.size().height()
        text_edit.setFixedHeight(min(max(int(height) + 8, 30), max_height))


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setWindowIcon(QIcon("icon.ico"))
    window = MasaLogViewer()
    window.show()
    sys.exit(app.exec())
