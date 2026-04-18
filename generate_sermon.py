#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
바른믿음교회 — 설교 자동생성 스크립트 (Gemini 무료 버전)
실행: python generate_sermon.py [wednesday|sunday]
"""

import os, sys, json, re, subprocess, datetime
from pathlib import Path

def install(pkg):
    subprocess.check_call([sys.executable, "-m", "pip", "install", pkg, "-q"])

try:
    from google import genai
except ImportError:
    install("google-genai"); from google import genai

try:
    from google.cloud import texttospeech
except ImportError:
    install("google-cloud-texttospeech"); from google.cloud import texttospeech

# ══════════════════════════════════════════════
# 설정
# ══════════════════════════════════════════════
GOOGLE_KEY_FILE = "barunmideum-church-a663de7588cf.json"
SERMONS_DATA_JS = "sermons-data.js"

CHURCH = {
    "name_ko": "바른믿음교회",
    "name_en": "True Faith Church",
    "motto": "느리게·바르게·함께",
    "five_ways": [
        "정도(正道) 바른 길을 걸어라",
        "정직(正直) 투명하게 살아라",
        "정착(定着) 뿌리를 내려라",
        "정민(正民) 이웃을 섬겨라",
        "정지(靜止) 멈추어야 들린다"
    ],
    "target": "직장생활로 바빠서 교회를 못 가는 20~40대 디지털 세대",
    "pastor": "Daniel Joung 목사",
}

TTS_VOICES = {
    "sermon":   {"name": "ko-KR-Chirp3-HD-Charon", "rate": 0.82},
    "fallback": {"name": "ko-KR-Chirp3-HD-Orus",   "rate": 0.82},
}


# ══════════════════════════════════════════════
# 1) 다음 설교 번호 & 타입 결정
# ══════════════════════════════════════════════
def get_next_sermon_info(force_type=None):
    latest_n = 8
    if Path(SERMONS_DATA_JS).exists():
        content = Path(SERMONS_DATA_JS).read_text(encoding="utf-8")
        nums = re.findall(r'n:\s*(\d+)', content)
        if nums:
            latest_n = max(int(x) for x in nums)

    next_n = latest_n + 1

    if force_type:
        stype = force_type
    else:
        weekday = datetime.datetime.now().weekday()
        stype = "wednesday" if weekday == 1 else "sunday"

    today = datetime.date.today()
    if stype == "wednesday":
        days_ahead = (2 - today.weekday()) % 7
        if days_ahead == 0: days_ahead = 7
        target_date = today + datetime.timedelta(days=days_ahead)
        series    = "바른믿음교회 말씀 · 수요 저녁 예배"
        series_en = "True Faith Church 말씀 · 수요 저녁 예배"
        worship   = "수요 저녁 예배"
    else:
        days_ahead = (6 - today.weekday()) % 7
        if days_ahead == 0: days_ahead = 7
        target_date = today + datetime.timedelta(days=days_ahead)
        series    = "바른믿음교회 말씀 · 주일 아침 예배"
        series_en = "True Faith Church 말씀 · 주일 아침 예배"
        worship   = "주일 아침 예배"

    kor_days = ["월요일","화요일","수요일","목요일","금요일","토요일","주일"]
    date_str = f"{target_date.year}년 {target_date.month}월 {target_date.day}일 {kor_days[target_date.weekday()]}"

    return {
        "n": next_n, "type": stype, "date": date_str,
        "series": series, "series_en": series_en,
        "worship": worship, "filename": f"sermon-{next_n}.html",
    }


# ══════════════════════════════════════════════
# 2) Gemini API로 설교 내용 생성
# ══════════════════════════════════════════════
def generate_sermon_content(info):
    print(f"\n📡 Gemini API 호출 중... (sermon-{info['n']}, {info['worship']})")

    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        print("❌ GEMINI_API_KEY 환경변수 없음"); sys.exit(1)

    client = genai.Client(api_key=api_key)

    prompt = f"""당신은 {CHURCH['name_ko']} ({CHURCH['name_en']}) 설교 작성 전문가입니다.

