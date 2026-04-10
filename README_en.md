# LocalLensTranslator

A Windows desktop tool that reads multiple screen regions via OCR and displays real-time translations as overlays using a local LLM.

<img width="256" height="256" alt="Image" src="https://github.com/user-attachments/assets/e2994e7a-b7c6-4292-94f6-5120fcfff2bd" />
<img width="564" height="395" alt="Image" src="https://github.com/user-attachments/assets/59e5217f-aa00-48b6-b16b-51f284693054" />

---

## Requirements

- Windows 10 / 11
- Python 3.10 or later
- LM Studio, Ollama, llama.cpp, or similar (must be running as a local server)
- VRAM: ~3GB+ (varies by model. TranslateGemma 4B or Gemma 4 E4B are recommended)
- RAM: ~3GB+ (varies by model. TranslateGemma uses ~3GB / Gemma 4 uses ~5GB)

---

## Translation Speed

On a 4070Ti 12GB: short texts translate in under 2 seconds (3 sentences), longer texts in under 4 seconds (10 sentences).  
For gaming use, most translations complete in under 2 seconds.  
Gemma 4 tends to produce better translation quality. For gaming, Gemma4-E2B Q6_K is lightweight and fast — recommended.  
Note: The model may occasionally start "talking" when it encounters a colon (:). (Fixed)

---

## Installation ⚠️ An exe file is available — please download it from Releases ⚠️

▼ For those who prefer running from source ▼

```bash
pip install mss Pillow winsdk
```

For the borderless window feature:

```bash
pip install pywin32
```

For global shortcuts (works even when the app is in the background):

```bash
pip install keyboard
```

---

## How to Launch

```bash
python main.py
```

---

## Basic Usage

1. Start LM Studio and enable the local server
2. Launch LocalLensTranslator
3. Open **⚙ Settings**, configure the LM Studio endpoint URL and model, then save
4. Click **＋ Add Region** and drag to select the screen area you want to translate
5. OCR and translation run automatically, and the result is displayed as an overlay on the selected area

---

## UI Overview

### Main Window

| Element | Description |
|---------|-------------|
| ● Translation ON / ○ Translation OFF | Indicator showing whether translation display is active |
| Dot Optimize | Enables OCR preprocessing optimized for pixel/dot fonts |
| Move & Retranslate | Automatically retranslates when a region overlay is moved |
| ＋ Add Region | Select a new translation area on screen |
| ▶ Translate All | Retranslate all active regions |
| ⏸ ON/OFF | Toggle translation display on/off for all regions |
| ✕ Clear All | Remove all translation regions |
| ⚙ | Open the settings dialog |
| Region List | Shows the coordinates and status of all translation regions |
| OCR Correction Dictionary | Enter correction rules for common OCR misreads |
| Borderless Mode | Force the active game window into borderless fullscreen |
| Source Language Toggle | Switch the source language (JA→EN / English / Chinese / Korean) |

※ Dot Optimize may or may not improve accuracy depending on the font.

### Overlay Controls

| Action | Behavior |
|--------|----------|
| Right-click | Show menu (Retranslate / JA→EN & Copy / ☒ Move & Retranslate / Delete / ON-OFF) |
| Right-click + Drag | Move the overlay (retranslates at new position if Move & Retranslate is ON) |

---

## Source Language Modes

| Toggle | Behavior |
|--------|----------|
| JA→EN | Reads Japanese via OCR, translates to English, and copies the result to the clipboard |
| English | Reads English via OCR and translates to Japanese |
| Chinese | Reads Chinese via OCR and translates to Japanese |
| Korean | Reads Korean via OCR and translates to Japanese |

The JA→EN mode is designed for use in overseas forums and chats —  
translate what others write, then type your reply in Japanese and paste the translation directly.

---

## OCR Correction Dictionary

You can manually correct words that OCR frequently misreads.  
The OCR result is printed to the console window — use that as a reference when filling in corrections.

**Format:** `misread=correct` (one entry per line, lines starting with `#` are comments)

```
# Common OCR correction examples
Clamage=Damage
Mp=HP
0rk=Ork
```

- 💾 Button: Save the current dictionary to a text file
- 📂 Button: Load a previously saved dictionary file

Saving a dictionary per game makes it easy to reuse. You can also share the text file with friends. **Not that I have any!**

---

## ⚙ Settings

| Option | Description |
|--------|-------------|
| Endpoint URL | LM Studio API address (default: `http://127.0.0.1:1234/v1`) |
| Model Name | Model to use (click "Fetch" to retrieve available models from LM Studio) |
| Translation Prompt | Additional instructions appended to the system prompt (e.g., proper nouns, tone) |
| Use Translation Prompt | Uncheck to ignore the prompt field entirely |
| Font / Font Size | Display font for translation results |
| Shortcut Keys | Global hotkey assignments (click ✕ to clear) |
| Background Opacity | Overlay transparency (10%–100%) |
| Color Mode | Auto-extract from image / manually specify background and text colors |

### Writing a Translation Prompt

Writing instructions in English tends to produce the most consistent results.

```
Do not translate proper nouns such as "Survivor". Keep them as is.
Use polite Japanese (丁寧語) for the translation.
```

---

## Borderless Mode

Forces a game window into borderless fullscreen — useful for games that only support exclusive fullscreen.

1. Click **"Borderless Mode"**
2. Within 3 seconds, click on the game window to make it active
3. The game window becomes borderless fullscreen
4. Click the button again to restore the window (restores to 1280×720)

> Requires `pip install pywin32`.

---

## Default Shortcuts

| Key | Action |
|-----|--------|
| `Ctrl+Shift+T` | Toggle translation ON/OFF |
| `Ctrl+Shift+A` | Add a new translation region |
| `Ctrl+Shift+R` | Retranslate all regions |
| `Ctrl+Shift+C` | Clear all regions |

Shortcuts can be changed in the settings dialog.

---

## Settings File Location

```
%APPDATA%\LocalLensTranslator\settings.json
```

---

## Notes

- In exclusive fullscreen (DirectX fullscreen) mode, overlays may appear behind the game. Set the game to **borderless windowed mode** for best results.
- Windows OCR requires `pip install winsdk`. If not installed, Tesseract is used as a fallback.
- Language packs for OCR must be downloaded separately via Windows Settings → Time & Language → Language & Region.
- If the LM Studio server is not running, translation will fail with an error.
- If the model reacts to colons (:), try adding `:=` to the OCR correction dictionary — it may help.

<img width="256" height="256" alt="Image" src="https://github.com/user-attachments/assets/c7c8d529-785f-4685-b616-f564716a538f" /><img width="256" height="256" alt="Image" src="https://github.com/user-attachments/assets/dea392aa-45f4-499e-aca3-e48794749787" /> Unused icons <img width="256" height="256" alt="Image" src="https://github.com/user-attachments/assets/f2c44906-3b3d-4dc3-878b-2ace06516d0a" />
