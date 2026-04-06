"""
LocalLensTranslator v2.62 
主な修正:
  - RegionSelector がスクリーン絶対座標を正しく返すよう修正
  - OCR が使えない場合に明確なエラーメッセージを表示
  - 翻訳エラーをオーバーレイに表示してデバッグしやすくする
  - オーバーレイは選択枠と同座標に重なる（タイトルバーなし）
"""

import os
import json
import threading
import tkinter as tk
from tkinter import filedialog
from tkinter import font as tkfont
import urllib.request
import urllib.error
import re

# ─────────────────────────────────────────────
# 設定
# ─────────────────────────────────────────────

SETTINGS_PATH = os.path.join(
    os.environ.get("APPDATA", os.path.expanduser("~")),
    "LocalLensTranslator", "settings.json"
)

DEFAULT_SETTINGS = {
    "lm_studio_url":       "http://127.0.0.1:1234/v1",
    "model":               "",
    "prompt_template":     "",
    "font_family":         "Yu Gothic UI",
    "font_size":           13,
    "shortcut_retranslate": "ctrl+shift+r",
    "shortcut_toggle":     "ctrl+shift+t",
    "shortcut_add_region": "ctrl+shift+a",
    "shortcut_clear":      "ctrl+shift+c",
    "overlay_alpha":       0.92,
    "use_image_colors":    True,
    "overlay_bg":          "#1A1A2E",
    "overlay_fg":          "#E8F4FD",
    "pixel_font_mode":     False,
    "drag_retranslate":    False,   # ドラッグ追従モード
    "source_lang":         "en",    # 翻訳元言語 (en / zh / ko / ja)
    "ocr_corrections":     "",      # OCR補正辞書 (例: Clamage=Damage\nMp=HP)
    "use_prompt":          True,       # 翻訳プロンプトを使用するか
}

# 翻訳元言語の定義
SOURCE_LANGS = [
    ("ja",   "日→英"),
    ("en",   "英語"),
    ("zh",   "中国語"),
    ("ko",   "韓国語"),
]

# 言語ごとのOCR設定
# Windows OCR の Language コード / Tesseract の lang パラメータ
LANG_OCR = {
    "ja":   {"winsdk": "ja",   "tesseract": "jpn"},
    "en":   {"winsdk": "en",   "tesseract": "eng"},
    "zh":   {"winsdk": "zh-Hans", "tesseract": "chi_sim+chi_tra"},
    "ko":   {"winsdk": "ko",   "tesseract": "kor"},
}

# 言語ごとのシステムプロンプト（system ロールで渡す）
# prompt_template（ユーザーのカスタム指示）とは完全に分離する
LANG_SYSTEM_PROMPT = {
    "ja":   "You are a professional Japanese (ja) to English (en) translator. Your goal is to accurately convey the meaning and nuances of the original Japanese text while adhering to English grammar, vocabulary, and cultural sensitivities. Produce only the English translation, without any additional explanations or commentary.",
    "en":   "You are a professional English (en) to Japanese (ja) translator. Your goal is to accurately convey the meaning and nuances of the original English text while adhering to Japanese grammar, vocabulary, and cultural sensitivities. Produce only the Japanese translation, without any additional explanations or commentary.",
    "zh":   "你是一位专业的中文 (zh) 到日文 (ja) 翻译员。你的目标是准确传达原文的意思和细微差别，同时遵循日文的语法、词汇和文化敏感性。请仅提供日文翻译，不要有任何额外的解释或评论。",
    "ko":   "당신은 전문적인 **한국어 (ko)**에서 **일본어 (ja)**로의 번역가입니다. 당신의 목표는 한국어 원문의 의미와 뉘앙스를 정확하게 전달하면서 일본어의 문법, 어휘 및 문화적 감수성을 준수하는 것입니다. 추가적인 설명이나 해설 없이 일본어 번역 결과만 출력해 주세요.",
}


def load_settings() -> dict:
    if os.path.exists(SETTINGS_PATH):
        try:
            with open(SETTINGS_PATH, encoding="utf-8") as f:
                return {**DEFAULT_SETTINGS, **json.load(f)}
        except Exception:
            pass
    return dict(DEFAULT_SETTINGS)


def save_settings(s: dict):
    os.makedirs(os.path.dirname(SETTINGS_PATH), exist_ok=True)
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=2)


# ─────────────────────────────────────────────
# 色抽出
# ─────────────────────────────────────────────