교회 정보:
- 표어: {CHURCH['motto']}
- 타겟: {CHURCH['target']}
- 담임: {CHURCH['pastor']}
- Five Ways of Life: {', '.join(CHURCH['five_ways'])}

오늘 작성할 설교:
- 번호: {info['n']}번 설교
- 예배: {info['worship']}
- 날짜: {info['date']}

다음 형식으로 JSON 하나만 반환하세요 (코드펜스 없이, 순수 JSON만):

{{
  "title": "설교 제목",
  "verse": "대표 성경 구절 원문",
  "verse_ref": "책 장:절 (예: 요한복음 15:5)",
  "sections": [
    {{
      "label": "배경 · 도입",
      "paragraphs": ["문단1", "문단2"],
      "verse_box": {{"text": "인용구절", "ref": "출처"}},
      "emphasis": "강조문장 또는 null"
    }},
    {{
      "label": "1부 · 소제목",
      "paragraphs": ["문단1", "문단2", "문단3"],
      "verse_box": {{"text": "인용구절", "ref": "출처"}},
      "emphasis": "강조문장"
    }},
    {{
      "label": "2부 · 소제목",
      "paragraphs": ["문단1", "문단2"],
      "verse_box": null,
      "emphasis": "강조문장"
    }},
    {{
      "label": "3부 · 소제목",
      "paragraphs": ["문단1", "문단2"],
      "verse_box": {{"text": "인용구절", "ref": "출처"}},
      "emphasis": null
    }},
    {{
      "label": "마무리 · 이번 주 실천",
      "paragraphs": ["문단1", "문단2"],
      "verse_box": {{"text": "기도문", "ref": "— 아멘 —"}},
      "emphasis": "느리게 · 바르게 · 함께 · 아멘"
    }}
  ],
  "plain_text": "TTS용 전체 설교 텍스트 (600~800자, 마침표 사용)",
  "reading_minutes": 5
}}

요구사항:
1. 20~40대 직장인에게 말하듯 쉽고 따뜻하게
2. 스마트폰·디지털 환경 공감 포함
3. Five Ways of Life 중 하나 자연스럽게 연결
4. 마지막은 반드시 "느리게, 바르게, 함께. 아멘." 으로 끝
5. 실천 가능한 한 가지 행동 제안
6. 한국어로만 작성
7. JSON만 반환"""

    response = client.models.generate_content(model="gemini-2.0-flash", contents=prompt)
    raw = response.text.strip()
    raw = re.sub(r'^```json\s*', '', raw)
    raw = re.sub(r'^```\s*', '', raw)
    raw = re.sub(r'\s*```$', '', raw)

    data = json.loads(raw)
    print(f"  ✅ 설교 생성 완료: {data['title']}")
    return data


# ══════════════════════════════════════════════
# 3) HTML 생성
# ══════════════════════════════════════════════
def build_html(info, sermon):
    prev_n = info['n'] - 1

    sections_html = ""
    for sec in sermon["sections"]:
        label      = sec.get("label", "")
        paragraphs = sec.get("paragraphs", [])
        verse_box  = sec.get("verse_box")
        emphasis   = sec.get("emphasis")

        p_html = "\n".join(f"      <p>{p}</p>" for p in paragraphs)

        vb_html = ""
        if verse_box:
            vb_html = f"""      <div class="verse-box">
        <div class="v-text">"{verse_box['text']}"</div>
        <div class="v-ref">— {verse_box['ref']} —</div>
      </div>"""

        em_html = ""
        if emphasis:
            if "아멘" in emphasis:
                em_html = f'      <p style="font-family:var(--font-c);font-size:11px;letter-spacing:4px;color:var(--gold);opacity:.7;text-align:center;margin-top:24px">{emphasis}</p>'
            else:
                em_html = f'      <p><span class="emphasis">{emphasis}</span></p>'

        sections_html += f"""
  <div class="sermon-section">
    <div class="section-label">{label}</div>
    <div class="sermon-text">
{p_html}
{vb_html}
{em_html}
    </div>
  </div>
  <div class="h-rule"><div class="h-rule-line"></div><div class="h-rule-dot"></div><div class="h-rule-line"></div></div>
