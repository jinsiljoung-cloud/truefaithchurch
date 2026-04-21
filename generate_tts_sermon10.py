#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
바른믿음교회 — Google Cloud TTS MP3 생성 스크립트
실행: python generate_tts.py
"""

import os, sys

try:
    from google.cloud import texttospeech
except ImportError:
    os.system("pip install google-cloud-texttospeech")
    from google.cloud import texttospeech

KEY_FILE = "barunmideum-church-a663de7588cf.json"
os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = KEY_FILE

# ── 파트별 음성 설정 ──
VOICES = {
    "prayer":      {"name": "ko-KR-Chirp3-HD-Achernar", "rate": 0.85, "pitch": 0.0},
    "creed":       {"name": "ko-KR-Chirp3-HD-Achernar", "rate": 0.82, "pitch": 0.0},
    "complete":    {"name": "ko-KR-Chirp3-HD-Achernar", "rate": 0.85, "pitch": 0.0},
    "benediction": {"name": "ko-KR-Chirp3-HD-Charon",   "rate": 0.83, "pitch": 0.0},
    "sermon":      {"name": "ko-KR-Chirp3-HD-Charon",   "rate": 0.82, "pitch": 0.0},
}

FALLBACK_VOICES = {
    "prayer":      "ko-KR-Chirp3-HD-Kore",
    "creed":       "ko-KR-Chirp3-HD-Kore",
    "complete":    "ko-KR-Chirp3-HD-Kore",
    "benediction": "ko-KR-Chirp3-HD-Orus",
    "sermon":      "ko-KR-Chirp3-HD-Charon",
}

TEXTS = {
    "prayer": """하늘에 계신 하나님 아버지, 오늘도 이 자리에 나아올 수 있음에 감사드립니다.
바쁜 일상 속에서도 하나님을 향해 마음을 열 수 있도록 인도해 주셨음에 감사합니다.
오늘 하루 우리의 발걸음을 바른 길로 인도해 주시고,
스마트폰 화면 너머에서도 하나님의 존재를 느낄 수 있게 해주세요.
빠른 세상 속에서 느리게, 바르게, 함께 걸어가게 하시고,
디지털 세대에게 하나님의 말씀이 빛이 되게 하여 주옵소서.
오늘 이 예배를 통해 마음에 평안을 주시고,
하루를 주님 안에서 시작하게 해주세요.
예수님 이름으로 기도드립니다. 아멘.""",

    "creed": """나는 바른 길을 걷는 바른믿음 성도입니다.
빠름보다 깊음을, 소유보다 존재를, 성공보다 성품을 추구합니다.
나는 디지털 세계에서도 하나님의 사람으로 살며,
느린 삶 안에서 영원을 만납니다.
아멘.""",

    "benediction": """여호와는 너를 지키시는 이시라.
여호와께서 네 오른쪽에서 네 그늘이 되시나니.
낮의 해가 너를 상하게 하지 아니하며 밤의 달도 너를 해치지 아니하리로다.
여호와께서 너를 지켜 모든 환난을 면하게 하시며 또 네 영혼을 지키시리로다.
여호와께서 너의 출입을 지금부터 영원까지 지키시리로다.
아멘.
오늘도 느리게, 바르게, 함께 걸어가십시오.
하나님의 은혜가 함께하시기를 축원합니다.""",

    "complete": """오늘 예배를 마쳤습니다.
하나님이 당신과 함께하십니다.
오늘 예배가 은혜로웠다면 아래 헌금하기를 눌러 바른믿음교회 후원에 참여해 주세요.
성도님들의 작은 후원이 온라인 성전을 만드는데 큰 힘이 됩니다.
감사합니다.""",

        "sermon": """불안한 당신에게. 지금 이 자리면 충분합니다.

오늘 수요 저녁, 빌립보서 4장 말씀을 함께 나눕니다.

솔직하게 물어볼게요. 요즘 어떠세요? 진짜로요.

취업 걱정, 돈 걱정, 미래 걱정. 카톡 알림은 쉬지 않고 울리고, SNS는 남들 잘 사는 모습을 끊임없이 보여줘요. 우리는 역사상 가장 많은 정보를 가진 세대이면서, 동시에 가장 불안한 세대일 수 있어요.

이 불안은 나약해서가 아니에요. 치열하게 살고 있다는 증거예요. 그런데 문제는 이 불안이 나를 끌고 다닐 때예요.

1부. 걱정하지 말라는 말의 진짜 의미.

아무것도 염려하지 말고 다만 모든 일에 기도와 간구로 너희 구할 것을 감사함으로 하나님께 아뢰라. 빌립보서 4장 6절.

이 말이 들릴 때마다 억울하지 않으세요? 걱정이 되니까 걱정하는 거지, 안 하고 싶어서 하는 게 아니잖아요.

