# pyniim

用於 NIIMBOT 標籤印表機的 Python library 與 CLI，以 **M2_H 的 BLE 列印**為首要目標，使用 **Bleak** 連線。

**API／CLI 參考 [pklaus/brother_ql](https://github.com/pklaus/brother_ql) 設計**，沿用其模組分工、呼叫流程與通用命令列參數，並依 NIIMBOT 協定與 BLE 連線需求調整。型號支援以 M2_H 為起點，逐步涵蓋其他 NIIMBOT 型號。

本專案使用 **GPT-6 Astra** 協助規劃與開發。

> **狀態：0.1.0a1，實驗性實作。** 已提供 Python API、CLI、離線轉換與 M2_H BLE 列印流程，並以模擬 transport 測試。目前沒有任何型號經本專案實機驗證；77 筆型號規格中，僅 M2_H 開放實驗性列印，其餘僅供查詢。

## 安裝與快速開始

需要 Python 3.11 以上版本。從本機 checkout 安裝：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
pyniim --help
pyniim info models --json
```

這是本機安裝方式，尚未發佈到 PyPI。BLE 功能需要作業系統的藍牙權限與可用的 BLE adapter；離線轉換與查詢型號不需要連線。

先以 `pyniim discover --json` 找出裝置識別碼，再傳給 `--printer`。掃描會列出所有找到的 BLE 廣播，包含未確認為 NIIMBOT 的裝置；真正連線時會確認服務與型號 ID。

40 × 30 mm 在目前 M2_H 300 DPI 設定下需要 **472 × 354 px** 的圖片。固定尺寸標籤不自動縮放；尺寸不符會回報預期像素，`rotate=auto` 只處理寬高對調的情況。

## 目標

- 使用 Bleak 掃描、連線與控制 M2_H，透過 BLE GATT 列印。
- 提供簡單的 `print_image()`，以及可分開使用的 `convert()`／`send()`。
- 提供同步便利介面與非同步連線 session。
- 對齊 `brother_ql` 的模組分工、通用參數與列印結果欄位。
- 建立 NIIMBOT 型號目錄，區分已收錄規格、實驗性支援與實機驗證。
- 列印完成與資料已傳送分開判定，回報裝置錯誤與不確定的列印結果。

## 輸入格式

接受圖片路徑、Pillow `Image.Image`，以及 Pillow 可讀取的二進位檔案物件。

圖片解碼由 Pillow 處理，常見格式包括 PNG、JPEG、BMP、GIF 與 TIFF；實際可用格式取決於 Pillow 的安裝環境。多幀圖片不會自動展開為多頁，應先逐幀擷取，再明確傳入圖片清單。

SVG、PDF、HTML 與文字排版不屬於首版的直接輸入，需先轉成點陣圖。條碼、QR code 與文字可以透過其他工具繪製成 Pillow Image 後列印。

## Python API

以下介面已實作；實際 BLE 列印仍待 M2_H 硬體驗證。

### 直接列印圖片

```python
from pyniim import Printer

with Printer("ble://<device-id>", model="M2_H") as printer:
    result = printer.print_image(
        "label.png",
        label="40x30",
        density=3,
        copies=1,
    )
```

`print_image()` 會在內部完成影像轉換與列印流程。`label="40x30"` 表示毫米尺寸，順序為印字頭方向的寬度 × 進紙方向的長度；這是用法範例，仍須使用符合機型與實際耗材的設定。

### 分開轉換與傳送

```python
from pyniim.raster import NiimbotRaster
from pyniim.conversion import convert
from pyniim.backends.helpers import discover, send

devices = discover(backend_identifier="bleak")

raster = NiimbotRaster("M2_H")
instructions = convert(
    raster,
    images=["label.png"],
    label="40x30",
    rotate="auto",
    threshold=70,
    dither=False,
    density=3,
    copies=1,
)

result = send(
    instructions,
    printer_identifier="ble://<device-id>",
    backend_identifier="bleak",
    blocking=True,
)
```

印表機無法直接讀取 PNG 或 JPEG。`convert()` 負責尺寸檢查、旋轉、邊界處理、黑白轉換與點陣資料準備；`send()` 負責裝置連線、協定交握、資料傳送與完成判定。

`convert()` 回傳 `PrintJob`，保存型號、頁面與列印設定，供傳送器依協定逐步執行。這與 `brother_ql.convert()` 回傳裸 `bytes` 不同，並非可直接互換的 API 或指令格式。

`blocking=True` 等待列印完成判定。首版明確拒絕 `blocking=False`，因為結束 BLE session 或過早傳送 PrintEnd 可能中斷實體列印；安全的背景工作管理尚未實作。

### 非同步使用

```python
import asyncio
from pyniim import AsyncPrinter

async def main():
    async with AsyncPrinter("ble://<device-id>", model="M2_H") as printer:
        result = await printer.print_image("label.png", label="40x30")
        return result

asyncio.run(main())
```

`pyniim.backends.helpers` 另提供 `discover_async()` 與 `send_async()`。同步 session 在同一生命週期內維持 BLE 連線與事件迴圈；已運行事件迴圈的應用程式應使用非同步介面。

### 列印結果

沿用 `brother_ql.send()` 的主要 dictionary 欄位，另附 `pages_printed`：

| 欄位 | 意義 |
| --- | --- |
| `instructions_sent` | 是否完成工作資料傳送 |
| `outcome` | `unknown`、`sent`、`printed` 或 `error` |
| `printer_state` | 最近取得的裝置狀態；未知時為 `None` |
| `did_print` | 是否收到可確認列印產生的證據 |
| `ready_for_next_job` | 是否確認裝置可接收下一個工作 |

只完成 BLE 寫入不能視為列印成功。列印過程失敗會拋出 `pyniim.errors.PrintError`，其 `result` 屬性保留最後已知進度；`did_print=True` 可能只代表部分頁面完成，整個工作成功須檢查 `outcome == "printed"`。逾時或中途斷線時不自動重送工作，以免重複列印；失敗的 session 不可再用，必須重新連線。

## CLI

```bash
# 掃描裝置與查詢規格
pyniim discover
pyniim info models
pyniim -m M2_H info labels
pyniim info env

# 直接列印
pyniim -b bleak -m M2_H -p 'ble://<device-id>' \
  print -l 40x30 --density 3 --copies 1 label.png

# 查詢裝置狀態
pyniim -p 'ble://<device-id>' status

# 離線轉換、檢視與傳送工作檔案
pyniim -m M2_H convert -l 40x30 label.png -o label.niim
pyniim analyze label.niim --output-dir previews
pyniim -p 'ble://<device-id>' send label.niim
```

`.niim` 是版本 1 的 JSON 工作檔案，包含型號、標籤尺寸、濃度、份數及以 Base64 保存的頁面點陣。檔案上限為 32 MiB，載入時驗證版本、像素尺寸與資料長度。它不是 Brother 指令檔，也不是可直接寫入 BLE 的封包擷取檔。

支援 `PYNIIM_BACKEND`、`PYNIIM_MODEL`、`PYNIIM_PRINTER` 與 `PYNIIM_LABEL` 環境變數，以及 `--debug` 與查詢命令的 `--json` 輸出。

## 與 brother_ql 的對照

| brother_ql | pyniim |
| --- | --- |
| `BrotherQLRaster(model)` | `NiimbotRaster(model)` |
| `convert(qlr, images, label, **kwargs)` | `convert(raster, images, label, ...)` |
| `convert()` 回傳指令 `bytes` | 回傳結構化 `PrintJob` |
| `backends.helpers.discover()` | 同一路徑，預設 backend 為 `bleak` |
| `backends.helpers.send()` | 沿用主要參數與結果欄位 |
| `ModelsManager`／`LabelsManager` | 提供對應的型號與標籤查詢 |
| `discover`、`info`、`print`、`send`、`analyze` | 保留相同命令分工 |
| `-b`、`-m`、`-p`、`-l`、`-r`、`-t`、`-d` | 保留對應的通用參數意義 |

`convert` 子命令、`status`、`print_image()` 與 async API 是 pyniim 的擴充。Niimbot 專用設定包含濃度、份數與紙張類型；Brother 專用的雙色、600 DPI 與品質模式不直接套用。壓縮與裁切功能依各型號實際能力決定。

## 型號目錄與支援狀態

以 [niimbluelib 的型號資料](https://github.com/MultiMote/niimbluelib/blob/main/src/printer_models.ts) 為研究起點，已匯入 77 筆規格並保留 MIT 授權及來源 revision。收錄目標涵蓋其 NIIMBOT 型號，包括 A、B、D、K、M、N、P、S、T 等系列；不代表所有型號均可使用 BLE 或已可列印。

| 優先順序 | 型號 | 目前狀態 |
| --- | --- | --- |
| 首要 | M2_H（接受 M2-H 別名） | 已實作 Bleak 流程；實驗性、未實機驗證 |
| 後續 | B1、B21、D11、D110 | 已收錄；未開放列印 |
| 後續 | B1_PRO、B2_PRO、B21_PRO、D11_H、D110_M | 已收錄；未開放列印 |
| 其他 | 型號目錄中的其餘 NIIMBOT 機型 | 已收錄；未開放列印 |

目前 `info models` 使用 `catalog-only`（僅收錄）與 `experimental`（實驗性）標記。未來通過硬體測試才會標示實機驗證，並記錄連線方式、韌體、作業系統、紙張與測試結果。未知型號或未實作的協定不自動套用 M2_H 設定。

M2_H 的研究資料記載 model ID 為 4608、300 DPI。印字頭寬度存在來源差異：[社群 Wiki](https://printers.niim.blue/hardware/niimbot-m2h/) 記載 576 px，[另一個 BLE 實作](https://github.com/iscarelli/niimbot-web-bluetooth/blob/main/docs/protocol-v4.md) 使用 567 px。目前保留上游印字頭規格，列印寬度暫時保守限制為 567 px；這項差異仍需透過實機邊界測試確認。尚未提供經校正的置中偏移與邊界設定。

## BLE 與內部架構

Bleak 後端先掃描取得 `BLEDevice`，連線後確認服務與 characteristic、訂閱通知，再執行型號辨識與列印命令。寫入依 characteristic 的容量分段，接收端處理通知分片與多封包合併。

`ble://<device-id>` 使用掃描結果中的識別碼。在 macOS 上通常為裝置 UUID，不假設一定是 MAC address。參考 [Bleak 文件](https://bleak.readthedocs.io/en/latest/api/client.html)。

| 模組 | 職責 |
| --- | --- |
| `models`、`labels` | 型號能力、標籤尺寸、紙張類型與列印邊界 |
| `conversion`、`raster` | 影像處理與點陣資料準備 |
| `protocol` | 封包編解碼、checksum 與回覆解析 |
| `tasks` | 依機型選擇列印流程、流量控制與完成判定 |
| `backends/bleak` | 掃描、GATT、notify 與分段寫入 |
| `printer`、`cli` | 公開 Python API 與命令列 |

## 首版限制

- 只允許 M2_H 列印，紙張類型只開放有間隙標籤（`label_type=1`）。
- `status` 回傳原始 heartbeat，尚不解讀缺紙、上蓋、色帶或電量，避免套用其他型號的未驗證欄位。
- 多頁使用逐頁等待完成的保守流程；多份會使用裝置 copies 欄位。兩者仍需實機測試。
- `blocking=False`、自動縮放、連續紙、裁切與其他型號的列印尚未實作。
- `info labels` 是尺寸範例，不是已驗證的 NIIMBOT 耗材產品清單。

## 開發與驗證

```bash
python -m pip install -e '.[dev]'
pytest -q
ruff check src tests tools
ruff format --check src tests tools
python -m build
```

單元測試不會掃描或連線實體裝置，涵蓋圖片輸入、黑白位元順序、工作檔案驗證、CLI 離線轉換、封包分片、寫入分段、型號不符、逾時、斷線與延遲回覆。

型號資料固定於 niimbluelib revision `35110a716c960448ee17a9270e4c3a131149866f`。更新時先下載該 revision 的 `src/printer_models.ts` 與 `LICENSE`，再執行：

```bash
python tools/import_models.py /path/to/printer_models.ts <revision> /path/to/LICENSE
```

## 後續驗收

1. M2_H 實機掃描、連線、辨識與 heartbeat 擷取。
2. 單張、多份、多頁列印；測量方向、尺寸、邊界及確認完成狀態。
3. 缺紙、上蓋開啟、色帶問題與中途斷線的實機測試。
4. macOS、Linux、Windows 的 BLE 相容性驗證。
5. 依實機證據擴充其他型號與紙張類型。

首版驗收目標：透過 Bleak 在 M2_H 正確列印指定尺寸圖片，確認完成或回報失敗，且 API 與 CLI 共用相同的列印流程。

## 參考資料

- [brother_ql](https://github.com/pklaus/brother_ql)：API 與 CLI 設計參考。
- [niimbluelib](https://github.com/MultiMote/niimbluelib)：NIIMBOT 型號與協定實作參考。
- [NIIMBOT Community Wiki](https://printers.niim.blue/)：硬體及協定研究資料。
- [niimbot-web-bluetooth](https://github.com/iscarelli/niimbot-web-bluetooth)：BLE 列印流程與機型驗證參考。
- [Bleak](https://github.com/hbldh/bleak)：Python BLE 通訊。

本專案自身的授權尚未決定。型號資料取自 MIT 授權的 niimbluelib，授權全文隨套件保存於 `src/pyniim/data/niimbluelib-LICENSE.txt`。Brother API 僅作介面設計參考；協定研究來源另見 [THIRD_PARTY.md](THIRD_PARTY.md)。