"""

    plain_escaped = sermon["plain_text"].replace("\\","\\\\").replace("`","\\`").replace("${","\\${")

    html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>True Faith Church — {sermon['title']}</title>
<link href="https://fonts.googleapis.com/css2?family=Noto+Serif+KR:wght@300;400;500;700;900&family=Cinzel:wght@400;600;700&display=swap" rel="stylesheet">
<style>
:root{{--navy:#0B1622;--navy2:#0F1D2E;--gold:#C9A84C;--gold-l:#E8C96A;--gold-p:#F0DFAA;--sage:#7A9E7E;--cream:#F5EDD6;--border:rgba(201,168,76,0.14);--font-s:'Noto Serif KR',serif;--font-c:'Cinzel',serif}}
*,*::before,*::after{{margin:0;padding:0;box-sizing:border-box}}
html{{scroll-behavior:smooth}}
body{{background:var(--navy);color:var(--cream);font-family:var(--font-s);overflow-x:hidden}}
body::after{{content:'';position:fixed;inset:0;background-image:url("data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='0.035'/%3E%3C/svg%3E");pointer-events:none;z-index:9000;opacity:.4}}
a{{color:inherit;text-decoration:none}}
.nav{{position:sticky;top:0;z-index:200;padding:0 32px;height:64px;display:flex;align-items:center;justify-content:space-between;background:rgba(11,22,34,.92);backdrop-filter:blur(16px);border-bottom:1px solid var(--border)}}
.nav-logo{{font-family:var(--font-s);font-size:17px;font-weight:700;letter-spacing:4px}}
.nav-back{{font-family:var(--font-c);font-size:9px;letter-spacing:3px;color:var(--cream);opacity:.5;text-transform:uppercase;padding:8px 16px;border:1px solid var(--border);border-radius:2px}}
.progress-bar{{position:fixed;top:64px;left:0;width:0%;height:3px;background:linear-gradient(90deg,var(--sage),var(--gold));z-index:199}}
.hero{{padding:80px 32px 60px;max-width:720px;margin:0 auto;position:relative}}
.hero::before{{content:'';position:absolute;top:0;left:0;right:0;height:300px;background:radial-gradient(ellipse 80% 60% at 50% 0%,rgba(201,168,76,.07) 0%,transparent 70%);pointer-events:none}}
.sermon-series{{font-family:var(--font-c);font-size:9px;letter-spacing:5px;color:var(--gold);opacity:.6;text-transform:uppercase;margin-bottom:12px}}
.sermon-title{{font-family:var(--font-s);font-size:clamp(24px,5vw,38px);font-weight:900;color:var(--cream);line-height:1.4;margin-bottom:16px}}
.sermon-meta{{display:flex;align-items:center;gap:16px;flex-wrap:wrap;margin-bottom:24px}}
.meta-item{{font-family:var(--font-c);font-size:9px;letter-spacing:3px;color:var(--cream);opacity:.4;display:flex;align-items:center;gap:6px}}
.sermon-verse-hero{{border-left:3px solid var(--gold);padding:16px 20px;background:rgba(201,168,76,.05);border-radius:0 6px 6px 0;margin-bottom:32px}}
.verse-text{{font-style:italic;font-size:15px;color:var(--gold-p);line-height:2;margin-bottom:6px}}
.verse-ref{{font-family:var(--font-c);font-size:9px;letter-spacing:3px;color:var(--gold);opacity:.55}}
.tts-player{{background:linear-gradient(145deg,rgba(201,168,76,.08),rgba(15,29,46,.95));border:1px solid rgba(201,168,76,.25);border-radius:12px;padding:24px 28px;margin-bottom:40px;position:relative;overflow:hidden}}
.tts-player::before{{content:'';position:absolute;top:-1px;left:15%;right:15%;height:1px;background:linear-gradient(90deg,transparent,var(--gold),transparent)}}
.tts-header{{display:flex;align-items:center;justify-content:space-between;margin-bottom:16px;flex-wrap:wrap;gap:10px}}
.tts-info .tts-title{{font-family:var(--font-s);font-size:13px;font-weight:700;color:var(--cream)}}
.tts-info .tts-sub{{font-family:var(--font-c);font-size:8px;letter-spacing:3px;color:var(--gold);opacity:.55;text-transform:uppercase;margin-top:3px}}
.tts-controls{{display:flex;align-items:center;gap:8px}}
.tts-btn{{width:44px;height:44px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:16px;cursor:pointer;border:1px solid rgba(201,168,76,.3);background:rgba(201,168,76,.08);color:var(--gold)}}
.tts-btn.play{{width:52px;height:52px;font-size:20px;background:linear-gradient(135deg,var(--gold),var(--gold-l));color:var(--navy);border:none;box-shadow:0 4px 20px rgba(201,168,76,.3)}}
.tts-progress-bar{{width:100%;height:3px;background:rgba(255,255,255,.08);border-radius:2px;cursor:pointer;margin-bottom:8px;overflow:hidden}}
.tts-progress-fill{{height:100%;background:linear-gradient(90deg,var(--gold),var(--gold-l));border-radius:2px;width:0%}}
.tts-time-row{{display:flex;justify-content:space-between}}
.tts-time{{font-family:var(--font-c);font-size:8px;letter-spacing:2px;color:var(--cream);opacity:.4}}
.tts-settings{{display:flex;align-items:center;gap:14px;padding-top:14px;border-top:1px solid rgba(201,168,76,.1);margin-top:10px}}
.tts-status{{font-family:var(--font-c);font-size:8px;letter-spacing:3px;color:var(--gold);opacity:.7;text-transform:uppercase;margin-left:auto;display:flex;align-items:center;gap:5px}}
.tts-dot{{width:6px;height:6px;border-radius:50%;background:var(--sage);display:none}}
.tts-dot.on{{display:block;animation:blink 1.4s ease-in-out infinite}}
@keyframes blink{{0%,100%{{opacity:1}}50%{{opacity:.3}}}}
.content{{max-width:720px;margin:0 auto;padding:0 32px 80px}}
.sermon-section{{margin-bottom:36px}}
.section-label{{font-family:var(--font-c);font-size:8px;letter-spacing:4px;color:var(--gold);opacity:.55;text-transform:uppercase;margin-bottom:16px;display:flex;align-items:center;gap:10px}}
.section-label::after{{content:'';flex:1;height:1px;background:linear-gradient(90deg,rgba(201,168,76,.2),transparent)}}
.sermon-text p{{font-size:15px;font-weight:300;line-height:2.8;color:var(--cream);opacity:.82;margin-bottom:14px}}
.emphasis{{color:var(--gold-p);font-weight:500}}
.bold{{font-weight:700;color:var(--cream)}}
.verse-box{{border-left:2px solid rgba(201,168,76,.4);padding:16px 20px;background:rgba(201,168,76,.04);border-radius:0 6px 6px 0;margin:20px 0}}
.verse-box .v-text{{font-style:italic;font-size:14px;color:var(--gold-p);line-height:2.2;margin-bottom:6px}}
.verse-box .v-ref{{font-family:var(--font-c);font-size:8px;letter-spacing:2px;color:var(--gold);opacity:.5}}
.h-rule{{display:flex;align-items:center;gap:12px;margin:32px 0}}
.h-rule-line{{flex:1;height:1px;background:linear-gradient(90deg,transparent,rgba(201,168,76,.2),transparent)}}
.h-rule-dot{{width:5px;height:5px;background:var(--gold);transform:rotate(45deg);opacity:.45;flex-shrink:0}}
.sermon-nav{{display:flex;justify-content:space-between;align-items:center;padding:24px 0;border-top:1px solid var(--border);margin-top:20px;flex-wrap:wrap;gap:12px}}
.sn-btn{{font-family:var(--font-c);font-size:9px;letter-spacing:3px;color:var(--gold);opacity:.65;text-transform:uppercase;padding:10px 18px;border:1px solid rgba(201,168,76,.25);border-radius:4px}}
.share-row{{display:flex;justify-content:flex-end;margin-top:20px}}
.share-btn{{width:48px;height:48px;border-radius:50%;background:rgba(201,168,76,.12);border:1px solid rgba(201,168,76,.25);display:flex;align-items:center;justify-content:center;font-size:18px;cursor:pointer}}
@media(max-width:600px){{.nav{{padding:0 16px}}.content,.hero{{padding-left:20px;padding-right:20px}}}}
</style>
</head>
<body>
<div class="progress-bar" id="progressBar"></div>
<nav class="nav">
  <div class="nav-logo">True Faith Church</div>
  <a href="/sermons.html" class="nav-back">← 말씀 목록</a>
</nav>
<div class="hero">
  <div class="sermon-series">{info['series_en']}</div>
  <h1 class="sermon-title">{sermon['title']}</h1>
  <div class="sermon-meta">
    <span class="meta-item">✝ {info['date']}</span>
    <span class="meta-item">📖 {info['worship']}</span>
    <span class="meta-item">⏱ 낭독 약 {sermon['reading_minutes']}분</span>
  </div>
  <div class="sermon-verse-hero">
    <div class="verse-text">"{sermon['verse']}"</div>
    <div class="verse-ref">— {sermon['verse_ref']} —</div>
  </div>
  <div class="tts-player">
    <div class="tts-header">
      <div class="tts-info">
        <div class="tts-title">✝ 말씀 낭독</div>
        <div class="tts-sub">낮고 차분한 목소리 · 약 {sermon['reading_minutes']}분</div>
      </div>
      <div class="tts-controls">
        <button class="tts-btn" onclick="ttsRestart()">↺</button>
        <button class="tts-btn play" id="playBtn" onclick="ttsToggle()">▶</button>
        <button class="tts-btn" onclick="ttsStop()">■</button>
      </div>
    </div>
    <div class="tts-progress-bar" onclick="ttsSeek(event)">
      <div class="tts-progress-fill" id="ttsProgressFill"></div>
    </div>
    <div class="tts-time-row">
      <span class="tts-time" id="ttsCurrentTime">0:00</span>
      <span class="tts-time">약 {sermon['reading_minutes']}:00</span>
    </div>
    <div class="tts-settings">
      <div class="tts-status">
        <div class="tts-dot" id="ttsDot"></div>
        <span id="ttsStatusText">재생 준비</span>
      </div>
    </div>
  </div>
</div>
<div class="content">
{sections_html}
  <div class="sermon-nav">
    <a href="/sermon-{prev_n}.html" class="sn-btn">← 이전 말씀</a>
    <a href="/sermons.html" class="sn-btn">말씀 목록</a>
  </div>
  <div class="share-row">
    <button class="share-btn" onclick="window.scrollTo({{top:0,behavior:'smooth'}})">↑</button>
  </div>
</div>
<audio id="sermonAudio" src="sermon.mp3" preload="auto"></audio>
<script>
let sermonAudio,isPlaying=false,isPaused=false,elapsedTime=0,startTime=0,timerInterval=null;
window.addEventListener('scroll',()=>{{const st=document.documentElement.scrollTop,sh=document.documentElement.scrollHeight-window.innerHeight;if(sh>0)document.getElementById('progressBar').style.width=(st/sh*100)+'%';}});
window.addEventListener('load',()=>{{
  sermonAudio=document.getElementById('sermonAudio');
  document.getElementById('ttsStatusText').textContent='재생 준비 ✅';
  sermonAudio.addEventListener('timeupdate',()=>{{if(sermonAudio.duration){{document.getElementById('ttsProgressFill').style.width=(sermonAudio.currentTime/sermonAudio.duration*100)+'%';const m=Math.floor(sermonAudio.currentTime/60),s=Math.floor(sermonAudio.currentTime%60);document.getElementById('ttsCurrentTime').textContent=m+':'+String(s).padStart(2,'0');}}}});
  sermonAudio.addEventListener('ended',()=>{{isPlaying=false;isPaused=false;document.getElementById('playBtn').textContent='▶';document.getElementById('ttsDot').classList.remove('on');document.getElementById('ttsStatusText').textContent='낭독 완료 🙏';document.getElementById('ttsProgressFill').style.width='100%';clearInterval(timerInterval);}});
}});
function ttsToggle(){{if(!isPlaying&&!isPaused)ttsPlay();else if(isPlaying)ttsPause();else ttsResume();}}
function ttsPlay(){{sermonAudio.currentTime=0;sermonAudio.play();isPlaying=true;isPaused=false;document.getElementById('playBtn').textContent='⏸';document.getElementById('ttsDot').classList.add('on');document.getElementById('ttsStatusText').textContent='낭독 중 ✝';startTimer();}}
function ttsPause(){{sermonAudio.pause();isPlaying=false;isPaused=true;document.getElementById('playBtn').textContent='▶';document.getElementById('ttsDot').classList.remove('on');document.getElementById('ttsStatusText').textContent='일시정지';clearInterval(timerInterval);}}
function ttsResume(){{sermonAudio.play();isPlaying=true;isPaused=false;document.getElementById('playBtn').textContent='⏸';document.getElementById('ttsDot').classList.add('on');document.getElementById('ttsStatusText').textContent='낭독 중 ✝';startTimer();}}
function ttsStop(){{sermonAudio.pause();sermonAudio.currentTime=0;isPlaying=false;isPaused=false;elapsedTime=0;document.getElementById('playBtn').textContent='▶';document.getElementById('ttsDot').classList.remove('on');document.getElementById('ttsStatusText').textContent='재생 준비 ✅';document.getElementById('ttsProgressFill').style.width='0%';document.getElementById('ttsCurrentTime').textContent='0:00';clearInterval(timerInterval);}}
function ttsRestart(){{ttsStop();setTimeout(()=>ttsPlay(),300);}}
function startTimer(){{startTime=Date.now()-(elapsedTime*1000);timerInterval=setInterval(()=>{{elapsedTime=(Date.now()-startTime)/1000;const m=Math.floor(elapsedTime/60),s=Math.floor(elapsedTime%60);document.getElementById('ttsCurrentTime').textContent=m+':'+String(s).padStart(2,'0');}},500);}}
function ttsSeek(e){{if(sermonAudio.duration){{sermonAudio.currentTime=(e.offsetX/e.currentTarget.offsetWidth)*sermonAudio.duration;}}}}
window.addEventListener('beforeunload',()=>{{if(sermonAudio)sermonAudio.pause();}});
</script>
</body>
</html>"""
    return html


