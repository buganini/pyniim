# pyniim

用於 NIIMBOT 標籤印表機的 Python library 與 CLI，以 **M2_H 的 BLE 列印**為首要目標，使用 **Bleak** 連線。

**API／CLI 參考 [pklaus/brother_ql](https://github.com/pklaus/brother_ql) 設計**，沿用其模組分工、呼叫流程與通用命令列參數，並依 NIIMBOT 協定與 BLE 連線需求調整。型號支援以 M2_H 為起點，逐步涵蓋其他 NIIMBOT 型號。

本專案使用 **GPT-6 Astra** 協助規劃與開發。

> **狀態：規劃中。** 目前僅有設計文件，尚未實作 Python 套件或 CLI。以下程式碼、命令、檔案格式與介面均為預定設計，尚不可執行；目前沒有任何型號經本專案實機驗證。

## 目標

- 使用 Bleak 掃描、連線與控制 M2_H，透過 BLE GATT 列印。
- 提供簡單的 `print_image()`，以及可分開使用的 `convert()`／`send()`。
- 提供同步便利介面與非同步連線 session。
- 對齊 `brother_ql` 的模組分工、通用參數與列印結果欄位。
- 建立 NIIMBOT 型號目錄，區分已收錄規格、實驗性支援與實機驗證。
- 列印完成與資料已傳送分開判定，回報裝置錯誤與不確定的列印結果。

## 輸入格式

預計接受圖片路徑、Pillow `Image.Image`，以及 Pillow 可讀取的二進位檔案物件。

圖片解碼由 Pillow 處理，常見格式包括 PNG、JPEG、BMP、GIF 與 TIFF；實際可用格式取決於 Pillow 的安裝環境。多幀圖片不會自動展開為多頁，應先逐幀擷取，再明確傳入圖片清單。

SVG、PDF、HTML 與文字排版不屬於首版的直接輸入，需先轉成點陣圖。條碼、QR code 與文字可以透過其他工具繪製成 Pillow Image 後列印。

## Python API

以下均為規劃中的介面。

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

規劃中 `convert()` 回傳 `PrintJob`，保存型號、頁面與列印設定，供傳送器依協定逐步執行。這與 `brother_ql.convert()` 回傳裸 `bytes` 不同，並非可直接互換的 API 或指令格式。

`blocking=True` 等待列印完成判定；`blocking=False` 完成必要的傳輸與交握後返回，不等待實體列印完成，也不表示整個工作會在背景執行。

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

另規劃 `discover_async()` 與 `send_async()`。同步 session 在同一生命週期內維持 BLE 連線與事件迴圈；已運行事件迴圈的應用程式應使用非同步介面。

### 列印結果

預計沿用 `brother_ql.send()` 的主要 dictionary 欄位：

| 欄位 | 意義 |
| --- | --- |
| `instructions_sent` | 是否完成工作資料傳送 |
| `outcome` | `unknown`、`sent`、`printed` 或 `error` |
| `printer_state` | 最近取得的裝置狀態；未知時為 `None` |
| `did_print` | 是否收到可確認列印產生的證據 |
| `ready_for_next_job` | 是否確認裝置可接收下一個工作 |

只完成 BLE 寫入不能視為列印成功。逾時或中途斷線時保留不確定狀態，不自動重新送出整個工作，以免重複列印。

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
pyniim analyze label.niim
pyniim -p 'ble://<device-id>' send label.niim
```

`.niim` 預計為帶有版本與型號資訊的工作檔案，格式尚未定案；它不是 Brother 指令檔，也不是可直接寫入 BLE 的封包擷取檔。

預計支援 `PYNIIM_BACKEND`、`PYNIIM_MODEL`、`PYNIIM_PRINTER` 與 `PYNIIM_LABEL` 環境變數，以及 `--debug` 與查詢命令的 `--json` 輸出。

## 與 brother_ql 的對照

| brother_ql | pyniim 規劃 |
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

## 型號支援計畫

以 [niimbluelib 的型號資料](https://github.com/MultiMote/niimbluelib/blob/main/src/printer_models.ts) 為研究起點，建立有來源版本與驗證紀錄的型號目錄。收錄目標涵蓋其 NIIMBOT 型號，包括 A、B、D、K、M、N、P、S、T 等系列；不代表所有型號均可使用 BLE 或已可列印。

| 優先順序 | 型號 | 目前狀態 |
| --- | --- | --- |
| 首要 | M2_H（接受 M2-H 別名） | 規劃以 Bleak 實作及實機驗證 |
| 後續 | B1、B21、D11、D110 | 待確認各型號協定與 BLE 能力 |
| 後續 | B1_PRO、B2_PRO、B21_PRO、D11_H、D110_M | 待實作對應列印流程與驗證 |
| 其他 | 型號目錄中的其餘 NIIMBOT 機型 | 逐步收錄規格，另行評估列印支援 |

未來每個型號分別標示「已收錄」、「實驗性支援」或「實機驗證」，並記錄連線方式、韌體、作業系統、紙張與測試結果。未知型號或未實作的協定不自動套用 M2_H 設定。

M2_H 的研究資料記載 model ID 為 4608、300 DPI。印字頭寬度存在來源差異：[社群 Wiki](https://printers.niim.blue/hardware/niimbot-m2h/) 記載 576 px，[另一個 BLE 實作](https://github.com/iscarelli/niimbot-web-bluetooth/blob/main/docs/protocol-v4.md) 使用 567 px。實作前需透過邊界測試確認，並分開定義實體印字頭寬度與可列印區域。

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

## 開發順序

1. 套件骨架、型號資料結構、影像轉換與封包編解碼測試。
2. M2_H BLE 掃描、連線、辨識、通知解析與狀態查詢。
3. M2_H 單張、多份、多頁列印，以及尺寸、方向、邊界與完成判定。
4. 同步／非同步 API、CLI、工作檔案與錯誤處理。
5. 其他型號擴充與逐型號、逐平台的實機驗證。

首版驗收目標：透過 Bleak 在 M2_H 正確列印指定尺寸圖片，確認完成或回報失敗，且 API 與 CLI 共用相同的列印流程。

測試將涵蓋封包分片、checksum 錯誤、回覆逾時、斷線與型號不符；單元測試使用模擬 transport，實際列印能力另以硬體測試驗證。

## 參考資料

- [brother_ql](https://github.com/pklaus/brother_ql)：API 與 CLI 設計參考。
- [niimbluelib](https://github.com/MultiMote/niimbluelib)：NIIMBOT 型號與協定實作參考。
- [NIIMBOT Community Wiki](https://printers.niim.blue/)：硬體及協定研究資料。
- [niimbot-web-bluetooth](https://github.com/iscarelli/niimbot-web-bluetooth)：BLE 列印流程與機型驗證參考。
- [Bleak](https://github.com/hbldh/bleak)：Python BLE 通訊。

本專案授權尚未決定；若引入第三方程式碼或資料，將記錄來源並遵守其授權。
