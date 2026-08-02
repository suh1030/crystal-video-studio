# Crystal Video Workflow

免費、無訂閱的 5 分鐘水晶 YouTube 影片產生器。輸入主題、英文腳本與選用的背景音樂後，自動完成：

- 免費 AI 英文配音
- 本機素材優先，選配 Pexels 免費素材搜尋
- 1920×1080、16:9、30 FPS
- 固定 300 秒影片
- 每句一行字幕及半透明黑底
- 圖片 Ken Burns 動態效果、影片裁切與淡入淡出
- 旁白與背景音樂混音
- MP4、SRT、腳本與 metadata 輸出

## Web 版：只輸入礦石名稱

Web 版會呼叫免費的本機 Ollama 模型撰寫腳本，再接續素材、配音、字幕與 FFmpeg 合成，頁面會即時顯示每個處理階段。

先安裝並下載模型：

```bash
brew install ollama
ollama serve
ollama pull qwen3:8b
```

第一次執行請保留 `ollama serve` 的終端機視窗，再開另一個終端機執行：

```bash
./run.command
```

瀏覽器開啟：

```text
http://127.0.0.1:8765
```

輸入 `Amethyst`、`Aquamarine` 等礦石名稱後即可開始。完成時頁面會出現 MP4 下載按鈕。

若使用其他 Ollama 模型：

```bash
OLLAMA_MODEL=llama3.1:8b ./run.command
```

## 1. 安裝（macOS）

先安裝 Python 3 與 FFmpeg：

```bash
brew install python ffmpeg
```

在專案資料夾中執行：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

也可以直接使用 `run.command`；第一次執行時會自動建立 Python 環境。

## 2. 準備腳本

5 分鐘英文旁白建議約 600～700 個英文單字。先將腳本存成文字檔，例如：

```text
scripts/aquamarine.txt
```

內附 `sample/amethyst-script.txt` 可直接測試。實際配音超過 298 秒時，程式會停止並要求縮短腳本，避免旁白被截斷。

## 3. 準備畫面

### 使用自己的素材（最推薦）

建立與主題同名的小寫資料夾：

```text
assets/amethyst/
├── amethyst-01.mp4
├── amethyst-02.jpg
└── amethyst-03.png
```

支援 MP4、MOV、MKV、WebM、JPG、PNG 與 WebP。程式會循環素材填滿 5 分鐘。影片素材不足時，圖片會套用緩慢推近／拉遠效果。

### 自動取得 Pexels 素材（選配）

1. 到 <https://www.pexels.com/api/> 免費申請 API key。
2. 複製 `.env.example` 為 `.env`。
3. 填入 `PEXELS_API_KEY`。

沒有 key 也能執行；程式會使用本機素材。請依 Pexels API 規範在 YouTube 說明欄標示素材來源，並盡可能標註攝影者。

## 4. 加入背景音樂（選配）

將 YouTube Audio Library 下載的音樂放入：

```text
assets/music/ambient.mp3
```

背景音量預設為旁白的 10%，可在 `config.yaml` 修改。

## 5. 產生影片

```bash
./run.command \
  --topic "Amethyst" \
  --script sample/amethyst-script.txt \
  --music assets/music/ambient.mp3
```

沒有背景音樂：

```bash
./run.command --topic "Amethyst" --script sample/amethyst-script.txt
```

輸出位於：

```text
output/
├── amethyst-5min-1080p.mp4
├── amethyst.srt
├── amethyst-script.txt
└── amethyst-metadata.json
```

## 6. 調整風格

`config.yaml` 可調整：

- AI 聲音與語速
- 影片長度、畫面尺寸與 FPS
- 每個鏡頭秒數
- 字幕字體、大小和位置
- 背景音樂音量
- Pexels 搜尋詞與下載數量

常用英文聲音：

- `en-US-AriaNeural`：自然、溫和女聲
- `en-US-JennyNeural`：清晰女聲
- `en-GB-SoniaNeural`：英式女聲
- `en-US-GuyNeural`：美式男聲

列出目前所有聲音：

```bash
source .venv/bin/activate
edge-tts --list-voices
```

## 注意事項

- Edge TTS 需要網路，但不需要 API key；若服務端規則改變，可能需要更換成 Kokoro 或 Piper 本機語音。
- 素材的授權仍應逐一確認，並保存原始素材網址與作者資訊。
- YouTube 是否營利不只看素材授權，也會評估內容是否具有原創價值。建議自行審稿、挑選素材，並建立固定的頻道敘事與視覺風格。
- 第一次產生 1080p 五分鐘影片會花數分鐘，速度取決於電腦效能與素材數量。