# ══════════════════════════════════════════════
# 4) sermons-data.js 업데이트
# ══════════════════════════════════════════════
def update_sermons_data(info, sermon):
    print(f"\n📝 {SERMONS_DATA_JS} 업데이트 중...")
    content = ""
    if Path(SERMONS_DATA_JS).exists():
        content = Path(SERMONS_DATA_JS).read_text(encoding="utf-8")

    new_entry = f"""  {{
    n: {info['n']},
    title: "{sermon['title']}",
    verse: "{sermon['verse']}",
    ref: "{sermon['verse_ref']}",
    series: "{info['series']}",
    date: "{info['date']}",
    url: "/sermon-{info['n']}.html",
    text: {json.dumps(sermon['plain_text'], ensure_ascii=False)}
  }}"""

    content = re.sub(r'/\*\s*최신 설교.*?\*/', '', content, flags=re.DOTALL).rstrip()
    idx = content.rfind("}")
    if idx >= 0:
        content = content[:idx+1] + ",\n" + new_entry + "\n\n];\n"
    else:
        content = f"var SERMONS_DATA = [\n{new_entry}\n];\n"

    content += "\n/* 최신 설교 = 마지막 항목 */\nvar LATEST_SERMON = SERMONS_DATA[SERMONS_DATA.length - 1];\n"
    Path(SERMONS_DATA_JS).write_text(content, encoding="utf-8")
    print(f"  ✅ {SERMONS_DATA_JS} 업데이트 완료")