def extract_dominant_colors(image) -> tuple[str, str]:
    small = image.resize((80, 80)).convert("RGB")
    pixels = list(small.getdata())

    def lum(p):
        return 0.2126 * p[0] + 0.7152 * p[1] + 0.0722 * p[2]

    def saturation(p):
        r, g, b = p[0]/255, p[1]/255, p[2]/255
        return max(r, g, b) - min(r, g, b)

    # 輝度でソートして上位20%を文字候補、下位20%を背景候補として取る
    # （背景は大多数を占め、文字は少数の特徴的な色）
    luminances = [(lum(p), p) for p in pixels]
    luminances.sort(key=lambda x: x[0])

    n = len(luminances)
    dark_pixels  = [p for _, p in luminances[:n//5]]   # 暗い方20%
    bright_pixels = [p for _, p in luminances[n*4//5:]] # 明るい方20%

    def median_color(lst):
        if not lst:
            return (128, 128, 128)
        lst_s = sorted(lst, key=lambda p: p[0]*299 + p[1]*587 + p[2]*114)
        return lst_s[len(lst_s)//2]

    dark_color  = median_color(dark_pixels)
    bright_color = median_color(bright_pixels)

    # 輝度差が大きい方を fg/bg に割り当てる
    # 背景は広い面積 = 中間輝度帯に多い
    mid_pixels = [p for _, p in luminances[n//5:n*4//5]]
    def avg_color(lst):
        if not lst:
            return (128, 128, 128)
        return (sum(p[0] for p in lst)//len(lst),
                sum(p[1] for p in lst)//len(lst),
                sum(p[2] for p in lst)//len(lst))

    bg_rgb  = avg_color(mid_pixels) if mid_pixels else bright_color
    # 文字色は背景と輝度差が大きい方（暗いか明るいか）を選ぶ
    if abs(lum(dark_color) - lum(bg_rgb)) >= abs(lum(bright_color) - lum(bg_rgb)):
        fg_rgb = dark_color
    else:
        fg_rgb = bright_color

    # 彩度が低い場合のみコントラスト補正（色付き文字は補正しない）
    if abs(lum(bg_rgb) - lum(fg_rgb)) < 80 and saturation(fg_rgb) < 0.3:
        fg_rgb = (20, 20, 20) if lum(bg_rgb) > 128 else (235, 235, 235)
    # 文字色のコントラストを強調する
    # HSVに変換して明度(V)と彩度(S)を調整する
    import colorsys
    r, g, b = fg_rgb[0]/255, fg_rgb[1]/255, fg_rgb[2]/255
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    if lum(fg_rgb) < lum(bg_rgb):
        v = max(0.0, v * 0.8)
        s = min(1.0, s * 1.8)   # 彩度を上げて鮮やかにする
    else:
        v = min(1.0, v * 1.6)
        s = min(1.0, s * 1.4)   # 彩度を上げて鮮やかにする
    r2, g2, b2 = colorsys.hsv_to_rgb(h, s, v)
    fg_rgb = (int(r2 * 255), int(g2 * 255), int(b2 * 255))
                  
    return ("#{:02X}{:02X}{:02X}".format(*bg_rgb),
            "#{:02X}{:02X}{:02X}".format(*fg_rgb))


# ─────────────────────────────────────────────
# キャプチャ
# ─────────────────────────────────────────────

def capture_region(x1: int, y1: int, x2: int, y2: int):
    """
    スクリーン絶対座標で矩形をキャプチャして PIL.Image を返す。
    mss が必要: pip install mss Pillow
    """
    try:
        import mss
        from PIL import Image
    except ImportError as e:
        raise RuntimeError(f"pip install mss Pillow が必要です: {e}")

    with mss.mss() as sct:
        mon = {
            "top":    min(y1, y2),
            "left":   min(x1, x2),
            "width":  abs(x2 - x1),
            "height": abs(y2 - y1),
        }
        shot = sct.grab(mon)
        from PIL import Image
        return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")


# ─────────────────────────────────────────────
# OCR
# ─────────────────────────────────────────────

def ocr_image(image, settings) -> str:
    """
    OCRを実行してテキストを返す。
    settings["source_lang"] に応じて認識言語を切り替える。
    """
    from PIL import Image, ImageOps, ImageFilter

    lang = settings.get("source_lang", "en")
    ocr_cfg = LANG_OCR.get(lang, LANG_OCR["en"])

    def preprocess_for_ocr(img):
        img = img.convert("L")
        if settings.get("pixel_font_mode", False):
            w, h = img.size
            img = img.resize((w * 4, h * 4), Image.Resampling.NEAREST)
            img = ImageOps.autocontrast(img, cutoff=2)
            img = img.filter(ImageFilter.EDGE_ENHANCE_MORE)
            threshold = 128 
            img = img.point(lambda x: 0 if x < threshold else 255, '1')
        else:
            w, h = img.size
            img = img.resize((w * 2, h * 2), Image.Resampling.LANCZOS)
            img = ImageOps.autocontrast(img)
            img = img.filter(ImageFilter.UnsharpMask(radius=2, percent=150, threshold=3))
        return img

    image = preprocess_for_ocr(image)

    errors = []

    # --- Windows OCR (winsdk) ---
    try:
        winsdk_lang = ocr_cfg["winsdk"]
        result = _ocr_windows(image, winsdk_lang)
        if result and result.strip():
            result = _filter_ocr_by_lang(result, lang)
            return result
        errors.append("Windows OCR: テキストなし")
    except ImportError:
        errors.append("Windows OCR: winsdk 未インストール")
    except Exception as e:
        errors.append(f"Windows OCR: {e}")

    # --- Tesseract ---
    try:
        import pytesseract
        tess_lang = ocr_cfg["tesseract"]
        result = pytesseract.image_to_string(
            image, lang=tess_lang,
            config=r'--oem 3 --psm 6'
        )
        if result and result.strip():
            result = _filter_ocr_by_lang(result, lang)
            return result
        errors.append("Tesseract: テキストなし")
    except ImportError:
        errors.append("Tesseract: pytesseract 未インストール")
    except Exception as e:
        errors.append(f"Tesseract: {e}")

    raise RuntimeError(
        "OCR が使用できません。\n"
        "コマンドプロンプトで以下を実行してください:\n"
        "  pip install winsdk\n"
        "その後アプリを再起動してください。\n"
        f"({' / '.join(errors)})"
    )


def _ocr_windows(image, lang: str | None = None) -> str:
    """
    Windows Media OCR を使ってテキスト認識する。
    lang: None=ユーザープロファイル言語、"en"/"zh-Hans"/"ko" など
    """
    import asyncio
    import io
    from winsdk.windows.media.ocr import OcrEngine
    from winsdk.windows.graphics.imaging import (
        SoftwareBitmap, BitmapPixelFormat, BitmapAlphaMode, BitmapDecoder,
    )
    from winsdk.windows.storage.streams import InMemoryRandomAccessStream, DataWriter
    from winsdk.windows.globalization import Language

    buf = io.BytesIO()
    # Windows OCR は RGB が必要。グレースケール(L)等は変換する
    img_for_ocr = image.convert("RGB") if image.mode != "RGB" else image
    img_for_ocr.save(buf, format="PNG")
    png_bytes = buf.getvalue()

    async def _run():
        stream = InMemoryRandomAccessStream()
        writer = DataWriter(stream)
        writer.write_bytes(png_bytes)
        await writer.store_async()
        stream.seek(0)
        decoder = await BitmapDecoder.create_async(stream)
        bmp = await decoder.get_software_bitmap_async()
        bmp = SoftwareBitmap.convert(
            bmp, BitmapPixelFormat.BGRA8, BitmapAlphaMode.PREMULTIPLIED)

        if lang is None:
            # 自動: ユーザープロファイル言語を使用
            engine = OcrEngine.try_create_from_user_profile_languages()
            if engine is None:
                engine = OcrEngine.try_create_from_language(Language("en-US"))
        else:
            # 言語を明示指定
            engine = OcrEngine.try_create_from_language(Language(lang))
            if engine is None:
                # 指定言語が利用不可の場合はフォールバック
                engine = OcrEngine.try_create_from_user_profile_languages()
            if engine is None:
                engine = OcrEngine.try_create_from_language(Language("en-US"))

        result = await engine.recognize_async(bmp)
        return result.text

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    return loop.run_until_complete(_run())


# ─────────────────────────────────────────────
# LM Studio 翻訳
# ─────────────────────────────────────────────

def _apply_ocr_corrections(text: str, corrections_str: str) -> str:
    """
    OCR補正辞書を適用する。
    corrections_str の形式:
      誤認識語=正しい語 （1行1エントリ、# で始まる行はコメント）
    例:
      Clamage=Damage
      Mp=HP
      0rk=Ork
    """
    if not corrections_str.strip():
        return text
    for line in corrections_str.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        wrong, correct = line.split("=", 1)
        wrong, correct = wrong.strip(), correct.strip()
        if wrong:
            text = text.replace(wrong, correct)
    return text


def lm_translate(text: str, settings: dict, force_lang: str | None = None) -> str:
    base_url = settings["lm_studio_url"].rstrip("/")
    url = f"{base_url}/chat/completions"

    lang = force_lang if force_lang else settings.get("source_lang", "en")

    # system ロール: 言語指示（ユーザープロンプトとは完全に分離）
    system_msg = LANG_SYSTEM_PROMPT.get(lang, LANG_SYSTEM_PROMPT["en"])
    if settings.get("use_prompt", True):
        custom = settings.get("prompt_template", "").strip()
        if custom and custom != DEFAULT_SETTINGS["prompt_template"]:
            system_msg = system_msg + "\n\n" + custom
    # user ロール: テキストをそのまま渡す
    if lang in ("ja", "en", "zh", "ko"):
        user_content = text  # systemプロンプトで言語指示済み。テキストのみ渡す
    else:
        template = settings.get("prompt_template",
                            "以下のテキストを翻訳してください。\n\n{text}")
        user_content = template.replace("{text}", text)

    payload = {
        "messages": [
            {"role": "system", "content": system_msg},
            {"role": "user",   "content": user_content},
        ],
        "temperature": 0.2,
        "max_tokens": 2048,
        "stream": False,
    }
    model = settings.get("model", "").strip()
    if model:
        payload["model"] = model

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            result = body["choices"][0]["message"]["content"].strip()
            if lang == "en":
                result = re.sub(r'[\uAC00-\uD7AF\u1100-\u11FF]+', '', result)
                result = re.sub(r'\n{2,}', '\n', result).strip()
            return result
    except urllib.error.URLError as e:
        raise RuntimeError(
            f"LM Studio に接続できません。\n"
            f"URL: {url}\n"
            f"LM Studio を起動してローカルサーバーをONにしてください。\n"
            f"詳細: {e}"
        )
    except (KeyError, IndexError) as e:
        raise RuntimeError(f"LM Studio のレスポンス形式が予期しない形式です: {e}")


def fetch_lm_models(base_url: str) -> list[str]:
    url = base_url.rstrip("/") + "/models"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            return [m["id"] for m in body.get("data", [])]
    except Exception:
        return []


# ─────────────────────────────────────────────
# ショートカット
# ─────────────────────────────────────────────

_MOD_MAP   = {"ctrl": "Control", "shift": "Shift", "alt": "Alt", "win": "super"}
_MOD_ORDER = ["Control", "Alt", "Shift"]


def shortcut_to_bind(sc: str) -> str:
    parts = [p.strip().lower() for p in sc.split("+")]
    mods, key = set(), ""
    for p in parts:
        if p in _MOD_MAP:
            mods.add(_MOD_MAP[p])
        else:
            key = p
    prefix = "-".join(m for m in _MOD_ORDER if m in mods)
    return f"<{prefix}-{key}>" if prefix else f"<{key}>"


def event_to_shortcut(event) -> str:
    mods = []
    if event.state & 0x0004:  mods.append("ctrl")
    if event.state & 0x0001:  mods.append("shift")
    if event.state & 0x20000: mods.append("alt")
    ks = event.keysym
    if ks in ("Control_L","Control_R","Shift_L","Shift_R","Alt_L","Alt_R"):
        return ""
    key = ks.lower() if len(ks) == 1 else ks
    return "+".join(mods + [key]) if (mods or key) else ""


# ─────────────────────────────────────────────
# ShortcutEntry
# ─────────────────────────────────────────────

class ShortcutEntry(tk.Frame):
    def __init__(self, parent, var: tk.StringVar, **kw):
        super().__init__(parent, bg="#161B22", **kw)
        self._var = var
        self._listening = False
        
        self._disp = tk.Label(
            self, textvariable=var,
            bg="#21262D", fg="#C9D1D9",
            font=("Consolas", 10), width=22, anchor="w",
            padx=8, pady=4, cursor="hand2", relief="flat",
        )
        self._disp.pack(side="left")
        
        self._hint = tk.Label(self, text="クリックして入力",
                              bg="#161B22", fg="#6E7681",
                              font=("Yu Gothic UI", 8))
        self._hint.pack(side="left", padx=(6, 0))

        # --- 【追加】解除（クリア）ボタン ---
        self._clear_btn = tk.Button(
            self, text="✕", command=self._clear_shortcut,
            bg="#161B22", fg="#8B949E", relief="flat",
            font=("Yu Gothic UI", 9, "bold"), cursor="hand2",
            activebackground="#161B22", activeforeground="#F85149", # ホバー時は赤くする
            bd=0, padx=4, pady=0
        )
        self._clear_btn.pack(side="left", padx=(2, 0))
        # ------------------------------------

        self._disp.bind("<Button-1>", self._start)
        self._disp.bind("<KeyPress>",  self._on_key)
        self._disp.bind("<FocusOut>",  self._stop)
        self._disp.configure(takefocus=1)

    def _start(self, _=None):
        self._listening = True
        self._disp.configure(bg="#1F3A5F", fg="#58A6FF")
        self._hint.configure(text="キーを押してください...")
        self._disp.focus_set()

    def _stop(self, _=None):
        self._listening = False
        self._disp.configure(bg="#21262D", fg="#C9D1D9")
        self._hint.configure(text="クリックして入力")

    def _on_key(self, event):
        if not self._listening:
            return
        
        # BackSpaceやDeleteキーが押された場合もクリアする（おまけの便利機能）
        if event.keysym in ("BackSpace", "Delete", "Escape"):
            self._clear_shortcut()
            return

        sc = event_to_shortcut(event)
        if sc:
            self._var.set(sc)
            self._stop()

    # --- 【追加】ショートカットを空にするメソッド ---
    def _clear_shortcut(self):
        self._var.set("")
        self._stop()


# ─────────────────────────────────────────────
# 翻訳オーバーレイ（タイトルバーなし・選択枠と同位置）
# ─────────────────────────────────────────────
def _filter_ocr_by_lang(text: str, lang: str) -> str:
    """
    OCR結果から指定言語以外の文字を除去する。
    英語モード時に韓国語・日本語・中国語が混入するのを防ぐ。
    """
    if lang == "en":
        # 英語: ASCII文字・記号・空白・改行のみ残す
        text = re.sub(r'[^\x00-\x7F\n]+', '', text)
    elif lang == "ja":
        # 日本語: ひらがな・カタカナ・漢字・ASCII を残す
        text = re.sub(r'[^\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF\x00-\x7F\n]+', '', text)
    elif lang == "zh":
        # 中国語: 漢字・ASCII を残す
        text = re.sub(r'[^\u4E00-\u9FFF\u3400-\u4DBF\x00-\x7F\n]+', '', text)
    elif lang == "ko":
        # 韓国語: ハングル・ASCII を残す
        text = re.sub(r'[^\uAC00-\uD7AF\u1100-\u11FF\x00-\x7F\n]+', '', text)
    return text.strip()

def _fix_ocr_errors(text: str, lang: str = "en") -> str:
    """
    OCR の誤認識を後処理で補正する。
    主な誤認識パターン:
      - 半角英数の前後に全角括弧が混入 (r → 「、l → 」など)
      - 行末・行頭の不要な記号
    """
    # 全角の「」が半角英数字に隣接している場合は除去
    # 例: 「r」→ r、「hello」→ hello
    text = re.sub(r'「([a-zA-Z0-9])」', r'\1', text)
    text = re.sub(r'「([a-zA-Z0-9])', r'\1', text)
    text = re.sub(r'([a-zA-Z0-9])」', r'\1', text)
    # 単独の「や」が英数字に隣接している場合も除去
    text = re.sub(r'(?<=[a-zA-Z0-9])「', '', text)
    text = re.sub(r'」(?=[a-zA-Z0-9])', '', text)
    # ・(中黒)の誤認識補正
    if lang == "ja":
        text = re.sub(r'(?<=[^\n])\. (?=[^\n])', '・', text)
        text = re.sub(r'·', '・', text)
    # 日本語文字間の余分なスペースを除去
    # 例: "発 生 し て い る" → "発生している"
    text = re.sub(r'(?<=[\u3040-\u9FFF])\s+(?=[\u3040-\u9FFF])', '', text)
    # 日本語と日本語の間にある半角スペース1つも除去（句読点含む）
    text = re.sub(r'(?<=[\u3000-\u9FFF])\s(?=[\u3000-\u9FFF])', '', text)
    return text


def _insert_linebreaks(text: str) -> str:
    """
    句点（。！？）の後に改行を挿入して読みやすくする。
    既に改行がある場合はそのまま保持する。
    """
    # ・ や - の直後の改行を除去
    text = re.sub(r'(・|-)\n', r'\1', text)
    # ・ や - で始まる行の直後の改行を除去して1行にまとめる
    text = re.sub(r'(^|\n)(・|-) *([^\n]+)\n(?=[^\n・-])', r'\1\2\3 ', text)
    # 句点（。）の後にのみ改行を挿入（！？は対象外）
    result = re.sub(r'(。)(?!\n)', r'\1\n', text)
    # 連続する改行は1つにまとめる
    result = re.sub(r'\n{2,}', '\n', result)
    return result.strip()


class RegionOverlay(tk.Toplevel):
    """
    選択した矩形に重なるオーバーレイウィンドウ。
    右クリック: メニュー表示
    右クリック+ドラッグ: 移動
    """

    def __init__(self, parent, region_id: int,
                 x1: int, y1: int, x2: int, y2: int,
                 settings: dict, on_remove, on_retranslate, on_ja_to_en=None, on_sync_drag=None):
        super().__init__(parent)
        self._id             = region_id
        self._x1             = min(x1, x2)
        self._y1             = min(y1, y2)
        self._x2             = max(x1, x2)
        self._y2             = max(y1, y2)
        self.win_w           = self._x2 - self._x1
        self.win_h           = self._y2 - self._y1
        self._settings       = settings
        self._on_remove      = on_remove
        self._on_retranslate = on_retranslate
        self._on_ja_to_en    = on_ja_to_en
        self._enabled        = True
        self._text           = ""
        self._drag_start     = None   # 右クリックドラッグ用
        self._on_sync_drag   = on_sync_drag

        # 色は __init__ 時点で確定させる（初回から正しく反映するため）
        self._bg = settings.get("overlay_bg", "#1A1A2E")
        self._fg = settings.get("overlay_fg", "#E8F4FD")
        
        self._drag_retrans_var = tk.BooleanVar(
            value=self._settings.get("drag_retranslate", False))        

        # ── ウィンドウを1回だけ確実に構築 ──────────────────────────
        # withdraw → overrideredirect → geometry → deiconify の順が重要
        # overrideredirect を先に呼ぶと Windows でちらつくため
        # 一旦 withdraw した状態で設定してから deiconify する
        self.withdraw()
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(bg=self._bg)
        self.geometry(f"{self.win_w}x{self.win_h}+{self._x1}+{self._y1}")

        # Canvas（背景色を __init__ 時点の _bg で初期化）
        self._canvas = tk.Canvas(
            self,
            width=self.win_w, height=self.win_h,
            highlightthickness=0, borderwidth=0,
            bg=self._bg,
        )
        self._canvas.pack(fill="both", expand=True)

        # 右クリックメニュー
        self._menu = tk.Menu(
            self, tearoff=0,
            bg="#21262D", fg="#C9D1D9", activebackground="#1F6FEB",
        )
        self._menu.add_command(label="再翻訳",
                               command=lambda: self._on_retranslate(self._id))
        self._menu.add_command(label="日→英翻訳してコピー",
                               command=self._do_ja_to_en)
        self._menu.add_separator()
        self._menu.add_checkbutton(label="移動再翻訳",
                               variable=self._drag_retrans_var,
                               command=self._toggle_drag_retranslate)
        self._menu.add_separator()
        self._menu.add_command(label="この枠を削除", command=self._remove)
        self._menu.add_command(label="ON/OFF 切り替え", command=self.toggle_active)

        # 右クリック: メニュー表示 / ドラッグ: 移動
        for w in (self, self._canvas):
            w.bind("<ButtonPress-3>",   self._on_rclick_press)
            w.bind("<B3-Motion>",        self._on_rclick_drag)
            w.bind("<ButtonRelease-3>",  self._on_rclick_release)

        # 透明度を設定して表示
        self.attributes("-alpha", float(settings.get("overlay_alpha", 0.92)))
        self.deiconify()

    # ── 描画 ──────────────────────────────────────────────────

    def _redraw(self):
        if not hasattr(self, "_canvas"):
            return
        c = self._canvas
        c.delete("all")
        c.configure(bg=self._bg)
        if not self._text:
            return

        ff      = self._settings.get("font_family", "Yu Gothic UI")
        base_fs = int(self._settings.get("font_size", 13))
        wrap_w  = max(10, self.win_w - 8)

        # まず base_fs で描画して高さを確認し、
        # 余裕があれば拡大、はみ出していれば縮小する
        fs = base_fs
        tid = None

        # 1回目の描画で余裕を確認して拡大を試みる
        if tid is not None:
            c.delete(tid)
        tid = c.create_text(
            4, 4, text=self._text, fill=self._fg,
            font=(ff, fs), anchor="nw", width=wrap_w,
        )
        c.update_idletasks()
        bbox = c.bbox(tid)
        if bbox is not None:
            text_h = bbox[3] - bbox[1]
            avail_h = self.win_h - 8
            if text_h < avail_h * 0.6:
                # 余裕が40%以上あれば拡大を試みる（上限は base_fs * 3）
                ratio = (avail_h / text_h) ** 0.5
                fs = min(int(base_fs * 3), int(fs * ratio))
                c.delete(tid)
                tid = None

        for _ in range(10):
            if tid is not None:
                c.delete(tid)
            tid = c.create_text(
                4, 4,
                text=self._text,
                fill=self._fg,
                font=(ff, fs),
                anchor="nw",
                width=wrap_w,
            )
            c.update_idletasks()
            bbox = c.bbox(tid)
            if bbox is None:
                break
            # 実際の描画高さが枠に収まっていれば終了
            if bbox[3] - bbox[1] <= self.win_h - 8:
                break
            # はみ出している場合はフォントを縮小して再試行
            new_fs = max(8, int(fs * 0.85))
            if new_fs == fs:
                break  # これ以上縮小できない
            fs = new_fs

        # テキスト範囲だけ背景矩形を重ねる
        c.update_idletasks()
        bbox = c.bbox(tid)
        if bbox:
            c.create_rectangle(
                bbox[0] - 2, bbox[1] - 2,
                bbox[2] + 2, bbox[3] + 2,
                fill=self._bg, outline="", tags="bg_rect",
            )
            c.tag_lower("bg_rect", tid)

    # ── 公開 API ──────────────────────────────────────────────

    def set_text(self, text: str):
        """翻訳結果テキストをセットして再描画する。"""
        # 句点で改行を挿入して読みやすくする
        formatted = _insert_linebreaks(text)
        self._text = formatted
        self._redraw()

    def set_status(self, msg: str):
        """処理中などの一時ステータスを表示する。"""
        c = self._canvas
        c.delete("all")
        c.configure(bg=self._bg)
        fs = max(8, int(self._settings.get("font_size", 13)) - 2)
        tid = c.create_text(
            4, 4, text=msg,
            fill="#8B949E",
            font=("Yu Gothic UI", fs),
            anchor="nw",
            width=self.win_w - 8,
        )
        c.update_idletasks()
        bbox = c.bbox(tid)
        if bbox:
            c.create_rectangle(
                bbox[0] - 2, bbox[1] - 2,
                bbox[2] + 2, bbox[3] + 2,
                fill=self._bg, outline="", tags="bg_rect",
            )
            c.tag_lower("bg_rect", tid)

    def apply_image_colors(self, bg: str, fg: str):
        """
        画像から抽出した色をセットする。
        _redraw は呼ばない — set_text から呼ばれるときに反映される。
        """
        self._bg = bg
        self._fg = fg
        try:
            self.configure(bg=bg)
            self._canvas.configure(bg=bg)
        except tk.TclError:
            pass

    def apply_alpha(self, alpha: float):
        try:
            self.attributes("-alpha", alpha)
        except tk.TclError:
            pass

    def update_font(self, _family: str, _size: int):
        self._redraw()

    def toggle_active(self):
        self._enabled = not self._enabled
        if self._enabled:
            self.deiconify()
            self._on_retranslate(self._id)
        else:
            self.withdraw()

    def set_enabled(self, enabled: bool):
        self._enabled = enabled
        try:
            if not enabled:
                self.withdraw()
            else:
                self.deiconify()
        except tk.TclError:
            pass

    @property
    def is_active(self) -> bool:
        return self._enabled

    @property
    def region(self) -> tuple[int, int, int, int]:
        return self._x1, self._y1, self._x2, self._y2
        
    def _do_ja_to_en(self):
        """右クリック「日→英翻訳してコピー」"""
        if self._on_ja_to_en:
            self._on_ja_to_en(self._id)
            
    def _toggle_drag_retranslate(self):
        val = self._drag_retrans_var.get()
        self._settings["drag_retranslate"] = val
        # メインUIのチェックボックスを同期
        if self._on_sync_drag is not None:
            self._on_sync_drag(val)         
        
    def _remove(self):
        self._on_remove(self._id)
        self.destroy()

# ── 右クリックドラッグ（移動）──────────────────────────────
    def _on_rclick_press(self, event):
        self._drag_start = (event.x_root, event.y_root,
                            self.winfo_x(), self.winfo_y())
        self._drag_moved = False
        self._drag_end_time = 0

    def _on_rclick_drag(self, event):
        if self._drag_start is None:
            return
        sx, sy, wx, wy = self._drag_start
        dx = event.x_root - sx
        dy = event.y_root - sy
        if abs(dx) > 3 or abs(dy) > 3:
            self._drag_moved = True
        if self._drag_moved:
            self.geometry(f"+{wx + dx}+{wy + dy}")

    def _on_rclick_release(self, event):
        import time
        moved = getattr(self, "_drag_moved", False)
        self._drag_start = None
        self._drag_moved = False
        if moved:
            self._drag_end_time = time.time()
            if self._settings.get("drag_retranslate", False):
                new_x = self.winfo_x()
                new_y = self.winfo_y()
                w = self._x2 - self._x1
                h = self._y2 - self._y1
                self._x1 = new_x
                self._y1 = new_y
                self._x2 = new_x + w
                self._y2 = new_y + h
                self._on_retranslate(self._id)
        else:
            if time.time() - getattr(self, "_drag_end_time", 0) > 0.3:
                self._menu.post(event.x_root, event.y_root)

# ─────────────────────────────────────────────
# 領域選択（フルスクリーン透過）
# ─────────────────────────────────────────────

class RegionSelector(tk.Toplevel):
    """
    半透明フルスクリーンでマウスドラッグ選択する。
    コールバックにはスクリーン絶対座標 (x1, y1, x2, y2) を渡す。
    ポイント: event.x_root / event.y_root でスクリーン絶対座標を取得する。
    """

    def __init__(self, parent, callback):
        super().__init__(parent)
        self._callback = callback
        self._start    = None   # スクリーン絶対座標
        self._rect_id  = None

        self.attributes("-topmost", True)
        self.overrideredirect(True)
        self.configure(bg="#000000", cursor="crosshair")

        # マルチディスプレイ対応: 全モニターをカバーする仮想デスクトップ全体に広げる
        try:
            import ctypes
            SM_XVIRTUALSCREEN  = 76
            SM_YVIRTUALSCREEN  = 77
            SM_CXVIRTUALSCREEN = 78
            SM_CYVIRTUALSCREEN = 79
            vx = ctypes.windll.user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
            vy = ctypes.windll.user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
            vw = ctypes.windll.user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
            vh = ctypes.windll.user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
        except Exception:
            vx, vy = 0, 0
            vw = self.winfo_screenwidth()
            vh = self.winfo_screenheight()

        self._vx = vx
        self._vy = vy
        self.geometry(f"{vw}x{vh}+{vx}+{vy}")
        self.attributes("-alpha", 0.25)

        self._canvas = tk.Canvas(self, bg="#000000", highlightthickness=0,
                                 width=vw, height=vh)
        self._canvas.pack(fill="both", expand=True)

        cx = vw // 2
        self._canvas.create_text(
            cx, 36,
            text="ドラッグして翻訳範囲を選択  /  Esc でキャンセル",
            fill="#58A6FF", font=("Yu Gothic UI", 13),
        )

        # ButtonPress / Motion / Release すべて x_root/y_root を使う
        self._canvas.bind("<ButtonPress-1>",   self._on_press)
        self._canvas.bind("<B1-Motion>",        self._on_drag)
        self._canvas.bind("<ButtonRelease-1>",  self._on_release)
        self.bind("<Escape>", lambda e: self.destroy())

    def _on_press(self, e):
        # e.x_root, e.y_root = スクリーン絶対座標
        self._start = (e.x_root, e.y_root)
        if self._rect_id:
            self._canvas.delete(self._rect_id)

    def _on_drag(self, e):
        if not self._start:
            return
        if self._rect_id:
            self._canvas.delete(self._rect_id)

        # sx, sy (開始地点) から仮想座標のオフセットを引く
        sx = self._start[0] - self._vx
        sy = self._start[1] - self._vy
        
        # e.x_root (現在のマウス) から仮想座標のオフセットを引く
        cur_x = e.x_root - self._vx
        cur_y = e.y_root - self._vy

        self._rect_id = self._canvas.create_rectangle(
            sx, sy, cur_x, cur_y, # ← ここを修正した座標に変更
            outline="#58A6FF", width=2, dash=(5, 3), fill="",
        )

    def _on_release(self, e):
        if not self._start:
            return
        x1, y1 = self._start
        x2, y2 = e.x_root, e.y_root

        # セレクターを隠す
        self.withdraw()
        
        # 300ms 待ってからオーバーレイを作成（干渉防止）
        if abs(x2 - x1) > 10 and abs(y2 - y1) > 10:
            self.master.after(300, lambda: self._callback(x1, y1, x2, y2))
        
        # 自身の破棄
        self.after(400, self.destroy)

# ─────────────────────────────────────────────
# 設定ダイアログ
# ─────────────────────────────────────────────

class SettingsDialog(tk.Toplevel):

    def __init__(self, parent, settings: dict, on_save):
        super().__init__(parent)
        self.title("設定")
        self.resizable(False, True)
        self.grab_set()
        self.configure(bg="#0D1117")
        self._settings = dict(settings)
        self._on_save  = on_save
        self._vars: dict[str, tk.Variable] = {}
        self._build_ui()

    def _sv(self, key):
        v = tk.StringVar(value=str(
            self._settings.get(key, DEFAULT_SETTINGS.get(key, ""))))
        self._vars[key] = v
        return v

    def _section(self, text):
        f = tk.Frame(self, bg="#161B22", pady=3)
        f.pack(fill="x", padx=12, pady=(12, 0))
        tk.Label(f, text=text, bg="#161B22", fg="#58A6FF",
                 font=("Consolas", 10, "bold")).pack(side="left", padx=8)

    def _card(self):
        f = tk.Frame(self, bg="#161B22", pady=5)
        f.pack(fill="x", padx=12, pady=2)
        f.columnconfigure(1, weight=1)
        return f

    def _lbl(self, p, text, row=0):
        tk.Label(p, text=text, bg="#161B22", fg="#8B949E",
                 font=("Yu Gothic UI", 10), anchor="w"
                 ).grid(row=row, column=0, sticky="w", padx=(12,6), pady=4)

    def _ent(self, p, var, width=36, row=0):
        e = tk.Entry(p, textvariable=var,
                     bg="#21262D", fg="#C9D1D9",
                     insertbackground="#C9D1D9",
                     relief="flat", font=("Yu Gothic UI", 10), width=width)
        e.grid(row=row, column=1, sticky="ew", padx=(0,12), pady=4)
        return e

    def _build_ui(self):
        s = self._settings

        # ── LM Studio ──
        self._section("LM Studio 接続設定")
        c = self._card()
        self._lbl(c, "エンドポイントURL")
        self._ent(c, self._sv("lm_studio_url"))

        c = self._card()
        self._lbl(c, "モデル名")
        mv = self._sv("model")
        self._ent(c, mv, width=28)
        tk.Button(c, text="取得", bg="#21262D", fg="#58A6FF",
                  relief="flat", font=("Yu Gothic UI", 9), cursor="hand2",
                  command=lambda: self._fetch_models(mv)
                  ).grid(row=0, column=2, padx=(0,12), pady=4)

        # ── プロンプト ──
        self._section("翻訳プロンプト")
        c = self._card()
        self._prompt = tk.Text(
            c, bg="#21262D", fg="#C9D1D9",
            insertbackground="#C9D1D9",
            relief="flat", font=("Yu Gothic UI", 10),
            width=46, height=7, wrap="word",
        )
        self._prompt.insert("1.0", s.get("prompt_template",
                                          DEFAULT_SETTINGS["prompt_template"]))
        self._prompt.grid(row=0, column=0, columnspan=3,
                          padx=12, pady=4, sticky="ew")
        tk.Label(c, text="system プロンプトの末尾に追加されます",
                 bg="#161B22", fg="#6E7681", font=("Yu Gothic UI", 9)
                 ).grid(row=1, column=0, columnspan=3, sticky="w", padx=12)
        use_prompt_v = tk.BooleanVar(value=bool(s.get("use_prompt", True)))
        self._vars["use_prompt"] = use_prompt_v
        tk.Checkbutton(c, text="翻訳プロンプトを使用する",
                       variable=use_prompt_v,
                       bg="#161B22", fg="#C9D1D9", selectcolor="#21262D",
                       activebackground="#161B22", activeforeground="#58A6FF",
                       font=("Yu Gothic UI", 10)
                       ).grid(row=2, column=0, columnspan=3, sticky="w", padx=12, pady=(0,4))
        c.columnconfigure(0, weight=1)

        # ── フォント ──
        self._section("表示フォント")
        c = self._card()
        self._lbl(c, "フォント")
        fv = self._sv("font_family")
        inner = tk.Frame(c, bg="#161B22")
        inner.grid(row=0, column=1, columnspan=2, sticky="ew",
                   padx=(0,12), pady=4)
        inner.columnconfigure(0, weight=1)
        fe = tk.Entry(inner, textvariable=fv,
                      bg="#21262D", fg="#C9D1D9",
                      insertbackground="#C9D1D9",
                      relief="flat", font=("Yu Gothic UI", 10), width=26)
        fe.grid(row=0, column=0, sticky="ew")
        tk.Button(inner, text="選択", bg="#21262D", fg="#58A6FF",
                  relief="flat", font=("Yu Gothic UI", 9), cursor="hand2",
                  command=lambda: self._pick_font(fv)
                  ).grid(row=0, column=1, padx=(4,0))
        self._font_preview = tk.Label(
            c, text="あいうえお ABC 123",
            bg="#161B22", fg="#C9D1D9", font=(fv.get(), 12))
        self._font_preview.grid(row=1, column=0, columnspan=3,
                                 sticky="w", padx=12, pady=(0,4))
        fv.trace_add("write", lambda *_: self._preview_font(fv))

        c2 = self._card()
        self._lbl(c2, "フォントサイズ")
        self._ent(c2, self._sv("font_size"), width=6)

        # ── ショートカット ──
        self._section("ショートカットキー")
        for key, label in [
            ("shortcut_toggle",      "翻訳 ON/OFF"),
            ("shortcut_add_region",  "翻訳枠を追加"),
            ("shortcut_clear",       "全枠をクリア"),
            ("shortcut_retranslate", "全枠を再翻訳"),
        ]:
            c = self._card()
            self._lbl(c, label)
            ShortcutEntry(c, self._sv(key)).grid(
                row=0, column=1, columnspan=2,
                sticky="w", padx=(0,12), pady=4)

        # ── オーバーレイ ──
        self._section("オーバーレイ表示")
        c = self._card()
        self._lbl(c, "背景の透明度")
        alpha_val = tk.DoubleVar(value=float(s.get("overlay_alpha", 0.92)))
        self._vars["overlay_alpha"] = alpha_val
        inner = tk.Frame(c, bg="#161B22")
        inner.grid(row=0, column=1, columnspan=2, sticky="ew",
                   padx=(0,12), pady=4)
        pct = tk.Label(inner, text=f"{int(alpha_val.get()*100)}%",
                       bg="#161B22", fg="#C9D1D9",
                       font=("Consolas", 10), width=5)
        pct.pack(side="right")
        tk.Scale(inner, from_=0.10, to=1.00, resolution=0.01,
                 orient="horizontal", variable=alpha_val,
                 command=lambda v: pct.configure(text=f"{int(float(v)*100)}%"),
                 bg="#161B22", fg="#C9D1D9", troughcolor="#21262D",
                 activebackground="#58A6FF",
                 highlightthickness=0, length=220, showvalue=False,
                 ).pack(side="left", fill="x", expand=True)

        c = self._card()
        self._lbl(c, "色の取得方法")
        uiv = tk.BooleanVar(value=bool(s.get("use_image_colors", True)))
        self._vars["use_image_colors"] = uiv
        inner = tk.Frame(c, bg="#161B22")
        inner.grid(row=0, column=1, columnspan=2, sticky="w",
                   padx=(0,12), pady=4)
        for val, txt in [(True,"画像から自動抽出"), (False,"手動で指定")]:
            tk.Radiobutton(inner, text=txt, variable=uiv, value=val,
                           bg="#161B22", fg="#C9D1D9", selectcolor="#21262D",
                           font=("Yu Gothic UI", 10),
                           command=lambda: self._toggle_colors(not uiv.get()),
                           ).pack(anchor="w")

        self._color_card = self._card()
        self._lbl(self._color_card, "背景色 (Hex)")
        self._ent(self._color_card, self._sv("overlay_bg"), width=10)
        self._lbl(self._color_card, "文字色 (Hex)", row=1)
        self._ent(self._color_card, self._sv("overlay_fg"), width=10, row=1)
        self._toggle_colors(not uiv.get())

        # ── ボタン ──
        bf = tk.Frame(self, bg="#0D1117")
        bf.pack(fill="x", padx=12, pady=12)
        tk.Button(bf, text="接続テスト",
                  bg="#21262D", fg="#58A6FF",
                  relief="flat", font=("Yu Gothic UI", 10),
                  cursor="hand2", padx=10, pady=5,
                  command=self._test_conn).pack(side="left")
        tk.Button(bf, text="保存",
                  bg="#238636", fg="#FFFFFF",
                  relief="flat", font=("Yu Gothic UI", 10, "bold"),
                  cursor="hand2", padx=16, pady=5,
                  command=self._save).pack(side="right", padx=(6,0))
        tk.Button(bf, text="キャンセル",
                  bg="#21262D", fg="#8B949E",
                  relief="flat", font=("Yu Gothic UI", 10),
                  cursor="hand2", padx=10, pady=5,
                  command=self.destroy).pack(side="right")

    def _toggle_colors(self, enabled: bool):
        st = "normal" if enabled else "disabled"
        for ch in self._color_card.winfo_children():
            try: ch.configure(state=st)
            except Exception: pass

    def _preview_font(self, fv):
        try: self._font_preview.configure(font=(fv.get(), 12))
        except Exception: pass

    def _pick_font(self, fv: tk.StringVar):
        popup = tk.Toplevel(self)
        popup.title("フォントを選択")
        popup.configure(bg="#161B22")
        popup.grab_set()
        popup.geometry("340x480")
        sv = tk.StringVar()
        tk.Entry(popup, textvariable=sv,
                 bg="#21262D", fg="#C9D1D9",
                 insertbackground="#C9D1D9",
                 relief="flat", font=("Yu Gothic UI", 10)
                 ).pack(fill="x", padx=8, pady=(8,2))
        tk.Label(popup, text="フォント名で絞り込み",
                 bg="#161B22", fg="#6E7681",
                 font=("Yu Gothic UI", 8)).pack(anchor="w", padx=10)
        lf = tk.Frame(popup, bg="#161B22")
        lf.pack(fill="both", expand=True, padx=8, pady=4)
        sb = tk.Scrollbar(lf, bg="#21262D")
        sb.pack(side="right", fill="y")
        lb = tk.Listbox(lf, bg="#21262D", fg="#C9D1D9",
                        selectbackground="#1F6FEB",
                        relief="flat", font=("Yu Gothic UI", 10),
                        yscrollcommand=sb.set, activestyle="none")
        lb.pack(side="left", fill="both", expand=True)
        sb.configure(command=lb.yview)
        all_fonts = sorted(tkfont.families())
        prev = tk.Label(popup, text="あいうえお ABC 123",
                        bg="#0D1117", fg="#C9D1D9",
                        font=(fv.get(), 13), pady=6)
        prev.pack(fill="x", padx=8)

        def refresh(*_):
            q = sv.get().lower()
            lb.delete(0, "end")
            for f in all_fonts:
                if q in f.lower():
                    lb.insert("end", f)
            cur = fv.get()
            items = list(lb.get(0, "end"))
            if cur in items:
                idx = items.index(cur)
                lb.selection_set(idx)
                lb.see(idx)

        sv.trace_add("write", refresh)
        refresh()
        lb.bind("<<ListboxSelect>>", lambda _: [
            sel := lb.curselection(),
            prev.configure(font=(lb.get(sel[0]), 13)) if sel else None
        ])

        def confirm():
            sel = lb.curselection()
            if sel:
                fv.set(lb.get(sel[0]))
            popup.destroy()

        lb.bind("<Double-Button-1>", lambda _: confirm())
        tk.Button(popup, text="このフォントを使用",
                  bg="#238636", fg="white",
                  relief="flat", font=("Yu Gothic UI", 10),
                  cursor="hand2", pady=5,
                  command=confirm).pack(fill="x", padx=8, pady=(4,8))

    def _fetch_models(self, var: tk.StringVar):
        import tkinter.messagebox as mb
        url = self._vars["lm_studio_url"].get()
        models = fetch_lm_models(url)
        if not models:
            mb.showwarning("取得失敗",
                           "LM Studio に接続できないかモデルがありません", parent=self)
            return
        popup = tk.Toplevel(self)
        popup.title("モデルを選択")
        popup.configure(bg="#161B22")
        popup.grab_set()
        lb = tk.Listbox(popup, bg="#21262D", fg="#C9D1D9",
                        selectbackground="#1F6FEB",
                        relief="flat", font=("Yu Gothic UI", 10),
                        width=52, height=min(len(models), 10))
        lb.pack(padx=8, pady=8)
        for m in models:
            lb.insert("end", m)
        def select():
            sel = lb.curselection()
            if sel: var.set(models[sel[0]])
            popup.destroy()
        lb.bind("<Double-Button-1>", lambda _: select())
        tk.Button(popup, text="選択", bg="#238636", fg="white",
                  relief="flat", font=("Yu Gothic UI", 10),
                  command=select).pack(pady=(0,8))

    def _test_conn(self):
        import tkinter.messagebox as mb
        url = self._vars["lm_studio_url"].get()
        models = fetch_lm_models(url)
        if models:
            mb.showinfo("接続テスト", f"✅ 接続成功\nモデル数: {len(models)}", parent=self)
        else:
            mb.showerror("接続失敗",
                         f"❌ {url} に接続できません\n"
                         "LM Studio を起動してローカルサーバーをONにしてください",
                         parent=self)

    def _save(self):
        new_s = dict(self._settings)
        for key, var in self._vars.items():
            val = var.get()
            if key == "font_size":
                try:    val = int(val)
                except: val = 13
            elif key == "overlay_alpha":
                try:    val = round(float(val), 2)
                except: val = 0.92
            elif key == "use_image_colors":
                val = bool(val)
            new_s[key] = val
        new_s["prompt_template"] = self._prompt.get("1.0", "end-1c")
        self._on_save(new_s)
        self.destroy()


# ─────────────────────────────────────────────
# メインアプリ
# ─────────────────────────────────────────────

class LocalLensTranslatorApp(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title("LocalLensTranslator")
        self.resizable(False, False)
        self.configure(bg="#0D1117")
        self._settings = load_settings()
        self._regions: dict[int, RegionOverlay] = {}
        self._next_id  = 1
        self._trans_on = True
        self._build_ui()
        self._bind_shortcuts()

    def _build_ui(self):
        # ── 1段目（タイトル ＋ 状態表示） ──
        hdr = tk.Frame(self, bg="#161B22", pady=8)
        hdr.pack(fill="x")
        tk.Label(hdr, text="LocalLensTranslator",
                 bg="#161B22", fg="#58A6FF",
                 font=("Consolas", 14, "bold")).pack(side="left", padx=14)
        self._ind = tk.Label(hdr, text="● 翻訳 ON",
                             bg="#161B22", fg="#3FB950",
                             font=("Yu Gothic UI", 10))
        self._ind.pack(side="left", padx=8)
        
        # 3. 【追加】ドット最適化チェックボックス（インジケーターの右側）
        self._pixel_var = tk.BooleanVar(value=self._settings.get("pixel_font_mode", False))
        cb = tk.Checkbutton(
            hdr, text="ドット最適化", variable=self._pixel_var,
            command=self._on_pixel_mode_changed,
            bg="#161B22", fg="#8B949E", selectcolor="#161B22",
            activebackground="#161B22", activeforeground="#58A6FF",
            font=("Yu Gothic UI", 9), bd=0, highlightthickness=0
        )
        cb.pack(side="left", padx=15) # 翻訳ONとの間に少し隙間を空ける

        # ドラッグ追従モードチェックボックス（ドット最適化の右側）
        self._drag_retrans_var = tk.BooleanVar(
            value=self._settings.get("drag_retranslate", False))
        tk.Checkbutton(
            hdr, text="移動再翻訳", variable=self._drag_retrans_var,
            command=self._on_drag_retranslate_changed,
            bg="#161B22", fg="#8B949E", selectcolor="#161B22",
            activebackground="#161B22", activeforeground="#58A6FF",
            font=("Yu Gothic UI", 9), bd=0, highlightthickness=0
        ).pack(side="left", padx=(0, 8))

        # ── 2段目（操作ボタン群 ＋ ⚙設定） ──
        def btn(p, txt, cmd, bg="#21262D", fg="#C9D1D9"):
            return tk.Button(p, text=txt, command=cmd,
                             bg=bg, fg=fg, relief="flat",
                             font=("Yu Gothic UI", 10), cursor="hand2",
                             padx=10, pady=5,
                             activebackground="#30363D", activeforeground=fg)

        r1 = tk.Frame(self, bg="#0D1117", pady=5)
        r1.pack(fill="x", padx=10)

        # 左側に並べるボタン群
        btn(r1, "＋ 枠を追加",  self._add_region, "#1F6FEB", "#FFF").pack(side="left", padx=3)
        btn(r1, "▶ 全枠を翻訳", self._translate_all).pack(side="left", padx=3)
        btn(r1, "⏸ ON/OFF",      self._toggle).pack(side="left", padx=3)
        btn(r1, "✕ 全枠クリア", self._clear_all).pack(side="left", padx=3)

        # 右端に⚙ボタンを追加
        tk.Button(r1, text="⚙", command=self._open_settings,
                  # 背景を少し明るく (#21262D) して目立たせる
                  bg="#0D1117", fg="#58A6FF", relief="flat",
                  # フォントを大きく (12px -> 18px)
                  font=("Segoe UI Symbol", 18, "bold"), cursor="hand2",
                  # ホバー時はもっと明るく
                  activebackground="#1F6FEB", activeforeground="#FFF",
                  # bd=0（枠なし）にしつつ、文字を縦中央に収めるためにpady=-2
                  bd=0, padx=12, pady=-2).pack(side="right", padx=(3, 0))

        # ── 3段目（リストボックス ＋ OCR補正辞書） ──
        mid = tk.Frame(self, bg="#0D1117")
        mid.pack(fill="both", expand=True, padx=10, pady=(0, 4))
        mid.columnconfigure(0, weight=3)
        mid.columnconfigure(1, weight=2)

        # 左: 翻訳枠一覧
        lf = tk.Frame(mid, bg="#0D1117")
        lf.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        tk.Label(lf, text="翻訳枠一覧  ※右クリックで削除/ON-OFF",
                 bg="#0D1117", fg="#8B949E",
                 font=("Yu Gothic UI", 9)).pack(anchor="w")
        self._lb = tk.Listbox(lf, bg="#161B22", fg="#C9D1D9",
                              selectbackground="#1F6FEB",
                              relief="flat", font=("Consolas", 9),
                              height=8, width=24, borderwidth=0)
        self._lb.pack(fill="both", expand=True)

        # 右: OCR補正辞書
        rf = tk.Frame(mid, bg="#0D1117")
        rf.grid(row=0, column=1, sticky="nsew")
        # --- 【変更】ラベルとボタンを横並びにするヘッダーフレーム ---
        dict_hdr = tk.Frame(rf, bg="#0D1117")
        dict_hdr.pack(fill="x", pady=(0, 2))

        tk.Label(dict_hdr, text="OCR補正辞書",
                 bg="#0D1117", fg="#8B949E",
                 font=("Yu Gothic UI", 9)).pack(side="left")

        # 保存ボタン
        tk.Button(
            dict_hdr, text="💾 保存", command=self._save_dictionary,
            bg="#21262D", fg="#C9D1D9", relief="flat",
            font=("Yu Gothic UI", 8), cursor="hand2",
            activebackground="#30363D", activeforeground="#58A6FF",
            bd=0, padx=6
        ).pack(side="right", padx=(2, 0))

        # 読込ボタン
        tk.Button(
            dict_hdr, text="📂 読込", command=self._load_dictionary,
            bg="#21262D", fg="#C9D1D9", relief="flat",
            font=("Yu Gothic UI", 8), cursor="hand2",
            activebackground="#30363D", activeforeground="#58A6FF",
            bd=0, padx=6
        ).pack(side="right")
        # ------------------------------------------------------
        self._corrections_text = tk.Text(
            rf, bg="#161B22", fg="#C9D1D9",
            insertbackground="#C9D1D9",
            relief="flat", font=("Consolas", 9),
            height=8, width=22,
            wrap="none",
        )
        self._corrections_text.insert(
            "1.0", self._settings.get("ocr_corrections", ""))
        self._corrections_text.pack(fill="both", expand=True)
        # フォーカスが外れたとき（または変更時）に設定に保存
        self._corrections_text.bind(
            "<FocusOut>", lambda e: self._save_corrections())
        # ツールチップ的なヒント
        tk.Label(rf, text="例: Clamage=Damage",
                 bg="#0D1117", fg="#6E7681",
                 font=("Yu Gothic UI", 8)).pack(anchor="w")
        
        # ▼ ここから追加：ボーダーレス操作UI
        # ==========================================
        tools_frame = tk.Frame(self, bg="#0D1117", pady=4)
        tools_frame.pack(fill="x", padx=10)

        self._borderless_btn = tk.Button(
            tools_frame, text="対象をボーダーレス化", command=self._toggle_borderless,
            bg="#21262D", fg="#C9D1D9", relief="flat",
            font=("Yu Gothic UI", 9), cursor="hand2",
            activebackground="#30363D", activeforeground="#58A6FF",
            padx=10, bd=0
        )
        self._borderless_btn.pack(side="left")

        tk.Label(tools_frame, text="※現在アクティブな窓を全画面化",
                 bg="#0D1117", fg="#8B949E", font=("Yu Gothic UI", 8)).pack(side="left", padx=5)
        # ==========================================
        # ▲ ここまで追加

        # ── 4段目（翻訳元言語トグル） ──
        lang_frame = tk.Frame(self, bg="#0D1117", pady=4)
        lang_frame.pack(fill="x", padx=10)
        tk.Label(lang_frame, text="翻訳元:",
                 bg="#0D1117", fg="#8B949E",
                 font=("Yu Gothic UI", 9)).pack(side="left", padx=(2, 6))

        self._lang_var = tk.StringVar(
            value=self._settings.get("source_lang", "en"))
        self._lang_btns = {}
        for code, label in SOURCE_LANGS:
            is_active = (code == self._lang_var.get())
            b = tk.Button(
                lang_frame, text=label,
                bg="#1F6FEB" if is_active else "#21262D",
                fg="#FFFFFF" if is_active else "#8B949E",
                relief="flat", font=("Yu Gothic UI", 9),
                cursor="hand2", padx=8, pady=2,
                activebackground="#1F6FEB", activeforeground="#FFF",
                command=lambda c=code: self._set_source_lang(c),
            )
            b.pack(side="left", padx=2)
            self._lang_btns[code] = b
            
        tk.Label(lang_frame, text="※日→英は結果をクリップボードにコピー",
                 bg="#0D1117", fg="#6E7681",
                 font=("Yu Gothic UI", 8)).pack(side="left", padx=(8, 0))    

        # ── 5段目（ステータスバー） ──
        sb = tk.Frame(self, bg="#161B22", pady=3)
        sb.pack(fill="x")
        self._sv = tk.StringVar(value="準備完了")
        tk.Label(sb, textvariable=self._sv,
                 bg="#161B22", fg="#8B949E",
                 font=("Yu Gothic UI", 9), anchor="w").pack(side="left", padx=10)
        
        self._scl = tk.Label(sb, text=self._sc_hint(),
                             bg="#161B22", fg="#6E7681",
                             font=("Consolas", 8))
        self._scl.pack(side="right", padx=10)

        self.geometry("560x360")

    # ── ウィンドウ操作 ──

    def _toggle_borderless(self):
        """
        すでにボーダーレス化している場合は即座に解除。
        そうでない場合は3秒待機して対象を取得しボーダーレス化。
        """
        # 記憶している対象ウィンドウがあれば即座に解除
        if getattr(self, "_target_hwnd", None):
            self._restore_borderless()
        else:
            self.set_status("⏳ 3秒後に処理します。対象のゲーム画面をクリックしてアクティブにしてください！")
            self.after(3000, self._execute_borderless)

    def _execute_borderless(self):
        """実際にボーダーレス化を行う処理（3秒後に発動）"""
        try:
            import win32gui
            import win32con
            import win32api
        except ImportError:
            self.set_status("エラー: コマンドラインで pip install pywin32 を実行してください")
            return

        # self.winfo_toplevel().winfo_id() を使うとより確実です
        my_hwnd = self.winfo_toplevel().winfo_id()
        target_hwnd = win32gui.GetForegroundWindow()

        # 3秒経ってもLocalLensTranslatorがアクティブなままだった場合はキャンセル
        if target_hwnd == my_hwnd or target_hwnd == 0:
            self.set_status("❌ キャンセルされました。ゲーム画面をアクティブにできませんでした。")
            return

        style = win32gui.GetWindowLong(target_hwnd, win32con.GWL_STYLE)
        has_border = style & win32con.WS_CAPTION

        if has_border:
            # 【ボーダーレス化】
            new_style = style & ~win32con.WS_CAPTION & ~win32con.WS_THICKFRAME
            win32gui.SetWindowLong(target_hwnd, win32con.GWL_STYLE, new_style)
            
            w = win32api.GetSystemMetrics(win32con.SM_CXSCREEN)
            h = win32api.GetSystemMetrics(win32con.SM_CYSCREEN)
            win32gui.SetWindowPos(target_hwnd, win32con.HWND_TOP, 0, 0, w, h, win32con.SWP_FRAMECHANGED)
            
            self.set_status("✅ 対象ウィンドウをボーダーレス全画面化しました")
            self._borderless_btn.config(text="ボーダーレス解除", fg="#58A6FF")
            
            # 後で即座に解除できるよう、ウィンドウIDを記憶
            self._target_hwnd = target_hwnd
        else:
            self.set_status("⚠ 対象はすでにボーダーレスのようです")

    def _restore_borderless(self):
        """記憶したウィンドウを即座に枠ありに戻す処理"""
        try:
            import win32gui
            import win32con
        except ImportError:
            return

        target_hwnd = self._target_hwnd

        # もしゲームがすでに終了していた場合の安全対策
        if not win32gui.IsWindow(target_hwnd):
            self.set_status("⚠ 対象のウィンドウが既に見つかりません")
            self._target_hwnd = None
            self._borderless_btn.config(text="対象をボーダーレス化", fg="#C9D1D9")
            return

        style = win32gui.GetWindowLong(target_hwnd, win32con.GWL_STYLE)
        
        # 【元に戻す】
        new_style = style | win32con.WS_CAPTION | win32con.WS_THICKFRAME
        win32gui.SetWindowLong(target_hwnd, win32con.GWL_STYLE, new_style)
        
        win32gui.SetWindowPos(target_hwnd, win32con.HWND_TOP, 100, 100, 1280, 720, win32con.SWP_FRAMECHANGED)
        self.set_status("✅ ウィンドウ枠を復元しました（1280x720）")
        self._borderless_btn.config(text="対象をボーダーレス化", fg="#C9D1D9")
        
        # 記憶したIDをリセット
        self._target_hwnd = None
            
# ── OCR補正辞書のファイル管理 ──

    def _load_dictionary(self):
        """外部テキストファイルからOCR補正辞書を読み込む"""
        filepath = filedialog.askopenfilename(
            title="OCR補正辞書を読み込み",
            filetypes=[("Text Files", "*.txt"), ("All Files", "*.*")]
        )
        if filepath:
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    content = f.read()
                
                # テキストエリアをクリアして読み込んだ内容を挿入
                self._corrections_text.delete("1.0", tk.END)
                self._corrections_text.insert("1.0", content)
                
                # 内部設定も更新して保存
                self._settings["ocr_corrections"] = content
                if hasattr(self, 'save_settings'):
                    self.save_settings()
                    
                self.set_status(f"✅ 辞書を読み込みました: {os.path.basename(filepath)}")
            except Exception as e:
                self.set_status(f"❌ 辞書の読み込みに失敗しました: {e}")

    def _save_dictionary(self):
        """現在のOCR補正辞書を外部テキストファイルに保存する"""
        # テキストエリアの現在の内容を取得 (最後の改行文字は除く)
        content = self._corrections_text.get("1.0", "end-1c")
        
        filepath = filedialog.asksaveasfilename(
            title="OCR補正辞書を保存",
            defaultextension=".txt",
            initialfile="ocr_dict_gameA.txt", # デフォルトのファイル名
            filetypes=[("Text Files", "*.txt"), ("All Files", "*.*")]
        )
        if filepath:
            try:
                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(content)
                self.set_status(f"✅ 辞書を保存しました: {os.path.basename(filepath)}")
            except Exception as e:
                self.set_status(f"❌ 辞書の保存に失敗しました: {e}")
                
    def _sc_hint(self):
        s = self._settings
        return (f"ON/OFF:{s['shortcut_toggle']}  "
                f"枠追加:{s['shortcut_add_region']}  "
                f"再翻訳:{s['shortcut_retranslate']}  "
                f"クリア:{s['shortcut_clear']}")

    def _bind_shortcuts(self):
        """
        グローバルホットキーを登録する。
        これにより、アプリが背面にあってもショートカットが有効になる。
        keyboard ライブラリがない場合は tkinter の bind_all にフォールバックする。
        """
        # 既存の登録を個別に解除（unhook_all は他アプリのフックも消すため使わない）
        for hk in getattr(self, "_registered_hotkeys", []):
            try:
                import keyboard
                keyboard.remove_hotkey(hk)
            except Exception:
                pass
        self._registered_hotkeys = []

        # tkinter bind_all も一旦クリア
        for seq in getattr(self, "_bound_seqs", []):
            try:
                self.unbind_all(seq)
            except Exception:
                pass
        self._bound_seqs = []

        mapping = [
            ("shortcut_toggle",      self._toggle),
            ("shortcut_add_region",  self._add_region),
            ("shortcut_clear",       self._clear_all),
            ("shortcut_retranslate", self._translate_all_active),
        ]

        try:
            import keyboard as kb
            for config_key, method in mapping:
                hotkey = self._settings.get(config_key, "")
                if not hotkey:
                    continue
                try:
                    hk = kb.add_hotkey(hotkey, lambda m=method: self.after(0, m))
                    self._registered_hotkeys.append(hk)
                except Exception as e:
                    print(f"ホットキー登録失敗 ({hotkey}): {e}")
            self.set_status("グローバルショートカット有効")
        except ImportError:
            # keyboard がなければ tkinter の bind_all で代替（ウィンドウフォーカス時のみ有効）
            for config_key, method in mapping:
                sc = self._settings.get(config_key, "")
                if not sc:
                    continue
                try:
                    seq = shortcut_to_bind(sc)
                    self.bind_all(seq, lambda e, m=method: m())
                    self._bound_seqs.append(seq)
                except Exception:
                    pass
            self.set_status("ショートカット有効（フォーカス時のみ）")

    # ── 枠操作 ──

    def _add_region(self):
        self.iconify()
        self.after(300, lambda: RegionSelector(self, self._on_selected))

    def _on_selected(self, x1: int, y1: int, x2: int, y2: int):
        """RegionSelector から呼ばれる。x1〜y2 はスクリーン絶対座標。"""
        self.deiconify()
        rid = self._next_id
        self._next_id += 1
        ov = RegionOverlay(
            self, rid, x1, y1, x2, y2,
            self._settings,
            on_remove=self._remove_region,
            on_retranslate=self._retranslate_region,
            on_ja_to_en=self._ja_to_en_region,
            on_sync_drag=lambda val: self._drag_retrans_var.set(val),
        )
        self._regions[rid] = ov
        self._refresh_lb()
        self.set_status(f"枠 {rid} を追加（計 {len(self._regions)} 枠）")
        if self._trans_on:
            self._translate_region(rid)

    def _retranslate_region(self, rid):
        """右クリック「再翻訳」から呼ばれる。_translate_region に委譲。"""
        self._translate_region(rid)

    def _remove_region(self, rid: int):
        self._regions.pop(rid, None)
        self._refresh_lb()
        self.set_status(f"枠 {rid} を削除しました")

    def _refresh_lb(self):
        self._lb.delete(0, "end")
        for rid, ov in self._regions.items():
            x1, y1, x2, y2 = ov.region
            st = "ON " if ov.is_active else "OFF"
            self._lb.insert("end",
                            f"  枠{rid:02d} [{st}] ({x1},{y1})→({x2},{y2})")

    # ── ON/OFF ──

    def _toggle(self):
        self._trans_on = not self._trans_on
        if self._trans_on:
            self._ind.configure(text="● 翻訳 ON", fg="#3FB950")
            self.set_status("翻訳 ON — 全枠を再翻訳します")
            for rid, ov in list(self._regions.items()):
                ov.set_enabled(True)   # 内部で deiconify される
            # オーバーレイが画面に描画されてからキャプチャするため少し待つ
            self.after(300, self._translate_all_active)
        else:
            self._ind.configure(text="○ 翻訳 OFF", fg="#6E7681")
            self.set_status("翻訳 OFF")
            for ov in self._regions.values():
                ov.set_enabled(False)

    # ── 翻訳 ──

    def _translate_all(self):
        """ボタンから呼ばれる。is_active な枠のみ翻訳。"""
        if not self._regions:
            self.set_status("翻訳枠がありません")
            return
        self.set_status(f"{len(self._regions)} 枠を翻訳中...")
        for rid in list(self._regions.keys()):
            self._translate_region(rid)

    def _translate_all_active(self):
        """翻訳ONに戻したとき・再翻訳ショートカットから呼ばれる。
        is_active フラグを強制的に True にしてから全枠翻訳する。"""
        if not self._regions:
            self.set_status("翻訳枠がありません")
            return
        self.set_status(f"{len(self._regions)} 枠を再翻訳中...")
        for rid, ov in list(self._regions.items()):
            if not ov.is_active:
                ov.set_enabled(True)   # deiconify も行われる
            self._translate_region(rid)

    def _translate_region(self, rid: int):
        ov = self._regions.get(rid)
        if ov is None or not ov.is_active:
            return

        region = ov.region  # スレッド開始前に座標を取得

        # キャプチャ前にオーバーレイを非表示（自分自身が写り込まないため）
        ov.set_enabled(False)
        self.after(0, lambda: ov.set_status("処理中..."))

        def worker():
            import time
            try:
                # withdraw が画面に反映されるまで待つ
                time.sleep(0.15)

                # 1. キャプチャ（カラー画像のまま保持）
                img_original = capture_region(*region)

                # 2. 色抽出（OCR前処理前のカラー画像で行う）
                bg_color, fg_color = None, None
                if self._settings.get("use_image_colors", True):
                    bg_color, fg_color = extract_dominant_colors(img_original)

                # 3. OCR
                self.after(0, lambda: ov.set_status("テキスト認識中..."))
                text = ocr_image(img_original, self._settings).strip()
                text = _fix_ocr_errors(text, self._settings.get("source_lang", "en"))
                # OCR補正辞書を適用
                corrections = self._settings.get("ocr_corrections", "")
                text = _apply_ocr_corrections(text, corrections)
                print(f"【OCR結果】: {text}")

                if not text:
                    def no_text():
                        ov.set_text("テキストが見つかりません\n"
                                    "（範囲を広げるか文字が鮮明か確認してください）")
                        ov.set_enabled(True)
                    self.after(0, no_text)
                    self.set_status(f"枠 {rid}: テキストなし")
                    return

                # 4. 翻訳
                self.after(0, lambda n=len(text): ov.set_status(f"翻訳中... ({n}文字)"))
                translated = lm_translate(text, self._settings)

                # 5. 色・テキスト・表示を1つのコールバックにまとめる
                #    （apply_image_colors → set_text → set_enabled の順を保証）
                def show_result(t=translated, b=bg_color, f=fg_color):
                    if b is not None:
                        ov.apply_image_colors(b, f)  # _bg/_fg を更新
                    ov.set_text(t)                   # _redraw（色反映済み）
                    ov.set_enabled(True)             # deiconify
                    # 日→英モードのときはクリップボードにもコピー
                    if self._settings.get("source_lang", "en") == "ja":
                        self.clipboard_clear()
                        self.clipboard_append(t)
                self.after(0, show_result)
                self.set_status(f"枠 {rid} 翻訳完了")

            except Exception as e:
                err = str(e)
                def show_error(m=err):
                    ov.set_text(f"⚠ エラー:\n{m}")
                    ov.set_enabled(True)
                self.after(0, show_error)
                self.set_status(f"枠 {rid} エラー: {err[:80]}")

        threading.Thread(target=worker, daemon=True).start()

    def _ja_to_en_region(self, rid: int):
        """日→英翻訳してクリップボードにコピーする"""
        ov = self._regions.get(rid)
        if ov is None:
            return
        region = ov.region
        ov.set_enabled(False)
        self.after(0, lambda: ov.set_status("日→英翻訳中..."))

        def worker():
            import time
            try:
                time.sleep(0.15)
                img = capture_region(*region)
                # 日本語OCRで読み取り
                settings_ja = dict(self._settings)
                settings_ja["source_lang"] = "ja"
                text = ocr_image(img, settings_ja).strip()
                text = _fix_ocr_errors(text, "ja")
                corrections = self._settings.get("ocr_corrections", "")
                text = _apply_ocr_corrections(text, corrections)
                print(f"【日→英 OCR】: {text}")
                if not text:
                    def no_text():
                        ov.set_text("テキストが見つかりません")
                        ov.set_enabled(True)
                    self.after(0, no_text)
                    return
                # 日→英翻訳（force_lang="ja"）
                translated = lm_translate(text, self._settings, force_lang="ja")
                # クリップボードにコピーして結果を表示
                def show_en(t=translated):
                    self.clipboard_clear()
                    self.clipboard_append(t)
                    ov.set_text(t)
                    ov.set_enabled(True)
                self.after(0, show_en)
                self.set_status("日→英翻訳完了・クリップボードにコピーしました")
            except Exception as e:
                err = str(e)
                def show_error(m=err):
                    ov.set_text(f"⚠ エラー:\n{m}")
                    ov.set_enabled(True)
                self.after(0, show_error)
                self.set_status(f"日→英エラー: {err[:60]}")

        threading.Thread(target=worker, daemon=True).start()
        
    def _save_corrections(self):
        """OCR補正辞書テキストエリアの内容を設定に保存する"""
        val = self._corrections_text.get("1.0", "end-1c")
        self._settings["ocr_corrections"] = val
        save_settings(self._settings)

    def _clear_all(self):
        for ov in list(self._regions.values()):
            try: ov.destroy()
            except Exception: pass
        self._regions.clear()
        self._next_id = 1
        self._refresh_lb()
        self.set_status("全枠をクリアしました")
        
    def _on_pixel_mode_changed(self):
        """ドットフォント最適化チェックボックスの状態を設定に反映して保存する"""
        is_on = self._pixel_var.get()
        self._settings["pixel_font_mode"] = is_on
        save_settings(self._settings)
        self.set_status(f"ドットフォント最適化: {'ON' if is_on else 'OFF'}")

    def _on_drag_retranslate_changed(self):
        """ドラッグ追従モードチェックボックスの状態を設定に反映して保存する"""
        is_on = self._drag_retrans_var.get()
        self._settings["drag_retranslate"] = is_on
        save_settings(self._settings)
        self.set_status(f"移動再翻訳モード: {'ON' if is_on else 'OFF'}")

    def _set_source_lang(self, code: str):
        """
        翻訳元言語を切り替える。
        prompt_template はユーザーが設定したものを保持し、上書きしない。
        言語指示は lm_translate 内でプレフィックスとして付加される。
        """
        self._lang_var.set(code)
        self._settings["source_lang"] = code
        # prompt_template は変更しない（カスタマイズを保持）
        save_settings(self._settings)
        # ボタンの見た目を更新
        for c, b in self._lang_btns.items():
            active = (c == code)
            b.configure(
                bg="#1F6FEB" if active else "#21262D",
                fg="#FFFFFF" if active else "#8B949E",
            )
        lang_label = dict(SOURCE_LANGS).get(code, code)
        self.set_status(f"翻訳元言語: {lang_label}")

    def _open_settings(self):
        def on_save(new_s):
            self._settings = new_s
            save_settings(new_s)
            self._bind_shortcuts()
            self._scl.configure(text=self._sc_hint())
            ff    = new_s.get("font_family", "Yu Gothic UI")
            fs    = int(new_s.get("font_size", 13))
            alpha = float(new_s.get("overlay_alpha", 0.92))
            for ov in self._regions.values():
                ov._settings = new_s      # settings の参照を新しいものに更新
                ov.update_font(ff, fs)
                ov.apply_alpha(alpha)
                if not new_s.get("use_image_colors", True):
                    ov.apply_image_colors(
                        new_s.get("overlay_bg", "#1A1A2E"),
                        new_s.get("overlay_fg", "#E8F4FD"),
                    )
            # メイン画面のチェックボックスを設定値に同期
            self._drag_retrans_var.set(bool(new_s.get("drag_retranslate", False)))
            # 言語ボタンを設定値に同期
            cur_lang = new_s.get("source_lang", "en")
            self._lang_var.set(cur_lang)
            for c, b in self._lang_btns.items():
                active = (c == cur_lang)
                b.configure(
                    bg="#1F6FEB" if active else "#21262D",
                    fg="#FFFFFF" if active else "#8B949E",
                )
            # OCR補正辞書をテキストエリアに同期
            self._corrections_text.delete("1.0", "end")
            self._corrections_text.insert(
                "1.0", new_s.get("ocr_corrections", ""))
            self.set_status("設定を保存しました")
        SettingsDialog(self, self._settings, on_save)

    def set_status(self, msg: str):
        self.after(0, lambda: self._sv.set(msg))


# ─────────────────────────────────────────────
if __name__ == "__main__":
    app = LocalLensTranslatorApp()
    app.mainloop()
