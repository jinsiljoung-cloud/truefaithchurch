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

    "sermon": """끝까지 믿음을 지키라.

오늘 수요 저녁, 이 말씀을 드립니다.

잠깐 자신에게 솔직하게 물어보십시오.
요즘 믿음이 흔들린 적이 있으십니까?
기도가 잘 안 되고, 말씀이 잘 안 읽히고, 하나님이 멀게 느껴진 적이 있으십니까?

있다면, 오늘 이 말씀은 바로 당신을 위한 것입니다.

끝까지 견디는 자는 구원을 얻으리라. 마태복음 10장 22절.

이 말씀은 예수님이 열두 제자를 처음 세상으로 파송하시면서 하신 말씀입니다.
예수님은 아름다운 말씀만 하지 않으셨습니다.

보라 내가 너희를 보냄이 양을 이리 가운데로 보냄과 같도다. 마태복음 10장 16절.

양이 이리 가운데로 들어가는 것입니다. 위험하고, 힘들고, 두렵습니다.
예수님은 이것을 알고 계셨습니다. 그래서 미리 말씀하셨습니다.
힘들 것이다. 그러나 끝까지 견디는 자는 구원을 얻으리라.

1부. 믿음을 흔드는 것들.

오늘 우리의 믿음을 흔드는 것은 무엇입니까?

첫 번째는 바쁨입니다.
너무 바빠서 기도할 시간이 없습니다. 믿음이 없어서가 아닙니다. 그냥 바쁩니다.

두 번째는 지침입니다.
기도해도 응답이 없는 것 같습니다. 믿음으로 살려고 했는데 오히려 더 힘든 것 같습니다.

세 번째는 스마트폰과 디지털 세상입니다.
하루 종일 화면 앞에 있습니다. 그 속에서 하나님의 음성은 점점 작아집니다.

가시덤불에 뿌려진 씨는 세상의 염려와 재물의 유혹에 막혀 열매를 맺지 못합니다.
지금 내 믿음 주변에 가시덤불이 있지는 않습니까?

2부. 끝까지라는 말의 의미.

끝까지의 헬라어 원어는 휘포메네입니다.
단순히 버틴다는 뜻이 아닙니다. 그 자리를 지킨다는 뜻입니다.

폭풍이 올 때 나무는 달아나지 않습니다.
뿌리를 더 깊이 박고 그 자리를 지킵니다.
바람이 불수록 뿌리가 더 깊어집니다.

끝까지 견딘다는 것은, 상황이 어려워도 하나님 앞에서 달아나지 않는 것입니다.
기도가 잘 안 돼도 기도의 자리를 지키는 것입니다.
믿음은 느낌이 아닙니다.
보이지 않아도, 느껴지지 않아도, 하나님이 계신다는 것을 붙드는 것이 믿음입니다.

3부. 끝까지 견딘 사람들.

욥을 생각해 보십시오.
모든 것을 잃었습니다. 재산도 잃고, 자녀도 잃고, 건강도 잃었습니다.
그런데 욥은 말했습니다.
그가 나를 죽이실지라도 나는 그를 의뢰하리라. 욥기 13장 15절.

다윗을 생각해 보십시오.
굴속에 숨어 도망자로 살면서도 선포했습니다.
내 영광아 깰지어다 내가 새벽을 깨우리로다.

그들은 특별한 사람이 아니었습니다.
그러나 끝까지 하나님 앞에 머물렀습니다.

4부. 바른믿음교회가 말하는 정착의 믿음.

바른믿음교회의 다섯 가지 삶의 방식 중 하나가 정착입니다. 뿌리를 내려라.

흔들리는 세상에서 하나님께 뿌리를 내리는 것.
끝까지 견디는 자는 구원을 얻으리라.
이 구원은 지금 이 자리에서 하나님 안에 머무는 것, 그 자체입니다.

마무리.

내일 아침, 눈을 뜨면 딱 1분만 하나님께 드리십시오.
화면을 열기 전에. 알림을 확인하기 전에.

하나님, 오늘도 당신 앞에 섰습니다.
오늘 하루도 끝까지 당신을 붙들게 하십시오.

그 1분이, 끝까지 견디는 삶의 시작입니다.

끝까지 견디는 자는 구원을 얻으리라.
당신이 바로 그 사람입니다.

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

    for name, text in TEXTS.items():
        cfg = VOICES[name]
        ok = generate_mp3(name, text, cfg["name"], cfg["rate"], cfg["pitch"], client)
        if not ok and name in FALLBACK_VOICES:
            fb = FALLBACK_VOICES[name]
            print(f"  ↳ fallback: {fb} ...", end=" ", flush=True)
            generate_mp3(name, text, fb, cfg["rate"], cfg["pitch"], client)

    print()
    print("=" * 55)
    print("✅ 완료! 생성된 파일:")
    for name in TEXTS:
        if os.path.exists(f"{name}.mp3"):
            size = os.path.getsize(f"{name}.mp3") // 1024
            print(f"  📁 {name}.mp3  ({size}KB)")
    print()
    print("👉 생성된 MP3 파일을 GitHub에 업로드하세요!")
    print("=" * 55)


if __name__ == "__main__":
    main()