# ══════════════════════════════════════════════
# 5) TTS 설교 MP3 생성
# ══════════════════════════════════════════════
def generate_sermon_tts(plain_text):
    print(f"\n🎙 설교 TTS 생성 중 (sermon.mp3)...")
    if not Path(GOOGLE_KEY_FILE).exists():
        print(f"  ⚠️ 키 파일 없음 — TTS 건너뜀"); return False
    os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = GOOGLE_KEY_FILE
    try:
        client = texttospeech.TextToSpeechClient()
        cfg = TTS_VOICES["sermon"]
        synthesis_input = texttospeech.SynthesisInput(text=plain_text)
        voice = texttospeech.VoiceSelectionParams(language_code="ko-KR", name=cfg["name"])
        audio_config = texttospeech.AudioConfig(
            audio_encoding=texttospeech.AudioEncoding.MP3, speaking_rate=cfg["rate"])
        response = client.synthesize_speech(input=synthesis_input, voice=voice, audio_config=audio_config)
        with open("sermon.mp3", "wb") as f:
            f.write(response.audio_content)
        print(f"  ✅ sermon.mp3 완료 ({len(response.audio_content)//1024}KB)")
        return True
    except Exception as e:
        print(f"  ❌ TTS 오류: {e}"); return False


# ══════════════════════════════════════════════
# 6) GitHub 커밋 & 푸시
# ══════════════════════════════════════════════
def git_commit_push(info):
    print(f"\n🚀 GitHub 커밋 & 푸시 중...")
    files = [info["filename"], SERMONS_DATA_JS]
    if Path("sermon.mp3").exists():
        files.append("sermon.mp3")
    try:
        subprocess.run(["git", "config", "user.email", "jinsiljoung@gmail.com"], check=True)
        subprocess.run(["git", "config", "user.name", "Daniel Joung"], check=True)
        subprocess.run(["git", "add"] + files, check=True)
        msg = f"✝ 설교 자동생성: sermon-{info['n']} ({info['date']})"
        subprocess.run(["git", "commit", "-m", msg], check=True)
        subprocess.run(["git", "push"], check=True)
        print(f"  ✅ 푸시 완료!")
    except subprocess.CalledProcessError as e:
        print(f"  ❌ Git 오류: {e}"); sys.exit(1)


# ══════════════════════════════════════════════
# 메인
# ══════════════════════════════════════════════
def main():
    print("=" * 55)
    print("  ✝ 바른믿음교회 설교 자동생성 (Gemini 무료)")
    print("=" * 55)

    force_type = None
    if len(sys.argv) > 1:
        arg = sys.argv[1].lower()
        if arg in ("wednesday", "wed", "수요"): force_type = "wednesday"
        elif arg in ("sunday", "sun", "주일"):  force_type = "sunday"

    info   = get_next_sermon_info(force_type)
    print(f"\n📅 생성 대상: sermon-{info['n']}.html | {info['date']} | {info['worship']}")

    sermon = generate_sermon_content(info)

    print(f"\n🖊 HTML 생성 중: {info['filename']} ...")
    html = build_html(info, sermon)
    Path(info["filename"]).write_text(html, encoding="utf-8")
    print(f"  ✅ {info['filename']} 저장 완료")

    update_sermons_data(info, sermon)
    generate_sermon_tts(sermon["plain_text"])
    git_commit_push(info)

    print("\n" + "=" * 55)
    print(f"  🎉 완료! sermon-{info['n']}.html 생성 & 배포")
    print("=" * 55)


if __name__ == "__main__":
    main()