그런데 이 편지를 쓴 바울은 지금 감옥 안에 있어요. 목숨 걱정을 하는 자리에서 이 말을 썼어요.

그러니까 이 말은 걱정할 이유가 없다는 게 아니에요. 걱정이 너를 지배하게 두지 말라는 거예요.

불안은 사라지지 않아요. 그러나 불안이 나를 끌고 다니느냐, 내가 불안을 하나님 앞에 내려놓느냐, 그 선택은 우리에게 있어요.

2부. 정지, 멈추어야 들린다.

바른믿음교회가 말하는 다섯 번째 삶의 방식은 정지입니다. 멈추어야 들린다.

우리가 불안에서 벗어나지 못하는 이유 중 하나는 너무 많은 채널에 동시에 접속해 있기 때문이에요. 그 속에서 하나님의 음성이 들릴 리가 없어요.

라디오 주파수를 맞추려면 다른 채널을 잠깐 꺼야 하듯이, 하나님 앞에 서려면 잠깐 멈춰야 해요.

멈추는 것이 나태한 게 아니에요. 멈추는 것이 가장 적극적인 신앙의 행위예요.

3부. 모든 지각에 뛰어난 평강.

그리하면 모든 지각에 뛰어난 하나님의 평강이 그리스도 예수 안에서 너희 마음과 생각을 지키시리라. 빌립보서 4장 7절.

모든 지각에 뛰어난 평강이 뭔지 아세요? 논리적으로 설명이 안 되는 평안이에요.

상황이 해결되지 않았는데 마음이 괜찮은 상태. 미래가 불확실한데 이상하게 무섭지 않은 상태.

이건 내가 만들어내는 게 아니에요. 하나님이 우리 마음을 지켜주시는 거예요.

조건은 하나예요. 하나님 앞에 솔직하게 고백하는 것. 저 지금 불안해요. 모르겠어요. 힘들어요. 그 솔직함이 하나님과의 연결이에요.

마무리.

오늘 이 자리에 오신 것 자체가 이미 믿음이에요.

이번 한 주, 하루에 딱 한 번만 해보세요. 스마트폰을 내려놓고, 눈을 감고, 이렇게 고백해보세요.

하나님, 저 지금 불안해요. 그냥 당신 앞에 있을게요.

그 짧은 고백이 모든 지각에 뛰어난 평강의 시작입니다.

느리게, 바르게, 함께. 아멘.""",
}


def generate_mp3(name, text, voice_name, rate, pitch, client):
    print(f"  생성 중: {name}.mp3  [{voice_name}] ...", end=" ", flush=True)
    try:
        synthesis_input = texttospeech.SynthesisInput(text=text)
        voice = texttospeech.VoiceSelectionParams(
            language_code="ko-KR",
            name=voice_name,
        )
        audio_config = texttospeech.AudioConfig(
            audio_encoding=texttospeech.AudioEncoding.MP3,
            speaking_rate=rate,
            pitch=pitch,
        )
        response = client.synthesize_speech(
            input=synthesis_input, voice=voice, audio_config=audio_config
        )
        with open(f"{name}.mp3", 'wb') as f:
            f.write(response.audio_content)
        print(f"✅  {len(response.audio_content)//1024}KB")
        return True
    except Exception as e:
        print(f"❌  오류: {e}")
        return False


def main():
    print("=" * 55)
    print("  ✝ 바른믿음교회 TTS MP3 생성기 (Chirp3-HD)")
    print("=" * 55)

    if not os.path.exists(KEY_FILE):
        print(f"❌ 키 파일 없음: {KEY_FILE}")
        sys.exit(1)

    client = texttospeech.TextToSpeechClient()

    print("\n📁 기도·신앙고백·완료멘트  [Achernar — 여성]")
    print("📖 축도·설교  [Charon — 남성]\n")

    print("\n📖 설교 [Charon — 남성]\n")
    name = "sermon"
    cfg = VOICES[name]
    ok = generate_mp3(name, TEXTS[name], cfg["name"], cfg["rate"], cfg["pitch"], client)
    if not ok and name in FALLBACK_VOICES:
        fb = FALLBACK_VOICES[name]
        print(f"  ↳ fallback: {fb} ...", end=" ", flush=True)
        generate_mp3(name, TEXTS[name], fb, cfg["rate"], cfg["pitch"], client)

    print()
    print("=" * 55)
    if os.path.exists("sermon.mp3"):
        size = os.path.getsize("sermon.mp3") // 1024
        print(f"✅ 완료!  📁 sermon.mp3  ({size}KB)")
    print()
    print("👉 sermon.mp3를 GitHub에 업로드하세요!")
    print("=" * 55)


if __name__ == "__main__":
    main()
