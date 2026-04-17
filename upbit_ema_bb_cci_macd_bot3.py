"""
=============================================================
🚀 업비트 EMA+BB+CCI+MACD 자동매매 봇 (봇 3) v7
=============================================================

📶 노말 매수 조건:
  ① CCI > 0
  ② RSI > 50
  ③ EMA5 > EMA20 > EMA100 (정렬)
  ④ 현재가 > EMA100
  ⑤ EMA100 기울기 > 0.02% (우상향)
  ⑥ BB squeeze 필수 (직전봉 수축 → 현재봉 팽창)
  ⑦ EMA5↔EMA100 이격도 ≤ 2% (추격매수 차단)
  ⑧ BB 과팽창 차단 (현재 BB폭 ≤ 20봉 평균의 1.5배)

📶 노말 매도 전략:
  - 손절: 상승장 -1.5% / 하락장 -1.0%
  - 횡보 손절: 120분 후 +1% 미달
  - 본전 보장: +1.0% 활성 → +0.5% 이하 익절
  - 트레일링: +3.0% 도달 → 바닥 +3% 잠금 / 최고점 -2% 익절
  - 하드 TP: +10% 전량 익절
  - EMA5/20 데드크로스 → 전량 익절
  - EMA100 이탈 → 전량 익절

🚀 surge 매수 조건:
  [기본] EMA5>EMA20 + BB중앙 위 + EMA100 위 + CCI>100 + MACD 골든 + 거래량 3배
  [OR]   EMA5/20 골든 + MACD 골든 + BB상단 돌파 + 전봉 거래량 3배
  [저항] 최근 20봉 저항 돌파 + CCI>100 + 거래량 3배 + EMA100 위
  ※ ETH 5분봉 EMA100 위(bullish) 시에만 허용 / 체결강도 100% 이상

🚀 surge 매도 전략:
  - 손절: -1.5%
  - 트레일링 1차: 최고점 -2% → 50% 익절
  - 불타기: +5% & BB안→상단터치 & EMA20 유지 → 30만원 추가매수
  - 불타기 후 EMA20 이탈 or BB실패+음봉 → 전량 익절
  - 하드 TP: +20% 전량 익절
  - EMA5/20 데드크로스 (2봉 연속 + CCI<0) → 전량 익절

작성일: 2026-04-08
=============================================================
"""

import pyupbit
import time
import datetime
import json
import os
import logging
import logging.handlers
import requests
import threading

# ============================================================
# 📱 텔레그램 알림
# ============================================================

TELEGRAM_TOKEN   = "8520406894:AAF9MbrdmL0lg20rYHpukQfAqJRHc77K3Tc"
TELEGRAM_CHAT_ID = "5849651732"

def send_telegram(msg):
    """텔레그램 알림 전송 (매도 위주만)
    
    미누님 요청: "매도위주로 수익률 실수익 사유 요정도만"
    
    허용: 매도 체결, 시황 긴급, 홈런 발견, 급락 경고
    차단: 그 외 전부 (매수, 바닥잠금, 불타기, 전환, 매집봉 등)
    """
    try:
        # ✅ 매도 관련 + 긴급 + 시황 + 불타기 알림만 허용 (화이트리스트)
        allow_keywords = [
            "매도!", "실수익",           # 매도 체결 (수익률/사유 포함)
            "긴급속보",                  # 시장 전환
            "시황 변동",                 # 모드/BTC 변경
            "대형 홈런",                 # 홈런 발견
            "급락 감지", "하드재난",      # 긴급 경고
            "불타기",                    # 🔥 불타기 매수 (P5 추가)
            "매집→홈런", "추가 매집",     # 전환/물타기 알림 (P5 추가)
        ]
        
        is_allowed = any(kw in msg for kw in allow_keywords)
        if not is_allowed:
            return  # 조용히 스킵
        
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, data={
            "chat_id": TELEGRAM_CHAT_ID,
            "text":    msg,
            "parse_mode": "HTML"
        }, timeout=5)
    except Exception as e:
        logger.error(f"텔레그램 전송 오류: {e}")

# ============================================================
# 📌 설정
# ============================================================

CONFIG = {
    # 🔑 API 키
    "access_key": "oMxJobK2PIH3czfw2h7N4NGn0g9mLjKjBFyUsuet",
    "secret_key": "ZVgk1IPmfdN3bEfvX2xjIA4xbxASAcJOSprZdeGk",

    # 💰 투자 설정
    # ⚠️ 테스트 모드: True = 투자금 1/10 (개발/검증 기간)
    #                False = 실전 투자금
    "test_mode": True,

    "total_invest_krw": 1_000_000,
    "max_coins": 20,
    # 🎯 전략별 슬롯 쿼터 (미누님 통찰 기반 최종)
    # 세력 작전 5단계를 봇이 직접 추적:
    #   1단계 매집찾기 → whale_hunt (긴꼬리 멀티캔들 패턴)
    #   2단계 호가변동 → wakeup (호가창 깨어남)
    #   3단계 펌핑 → normal
    #   4단계 지지시험 → v_reversal
    #   서지는 응급용 (surge는 이미 늦은 매수 -65% 검증)
    "strategy_slots": {
        "whale_hunt":   3,    # 🐋 NEW 세력 매집찾기 (1단계)
        "accumulation": 6,    # 🏦 매집 (기존 시그널 유지)
        "wakeup":       4,    # 🔥 호가깨어남 (2단계)
        "active_watch": 4,    # ⚡ 1분봉 공격 감시 (NEW)
        "normal":       5,    # 📶 노말 (3단계)
        "v_reversal":   2,    # 📈 V자 (4단계)
        # surge는 쿼터 없음 (응급용, 자동 0~2개)
    },
    # test_mode 연동 → True: 5만원 / False: 30만원
    "max_per_coin_krw": 50_000,   # P5: 3만→5만 (시장배율 0.3x 적용 시 12,000원 보장)
    "min_trade_krw": 12_000,      # P5: 10,000→12,000 (50% 익절해도 6,000원 확보, 업비트 5,000원 최소 여유)
    "fee_rate": 0.0005,             # 업비트 수수료 0.05% (매수+매도 = 0.1%)

    # 📊 지표 설정
    "ema_short": 20,
    "ema_long": 55,
    "ema_trend": 100,               # v7 신규: EMA100 추세선 돌파
    "bb_period": 20,
    "bb_std": 2.0,
    "cci_period": 20,
    "cci_threshold": 100,
    "cci_exit": -150,
    "macd_fast": 12,
    "macd_slow": 26,
    "macd_signal": 9,
    "volume_ma_period": 20,         # 20봉 평균
    "volume_surge_mult": 3.0,       # 3배 이상 (완화)

    # 🚨 실시간 급등 감지
    "realtime_surge_mult": 3.0,
    "realtime_volume_period": 5,
    "realtime_top_count": 5,

    # 🎯 normal 코인 익절/손절
    "tp1_pct": 1.5,
    "tp2_pct": 5.0,           # +3% → +5% (더 크게!)
    "stop_loss_pct": -1.5,
    "trailing_stop_pct": -0.5, # -1% → -0.5% (빠르게 잡기)

    # 🚀 surge 코인 익절/손절
    "surge_stop_loss_pct": -1.5,     # 손절 동일
    "surge_trailing_pct": -2.0,      # 트레일링 넓게

    # 🛡️ 본전 보장 (normal만 적용)
    "breakeven_trigger_pct": 1.0,   # +1.0% → 본전 보장 활성
    "breakeven_exit_pct":    0.5,   # +0.5% 이하 → 익절

    # ⏰ 재매수 금지
    "rebuy_block_minutes": 30,
    "max_stoploss_per_day": 2,

    # 📈 코인 선별
    "top_coins_count": 20,
    "top_coins_replace": 3,
    "min_volume_today_billion": 0.1,
    "volume_surge_days": 5,
    "normal_to_surge_pct": 7.0,     # 전일대비 7% 이상 → surge 타입으로 매수

    # ⏰ API 설정
    "check_interval_sec": 30,
    "api_delay_sec": 0.5,
    "candle_interval":        "minute1",   # surge용 (빠른 포착)
    "candle_interval_normal": "minute5",   # 노말용 (5분봉 통일)
    "candle_interval_acc":    "minute5",   # 세력매집용 (중기 매집)
    "candle_interval_v":      "minute5",   # V자반등용 (중기 과매도)
    "candle_count": 100,
    "coin_refresh_min": 5,          # 5분마다 전일대비 상위 20개 재선별
    "coin_replace_min": 30,

    # 🔒 보호 코인
    "protected_coins": [],

    # 🚫 매수 제외 코인 (스테이블 + 대형코인)
    "stable_coins": [
        "KRW-USDT", "KRW-USDC", "KRW-USD1",
        "KRW-DAI", "KRW-BUSD", "KRW-TUSD",
        "KRW-BTC", "KRW-ETH", "KRW-TRX",   # 미누님 요청: 대형 제외
    ],

    # 📁 파일
    "log_file": "bot3_v7_log.txt",
    "trade_history_file": "bot3_v7_history.json",
    "bot_bought_file": "bot3_v7_bought.json",
}

# test_mode에 따라 max_per_coin_krw 자동 조정
# test_mode=True → 3만원 (기본), False → 30만원 (실전)
if not CONFIG["test_mode"]:
    CONFIG["max_per_coin_krw"] = 300_000

# ============================================================
# 📋 로깅
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.handlers.RotatingFileHandler(
            CONFIG["log_file"],
            maxBytes=50 * 1024 * 1024,   # 50MB
            backupCount=3,                # 최근 3개 유지
            encoding="utf-8"
        ),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ============================================================
# 🔧 유틸리티
# ============================================================

def load_json(filepath, default):
    if os.path.exists(filepath):
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    return default


def save_json(filepath, data):
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def safe_api_call(func, *args, retries=3, **kwargs):
    for i in range(retries):
        try:
            time.sleep(CONFIG["api_delay_sec"])
            return func(*args, **kwargs)
        except Exception as e:
            if "Too Many Requests" in str(e) or "RemainingReq" in str(e):
                wait = (i + 1) * 3
                logger.warning(f"⚠️ API 속도 제한 → {wait}초 대기")
                time.sleep(wait)
            else:
                time.sleep(1)
    return None


def get_current_price_safe(ticker):
    return safe_api_call(pyupbit.get_current_price, ticker)


def today_str():
    return datetime.datetime.now().strftime("%Y-%m-%d")


def get_trade_strength(ticker, count=100):
    """
    체결강도 계산
    매수 체결량 / 전체 체결량 × 100
    100% 이상 → 매수세 강함
    100% 미만 → 매도세 강함
    """
    try:
        trades = pyupbit.get_recent_trades(ticker, count=count)
        if not trades or len(trades) == 0:
            return 100.0  # 기본값 100%

        buy_vol  = sum(t['trade_volume'] for t in trades if t['ask_bid'] == 'BID')
        sell_vol = sum(t['trade_volume'] for t in trades if t['ask_bid'] == 'ASK')
        total    = buy_vol + sell_vol

        if total <= 0:
            return 100.0

        strength = (buy_vol / total) * 100
        return round(strength, 1)
    except Exception:
        return 100.0  # 오류 시 기본값


# ============================================================
# 📊 ETH 상태 체크
# ============================================================

def get_dynamic_stop(buy_price, strategy="accumulation"):
    """
    가격대별 동적 손절 기준
    저가 코인일수록 1호가 % 크므로 손절 넓게 적용

    전략별 성향:
      whale_hunt / accumulation / v_reversal → 관대 (저점 매수라 여유)
      wakeup / normal → 중간
      surge → 엄격 (추격매수라 빨리 끊음)
    """
    if buy_price < 10:
        stops = {"accumulation": -5.0, "whale_hunt": -5.0, "v_reversal": -5.0, 
                 "wakeup": -4.5, "normal": -4.0, "surge": -4.0}
    elif buy_price < 100:
        stops = {"accumulation": -8.0, "whale_hunt": -8.0, "v_reversal": -8.0, 
                 "wakeup": -7.5, "normal": -7.0, "surge": -6.0}
    elif buy_price < 500:
        stops = {"accumulation": -6.0, "whale_hunt": -6.0, "v_reversal": -6.0, 
                 "wakeup": -5.5, "normal": -5.0, "surge": -4.0}
    elif buy_price < 10000:
        stops = {"accumulation": -5.0, "whale_hunt": -5.0, "v_reversal": -5.0, 
                 "wakeup": -4.5, "normal": -4.0, "surge": -3.0}
    else:
        stops = {"accumulation": -4.0, "whale_hunt": -4.0, "v_reversal": -4.0, 
                 "wakeup": -3.5, "normal": -3.0, "surge": -2.5}
    return stops.get(strategy, -5.0)


_eth_status_cache = {"value": "bullish", "timestamp": 0}
_market_state_cache = {"value": "neutral", "details": {}, "timestamp": 0}
_eth_status_lock = threading.Lock()

# 🔧 CPU 최적화: API 결과 캐시 (미누님 CPU 75% 해결)
_ema_cache_10m = {}   # {ticker: {"ema20": x, "ema55": x, "bb_upper": x, "ts": time}}
_ema_cache_1m = {}    # {ticker: {"ema5": x, "ema20": x, "ema55": x, "aligned": bool, "ts": time}}
_orderbook_bid_cache = {}  # {ticker: {"ratio": x, "ts": time}}
# 🔧 CPU 최적화 P5: daily_change 캐시 (60초 TTL)
_daily_change_cache = {}  # {ticker: {"value": x, "ts": time}}
# 🔧 CPU 최적화 P5: get_tickers 글로벌 캐시 (5분 TTL)
_tickers_cache = {"tickers": [], "ts": 0}
# 🌊 파동감지 캐시 (5분 TTL, 파동은 느리게 변함)
_wave_cache = {}  # {ticker: {"overheated": bool, "info": {}, "ts": time}}

def get_cached_ema_10m(ticker):
    """10분봉 EMA20/55/BB 캐시 (60초 TTL, API 절약)"""
    import time as _t
    now = _t.time()
    c = _ema_cache_10m.get(ticker)
    if c and now - c["ts"] < 60:
        return c
    try:
        df = safe_api_call(pyupbit.get_ohlcv, ticker, interval="minute10", count=60)
        if df is not None and len(df) >= 56:
            close = df["close"]
            ema20 = close.ewm(span=20, adjust=False).mean().iloc[-1]
            ema55 = close.ewm(span=55, adjust=False).mean().iloc[-1]
            bb_mid = close.rolling(20).mean().iloc[-1]
            bb_std = close.rolling(20).std().iloc[-1]
            bb_upper = bb_mid + 2 * bb_std if bb_std > 0 else 0
            prev_close = close.iloc[-2]
            prev_ema55 = close.ewm(span=55, adjust=False).mean().iloc[-2]
            result = {"ema20": ema20, "ema55": ema55, "bb_upper": bb_upper,
                      "prev_close": prev_close, "prev_ema55": prev_ema55, "ts": now}
            _ema_cache_10m[ticker] = result
            return result
    except Exception:
        pass
    return None

def get_cached_ema_1m(ticker):
    """1분봉 EMA 정배열 캐시 (30초 TTL)"""
    import time as _t
    now = _t.time()
    c = _ema_cache_1m.get(ticker)
    if c and now - c["ts"] < 30:
        return c
    try:
        df = safe_api_call(pyupbit.get_ohlcv, ticker, interval="minute1", count=60)
        if df is not None and len(df) >= 56:
            close = df["close"]
            ema5  = close.ewm(span=5, adjust=False).mean()
            ema20 = close.ewm(span=20, adjust=False).mean()
            ema55 = close.ewm(span=55, adjust=False).mean()
            curr5, curr20, curr55 = ema5.iloc[-1], ema20.iloc[-1], ema55.iloc[-1]
            prev5, prev20 = ema5.iloc[-2], ema20.iloc[-2]
            aligned = (curr5 >= curr20 > curr55)
            golden = (prev5 < prev20 and curr5 >= curr20)
            result = {"ema5": curr5, "ema20": curr20, "ema55": curr55,
                      "aligned": aligned, "golden": golden, 
                      "ok": aligned or golden, "ts": now,
                      "df": df}
            _ema_cache_1m[ticker] = result
            return result
    except Exception:
        pass
    return None

def get_cached_bid_ratio(ticker):
    """호가 매수/매도 비율 캐시 (30초 TTL)"""
    import time as _t
    now = _t.time()
    c = _orderbook_bid_cache.get(ticker)
    if c and now - c["ts"] < 30:
        return c["ratio"]
    try:
        ob = safe_api_call(pyupbit.get_orderbook, ticker)
        if ob and len(ob) > 0:
            units = ob[0].get("orderbook_units", [])
            if units:
                bid_sum = sum(u["bid_size"] * u["bid_price"] for u in units[:5])
                ask_sum = sum(u["ask_size"] * u["ask_price"] for u in units[:5])
                ratio = bid_sum / ask_sum if ask_sum > 0 else 0
                _orderbook_bid_cache[ticker] = {"ratio": ratio, "ts": now}
                return ratio
    except Exception:
        pass
    return 1.0  # 실패 시 통과

def get_cached_daily_change(ticker, ttl=60):
    """🔧 P5: 전일대비 상승률 캐시 (60초 TTL, API 절약)
    TOP25 스캔 + _safe_put_buy + check_position 등에서 반복 호출 방지"""
    import time as _t
    now = _t.time()
    c = _daily_change_cache.get(ticker)
    if c and now - c["ts"] < ttl:
        return c["value"]
    val = get_daily_change(ticker)  # 원본 함수 호출 (캐시 아닌 직접 API)
    _daily_change_cache[ticker] = {"value": val, "ts": now}
    return val

def get_cached_tickers(ttl=300):
    """🔧 P5: pyupbit.get_tickers(fiat='KRW') 글로벌 캐시 (5분 TTL)
    active_watch, v_reversal_wide, select 등에서 중복 호출 방지"""
    import time as _t
    now = _t.time()
    if _tickers_cache["tickers"] and now - _tickers_cache["ts"] < ttl:
        return list(_tickers_cache["tickers"])
    try:
        result = safe_api_call(pyupbit.get_tickers, fiat="KRW") or []
        _tickers_cache["tickers"] = result
        _tickers_cache["ts"] = now
        return list(result)
    except Exception:
        return list(_tickers_cache["tickers"])  # 실패 시 이전 캐시 반환

def detect_wave_overheated(ticker, block_pct=90, min_swing_pct=2.0):
    """
    🌊 엘리엇 파동 + 피보나치 분석 (10분봉 기반, 5분 캐시)
    
    미누님 트레이딩뷰 분석과 같은 눈으로 보기:
    - ZigZag → Swing Point → 파동 카운팅 (1~5파)
    - 피보나치 되돌림: 2파/4파 조정 깊이 (0.382, 0.5, 0.618)
    - 피보나치 확장: 3파/5파 목표가 (1.618, 2.618)
    - 매수 적기 vs 위험 구간 판단
    
    Returns: (is_overheated, info_dict)
      info_dict: {
        wave_phase: "wave1"~"wave5" | "correction2" | "correction4",
        wave_count: 상승파동 수,
        waves: [{type, low, high, size}, ...],
        fib: {wave2_ret, wave3_ext, wave3_target, wave4_ret, wave5_target, ...},
        is_buy_zone: 2파/4파 되돌림 적기,
        is_danger_zone: 3파/5파 고점 근처,
        support: 피보나치 지지선,
        target: 다음 목표가,
      }
    """
    import time as _t
    now = _t.time()
    
    # 10분 캐시 (P5: 5분→10분, 파동은 느리게 변함)
    c = _wave_cache.get(ticker)
    if c and now - c["ts"] < 600:
        return c["overheated"], c["info"]
    
    try:
        df = safe_api_call(pyupbit.get_ohlcv, ticker, interval="minute10", count=60)
        if df is None or len(df) < 20:
            _wave_cache[ticker] = {"overheated": False, "info": {}, "ts": now}
            return False, {}
        
        close = df["close"].values
        highs = df["high"].values
        lows = df["low"].values
        vol = df["volume"].values
        curr = close[-1]
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # Step 1: ZigZag로 Swing Point 추출
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        swings = []
        direction = None
        ext_idx = 0
        ext_price = close[0]
        
        for i in range(1, len(close)):
            if direction is None:
                chg = (close[i] - ext_price) / ext_price * 100
                if chg >= min_swing_pct:
                    swings.append((ext_idx, ext_price, 'L'))
                    direction = 'up'
                    ext_idx, ext_price = i, highs[i]
                elif chg <= -min_swing_pct:
                    swings.append((ext_idx, ext_price, 'H'))
                    direction = 'down'
                    ext_idx, ext_price = i, lows[i]
            elif direction == 'up':
                if highs[i] > ext_price:
                    ext_idx, ext_price = i, highs[i]
                elif (ext_price - lows[i]) / ext_price * 100 >= min_swing_pct:
                    swings.append((ext_idx, ext_price, 'H'))
                    direction = 'down'
                    ext_idx, ext_price = i, lows[i]
            elif direction == 'down':
                if lows[i] < ext_price:
                    ext_idx, ext_price = i, lows[i]
                elif (highs[i] - ext_price) / ext_price * 100 >= min_swing_pct:
                    swings.append((ext_idx, ext_price, 'L'))
                    direction = 'up'
                    ext_idx, ext_price = i, highs[i]
        
        if direction == 'up':
            swings.append((ext_idx, ext_price, 'H'))
        elif direction == 'down':
            swings.append((ext_idx, ext_price, 'L'))
        
        if len(swings) < 4:
            _wave_cache[ticker] = {"overheated": False, "info": {"swings": len(swings)}, "ts": now}
            return False, {"swings": len(swings)}
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # Step 2: 파동 구조 추출 (up/down 교대)
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        waves = []
        for i in range(len(swings) - 1):
            s1, s2 = swings[i], swings[i + 1]
            if s1[2] == 'L' and s2[2] == 'H':
                waves.append({"type": "up", "low": s1[1], "high": s2[1],
                              "size": s2[1] - s1[1], "low_idx": s1[0], "high_idx": s2[0]})
            elif s1[2] == 'H' and s2[2] == 'L':
                waves.append({"type": "down", "high": s1[1], "low": s2[1],
                              "size": s1[1] - s2[1], "high_idx": s1[0], "low_idx": s2[0]})
        
        if not waves:
            _wave_cache[ticker] = {"overheated": False, "info": {}, "ts": now}
            return False, {}
        
        # 상승 트렌드의 시작점 찾기 (가장 큰 상승파 이전의 최저점)
        up_waves = [w for w in waves if w["type"] == "up"]
        if not up_waves:
            _wave_cache[ticker] = {"overheated": False, "info": {}, "ts": now}
            return False, {}
        
        biggest_up = max(up_waves, key=lambda w: w["size"])
        biggest_up_idx = waves.index(biggest_up)
        
        # 가장 큰 상승파 이전의 저점 = 트렌드 시작
        trend_low = biggest_up["low"]
        for w in waves[:biggest_up_idx]:
            if w["type"] == "down" and w["low"] < trend_low:
                trend_low = w["low"]
            if w["type"] == "up" and w["low"] < trend_low:
                trend_low = w["low"]
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # Step 3: 엘리엇 파동 매핑
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # 가장 큰 상승파 = 3파 (엘리엇 핵심 규칙)
        # 그 앞의 상승파 = 1파, 조정 = 2파
        # 그 뒤의 조정 = 4파, 상승 = 5파
        
        fib = {}
        wave1 = wave3 = wave5 = None
        wave2_ret = wave4_ret = None
        
        # 3파 = 가장 큰 상승파
        wave3 = biggest_up
        wave3_high = wave3["high"]
        
        # 1파 찾기: 3파 이전의 첫 번째 상승파
        prior_ups = [w for w in waves[:biggest_up_idx] if w["type"] == "up"]
        if prior_ups:
            wave1 = prior_ups[-1]  # 3파 바로 앞의 상승파
        
        # 2파 찾기: 1파와 3파 사이의 조정파
        if wave1:
            w1_idx = waves.index(wave1)
            between = waves[w1_idx + 1:biggest_up_idx]
            downs_between = [w for w in between if w["type"] == "down"]
            if downs_between:
                wave2_correction = downs_between[0]
                wave2_ret = wave2_correction["size"] / wave1["size"] if wave1["size"] > 0 else 0
                fib["wave2_ret"] = round(wave2_ret, 3)
        
        # 3파 확장 비율 (1파 대비)
        if wave1 and wave1["size"] > 0:
            wave3_ext = wave3["size"] / wave1["size"]
            fib["wave3_ext"] = round(wave3_ext, 3)
            # 3파 피보나치 목표 (1파의 1.618배 확장)
            fib["wave3_target_1618"] = round(wave3["low"] + wave1["size"] * 1.618, 1)
            fib["wave3_target_2618"] = round(wave3["low"] + wave1["size"] * 2.618, 1)
        
        # 4파 찾기: 3파 이후의 조정파
        post_downs = [w for w in waves[biggest_up_idx + 1:] if w["type"] == "down"]
        if post_downs:
            wave4_correction = post_downs[0]
            wave4_ret = wave4_correction["size"] / wave3["size"] if wave3["size"] > 0 else 0
            fib["wave4_ret"] = round(wave4_ret, 3)
            fib["wave4_low"] = wave4_correction["low"]
            # 4파 피보나치 지지선
            fib["wave4_support_382"] = round(wave3_high - wave3["size"] * 0.382, 1)
            fib["wave4_support_500"] = round(wave3_high - wave3["size"] * 0.500, 1)
            fib["wave4_support_618"] = round(wave3_high - wave3["size"] * 0.618, 1)
        
        # 5파 찾기: 4파 이후의 상승파
        post_ups = [w for w in waves[biggest_up_idx + 1:] if w["type"] == "up"]
        if post_ups:
            wave5 = post_ups[0]
            if wave3["size"] > 0:
                fib["wave5_ext"] = round(wave5["size"] / wave3["size"], 3)
            # 5파 목표: 보통 3파의 0.618배 또는 1파와 같은 크기
            if wave1:
                fib["wave5_target_equal"] = round(wave5["low"] + wave1["size"], 1)
            fib["wave5_target_618"] = round(wave5["low"] + wave3["size"] * 0.618, 1) if wave5 else None
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # Step 4: 현재 파동 단계 판단
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        last_wave = waves[-1]
        wave_phase = "unknown"
        
        if wave5:
            if curr >= wave5["low"]:
                wave_phase = "wave5"
            else:
                wave_phase = "post_wave5"
        elif post_downs:  # 4파 진행 중
            if curr <= wave3_high and curr >= post_downs[0]["low"]:
                wave_phase = "correction4"
            elif curr > wave3_high:
                wave_phase = "wave5"
        elif biggest_up_idx == len(waves) - 1:
            # 아직 3파 진행 중 (마지막 파동이 3파)
            wave_phase = "wave3"
        elif wave1 and biggest_up_idx > 0:
            w1_idx = waves.index(wave1)
            if biggest_up_idx == w1_idx + 2:
                wave_phase = "wave3"
            else:
                wave_phase = "wave3"
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # Step 5: 매수 적기 / 위험 구간 판단
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        wave_range = wave3_high - trend_low
        position_pct = (curr - trend_low) / wave_range * 100 if wave_range > 0 else 0
        
        # 📍 매수 적기 = 2파/4파 되돌림 0.382~0.618 구간
        is_buy_zone = False
        support = None
        if wave_phase == "correction4" and fib.get("wave4_support_382"):
            s382 = fib["wave4_support_382"]
            s618 = fib["wave4_support_618"]
            if s618 <= curr <= s382:
                is_buy_zone = True
                support = s382
        elif wave_phase == "correction2" and wave1:
            s382 = wave1["high"] - wave1["size"] * 0.382
            s618 = wave1["high"] - wave1["size"] * 0.618
            if s618 <= curr <= s382:
                is_buy_zone = True
                support = s382
        
        # 🚫 위험 구간 = 3파/5파 고점 90%+ (상승폭 기준)
        is_danger_zone = position_pct >= block_pct
        
        # 다음 목표가
        target = None
        if wave_phase in ("correction4", "wave5"):
            target = fib.get("wave5_target_618") or fib.get("wave5_target_equal")
        elif wave_phase == "wave3":
            target = fib.get("wave3_target_1618")
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # Step 6: 결과 조립
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        up_count = len(up_waves)
        info = {
            "wave_phase": wave_phase,
            "wave_count": up_count,
            "wave3_high": wave3_high,
            "trend_low": trend_low,
            "position_pct": round(position_pct, 1),
            "curr_price": curr,
            "swings": len(swings),
            "fib": fib,
            "is_buy_zone": is_buy_zone,
            "is_danger_zone": is_danger_zone,
            "support": support,
            "target": target,
            "wave1_size": wave1["size"] if wave1 else None,
            "wave3_size": wave3["size"],
            "wave5_size": wave5["size"] if wave5 else None,
        }
        
        # 과열 판단: 상승 2파동+ AND 상승폭 90%+ OR 5파 고점 근처
        is_overheated = (up_count >= 2 and is_danger_zone)
        if wave_phase == "wave5" and position_pct >= 85:
            is_overheated = True  # 5파는 85%부터 차단 (약한 파동)
        
        _wave_cache[ticker] = {"overheated": is_overheated, "info": info, "ts": now}
        return is_overheated, info
    
    except Exception as e:
        logger.debug(f"🌊 파동감지 오류 {ticker}: {e}")
        _wave_cache[ticker] = {"overheated": False, "info": {}, "ts": now}
        return False, {}


def get_market_state():
    """
    🎯 시장 상태 5단계 판단 (미누님 전면 전략 반영)
    
    3지표 조합:
      1. BTC 일변동률 (전일 종가 대비 %)
      2. BTC 4시간봉 EMA20 기울기 (최근 5봉, 20시간)
      3. BTC 1시간봉 RSI (과열/과매도)
    
    5단계:
      🟢🟢 strong_bull : 강한 상승장 (과열 주의)
      🟢 bullish       : 일반 상승장
      ⚪ neutral       : 횡보 (매집 최적기)
      🔴 bearish       : 일반 하락장 (세력 매집 시작)
      🔴🔴 strong_bear: 폭락 (매수 전면 금지)
    
    캐싱: 60초
    반환: (state, details_dict)
    """
    global _market_state_cache
    now_ts = time.time()
    
    # 캐시 유효
    if now_ts - _market_state_cache["timestamp"] < 60:
        return _market_state_cache["value"], _market_state_cache["details"]
    
    with _eth_status_lock:
        if now_ts - _market_state_cache["timestamp"] < 60:
            return _market_state_cache["value"], _market_state_cache["details"]
        
        try:
            # 1. BTC 일변동률
            df_day = safe_api_call(pyupbit.get_ohlcv, "KRW-BTC", 
                                   interval="day", count=2)
            if df_day is None or len(df_day) < 2:
                return _market_state_cache["value"], _market_state_cache["details"]
            
            prev_close = df_day["close"].iloc[-2]
            current = get_current_price_safe("KRW-BTC")
            if not current or prev_close <= 0:
                return _market_state_cache["value"], _market_state_cache["details"]
            
            daily_change = (current - prev_close) / prev_close * 100
            
            # 2. BTC 4시간봉 EMA20 기울기
            df_4h = safe_api_call(pyupbit.get_ohlcv, "KRW-BTC",
                                  interval="minute240", count=30)
            ema_slope = 0
            if df_4h is not None and len(df_4h) >= 25:
                ema20_4h = df_4h["close"].ewm(span=20, adjust=False).mean()
                if ema20_4h.iloc[-5] > 0:
                    ema_slope = (ema20_4h.iloc[-1] - ema20_4h.iloc[-5]) / ema20_4h.iloc[-5] * 100
            
            # 3. BTC 1시간봉 RSI
            df_1h = safe_api_call(pyupbit.get_ohlcv, "KRW-BTC",
                                  interval="minute60", count=20)
            rsi = 50
            if df_1h is not None and len(df_1h) >= 15:
                delta = df_1h["close"].diff()
                gain = delta.where(delta > 0, 0).rolling(14).mean()
                loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
                rs = gain.iloc[-1] / loss.iloc[-1] if loss.iloc[-1] > 0 else 100
                rsi = 100 - (100 / (1 + rs))
            
            # 5단계 판정
            # strong_bull: 강한 상승 (일 +2%+ AND EMA 상승 AND RSI < 75)
            if daily_change >= 2.0 and ema_slope >= 0.3 and rsi < 75:
                state = "strong_bull"
            # bullish: 일반 상승 (일 0~+2% AND EMA 상승/수평)
            elif daily_change >= 0 and ema_slope >= -0.2:
                state = "bullish"
            # strong_bear: 폭락 (일 -2% 이하 AND EMA 급락)
            elif daily_change <= -2.0 and ema_slope <= -0.3:
                state = "strong_bear"
            # bearish: 일반 하락 (일 -2~0% OR EMA 하락)
            elif daily_change < 0 and ema_slope < 0:
                state = "bearish"
            # neutral: 횡보
            else:
                state = "neutral"
            
            details = {
                "daily_change": round(daily_change, 2),
                "ema_slope":    round(ema_slope, 2),
                "rsi":          round(rsi, 1),
                "btc_price":    current,
            }
            
            _market_state_cache["value"]     = state
            _market_state_cache["details"]   = details
            _market_state_cache["timestamp"] = now_ts
            
            return state, details
        except Exception as e:
            logger.debug(f"시장 상태 판단 오류: {e}")
            _market_state_cache["timestamp"] = now_ts
            return _market_state_cache["value"], _market_state_cache["details"]


# 🎯 시장 상태 × 전략별 투자 배율 테이블
# 미누님 철학: 하락/횡보장에서 whale_hunt 전성기, 강상승장에서 추격 자제
MARKET_STRATEGY_MATRIX = {
    # strategy:      strong_bull  bullish  neutral  bearish  strong_bear
    "surge":        {"strong_bull": 1.0, "bullish": 0.8, "neutral": 0.0, "bearish": 0.0, "strong_bear": 0.0},
    "normal":       {"strong_bull": 0.8, "bullish": 1.0, "neutral": 0.5, "bearish": 0.0, "strong_bear": 0.0},
    "accumulation": {"strong_bull": 0.5, "bullish": 0.8, "neutral": 1.0, "bearish": 0.8, "strong_bear": 0.0},
    "whale_hunt":   {"strong_bull": 0.0, "bullish": 0.5, "neutral": 1.0, "bearish": 1.0, "strong_bear": 0.5},
    "wakeup":       {"strong_bull": 0.5, "bullish": 0.8, "neutral": 1.0, "bearish": 0.8, "strong_bear": 0.0},
    "active_watch": {"strong_bull": 1.0, "bullish": 1.0, "neutral": 1.0, "bearish": 0.5, "strong_bear": 0.5},
    "v_reversal":   {"strong_bull": 0.8, "bullish": 1.0, "neutral": 0.8, "bearish": 0.5, "strong_bear": 0.0},
}


def get_strategy_scale(buy_type):
    """
    🎯 현재 시장 상태 기반 전략별 투자 배율 반환
    
    반환: (scale, state, details)
      scale: 0.0 ~ 1.0 (0 = 매수 금지)
      state: strong_bull / bullish / neutral / bearish / strong_bear
      details: {daily_change, ema_slope, rsi, btc_price}
    """
    state, details = get_market_state()
    matrix = MARKET_STRATEGY_MATRIX.get(buy_type, {})
    scale = matrix.get(state, 0.5)  # 알 수 없으면 50%
    return scale, state, details


def get_eth_status():
    """
    📊 [레거시 호환] 기존 이분법 (bullish/bearish) 
    → 내부적으로 get_market_state() 사용
    
    - strong_bull, bullish, neutral → "bullish" (매수 가능 구간)
    - bearish, strong_bear → "bearish" (매수 제한 구간)
    
    ⚠️ 새 코드는 get_market_state() 직접 사용 권장
    """
    state, _ = get_market_state()
    if state in ("strong_bull", "bullish", "neutral"):
        return "bullish"
    else:
        return "bearish"


# ============================================================
# 🕐 시간대별 전략 모드 (전역 - 미누님 4시간봉 기반)
# ============================================================
# 09~20시: 🔥 공격 모드 / 20~00시: 📶 노멀 / 00~08시: 🏦 매집

HOUR_ATTACK_TIMES = [9, 12, 15, 18]

def get_time_mode():
    """🕐 현재 시간대 모드: attack/closing/normal/accumulate"""
    import datetime as _dt
    now = _dt.datetime.now()
    hour = now.hour
    minute = now.minute
    
    if 9 <= hour < 19:
        return "attack"       # 🔥 공격 (홈런 사냥, 09:00~19:00)
    elif 19 <= hour < 20:
        return "closing"      # 🏠 퇴근정리 (19:00~20:00) ← NEW!
    elif 20 <= hour < 24:
        return "normal"       # 📶 노멀
    else:  # 0 ~ 8
        if hour == 8 and minute >= 30:
            return "attack"   # 08:30부터 공격 준비
        return "accumulate"   # 🏦 매집 (00:00~08:30)

TIME_MODE_CONFIG = {
    "attack": {
        "label":          "🔥공격",
        "active_trigger": 1.5,
        "grace_enabled":  False,
        "quality_strict": False,
        "pre_hour_top":   30,
        "scan_interval":  10,     # active 감시 10초 (CPU 최적화)
        "min_daily_value": 5_000_000_000,  # 🔥 거래대금 50억+ 만 매수 (잡코인 감시만)
        # 🔥 공격 모드: 매집 0 / 홈런 풀가동 (미누님 전략)
        # "주간 상승장에서는 매집 필요없어. 전체 급등코인으로 전환!"
        "slots_override": {
            "whale_hunt": 3, "accumulation": 0, "wakeup": 4,
            "active_watch": 6, "normal": 3, "v_reversal": 2,
        },
    },
    "normal": {
        "label":          "📶노멀",
        "active_trigger": 2.0,
        "grace_enabled":  True,
        "quality_strict": True,
        "pre_hour_top":   10,
        "scan_interval":  15,
        "slots_override": {
            "whale_hunt": 3, "accumulation": 3, "wakeup": 4,
            "active_watch": 4, "normal": 5, "v_reversal": 2,
        },
    },
    # 🏠 퇴근정리 (19:00~20:00) — 세력 퇴근 대응
    # 미누님: "7시 이후에는 낮에 잡았던 공격적 코인들을 정리하는 시간"
    # 신규 매수 중단 + 타임아웃 2시간 + 바닥잠금 강화
    "closing": {
        "label":          "🏠퇴근정리",
        "active_trigger": 99.0,  # 트리거 불가 = 신규 매수 차단
        "grace_enabled":  True,
        "quality_strict": True,
        "pre_hour_top":   0,     # 정각 부스터 비활성
        "scan_interval":  60,    # 감시 느리게 (매수 안 함)
        "timeout_hours":  2.0,   # 4시간 → 2시간 타임아웃 (빠른 정리)
        "breakeven_floor": 0.5,  # 바닥잠금 +0.3% → +0.5% 강화
        "no_new_buy":     True,  # 신규 매수 차단 플래그
        "slots_override": {
            "whale_hunt": 0, "accumulation": 0, "wakeup": 0,
            "active_watch": 0, "normal": 0, "v_reversal": 0,
        },
    },
    "accumulate": {
        "label":          "🏦매집",
        "active_trigger": 2.5,
        "grace_enabled":  True,
        "quality_strict": True,
        "pre_hour_top":   0,
        "scan_interval":  30,
        # 🏦 매집 모드: 매집 풀가동 / 나머지 축소
        # "매집하고 2% 먹는 건 저녁 12시~아침 8시30분"
        "slots_override": {
            "whale_hunt": 4, "accumulation": 8, "wakeup": 2,
            "active_watch": 2, "normal": 3, "v_reversal": 1,
        },
    },
}

def get_dynamic_slots():
    """시간대별 동적 슬롯 쿼터 반환"""
    mode = get_time_mode()
    cfg = TIME_MODE_CONFIG.get(mode, {})
    return cfg.get("slots_override", CONFIG.get("strategy_slots", {}))

TIME_MARKET_MULTIPLIER = {
    # 🔥 공격 모드 (08:30~20:00)
    # 상승장 = 풀공격 / 횡보 50:50 / 하락 30:70
    "attack": {
        "strong_bull": 1.2, "bullish": 1.2,
        "neutral": 0.5, "bearish": 0.3, "strong_bear": 0.3,
    },
    # 📶 노멀 (20:00~00:00)
    "normal": {
        "strong_bull": 0.8, "bullish": 0.8,
        "neutral": 0.5, "bearish": 0.3, "strong_bear": 0.0,
    },
    # 🏠 퇴근정리 (19:00~20:00) — 신규 매수 거의 없음
    "closing": {
        "strong_bull": 0.3, "bullish": 0.3,
        "neutral": 0.0, "bearish": 0.0, "strong_bear": 0.0,
    },
    # 🏦 매집 (00:00~08:30)
    # 강세 50:50 / 하락 30:70
    "accumulate": {
        "strong_bull": 0.5, "bullish": 0.5,
        "neutral": 0.4, "bearish": 0.3, "strong_bear": 0.0,
    },
}

def is_pre_hour_window():
    """정각 임박 (XX:50~XX:59) - 공격 모드에서만"""
    import datetime as _dt
    now = _dt.datetime.now()
    if get_time_mode() != "attack":
        return False
    next_hour = (now.hour + 1) % 24
    return next_hour in HOUR_ATTACK_TIMES and now.minute >= 50

def is_hour_explosion_window():
    """정각 폭발 (XX:00~XX:05) - 공격 모드에서만"""
    import datetime as _dt
    now = _dt.datetime.now()
    if get_time_mode() != "attack":
        return False
    return now.hour in HOUR_ATTACK_TIMES and now.minute < 5


# ============================================================
# 📊 지표 계산
# ============================================================

def get_signal(ticker):
    try:
        # ── 일봉 EMA20 돌파 필터 ──
        # 전일 종가가 EMA20 아래 → 현재가가 EMA20 위 (돌파 순간!)
        # ENA처럼 EMA20 아래 횡보 차단 / ORDER처럼 돌파 순간 포착
        df_daily = safe_api_call(
            pyupbit.get_ohlcv, ticker,
            interval="day", count=60
        )
        if df_daily is None or len(df_daily) < 55:
            return None, {}
        daily_ema20      = df_daily["close"].ewm(span=20, adjust=False).mean()
        curr_daily_price = df_daily["close"].iloc[-1]
        curr_ema20_daily = daily_ema20.iloc[-1]

        # 현재가가 일봉 EMA20 위에 있어야 함
        # + 전일 종가가 EMA20 아래였으면 더 강한 신호 (돌파 순간)
        if curr_daily_price <= curr_ema20_daily:
            return None, {}  # 일봉 EMA20 아래 → 차단

        df = safe_api_call(
            pyupbit.get_ohlcv, ticker,
            interval=CONFIG["candle_interval"],
            count=CONFIG["candle_count"]
        )
        if df is None or len(df) < 60:
            return None, {}

        close  = df["close"]
        high   = df["high"]
        low    = df["low"]
        volume = df["volume"]

        ema20    = close.ewm(span=CONFIG["ema_short"], adjust=False).mean()
        ema100   = close.ewm(span=CONFIG["ema_trend"], adjust=False).mean()
        bb_mid   = close.rolling(window=CONFIG["bb_period"]).mean()
        bb_std   = close.rolling(window=CONFIG["bb_period"]).std()
        bb_upper = bb_mid + CONFIG["bb_std"] * bb_std

        tp      = (high + low + close) / 3
        tp_mean = tp.rolling(window=CONFIG["cci_period"]).mean()
        tp_std  = tp.rolling(window=CONFIG["cci_period"]).std()
        cci     = (tp - tp_mean) / (0.015 * tp_std)

        ema_fast    = close.ewm(span=CONFIG["macd_fast"],    adjust=False).mean()
        ema_slow    = close.ewm(span=CONFIG["macd_slow"],    adjust=False).mean()
        macd_line   = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=CONFIG["macd_signal"], adjust=False).mean()

        volume_ma = volume.rolling(window=CONFIG["volume_ma_period"]).mean()

        curr_close    = close.iloc[-1]
        curr_ema20    = ema20.iloc[-1]
        curr_ema100   = ema100.iloc[-1]
        prev_ema100   = ema100.iloc[-2]
        curr_bb_upper = bb_upper.iloc[-1]
        curr_cci      = cci.iloc[-1]
        prev_cci      = cci.iloc[-2]
        curr_macd     = macd_line.iloc[-1]
        curr_sig      = signal_line.iloc[-1]
        prev_macd     = macd_line.iloc[-2]
        prev_sig      = signal_line.iloc[-2]
        curr_volume   = volume.iloc[-1]
        curr_vol_ma   = volume_ma.iloc[-1]

        ema5_s          = close.ewm(span=5,  adjust=False).mean()
        ema_bullish     = ema5_s.iloc[-1] > curr_ema20          # EMA5 > EMA20
        vol_surge       = curr_volume > curr_vol_ma * CONFIG["volume_surge_mult"]
        bb_mid_above    = curr_close > bb_mid.iloc[-1]
        bb_upper_break  = curr_close > curr_bb_upper
        bb_breakout     = bb_mid_above and (bb_upper_break or vol_surge)
        ema100_above    = curr_close > curr_ema100
        ema100_breakout = ema100_above and close.iloc[-2] <= prev_ema100
        cci_above       = curr_cci > CONFIG["cci_threshold"]
        cci_breakout    = prev_cci <= CONFIG["cci_threshold"] and curr_cci > CONFIG["cci_threshold"]
        macd_golden     = prev_macd <= prev_sig and curr_macd > curr_sig
        macd_bullish    = curr_macd > curr_sig

        # ── 🚀 surge OR 조건: EMA5/20 골든 + BB상단 + 전봉거래량 3배 ──
        ema5_golden     = ema5_s.iloc[-1] > curr_ema20           # EMA5 > EMA20 (골든상태)
        prev_vol_surge  = volume.iloc[-2] > curr_vol_ma * CONFIG["volume_surge_mult"]
        surge_alt_signal = (
            ema_bullish and          # EMA5 > EMA20
            ema5_golden and          # EMA5 > EMA20 골든상태
            macd_bullish and         # MACD 골든상태
            bb_upper_break and       # BB 상단 돌파
            prev_vol_surge           # 전봉 거래량 3배
        )

        vol_ratio = round(curr_volume / curr_vol_ma, 2) if curr_vol_ma > 0 else 0

        info = {
            "ema20":           round(curr_ema20, 4),
            "ema100":          round(curr_ema100, 4),
            "bb_upper":        round(curr_bb_upper, 4),
            "cci":             round(curr_cci, 2),
            "macd_golden":     macd_golden,
            "cci_breakout":    cci_breakout,
            "ema100_breakout": ema100_breakout,
            "vol_ratio":       vol_ratio,
        }

        # ── 🔍 10분봉 EMA100 위 확인 (추세 필터 — 노말과 동일) ──
        try:
            df_10m = safe_api_call(
                pyupbit.get_ohlcv, ticker,
                interval="minute5", count=110
            )
            if df_10m is not None and len(df_10m) >= 105:
                close_10m  = df_10m["close"]
                ema100_10m = close_10m.ewm(span=100, adjust=False).mean()
                above_10m  = close_10m.iloc[-1] > ema100_10m.iloc[-1]
                slope_10m  = (ema100_10m.iloc[-1] - ema100_10m.iloc[-6]) / ema100_10m.iloc[-6] * 100
                rising_10m = slope_10m > 0.0
            else:
                above_10m  = True
                rising_10m = True
        except Exception:
            above_10m  = True
            rising_10m = True

        if not (above_10m and rising_10m):
            return None, info

        # ── 🔴 RSI 과매수 필터 (고점 추격 차단) ──
        try:
            # 1분봉 RSI
            delta_1m = close.diff()
            gain_1m  = delta_1m.clip(lower=0).rolling(window=14).mean()
            loss_1m  = (-delta_1m.clip(upper=0)).rolling(window=14).mean()
            rs_1m    = gain_1m / loss_1m
            rsi_1m   = (100 - (100 / (1 + rs_1m))).iloc[-1]

            # 10분봉 RSI
            if df_10m is not None and len(df_10m) >= 20:
                delta_10m = df_10m["close"].diff()
                gain_10m  = delta_10m.clip(lower=0).rolling(window=14).mean()
                loss_10m  = (-delta_10m.clip(upper=0)).rolling(window=14).mean()
                rs_10m    = gain_10m / loss_10m
                rsi_10m   = (100 - (100 / (1 + rs_10m))).iloc[-1]
            else:
                rsi_10m = 50

            if rsi_1m > 85 or rsi_10m > 80:
                logger.debug(
                    f"🚫 [surge 과매수차단] {ticker.replace('KRW-','')} | "
                    f"1분RSI: {rsi_1m:.1f} | 10분RSI: {rsi_10m:.1f} → 고점 추격 금지"
                )
                return None, info
        except Exception:
            pass

        # ── 전 저항선 돌파 감지 (최근 20봉 최고가) ──
        resistance      = high.iloc[-21:-1].max()
        resist_breakout = curr_close > resistance

        # ── 저항선 돌파 시 3개 조건만으로 선매수 ──
        if resist_breakout:
            early_signal = (
                cci_above and
                vol_surge and
                ema100_above
            )
            if early_signal:
                logger.info(
                    f"🔥 [저항돌파] {ticker.replace('KRW-','')} | "
                    f"저항: {resistance:.4f} → 현재: {curr_close:.4f} | "
                    f"CCI: {curr_cci:.1f} | 거래량: {vol_ratio}배"
                )
                return "strong_buy", info

        # ── 🚀 surge OR 조건 충족 시 strong_buy ──
        if surge_alt_signal:
            logger.info(
                f"🚀 [EMA5/20골든] {ticker.replace('KRW-','')} | "
                f"EMA5:{ema5_s.iloc[-1]:.2f} EMA20:{curr_ema20:.2f} | "
                f"BB상단돌파 | 전봉거래량: {round(volume.iloc[-2]/curr_vol_ma,1)}배"
            )
            return "strong_buy", info

        # 기존 조건
        buy_signal = (
            ema_bullish and
            bb_breakout and
            ema100_above and
            cci_above and
            macd_bullish and
            vol_surge
        )
        # 강한 신호: EMA100 돌파 순간 or MACD 골든크로스 or CCI 돌파
        strong = buy_signal and (ema100_breakout or macd_golden or cci_breakout)

        if strong:
            return "strong_buy", info
        elif buy_signal:
            return "buy", info
        return None, info

    except Exception as e:
        logger.error(f"지표 계산 오류 {ticker}: {e}")
        return None, {}


def get_normal_signal(ticker):
    """
    📈 새 노말 매수 신호 (AQT 스타일 중기 추세 전략)
    ① CCI > 0
    ② RSI > 50
    ③ EMA5 > EMA20 > EMA100
    ④ 현재가 > EMA100 위 or 돌파 순간
    ⑤ EMA100 기울기 > 0 (수평 제외)
    ⑥ BB 수축→팽창 순간
    ⑦ EMA 이격도 벌어지는 중
    → 10분봉 기준 (스윙 추세 추종)
    """
    try:
        df = safe_api_call(
            pyupbit.get_ohlcv, ticker,
            interval=CONFIG["candle_interval_normal"], count=120
        )
        if df is None or len(df) < 110:
            return None, {}

        close  = df["close"]
        high   = df["high"]
        low    = df["low"]

        # EMA 계산
        ema5   = close.ewm(span=5,   adjust=False).mean()
        ema20  = close.ewm(span=20,  adjust=False).mean()
        ema100 = close.ewm(span=100, adjust=False).mean()

        # BB 계산
        bb_mid = close.rolling(window=20).mean()
        bb_std = close.rolling(window=20).std()
        bb_up  = bb_mid + 2 * bb_std
        bb_dn  = bb_mid - 2 * bb_std
        bb_width = bb_up - bb_dn  # BB 폭

        # CCI 계산
        tp      = (high + low + close) / 3
        tp_mean = tp.rolling(window=20).mean()
        tp_std  = tp.rolling(window=20).std()
        cci     = (tp - tp_mean) / (0.015 * tp_std)

        # RSI 계산
        delta  = close.diff()
        gain   = delta.clip(lower=0).rolling(window=14).mean()
        loss   = (-delta.clip(upper=0)).rolling(window=14).mean()
        rs     = gain / loss
        rsi    = 100 - (100 / (1 + rs))

        curr_close   = close.iloc[-1]
        prev_close   = close.iloc[-2]
        curr_ema5    = ema5.iloc[-1]
        curr_ema20   = ema20.iloc[-1]
        curr_ema100  = ema100.iloc[-1]
        prev_ema100  = ema100.iloc[-2]
        curr_cci     = cci.iloc[-1]
        curr_rsi     = rsi.iloc[-1]

        # ① CCI > 0
        cci_ok = curr_cci > 0

        # ② RSI > 50
        rsi_ok = curr_rsi > 50

        # ③ EMA5 > EMA20 > EMA100
        ema_aligned = curr_ema5 > curr_ema20 > curr_ema100

        # ④ 현재가 EMA100 위 (이미 위에 있거나 돌파 순간 모두 허용)
        ema100_above    = curr_close > curr_ema100  # EMA100 위
        ema100_breakout = (curr_close > curr_ema100 and prev_close <= prev_ema100)  # 돌파 순간

        # ⑤ EMA100 기울기 > 0 (수평 제외 - 5봉 전과 비교)
        ema100_5ago  = ema100.iloc[-6]
        ema100_slope = (curr_ema100 - ema100_5ago) / ema100_5ago * 100
        ema100_rising = ema100_slope > 0.02  # 0.02% 이상 기울기

        # ⑥ BB 수축→팽창 (최근 5봉 최솟값이 직전봉, 현재 팽창 중)
        bb_width_min  = bb_width.iloc[-10:-1].min()  # 최근 10봉 중 최솟값
        bb_contracted = bb_width.iloc[-2] <= bb_width_min * 1.05  # 직전봉 수축
        bb_expanding  = bb_width.iloc[-1] > bb_width.iloc[-2]     # 현재 팽창 중
        bb_squeeze    = bb_contracted and bb_expanding

        # ⑦ EMA 이격도 벌어지는 중
        gap_cur  = curr_ema5 - curr_ema100
        gap_prev = ema5.iloc[-2] - ema100.iloc[-2]
        gap_widening = gap_cur > gap_prev > 0

        # ⑧ EMA5↔EMA100 이격도 과열 필터 (2% 초과 → 추격매수 차단)
        ema5_ema100_gap_pct = (curr_ema5 - curr_ema100) / curr_ema100 * 100
        ema100_not_overextended = ema5_ema100_gap_pct <= 2.0

        # ⑨ BB 과팽창 필터 (평균 BB폭의 1.5배 이상 → 이미 벌어진 상태 차단)
        bb_width_avg      = bb_width.iloc[-20:].mean()
        bb_not_overexpanded = bb_width.iloc[-1] <= bb_width_avg * 1.5

        # ⑩ 10분봉 EMA100 위 확인 (추세 필터 — 핵심!)
        try:
            df_10m = safe_api_call(
                pyupbit.get_ohlcv, ticker,
                interval="minute5", count=110
            )
            if df_10m is not None and len(df_10m) >= 105:
                close_10m   = df_10m["close"]
                ema100_10m  = close_10m.ewm(span=100, adjust=False).mean()
                above_10m   = close_10m.iloc[-1] > ema100_10m.iloc[-1]
                slope_10m   = (ema100_10m.iloc[-1] - ema100_10m.iloc[-6]) / ema100_10m.iloc[-6] * 100
                rising_10m  = slope_10m > 0.0
                # 10분봉 RSI
                delta_10m   = close_10m.diff()
                gain_10m    = delta_10m.clip(lower=0).rolling(window=14).mean()
                loss_10m    = (-delta_10m.clip(upper=0)).rolling(window=14).mean()
                rs_10m      = gain_10m / loss_10m
                rsi_10m_val = (100 - (100 / (1 + rs_10m))).iloc[-1]
            else:
                above_10m   = True
                rising_10m  = True
                rsi_10m_val = 50
        except Exception:
            above_10m   = True
            rising_10m  = True
            rsi_10m_val = 50

        # ⑪ RSI 과매수 필터 (고점 추격 차단)
        # 1분봉 RSI > 75 or 10분봉 RSI > 72 → 매수 금지
        if curr_rsi > 75 or rsi_10m_val > 72:
            logger.debug(
                f"🚫 [노말 과매수차단] {ticker.replace('KRW-','')} | "
                f"10분RSI: {curr_rsi:.1f} | 10분RSI(추가): {rsi_10m_val:.1f} → 고점 추격 금지"
            )
            return None, {}

        # 최종 신호 (BB squeeze 필수 + 이격도 필터 + 과팽창 필터 + 10분봉 EMA100 필터)
        buy_signal = (
            cci_ok and
            rsi_ok and
            ema_aligned and
            ema100_above and
            ema100_rising and
            bb_squeeze and              # ← 막 벌어지는 순간만 (필수)
            ema100_not_overextended and # ← EMA5↔EMA100 이격도 3% 이내
            bb_not_overexpanded and     # ← BB 과팽창 차단
            above_10m and              # ← 10분봉 EMA100 위 (핵심 추세 필터)
            rising_10m                 # ← 10분봉 EMA100 기울기 상승
        )

        # 강한 신호: 이격도 벌어지는 중 OR EMA100 돌파 순간
        strong = buy_signal and (gap_widening or ema100_breakout)

        info = {
            "cci":              round(curr_cci, 1),
            "rsi":              round(curr_rsi, 1),
            "ema5":             round(curr_ema5, 4),
            "ema20":            round(curr_ema20, 4),
            "ema100":           round(curr_ema100, 4),
            "slope":            round(ema100_slope, 3),
            "bb_squeeze":       bb_squeeze,
            "gap_widening":     gap_widening,
            "ema100_breakout":  ema100_breakout,
            "ema5_gap_pct":     round(ema5_ema100_gap_pct, 1),  # 이격도 로그용
        }

        if strong:
            return "strong_buy", info
        elif buy_signal:
            return "buy", info
        return None, info

    except Exception as e:
        logger.error(f"노말 신호 오류 {ticker}: {e}")
        return None, {}


# 🔧 P5: 매집 신호 캐시 (60초 TTL, 5분봉이라 60초 안에 안 바뀜)
_acc_signal_cache = {}  # {ticker: {"result": (signal, info), "ts": time}}

def get_accumulation_signal(ticker):
    """
    🏦 세력 매집 감지
    - BB 수축 중 (조용히 모으는 중)
    - 거래량 평균보다 낮음
    - EMA100 근처 완만한 횡보/우상향
    - CCI 중립권
    → 5분봉 기준 (고점 착시 방지)
    → 🔧 P5: 60초 캐시 (scan_thread에서 15초마다 호출 → 60초 1회로 축소)
    """
    import time as _t
    now = _t.time()
    c = _acc_signal_cache.get(ticker)
    if c and now - c["ts"] < 60:
        return c["result"]
    
    try:
        df = safe_api_call(pyupbit.get_ohlcv, ticker,
                           interval=CONFIG["candle_interval_acc"], count=120)
        if df is None or len(df) < 110:
            return None, {}

        close  = df["close"]
        high   = df["high"]
        low    = df["low"]
        volume = df["volume"]

        ema5   = close.ewm(span=5,   adjust=False).mean()
        ema20  = close.ewm(span=20,  adjust=False).mean()
        ema100 = close.ewm(span=100, adjust=False).mean()

        bb_mid = close.rolling(20).mean()
        bb_std = close.rolling(20).std()
        bb_up  = bb_mid + 2 * bb_std
        bb_dn  = bb_mid - 2 * bb_std
        bb_width = bb_up - bb_dn

        tp      = (high + low + close) / 3
        tp_mean = tp.rolling(20).mean()
        tp_std  = tp.rolling(20).std()
        cci     = (tp - tp_mean) / (0.015 * tp_std)

        vol_ma = volume.rolling(20).mean()

        curr_close  = close.iloc[-1]
        curr_ema5   = ema5.iloc[-1]
        curr_ema20  = ema20.iloc[-1]
        curr_ema100 = ema100.iloc[-1]
        curr_cci    = cci.iloc[-1]
        curr_vol    = volume.iloc[-1]
        curr_vol_ma = vol_ma.iloc[-1]

        # ① BB 수축 중 (최근 10봉 평균보다 좁아짐)
        bb_avg       = bb_width.iloc[-20:].mean()
        bb_narrow    = bb_width.iloc[-1] < bb_avg * 0.85   # 평균보다 15% 이상 좁음
        bb_shrinking = bb_width.iloc[-1] < bb_width.iloc[-5]  # 5봉 전보다 수축 중

        # ② 거래량 낮음 (조용히 매집)
        vol_quiet = curr_vol_ma > 0 and curr_vol < curr_vol_ma * 0.8

        # ③ EMA100 근처 (이격도 3% 이내)
        near_ema100 = abs(curr_close - curr_ema100) / curr_ema100 * 100 < 3.0

        # ④ 완만한 우상향 (EMA5 > EMA20 > EMA100)
        mild_uptrend = curr_ema5 > curr_ema20 > curr_ema100

        # ⑤ CCI 중립권 (-100 ~ +100)
        cci_neutral = -100 < curr_cci < 100

        # ⑥ 10분봉 EMA100 위
        try:
            df_10m = safe_api_call(pyupbit.get_ohlcv, ticker, interval="minute5", count=110)
            if df_10m is not None and len(df_10m) >= 105:
                c10 = df_10m["close"]
                e10 = c10.ewm(span=100, adjust=False).mean()
                above_10m = c10.iloc[-1] > e10.iloc[-1]
            else:
                above_10m = True
        except Exception:
            above_10m = True

        signal = (bb_narrow and bb_shrinking and vol_quiet and
                  near_ema100 and mild_uptrend and cci_neutral and above_10m)

        info = {
            "cci":        round(curr_cci, 1),
            "bb_narrow":  bb_narrow,
            "vol_quiet":  round(curr_vol / curr_vol_ma, 2) if curr_vol_ma > 0 else 0,
            "near_ema100": round(abs(curr_close - curr_ema100) / curr_ema100 * 100, 2),
        }

        if signal:
            _acc_signal_cache[ticker] = {"result": ("accumulation", info), "ts": now}
            return "accumulation", info
        _acc_signal_cache[ticker] = {"result": (None, info), "ts": now}
        return None, info

    except Exception as e:
        logger.error(f"세력매집 신호 오류 {ticker}: {e}")
        _acc_signal_cache[ticker] = {"result": (None, {}), "ts": now}
        return None, {}



def get_v_reversal_signal(ticker):
    """
    📈 V자 반등 매수 신호 (투매 캡튤레이션 + BB하단 이탈 + 반등 패턴)
    → 5분봉 기준, 하락장 생존 전략
    
    ⚾ 공격 모드 완화 (미누님 요청: 하락장 V자 0회 → 더 자주 잡기)
    
    조건:
      ① EMA100 아래 이격 -1% ~ -8% (유지)
      ② 3봉 전: 긴 음봉 (평균 body 2배+) + CCI<-120 + RSI<40 (완화: -150/35)
      ③ 3봉 전 거래량 평균 × 2.0 이상 (완화: 2.5 → 2.0)
      ④ 3봉 전 저가가 BB 하단 이탈 (유지)
      ⑤ 이후 2개 양봉 합산 body > 음봉 body (유지)
      ⑥ CCI가 저점 대비 +80 이상 회복 (완화: +100 → +80)
      ⑦ RSI 30 이상 회복 (유지)
      ⑧ 현재가 > 음봉 종가 (유지)
      ⑨ 5분봉 기준 -12% 이내 (완화: -10% → -12%)
      ⑩ EMA100 기울기 -0.2% 이상 (완화: -0.1% → -0.2%)
    """
    try:
        df = safe_api_call(pyupbit.get_ohlcv, ticker,
                           interval=CONFIG["candle_interval_v"], count=120)
        if df is None or len(df) < 110:
            return None, {}

        close  = df["close"]
        open_  = df["open"]
        high   = df["high"]
        low    = df["low"]
        volume = df["volume"]

        ema100 = close.ewm(span=100, adjust=False).mean()

        # BB(20,2) 계산
        bb_mid   = close.rolling(20).mean()
        bb_std   = close.rolling(20).std()
        bb_lower = bb_mid - 2.0 * bb_std

        # CCI 계산
        tp      = (high + low + close) / 3
        tp_mean = tp.rolling(20).mean()
        tp_std  = tp.rolling(20).std()
        cci     = (tp - tp_mean) / (0.015 * tp_std)

        # RSI 계산
        delta   = close.diff()
        gain    = delta.clip(lower=0).rolling(14).mean()
        loss    = (-delta.clip(upper=0)).rolling(14).mean()
        rs      = gain / loss
        rsi     = 100 - (100 / (1 + rs))

        curr_close  = close.iloc[-1]
        curr_ema100 = ema100.iloc[-1]

        # ① EMA100 아래 이격 -1% ~ -8% (유지)
        gap_pct = (curr_close - curr_ema100) / curr_ema100 * 100
        if not (-8.0 <= gap_pct <= -1.0):
            return None, {}

        # 캔들 body 계산
        bodies   = abs(close - open_)
        avg_body = bodies.iloc[-20:-3].mean()
        if avg_body <= 0:
            return None, {}

        # ② 3봉 전: 긴 음봉 + CCI/RSI 과매도 (⚾ 완화: CCI -150→-120, RSI 35→40)
        bear_body = abs(close.iloc[-3] - open_.iloc[-3])
        is_bear3  = close.iloc[-3] < open_.iloc[-3]
        long_bear = is_bear3 and bear_body >= avg_body * 2.0
        cci_low   = cci.iloc[-3]
        rsi_low   = rsi.iloc[-3]

        if not long_bear:
            return None, {}
        if cci_low >= -120 or rsi_low >= 40:
            return None, {}

        # ③ 3봉 전 거래량 폭증 (⚾ 완화: 2.5배 → 2.0배)
        avg_volume = volume.iloc[-23:-3].mean()
        bear_volume = volume.iloc[-3]
        if avg_volume <= 0:
            return None, {}
        volume_ratio = bear_volume / avg_volume
        if volume_ratio < 2.0:
            return None, {}

        # ④ 3봉 전 저가가 BB 하단 이탈 (유지)
        bear_low_3     = low.iloc[-3]
        bb_lower_3     = bb_lower.iloc[-3]
        if bb_lower_3 != bb_lower_3 or bear_low_3 >= bb_lower_3:
            return None, {}

        # ⑤ 이후 2개 양봉 합산 body > 음봉 body (유지)
        bull2_body = abs(close.iloc[-2] - open_.iloc[-2]) + abs(close.iloc[-1] - open_.iloc[-1])
        is_bull2   = close.iloc[-2] > open_.iloc[-2]
        is_bull1   = close.iloc[-1] > open_.iloc[-1]
        v_pattern  = is_bull2 and is_bull1 and bull2_body > bear_body
        if not v_pattern:
            return None, {}

        # ⑥ CCI 저점 대비 +80 이상 회복 (⚾ 완화: +100 → +80)
        cci_curr = cci.iloc[-1]
        if cci_curr < cci_low + 80:
            return None, {}

        # ⑦ RSI 30 이상 회복 (유지)
        rsi_curr = rsi.iloc[-1]
        if rsi_curr < 30:
            return None, {}

        # ⑧ 현재가 > 음봉 종가 (유지)
        if close.iloc[-1] <= close.iloc[-3]:
            return None, {}

        # ⑨ 5분봉 기준 -12% 이내 (⚾ 완화: -10% → -12%)
        try:
            df_10m = safe_api_call(pyupbit.get_ohlcv, ticker, interval="minute5", count=110)
            if df_10m is not None and len(df_10m) >= 105:
                c10    = df_10m["close"]
                e10    = c10.ewm(span=100, adjust=False).mean()
                gap_10 = (c10.iloc[-1] - e10.iloc[-1]) / e10.iloc[-1] * 100
                if gap_10 < -12.0:
                    return None, {}
        except Exception:
            pass

        # ⑩ EMA100 기울기 (⚾ 완화: -0.1% → -0.2%)
        ema100_slope = (ema100.iloc[-1] - ema100.iloc[-10]) / ema100.iloc[-10] * 100
        if ema100_slope < -0.2:
            return None, {}

        info = {
            "gap_pct":      round(gap_pct, 2),
            "cci_low":      round(cci_low, 1),
            "cci_curr":     round(cci_curr, 1),
            "rsi_low":      round(rsi_low, 1),
            "rsi_curr":     round(rsi_curr, 1),
            "ratio":        round(bull2_body / bear_body, 2),
            "vol_ratio":    round(volume_ratio, 2),
            "bb_break_pct": round((bear_low_3 - bb_lower_3) / bb_lower_3 * 100, 2),
            "ema100_slope": round(ema100_slope, 3),
        }
        return "v_reversal", info

    except Exception as e:
        logger.error(f"V자반등 신호 오류 {ticker}: {e}")
        return None, {}


def get_ema_now(ticker):
    """surge 코인 EMA5/20 데드크로스 감지용"""
    try:
        df = safe_api_call(
            pyupbit.get_ohlcv, ticker,
            interval=CONFIG["candle_interval"], count=60
        )
        if df is None or len(df) < 60:
            return None, None, None, None
        close    = df["close"]
        ema5     = close.ewm(span=5,  adjust=False).mean()
        ema20    = close.ewm(span=20, adjust=False).mean()
        curr_e5  = ema5.iloc[-1]
        curr_e20 = ema20.iloc[-1]
        prev_e5  = ema5.iloc[-2]
        prev_e20 = ema20.iloc[-2]
        return curr_e5, curr_e20, prev_e5, prev_e20
    except Exception:
        return None, None, None, None


# ============================================================
# 🚨 실시간 거래량 급등 감지
# ============================================================

def detect_realtime_surge(protected_coins, bought_coins, candidate_coins, priority_tickers=None):
    """
    전체 코인 거래량 급등 감지
    - 🔧 P5: 전체 200개 → priority_tickers(candidate+wide+top25)만 스캔
    - 최근 5분 평균 대비 3배 이상 터진 코인 즉시 반환
    """
    try:
        stable_coins = set(CONFIG.get("stable_coins", []))
        # 🔧 P5: CPU 최적화 — priority_tickers로 스캔 대상 축소
        if priority_tickers:
            base_tickers = set(priority_tickers) | set(candidate_coins)
        else:
            base_tickers = set(get_cached_tickers())
        
        scan_targets = [
            t for t in base_tickers
            if t not in protected_coins
            and t not in bought_coins
            and t not in stable_coins
        ]

        # 🌅 개선⑤: 17~19시 surge 차단 (세력 퇴근 시간, -50% 손실 구간)
        from datetime import datetime as _dt_surge
        _h_surge = _dt_surge.now().hour
        if 17 <= _h_surge < 20:
            logger.debug(f"surge 차단: {_h_surge}시 세력 퇴근 시간대")
            return []

        surge_coins = []
        for ticker in scan_targets:
            try:
                df = pyupbit.get_ohlcv(
                    ticker, interval=CONFIG["candle_interval"],
                    count=CONFIG["realtime_volume_period"] + 1
                )
                if df is None or len(df) < CONFIG["realtime_volume_period"] + 1:
                    continue
                curr_vol = df["volume"].iloc[-1]
                avg_vol  = df["volume"].iloc[:-1].mean()
                if avg_vol <= 0:
                    continue

                # 현재 거래량이 0이면 스킵
                if curr_vol <= 0:
                    continue

                ratio = curr_vol / avg_vol
                if ratio >= CONFIG["realtime_surge_mult"]:
                    curr_price = df["close"].iloc[-1]
                    prev_price = df["close"].iloc[-2]
                    if curr_price <= 0 or prev_price <= 0:
                        continue

                    # 가격 변동 필터: 직전봉 대비 0.5% 이상 움직인 코인만
                    price_change = abs((curr_price - prev_price) / prev_price * 100)
                    if price_change < 0.5:
                        continue  # 스테이블코인 등 가격 안 움직이면 제외
                    
                    # 🎯 세력 작전 필터 (미누님 통찰 기반)
                    curr_value = curr_vol * curr_price
                    
                    if curr_value < 1_000_000:
                        continue  # 100만 미만 → 차단 (위험)
                    
                    if ratio >= 30:
                        pass  # 30배+ → 무조건 통과
                    elif ratio >= 10 and curr_value >= 10_000_000:
                        pass  # 10배+ & 1천만+ → 통과
                    elif ratio >= 5 and curr_value >= 20_000_000:
                        pass  # 5배+ & 2천만+ → 통과
                    else:
                        continue  # 그 외 차단
                    
                    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                    # 🆕 P5 surge 고도화 필터 (3단계 펌핑만 잡기)
                    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                    
                    # 개선④: 현재봉 양봉 + 직전봉 양봉 확인 (진짜 상승 중)
                    curr_open = df["open"].iloc[-1]
                    prev_open = df["open"].iloc[-2]
                    is_curr_bull = curr_price > curr_open
                    is_prev_bull = prev_price > prev_open
                    if not (is_curr_bull and is_prev_bull):
                        logger.debug(f"surge 차단 {ticker}: 양봉 아님")
                        continue
                    
                    # 개선②: 윗꼬리 3봉+ 차단 (세력 털기 패턴)
                    try:
                        upper_wick_count = 0
                        for i in range(-4, -1):  # 직전 3봉
                            _o = df["open"].iloc[i]
                            _c = df["close"].iloc[i]
                            _h = df["high"].iloc[i]
                            _body = abs(_c - _o)
                            _upper = _h - max(_c, _o)
                            # 윗꼬리가 body의 1.5배 이상 = 긴 윗꼬리
                            if _body > 0 and _upper >= _body * 1.5:
                                upper_wick_count += 1
                        if upper_wick_count >= 2:  # 직전 3봉 중 2개+ 윗꼬리
                            logger.debug(f"surge 차단 {ticker}: 윗꼬리 {upper_wick_count}/3 (세력털기)")
                            continue
                    except Exception:
                        pass
                    
                    # 개선①: EMA55 위 + 기울기 상승 중 (세력 건재)
                    # 개선③: 파동 과열 차단 (3파 90%+ / 5파 후반)
                    try:
                        _ema_info = get_cached_ema_10m(ticker)
                        if _ema_info:
                            _ema55 = _ema_info.get("ema55", 0)
                            _prev_ema55 = _ema_info.get("prev_ema55", _ema55)
                            if _ema55 > 0:
                                # EMA55 아래 = 세력 철수
                                if curr_price < _ema55:
                                    logger.debug(f"surge 차단 {ticker}: EMA55 아래")
                                    continue
                                # EMA55 기울기 하향 = 세력 떠나는 중
                                if _prev_ema55 > 0:
                                    _slope = (_ema55 - _prev_ema55) / _prev_ema55 * 100
                                    if _slope < -0.05:
                                        logger.debug(f"surge 차단 {ticker}: EMA55 하향 {_slope:.3f}%")
                                        continue
                    except Exception:
                        pass
                    
                    # 파동 과열 체크 (homerun처럼)
                    try:
                        _w_hot, _w_info = detect_wave_overheated(ticker, block_pct=90)
                        if _w_hot:
                            logger.debug(f"surge 차단 {ticker}: 파동과열 {_w_info.get('position_pct',0):.0f}%")
                            continue
                    except Exception:
                        pass

                    surge_coins.append({
                        "ticker":    ticker,
                        "vol_ratio": ratio,
                        "price":     curr_price,
                        "value":     curr_value,
                    })
                time.sleep(0.1)
            except Exception:
                continue

        surge_coins.sort(key=lambda x: x["vol_ratio"], reverse=True)
        top = surge_coins[:CONFIG["realtime_top_count"]]

        if top:
            names = [f"{c['ticker'].replace('KRW-','')}({c['vol_ratio']:.1f}배)" for c in top]
            logger.info(f"🚨 실시간 급등 감지: {', '.join(names)}")

        return [c["ticker"] for c in top]
    except Exception as e:
        logger.error(f"실시간 급등 감지 오류: {e}")
        return []


# ============================================================
# 🎯 코인 선별
# ============================================================

def check_prev_ema20_touch(ticker):
    """
    전일 고가가 일봉 EMA20을 터치했는지 확인
    → 전일에 EMA20 돌파 시도한 코인 = 당일 급등 사전 신호
    """
    try:
        df = pyupbit.get_ohlcv(ticker, interval="day", count=30)
        if df is None or len(df) < 25:
            return False
        ema20_daily = df["close"].ewm(span=20, adjust=False).mean()
        prev_high   = df["high"].iloc[-2]    # 전일 고가
        prev_ema20  = ema20_daily.iloc[-2]   # 전일 EMA20
        # 전일 고가가 EMA20 이상이면 터치/돌파한 것
        return prev_high >= prev_ema20
    except Exception:
        return False


def get_volume_surge_scores(tickers, protected_coins):
    """
    당일 거래대금 절대값 기준으로 상위 코인 선별
    - 전일 평균 대비 비율 X → 당일 거래대금 많은 순서로 정렬
    - 전일 EMA20 터치 필터 유지
    - 최소 거래대금 기준 없음 (당일 거래대금 순위로만 판단)
    """
    stable_coins = set(CONFIG.get("stable_coins", []))
    coin_scores = []
    for ticker in tickers:
        if ticker in protected_coins:
            continue
        if ticker in stable_coins:  # 스테이블코인 제외
            continue
        try:
            time.sleep(0.2)
            df = pyupbit.get_ohlcv(ticker, interval="day", count=22)
            if df is None or len(df) < 21:
                continue

            today_vol = df["value"].iloc[-1]
            if today_vol <= 0:
                continue

            # ── 전일 EMA20 터치 필터 ──
            ema20_daily = df["close"].ewm(span=20, adjust=False).mean()
            prev_high   = df["high"].iloc[-2]
            prev_ema20  = ema20_daily.iloc[-2]
            if prev_high < prev_ema20:
                continue  # 전일 고가가 EMA20 미달 → 제외

            coin_scores.append({
                "ticker":    ticker,
                "today_vol": today_vol,
            })
        except Exception:
            continue

    # 당일 거래대금 많은 순서로 정렬
    coin_scores.sort(key=lambda x: x["today_vol"], reverse=True)
    return coin_scores


def select_top_coins(protected_coins=None):
    """
    전일대비 상승률 상위 20개 선별
    - 5분마다 재선별 (장중 순위 변화 반영)
    - 일봉 EMA20 위 필터 유지
    - 스테이블코인 제외
    """
    if protected_coins is None:
        protected_coins = set()
    stable_coins = set(CONFIG.get("stable_coins", []))
    logger.info("🔍 [봇3] 전일대비 상승률 상위 20개 선별 중...")
    try:
        tickers = get_cached_tickers()
        if not tickers:
            return []

        coin_scores = []
        for ticker in tickers:
            if ticker in protected_coins:
                continue
            if ticker in stable_coins:
                continue
            try:
                time.sleep(0.1)
                # 현재가 및 전일 종가 가져오기
                df = pyupbit.get_ohlcv(ticker, interval="day", count=22)
                if df is None or len(df) < 21:
                    continue

                curr_price  = df["close"].iloc[-1]   # 오늘 현재가
                prev_close  = df["close"].iloc[-2]   # 전일 종가
                if prev_close <= 0:
                    continue

                # 전일대비 상승률
                change_pct = (curr_price - prev_close) / prev_close * 100
                if change_pct <= 0:
                    continue  # 상승 중인 코인만

                # 일봉 EMA20 위 필터
                ema20_daily = df["close"].ewm(span=20, adjust=False).mean()
                if curr_price <= ema20_daily.iloc[-1]:
                    continue  # EMA20 아래 차단

                # 최소 거래대금 필터 (너무 소량 코인 제외)
                today_vol = df["value"].iloc[-1]
                if today_vol < CONFIG["min_volume_today_billion"] * 1_000_000_000:
                    continue

                coin_scores.append({
                    "ticker":     ticker,
                    "change_pct": change_pct,
                    "today_vol":  today_vol,
                })
            except Exception:
                continue

        # 전일대비 상승률 높은 순서로 정렬
        coin_scores.sort(key=lambda x: x["change_pct"], reverse=True)
        selected = coin_scores[:CONFIG["top_coins_count"]]

        logger.info(f"📊 [봇3] 전일대비 상위 {len(selected)}개 선별 완료:")
        for i, c in enumerate(selected[:10], 1):
            surge_tag = "🚀" if c["change_pct"] >= CONFIG["normal_to_surge_pct"] else "📶"
            logger.info(
                f"  {i:>2}. {surge_tag} {c['ticker'].replace('KRW-',''):>8s} | "
                f"전일대비: +{c['change_pct']:.1f}% | "
                f"거래대금: {c['today_vol']/1e8:.1f}억"
            )

        # 전일대비 10% 이상 → surge 타입으로 분류
        ticker_types = {}
        for c in selected:
            if c["change_pct"] >= CONFIG["normal_to_surge_pct"]:
                ticker_types[c["ticker"]] = "surge"
            else:
                ticker_types[c["ticker"]] = "normal"

        return [c["ticker"] for c in selected], ticker_types

    except Exception as e:
        logger.error(f"[봇3] 코인 선별 오류: {e}")
        return [], {}


def replace_top_coins(current_coins, protected_coins=None, bought_coins=None):
    if protected_coins is None:
        protected_coins = set()
    if bought_coins is None:
        bought_coins = set()
    logger.info("🔄 [봇3] 급등 코인 부분 교체 중...")
    try:
        tickers = get_cached_tickers()
        if not tickers:
            return current_coins
        coin_scores = get_volume_surge_scores(tickers, protected_coins)
        top5 = [c["ticker"] for c in coin_scores[:5]]
        new_entries = [t for t in top5 if t not in current_coins]
        if not new_entries:
            logger.info("🔄 교체할 새 코인 없음 (유지)")
            return current_coins
        updated = list(current_coins)
        replace_count = min(CONFIG["top_coins_replace"], len(new_entries))
        replaced = []
        for new_ticker in new_entries[:replace_count]:
            for i in range(len(updated) - 1, -1, -1):
                if updated[i] not in bought_coins:
                    old = updated[i]
                    updated[i] = new_ticker
                    replaced.append(f"{old.replace('KRW-','')}→{new_ticker.replace('KRW-','')}")
                    break
        if replaced:
            logger.info(f"🔄 코인 교체: {', '.join(replaced)}")
        return updated
    except Exception as e:
        logger.error(f"[봇3] 코인 교체 오류: {e}")
        return current_coins


# ============================================================
# 💼 트레이더
# ============================================================

def get_daily_change(ticker):
    """전일대비 상승률 실시간 체크"""
    try:
        df = pyupbit.get_ohlcv(ticker, interval="day", count=2)
        if df is None or len(df) < 2:
            return 0
        curr  = df["close"].iloc[-1]
        prev  = df["close"].iloc[-2]
        if prev <= 0:
            return 0
        return (curr - prev) / prev * 100
    except Exception:
        return 0


# 호가창 스냅샷 캐시 (ticker → [최근 3개 스냅샷])
_orderbook_history = {}
_orderbook_lock = threading.Lock()


def get_orderbook_snapshot(ticker):
    """
    📖 호가창 스냅샷 조회 + 히스토리 저장
    
    미누님 통찰: "세력은 매도/매수 호가창을 채우고 빼고 반복"
    → 우리는 호가창 변화를 "빨리감기"로 관찰해야 함
    """
    try:
        ob = safe_api_call(pyupbit.get_orderbook, ticker)
        if not ob:
            return None
        
        if isinstance(ob, list):
            ob = ob[0] if ob else None
            if not ob:
                return None
        
        units = ob.get("orderbook_units", [])
        if not units or len(units) < 5:
            return None
        
        bid_total = sum(float(u.get("bid_size", 0)) for u in units)
        ask_total = sum(float(u.get("ask_size", 0)) for u in units)
        
        if bid_total <= 0 and ask_total <= 0:
            return None
        
        total = bid_total + ask_total
        bid_ask_ratio = bid_total / total if total > 0 else 0.5
        
        best_bid = float(units[0].get("bid_price", 0))
        best_ask = float(units[0].get("ask_price", 0))
        
        if best_bid <= 0 or best_ask <= 0:
            return None
        
        mid_price = (best_bid + best_ask) / 2
        spread_pct = (best_ask - best_bid) / mid_price * 100 if mid_price > 0 else 0
        
        snapshot = {
            "timestamp":     time.time(),
            "bid_total":     bid_total,
            "ask_total":     ask_total,
            "bid_ask_ratio": bid_ask_ratio,
            "spread_pct":    spread_pct,
            "mid_price":     mid_price,
            "best_bid":      best_bid,
            "best_ask":      best_ask,
        }
        
        with _orderbook_lock:
            if ticker not in _orderbook_history:
                _orderbook_history[ticker] = []
            _orderbook_history[ticker].append(snapshot)
            if len(_orderbook_history[ticker]) > 3:
                _orderbook_history[ticker] = _orderbook_history[ticker][-3:]
        
        return snapshot
    except Exception as e:
        logger.debug(f"호가창 조회 오류 {ticker}: {e}")
        return None


def detect_orderbook_heating(ticker):
    """
    🔥 호가창 깨어남 감지 (미누님 통찰: 세력 2단계)
    
    현재 vs 이전 스냅샷 비교해서 "호가창이 뜨거워지는지" 판단
    
    조건 (4개 중 3개+ 만족):
      ① 매수 or 매도 잔량 1.5배+ 증가 (호가 두꺼워짐)
      ② 중간 가격 0.2%+ 상승
      ③ bid/ask 비율 0.4~0.6 (양방향 활발)
      ④ 스프레드 0.5% 이하 (활발한 거래)
    """
    try:
        with _orderbook_lock:
            history = _orderbook_history.get(ticker, [])
            if len(history) < 2:
                return False, {}
            curr = history[-1]
            prev = history[-2]
        
        # 시간차 체크
        time_diff = curr["timestamp"] - prev["timestamp"]
        if time_diff > 30 or time_diff < 2:
            return False, {}
        
        # 조건 ①: 호가 두께 증가
        bid_growth = curr["bid_total"] / prev["bid_total"] if prev["bid_total"] > 0 else 1
        ask_growth = curr["ask_total"] / prev["ask_total"] if prev["ask_total"] > 0 else 1
        thickness_ok = bid_growth >= 1.5 or ask_growth >= 1.5
        
        # 조건 ②: 가격 상승
        if prev["mid_price"] <= 0:
            return False, {}
        price_change = (curr["mid_price"] - prev["mid_price"]) / prev["mid_price"] * 100
        price_ok = price_change >= 0.2
        
        # 조건 ③: 양방향 활발
        balance_ok = 0.4 <= curr["bid_ask_ratio"] <= 0.6
        
        # 조건 ④: 스프레드 좁음
        spread_ok = curr["spread_pct"] <= 0.5
        
        conditions = [thickness_ok, price_ok, balance_ok, spread_ok]
        passed = sum(conditions)
        
        info = {
            "bid_growth":   round(bid_growth, 2),
            "ask_growth":   round(ask_growth, 2),
            "price_change": round(price_change, 2),
            "bid_ratio":    round(curr["bid_ask_ratio"], 2),
            "spread":       round(curr["spread_pct"], 2),
            "passed":       f"{passed}/4",
        }
        
        return passed >= 3, info
    except Exception as e:
        logger.debug(f"호가깨어남 오류 {ticker}: {e}")
        return False, {}


def get_taker_buy_ratio(ticker, count=100):
    """체결강도 측정: 최근 N건 체결 중 매수체결 비율 (%)
    
    업비트 trade ticks API 사용:
    - BID (매수체결): 시장가 매수 주문이 호가를 먹어치움 (매수세)
    - ASK (매도체결): 시장가 매도 주문이 호가를 먹어치움 (매도세)
    
    반환값:
    - 50: 균형
    - 55 이상: 매수 우세
    - 60 이상: 강한 매수세
    - 70 이상: 폭발적 매수세
    - None: 조회 실패 (호출 측에서 기본값 처리)
    """
    try:
        url = f"https://api.upbit.com/v1/trades/ticks"
        params = {"market": ticker, "count": count}
        resp = requests.get(url, params=params, timeout=3)
        if resp.status_code != 200:
            return None
        trades = resp.json()
        if not trades:
            return None
        
        buy_vol  = sum(float(t.get('trade_volume', 0)) 
                       for t in trades if t.get('ask_bid') == 'BID')
        sell_vol = sum(float(t.get('trade_volume', 0)) 
                       for t in trades if t.get('ask_bid') == 'ASK')
        total = buy_vol + sell_vol
        if total <= 0:
            return None
        return (buy_vol / total) * 100
    except Exception:
        return None


def check_buy_quality(ticker, buy_type="normal"):
    """
    🛡️ 매수 품질 필터 (세력 털기 구간 진입 방지)
    
    실전 데이터 분석:
      0~10분 초단기 매도 140건 = -82.6% 누적 손실
      → 매수 시점이 이미 세력 털기 직전인 경우 대부분
    
    차단 조건:
      1. 최근 10분 최고점의 95% 이상 (고점 매수 방지)
      2. 최근 3분 거래량 급감 (세력 철수 중)
      3. BTC 1분봉 -0.5% 이상 급락 (전체 하락 시작)
    
    ⭐ 우회 조건 (미누님 철학):
      - whale_hunt/homerun → 자체 검증
      - 전일대비 +10% 이상 → 강력한 상승 추세, 무조건 잡음
      - 거래대금 50억+ → 대형 작전 코인
    
    반환: (통과 여부, 사유)
    """
    try:
        # 🌟 homerun/active_watch는 품질 필터 완전 우회
        # (active_watch는 자체 파동 체크 있음)
        if buy_type in ("homerun", "active_watch"):
            return True, "우회-전략"
        
        # 🐋 whale_hunt는 파동 체크만 적용 후 나머지 우회
        # BSV 24,580원 세력 펌핑 방지 (3파 고점 whale_hunt 진입 차단)
        if buy_type == "whale_hunt":
            wave_hot, wave_info = detect_wave_overheated(ticker, block_pct=90)
            if wave_hot:
                _phase = wave_info.get('wave_phase', '?')
                return False, (
                    f"🌊파동과열 {_phase} | "
                    f"고점 {wave_info.get('wave3_high',0):.0f} | "
                    f"위치 {wave_info.get('position_pct',0):.0f}%"
                )
            return True, "우회-whale(파동OK)"
        
        # ⭐ 강력한 상승 추세는 품질필터 우회 (미누님 요청)
        # MINA +14% 같은 대형 상승 코인은 무조건 잡아야 함
        try:
            daily_change = get_cached_daily_change(ticker)
            if daily_change >= 10.0:
                return True, f"우회-강상승 +{daily_change:.1f}%"
        except Exception:
            pass
        
        # 1. 현재가 + 최근 10분 봉 가져오기
        df = safe_api_call(pyupbit.get_ohlcv, ticker, 
                           interval="minute1", count=15)
        if df is None or len(df) < 12:
            return True, "데이터부족-통과"
        
        current = get_current_price_safe(ticker)
        if not current:
            return True, "현재가없음-통과"
        
        # ⭐ 대형 거래대금 코인 우회 (일 500억+)
        # 거래대금 큰 코인은 세력 작전 중일 가능성 높음 → 품질필터 우회
        try:
            df_day = safe_api_call(pyupbit.get_ohlcv, ticker, interval="day", count=2)
            if df_day is not None and len(df_day) >= 1:
                daily_value = df_day["close"].iloc[-1] * df_day["volume"].iloc[-1]
                if daily_value >= 50_000_000_000:  # 500억
                    return True, f"우회-대형 {daily_value/100_000_000:.0f}억"
        except Exception:
            pass
        
        # ── 체크 1: 고점 근처 매수 방지 ──
        recent_high = df["high"].iloc[-10:].max()
        recent_low  = df["low"].iloc[-10:].min()
        if recent_high > recent_low:
            # 최근 10분 레인지 내 위치 (0 = 최저, 1 = 최고)
            position = (current - recent_low) / (recent_high - recent_low)
            if position >= 0.95:
                return False, f"고점근처 {position*100:.0f}% (10분 최고점 {recent_high:,.2f})"
        
        # ── 체크 2: 거래량 급감 (세력 철수) ──
        recent_3_vol = df["volume"].iloc[-3:].mean()
        prev_7_vol   = df["volume"].iloc[-10:-3].mean()
        if prev_7_vol > 0:
            vol_ratio = recent_3_vol / prev_7_vol
            if vol_ratio < 0.4:  # 최근 3분 거래량이 이전의 40% 미만
                return False, f"거래량급감 {vol_ratio*100:.0f}% (세력철수)"
        
        # ── 체크 3: BTC 급락 중 차단 ──
        btc_df = safe_api_call(pyupbit.get_ohlcv, "KRW-BTC",
                               interval="minute1", count=3)
        if btc_df is not None and len(btc_df) >= 2:
            btc_now = btc_df["close"].iloc[-1]
            btc_prev = btc_df["close"].iloc[-2]
            if btc_prev > 0:
                btc_change = (btc_now - btc_prev) / btc_prev * 100
                if btc_change <= -0.5:  # BTC 1분봉 -0.5% 이상 급락
                    return False, f"BTC 1분봉 {btc_change:.2f}% 급락"
        
        # ── 체크 4: EMA55 세력선 체크 (미누님 5개 차트 + CFG 분석) ──
        # ⭐ 3가지 체크:
        #   1. EMA55 아래 = 세력 철수 → 차단
        #   2. EMA55 위 지지 = 좋은 기회 → 통과
        #   3. EMA55 위 +5% 초과 이격 = 과열 → 차단! (CFG 사례)
        try:
            _dc = get_cached_daily_change(ticker)
            if _dc >= 5.0:
                df_30 = safe_api_call(pyupbit.get_ohlcv, ticker,
                                     interval="minute10", count=60)
                if df_30 is not None and len(df_30) >= 56:
                    ema55 = df_30["close"].ewm(span=55, adjust=False).mean().iloc[-1]
                    
                    if ema55 > 0:
                        ema55_gap = (current - ema55) / ema55 * 100
                        
                        # 1. 현재가 < EMA55 = 세력 철수 중 → 차단
                        if current < ema55:
                            return False, (
                                f"EMA55 아래(세력철수) | 급등 +{_dc:.1f}% | "
                                f"이격 {ema55_gap:+.2f}%"
                            )
                        
                        # 2. EMA55 위 +5% 초과 = 과열 → 차단 (CFG 사례)
                        # "55일선과 이격이 너무 떨어졌잖아" - 미누님
                        if ema55_gap > 7.0:
                            return False, (
                                f"EMA55 과열(이격과다) | 급등 +{_dc:.1f}% | "
                                f"EMA55 이격 {ema55_gap:+.1f}% > 7%"
                            )
                        
                        # 3. EMA55 위 0~5% = 지지 중 → 통과! (좋은 매수 기회)
        except Exception:
            pass
        
        # ── 체크 5: 🌊 엘리엇 파동 과열 (3파/5파 고점 근처 매수 방지) ──
        # 미누님 파동이론: 3파 고점에서 사면 4파 조정에 물림
        # BSV 24,580원 → 바로 24,260원 하락 사례 방지
        try:
            wave_hot, wave_info = detect_wave_overheated(ticker, block_pct=90)
            if wave_hot:
                _phase = wave_info.get('wave_phase', '?')
                _fib = wave_info.get('fib', {})
                _w3ext = _fib.get('wave3_ext', 0)
                return False, (
                    f"🌊파동과열 {_phase} | "
                    f"고점 {wave_info.get('wave3_high',0):.0f} | "
                    f"저점 {wave_info.get('trend_low',0):.0f} | "
                    f"위치 {wave_info.get('position_pct',0):.0f}% | "
                    f"3파 {_w3ext:.1f}x"
                )
        except Exception:
            pass
        
        return True, "OK"
    except Exception as e:
        logger.debug(f"품질필터 오류 {ticker}: {e}")
        return True, "오류-통과"  # 오류 시 안전하게 통과


def detect_homerun_coin(ticker, protected_coins=None, bought_coins=None):
    """
    🌟 대형 홈런 코인 감지 — 3단계 시스템 (P5)
    
    미누님 철학: "세력은 작은 코인 + 작은 자금으로 시작"
    → 큰 코인만 잡던 기존 조건을 3단계로 완화
    
    🌟 HUGE (기존): 전일 +15%+ AND 거래대금 100억+ (BTC/ETH/SOL급)
    🌟 MID  (NEW): 전일 +20%+ AND 거래대금 30억+  + 거래량 5배+ (중형)
    🌟 SMALL(NEW): 전일 +30%+ AND 거래대금 10억+  + 거래량 10배+ (소형, LPT스타일)
    
    공통 필터:
      - 일봉 EMA20 위 (상승 추세)
      - 1분봉 EMA5 > EMA20 (단기 상승)
      - RSI < 85 (극과매수 제외)
    
    시장 상태 무관 — 폭락장이어도 잡음
    """
    try:
        if protected_coins and ticker in protected_coins:
            return False, {}
        if bought_coins and ticker in bought_coins:
            return False, {}
        
        # 1. 전일대비 조회
        daily_change = get_cached_daily_change(ticker)
        if daily_change < 15.0:  # 최소 +15% 미만은 즉시 컷
            return False, {}
        
        # 2. 일 거래대금 조회
        df_day = safe_api_call(pyupbit.get_ohlcv, ticker, interval="day", count=2)
        if df_day is None or len(df_day) < 1:
            return False, {}
        
        daily_value = df_day["close"].iloc[-1] * df_day["volume"].iloc[-1]
        if daily_value < 1_000_000_000:  # 10억 미만은 스캠 위험 → 차단
            return False, {}
        
        # 3. 일봉 EMA20 위 (상승 추세 확인)
        df_daily = safe_api_call(pyupbit.get_ohlcv, ticker, interval="day", count=30)
        if df_daily is None or len(df_daily) < 22:
            return False, {}
        
        daily_close = df_daily["close"].iloc[-1]
        daily_ema20 = df_daily["close"].ewm(span=20, adjust=False).mean().iloc[-1]
        if daily_close <= daily_ema20:
            return False, {}
        
        # 4. 1분봉 거래량 폭발 + 상승 추세
        df_1m = safe_api_call(pyupbit.get_ohlcv, ticker, 
                              interval=CONFIG["candle_interval"], count=30)
        if df_1m is None or len(df_1m) < 25:
            return False, {}
        
        c = df_1m["close"]
        v = df_1m["volume"]
        
        avg_vol = v.iloc[-21:-1].mean()
        if avg_vol <= 0:
            return False, {}
        vol_ratio = v.iloc[-1] / avg_vol
        
        # 🌟 3단계 분류 조건 체크
        homerun_tier = None
        if daily_change >= 15.0 and daily_value >= 10_000_000_000 and vol_ratio >= 3.0:
            homerun_tier = "HUGE"  # 기존 조건: 대형 작전
        elif daily_change >= 20.0 and daily_value >= 3_000_000_000 and vol_ratio >= 5.0:
            homerun_tier = "MID"   # 중형: 더 강한 움직임 요구
        elif daily_change >= 30.0 and daily_value >= 1_000_000_000 and vol_ratio >= 10.0:
            homerun_tier = "SMALL" # 소형: LPT 스타일, 매우 강한 폭발
        
        if not homerun_tier:
            return False, {}
        
        # 5. 1분봉 EMA5 > EMA20 (상승 추세)
        ema5 = c.ewm(span=5, adjust=False).mean().iloc[-1]
        ema20 = c.ewm(span=20, adjust=False).mean().iloc[-1]
        if ema5 <= ema20:
            return False, {}
        
        # 6. 1분봉 RSI < 85 (극과매수 제외)
        delta = c.diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = (-delta.clip(upper=0)).rolling(14).mean()
        rs = gain.iloc[-1] / loss.iloc[-1] if loss.iloc[-1] > 0 else 100
        rsi = 100 - (100 / (1 + rs))
        if rsi >= 85:
            return False, {}
        
        # 7. 🌊 파동 과열 체크 (5파 고점 근처는 차단)
        try:
            _w_hot, _w_info = detect_wave_overheated(ticker, block_pct=95)
            if _w_hot:
                return False, {}  # 파동 과열 → 스킵
        except Exception:
            pass
        
        info = {
            "tier":         homerun_tier,  # HUGE/MID/SMALL
            "daily_change": round(daily_change, 1),
            "daily_value":  int(daily_value),
            "vol_ratio":    round(vol_ratio, 1),
            "rsi":          round(rsi, 0),
            "ema5_gap":     round((ema5 - ema20) / ema20 * 100, 2),
        }
        return True, info
    except Exception as e:
        logger.debug(f"홈런 감지 오류 {ticker}: {e}")
        return False, {}


def detect_accumulation_candle(ticker, protected_coins=None, bought_coins=None):
    """
    🐋 매집봉 감지 (미누님 정의 반영)
    
    미누님 정의:
      매집봉 = 세력이 물량을 사들이는 캔들
      
      [분봉/5분봉] - 실시간 포착
        장대 양봉 OR 장대 음봉 (body가 평균의 1.5배+)
        + 거래량 많이 실림 (평균의 3배+)
      
      [일봉/4시간봉] - 큰 그림
        긴꼬리 양봉 OR 음봉
        + 거래량 많이 실림
      
      우리는 스윙 매매 → 4시간봉 ~ 5분봉까지만 체크
    
    3개 타임프레임 종합 판정:
      - 5분봉: 장대봉 + 거래량
      - 1시간봉: 장대봉 or 긴꼬리 + 거래량
      - 4시간봉: 긴꼬리 + 거래량 (큰 그림)
    
    반환: (신호, info)
      info: {timeframe, candle_type, body_ratio, vol_ratio, whale_size, ...}
    """
    try:
        if protected_coins and ticker in protected_coins:
            return False, {}
        if bought_coins and ticker in bought_coins:
            return False, {}
        
        detected = []  # 감지된 타임프레임 기록
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # 타임프레임별 체크
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        for tf_name, interval, body_mult, vol_mult in [
            ("5분봉",   "minute5",   1.5, 3.0),  # 장대봉 + 거래량 3배
            ("1시간봉", "minute60",  1.5, 2.5),  # 장대봉 or 긴꼬리 + 거래량 2.5배
            ("4시간봉", "minute240", 1.3, 2.0),  # 긴꼬리 + 거래량 2배 (큰 그림)
        ]:
            df = safe_api_call(pyupbit.get_ohlcv, ticker, interval=interval, count=25)
            if df is None or len(df) < 20:
                continue
            
            # 최근 봉 (최근 3봉 중 하나라도 매집봉이면 감지)
            for idx in [-1, -2, -3]:
                try:
                    c = df["close"].iloc[idx]
                    o = df["open"].iloc[idx]
                    h = df["high"].iloc[idx]
                    l = df["low"].iloc[idx]
                    v = df["volume"].iloc[idx]
                    
                    if c <= 0 or v <= 0:
                        continue
                    
                    body = abs(c - o)
                    total_range = h - l
                    if total_range <= 0:
                        continue
                    
                    # 평균 body, 평균 거래량 (과거 20봉)
                    past_slice = df.iloc[max(0, len(df)+idx-20):len(df)+idx]
                    if len(past_slice) < 10:
                        continue
                    
                    avg_body = (past_slice["close"] - past_slice["open"]).abs().mean()
                    avg_vol  = past_slice["volume"].mean()
                    
                    if avg_body <= 0 or avg_vol <= 0:
                        continue
                    
                    body_ratio = body / avg_body
                    vol_ratio = v / avg_vol
                    
                    # 꼬리 계산
                    upper_wick = h - max(c, o)
                    lower_wick = min(c, o) - l
                    long_wick = (body > 0 and 
                                 (upper_wick >= body * 1.2 or lower_wick >= body * 1.2))
                    
                    # 캔들 타입
                    is_bullish = c > o  # 양봉
                    is_long_candle = body_ratio >= body_mult  # 장대봉
                    is_high_volume = vol_ratio >= vol_mult  # 거래량 많음
                    
                    # 🎯 매집봉 판정 (양봉만! 음봉 제외 - 미누님)
                    # "긴장대양봉(거래량 많이) + 긴꼬리양봉(거래량 많이)만"
                    if tf_name in ("5분봉", "1시간봉"):
                        is_accum = (is_long_candle or long_wick) and is_high_volume and is_bullish
                    else:  # 4시간봉
                        is_accum = long_wick and is_high_volume and is_bullish
                    
                    if is_accum:
                        candle_type = (
                            "장대양봉" if is_long_candle else
                            "윗꼬리양봉" if upper_wick > lower_wick else
                            "아랫꼬리양봉"
                        )
                        detected.append({
                            "tf":         tf_name,
                            "interval":   interval,
                            "idx":        idx,
                            "type":       candle_type,
                            "body_ratio": round(body_ratio, 2),
                            "vol_ratio":  round(vol_ratio, 2),
                            "bullish":    is_bullish,
                            "long":       is_long_candle,
                            "wick":       long_wick,
                        })
                        break  # 해당 타임프레임에서 하나 감지하면 다음 TF로
                except Exception:
                    continue
        
        if not detected:
            return False, {}
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # 종합 판정
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # 거래대금 최소 기준 (시세조작 방어)
        df_day = safe_api_call(pyupbit.get_ohlcv, ticker, interval="day", count=2)
        if df_day is None or len(df_day) < 1:
            return False, {}
        daily_value = (df_day["close"].iloc[-1] * df_day["volume"].iloc[-1])
        if daily_value < 300_000_000:  # 일 3억 미만 → 시세조작 위험
            return False, {}
        
        # 🐋 세력 규모 판정
        # - 3개 타임프레임 전부 감지: big (확실한 세력)
        # - 2개 타임프레임 감지: big (4시간봉 포함 시)
        # - 1개 타임프레임 감지: small
        tf_count = len(detected)
        has_4h = any(d["tf"] == "4시간봉" for d in detected)
        
        if tf_count >= 3:
            whale_size = "big"
        elif tf_count == 2 and has_4h:
            whale_size = "big"
        else:
            whale_size = "small"
        
        # 대표 캔들 (가장 긴 타임프레임)
        primary = detected[-1]  # 4h > 1h > 5m 순으로 저장됨
        
        info = {
            "timeframes":  [d["tf"] for d in detected],
            "tf_count":    tf_count,
            "primary_tf":  primary["tf"],
            "candle_type": primary["type"],
            "body_ratio":  primary["body_ratio"],
            "vol_ratio":   primary["vol_ratio"],
            "daily_value": int(daily_value),
            "whale_size":  whale_size,
            "detected":    detected,
        }
        return True, info
    except Exception as e:
        logger.debug(f"매집봉 감지 오류 {ticker}: {e}")
        return False, {}


# 🔗 레거시 이름 호환 (기존 코드가 이 이름 사용)
detect_whale_accumulation_pattern = detect_accumulation_candle


def detect_wakeup_signal(ticker, protected_coins=None, bought_coins=None):
    """
    🔥 호가깨어남 (Wake-up) 감지 - 미누님 통찰 기반
    
    세력 작전 2단계 (호가창 채우기) 포착:
    - 1단계 매집 끝나고 본격 펌핑 직전
    - 4단계 펌핑 (서지)보다 훨씬 일찍 진입 가능
    - 평균 진입가 -1% ~ +1% (저점)
    
    조건 (6개 중 4개+ 만족):
      ① 거래량 1.5~3배 (큰 폭발 아님, 깨어남)
      ② 가격 변동 0.3% ~ 1.5% (작은 움직임)
      ③ 직전 5분봉 거래량 합 평균의 50% 이하 (조용했음)
      ④ 매수 체결강도 50% 이상 (매수 우세)
      ⑤ 1분봉 캔들 꼬리 있음 (세력 매집 흔적)
      ⑥ 일거래대금 1억 이상 (시세조작 방어)
    """
    try:
        # 보호 코인 / 보유 코인 제외
        if protected_coins and ticker in protected_coins:
            return False, {}
        if bought_coins and ticker in bought_coins:
            return False, {}
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # 🔧 P5 사전체크 ①: EMA55 기울기 확인
        # "EMA55가 하향이면 세력 떠나는 중 → 차단"
        # CFG 12:15 → 12:17 EMA55 이탈 방지
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        try:
            ema_10m = get_cached_ema_10m(ticker)
            if ema_10m:
                ema55_now = ema_10m.get("ema55", 0)
                prev_ema55 = ema_10m.get("prev_ema55", ema55_now)
                if ema55_now > 0 and prev_ema55 > 0:
                    ema55_slope = (ema55_now - prev_ema55) / prev_ema55 * 100
                    if ema55_slope < -0.05:  # EMA55 하향 중
                        return False, {"block": "EMA55하향", "slope": round(ema55_slope, 3)}
                    # 현재가 < EMA55 = 세력 철수 → 차단
                    curr_check = get_current_price_safe(ticker)
                    if curr_check and curr_check < ema55_now:
                        return False, {"block": "EMA55아래"}
        except Exception:
            pass
        
        # 1분봉 30개 가져오기
        df = safe_api_call(pyupbit.get_ohlcv, ticker, 
                          interval=CONFIG["candle_interval"], count=30)
        if df is None or len(df) < 25:
            return False, {}
        
        close = df["close"]
        open_ = df["open"]
        high  = df["high"]
        low   = df["low"]
        vol   = df["volume"]
        
        # 현재 봉 데이터
        curr_close = close.iloc[-1]
        curr_open  = open_.iloc[-1]
        curr_high  = high.iloc[-1]
        curr_low   = low.iloc[-1]
        curr_vol   = vol.iloc[-1]
        
        if curr_close <= 0 or curr_vol <= 0:
            return False, {}
        
        # 평균 거래량 (직전 20봉)
        avg_vol = vol.iloc[-21:-1].mean()
        if avg_vol <= 0:
            return False, {}
        vol_ratio = curr_vol / avg_vol
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # 🔧 P5 사전체크 ②: 직전 3분 거래량 동반 확인
        # "호가만 바뀌고 실제 거래가 없으면 세력 속임수"
        # 직전 3분 평균 거래량이 전체 평균의 1.5배 이상이어야 함
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        recent_3_avg = vol.iloc[-4:-1].mean()  # 직전 3봉 평균
        if avg_vol > 0 and recent_3_avg / avg_vol < 0.8:
            # 직전 3분 거래량이 평균의 80% 미만 = 거래 없이 호가만 변동
            return False, {"block": "거래량미동반", "recent_vol_ratio": round(recent_3_avg/avg_vol, 2)}
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # 조건 ①: 거래량 1.5~3배 (깨어남, 폭발 아님)
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        cond1 = 1.5 <= vol_ratio <= 3.0
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # 조건 ②: 가격 변동 0.3 ~ 1.5%
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        prev_close = close.iloc[-2]
        if prev_close <= 0:
            return False, {}
        price_change = (curr_close - prev_close) / prev_close * 100
        cond2 = 0.3 <= price_change <= 1.5
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # 조건 ③: 직전 5분 거래량 평균의 50% 이하 (조용)
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        prev_5_vol = vol.iloc[-6:-1].sum()
        prev_avg_5 = avg_vol * 5  # 평균 5봉 합
        cond3 = prev_5_vol < prev_avg_5 * 0.5
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # 조건 ④: 매수 체결강도 50% 이상
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        taker_ratio = get_taker_buy_ratio(ticker, count=50)
        cond4 = taker_ratio is not None and taker_ratio >= 50
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # 조건 ⑤: 1분봉 캔들 꼬리 있음 (세력 매집 흔적)
        # 윗꼬리 또는 아랫꼬리가 body의 30% 이상
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        body = abs(curr_close - curr_open)
        upper_wick = curr_high - max(curr_close, curr_open)
        lower_wick = min(curr_close, curr_open) - curr_low
        if body > 0:
            wick_ratio = (upper_wick + lower_wick) / body
            cond5 = wick_ratio >= 0.3
        else:
            cond5 = False
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # 조건 ⑥: 거래대금 1억 이상 (1분봉)
        # 너무 작은 코인은 시세조작 위험 + 진짜 세력 작전은 거래대금 있음
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        curr_value = curr_vol * curr_close
        cond6 = curr_value >= 100_000_000  # 1억
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # 종합 판정: 6개 중 4개 이상
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        conditions = [cond1, cond2, cond3, cond4, cond5, cond6]
        passed = sum(conditions)
        
        info = {
            "vol_ratio":    round(vol_ratio, 2),
            "price_change": round(price_change, 2),
            "prev_quiet":   cond3,
            "taker_ratio":  round(taker_ratio, 1) if taker_ratio else None,
            "wick_ratio":   round(wick_ratio, 2) if body > 0 else 0,
            "value":        int(curr_value),
            "passed":       f"{passed}/6",
        }
        
        return passed >= 4, info
    except Exception as e:
        logger.debug(f"호가깨어남 감지 오류 {ticker}: {e}")
        return False, {}


class Bot3Trader:
    def __init__(self):
        self.upbit             = pyupbit.Upbit(CONFIG["access_key"], CONFIG["secret_key"])
        self.candidate_coins   = []
        self.ticker_types      = {}
        self.bought_coins      = {}
        self.protected_coins   = set(CONFIG.get("protected_coins", []))
        self.history           = load_json(CONFIG["trade_history_file"], {"trades": []})
        self.last_select_time  = None
        self.last_replace_time = None
        self.rebuy_blocked     = {}
        self.daily_stoploss    = {}
        # ⚡ 동시성 보호 락 (매수/매도/파일저장 직렬화)
        self.trade_lock        = threading.RLock()   # 재진입 가능
        self.file_lock         = threading.Lock()    # JSON 파일 저장 전용
        self.bot_bought_tickers = set(
            load_json(CONFIG["bot_bought_file"], {}).get("tickers", [])
        )
        # ⚡ 패닉 감지용: 각 코인의 최근 가격 이력 (ticker → [(timestamp, price), ...])
        self.price_history = {}  # 최근 30초치 현재가 저장
        # ⚡ 매도 큐 참조 (run_bot에서 주입) - 큐 우선 매도 시스템
        self.sell_queue = None
        # 🔧 P4: 거래대금 상위 50개 공유 (v_reversal_wide가 갱신, 노말/매집도 공유 사용)
        self.wide_tickers = []
        # 🔥 공격모드 전일대비 상위 25 (미누님 전략)
        # "주간 강세장에는 전일대비 상위 25개에서만 거래"
        self.attack_top25 = []       # 현재 TOP 25
        self.prev_attack_top25 = []  # 이전 TOP 25 (순위 변화 감지용)
        # 🐋 매집봉 watchlist - 미누님 전략
        # {ticker: {detected_at, whale_size, candle_type, info, market_state}}
        # 횡보장에서 매집봉 감지 시 킵 → 호가창 활발 시 매수 승격
        self.whale_watchlist = {}
        self.watchlist_lock  = threading.Lock()
        # ⚡ 1분봉 공격 감시 리스트 (미누님 요청)
        # {ticker: {registered_at, reason, base_vol, base_price}}
        # 호가 활발/급변동/+5% 코인 → 1분봉 공격 매수
        self.active_watchlist = {}
        self.active_watch_lock = threading.Lock()
        # 🔥 전일대비 상위 25 (공격모드 매수 대상)
        self.top25_tickers = []      # 현재 상위 25
        self.prev_top25 = []         # 이전 스캔 상위 25 (순위 급상승 감지)
        self._restore_block_states()   # ← 재매수 금지 복원
        self._detect_existing_holdings()

    def _restore_block_states(self):
        """재시작 시 재매수 금지 / 당일 손절 횟수 복원"""
        try:
            saved = load_json(CONFIG["bot_bought_file"], {})
            now   = datetime.datetime.now()

            # ── rebuy_blocked 복원 (해제 시각 지났으면 무시) ──
            for ticker, unblock_str in saved.get("rebuy_blocked", {}).items():
                try:
                    unblock_dt = datetime.datetime.fromisoformat(unblock_str)
                    if unblock_dt > now:
                        self.rebuy_blocked[ticker] = unblock_dt
                        remaining = int((unblock_dt - now).seconds / 60)
                        logger.debug(f"⏳ [복원] {ticker.replace('KRW-','')} 재매수 금지 유지 (잔여 {remaining}분)")
                    else:
                        logger.debug(f"✅ [복원] {ticker.replace('KRW-','')} 재매수 금지 해제됨 (시간 경과)")
                except Exception:
                    pass

            # ── daily_stoploss 복원 (오늘 날짜 것만) ──
            today = today_str()
            for ticker, info in saved.get("daily_stoploss", {}).items():
                if info.get("date") == today:
                    self.daily_stoploss[ticker] = info
                    count = info.get("count", 0)
                    if count >= CONFIG["max_stoploss_per_day"]:
                        logger.debug(f"🚫 [복원] {ticker.replace('KRW-','')} 당일 {count}회 손절 → 오늘 재매수 금지 유지")
                    else:
                        logger.info(f"📋 [복원] {ticker.replace('KRW-','')} 당일 손절 {count}회 기록 유지")
        except Exception as e:
            logger.error(f"재매수 금지 복원 오류: {e}")

    def _detect_existing_holdings(self):
        try:
            time.sleep(1)
            balances = self.upbit.get_balances()
            if not balances:
                return
            logger.info("🔒 [봇3] 기존 보유 코인 감지 중...")

            # ── bought.json에서 coin_states 로드 ──
            saved_data   = load_json(CONFIG["bot_bought_file"], {})
            coin_states  = saved_data.get("coin_states", {})  # 신규: 상태 직접 복원

            protected_count = bot_managed_count = 0
            for b in balances:
                currency = b.get("currency", "")
                if currency == "KRW":
                    continue
                if float(b.get("balance", 0)) > 0 and float(b.get("avg_buy_price", 0)) > 0:
                    ticker    = f"KRW-{currency}"
                    avg_price = float(b.get("avg_buy_price", 0))
                    balance   = float(b.get("balance", 0))
                    if ticker in self.bot_bought_tickers:

                        # ── coin_states 우선 → 없으면 history 검색 fallback ──
                        if ticker in coin_states:
                            saved = coin_states[ticker]
                            buy_type           = saved.get("buy_type", "normal")
                            original_type      = saved.get("original_type", buy_type)  # 🎯 출신성분 (폴백: buy_type)
                            breakeven_armed    = saved.get("breakeven_armed", False)
                            trailing_active    = saved.get("trailing_active", False)
                            trail_floor_pct    = saved.get("trail_floor_pct", None)
                            buy_time           = saved.get("buy_time", datetime.datetime.now().isoformat())
                            highest            = max(saved.get("highest", avg_price), avg_price)
                            entry_eth_status   = saved.get("entry_eth_status", "bullish")
                            swing_mode         = saved.get("swing_mode", False)
                            swing_next_check   = saved.get("swing_next_check", None)
                            surge_upgraded     = saved.get("surge_upgraded", False)
                            normal_add_total   = saved.get("normal_add_total", 0)
                            surge_add_done     = saved.get("surge_add_buy_done", False)
                            surge_add_2_done   = saved.get("surge_add_buy_2_done", False)
                            surge_add_3_done   = saved.get("surge_add_buy_3_done", False)
                            surge_trail_done   = saved.get("surge_trail_half_done", False)
                            normal_tp1_done    = saved.get("normal_tp1_done", False)
                            surge_tp1_done     = saved.get("surge_tp1_done", False)
                            surge_tp2_done     = saved.get("surge_tp2_done", False)
                        else:
                            # fallback: history에서 buy_type 복원
                            buy_type = "normal"
                            for trade in reversed(self.history.get("trades", [])):
                                if trade.get("ticker") == ticker and trade.get("type") == "BUY":
                                    buy_type = trade.get("buy_type", "normal")
                                    break
                            original_type      = buy_type  # 🎯 폴백: 현재 buy_type을 출신성분으로
                            breakeven_armed    = False
                            trailing_active    = False
                            trail_floor_pct    = None
                            buy_time           = datetime.datetime.now().isoformat()
                            highest            = avg_price
                            entry_eth_status   = "bullish"
                            swing_mode         = False
                            swing_next_check   = None
                            surge_upgraded     = False
                            normal_add_total   = 0
                            surge_add_done     = False
                            surge_add_2_done   = False
                            surge_add_3_done   = False
                            surge_trail_done   = False
                            normal_tp1_done    = False
                            surge_tp1_done     = False
                            surge_tp2_done     = False

                        self.bought_coins[ticker] = {
                            "buy_price":              avg_price,
                            "highest":                highest,
                            "amount_krw":             avg_price * balance,
                            "buy_qty":                balance,
                            "tp1_done":               False,
                            "breakeven_armed":        breakeven_armed,
                            "trailing_active":        trailing_active,
                            "trail_floor_pct":        trail_floor_pct,
                            "buy_type":               buy_type,
                            "original_type":          original_type,   # 🎯 출신성분 (불변)
                            "buy_time":               buy_time,
                            "entry_eth_status":       entry_eth_status,
                            "swing_mode":             swing_mode,
                            "swing_next_check":       swing_next_check,
                            "surge_upgraded":         surge_upgraded,
                            "normal_add_total":       normal_add_total,
                            "surge_add_buy_done":     surge_add_done,
                            "surge_add_buy_2_done":   surge_add_2_done,
                            "surge_add_buy_3_done":   surge_add_3_done,
                            "surge_trail_half_done":  surge_trail_done,
                            "normal_tp1_done":        normal_tp1_done,
                            "surge_tp1_done":         surge_tp1_done,
                            "surge_tp2_done":         surge_tp2_done,
                        }
                        type_str  = "🚀 surge" if buy_type == "surge" else "🐋 매집찾기" if buy_type == "whale_hunt" else "🔥 깨어남" if buy_type == "wakeup" else "🏦 매집" if buy_type == "accumulation" else "📈 V자" if buy_type == "v_reversal" else "📶 normal"
                        trail_str = " | 🎯트레일링중" if trailing_active else ""
                        be_str    = " | 🛡️본전보장" if breakeven_armed else ""
                        logger.info(f"  🤖 {currency:>8s} → 봇 관리 재개 [{type_str}]{trail_str}{be_str}")
                        bot_managed_count += 1
                    else:
                        self.protected_coins.add(ticker)
                        logger.info(f"  🔒 {currency:>8s} → 보호됨")
                        protected_count += 1
            logger.info(f"🔒 보호: {protected_count}개 | 🤖 봇 관리 재개: {bot_managed_count}개")
        except Exception as e:
            logger.error(f"[봇3] 보유 코인 감지 오류: {e}")

    def _save_bot_bought(self):
        """bought.json에 전체 상태 저장 (재시작 복원용)
        ⚡ file_lock으로 동시 쓰기 방지 → JSON 파일 깨짐 방지
        """
        with self.file_lock:
            all_tickers = set(self.bot_bought_tickers) | set(self.bought_coins.keys())
            coin_states = {}
            for ticker, info in list(self.bought_coins.items()):
                coin_states[ticker] = {
                    "buy_type":               info.get("buy_type", "normal"),
                    "original_type":          info.get("original_type", info.get("buy_type", "normal")),
                    "buy_price":              info.get("buy_price", 0),
                    "highest":                info.get("highest", 0),
                    "amount_krw":             info.get("amount_krw", 0),
                    "buy_qty":                info.get("buy_qty", 0),
                    "tp1_done":               info.get("tp1_done", False),
                    "breakeven_armed":        info.get("breakeven_armed", False),
                    "trailing_active":        info.get("trailing_active", False),
                    "trail_floor_pct":        info.get("trail_floor_pct", None),
                    "buy_time":               info.get("buy_time", ""),
                    "entry_eth_status":       info.get("entry_eth_status", "bullish"),
                    "surge_upgraded":         info.get("surge_upgraded", False),
                    "normal_add_total":       info.get("normal_add_total", 0),
                    "swing_mode":             info.get("swing_mode", False),
                    "swing_next_check":       info.get("swing_next_check", None),
                    "surge_add_buy_done":     info.get("surge_add_buy_done", False),
                    "surge_add_buy_2_done":   info.get("surge_add_buy_2_done", False),
                    "surge_add_buy_3_done":   info.get("surge_add_buy_3_done", False),
                    "surge_trail_half_done":  info.get("surge_trail_half_done", False),
                    "normal_tp1_done":        info.get("normal_tp1_done", False),
                    "surge_tp1_done":         info.get("surge_tp1_done", False),
                    "surge_tp2_done":         info.get("surge_tp2_done", False),
                }

            # rebuy_blocked: datetime → isoformat 문자열로 직렬화
            rebuy_blocked_serial = {
                t: dt.isoformat()
                for t, dt in self.rebuy_blocked.items()
            }

            try:
                save_json(CONFIG["bot_bought_file"], {
                    "tickers":        list(all_tickers),
                    "coin_states":    coin_states,
                    "rebuy_blocked":  rebuy_blocked_serial,
                    "daily_stoploss": self.daily_stoploss,
                })
            except Exception as e:
                logger.error(f"⚠️ bought.json 저장 오류: {e}")

    def is_protected(self, ticker):
        return ticker in self.protected_coins

    def is_rebuy_blocked(self, ticker, buy_type="normal"):
        # 30분 재매수 금지 (손절 후 단기 재진입 방지)
        if ticker in self.rebuy_blocked:
            if datetime.datetime.now() < self.rebuy_blocked[ticker]:
                remaining = int((self.rebuy_blocked[ticker] - datetime.datetime.now()).seconds / 60)
                logger.debug(f"⏳ {ticker.replace('KRW-','')} 재매수 금지 중 (잔여 {remaining}분)")
                return True
            else:
                del self.rebuy_blocked[ticker]
        return False

    def record_stoploss(self, ticker):
        today = today_str()
        if ticker not in self.daily_stoploss or self.daily_stoploss[ticker]["date"] != today:
            self.daily_stoploss[ticker] = {"count": 0, "date": today}
        self.daily_stoploss[ticker]["count"] += 1
        count = self.daily_stoploss[ticker]["count"]
        # 30분 재매수 금지만 적용 (당일 완전금지 제거)
        unblock_time = datetime.datetime.now() + datetime.timedelta(minutes=CONFIG["rebuy_block_minutes"])
        self.rebuy_blocked[ticker] = unblock_time
        logger.warning(f"🚫 {ticker.replace('KRW-','')} {count}회 손절 → {CONFIG['rebuy_block_minutes']}분 재매수 금지 (해제: {unblock_time.strftime('%H:%M')})")
        self._save_bot_bought()

    def _reset_surge_flags(self, ticker):
        """🔧 서지 관련 모든 플래그 초기화 (전환 시 사용)
        
        사용처:
        - 매집 → 서지 전환
        - V자 → 서지 전환
        - 서지 → 매집 복귀
        - 노말 → 서지 승격
        
        초기화 대상:
        - 익절 플래그: tp1, tp2
        - 불타기 플래그: 1차/2차/3차
        - 트레일링 플래그: half_done
        - 세력털기 감지 플래그: whale_warn_sold
        """
        if ticker not in self.bought_coins:
            return
        reset_flags = [
            "surge_trail_half_done",
            "surge_add_buy_done",
            "surge_add_buy_2_done",
            "surge_add_buy_3_done",
            "surge_tp1_done",
            "surge_tp2_done",
            "whale_warn_sold",
            "trailing_active",
            "trail_floor_pct",
            "breakeven_armed",
            "breakout_add_count",      # 🔥 돌파 불타기 카운터
            "last_breakout_time",      # 🔥 직전 돌파 시점
            "acc_breakeven_armed",     # 🛡️ 매집 본전 잠금 (전환 시 초기화)
            "norm_breakeven_armed",    # 🛡️ 노말 본전 잠금 (전환 시 초기화)
            "acc_extend_count",        # ⏰ 매집 타임아웃 연장 횟수
            "acc_tp1_done",            # 💰 매집 +2% TP1 완료 플래그
            "acc_trail_high",          # 💰 매집 TP1 후 트레일링 최고점
        ]
        for f in reset_flags:
            if f in ("trail_floor_pct", "acc_trail_high"):
                self.bought_coins[ticker][f] = None
            elif f in ("breakout_add_count", "acc_extend_count"):
                self.bought_coins[ticker][f] = 0
            elif f == "last_breakout_time":
                self.bought_coins[ticker][f] = ""
            else:
                self.bought_coins[ticker][f] = False
        return

    def _warn_if_quota_exceeded(self, ticker, from_type, to_type):
        """전환 후 쿼터 초과 시 경고 (옵션 A+D)
        
        전환은 허용하되, 쿼터 위반이 발생하면 로그 + 텔레그램 경고.
        다음 봇 재시작 시 _rebalance_to_slot_quota가 자동 정리함.
        """
        slot_limits = get_dynamic_slots()
        limit = slot_limits.get(to_type)
        if limit is None:
            return  # surge는 쿼터 없음
        
        # 전환 후 해당 타입 카운트 (이미 self.bought_coins 업데이트됨)
        count = sum(
            1 for bc in self.bought_coins.values()
            if bc.get("buy_type") == to_type
        )
        
        if count > limit:
            coin = ticker.replace('KRW-','')
            logger.warning(
                f"⚠️  [쿼터초과] {coin} {from_type}→{to_type} 전환 | "
                f"{to_type} {count}/{limit} (초과 {count-limit}개) | "
                f"다음 재시작 시 자동 정리됨"
            )
            try:
                send_telegram(
                    f"⚠️ <b>쿼터 초과 경고</b>\n"
                    f"코인: {coin}\n"
                    f"전환: {from_type} → {to_type}\n"
                    f"현재: {to_type} {count}/{limit} (+{count-limit})\n"
                    f"→ 다음 재시작 시 자동 정리"
                )
            except Exception:
                pass

    def _calculate_vitality_score(self, ticker, info):
        """🧠 코인의 '가망 없음' 점수 계산 (높을수록 정리 우선순위)
        
        데이터 근거: 어제 매집 손실 27건 분석 결과
        - 손실 마감 코인의 100%가 "최근 추세 약화" 패턴
        - 변동폭 작은 코인이 손실 위험 높음
        
        점수 항목:
        - 추세 약화 (최근 3봉 < 초기 3봉): +3점
        - 변동폭 1.5% 미만 (죽은 코인): +1점
        - 본전잠금 미발동: +1점
        - 매집 4시간 경과 미수익: +2점
        """
        try:
            df = safe_api_call(pyupbit.get_ohlcv, ticker, 
                              interval=CONFIG["candle_interval"], count=20)
            if df is None or len(df) < 10:
                return 0  # 데이터 부족 시 보호
            
            buy_price = info.get("buy_price", 0)
            if buy_price <= 0:
                return 0
            
            close = df["close"]
            # 보유 시간 추정 (최근 N봉으로 분석)
            n = min(15, len(close))
            recent_pnls = [(c - buy_price) / buy_price * 100 for c in close.iloc[-n:]]
            
            score = 0
            reasons = []
            
            # ① 추세 약화: 최근 3봉 평균 < 초기 3봉 평균 → +3점
            if len(recent_pnls) >= 6:
                first_3_avg = sum(recent_pnls[:3]) / 3
                last_3_avg  = sum(recent_pnls[-3:]) / 3
                if last_3_avg < first_3_avg:
                    score += 3
                    reasons.append(f"추세약화{last_3_avg-first_3_avg:+.1f}p")
            
            # ② 변동폭 1.5% 미만 (죽은 코인) → +1점
            range_pnl = max(recent_pnls) - min(recent_pnls)
            if range_pnl < 1.5:
                score += 1
                reasons.append(f"좁은변동{range_pnl:.1f}%")
            
            # ③ 본전잠금 미발동 → +1점
            if not (info.get("acc_breakeven_armed", False) or 
                    info.get("norm_breakeven_armed", False)):
                score += 1
                reasons.append("미증명")
            
            # ④ 매집 4시간 경과 미수익 → +2점 (가망 없음 강력)
            try:
                buy_time = info.get("buy_time", "")
                if buy_time:
                    elapsed_h = (datetime.datetime.now() - 
                                datetime.datetime.fromisoformat(buy_time)).total_seconds() / 3600
                    current_pnl = recent_pnls[-1] if recent_pnls else 0
                    if elapsed_h >= 4.0 and current_pnl < 0.5:
                        score += 2
                        reasons.append(f"4h경과 {current_pnl:+.1f}%")
            except Exception:
                pass
            
            return score, reasons
        except Exception:
            return 0, []

    def _rebalance_to_slot_quota(self):
        """슬롯 쿼터 초기 정리 (봇 시작 시 1회 실행)
        
        각 전략별 쿼터(strategy_slots) 초과 시:
        - 수익률 낮은 순으로 정렬
        - -5% 이하 손실은 보호 (회복 대기, 무리한 손절 방지)
        - +3% 이상 수익은 보호 (런너 유지, 더 먹게 내버려둠)
        - 중간 구간(-5% ~ +3%)에서 쿼터 맞춰질 때까지 청산
        
        목적: 매집/노말이 슬롯을 독점해서 서지(BLUR) 못 잡는 문제 해결
        """
        slot_limits = get_dynamic_slots()
        if not slot_limits:
            return
        
        logger.info("=" * 55)
        logger.info("⚖️  [슬롯 쿼터 초기 정리] 시작")
        logger.info("=" * 55)
        
        total_cut = 0
        cut_summary = []
        
        for strategy, limit in slot_limits.items():
            # 해당 전략 코인 목록 (출신성분 기준)
            strategy_coins = [
                (ticker, info) for ticker, info in self.bought_coins.items()
                if info.get("original_type", info.get("buy_type")) == strategy
            ]
            
            count = len(strategy_coins)
            if count <= limit:
                logger.info(f"  ✅ {strategy}: {count}/{limit} OK")
                continue
            
            excess = count - limit
            logger.warning(
                f"  ⚠️  {strategy}: {count}/{limit} (초과 {excess}개)"
            )
            
            # 수익률 + 가망점수 계산
            with_pnl = []
            for ticker, info in strategy_coins:
                try:
                    current = get_current_price_safe(ticker)
                    buy_price = info.get("buy_price", 0)
                    if current and buy_price > 0:
                        pnl = (current - buy_price) / buy_price * 100
                        # 🧠 가망점수 계산 (높을수록 정리 우선)
                        score_result = self._calculate_vitality_score(ticker, info)
                        if isinstance(score_result, tuple):
                            score, reasons = score_result
                        else:
                            score, reasons = score_result, []
                        with_pnl.append((ticker, pnl, info, score, reasons))
                except Exception:
                    continue
            
            # 🧠 정렬 기준: 가망점수 높은 순 → 같으면 수익률 낮은 순
            # → "죽어가는 코인" 우선 청산, 활기찬 코인 보호
            with_pnl.sort(key=lambda x: (-x[3], x[1]))
            
            # 청산 대상 선정 (보호 규칙 적용)
            cut_targets = []
            protected = []
            for ticker, pnl, info, score, reasons in with_pnl:
                if len(cut_targets) >= excess:
                    break
                # 🚀 현재 buy_type이 승격된 상태면 보호 (매집→서지 성공한 코인)
                current_type = info.get("buy_type", strategy)
                if current_type == "surge" and strategy != "surge":
                    protected.append((ticker, pnl, f"🚀승격(→surge)", score))
                    continue
                # 🛡️ 본전잠금 활성화 보호 (+1% 한 번 찍은 실력 있는 코인)
                if info.get("acc_breakeven_armed", False) or info.get("norm_breakeven_armed", False):
                    protected.append((ticker, pnl, "🛡️본전잠금", score))
                    continue
                # -5% 이하 손실 보호 (회복 대기)
                if pnl <= -5.0:
                    protected.append((ticker, pnl, "🛡️-5%이하", score))
                    continue
                # 💰 +1% 이상 수익 보호 (본전잠금 트리거와 통일)
                if pnl >= 1.0:
                    protected.append((ticker, pnl, "💰+1%이상", score))
                    continue
                cut_targets.append((ticker, pnl, score, reasons))
            
            # 보호 리스트 로그
            for ticker, pnl, reason, score in protected:
                logger.info(f"    {reason} {ticker.replace('KRW-','')} {pnl:+.2f}% 보호 (가망점수 {score})")
            
            # 폴백: 중간 구간에서 충분히 못 뽑으면 경고
            if len(cut_targets) < excess:
                logger.warning(
                    f"    ⚠️ 중간구간에서 {len(cut_targets)}개만 청산 가능 "
                    f"(필요 {excess}개). 나머지는 자연 회복 대기"
                )
            
            # 청산 실행 (가망점수 높은 순)
            for ticker, pnl, score, reasons in cut_targets:
                reason_str = ",".join(reasons) if reasons else "기본"
                logger.warning(
                    f"    ✂️  [슬롯정리] {ticker.replace('KRW-','')} "
                    f"({strategy}) {pnl:+.2f}% | 가망점수 {score} ({reason_str}) → 청산"
                )
                try:
                    result = self.sell(
                        ticker, portion=1.0,
                        reason=f"[슬롯정리] {strategy} 쿼터 초과 ({pnl:+.2f}%, 점수{score})"
                    )
                    cut_summary.append((ticker, strategy, pnl))
                    total_cut += 1
                    time.sleep(1.5)  # 연속 매도 간격
                except Exception as e:
                    logger.error(f"    ❌ {ticker} 청산 실패: {e}")
        
        logger.info("=" * 55)
        logger.info(f"⚖️  [슬롯 쿼터 정리] 완료 - 총 {total_cut}개 청산")
        logger.info("=" * 55)
        
        # 텔레그램 요약 알림
        if cut_summary:
            msg = f"⚖️ <b>슬롯 쿼터 초기 정리</b>\n총 {total_cut}개 청산:\n\n"
            for ticker, strategy, pnl in cut_summary:
                emoji = "💰" if pnl >= 0 else "🔻"
                msg += f"{emoji} {ticker.replace('KRW-','')} ({strategy}) {pnl:+.2f}%\n"
            msg += f"\n→ 서지 슬롯 확보 완료 ⚾"
            send_telegram(msg)

    def get_balance(self, ticker="KRW"):
        try:
            time.sleep(0.3)
            b = self.upbit.get_balance(ticker)
            return b if b else 0
        except Exception:
            return 0

    def buy(self, ticker, amount_krw, buy_type="normal"):
        # ⚡ trade_lock으로 매수/매도 직렬화 (이중 주문 방지)
        with self.trade_lock:
            try:
                if self.is_protected(ticker) or amount_krw < CONFIG["min_trade_krw"]:
                    return None
                current = get_current_price_safe(ticker)
                if not current:
                    return None

                type_emoji = "🚀" if buy_type == "surge" else "🐋" if buy_type == "whale_hunt" else "🔥" if buy_type == "wakeup" else "🏦" if buy_type == "accumulation" else "📈" if buy_type == "v_reversal" else "📶"
                time.sleep(0.3)

                if buy_type == "surge":
                    # 🚀 surge → 시장가
                    logger.info(f"🟢 [봇3] 매수 [{buy_type}] {type_emoji}: {ticker} | {amount_krw:,.0f}원 | {current:,.4f}원 (시장가)")
                    result = self.upbit.buy_market_order(ticker, amount_krw)
                else:
                    # 📶 노말 → 시장가 (추세 전략, 빠른 진입!)
                    logger.info(f"🟢 [봇3] 매수 [{buy_type}] {type_emoji}: {ticker} | {amount_krw:,.0f}원 | {current:,.4f}원 (시장가)")
                    result = self.upbit.buy_market_order(ticker, amount_krw)

                if result and "error" not in result:
                    # 실제 체결가 추출 (슬리피지 반영)
                    try:
                        actual_price = float(result.get("avg_buy_price", current))
                        actual_qty   = float(result.get("volume", amount_krw / current))
                        if actual_price <= 0: actual_price = current
                        if actual_qty   <= 0: actual_qty   = amount_krw / current
                    except Exception:
                        actual_price = current
                        actual_qty   = amount_krw / current

                    if ticker in self.bought_coins:
                        # ── 불타기: 기존 포지션 유지하면서 평균단가 업데이트 ──
                        existing = self.bought_coins[ticker]
                        prev_qty   = existing.get("buy_qty", 0)
                        prev_price = existing.get("buy_price", actual_price)
                        new_qty    = prev_qty + actual_qty
                        avg_price  = (prev_price * prev_qty + actual_price * actual_qty) / new_qty if new_qty > 0 else actual_price
                        self.bought_coins[ticker]["buy_price"]  = round(avg_price, 8)
                        self.bought_coins[ticker]["buy_qty"]    = new_qty
                        self.bought_coins[ticker]["amount_krw"] = existing.get("amount_krw", 0) + amount_krw
                        # highest는 현재 최고가 유지 (덮어쓰지 않음)
                        logger.info(
                            f"🔥 [불타기 평균단가] {ticker.replace('KRW-','')} | "
                            f"이전: {prev_price:.4f} → 신규: {actual_price:.4f} → 평균: {avg_price:.4f}"
                        )
                    else:
                        # ── 신규 매수 ──
                        entry_eth_st = get_eth_status()
                        self.bought_coins[ticker] = {
                            "buy_price":        actual_price,
                            "highest":          actual_price,
                            "amount_krw":       amount_krw,
                            "buy_qty":          actual_qty,
                            "tp1_done":         False,
                            "breakeven_armed":  False,
                            "buy_type":         buy_type,
                            "original_type":    buy_type,   # 🎯 출신성분 (불변) - 쿼터 기준
                            "buy_time":         datetime.datetime.now().isoformat(),
                            "entry_eth_status": entry_eth_st,
                        }
                        self.bot_bought_tickers.add(ticker)
                    self._save_bot_bought()
                    self.history["trades"].append({
                        "type": "BUY", "ticker": ticker,
                        "price": current, "amount_krw": amount_krw,
                        "buy_type": buy_type,
                        "time": datetime.datetime.now().isoformat()
                    })
                    # 🔧 history 크기 제한 (장기 운영 메모리 관리)
                    if len(self.history["trades"]) > 5000:
                        self.history["trades"] = self.history["trades"][-5000:]
                    save_json(CONFIG["trade_history_file"], self.history)
                    type_str = "🚀 급등 포착" if buy_type == "surge" else "🐋 세력매집찾기" if buy_type == "whale_hunt" else "🔥 호가깨어남" if buy_type == "wakeup" else "🏦 세력매집" if buy_type == "accumulation" else "📈 V자반등" if buy_type == "v_reversal" else "📶 일반"
                    logger.info(f"✅ [봇3] 매수 완료 [{type_str}]: {ticker} @ {current:,.4f}원")
                    send_telegram(
                        f"{type_emoji} <b>매수!</b> [{type_str}]\n"
                        f"코인: {ticker.replace('KRW-','')}\n"
                        f"가격: {current:,.2f}원\n"
                        f"금액: {amount_krw:,.0f}원"
                    )
                    return result
                else:
                    logger.error(f"❌ [봇3] 매수 실패: {result}")
                    return None
            except Exception as e:
                logger.error(f"❌ [봇3] 매수 오류: {e}")
                return None

    def _queue_sell(self, ticker, portion=1.0, reason="", is_stoploss=False):
        """⚡ 매도 큐에 넣기 (check_position에서 호출)
        
        직접 매도하지 않고 큐에 넣어서 매도 워커가 병렬 처리하게 함.
        → position_thread가 매도 대기로 블록되지 않음
        → 폭락 상황에서 여러 코인 동시 매도 가능
        
        우선순위: 손실 큰 것 먼저 + 손절은 최우선
        """
        # 큐 없으면 fallback (시작 직후 등 엣지 케이스)
        if self.sell_queue is None:
            return self.sell(ticker, portion, reason, is_stoploss)
        
        if ticker not in self.bought_coins:
            return
        
        # 이미 큐에 있거나 매도 진행 중 → 중복 큐 방지
        if self.bought_coins[ticker].get("pending_sell", False):
            return
        
        # pending_sell 선마킹 → 중복 큐/중복 호출 차단
        self.bought_coins[ticker]["pending_sell"] = True
        
        # 우선순위 계산: priority가 작을수록 먼저 처리됨
        # - is_stoploss면 -1000 기저 페널티 (최우선)
        # - 그 위에 pnl_pct (손실 클수록 priority 작음)
        try:
            current   = get_current_price_safe(ticker)
            buy_price = self.bought_coins[ticker].get("buy_price", 0)
            if current and buy_price > 0:
                pnl_pct = (current - buy_price) / buy_price * 100
            else:
                pnl_pct = 0
        except Exception:
            pnl_pct = 0
        
        priority = (-1000.0 if is_stoploss else 0.0) + pnl_pct
        
        try:
            # 튜플 형식: (priority, 시각, ticker, portion, reason, is_stoploss)
            # 시각은 tiebreaker (같은 priority일 때 먼저 들어온 것 먼저)
            self.sell_queue.put_nowait((priority, time.time(), ticker, portion, reason, is_stoploss))
            logger.info(
                f"📥 [매도큐] {ticker.replace('KRW-','')} | "
                f"priority: {priority:+.2f} | {reason[:40]}"
            )
        except Exception as e:
            logger.error(f"⚠️ 매도큐 put 실패 {ticker}: {e}")
            # 실패 시 pending_sell 해제 (재시도 가능하게)
            if ticker in self.bought_coins:
                self.bought_coins[ticker]["pending_sell"] = False

    def sell(self, ticker, portion=1.0, reason="", is_stoploss=False):
        # ⚡ trade_lock으로 매수/매도 직렬화 (이중 주문 방지)
        with self.trade_lock:
            try:
                if self.is_protected(ticker):
                    return None
                # pending_sell 마킹 (이미 마킹돼 있어도 OK)
                # ※ 주의: _queue_sell 경유 호출에서는 이미 True 상태로 진입함
                # 여기서 "이미 진행 중이면 return" 체크를 하면 워커가 막힘 → 제거
                if ticker in self.bought_coins:
                    self.bought_coins[ticker]["pending_sell"] = True
                coin = ticker.replace("KRW-", "")
                time.sleep(0.3)
                balance = self.upbit.get_balance(coin)
                if not balance or balance <= 0:
                    # 매도 대상 잔고 없음 → pending_sell 해제
                    if ticker in self.bought_coins:
                        self.bought_coins[ticker]["pending_sell"] = False
                    return None
                current = get_current_price_safe(ticker)
                if not current:
                    if ticker in self.bought_coins:
                        self.bought_coins[ticker]["pending_sell"] = False
                    return None
                time.sleep(0.3)
                result = self.upbit.sell_market_order(ticker, balance * portion)
                if result and "error" not in result:
                    if ticker in self.bought_coins:
                        buy_price  = self.bought_coins[ticker]["buy_price"]
                        amount_krw = self.bought_coins[ticker]["amount_krw"]
                        buy_qty    = self.bought_coins[ticker].get("buy_qty", amount_krw / buy_price if buy_price > 0 else 0)
                        pnl        = ((current - buy_price) / buy_price) * 100 if buy_price > 0 else 0

                        # 수량 기준 실제 매도금액 계산
                        fee_rate    = CONFIG.get("fee_rate", 0.0005)
                        sell_qty    = buy_qty * portion              # 실제 매도 수량
                        sell_amt    = sell_qty * current             # 실제 매도 금액
                        buy_amt     = sell_qty * buy_price           # 매수 금액 (수량 기준)
                        buy_fee     = buy_amt  * fee_rate            # 매수 수수료
                        sell_fee    = sell_amt * fee_rate            # 매도 수수료
                        fee_krw     = buy_fee + sell_fee             # 총 수수료
                        pnl_krw     = sell_amt - buy_amt             # 실제 손익
                        net_pnl     = pnl_krw - fee_krw             # 수수료 차감 순수익

                        emoji = "💰" if net_pnl >= 0 else "🔻"
                        logger.info(
                            f"{emoji} [봇3] 매도: {ticker} | "
                            f"{pnl:+.2f}% | "
                            f"손익: {pnl_krw:+,.0f}원 | "
                            f"수수료: -{fee_krw:,.0f}원 | "
                            f"실수익: {net_pnl:+,.0f}원 | "
                            f"{reason}"
                        )
                        send_telegram(
                            f"{emoji} <b>매도!</b>\n"
                            f"코인: {ticker.replace('KRW-','')}\n"
                            f"수익률: {pnl:+.2f}%\n"
                            f"실수익: {net_pnl:+,.0f}원\n"
                            f"사유: {reason}"
                        )
                        if portion >= 1.0:
                            del self.bought_coins[ticker]
                            # 전량 매도 시 가격 이력도 정리 (메모리 누수 방지)
                            self.price_history.pop(ticker, None)
                            if is_stoploss:
                                self.record_stoploss(ticker)
                            else:
                                # 익절 시 5분 재매수 금지
                                if net_pnl > 0:
                                    block_time = datetime.datetime.now() + datetime.timedelta(minutes=5)
                                    self.rebuy_blocked[ticker] = block_time
                                    logger.debug(f"⏳ {ticker.replace('KRW-','')} 익절 후 5분 재매수 금지 (해제: {block_time.strftime('%H:%M')})")
                            self.bot_bought_tickers.discard(ticker)
                            self._save_bot_bought()
                        else:
                            if ticker in self.bought_coins:
                                self.bought_coins[ticker]["tp1_done"] = True
                                # 절반 매도 후 남은 수량/금액 업데이트
                                self.bought_coins[ticker]["buy_qty"]    = buy_qty * (1 - portion)
                                self.bought_coins[ticker]["amount_krw"] = amount_krw * (1 - portion)
                                # 부분매도 후 pending_sell 해제 (남은 포지션 관리 재개)
                                self.bought_coins[ticker]["pending_sell"] = False
                                self._save_bot_bought()
                    self.history["trades"].append({
                        "type": "SELL", "ticker": ticker,
                        "price": current, "portion": portion,
                        "reason": reason,
                        "time": datetime.datetime.now().isoformat()
                    })
                    # 🔧 history 크기 제한 (장기 운영 메모리 관리)
                    if len(self.history["trades"]) > 5000:
                        self.history["trades"] = self.history["trades"][-5000:]
                    save_json(CONFIG["trade_history_file"], self.history)
                    return result
                else:
                    # 매도 주문 실패 → pending_sell 해제
                    logger.error(f"❌ [봇3] 매도 실패: {result}")
                    if ticker in self.bought_coins:
                        self.bought_coins[ticker]["pending_sell"] = False
                    return None
            except Exception as e:
                logger.error(f"❌ [봇3] 매도 오류: {e}")
                # 매도 실패 시 pending_sell 해제
                if ticker in self.bought_coins:
                    self.bought_coins[ticker]["pending_sell"] = False
                return None

    def check_position(self, ticker):
        if ticker not in self.bought_coins:
            return
        # 이미 매도 진행 중인 경우 스킵 (이중 청산 방지)
        if self.bought_coins.get(ticker, {}).get("pending_sell", False):
            return

        info            = self.bought_coins[ticker]
        current         = get_current_price_safe(ticker)
        if not current:
            return

        buy_price       = info["buy_price"]
        highest         = info.get("highest", buy_price)

        # ⚠️ 데이터 무결성 방어 (ZeroDivision 차단)
        if not buy_price or buy_price <= 0:
            logger.error(f"⚠️ [데이터오류] {ticker.replace('KRW-','')} buy_price={buy_price} → 포지션 체크 스킵")
            return
        if not highest or highest <= 0:
            highest = buy_price
            self.bought_coins[ticker]["highest"] = buy_price

        pnl_pct         = ((current - buy_price) / buy_price) * 100
        breakeven_armed = info.get("breakeven_armed", False)
        buy_type        = info.get("buy_type", "normal")

        # 최고가 갱신
        if current > highest:
            self.bought_coins[ticker]["highest"] = current
            highest = current
        
        # ════════════════════════════════════════════════════════
        # 🛡️ Grace Period (매수 직후 5분 보호막)
        # ════════════════════════════════════════════════════════
        # 실전 데이터: 0~10분 초단기 매도 = -82.6% 손실
        # → 매수 후 5분간 특별 관리:
        #   +0.5% 도달 → 즉시 익절 (세력 털기 전 탈출)
        #   -0.5% 이탈 → 즉시 손절 (손실 확대 방지)
        #   거래량 폭발 + 하락 → 즉시 전량 (세력 공격)
        # 예외: whale_hunt/homerun은 grace 면제 (자체 로직)
        # ⭐ 예외: 전일대비 +10% 이상 강력 상승 → grace 면제 (홈런 기회 놓치지 않기)
        try:
            buy_time_str = info.get("buy_time", "")
            
            # ⭐ Grace Period 우회 조건
            grace_bypass = False
            if buy_type not in ("whale_hunt", "homerun", "active_watch"):
                try:
                    _dc = get_cached_daily_change(ticker)
                    if _dc >= 10.0:
                        grace_bypass = True  # +10% 이상은 grace 면제
                except Exception:
                    pass
            
            # 🔥 공격 모드 (09~20시)에는 Grace 비활성
            try:
                _tmode = get_time_mode()
                _tcfg = TIME_MODE_CONFIG.get(_tmode, {})
                if not _tcfg.get("grace_enabled", True):
                    grace_bypass = True
            except Exception:
                pass
            
            if buy_time_str and buy_type not in ("whale_hunt", "homerun", "active_watch") and not grace_bypass:
                from datetime import datetime
                try:
                    buy_dt = datetime.fromisoformat(buy_time_str)
                    elapsed_mins = (datetime.now() - buy_dt).total_seconds() / 60
                except Exception:
                    elapsed_mins = 999  # 파싱 실패 → grace 해제
                
                # 🌙 P5: 야간(normal/accumulate) Grace 완화
                # 20시~08:30 = 변동성 크고 느리게 움직임 → 기다려주기
                _is_night = _tmode in ("normal", "accumulate")
                _grace_period = 10.0 if _is_night else 5.0   # 5분 → 10분
                _grace_stop = -0.8 if _is_night else -0.5    # -0.5% → -0.8%
                
                if elapsed_mins <= _grace_period:  # grace period
                    # ⚡ 초단기 익절 (세력 털기 전 탈출)
                    if pnl_pct >= 0.5:
                        logger.warning(
                            f"🛡️ [Grace익절] {ticker.replace('KRW-','')} "
                            f"{elapsed_mins:.1f}분 | {pnl_pct:+.2f}% → 초단기 익절!"
                        )
                        self._queue_sell(
                            ticker, portion=1.0,
                            reason=f"Grace 익절 {elapsed_mins:.1f}분"
                        )
                        return
                    
                    # ⚡ 초단기 손절 (손실 확대 방지)
                    if pnl_pct <= _grace_stop:
                        logger.warning(
                            f"🛡️ [Grace손절] {ticker.replace('KRW-','')} "
                            f"{elapsed_mins:.1f}분 | {pnl_pct:+.2f}% → 초단기 손절! "
                            f"({'야간완화' if _is_night else '기본'})"
                        )
                        self._queue_sell(
                            ticker, portion=1.0,
                            reason=f"Grace 손절 {elapsed_mins:.1f}분"
                        )
                        return
                    
                    # ⚡ 세력 공격 감지 (거래량 폭발 + 가격 하락)
                    try:
                        df_grace = safe_api_call(pyupbit.get_ohlcv, ticker,
                                                 interval="minute1", count=5)
                        if df_grace is not None and len(df_grace) >= 4:
                            curr_vol = df_grace["volume"].iloc[-1]
                            avg_vol  = df_grace["volume"].iloc[-4:-1].mean()
                            curr_close = df_grace["close"].iloc[-1]
                            curr_open  = df_grace["open"].iloc[-1]
                            
                            # 🌙 야간 완화: 3배 → 5배 (야간은 변동 크니까 여유)
                            _vol_threshold = 5.0 if _is_night else 3.0
                            
                            if (avg_vol > 0 and curr_vol >= avg_vol * _vol_threshold 
                                and curr_close < curr_open):
                                vol_mult = curr_vol / avg_vol
                                logger.warning(
                                    f"🛡️ [Grace탈출] {ticker.replace('KRW-','')} "
                                    f"{elapsed_mins:.1f}분 | 세력공격 감지 "
                                    f"(거래량 {vol_mult:.1f}배 + 음봉) → 즉시 탈출!"
                                )
                                self._queue_sell(
                                    ticker, portion=1.0,
                                    reason=f"Grace 세력탈출 {elapsed_mins:.1f}분"
                                )
                                return
                    except Exception:
                        pass
        except Exception as e:
            logger.debug(f"Grace period 오류 {ticker}: {e}")

        # ════════════════════════════════════════════════════════
        # 🚨🚨🚨 1차 방어선: 무조건 손절 (무거운 지표 계산 전!)
        # ════════════════════════════════════════════════════════
        # "잃지 않는 것이 먼저"를 위한 최우선 방어선
        # 아래 조건 중 하나라도 만족하면 즉시 전량 매도 (유예 없음)
        # - 하드 재난선: 손절 기준 × 2.0 초과 폭락 (BB 안이든 뭐든 끝)
        # - 급락 감지: 최근 30초 이내 가격 대비 -3% 이상 폭락
        try:
            # 가격 이력 기록 (30초치만 유지)
            now_ts = time.time()
            hist = self.price_history.setdefault(ticker, [])
            hist.append((now_ts, current))
            # 30초보다 오래된 것 제거
            self.price_history[ticker] = [(t, p) for t, p in hist if now_ts - t <= 30]

            base_stop_hard = get_dynamic_stop(buy_price, buy_type if buy_type in ("accumulation","v_reversal","normal","surge","wakeup","whale_hunt","active_watch") else "normal")
            disaster_line  = base_stop_hard * 2.0   # 예: -5% × 2.0 = -10%

            # A. 하드 재난선 (손절선 × 2배 폭락)
            if pnl_pct <= disaster_line:
                logger.warning(
                    f"🚨🚨 [하드재난선] {ticker.replace('KRW-','')} | "
                    f"{pnl_pct:+.2f}% ≤ {disaster_line}% → 무조건 전량 매도!"
                )
                send_telegram(
                    f"🚨🚨 <b>하드재난선 발동!</b>\n"
                    f"코인: {ticker.replace('KRW-','')}\n"
                    f"손실: {pnl_pct:+.2f}%\n"
                    f"→ 즉시 전량 매도"
                )
                self._queue_sell(ticker, portion=1.0,
                          reason=f"[재난선] {pnl_pct:+.2f}%",
                          is_stoploss=True)
                return

            # B. 급락 감지 (30초 이내 피크 가격 대비)
            if len(self.price_history[ticker]) >= 3:
                recent_max = max(p for _, p in self.price_history[ticker])
                if recent_max > 0:
                    crash_pct = (current - recent_max) / recent_max * 100
                    # 최근 30초 고점 대비 -3% 이상 폭락
                    if crash_pct <= -3.0:
                        logger.warning(
                            f"⚡ [급락감지] {ticker.replace('KRW-','')} | "
                            f"30초내 {crash_pct:.2f}% 폭락 | 수익률 {pnl_pct:+.2f}% → 즉시 매도!"
                        )
                        send_telegram(
                            f"⚡ <b>급락 감지!</b>\n"
                            f"코인: {ticker.replace('KRW-','')}\n"
                            f"30초 {crash_pct:.2f}% 폭락\n"
                            f"수익률: {pnl_pct:+.2f}%\n"
                            f"→ 즉시 매도"
                        )
                        self._queue_sell(ticker, portion=1.0,
                                  reason=f"[급락감지] 30초 {crash_pct:.2f}%",
                                  is_stoploss=pnl_pct < 0)
                        return
        except Exception as _e:
            logger.debug(f"패닉 가드 오류 {ticker}: {_e}")

        # ════════════════════════════════════════════════════════
        # 2차 방어선부터: 기존 지표 기반 매도 로직
        # ════════════════════════════════════════════════════════

        # ── 🚨 공통 매도 조건 (전략별 캔들 기준) ──
        # 🔧 P1: 노말도 5분봉으로 통일 (매수 기준과 일치)
        #   - surge: 1분봉 (빠른 포착)
        #   - normal/accumulation/v_reversal: 5분봉 (안정적 추세)
        try:
            _interval_common = (CONFIG["candle_interval_acc"]
                                if buy_type in ("accumulation", "v_reversal", "normal", "wakeup", "whale_hunt")
                                else CONFIG["candle_interval"])
            df_ab = safe_api_call(
                pyupbit.get_ohlcv, ticker,
                interval=_interval_common, count=25
            )
            if df_ab is not None and len(df_ab) >= 22:
                close_ab = df_ab["close"]
                open_ab  = df_ab["open"]
                high_ab  = df_ab["high"]
                vol_ab   = df_ab["volume"]

                curr_close = close_ab.iloc[-1]
                curr_open  = open_ab.iloc[-1]
                curr_high  = high_ab.iloc[-1]
                curr_vol   = vol_ab.iloc[-1]
                curr_body  = abs(curr_close - curr_open)
                is_bearish = curr_close < curr_open
                avg_body   = abs(close_ab.iloc[-21:-1] - open_ab.iloc[-21:-1]).mean()
                avg_vol    = vol_ab.iloc[-21:-1].mean()

                # ── 🆕 🎯 스마트 세력털기 감지 (점수제, 조기 대응) ──
                # 기존 세력경고(5배 볼륨)보다 민감하게 작동
                # 수익권 +3% 이상, surge 전략에서만 발동
                # 2점: 50% 익절 / 3점 이상: 전량 익절
                if buy_type == "surge" and pnl_pct >= 3.0:
                    try:
                        warning_score = 0
                        reasons = []
                        
                        # 1. 최근 3봉 중 2봉 이상 윗꼬리 (세력 위에서 던지는 중)
                        if len(close_ab) >= 4:
                            wick_count = 0
                            for i in range(-3, 0):
                                body_i = abs(close_ab.iloc[i] - open_ab.iloc[i])
                                wick_i = high_ab.iloc[i] - max(close_ab.iloc[i], open_ab.iloc[i])
                                if body_i > 0 and wick_i >= body_i * 1.2:
                                    wick_count += 1
                            if wick_count >= 2:
                                warning_score += 2
                                reasons.append(f"연속윗꼬리{wick_count}/3")
                        
                        # 2. 거래량 증가인데 가격 정체/하락 (분산 매도 패턴)
                        # 5배까진 아니지만 1.5배 이상 + 가격 안 오름
                        if avg_vol > 0 and curr_vol >= avg_vol * 1.5 and price_change_pct < 0.3:
                            warning_score += 2
                            reasons.append(f"분산매도{curr_vol/avg_vol:.1f}배")
                        
                        # 3. BB 상단 터치 후 밀려남 (저항선 거부)
                        bb_mid_ab = close_ab.rolling(20).mean()
                        bb_std_ab = close_ab.rolling(20).std()
                        bb_up_ab  = bb_mid_ab + 2 * bb_std_ab
                        if len(bb_up_ab) >= 2:
                            prev_hit_bb  = high_ab.iloc[-2] >= bb_up_ab.iloc[-2]
                            now_below_bb = curr_close < bb_up_ab.iloc[-1] * 0.995
                            if prev_hit_bb and now_below_bb:
                                warning_score += 1
                                reasons.append("BB상단밀림")
                        
                        # 4. EMA5 하향 이탈 (추세 꺾임 초기 신호)
                        if len(close_ab) >= 6:
                            ema5_ab_v = close_ab.ewm(span=5, adjust=False).mean()
                            if (curr_close < ema5_ab_v.iloc[-1] and
                                close_ab.iloc[-2] >= ema5_ab_v.iloc[-2]):
                                warning_score += 2
                                reasons.append("EMA5이탈")
                        
                        # 5. RSI divergence (가격 신고가인데 RSI 하락)
                        if len(close_ab) >= 15:
                            delta_ab = close_ab.diff()
                            gain_ab  = delta_ab.clip(lower=0).rolling(14).mean()
                            loss_ab  = (-delta_ab.clip(upper=0)).rolling(14).mean()
                            rs_ab    = gain_ab / loss_ab
                            rsi_ab   = 100 - (100 / (1 + rs_ab))
                            # 현재가 직전 5봉 최고가 넘었는데 RSI는 안 넘으면 divergence
                            try:
                                recent_price_high = close_ab.iloc[-6:-1].max()
                                recent_rsi_high   = rsi_ab.iloc[-6:-1].max()
                                if (curr_close >= recent_price_high and
                                    rsi_ab.iloc[-1] < recent_rsi_high - 3):
                                    warning_score += 1
                                    reasons.append("RSI divergence")
                            except Exception:
                                pass
                        
                        # 점수 기반 매도 결정
                        if warning_score >= 3:
                            # 강한 신호 → 전량 익절
                            reason_str = "+".join(reasons)
                            logger.warning(
                                f"🎯🎯 [세력털기L3] {ticker.replace('KRW-','')} | "
                                f"점수 {warning_score} | {reason_str} | {pnl_pct:+.2f}% → 전량!"
                            )
                            send_telegram(
                                f"🎯 <b>세력털기 감지 (전량)</b>\n"
                                f"코인: {ticker.replace('KRW-','')}\n"
                                f"점수: {warning_score}\n"
                                f"신호: {reason_str}\n"
                                f"수익률: {pnl_pct:+.2f}%"
                            )
                            self._queue_sell(ticker, portion=1.0,
                                      reason=f"[세력털기L3] {reason_str}")
                            return
                        elif warning_score >= 2:
                            # 중간 신호 → 50% 익절 (수익 확보, 잔량은 트레일링)
                            whale_warn_sold = self.bought_coins[ticker].get("whale_warn_sold", False)
                            if not whale_warn_sold:
                                reason_str = "+".join(reasons)
                                logger.warning(
                                    f"🎯 [세력털기L2] {ticker.replace('KRW-','')} | "
                                    f"점수 {warning_score} | {reason_str} | {pnl_pct:+.2f}% → 50% 익절!"
                                )
                                send_telegram(
                                    f"🎯 <b>세력털기 감지 (50%)</b>\n"
                                    f"코인: {ticker.replace('KRW-','')}\n"
                                    f"점수: {warning_score}\n"
                                    f"신호: {reason_str}\n"
                                    f"수익률: {pnl_pct:+.2f}%\n"
                                    f"→ 50% 익절, 잔량 트레일링 전환"
                                )
                                self._queue_sell(ticker, portion=0.5,
                                          reason=f"[세력털기L2] {reason_str}")
                                self.bought_coins[ticker]["whale_warn_sold"] = True
                                # 50% 매도 후 잔량은 타이트 트레일링 모드로 전환
                                self.bought_coins[ticker]["trailing_active"] = True
                                self.bought_coins[ticker]["trail_floor_pct"] = pnl_pct - 1.0  # 현재-1% 바닥
                                return
                    except Exception as _e:
                        logger.debug(f"세력털기 감지 오류 {ticker}: {_e}")

                # ── ① 세력 사전 경고: 긴 윗꼬리 + 고점 거래량 급증 ──
                # 단, 급등 후 고점 근처일 때만 (횡보 매집 구간 제외)
                upper_wick = curr_high - max(curr_close, curr_open)
                price_change_pct = (curr_close - close_ab.iloc[-2]) / close_ab.iloc[-2] * 100  # 부호 유지 (하락=음수)
                vol_surge_now = avg_vol > 0 and curr_vol >= avg_vol * 5.0
                long_upper_wick = curr_body > 0 and upper_wick >= curr_body * 2.0
                # 가격정체 = 거래량 폭발인데 가격이 하락 중일 때만 (세력 던지기)
                # 가격 횡보/상승 + 거래량 폭발 = 세력 매집 가능성 → 경고 안 함
                # accumulation: -1.5% 이상 하락 시만 (매집 중 노이즈 허용)
                # 기타: -0.5% 이상 하락 시 발동
                stall_threshold = -1.5 if buy_type == "accumulation" else -0.5
                price_stall = price_change_pct < stall_threshold
                near_high = highest > 0 and current >= highest * 0.97  # 최고점 -3% 이내

                if vol_surge_now and (long_upper_wick or price_stall) and near_high:
                    warn_reason = "윗꼬리+거래량폭발" if long_upper_wick else "거래량폭발+가격하락"
                    
                    # 🔧 미누님 요청: 수익권에서는 점진 청산 (50% + 잔량 트레일링)
                    # 어제 4건 피해 (JST/HYPER/ORBS/ZBT) 방지
                    if pnl_pct >= 0.5:
                        # 수익권: 50% 매도 → 잔량 트레일링 (홈런 기회 유지)
                        logger.warning(
                            f"⚠️ [세력경고-점진] {ticker.replace('KRW-','')} | {warn_reason} | "
                            f"거래량: {curr_vol/avg_vol:.1f}배 | {pnl_pct:+.2f}% → 50% 익절 + 잔량 트레일링!"
                        )
                        try:
                            send_telegram(
                                f"⚠️ <b>세력 경고 - 점진 청산</b>\n"
                                f"코인: {ticker.replace('KRW-','')}\n"
                                f"사유: {warn_reason}\n"
                                f"거래량: {curr_vol/avg_vol:.1f}배\n"
                                f"수익률: {pnl_pct:+.2f}%\n"
                                f"→ 50% 익절, 잔량 트레일링"
                            )
                        except Exception:
                            pass
                        self._queue_sell(ticker, portion=0.5,
                                  reason=f"세력경고 점진50% {warn_reason}")
                        # 잔량은 타이트 트레일링 모드
                        if ticker in self.bought_coins:
                            self.bought_coins[ticker]["whale_warn_sold"] = True
                            self.bought_coins[ticker]["trailing_active"] = True
                            self.bought_coins[ticker]["trail_floor_pct"] = max(0.0, pnl_pct - 1.5)  # 현재-1.5% 또는 본전
                            self._save_bot_bought()
                        return
                    else:
                        # 손실권/본전 근처: 기존대로 전량 매도
                        logger.warning(
                            f"⚠️ [세력경고] {ticker.replace('KRW-','')} | {warn_reason} | "
                            f"거래량: {curr_vol/avg_vol:.1f}배 | {pnl_pct:+.2f}% → 즉시 전량 익절!"
                        )
                        try:
                            send_telegram(
                                f"⚠️ <b>세력 이탈 경고!</b>\n"
                                f"코인: {ticker.replace('KRW-','')}\n"
                                f"사유: {warn_reason}\n"
                                f"거래량: {curr_vol/avg_vol:.1f}배\n"
                                f"수익률: {pnl_pct:+.2f}%\n"
                                f"→ 즉시 전량 매도!"
                            )
                        except Exception:
                            pass
                        self._queue_sell(ticker, portion=1.0,
                                  reason=f"세력경고 {warn_reason}",
                                  is_stoploss=pnl_pct < 0)
                        return

                # ── ② 장대음봉 감지 → 즉시 전량 손절 ──
                # EMA5↔EMA100 이격 1.5% 이상일 때만 적용
                # (횡보/매집 구간에서는 캔들 body가 작아 오발동 방지)
                ema5_ab  = close_ab.ewm(span=5,   adjust=False).mean()
                ema100_ab = close_ab.ewm(span=100, adjust=False).mean()
                ema_gap_pct = abs(ema5_ab.iloc[-1] - ema100_ab.iloc[-1]) / ema100_ab.iloc[-1] * 100
                in_trend = ema_gap_pct >= 1.5  # 이격 1.5% 이상 = 추세 중

                if is_bearish and avg_body > 0 and curr_body >= avg_body * 5.0 and in_trend:
                    logger.warning(
                        f"🚨 [장대음봉] {ticker.replace('KRW-','')} | "
                        f"음봉 {curr_body:.4f} / 평균 {avg_body:.4f} = "
                        f"{curr_body/avg_body:.1f}배 | EMA이격 {ema_gap_pct:.1f}% → 즉시 손절!"
                    )
                    self._queue_sell(ticker, portion=1.0,
                              reason=f"장대음봉 손절 {curr_body/avg_body:.1f}배",
                              is_stoploss=True)
                    return

                # ── ③ 양봉 잡아먹기 음봉 + EMA5 하향 마감 → 즉시 전량 매도 ──
                # EMA5↔EMA100 이격 2% 이상 (추세 중)일 때만 적용
                # 횡보/매집 구간에서는 작은 잡아먹기 음봉도 발생 → 무시
                prev_open_ab  = open_ab.iloc[-2]
                prev_close_ab = close_ab.iloc[-2]
                prev_is_bull  = prev_close_ab > prev_open_ab
                curr_ema5_ab  = ema5_ab.iloc[-1]
                engulf_gap_pct = abs(curr_ema5_ab - ema100_ab.iloc[-1]) / ema100_ab.iloc[-1] * 100
                in_trend_engulf = engulf_gap_pct >= 2.0  # 이격 2% 이상 = 추세 중

                engulf_bear = (
                    in_trend_engulf and                       # 추세 중일 때만!
                    prev_is_bull and                          # 이전봉 양봉
                    is_bearish and                            # 현재봉 음봉
                    curr_open >= prev_close_ab and            # 현재 시가 ≥ 이전 종가
                    curr_close <= prev_open_ab and            # 현재 종가 ≤ 이전 시가 (완전 잡아먹기)
                    curr_close < curr_ema5_ab                 # EMA5 아래 마감
                )

                if engulf_bear:
                    logger.warning(
                        f"🕯️ [잡아먹기] {ticker.replace('KRW-','')} | "
                        f"양봉({prev_open_ab:.4f}→{prev_close_ab:.4f}) 음봉에 잡아먹힘 | "
                        f"EMA이격 {engulf_gap_pct:.1f}% | "
                        f"EMA5({curr_ema5_ab:.4f}) 하향 마감 → 즉시 매도!"
                    )
                    self._queue_sell(ticker, portion=1.0,
                              reason=f"잡아먹기+EMA5하향",
                              is_stoploss=pnl_pct < 0)
                    return
        except Exception:
            pass

        # ── 🎯 10분봉 세력선 감시 (미누님 ENA/EDGE/CFG 차트) ──
        # "10분봉 기준으로 55일선을 깨지 않으면 계속 홀딩"
        # "파란선(20일선)을 깨고 다시 올라올 때 불타기"
        #
        # 10분봉 EMA55 위 → 홀딩 (세력 건재)
        # 10분봉 EMA55 아래 2봉 → 전량 손절
        # 10분봉 EMA20 깨고 → 다시 올라오면 → 불타기!
        # BB 상단 터치 → 50% 익절
        #
        # 적용: 모든 전략 (P5: AZTEC같은 매집→홈런 케이스 대응)
        if buy_type in ("active_watch", "wakeup", "normal", "whale_hunt", "surge", "accumulation"):
            try:
                _dc_held = get_cached_daily_change(ticker)
                if _dc_held >= 5.0:
                    # 🔧 캐시 사용 (CPU 최적화 - 60초 TTL)
                    ema_data = get_cached_ema_10m(ticker)
                    if ema_data:
                        ema20_val = ema_data["ema20"]
                        ema55_val = ema_data["ema55"]
                        bb_upper = ema_data["bb_upper"]
                        
                        if ema55_val > 0 and ema20_val > 0:
                            
                            # ━━━ EMA55 이탈 (2봉 연속) → 전량 손절 ━━━
                            # "55일선을 깨면 세력 철수"
                            if current < ema55_val:
                                prev_close = ema_data.get("prev_close", current)
                                prev_ema55 = ema_data.get("prev_ema55", ema55_val)
                                
                                if prev_close < prev_ema55:
                                    ema55_gap = (current - ema55_val) / ema55_val * 100
                                    logger.warning(
                                        f"🎯 [EMA55세력선] {ticker.replace('KRW-','')} | "
                                        f"10분봉 2봉 연속 EMA55 아래 ({ema55_gap:+.2f}%) "
                                        f"→ 세력 철수! 즉시 손절!"
                                    )
                                    self._queue_sell(
                                        ticker, portion=1.0,
                                        reason=f"EMA55 세력철수 10분봉"
                                    )
                                    return
                                else:
                                    logger.info(
                                        f"⚠️ [EMA55경고] {ticker.replace('KRW-','')} | "
                                        f"10분봉 1봉 EMA55 이탈 → 다음 봉 확인 대기"
                                    )
                            
                            # ━━━ EMA20 깨고 → 다시 올라오면 → 불타기! ━━━
                            # "파란선을 깨고 다시 올라올 때 불타기가 계속 되야 함"
                            ema20_broken = info.get("ema20_broken", False)
                            
                            if current < ema20_val:
                                # EMA20 아래 = 기록만 (팔지 않음!)
                                if not ema20_broken:
                                    self.bought_coins[ticker]["ema20_broken"] = True
                                    self._save_bot_bought()
                                    logger.info(
                                        f"📉 [EMA20이탈] {ticker.replace('KRW-','')} | "
                                        f"10분봉 EMA20 아래 → 불타기 대기 (복귀 시 추가매수)"
                                    )
                            elif ema20_broken and current > ema20_val:
                                # EMA20 복귀! → 불타기 (추가매수)
                                # 🌊 P5: 파동 위치 교차 검증 — 과열이면 불타기 금지
                                _w_hot, _w_info = detect_wave_overheated(ticker, block_pct=90)
                                if _w_hot:
                                    logger.info(
                                        f"🌊 [불타기차단] {ticker.replace('KRW-','')} | "
                                        f"EMA20 복귀했지만 파동과열 "
                                        f"{_w_info.get('position_pct',0):.0f}% → 불타기 안 함"
                                    )
                                    self.bought_coins[ticker]["ema20_broken"] = False
                                    self._save_bot_bought()
                                else:
                                    # 파동 OK → 기존 불타기 진행
                                    _fire_tag = ""
                                    _fire_mult = 0.5  # 기본 50%
                                    if _w_info.get("wave_phase") == "correction4" and _w_info.get("is_buy_zone"):
                                        _fire_tag = " 🌊4파지지"
                                        _fire_mult = 0.5  # 4파 지지 = 강한 불타기
                                    elif _w_info.get("wave_phase") == "correction2" and _w_info.get("is_buy_zone"):
                                        _fire_tag = " 🌊2파되돌림"
                                        _fire_mult = 0.3  # 2파 = 보수적
                                    
                                    self.bought_coins[ticker]["ema20_broken"] = False
                                    self._save_bot_bought()
                                    
                                    # 불타기 금액 (파동 위치에 따라 동적)
                                    existing_amount = info.get("amount_krw", 0)
                                    addon_amount = int(existing_amount * _fire_mult)
                                    
                                    if addon_amount >= CONFIG["min_trade_krw"]:
                                        available = get_balance_safe("KRW") or 0
                                        if available >= addon_amount:
                                            logger.warning(
                                                f"🔥 [불타기!] {ticker.replace('KRW-','')} | "
                                                f"EMA20 복귀{_fire_tag} | {addon_amount:,}원 ({_fire_mult*100:.0f}%) | "
                                                f"현재 {pnl_pct:+.2f}%"
                                            )
                                            try:
                                                result = safe_api_call(
                                                    pyupbit.Upbit.buy_market_order,
                                                    self.upbit, ticker, addon_amount
                                                )
                                                if result:
                                                    old_amount = info.get("amount_krw", 0)
                                                    old_price = info.get("buy_price", 0)
                                                    new_avg = ((old_price * old_amount) + (current * addon_amount)) / (old_amount + addon_amount)
                                                    self.bought_coins[ticker]["buy_price"] = new_avg
                                                    self.bought_coins[ticker]["amount_krw"] = old_amount + addon_amount
                                                    self.bought_coins[ticker]["highest"] = max(highest, current)
                                                    self._save_bot_bought()
                                                    logger.warning(
                                                        f"🔥 [불타기완료] {ticker.replace('KRW-','')} | "
                                                        f"평단 {old_price:,.1f}→{new_avg:,.1f} | "
                                                        f"+{addon_amount:,}원{_fire_tag}"
                                                    )
                                                    # P5: 불타기 텔레그램 알림
                                                    try:
                                                        send_telegram(
                                                            f"🔥 <b>불타기 매수!</b>\n"
                                                            f"코인: {ticker.replace('KRW-','')}\n"
                                                            f"유형: EMA20 복귀{_fire_tag}\n"
                                                            f"수익률: {pnl_pct:+.2f}%\n"
                                                            f"추가: {addon_amount:,}원 ({_fire_mult*100:.0f}%)\n"
                                                            f"평단: {old_price:,.1f}→{new_avg:,.1f}"
                                                        )
                                                    except Exception:
                                                        pass
                                            except Exception as e:
                                                logger.error(f"불타기 오류 {ticker}: {e}")
                            
                            # ━━━ BB 상단 터치 → 50% 익절 ━━━
                            bb_sold = info.get("bb_upper_sold", False)
                            if bb_upper > 0 and current >= bb_upper and not bb_sold:
                                logger.warning(
                                    f"📈 [BB상단익절] {ticker.replace('KRW-','')} | "
                                    f"10분봉 BB상단 도달 → 과열! 50% 익절!"
                                )
                                self.bought_coins[ticker]["bb_upper_sold"] = True
                                self._save_bot_bought()
                                self._queue_sell(
                                    ticker, portion=0.5,
                                    reason=f"BB상단 과열 익절"
                                )
                                return
            except Exception:
                pass

        # ── 🌊 5파 돌파 불타기 (전고점 돌파 탑승) ──
        # 미누님: "파동 지지/돌파 자리 = 불타기 자리"
        # 4파 조정 끝 → 3파 고점 돌파 = 5파 시작 = 마지막 상승파 탑승!
        if not info.get("wave5_fire_done", False) and pnl_pct > 0:
            try:
                _w5_hot, _w5_info = detect_wave_overheated(ticker, block_pct=90)
                if not _w5_hot and _w5_info.get("wave_phase") == "wave5":
                    _w3_high = _w5_info.get("wave3_high", 0)
                    if _w3_high > 0 and current > _w3_high:
                        # 거래량 2배+ 확인 (세력 재참여)
                        _ema_1m_w5 = get_cached_ema_1m(ticker)
                        if _ema_1m_w5 and _ema_1m_w5.get("df") is not None:
                            _df_w5 = _ema_1m_w5["df"]
                            _avg_vol_w5 = _df_w5["volume"].iloc[-6:-1].mean()
                            _vol_ratio_w5 = _df_w5.iloc[-1]["volume"] / _avg_vol_w5 if _avg_vol_w5 > 0 else 0
                            
                            if _vol_ratio_w5 >= 2.0:
                                _w5_amount = int(info.get("amount_krw", 0) * 0.5)
                                _w5_target = _w5_info.get("target", 0)
                                if _w5_amount >= CONFIG["min_trade_krw"]:
                                    _avail = get_balance_safe("KRW") or 0
                                    if _avail >= _w5_amount:
                                        try:
                                            _result = safe_api_call(
                                                pyupbit.Upbit.buy_market_order,
                                                self.upbit, ticker, _w5_amount
                                            )
                                            if _result:
                                                _old_amt = info.get("amount_krw", 0)
                                                _old_pr = info.get("buy_price", 0)
                                                _new_avg = ((_old_pr * _old_amt) + (current * _w5_amount)) / (_old_amt + _w5_amount)
                                                self.bought_coins[ticker]["buy_price"] = _new_avg
                                                self.bought_coins[ticker]["amount_krw"] = _old_amt + _w5_amount
                                                self.bought_coins[ticker]["wave5_fire_done"] = True
                                                self.bought_coins[ticker]["highest"] = max(highest, current)
                                                self._save_bot_bought()
                                                logger.warning(
                                                    f"🌊🔥 [5파돌파불타기] {ticker.replace('KRW-','')} | "
                                                    f"3파고점 {_w3_high:.0f} 돌파! | "
                                                    f"거래량 {_vol_ratio_w5:.1f}배 | "
                                                    f"+{_w5_amount:,}원 | "
                                                    f"목표 {_w5_target:.0f}원 | "
                                                    f"평단 {_old_pr:,.1f}→{_new_avg:,.1f}"
                                                )
                                        except Exception as e:
                                            logger.error(f"5파돌파불타기 오류 {ticker}: {e}")
            except Exception:
                pass

        # ── ⏰ 전략 공통 타임아웃 (미누님 요청) ──
        # 🏠 퇴근정리(19~20시): 2시간 / 그 외: 4시간
        if buy_type not in ("accumulation",):  # 매집은 자체 타임아웃 있음
            try:
                _bt_str = info.get("buy_time", "")
                if _bt_str:
                    _elapsed_h = (datetime.datetime.now() - datetime.datetime.fromisoformat(_bt_str)).total_seconds() / 3600
                    # 🏠 퇴근정리 모드: 2시간 타임아웃 (세력 퇴근 대응)
                    _tmode_to = get_time_mode()
                    _timeout_h = 2.0 if _tmode_to == "closing" else 4.0
                    if _elapsed_h >= _timeout_h:
                        if pnl_pct > 0:
                            # 수익 중 → 경고만 (10분봉 EMA 로직에 맡김)
                            pass
                        else:
                            # 타임아웃 + 손실 → 손절
                            logger.warning(
                                f"⏰ [타임아웃] {ticker.replace('KRW-','')} [{buy_type}] | "
                                f"{_elapsed_h:.1f}시간 보유 {pnl_pct:+.2f}% → {_timeout_h:.0f}시간 초과 손절!"
                            )
                            self._queue_sell(
                                ticker, portion=1.0,
                                reason=f"{_timeout_h:.0f}h 타임아웃 {_elapsed_h:.1f}h"
                            )
                            return
            except Exception:
                pass

        # ── 📉 10분봉 EMA100 이탈 2봉 → 손절 (미누님 요청) ──
        # "100 미만으로 떨어진 코인은 2봉 지나서 올라오지 못하면 손절"
        if buy_type in ("active_watch", "wakeup", "normal", "accumulation", "whale_hunt", "surge"):
            try:
                ema_data_100 = get_cached_ema_10m(ticker)
                if ema_data_100:
                    # EMA100은 캐시에 없으니 별도 계산 (캐시 df 재사용 불가 → 간단히)
                    ema100_below_count = info.get("ema100_below_count", 0)
                    
                    # 10분봉에서 EMA100 체크
                    df_100 = safe_api_call(pyupbit.get_ohlcv, ticker,
                                          interval="minute10", count=110)
                    if df_100 is not None and len(df_100) >= 101:
                        ema100_val = df_100["close"].ewm(span=100, adjust=False).mean().iloc[-1]
                        
                        if ema100_val > 0:
                            if current < ema100_val:
                                ema100_below_count += 1
                                self.bought_coins[ticker]["ema100_below_count"] = ema100_below_count
                                
                                if ema100_below_count >= 2:
                                    ema100_gap = (current - ema100_val) / ema100_val * 100
                                    logger.warning(
                                        f"📉 [EMA100이탈] {ticker.replace('KRW-','')} | "
                                        f"10분봉 EMA100 아래 {ema100_below_count}봉 "
                                        f"({ema100_gap:+.2f}%) → 복귀 실패! 손절!"
                                    )
                                    self._queue_sell(
                                        ticker, portion=1.0,
                                        reason=f"EMA100 이탈 {ema100_below_count}봉"
                                    )
                                    return
                                else:
                                    logger.info(
                                        f"⚠️ [EMA100경고] {ticker.replace('KRW-','')} | "
                                        f"10분봉 EMA100 아래 1봉 → 다음 봉 관찰"
                                    )
                            else:
                                # EMA100 위 복귀 → 카운트 리셋
                                if ema100_below_count > 0:
                                    self.bought_coins[ticker]["ema100_below_count"] = 0
            except Exception:
                pass

        # ── 🚨 1분봉 세력감지센서 (미누님 전략) ──
        # "탈출은 긴 장대음봉 + 거래량 많은 세력감지센서로 매도"
        # "세력감지센서는 1분봉" → 빠르게 감지해서 즉시 탈출
        # 적용: 모든 전략 (P5: 매집→홈런 코인도 세력 탈출 감지)
        if buy_type in ("active_watch", "wakeup", "normal", "whale_hunt", "surge", "accumulation"):
            try:
                _dc_sensor = get_cached_daily_change(ticker)
                if _dc_sensor >= 5.0:
                    df_1m_sensor = safe_api_call(pyupbit.get_ohlcv, ticker,
                                                interval="minute1", count=10)
                    if df_1m_sensor is not None and len(df_1m_sensor) >= 5:
                        s_curr = df_1m_sensor.iloc[-1]
                        s_body = abs(s_curr["close"] - s_curr["open"])
                        s_avg_body = df_1m_sensor.iloc[-5:-1].apply(
                            lambda r: abs(r["close"] - r["open"]), axis=1).mean()
                        s_vol = s_curr["volume"]
                        s_avg_vol = df_1m_sensor["volume"].iloc[-5:-1].mean()
                        s_is_bear = s_curr["close"] < s_curr["open"]
                        
                        # 장대음봉 (body 3배+) + 거래량 폭발 (3배+) = 세력 탈출!
                        if (s_is_bear and s_avg_body > 0 and s_body >= s_avg_body * 3.0
                            and s_avg_vol > 0 and s_vol >= s_avg_vol * 3.0):
                            vol_mult = s_vol / s_avg_vol
                            body_mult = s_body / s_avg_body
                            logger.warning(
                                f"🚨 [1분봉세력탈출] {ticker.replace('KRW-','')} | "
                                f"장대음봉 {body_mult:.1f}배 + 거래량 {vol_mult:.1f}배 "
                                f"→ 세력 털기! 즉시 전량 매도!"
                            )
                            self._queue_sell(
                                ticker, portion=1.0,
                                reason=f"1분봉 세력탈출 음봉{body_mult:.1f}x+거래량{vol_mult:.1f}x",
                                is_stoploss=True
                            )
                            return
            except Exception:
                pass

        # ── 🛡️ 본전 보장는 노말 전략 내부에서 처리 ──

        # ============================================================
        # 🚀 SURGE 코인 전략
        # ============================================================
        if buy_type == "surge":

            eth_status = get_eth_status()

            # ── 🔴 하락장 (BTC 전일대비↓) 추가 보호 조건 ──
            if eth_status == "bearish":

                # ① 3분 내 -1% → 즉시 손절 (초기 요동 방지)
                buy_time = info.get("buy_time", "")
                if buy_time:
                    try:
                        elapsed = (datetime.datetime.now() - datetime.datetime.fromisoformat(buy_time)).seconds
                        if elapsed <= 180 and pnl_pct <= -1.0:
                            self._queue_sell(ticker, portion=1.0,
                                      reason=f"[급등] 3분내 -1% 손절", is_stoploss=True)
                            return
                    except Exception:
                        pass

                # 50% 익절 후 +0.5% 이하 → 잔량 익절 (TREE 사례 방지)
                if breakeven_armed and pnl_pct <= CONFIG["breakeven_trigger_pct"]:
                    self._queue_sell(ticker, portion=1.0,
                              reason=f"[급등] 50%후 +0.5% 보장 익절")
                    return

            # ── 공통 손절/EMA100 체크 (BB 안이면 유지) ──
            try:
                # 매수 후 경과 시간 계산 (손절 유예용)
                _buy_time   = info.get("buy_time", "")
                _elapsed_s  = 0
                if _buy_time:
                    try:
                        _elapsed_s = (datetime.datetime.now() - datetime.datetime.fromisoformat(_buy_time)).seconds
                    except Exception:
                        pass

                df_sl = safe_api_call(
                    pyupbit.get_ohlcv, ticker,
                    interval=CONFIG["candle_interval"], count=110
                )
                if df_sl is not None and len(df_sl) >= 105:
                    close_sl  = df_sl["close"]
                    ema100_sl = close_sl.ewm(span=100, adjust=False).mean()
                    bb_mid_sl = close_sl.rolling(window=20).mean()
                    bb_std_sl = close_sl.rolling(window=20).std()
                    bb_low_sl = bb_mid_sl - 2 * bb_std_sl

                    inside_bb = current >= bb_low_sl.iloc[-1]  # BB 하단 위 = BB 안

                    # ① 손절: 가격대별 동적 기준 + 2분 유예 + BB 안이면 유지
                    surge_stop = get_dynamic_stop(buy_price, "surge")
                    if not breakeven_armed and pnl_pct <= surge_stop:
                        # 유예: 매수 후 2분 이내 & 손실이 손절선의 1.3배 미만일 때만
                        # (기존: 5분 × 1.5배 → 너무 느슨했음)
                        if _elapsed_s < 120 and pnl_pct > (surge_stop * 1.3):
                            logger.info(
                                f"⏳ [🚀surge] {ticker.replace('KRW-','')} | "
                                f"손절 {pnl_pct:+.2f}% 도달, 매수 후 {_elapsed_s//60}분 → 2분 유예 중"
                            )
                        elif inside_bb and pnl_pct > surge_stop * 1.5:
                            # BB 안이어도 손절선의 1.5배를 넘으면 무조건 매도 (안전망)
                            logger.info(
                                f"🛡️ [🚀surge] {ticker.replace('KRW-','')} | "
                                f"손절 {surge_stop}% 도달({pnl_pct:+.2f}%)이지만 BB 안 → 유지"
                            )
                        else:
                            reason_tag = "BB이탈" if not inside_bb else "BB안이지만 1.5배초과"
                            logger.warning(
                                f"💀 [🚀surge] {ticker.replace('KRW-','')} | "
                                f"손절 {surge_stop}%+{reason_tag} → 손절!"
                            )
                            self._queue_sell(ticker, portion=1.0,
                                      reason=f"[급등] 손절 {surge_stop}%+{reason_tag}", is_stoploss=True)
                            return

                    # ② EMA100 아래 5봉 연속 → BB 안이면 유지
                    # (accumulation→surge 전환 코인의 흔들기 구간 허용)
                    ema100_b1 = close_sl.iloc[-1] < ema100_sl.iloc[-1]
                    ema100_b2 = close_sl.iloc[-2] < ema100_sl.iloc[-2]
                    ema100_b3 = close_sl.iloc[-3] < ema100_sl.iloc[-3]
                    ema100_b4 = close_sl.iloc[-4] < ema100_sl.iloc[-4]
                    ema100_b5 = close_sl.iloc[-5] < ema100_sl.iloc[-5]
                    if ema100_b1 and ema100_b2 and ema100_b3 and ema100_b4 and ema100_b5:
                        if inside_bb:
                            logger.info(
                                f"🛡️ [🚀surge] {ticker.replace('KRW-','')} | "
                                f"EMA100 5봉 이탈이지만 BB 안 → 유지"
                            )
                        else:
                            logger.warning(
                                f"📉 [🚀surge] {ticker.replace('KRW-','')} | "
                                f"EMA100 5봉 + BB 이탈 → 매도!"
                            )
                            self._queue_sell(ticker, portion=1.0,
                                      reason=f"[급등] EMA100 5봉+BB이탈", is_stoploss=pnl_pct < 0)
                            return
                else:
                    # API 실패 시 기존 손절 -3% 적용
                    if not breakeven_armed and pnl_pct <= -3.0:
                        self._queue_sell(ticker, portion=1.0,
                                  reason=f"[급등] 손절", is_stoploss=True)
                        return
            except Exception:
                # 폴백: 기존 손절 -1.5%
                if not breakeven_armed and pnl_pct <= CONFIG["surge_stop_loss_pct"]:
                    self._queue_sell(ticker, portion=1.0,
                              reason=f"[급등] 손절", is_stoploss=True)
                    return

            # ── 🚀 surge 스윙 체크 (120분 후 BB 기준 / 이후 60분마다 반복) ──
            try:
                buy_time = info.get("buy_time", "")
                if buy_time:
                    elapsed_min = (datetime.datetime.now() - datetime.datetime.fromisoformat(buy_time)).seconds // 60
                    swing_mode  = self.bought_coins[ticker].get("swing_mode", False)
                    next_check  = self.bought_coins[ticker].get("swing_next_check", None)

                    df_sw = safe_api_call(pyupbit.get_ohlcv, ticker,
                                          interval=CONFIG["candle_interval"], count=30)
                    inside_bb_sw = True
                    if df_sw is not None and len(df_sw) >= 25:
                        close_sw  = df_sw["close"]
                        bb_mid_sw = close_sw.rolling(window=20).mean()
                        bb_std_sw = close_sw.rolling(window=20).std()
                        bb_low_sw = bb_mid_sw - 2 * bb_std_sw
                        inside_bb_sw = current >= bb_low_sw.iloc[-1]

                    if not swing_mode and elapsed_min >= 120:
                        if not inside_bb_sw:
                            logger.info(f"⏱️ [🚀스윙체크] {ticker.replace('KRW-','')} | 120분 BB 이탈 {pnl_pct:+.2f}% → 매도")
                            self._queue_sell(ticker, portion=1.0,
                                      reason=f"[급등] 스윙 120분 BB이탈", is_stoploss=True)
                            return
                        else:
                            next_dt = (datetime.datetime.now() + datetime.timedelta(minutes=60)).isoformat()
                            self.bought_coins[ticker]["swing_mode"]       = True
                            self.bought_coins[ticker]["swing_next_check"] = next_dt
                            logger.info(f"🔄 [🚀스윙모드] {ticker.replace('KRW-','')} | 120분 BB 안 {pnl_pct:+.2f}% → 스윙 유지! (60분마다 체크)")

                    elif swing_mode and next_check:
                        if datetime.datetime.now() >= datetime.datetime.fromisoformat(next_check):
                            if not inside_bb_sw:
                                logger.info(f"⏱️ [🚀스윙체크] {ticker.replace('KRW-','')} | BB 이탈 {pnl_pct:+.2f}% → 매도")
                                self._queue_sell(ticker, portion=1.0,
                                          reason=f"[급등] 스윙 BB이탈", is_stoploss=True)
                                return
                            else:
                                next_dt = (datetime.datetime.now() + datetime.timedelta(minutes=60)).isoformat()
                                self.bought_coins[ticker]["swing_next_check"] = next_dt
                                logger.info(f"🔄 [🚀스윙유지] {ticker.replace('KRW-','')} | BB 안 {pnl_pct:+.2f}% → 60분 후 재체크")
            except Exception:
                pass

            # ── 🎯 수익률별 동적 트레일링 (옵션 B: 2차 상승 대기 모드) ──
            # 🔥 TP2 (+15%) 달성 전: 기존 타이트 트레일링 유지
            # 🔥 TP2 달성 후: 트레일링 완화 (세력털기 감지기에 맡김)
            #    → 미누님 시나리오: 2차 돌파 불타기 기회 잡기 위해
            #
            # TP2 이전 (+0~+15%):
            #   +5% 미만: -2.0% (여유)
            #   +5~10%:   -1.8%
            #   +10~15%:  -1.5%
            # TP2 이후 (+15%↑): 트레일링 대폭 완화
            #   -3.0% 여유 (2차 상승 기다리기)
            #   잔량은 세력털기 점수제가 지켜줌
            tp2_done = info.get("surge_tp2_done", False)
            
            if tp2_done:
                # TP2 이후: 여유 모드 (-3%)
                dynamic_trail_drop = -3.0
            elif pnl_pct >= 10:
                dynamic_trail_drop = -1.5
            elif pnl_pct >= 5:
                dynamic_trail_drop = -1.8
            else:
                dynamic_trail_drop = CONFIG["surge_trailing_pct"]  # -2.0

            # ── 트레일링: 최고점 대비 동적 % → 50% 익절 ──
            if highest > buy_price:
                drop = ((current - highest) / highest) * 100
                trail_half_done = info.get("surge_trail_half_done", False)

                if not trail_half_done and drop <= dynamic_trail_drop:
                    mode_tag = "(TP2후 여유모드)" if tp2_done else ""
                    logger.info(
                        f"📉 [급등] 트레일링 1차: {ticker} | "
                        f"고점 {drop:.2f}% (기준 {dynamic_trail_drop}% @ +{pnl_pct:.1f}%) {mode_tag} → 50% 익절!"
                    )
                    self._queue_sell(ticker, portion=0.5,
                              reason=f"[급등] 트레일링 50% ({drop:.2f}%) {mode_tag}")
                    if ticker in self.bought_coins:
                        self.bought_coins[ticker]["surge_trail_half_done"] = True
                        self.bought_coins[ticker]["highest"] = current
                        self.bought_coins[ticker]["breakeven_armed"] = True
                        breakeven_armed = True
                    return

            # ── 🔥🔥 돌파 불타기: 전고점 돌파 + 거래량 폭발 (공격 모드) ──
            # BLUR 12:00 같은 케이스: 1시간 이상 횡보 후 전고점 돌파 순간
            # ⚾ 공격 모드: 민감도 상향 + 금액 50만원
            # 🔥 옵션 B: 최대 3회 허용 + 30분 쿨다운 (1차/2차/3차 돌파 모두 잡기)
            breakout_count = info.get("breakout_add_count", 0)
            last_breakout_time = info.get("last_breakout_time", "")
            
            # 쿨다운 체크 (30분)
            cooldown_ok = True
            if last_breakout_time:
                try:
                    elapsed = (datetime.datetime.now() - 
                              datetime.datetime.fromisoformat(last_breakout_time)).total_seconds()
                    cooldown_ok = elapsed >= 1800  # 30분
                except Exception:
                    pass
            
            if breakout_count < 3 and cooldown_ok:
                try:
                    df_bo = safe_api_call(
                        pyupbit.get_ohlcv, ticker,
                        interval=CONFIG["candle_interval"], count=70
                    )
                    if df_bo is not None and len(df_bo) >= 65:
                        c_bo = df_bo["close"]
                        h_bo = df_bo["high"]
                        o_bo = df_bo["open"]
                        v_bo = df_bo["volume"]
                        
                        # 현재봉 제외 최근 60봉(약 1시간) 최고가
                        prev_high_60 = h_bo.iloc[-61:-1].max()
                        
                        curr_close = c_bo.iloc[-1]
                        curr_open  = o_bo.iloc[-1]
                        curr_vol   = v_bo.iloc[-1]
                        avg_vol    = v_bo.iloc[-21:-1].mean()
                        
                        # 돌파 조건 (공격 모드 완화)
                        breakout    = curr_close > prev_high_60
                        vol_surge   = avg_vol > 0 and curr_vol >= avg_vol * 2.0
                        is_bullish  = curr_close > curr_open
                        clean_break = curr_close >= prev_high_60 * 1.001
                        
                        if breakout and vol_surge and is_bullish and clean_break:
                            avail = self.get_balance("KRW")
                            # 1차: 50만 / 2차: 40만 / 3차: 30만 (점점 줄임)
                            amounts = [500_000, 400_000, 300_000]
                            base_amt = amounts[breakout_count]
                            add_amt = min(
                                avail * 0.95,
                                int(base_amt * (0.1 if CONFIG.get("test_mode") else 1.0))
                            )
                            if add_amt >= CONFIG["min_trade_krw"]:
                                break_pct = (curr_close - prev_high_60) / prev_high_60 * 100
                                order_num = breakout_count + 1
                                logger.warning(
                                    f"🔥🔥🔥 [돌파불타기{order_num}차⚾] {ticker.replace('KRW-','')} | "
                                    f"1시간 고점 {prev_high_60:.4f} → {curr_close:.4f} (+{break_pct:.2f}%) | "
                                    f"거래량 {curr_vol/avg_vol:.1f}배 | 수익률 {pnl_pct:+.2f}% → {add_amt:,.0f}원 추가!"
                                )
                                self.buy(ticker, add_amt, buy_type="surge")
                                if ticker in self.bought_coins:
                                    self.bought_coins[ticker]["breakout_add_count"] = breakout_count + 1
                                    self.bought_coins[ticker]["last_breakout_time"] = datetime.datetime.now().isoformat()
                                    self._save_bot_bought()
                                send_telegram(
                                    f"🔥🔥🔥 <b>돌파 불타기 {order_num}차!</b>\n"
                                    f"코인: {ticker.replace('KRW-','')}\n"
                                    f"1시간 고점: {prev_high_60:.4f}\n"
                                    f"현재가: {curr_close:.4f} (+{break_pct:.2f}%)\n"
                                    f"거래량: {curr_vol/avg_vol:.1f}배\n"
                                    f"수익률: {pnl_pct:+.2f}%\n"
                                    f"→ {add_amt:,.0f}원 추가! ⚾🔥"
                                )
                                return
                except Exception as _e:
                    logger.debug(f"돌파 불타기 체크 오류 {ticker}: {_e}")

            # ── 🔥 불타기 1차: +5% + 추세 양호 → 50만 (공격 모드) ──
            add_buy_1_done = info.get("surge_add_buy_done", False)
            if not add_buy_1_done and pnl_pct >= 5.0 and get_eth_status() == "bullish":
                try:
                    df_bb = safe_api_call(pyupbit.get_ohlcv, ticker, interval=CONFIG["candle_interval"], count=30)
                    if df_bb is not None and len(df_bb) >= 25:
                        close_b  = df_bb["close"]
                        ema5_b   = close_b.ewm(span=5,  adjust=False).mean()
                        ema20_b  = close_b.ewm(span=20, adjust=False).mean()
                        bb_mid_b = close_b.rolling(window=20).mean()
                        bb_std_b = close_b.rolling(window=20).std()
                        bb_up_b  = bb_mid_b + 2 * bb_std_b
                        vol_b    = df_bb["volume"]

                        # 조건 A: EMA5 > EMA20 (단기 상승)
                        # 조건 B: 현재가 > EMA5 (추세 위)
                        # 조건 C: BB 상단 근처 또는 위 (강한 상승)
                        # 조건 D: 직전봉도 양봉 (조정 아님)
                        ema_rising  = ema5_b.iloc[-1] > ema20_b.iloc[-1]
                        above_ema5  = close_b.iloc[-1] > ema5_b.iloc[-1]
                        near_bb_up  = close_b.iloc[-1] >= bb_up_b.iloc[-1] * 0.95
                        prev_bullish = close_b.iloc[-2] > df_bb["open"].iloc[-2]
                        
                        # 🔧 미누님 요청: 거래량 + 체결강도 체크 추가
                        # 조건 E: 거래량 증가 중 (평균 × 1.3 이상)
                        avg_vol_b    = vol_b.iloc[-21:-1].mean()
                        curr_vol_b   = vol_b.iloc[-1]
                        vol_increase = avg_vol_b > 0 and curr_vol_b >= avg_vol_b * 1.3
                        
                        # 조건 F: 체결강도 (매수세 ≥ 55%)
                        taker_ratio = get_taker_buy_ratio(ticker, count=100)
                        # None이면 API 실패 → 안전하게 False (불타기 안 함)
                        strong_buying = taker_ratio is not None and taker_ratio >= 55.0

                        # 추세 + 거래량 + 체결강도 모두 확인되어야 불타기
                        trend_ok   = ema_rising and above_ema5 and (near_bb_up or prev_bullish)
                        momentum_ok = vol_increase and strong_buying

                        if trend_ok and momentum_ok:
                            avail = self.get_balance("KRW")
                            # P5: 본매수 금액 × 1.0 (최소 min_trade_krw 보장)
                            existing = info.get("amount_krw", 0)
                            add_amount = min(avail * 0.95, max(CONFIG["min_trade_krw"], int(existing * 1.0)))
                            if add_amount >= CONFIG["min_trade_krw"]:
                                logger.info(
                                    f"🔥 [불타기1차] {ticker.replace('KRW-','')} | "
                                    f"+{pnl_pct:.1f}% | 거래량 {curr_vol_b/avg_vol_b:.1f}배 | "
                                    f"체결강도 {taker_ratio:.0f} | {add_amount:,.0f}원"
                                )
                                self.buy(ticker, add_amount, buy_type="surge")
                                if ticker in self.bought_coins:
                                    self.bought_coins[ticker]["surge_add_buy_done"] = True
                                    self._save_bot_bought()
                                send_telegram(
                                    f"🔥 <b>불타기 1차 매수!</b>\n"
                                    f"코인: {ticker.replace('KRW-','')}\n"
                                    f"수익률: {pnl_pct:+.1f}%\n"
                                    f"거래량: {curr_vol_b/avg_vol_b:.1f}배\n"
                                    f"체결강도: {taker_ratio:.0f} (매수세)\n"
                                    f"추가: {add_amount:,.0f}원"
                                )
                                return
                        else:
                            # 조건 불충족 사유 디버그 로깅
                            fail = []
                            if not trend_ok:       fail.append("추세")
                            if not vol_increase:   fail.append(f"거래량{curr_vol_b/avg_vol_b:.1f}배")
                            if not strong_buying:  
                                tr_str = f"{taker_ratio:.0f}" if taker_ratio else "N/A"
                                fail.append(f"체결강도{tr_str}")
                            logger.debug(
                                f"🔥 [불타기1차보류] {ticker.replace('KRW-','')} "
                                f"+{pnl_pct:.1f}% | 실패: {','.join(fail)}"
                            )
                except Exception:
                    pass

            # ── 불타기 후 익절 조건 (1차 후) ──
            add_buy_done = info.get("surge_add_buy_done", False)
            if add_buy_done:
                try:
                    df_ex = safe_api_call(
                        pyupbit.get_ohlcv, ticker,
                        interval=CONFIG["candle_interval"], count=30
                    )
                    if df_ex is not None and len(df_ex) >= 25:
                        close_x  = df_ex["close"]
                        open_x   = df_ex["open"]
                        ema5_x   = close_x.ewm(span=5,  adjust=False).mean()
                        ema20_x  = close_x.ewm(span=20, adjust=False).mean()
                        bb_mid_x = close_x.rolling(window=20).mean()
                        bb_std_x = close_x.rolling(window=20).std()
                        bb_up_x  = bb_mid_x + 2 * bb_std_x

                        curr_close_x = close_x.iloc[-1]
                        prev_close_x = close_x.iloc[-2]
                        curr_open_x  = open_x.iloc[-1]
                        prev_open_x  = open_x.iloc[-2]
                        curr_ema5_x  = ema5_x.iloc[-1]
                        prev_ema5_x  = ema5_x.iloc[-2]
                        curr_ema20_x = ema20_x.iloc[-1]
                        prev_ema20_x = ema20_x.iloc[-2]
                        curr_bb_up_x = bb_up_x.iloc[-1]

                        # BB 상단 못 뚫고 안으로 들어옴
                        bb_fail      = curr_close_x < curr_bb_up_x
                        # 직전 양봉 크기
                        prev_bull_sz = abs(prev_close_x - prev_open_x) if prev_close_x > prev_open_x else 0
                        # 현재 음봉이 직전 양봉 잡아먹는지
                        engulf       = (prev_bull_sz > 0 and
                                       curr_open_x >= prev_close_x and
                                       curr_close_x <= prev_open_x and
                                       curr_close_x < curr_open_x)  # 음봉 조건 포함
                        # EMA5 이탈
                        ema5_break_x  = prev_close_x >= prev_ema5_x  and curr_close_x < curr_ema5_x
                        # EMA20 이탈 (불타기 후 핵심!)
                        ema20_break_x = prev_close_x >= prev_ema20_x and curr_close_x < curr_ema20_x

                        # 조건: EMA20 이탈 → 즉시 전량
                        if ema20_break_x:
                            logger.warning(
                                f"🔥 [불타기익절] {ticker.replace('KRW-','')} | "
                                f"{pnl_pct:+.2f}% → 전량 익절! (EMA20 이탈)"
                            )
                            self._queue_sell(ticker, portion=1.0,
                                      reason=f"[불타기] EMA20 이탈")
                            return

                        # 조건: BB실패 + (음봉잡아먹기 OR EMA5 이탈)
                        exit_signal  = bb_fail and (engulf or ema5_break_x)
                        if exit_signal:
                            reason_str = "BB실패+잡아먹기" if engulf else "BB실패+EMA5이탈"
                            logger.warning(
                                f"🔥 [불타기익절] {ticker.replace('KRW-','')} | "
                                f"{pnl_pct:+.2f}% → 전량 익절! ({reason_str})"
                            )
                            self._queue_sell(ticker, portion=1.0,
                                      reason=f"[불타기] {reason_str}")
                            return
                except Exception:
                    pass
            # ── EMA5/20 데드크로스 / TP 구간 ──
            # TP1 +7% → 50% 익절
            if pnl_pct >= 7.0 and not info.get("surge_tp1_done", False):
                self._queue_sell(ticker, portion=0.5, reason=f"[급등] TP1 +7%")
                if ticker in self.bought_coins:
                    self.bought_coins[ticker]["surge_tp1_done"] = True
                    self._save_bot_bought()
                    logger.info(f"✅ [🚀급등] {ticker.replace('KRW-','')} TP1 +7% 50% 익절")
                return

            # ── 🔥 불타기 2차: +10% + TP1 완료 + 추세 양호 → 30만 (공격 모드) ──
            if (info.get("surge_tp1_done", False) and
                    not info.get("surge_add_buy_2_done", False) and
                    pnl_pct >= 10.0 and get_eth_status() == "bullish"):
                try:
                    df_bb2 = safe_api_call(pyupbit.get_ohlcv, ticker, interval=CONFIG["candle_interval"], count=30)
                    if df_bb2 is not None and len(df_bb2) >= 25:
                        c2 = df_bb2["close"]
                        o2 = df_bb2["open"]
                        v2 = df_bb2["volume"]
                        e5_2  = c2.ewm(span=5,  adjust=False).mean()
                        e20_2 = c2.ewm(span=20, adjust=False).mean()
                        bm2 = c2.rolling(20).mean(); bs2 = c2.rolling(20).std()
                        bu2 = bm2 + 2*bs2

                        # 추세 조건
                        ema_rising2  = e5_2.iloc[-1] > e20_2.iloc[-1]
                        above_ema5_2 = c2.iloc[-1] > e5_2.iloc[-1]
                        near_bb_up2  = c2.iloc[-1] >= bu2.iloc[-1] * 0.95
                        prev_bull2   = c2.iloc[-2] > o2.iloc[-2]
                        
                        # 🔧 거래량 + 체결강도 체크 (미누님 요청)
                        avg_vol_2    = v2.iloc[-21:-1].mean()
                        curr_vol_2   = v2.iloc[-1]
                        vol_inc2     = avg_vol_2 > 0 and curr_vol_2 >= avg_vol_2 * 1.3
                        taker_ratio2 = get_taker_buy_ratio(ticker, count=100)
                        strong_buy2  = taker_ratio2 is not None and taker_ratio2 >= 55.0

                        trend_ok2    = ema_rising2 and above_ema5_2 and (near_bb_up2 or prev_bull2)
                        momentum_ok2 = vol_inc2 and strong_buy2

                        if trend_ok2 and momentum_ok2:
                            avail = self.get_balance("KRW")
                            # P5: 본매수 × 0.6 (최소 min_trade_krw 보장)
                            existing2 = info.get("amount_krw", 0)
                            add_amt2 = min(avail * 0.95, max(CONFIG["min_trade_krw"], int(existing2 * 0.6)))
                            if add_amt2 >= CONFIG["min_trade_krw"]:
                                logger.info(
                                    f"🔥 [불타기2차] {ticker.replace('KRW-','')} | "
                                    f"+{pnl_pct:.1f}% | 거래량 {curr_vol_2/avg_vol_2:.1f}배 | "
                                    f"체결강도 {taker_ratio2:.0f} | {add_amt2:,.0f}원"
                                )
                                self.buy(ticker, add_amt2, buy_type="surge")
                                if ticker in self.bought_coins:
                                    self.bought_coins[ticker]["surge_add_buy_2_done"] = True
                                    self._save_bot_bought()
                                send_telegram(
                                    f"🔥 <b>불타기 2차 매수!</b>\n"
                                    f"코인: {ticker.replace('KRW-','')}\n"
                                    f"수익률: {pnl_pct:+.1f}%\n"
                                    f"거래량: {curr_vol_2/avg_vol_2:.1f}배\n"
                                    f"체결강도: {taker_ratio2:.0f}\n"
                                    f"추가: {add_amt2:,.0f}원"
                                )
                                return
                except Exception:
                    pass

            # TP2 +15% → 50% 익절
            if pnl_pct >= 15.0 and not info.get("surge_tp2_done", False):
                self._queue_sell(ticker, portion=0.5, reason=f"[급등] TP2 +15%")
                if ticker in self.bought_coins:
                    self.bought_coins[ticker]["surge_tp2_done"] = True
                    self._save_bot_bought()
                    logger.info(f"✅ [🚀급등] {ticker.replace('KRW-','')} TP2 +15% 50% 익절")
                return

            # ── 🔥 불타기 3차: +20% + TP2 완료 + 추세 양호 → 20만 (공격 모드) ──
            if (info.get("surge_tp2_done", False) and
                    not info.get("surge_add_buy_3_done", False) and
                    pnl_pct >= 20.0 and get_eth_status() == "bullish"):
                try:
                    df_bb3 = safe_api_call(pyupbit.get_ohlcv, ticker, interval=CONFIG["candle_interval"], count=30)
                    if df_bb3 is not None and len(df_bb3) >= 25:
                        c3 = df_bb3["close"]
                        o3 = df_bb3["open"]
                        v3 = df_bb3["volume"]
                        e5_3  = c3.ewm(span=5,  adjust=False).mean()
                        e20_3 = c3.ewm(span=20, adjust=False).mean()
                        bm3 = c3.rolling(20).mean(); bs3 = c3.rolling(20).std()
                        bu3 = bm3 + 2*bs3

                        ema_rising3  = e5_3.iloc[-1] > e20_3.iloc[-1]
                        above_ema5_3 = c3.iloc[-1] > e5_3.iloc[-1]
                        near_bb_up3  = c3.iloc[-1] >= bu3.iloc[-1] * 0.95
                        prev_bull3   = c3.iloc[-2] > o3.iloc[-2]
                        
                        # 🔧 거래량 + 체결강도 체크 (미누님 요청)
                        avg_vol_3    = v3.iloc[-21:-1].mean()
                        curr_vol_3   = v3.iloc[-1]
                        vol_inc3     = avg_vol_3 > 0 and curr_vol_3 >= avg_vol_3 * 1.3
                        taker_ratio3 = get_taker_buy_ratio(ticker, count=100)
                        strong_buy3  = taker_ratio3 is not None and taker_ratio3 >= 55.0

                        trend_ok3    = ema_rising3 and above_ema5_3 and (near_bb_up3 or prev_bull3)
                        momentum_ok3 = vol_inc3 and strong_buy3

                        if trend_ok3 and momentum_ok3:
                            avail = self.get_balance("KRW")
                            # P5: 본매수 × 0.4 (최소 min_trade_krw 보장)
                            existing3 = info.get("amount_krw", 0)
                            add_amt3 = min(avail * 0.95, max(CONFIG["min_trade_krw"], int(existing3 * 0.4)))
                            if add_amt3 >= CONFIG["min_trade_krw"]:
                                logger.info(
                                    f"🔥 [불타기3차] {ticker.replace('KRW-','')} | "
                                    f"+{pnl_pct:.1f}% | 거래량 {curr_vol_3/avg_vol_3:.1f}배 | "
                                    f"체결강도 {taker_ratio3:.0f} | {add_amt3:,.0f}원"
                                )
                                self.buy(ticker, add_amt3, buy_type="surge")
                                if ticker in self.bought_coins:
                                    self.bought_coins[ticker]["surge_add_buy_3_done"] = True
                                    self._save_bot_bought()
                                send_telegram(
                                    f"🔥 <b>불타기 3차 매수!</b>\n"
                                    f"코인: {ticker.replace('KRW-','')}\n"
                                    f"수익률: {pnl_pct:+.1f}%\n"
                                    f"거래량: {curr_vol_3/avg_vol_3:.1f}배\n"
                                    f"체결강도: {taker_ratio3:.0f}\n"
                                    f"추가: {add_amt3:,.0f}원"
                                )
                                return
                except Exception:
                    pass

            # 하드TP → 옵션 B: 25% → 40%로 대폭 상향 (2차 상승 대기)
            # BLUR 같은 +25~30% 홈런을 +40%까지 끌고 가기 위함
            # 잔량 관리는 세력털기 점수제가 담당
            if pnl_pct >= 40.0:
                logger.info(f"🎯 [급등] +40% 달성: {ticker} | {pnl_pct:+.2f}% → 전량 익절!")
                self._queue_sell(ticker, portion=1.0, reason=f"[급등] +40% 달성")
                return

            # EMA5/20 데드크로스 → 전량 익절 (2봉 연속 데드 + CCI < 0)
            try:
                df_ema = safe_api_call(
                    pyupbit.get_ohlcv, ticker,
                    interval=CONFIG["candle_interval"], count=30
                )
                if df_ema is not None and len(df_ema) >= 25:
                    close_s    = df_ema["close"]
                    high_s     = df_ema["high"]
                    low_s      = df_ema["low"]
                    ema5_s     = close_s.ewm(span=5,  adjust=False).mean()
                    ema20_s    = close_s.ewm(span=20, adjust=False).mean()

                    # 3봉 이상 데드 유지 체크
                    curr_dead  = ema5_s.iloc[-1] < ema20_s.iloc[-1]
                    prev_dead  = ema5_s.iloc[-2] < ema20_s.iloc[-2]
                    prev2_dead = ema5_s.iloc[-3] < ema20_s.iloc[-3]
                    dead_2bars = curr_dead and prev_dead and prev2_dead  # 3봉 연속 데드

                    # CCI < 0 체크
                    tp_s      = (high_s + low_s + close_s) / 3
                    tp_mean_s = tp_s.rolling(window=20).mean()
                    tp_std_s  = tp_s.rolling(window=20).std()
                    cci_s     = (tp_s - tp_mean_s) / (0.015 * tp_std_s)
                    cci_neg   = cci_s.iloc[-1] < 0

                    if dead_2bars and cci_neg and pnl_pct > 0:  # 수익권일 때만 데드크로스 익절
                        logger.warning(
                            f"💀 [급등] EMA5/20 데드(3봉)+CCI음수: {ticker} | "
                            f"EMA5: {ema5_s.iloc[-1]:.4f} | CCI: {cci_s.iloc[-1]:.1f} | "
                            f"{pnl_pct:+.2f}%"
                        )
                        self._queue_sell(ticker, portion=1.0,
                                  reason=f"[급등] EMA5/20 데드크로스")
                        return

                    # ── surge 힘 빠지면 매집 복귀 (P5 완화) ──
                    # 미누님: "힘 없으면 바로 매집으로 복귀"
                    # 기존: BB수축 AND 거래량50% AND 수익<3% AND 6h내 → 4개 AND (1건만 발동)
                    # 개선: 약한 신호 1개 + 시간 8h → 빠르게 복귀
                    acc_origin = self.bought_coins[ticker].get("acc_origin_buy_time", "")
                    if acc_origin:  # 매집 출신 surge만
                        try:
                            bb_w_s = ((close_s.rolling(20).mean() + 2*close_s.rolling(20).std()) -
                                      (close_s.rolling(20).mean() - 2*close_s.rolling(20).std()))
                            bb_avg_s = bb_w_s.iloc[-10:].mean()
                            bb_shrink = bb_w_s.iloc[-1] < bb_avg_s * 0.95  # 95% (90%→95% 완화)
                            
                            vol_s = df_ema["volume"]
                            vol_low = vol_s.iloc[-1] < vol_s.iloc[-6:-1].mean() * 0.7  # 70% (50%→70% 완화)
                            
                            # 🆕 EMA5 하향 체크 (추가 신호)
                            ema5_falling = ema5_s.iloc[-1] < ema5_s.iloc[-3]  # 직전 3봉 대비 하락
                            
                            # 🆕 5봉 중 음봉 3개+ (힘 빠지는 신호)
                            recent_5 = df_ema.iloc[-5:]
                            bear_count = sum(1 for i in range(5) if recent_5["close"].iloc[i] < recent_5["open"].iloc[i])
                            many_bears = bear_count >= 3

                            total_elapsed = (datetime.datetime.now() -
                                           datetime.datetime.fromisoformat(acc_origin)).total_seconds() / 3600

                            # P5: OR 조건 (1개만 만족해도) + 시간 6h→8h
                            weak_signal = bb_shrink or vol_low or ema5_falling or many_bears
                            
                            if weak_signal and pnl_pct < 3.0 and total_elapsed < 8.0:
                                self.bought_coins[ticker]["buy_type"] = "accumulation"
                                self.bought_coins[ticker]["buy_time"] = acc_origin  # 원래 시간 복원!
                                self._reset_surge_flags(ticker)
                                self._save_bot_bought()
                                orig = self.bought_coins[ticker].get("original_type", "surge")
                                
                                # 어떤 신호로 복귀했는지
                                _signals = []
                                if bb_shrink: _signals.append("BB수축")
                                if vol_low: _signals.append("거래량↓")
                                if ema5_falling: _signals.append("EMA5↓")
                                if many_bears: _signals.append(f"음봉{bear_count}/5")
                                
                                logger.warning(
                                    f"🏦 [surge→매집복귀] {ticker.replace('KRW-','')} | "
                                    f"힘빠짐: {','.join(_signals)} | 경과 {total_elapsed:.1f}h | "
                                    f"{pnl_pct:+.2f}% [출신:{orig}]"
                                )
                                return
                        except Exception:
                            pass
            except Exception:
                pass

        # ============================================================
        # 🏦 세력 매집 코인 전략
        # ============================================================
        if buy_type in ("accumulation", "whale_hunt"):
            eth_st = get_eth_status()
            
            # ── ⚪ 횡보장(neutral) 순환 매매 모드 ──
            # 미누님 전략: 횡보장은 변동성 작음 → 작게 먹고 빨리 빠지기
            #   +1% → 바닥 +1% 잠금
            #   +2% → 즉시 전량 매도
            #   +1% 이탈 → 즉시 매도 (순환)
            _mkt_state, _ = get_market_state()
            if _mkt_state == "neutral":
                # 마이너스 경험 기록
                if pnl_pct < 0 and not info.get("was_underwater", False):
                    self.bought_coins[ticker]["was_underwater"] = True
                    self._save_bot_bought()
                
                # 마이너스 찍었다가 플러스 복귀 → 즉시 매도 (본전탈출)
                if info.get("was_underwater", False) and pnl_pct > 0:
                    logger.warning(
                        f"⚪ [횡보장탈출] {ticker.replace('KRW-','')} "
                        f"마이너스→플러스 복귀 → 즉시 매도!"
                    )
                    self._queue_sell(
                        ticker, portion=1.0,
                        reason=f"[횡보장] 본전탈출"
                    )
                    return
                
                # +2% 도달 → 즉시 전량 매도
                if pnl_pct >= 2.0:
                    logger.warning(
                        f"⚪ [횡보장순환] {ticker.replace('KRW-','')} "
                        f"+2% 달성 → 즉시 전량 매도!"
                    )
                    self._queue_sell(
                        ticker, portion=1.0,
                        reason=f"[횡보장] +2% 순환 매도"
                    )
                    return
                
                # +1% 도달 시 바닥 +1% 잠금 활성화
                neut_armed = info.get("neutral_armed", False)
                if not neut_armed and pnl_pct >= 1.0:
                    self.bought_coins[ticker]["neutral_armed"] = True
                    self._save_bot_bought()
                    logger.info(
                        f"⚪ [횡보장순환] {ticker.replace('KRW-','')} "
                        f"+1% 달성 → 바닥 +1% 잠금!"
                    )
                
                # 바닥 +1% 이탈 → 즉시 매도
                if neut_armed and pnl_pct <= 1.0:
                    logger.warning(
                        f"⚪ [횡보장순환] {ticker.replace('KRW-','')} "
                        f"바닥선 +1% 이탈 → 즉시 익절!"
                    )
                    self._queue_sell(
                        ticker, portion=1.0,
                        reason=f"[횡보장] +1% 바닥 이탈"
                    )
                    return
            
            # ── 🛡️ 본전 잠금 (찍먹 방지) ──
            # 미누님 요청: 이익→손해 전환 방지
            # 데이터 근거: 어제 성공 22건 분석
            #   +1% 한 번 찍으면 → +0.3% 바닥 잠금 (미누님 분석 반영)
            #   이후 +0.3% 이하로 떨어지면 즉시 전량 매도
            #   (수수료 0.1% 차감 후 실수익 +0.2% 보장)
            
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            # 🌙 P5: 새벽 짠돌이 모드 (00:00~08:30)
            # 미누님 철학: "새벽은 빠르게 1~2% 먹고 빠지기"
            # 데이터 근거: 4/10 새벽 41건 -4,015원 (반나절씩 보유 후 손실)
            # ⚠️ 예외: 이미 TP1(+2%) 달성한 잔량은 건들지 않음 
            #          → 기존 트레일링으로 홈런 추적 (AZTEC 케이스)
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            try:
                _now_h = datetime.datetime.now().hour
                _now_m = datetime.datetime.now().minute
                _is_dawn = _now_h < 8 or (_now_h == 8 and _now_m < 30)
                _already_tp1 = info.get("acc_tp1_done", False)  # 이미 낮에 TP1 달성?
                
                # 🌟 P5: 홈런 면제 체크 (FF 케이스)
                # 전일대비 +10% 이상 강력 상승 = 홈런 가능성 → 새벽짠돌이 건너뜀
                _homerun_bypass_dawn = False
                try:
                    _dc_dawn = get_cached_daily_change(ticker)
                    if _dc_dawn >= 10.0:
                        _homerun_bypass_dawn = True
                except Exception:
                    pass
                
                if _is_dawn and not _already_tp1 and not _homerun_bypass_dawn:
                    acc_dawn_tp1 = info.get("acc_dawn_tp1", False)
                    
                    # 🌙 D: +1.5% 하드 익절 (욕심 부리지 마)
                    if pnl_pct >= 1.5:
                        logger.warning(
                            f"🌙 [새벽짠돌이] {ticker.replace('KRW-','')} "
                            f"+1.5% 도달 ({pnl_pct:+.2f}%) → 전량 하드익절!"
                        )
                        self._queue_sell(
                            ticker, portion=1.0,
                            reason=f"[새벽짠돌이] +1.5% 하드익절"
                        )
                        return
                    
                    # 🌙 A: +1% 도달 시 즉시 50% 익절
                    if not acc_dawn_tp1 and pnl_pct >= 1.0:
                        logger.warning(
                            f"🌙 [새벽짠돌이] {ticker.replace('KRW-','')} "
                            f"+1% 도달 ({pnl_pct:+.2f}%) → 50% 즉시 익절!"
                        )
                        self._queue_sell(
                            ticker, portion=0.5,
                            reason=f"[새벽짠돌이] +1% 50%익절"
                        )
                        if ticker in self.bought_coins:
                            self.bought_coins[ticker]["acc_dawn_tp1"] = True
                            self.bought_coins[ticker]["acc_dawn_floor"] = 0.7  # 바닥 +0.7%
                            self._save_bot_bought()
                        return
                    
                    # 🌙 A 후속: 50% 익절 후 +0.7% 바닥 이탈 → 전량
                    if acc_dawn_tp1 and pnl_pct <= 0.7:
                        logger.warning(
                            f"🌙 [새벽짠돌이] {ticker.replace('KRW-','')} "
                            f"잔량 바닥 +0.7% 이탈 ({pnl_pct:+.2f}%) → 전량 익절!"
                        )
                        self._queue_sell(
                            ticker, portion=1.0,
                            reason=f"[새벽짠돌이] 잔량 +0.7% 이탈"
                        )
                        return
                    
                    # 🌙 B: 2시간 경과 + 수익 중 → 즉시 전량 (장기보유 방지)
                    _bt_str = info.get("buy_time", "")
                    if _bt_str:
                        _elapsed_h = (datetime.datetime.now() - datetime.datetime.fromisoformat(_bt_str)).total_seconds() / 3600
                        if _elapsed_h >= 2.0 and pnl_pct >= 0:
                            logger.warning(
                                f"🌙 [새벽짠돌이] {ticker.replace('KRW-','')} "
                                f"2h 경과 + 수익중 ({pnl_pct:+.2f}%) → 즉시 전량!"
                            )
                            self._queue_sell(
                                ticker, portion=1.0,
                                reason=f"[새벽짠돌이] 2h+수익 청산"
                            )
                            return
            except Exception as e:
                logger.debug(f"새벽짠돌이 로직 오류 {ticker}: {e}")
            
            acc_be_armed = info.get("acc_breakeven_armed", False)
            if not acc_be_armed and pnl_pct >= 1.0:
                # 본전 잠금 활성화
                self.bought_coins[ticker]["acc_breakeven_armed"] = True
                self._save_bot_bought()
                logger.info(
                    f"🛡️ [🏦매집] {ticker.replace('KRW-','')} "
                    f"+1% 달성 → +0.3% 바닥 잠금 활성화!"
                )
                try:
                    send_telegram(
                        f"🛡️ <b>매집 바닥 잠금!</b>\n"
                        f"코인: {ticker.replace('KRW-','')}\n"
                        f"현재: {pnl_pct:+.2f}%\n"
                        f"→ 이후 +0.3% 이하 → 즉시 익절"
                    )
                except Exception:
                    pass
            
            # 본전 잠금 활성화 후 +0.3% 이하 → 즉시 매도
            # (수수료 0.1% 차감 후 실수익 +0.2% 보장)
            if acc_be_armed and pnl_pct <= 0.3:
                logger.warning(
                    f"🛡️ [🏦매집] {ticker.replace('KRW-','')} "
                    f"바닥선 +0.3% 이탈 → 즉시 익절!"
                )
                self._queue_sell(
                    ticker, portion=1.0,
                    reason=f"[매집] 바닥잠금 +0.3% 이탈"
                )
                return
            
            # ── 💰 매집 TP: +2% 도달 시 50% 익절 + 잔량 트레일링 ──
            # 데이터 근거: 어제 성공 22건 평균 최고 +2.32%, 평균 마감 +1.54%
            # → 최고점 찍고 되돌아가는 패턴 → 절반 확정 수익 필요
            # 잔량 50%는 최고점 -1% 트레일링으로 홈런 기회도 유지
            acc_tp1_done = info.get("acc_tp1_done", False)
            if not acc_tp1_done and pnl_pct >= 2.0:
                logger.info(
                    f"💰 [🏦매집] {ticker.replace('KRW-','')} "
                    f"+2% 달성 → 50% 익절 + 잔량 트레일링!"
                )
                self._queue_sell(
                    ticker, portion=0.5,
                    reason=f"[매집] TP +2% 50%"
                )
                if ticker in self.bought_coins:
                    self.bought_coins[ticker]["acc_tp1_done"] = True
                    self.bought_coins[ticker]["acc_trail_high"] = current  # 트레일링 기준 시작점
                    self._save_bot_bought()
                try:
                    send_telegram(
                        f"💰 <b>매집 TP +2%!</b>\n"
                        f"코인: {ticker.replace('KRW-','')}\n"
                        f"현재: {pnl_pct:+.2f}%\n"
                        f"→ 50% 익절, 잔량 트레일링 모드"
                    )
                except Exception:
                    pass
                return
            
            # ── 매집 TP1 이후 잔량 트레일링 (홈런 추적) ──
            if acc_tp1_done:
                # 🆕 P5: TP1 이후 BB 폭발 감지 → surge 승격 (AZTEC 홈런 케이스!)
                # 미누님: "매집→홈런 가는 코인은 불타기 + 홈런 추적 필요"
                # 잔량 보유 중인데 +5% 이상 추가 상승 + BB 폭발 → surge 승격
                try:
                    if pnl_pct >= 5.0 and not info.get("surge_upgraded_from_acc", False):
                        df_acc_check = safe_api_call(pyupbit.get_ohlcv, ticker, 
                                                      interval=CONFIG["candle_interval_acc"], count=30)
                        if df_acc_check is not None and len(df_acc_check) >= 25:
                            close_a = df_acc_check["close"]
                            bb_w_a = ((close_a.rolling(20).mean() + 2*close_a.rolling(20).std()) -
                                      (close_a.rolling(20).mean() - 2*close_a.rolling(20).std()))
                            bb_avg_a = bb_w_a.iloc[-20:].mean()
                            
                            if bb_w_a.iloc[-1] >= bb_avg_a * 1.3:  # BB 30% 확장
                                # surge로 승격! (잔량이 홈런 가는 중)
                                if "acc_origin_buy_time" not in self.bought_coins[ticker]:
                                    self.bought_coins[ticker]["acc_origin_buy_time"] = info.get("buy_time", "")
                                self.bought_coins[ticker]["buy_type"] = "surge"
                                self.bought_coins[ticker]["surge_upgraded_from_acc"] = True
                                self._reset_surge_flags(ticker)
                                self._save_bot_bought()
                                logger.warning(
                                    f"🚀🌟 [매집TP1→서지] {ticker.replace('KRW-','')} | "
                                    f"+{pnl_pct:.2f}% + BB 30% 확장 → 홈런 추적 모드! "
                                    f"(불타기/트레일링/홈런 전환 활성화)"
                                )
                                send_telegram(
                                    f"🚀🌟 <b>매집→홈런 전환!</b>\n"
                                    f"코인: {ticker.replace('KRW-','')}\n"
                                    f"수익률: {pnl_pct:+.2f}%\n"
                                    f"BB 폭발 → 잔량 홈런 추적!"
                                )
                                return
                except Exception:
                    pass
                
                trail_high = self.bought_coins[ticker].get("acc_trail_high", current)
                # 최고점 갱신
                if current > trail_high:
                    self.bought_coins[ticker]["acc_trail_high"] = current
                    trail_high = current
                
                # 최고점 대비 -1% 하락 → 잔량 전량 매도
                trail_drop = (current - trail_high) / trail_high * 100
                if trail_drop <= -1.0:
                    logger.info(
                        f"💰 [🏦매집] {ticker.replace('KRW-','')} "
                        f"잔량 트레일링 -1% 이탈 → 전량 익절!"
                    )
                    self._queue_sell(
                        ticker, portion=1.0,
                        reason=f"[매집] 잔량 트레일링"
                    )
                    return
                
                # 안전장치: 본전(0%) 이하로 떨어지면 무조건 매도
                if pnl_pct <= 0.0:
                    logger.warning(
                        f"🛡️ [🏦매집] {ticker.replace('KRW-','')} "
                        f"잔량 본전 이탈 → 전량 매도!"
                    )
                    self._queue_sell(
                        ticker, portion=1.0,
                        reason=f"[매집] 잔량 본전 이탈"
                    )
                    return

            # ── 손절: -5% (저가 코인은 더 넓게) ──
            # ① 진입 후 2분은 손절 유예 (순간 개미털기 방지, 3분→2분 축소)
            _acc_elapsed = 0
            try:
                _bt = info.get("buy_time", "")
                if _bt:
                    _acc_elapsed = (datetime.datetime.now() - datetime.datetime.fromisoformat(_bt)).total_seconds()
            except Exception:
                pass

            if _acc_elapsed < 120:  # 진입 후 2분 유예
                pass  # 손절 스킵
            else:
                # 가격대별 동적 손절 기준
                stop_threshold = get_dynamic_stop(buy_price, "accumulation")

                # ③ 1분봉 종가 기준 손절 (순간 털기 무시)
                try:
                    df_sl = safe_api_call(pyupbit.get_ohlcv, ticker,
                                         interval="minute1", count=3)
                    if df_sl is not None and len(df_sl) >= 2:
                        close_price = df_sl["close"].iloc[-2]  # 직전 완성된 봉 종가
                        pnl_close   = (close_price - buy_price) / buy_price * 100
                        if pnl_close <= stop_threshold:
                            logger.warning(
                                f"💀 [🏦매집] {ticker.replace('KRW-','')} | "
                                f"종가손절 {stop_threshold}% ({pnl_close:+.2f}%)"
                            )
                            self._queue_sell(ticker, portion=1.0,
                                      reason=f"[매집] 손절 {stop_threshold}% ({pnl_close:.2f}%)",
                                      is_stoploss=True)
                            return
                    else:
                        # API 실패 시 현재가 기준
                        if pnl_pct <= stop_threshold:
                            logger.warning(f"💀 [🏦매집] {ticker.replace('KRW-','')} | 손절 {stop_threshold}%")
                            self._queue_sell(ticker, portion=1.0, reason=f"[매집] 손절 {stop_threshold}%", is_stoploss=True)
                            return
                except Exception:
                    if pnl_pct <= stop_threshold:
                        logger.warning(f"💀 [🏦매집] {ticker.replace('KRW-','')} | 손절 {stop_threshold}%")
                        self._queue_sell(ticker, portion=1.0, reason=f"[매집] 손절 {stop_threshold}%", is_stoploss=True)
                        return

            # ── 🔧 강화된 추가 매집: 다중 조건 확인 (추세하락 물타기 방지) ──
            # 오늘 CFG -6.18%, ONT -5.04%, ENA -2.94% 손실의 주범이었던
            # "무조건 -2%에서 물타기" 로직 개선
            acc_added = self.bought_coins[ticker].get("acc_add_done", False)
            if not acc_added and pnl_pct <= -2.0:
                try:
                    # 1. 시장 상태: BTC 상승장에서만 허용 (하락장 = 추세 하락 위험)
                    if get_eth_status() != "bullish":
                        logger.debug(f"🏦 [물타기금지] {ticker.replace('KRW-','')} BTC 하락장 → 추가매집 차단")
                    else:
                        df_wt = safe_api_call(
                            pyupbit.get_ohlcv, ticker,
                            interval=CONFIG["candle_interval_acc"], count=30
                        )
                        if df_wt is not None and len(df_wt) >= 25:
                            c_wt = df_wt["close"]
                            o_wt = df_wt["open"]
                            h_wt = df_wt["high"]
                            l_wt = df_wt["low"]
                            v_wt = df_wt["volume"]
                            
                            # 2. CCI 극단 과매도 확인 (< -150)
                            tp_wt = (h_wt + l_wt + c_wt) / 3
                            tpm_wt = tp_wt.rolling(20).mean()
                            tps_wt = tp_wt.rolling(20).std()
                            cci_wt = (tp_wt - tpm_wt) / (0.015 * tps_wt)
                            cci_now = cci_wt.iloc[-1]
                            cci_oversold = cci_now < -150
                            
                            # 3. 현재봉 양봉 확인 (반등 시작 신호)
                            curr_bullish = c_wt.iloc[-1] > o_wt.iloc[-1]
                            
                            # 4. 거래량 지지 (평균의 80% 이상 = 매수세 유입)
                            avg_vol = v_wt.iloc[-21:-1].mean()
                            vol_support = avg_vol > 0 and v_wt.iloc[-1] >= avg_vol * 0.8
                            
                            # 5. 추세 하락 아님 (최근 5봉 연속 하락 체크)
                            consecutive_down = all(
                                c_wt.iloc[-i] < c_wt.iloc[-i-1] for i in range(1, 5)
                            )
                            not_trending_down = not consecutive_down
                            
                            # 모든 조건 만족 시에만 물타기
                            if (cci_oversold and curr_bullish and 
                                vol_support and not_trending_down):
                                avail = self.get_balance("KRW")
                                # P5: 본매수 × 0.8 (최소 min_trade_krw 보장)
                                _existing_acc = info.get("amount_krw", 0)
                                add_amt = min(avail * 0.95, max(CONFIG["min_trade_krw"], int(_existing_acc * 0.8)))
                                if add_amt >= CONFIG["min_trade_krw"]:
                                    logger.info(
                                        f"🏦 [매집추가✅] {ticker.replace('KRW-','')} | "
                                        f"{pnl_pct:+.2f}% | CCI:{cci_now:.0f} | 양봉+거래량OK → 10만 추가!"
                                    )
                                    self.buy(ticker, add_amt, buy_type="accumulation")
                                    if ticker in self.bought_coins:
                                        self.bought_coins[ticker]["acc_add_done"] = True
                                        # 물타기 시점부터 2시간 카운트 시작
                                        self.bought_coins[ticker]["buy_time"] = datetime.datetime.now().isoformat()
                                        self.bought_coins[ticker]["timeout_mode"] = False
                                        self._save_bot_bought()
                                    send_telegram(
                                        f"🏦 <b>추가 매집!</b>\n"
                                        f"코인: {ticker.replace('KRW-','')}\n"
                                        f"수익률: {pnl_pct:+.2f}%\n"
                                        f"CCI: {cci_now:.0f} | 양봉반등 확인\n"
                                        f"추가: {add_amt:,.0f}원"
                                    )
                            else:
                                # 조건 불충족 사유 로깅 (디버깅용)
                                fail_reasons = []
                                if not cci_oversold:  fail_reasons.append(f"CCI{cci_now:.0f}")
                                if not curr_bullish:  fail_reasons.append("음봉")
                                if not vol_support:   fail_reasons.append("거래량↓")
                                if consecutive_down:  fail_reasons.append("5봉연속하락")
                                logger.debug(
                                    f"🏦 [물타기보류] {ticker.replace('KRW-','')} "
                                    f"{pnl_pct:+.2f}% | 실패: {','.join(fail_reasons)}"
                                )
                except Exception as _e:
                    logger.debug(f"물타기 체크 오류 {ticker}: {_e}")

            # ── BB 폭발 (수축→팽창) → surge 전환! + 노말 전환 체크 (P3) ──
            try:
                df_acc = safe_api_call(pyupbit.get_ohlcv, ticker, interval=CONFIG["candle_interval_acc"], count=30)
                if df_acc is not None and len(df_acc) >= 25:
                    close_acc = df_acc["close"]
                    open_acc  = df_acc["open"]
                    ema5_acc  = close_acc.ewm(span=5,   adjust=False).mean()
                    ema20_acc = close_acc.ewm(span=20,  adjust=False).mean()
                    ema100_acc= close_acc.ewm(span=100, adjust=False).mean()
                    bb_mid_acc = close_acc.rolling(20).mean()
                    bb_std_acc = close_acc.rolling(20).std()
                    bb_w_acc   = (bb_mid_acc + 2*bb_std_acc) - (bb_mid_acc - 2*bb_std_acc)
                    bb_avg_acc = bb_w_acc.iloc[-20:].mean()
                    
                    # ── (A) BB 폭발 → 서지 전환 (기존) ──
                    if bb_w_acc.iloc[-1] >= bb_avg_acc * 1.2 and pnl_pct >= 0:
                        # surge 전환 시 원래 매집 시간 기억 (타임아웃 기준 유지)
                        if "acc_origin_buy_time" not in self.bought_coins[ticker]:
                            self.bought_coins[ticker]["acc_origin_buy_time"] = info.get("buy_time", "")
                        self.bought_coins[ticker]["buy_type"] = "surge"
                        # 🔧 P2: 서지 플래그 완전 초기화 (단순 2개 → 모두 초기화)
                        self._reset_surge_flags(ticker)
                        self._save_bot_bought()
                        logger.info(f"🚀 [🏦→surge] {ticker.replace('KRW-','')} | BB 폭발! → surge 전환 (플래그 초기화)")
                        send_telegram(f"🚀 <b>매집→급등 전환!</b>\n코인: {ticker.replace('KRW-','')}\n수익률: {pnl_pct:+.2f}%")
                        return
                    
                    # ── (B) 🔧 P3: 조용한 추세 형성 → 노말 전환 ──
                    # 조건: EMA 정렬 + BB 완만하게 팽창 + EMA5 상승 + 수익권
                    ema_aligned_n = (ema5_acc.iloc[-1] > ema20_acc.iloc[-1] > ema100_acc.iloc[-1])
                    bb_widening_n = (bb_w_acc.iloc[-1] > bb_w_acc.iloc[-3] * 1.05)  # 약한 팽창
                    ema5_rising_n = (ema5_acc.iloc[-1] > ema5_acc.iloc[-3])          # EMA5 상승 중
                    prev_bull_n   = (close_acc.iloc[-2] > open_acc.iloc[-2])         # 직전봉 양봉
                    
                    if (ema_aligned_n and bb_widening_n and ema5_rising_n and 
                        prev_bull_n and pnl_pct > 0):
                        self.bought_coins[ticker]["buy_type"] = "normal"
                        self.bought_coins[ticker]["entry_eth_status"] = get_eth_status()
                        self._save_bot_bought()
                        logger.info(
                            f"📶 [🏦→노말] {ticker.replace('KRW-','')} | "
                            f"추세 형성 (EMA정렬+BB팽창+양봉) → 노말 전환 "
                            f"[출신:매집 유지]"
                        )
                        send_telegram(
                            f"📶 <b>매집→노말 전환!</b>\n"
                            f"코인: {ticker.replace('KRW-','')}\n"
                            f"사유: 추세 형성 감지\n"
                            f"수익률: {pnl_pct:+.2f}%"
                        )
                        return
            except Exception:
                pass

            # ── EMA100 이탈 3봉 + BB 밖 → 매도 ──
            # 추가매집(acc_add_done) 후에만 적용
            # → 최초 진입 시 세력 흔들기(일시 BB이탈) 허용
            # → 추가매집 후에도 계속 밀리면 진짜 하락으로 판단
            acc_added_check = self.bought_coins[ticker].get("acc_add_done", False)
            if acc_added_check:
                try:
                    df_n = safe_api_call(pyupbit.get_ohlcv, ticker, interval=CONFIG["candle_interval_acc"], count=120)
                    if df_n is not None and len(df_n) >= 110:
                        c_n  = df_n["close"]
                        e100 = c_n.ewm(span=100, adjust=False).mean()
                        bm_n = c_n.rolling(20).mean(); bs_n = c_n.rolling(20).std()
                        bl_n = bm_n - 2*bs_n
                        inside_n = current >= bl_n.iloc[-1]
                        # 5봉 연속 EMA100 아래 + BB 이탈 → 진짜 하락
                        five_below = (c_n.iloc[-1] < e100.iloc[-1] and
                                      c_n.iloc[-2] < e100.iloc[-2] and
                                      c_n.iloc[-3] < e100.iloc[-3] and
                                      c_n.iloc[-4] < e100.iloc[-4] and
                                      c_n.iloc[-5] < e100.iloc[-5])
                        if five_below and not inside_n:
                            logger.warning(f"📉 [🏦매집] {ticker.replace('KRW-','')} 추가매집 후 EMA100 5봉+BB 이탈 → 매도!")
                            self._queue_sell(ticker, portion=1.0, reason=f"[매집] EMA100+BB이탈", is_stoploss=pnl_pct < 0)
                            return
                except Exception:
                    pass
            else:
                # 추가매집 전: EMA100 아래로 깊이 내려가면 -5% 손절로만 처리
                # (세력 흔들기 구간 - BB 이탈해도 바로 팔지 않음)
                logger.debug(f"🏦 [매집대기] {ticker.replace('KRW-','')} | BB/EMA100 체크 패스 (추가매집 전 흔들기 허용) {pnl_pct:+.2f}%") if pnl_pct < -1.5 else None

            # ── 타임아웃: BB 폭발 없으면 청산 ──
            # 🔧 미누님 개선안: 이익 중이면 1시간씩 계속 연장
            # 물타기 전: 4시간 / 물타기 후: 2시간 (기본 타임아웃)
            # 타임아웃 도달 시:
            #   ① 본전잠금 활성화 상태 (+1% 찍음) → 1시간 연장 (최대 12시간)
            #   ② 수익권 (+0% 이상)              → 1시간 연장 (최대 12시간)
            #   ③ 손실권 (-0% 미만)              → 기존 트레일링 모드 진입
            try:
                _buy_time = info.get("buy_time", "")
                acc_added_timeout = self.bought_coins[ticker].get("acc_add_done", False)
                base_timeout_h = 2.0 if acc_added_timeout else 4.0
                # 연장 횟수 (0 ~ 8회, 기본 타임아웃 + 최대 8시간 추가)
                extend_count = self.bought_coins[ticker].get("acc_extend_count", 0)
                max_extends  = 8  # 최대 8회 연장 (총 최대 12시간)
                timeout_h    = base_timeout_h + extend_count * 1.0
                
                if _buy_time:
                    elapsed_h = (datetime.datetime.now() - datetime.datetime.fromisoformat(_buy_time)).total_seconds() / 3600
                    if elapsed_h >= timeout_h:
                        # 🎯 연장 조건: 본전잠금 발동 OR 현재 수익권
                        is_profitable = pnl_pct >= 0.0
                        be_armed = self.bought_coins[ticker].get("acc_breakeven_armed", False)
                        
                        if (is_profitable or be_armed) and extend_count < max_extends:
                            # 1시간 연장!
                            new_count = extend_count + 1
                            self.bought_coins[ticker]["acc_extend_count"] = new_count
                            self._save_bot_bought()
                            be_tag = " 🛡️본전잠금" if be_armed else ""
                            logger.info(
                                f"⏰➕ [🏦매집] {ticker.replace('KRW-','')} | "
                                f"{elapsed_h:.1f}h 경과 {pnl_pct:+.2f}%{be_tag} → "
                                f"1시간 연장! (연장 {new_count}/{max_extends}, 총 {timeout_h + 1:.0f}h)"
                            )
                            return
                        
                        # 최대 연장 횟수 도달 or 손실권 → 기존 트레일링 모드
                        timeout_mode  = self.bought_coins[ticker].get("timeout_mode", False)
                        timeout_ref   = self.bought_coins[ticker].get("timeout_ref_price", current)
                        timeout_start = self.bought_coins[ticker].get("timeout_start_time", None)

                        if not timeout_mode:
                            # 처음 타임아웃 도달 → 트레일링 모드 진입
                            tag = "물타기 후" if acc_added_timeout else ""
                            max_tag = " (최대연장)" if extend_count >= max_extends else ""
                            logger.info(
                                f"⏰ [🏦매집] {ticker.replace('KRW-','')} | "
                                f"{tag}{timeout_h:.0f}h{max_tag} → 트레일링 모드! "
                                f"기준가: {current:.4f}"
                            )
                            self.bought_coins[ticker]["timeout_mode"]       = True
                            self.bought_coins[ticker]["timeout_ref_price"]  = current
                            self.bought_coins[ticker]["timeout_start_time"] = datetime.datetime.now().isoformat()
                            self._save_bot_bought()
                            return

                        # 트레일링 모드 진행 중
                        ref_change    = (current - timeout_ref) / timeout_ref * 100
                        elapsed_extra = 0
                        if timeout_start:
                            elapsed_extra = (datetime.datetime.now() - datetime.datetime.fromisoformat(timeout_start)).total_seconds() / 60

                        if ref_change >= 1.0:
                            logger.info(f"⏰✅ [🏦타임아웃] {ticker.replace('KRW-','')} | +1% 반등 → 익절!")
                            self._queue_sell(ticker, portion=1.0, reason=f"[매집] 타임아웃반등", is_stoploss=False)
                            return
                        elif ref_change <= -1.0 or elapsed_extra >= 30:
                            tag2 = "추가하락" if ref_change <= -1.0 else "30분초과"
                            logger.info(f"⏰❌ [🏦타임아웃] {ticker.replace('KRW-','')} | {tag2} → 청산")
                            self._queue_sell(ticker, portion=1.0, reason=f"[매집] 타임아웃{tag2}", is_stoploss=pnl_pct < 0)
                            return
            except Exception:
                pass

        # ============================================================
        # 📈 V자 반등 전략
        # ============================================================
        elif buy_type == "v_reversal":

            # ── 손절: 진입 2분 유예 + 저가코인 완화 + 1분봉 종가 기준 (3분→2분 축소) ──
            _v_elapsed = 0
            try:
                _bt = info.get("buy_time", "")
                if _bt:
                    _v_elapsed = (datetime.datetime.now() - datetime.datetime.fromisoformat(_bt)).total_seconds()
            except Exception:
                pass

            if _v_elapsed >= 120:
                v_stop = get_dynamic_stop(buy_price, "v_reversal")
                try:
                    df_vsl = safe_api_call(pyupbit.get_ohlcv, ticker, interval="minute1", count=3)
                    if df_vsl is not None and len(df_vsl) >= 2:
                        close_v   = df_vsl["close"].iloc[-2]
                        pnl_close_v = (close_v - buy_price) / buy_price * 100
                        if pnl_close_v <= v_stop:
                            logger.warning(f"💀 [📈V자] {ticker.replace('KRW-','')} | 종가손절 {v_stop}% ({pnl_close_v:+.2f}%)")
                            self._queue_sell(ticker, portion=1.0, reason=f"[V자] 손절 {v_stop}% ({pnl_close_v:.2f}%)", is_stoploss=True)
                            return
                    else:
                        if pnl_pct <= v_stop:
                            logger.warning(f"💀 [📈V자] {ticker.replace('KRW-','')} | 손절 {v_stop}%")
                            self._queue_sell(ticker, portion=1.0, reason=f"[V자] 손절 {v_stop}%", is_stoploss=True)
                            return
                except Exception:
                    if pnl_pct <= v_stop:
                        logger.warning(f"💀 [📈V자] {ticker.replace('KRW-','')} | 손절 {v_stop}%")
                        self._queue_sell(ticker, portion=1.0, reason=f"[V자] 손절 {v_stop}%", is_stoploss=True)
                        return

            # ── 물타기: -2% + CCI/RSI 더 극단일 때 10만 추가 ──
            v_add_done = self.bought_coins[ticker].get("v_add_done", False)
            if not v_add_done and pnl_pct <= -2.0:
                try:
                    df_wt = safe_api_call(pyupbit.get_ohlcv, ticker, interval=CONFIG["candle_interval_v"], count=60)
                    if df_wt is not None and len(df_wt) >= 50:
                        c_wt   = df_wt["close"]
                        l_wt   = df_wt["low"]
                        h_wt   = df_wt["high"]
                        tp_wt  = (h_wt + l_wt + c_wt) / 3
                        tpm_wt = tp_wt.rolling(20).mean()
                        tps_wt = tp_wt.rolling(20).std()
                        cci_wt = (tp_wt - tpm_wt) / (0.015 * tps_wt)
                        d_wt   = c_wt.diff()
                        g_wt   = d_wt.clip(lower=0).rolling(14).mean()
                        ls_wt  = (-d_wt.clip(upper=0)).rolling(14).mean()
                        rsi_wt = (100 - (100 / (1 + g_wt / ls_wt))).iloc[-1]
                        cci_now = cci_wt.iloc[-1]

                        # CCI < -200, RSI < 25 → 더 극단 과매도 → 물타기
                        if cci_now < -200 and rsi_wt < 25:
                            avail   = self.get_balance("KRW")
                            # P5: 본매수 × 0.8 (최소 min_trade_krw 보장)
                            _existing_v = info.get("amount_krw", 0)
                            add_amt = min(avail * 0.95, max(CONFIG["min_trade_krw"], int(_existing_v * 0.8)))
                            if add_amt >= CONFIG["min_trade_krw"]:
                                logger.info(f"📈 [V자물타기] {ticker.replace('KRW-','')} | CCI:{cci_now:.0f} RSI:{rsi_wt:.1f} → 10만 추가!")
                                self.buy(ticker, add_amt, buy_type="v_reversal")
                                if ticker in self.bought_coins:
                                    self.bought_coins[ticker]["v_add_done"] = True
                                    self._save_bot_bought()
                                send_telegram(f"📈 <b>V자 물타기!</b>\n코인: {ticker.replace('KRW-','')}\nCCI: {cci_now:.0f} | RSI: {rsi_wt:.1f}")
                except Exception:
                    pass

            # ── EMA100 위로 복귀 + 수익권 → 50% 익절 ──
            try:
                df_v = safe_api_call(pyupbit.get_ohlcv, ticker, interval=CONFIG["candle_interval_v"], count=110)
                if df_v is not None and len(df_v) >= 105:
                    c_v   = df_v["close"]
                    e100v = c_v.ewm(span=100, adjust=False).mean()
                    if c_v.iloc[-1] > e100v.iloc[-1] and pnl_pct > 0:
                        if not info.get("v_tp1_done", False):
                            logger.info(f"📈 [V자TP1] {ticker.replace('KRW-','')} | EMA100 복귀 50% 익절")
                            self._queue_sell(ticker, portion=0.5, reason=f"[V자] EMA100복귀 50%")
                            if ticker in self.bought_coins:
                                self.bought_coins[ticker]["v_tp1_done"] = True
                                self._save_bot_bought()
                            return
            except Exception:
                pass

            # ── EMA100 3봉 안착 → 서지 or 노말 전환 (🔧 P5: 노말 경로 추가) ──
            try:
                df_v2 = safe_api_call(pyupbit.get_ohlcv, ticker, interval=CONFIG["candle_interval_v"], count=110)
                if df_v2 is not None and len(df_v2) >= 105:
                    c_v2   = df_v2["close"]
                    o_v2   = df_v2["open"]
                    e5_v2  = c_v2.ewm(span=5,   adjust=False).mean()
                    e20_v2 = c_v2.ewm(span=20,  adjust=False).mean()
                    e100v2 = c_v2.ewm(span=100, adjust=False).mean()
                    bm_v2  = c_v2.rolling(20).mean()
                    bs_v2  = c_v2.rolling(20).std()
                    bw_v2  = (bm_v2 + 2*bs_v2) - (bm_v2 - 2*bs_v2)

                    # 공통: EMA100 3봉 연속 안착 확인
                    ema100_settled = (c_v2.iloc[-1] > e100v2.iloc[-1] and
                                      c_v2.iloc[-2] > e100v2.iloc[-2] and
                                      c_v2.iloc[-3] > e100v2.iloc[-3])

                    # ── (A) V자 → 서지 전환 (강한 회복: +2% 이상 + TP1 완료) ──
                    if (ema100_settled and
                            pnl_pct >= 2.0 and info.get("v_tp1_done", False)):
                        self.bought_coins[ticker]["buy_type"] = "surge"
                        # 🔧 P2: 서지 플래그 완전 초기화
                        self._reset_surge_flags(ticker)
                        self._save_bot_bought()
                        logger.info(f"🚀 [V자→서지] {ticker.replace('KRW-','')} | EMA100 안착+TP1완료 → 서지 전환 (플래그 초기화)")
                        send_telegram(f"🚀 <b>V자→급등 전환!</b>\n코인: {ticker.replace('KRW-','')}\n수익률: {pnl_pct:+.2f}%")
                        return

                    # ── (B) 🔧 P5: V자 → 노말 전환 (약한 회복: 추세만 형성) ──
                    # 서지 조건 불충족이지만 EMA100 위 + 추세 형성 시 → 노말로 오래 가져가기
                    # 조건: EMA 정렬 + BB 팽창 중 + EMA5 상승 + 직전봉 양봉 + 수익권(0% 이상)
                    if ema100_settled and pnl_pct >= 0:
                        ema_aligned_v = (e5_v2.iloc[-1] > e20_v2.iloc[-1] > e100v2.iloc[-1])
                        bb_widening_v = (bw_v2.iloc[-1] > bw_v2.iloc[-3] * 1.05)
                        ema5_rising_v = (e5_v2.iloc[-1] > e5_v2.iloc[-3])
                        prev_bull_v   = (c_v2.iloc[-2] > o_v2.iloc[-2])

                        if ema_aligned_v and bb_widening_v and ema5_rising_v and prev_bull_v:
                            self.bought_coins[ticker]["buy_type"] = "normal"
                            self.bought_coins[ticker]["entry_eth_status"] = get_eth_status()
                            # V자 플래그 유지 (v_tp1_done 등) - 혹시 다시 V자로 돌아올 경우 대비
                            self._save_bot_bought()
                            logger.info(
                                f"📶 [V자→노말] {ticker.replace('KRW-','')} | "
                                f"EMA100 안착+추세 형성 → 노말 전환 "
                                f"[출신:V자 유지]"
                            )
                            send_telegram(
                                f"📶 <b>V자→노말 전환!</b>\n"
                                f"코인: {ticker.replace('KRW-','')}\n"
                                f"사유: EMA100 안정+추세 형성\n"
                                f"수익률: {pnl_pct:+.2f}%\n"
                                f"→ 오래 가져가기 모드"
                            )
                            return
            except Exception:
                pass

            # ── 잡아먹기 음봉 + EMA5 하향 → 즉시 매도 (공통 조건에서 처리됨) ──

            # ── 패턴 소멸: 매수 후 고점 갱신 없이 -2% → 매도 ──
            if pnl_pct <= -2.0 and highest <= buy_price * 1.005:
                logger.info(f"📈 [V자소멸] {ticker.replace('KRW-','')} | 고점 갱신 없이 하락 → 매도")
                self._queue_sell(ticker, portion=1.0, reason=f"[V자] 패턴소멸", is_stoploss=True)
                return

        # ============================================================
        # 📶 NORMAL 코인 전략 (오래 가져가기 — 트레일링 + EMA 기반 exit)
        # ============================================================
        elif buy_type in ("normal", "wakeup", "active_watch"):
            eth_st = get_eth_status()  # 현재 ETH 상태 (트레일링/불타기용)
            entry_eth_st = info.get("entry_eth_status", eth_st)  # 매수 시점 ETH (손절용)
            
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            # 🌙 P5: 새벽 짠돌이 모드 (00:00~08:30) — normal/active 확장
            # 미누님 철학: "새벽은 무조건 짠돌이"
            # 데이터 근거: 4/11 새벽 36건 -993원 (active/normal 위주 손실)
            # ⚠️ 예외: 이미 TP1(노말 +3%) 달성한 잔량은 건들지 않음
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            try:
                _now_h = datetime.datetime.now().hour
                _now_m = datetime.datetime.now().minute
                _is_dawn = _now_h < 8 or (_now_h == 8 and _now_m < 30)
                _normal_tp1_done = info.get("normal_tp1_done", False)
                
                # 🌟 P5: 홈런 면제 체크 (FF 케이스)
                _homerun_bypass_dawn_n = False
                try:
                    _dc_dawn_n = get_cached_daily_change(ticker)
                    if _dc_dawn_n >= 10.0:
                        _homerun_bypass_dawn_n = True
                except Exception:
                    pass
                
                if _is_dawn and not _normal_tp1_done and not _homerun_bypass_dawn_n:
                    n_dawn_tp1 = info.get("normal_dawn_tp1", False)
                    
                    # 🌙 D: +1.5% 하드 익절 (욕심금지)
                    if pnl_pct >= 1.5:
                        logger.warning(
                            f"🌙 [새벽짠돌이] {ticker.replace('KRW-','')} [{buy_type}] "
                            f"+1.5% 도달 ({pnl_pct:+.2f}%) → 전량 하드익절!"
                        )
                        self._queue_sell(
                            ticker, portion=1.0,
                            reason=f"[새벽짠돌이] +1.5% 하드익절"
                        )
                        return
                    
                    # 🌙 A: +1% 도달 시 즉시 50% 익절
                    if not n_dawn_tp1 and pnl_pct >= 1.0:
                        logger.warning(
                            f"🌙 [새벽짠돌이] {ticker.replace('KRW-','')} [{buy_type}] "
                            f"+1% 도달 ({pnl_pct:+.2f}%) → 50% 즉시 익절!"
                        )
                        self._queue_sell(
                            ticker, portion=0.5,
                            reason=f"[새벽짠돌이] +1% 50%익절"
                        )
                        if ticker in self.bought_coins:
                            self.bought_coins[ticker]["normal_dawn_tp1"] = True
                            self._save_bot_bought()
                        return
                    
                    # 🌙 A 후속: 50% 익절 후 +0.7% 바닥 이탈 → 전량
                    if n_dawn_tp1 and pnl_pct <= 0.7:
                        logger.warning(
                            f"🌙 [새벽짠돌이] {ticker.replace('KRW-','')} [{buy_type}] "
                            f"잔량 바닥 +0.7% 이탈 ({pnl_pct:+.2f}%) → 전량 익절!"
                        )
                        self._queue_sell(
                            ticker, portion=1.0,
                            reason=f"[새벽짠돌이] 잔량 +0.7% 이탈"
                        )
                        return
                    
                    # 🌙 B: 2시간 경과 + 수익 중 → 즉시 전량
                    _bt_str = info.get("buy_time", "")
                    if _bt_str:
                        _elapsed_h = (datetime.datetime.now() - datetime.datetime.fromisoformat(_bt_str)).total_seconds() / 3600
                        if _elapsed_h >= 2.0 and pnl_pct >= 0:
                            logger.warning(
                                f"🌙 [새벽짠돌이] {ticker.replace('KRW-','')} [{buy_type}] "
                                f"2h 경과 + 수익중 ({pnl_pct:+.2f}%) → 즉시 전량!"
                            )
                            self._queue_sell(
                                ticker, portion=1.0,
                                reason=f"[새벽짠돌이] 2h+수익 청산"
                            )
                            return
            except Exception as e:
                logger.debug(f"새벽짠돌이(노말) 로직 오류 {ticker}: {e}")
            
            # ── ⚪ 횡보장(neutral) 순환 매매 모드 ──
            # 미누님 전략: 횡보장은 변동성 작음 → 작게 먹고 빨리 빠지기
            #   +1% → 바닥 +1% 잠금
            #   +2% → 즉시 전량 매도
            #   +1% 이탈 → 즉시 매도 (순환)
            _mkt_state, _ = get_market_state()
            if _mkt_state == "neutral":
                # 마이너스 경험 기록
                if pnl_pct < 0 and not info.get("was_underwater", False):
                    self.bought_coins[ticker]["was_underwater"] = True
                    self._save_bot_bought()
                
                # 마이너스 찍었다가 플러스 복귀 → 즉시 매도 (본전탈출)
                if info.get("was_underwater", False) and pnl_pct > 0:
                    logger.warning(
                        f"⚪ [횡보장탈출] {ticker.replace('KRW-','')} "
                        f"마이너스→플러스 복귀 → 즉시 매도!"
                    )
                    self._queue_sell(
                        ticker, portion=1.0,
                        reason=f"[횡보장] 본전탈출"
                    )
                    return
                
                # +2% 도달 → 즉시 전량 매도
                if pnl_pct >= 2.0:
                    logger.warning(
                        f"⚪ [횡보장순환] {ticker.replace('KRW-','')} "
                        f"+2% 달성 → 즉시 전량 매도!"
                    )
                    self._queue_sell(
                        ticker, portion=1.0,
                        reason=f"[횡보장] +2% 순환 매도"
                    )
                    return
                
                # +1% 도달 시 바닥 +1% 잠금 활성화
                neut_armed = info.get("neutral_armed", False)
                if not neut_armed and pnl_pct >= 1.0:
                    self.bought_coins[ticker]["neutral_armed"] = True
                    self._save_bot_bought()
                    logger.info(
                        f"⚪ [횡보장순환] {ticker.replace('KRW-','')} "
                        f"+1% 달성 → 바닥 +1% 잠금!"
                    )
                
                # 바닥 +1% 이탈 → 즉시 매도
                if neut_armed and pnl_pct <= 1.0:
                    logger.warning(
                        f"⚪ [횡보장순환] {ticker.replace('KRW-','')} "
                        f"바닥선 +1% 이탈 → 즉시 익절!"
                    )
                    self._queue_sell(
                        ticker, portion=1.0,
                        reason=f"[횡보장] +1% 바닥 이탈"
                    )
                    return
            
            # ── 🛡️ 바닥 잠금 (수수료 만회 + 최소 수익 보장) ──
            # 🔧 P5: wakeup은 +0.5%, normal/active_watch는 +0.3%
            # wakeup은 "호가 깨어남 = 세력 출발"이니 좀 더 홀딩
            # 🔧 P5: TP1 완료 후에는 이 바닥잠금 스킵 → TP1 전용 동적 바닥 적용
            norm_be_armed = info.get("norm_breakeven_armed", False)
            tp1_done = info.get("normal_tp1_done", False)
            _be_floor = 0.5 if buy_type == "wakeup" else 0.3  # P5: wakeup +0.5%
            
            if not norm_be_armed and pnl_pct >= 1.0:
                self.bought_coins[ticker]["norm_breakeven_armed"] = True
                self._save_bot_bought()
                _type_tag = "🔥깨어남" if buy_type == "wakeup" else "📶노말"
                logger.info(
                    f"🛡️ [{_type_tag}] {ticker.replace('KRW-','')} "
                    f"+1% 달성 → +{_be_floor}% 바닥 잠금 활성화!"
                )
                try:
                    send_telegram(
                        f"🛡️ <b>{_type_tag} 바닥 잠금!</b>\n"
                        f"코인: {ticker.replace('KRW-','')}\n"
                        f"현재: {pnl_pct:+.2f}%\n"
                        f"→ 이후 +{_be_floor}% 이하 → 즉시 익절"
                    )
                except Exception:
                    pass
            
            # 🔧 P5: TP1 전에만 바닥 적용 (TP1 후는 아래 동적 바닥으로)
            if norm_be_armed and not tp1_done and pnl_pct <= _be_floor:
                _type_tag = "🔥깨어남" if buy_type == "wakeup" else "📶노말"
                logger.warning(
                    f"🛡️ [{_type_tag}] {ticker.replace('KRW-','')} "
                    f"바닥선 +{_be_floor}% 이탈 → 즉시 익절!"
                )
                self._queue_sell(
                    ticker, portion=1.0,
                    reason=f"[{_type_tag}] 바닥잠금 +{_be_floor}% 이탈"
                )
                return

            # 손절 기준: 가격대별 동적 + ETH 상태
            base_stop = get_dynamic_stop(buy_price, "normal")
            if entry_eth_st == "bearish":
                stop_pct = base_stop * 0.5  # 하락장: 절반으로 더 타이트
            else:
                stop_pct = base_stop

            # 트레일링 기준: 현재 ETH 상태 실시간 반영
            if eth_st == "bearish":
                trail_trigger = 3.0
                trail_drop    = -2.0
                hard_tp       = 10.0
            else:
                trail_trigger = 5.0
                trail_drop    = -2.5
                hard_tp       = 15.0

            # ── ① 손절 ──
            if pnl_pct <= stop_pct:
                self._queue_sell(ticker, portion=1.0,
                          reason=f"손절", is_stoploss=True)
                return

            # ── ② 스윙 체크 (120분 후 BB 기준 / 이후 60분마다 반복) ──
            try:
                buy_time = info.get("buy_time", "")
                if buy_time:
                    elapsed_min = (datetime.datetime.now() - datetime.datetime.fromisoformat(buy_time)).seconds // 60
                    swing_mode  = self.bought_coins[ticker].get("swing_mode", False)
                    next_check  = self.bought_coins[ticker].get("swing_next_check", None)

                    df_sw = safe_api_call(pyupbit.get_ohlcv, ticker,
                                          interval=CONFIG["candle_interval_acc"], count=30)
                    inside_bb_sw = True
                    if df_sw is not None and len(df_sw) >= 25:
                        close_sw  = df_sw["close"]
                        bb_mid_sw = close_sw.rolling(window=20).mean()
                        bb_std_sw = close_sw.rolling(window=20).std()
                        bb_low_sw = bb_mid_sw - 2 * bb_std_sw
                        inside_bb_sw = current >= bb_low_sw.iloc[-1]

                    if not swing_mode and elapsed_min >= 120:
                        if not inside_bb_sw:
                            logger.info(f"⏱️ [스윙체크] {ticker.replace('KRW-','')} | 120분 BB 이탈 {pnl_pct:+.2f}% → 매도")
                            self._queue_sell(ticker, portion=1.0,
                                      reason=f"스윙 120분 BB이탈", is_stoploss=True)
                            return
                        else:
                            next_dt = (datetime.datetime.now() + datetime.timedelta(minutes=60)).isoformat()
                            self.bought_coins[ticker]["swing_mode"]       = True
                            self.bought_coins[ticker]["swing_next_check"] = next_dt
                            logger.info(f"🔄 [스윙모드] {ticker.replace('KRW-','')} | 120분 BB 안 {pnl_pct:+.2f}% → 스윙 유지! (60분마다 체크)")

                    elif swing_mode and next_check:
                        if datetime.datetime.now() >= datetime.datetime.fromisoformat(next_check):
                            if not inside_bb_sw:
                                logger.info(f"⏱️ [스윙체크] {ticker.replace('KRW-','')} | BB 이탈 {pnl_pct:+.2f}% → 매도")
                                self._queue_sell(ticker, portion=1.0,
                                          reason=f"스윙 BB이탈", is_stoploss=True)
                                return
                            else:
                                next_dt = (datetime.datetime.now() + datetime.timedelta(minutes=60)).isoformat()
                                self.bought_coins[ticker]["swing_next_check"] = next_dt
                                logger.info(f"🔄 [스윙유지] {ticker.replace('KRW-','')} | BB 안 {pnl_pct:+.2f}% → 60분 후 재체크")
            except Exception:
                pass

            # ── ③ 본전보장 제거 (스윙 전략: 손절-2.5%로만 처리) ──

            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            # 🆕 P5: 노말 → 매집 복귀 (힘 빠지면 반대로!)
            # 미누님 통찰: "힘이 없으면 (거래량 죽으면) 반대로 되고"
            # 매집 출신만 적용 (acc_origin_buy_time 있는 코인)
            # 조건: BB수축 OR EMA5하향 OR 거래량죽음 OR 음봉연속
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            try:
                acc_origin_n = self.bought_coins[ticker].get("acc_origin_buy_time", "")
                if acc_origin_n and not info.get("normal_tp1_done", False):
                    df_n_check = safe_api_call(pyupbit.get_ohlcv, ticker, 
                                                interval=CONFIG["candle_interval_acc"], count=30)
                    if df_n_check is not None and len(df_n_check) >= 25:
                        close_n = df_n_check["close"]
                        open_n = df_n_check["open"]
                        vol_n = df_n_check["volume"]
                        ema5_n = close_n.ewm(span=5, adjust=False).mean()
                        bb_w_n = ((close_n.rolling(20).mean() + 2*close_n.rolling(20).std()) -
                                  (close_n.rolling(20).mean() - 2*close_n.rolling(20).std()))
                        bb_avg_n = bb_w_n.iloc[-10:].mean()
                        
                        bb_shrink_n = bb_w_n.iloc[-1] < bb_avg_n * 0.95
                        vol_low_n = vol_n.iloc[-1] < vol_n.iloc[-6:-1].mean() * 0.7
                        ema5_falling_n = ema5_n.iloc[-1] < ema5_n.iloc[-3]
                        recent_5_n = df_n_check.iloc[-5:]
                        bear_count_n = sum(1 for i in range(5) if recent_5_n["close"].iloc[i] < recent_5_n["open"].iloc[i])
                        many_bears_n = bear_count_n >= 3
                        
                        weak_signal_n = bb_shrink_n or vol_low_n or ema5_falling_n or many_bears_n
                        total_elapsed_n = (datetime.datetime.now() -
                                          datetime.datetime.fromisoformat(acc_origin_n)).total_seconds() / 3600
                        
                        # 노말 → 매집 복귀: 힘 빠지고 + 수익권 못 가고 + 8h 내
                        if weak_signal_n and pnl_pct < 1.5 and total_elapsed_n < 8.0:
                            self.bought_coins[ticker]["buy_type"] = "accumulation"
                            self.bought_coins[ticker]["buy_time"] = acc_origin_n
                            self._reset_surge_flags(ticker)
                            self._save_bot_bought()
                            
                            _signals_n = []
                            if bb_shrink_n: _signals_n.append("BB수축")
                            if vol_low_n: _signals_n.append("거래량↓")
                            if ema5_falling_n: _signals_n.append("EMA5↓")
                            if many_bears_n: _signals_n.append(f"음봉{bear_count_n}/5")
                            
                            logger.warning(
                                f"🏦 [노말→매집복귀] {ticker.replace('KRW-','')} | "
                                f"힘빠짐: {','.join(_signals_n)} | 경과 {total_elapsed_n:.1f}h | "
                                f"{pnl_pct:+.2f}%"
                            )
                            return
            except Exception:
                pass

            # ── ④ +7% 도달 → 🚀 surge로 자동 전환! (TP1보다 먼저 체크!) ──
            SURGE_UPGRADE_PCT = 7.0
            if pnl_pct >= SURGE_UPGRADE_PCT and not self.bought_coins[ticker].get("surge_upgraded", False):
                self.bought_coins[ticker]["buy_type"]       = "surge"
                self.bought_coins[ticker]["surge_upgraded"] = True
                # 🔧 P2: 서지 플래그 완전 초기화
                self._reset_surge_flags(ticker)
                self._save_bot_bought()
                logger.info(
                    f"🚀 [노말→서지 전환] {ticker.replace('KRW-','')} | "
                    f"+{pnl_pct:.2f}% 달성 → surge 전환 (플래그 초기화)"
                )
                send_telegram(
                    f"🚀 <b>노말→서지 전환!</b>\n"
                    f"코인: {ticker.replace('KRW-','')}\n"
                    f"수익률: {pnl_pct:+.2f}%\n"
                    f"이후 surge 전략 적용"
                )
                return

            # ── ⑤ +3% 도달 → 50% 익절 (1회) ──
            if pnl_pct >= 3.0 and not self.bought_coins[ticker].get("normal_tp1_done", False):
                self._queue_sell(ticker, portion=0.5, reason=f"노말 TP1 +3%")
                if ticker in self.bought_coins:
                    self.bought_coins[ticker]["normal_tp1_done"]  = True
                    self.bought_coins[ticker]["tp1_sell_price"]   = current  # 불타기 눌림 기준
                    self._save_bot_bought()
                    logger.info(f"✅ [📶노말] {ticker.replace('KRW-','')} +3% 50% 익절 완료 → 남은 50% 동적 바닥잠금")
                return

            # ── 🛡️ TP1 후 동적 바닥잠금 (P5: 거래속도 기반) ──
            # 미누님 세력 심리:
            #   급등 코인 (거래 활발) → ±2% 개미털기 흔들림
            #     → 바닥 +1.0%이면 -2% 흔들림에 털림!
            #     → 바닥 +0.3%로 낮춰서 개미털기 통과
            #   보통 코인 (거래 한산) → 천천히 내려옴 = 진짜 하락
            #     → 바닥 +1.0%로 최소 수익 확보
            if self.bought_coins[ticker].get("normal_tp1_done", False):
                # 1분봉 캐시로 거래 활발도 판단 (추가 API 호출 0)
                is_active_trading = False
                try:
                    ema_1m = get_cached_ema_1m(ticker)
                    if ema_1m and ema_1m.get("df") is not None:
                        df_1m = ema_1m["df"]
                        curr_1m = df_1m.iloc[-1]
                        avg_vol = df_1m["volume"].iloc[-6:-1].mean()
                        vol_ratio = curr_1m["volume"] / avg_vol if avg_vol > 0 else 1.0
                        # 거래 활발 = 거래량 평균 2배+ (양봉/음봉 무관)
                        if vol_ratio >= 2.0:
                            is_active_trading = True
                except Exception:
                    pass
                
                # 동적 바닥 결정
                if is_active_trading:
                    tp1_floor_pct = 0.3   # 급등코인 ±2% 흔들림 → 개미털기 방어
                    floor_tag = "⚡거래활발"
                else:
                    tp1_floor_pct = 1.0   # 천천히 하락 → 수익 확보
                    floor_tag = "🛡️거래한산"
                
                if pnl_pct <= tp1_floor_pct:
                    logger.warning(
                        f"🛡️ [📶노말] {ticker.replace('KRW-','')} | "
                        f"TP1후 {floor_tag} 바닥 +{tp1_floor_pct}% 이탈 → 잔량 익절!"
                    )
                    try:
                        send_telegram(
                            f"🛡️ <b>TP1후 동적 바닥잠금!</b>\n"
                            f"코인: {ticker.replace('KRW-','')}\n"
                            f"모드: {floor_tag}\n"
                            f"바닥: +{tp1_floor_pct}% | 현재: {pnl_pct:+.2f}%\n"
                            f"→ 잔량 익절"
                        )
                    except Exception:
                        pass
                    self._queue_sell(ticker, portion=1.0,
                              reason=f"TP1후 {floor_tag} +{tp1_floor_pct}%")
                    return
            # ── 노말 불타기 제거됨 ──
            # 이전: +5%에서 EMA5 눌림→반등봉 조건으로 10만원 추가
            # 제거 이유:
            #   1. 쿨다운 없음 → 같은 5분봉 동안 무한 반복 가능 (치명적 버그)
            #   2. 발동 윈도우가 +5~+7% 2% 사이뿐 (+7%에서 surge 전환됨)
            #   3. surge 전환 후 1/2/3차 불타기가 이미 동일 역할 수행
            #   → 중복 제거로 코드 간소화 + 버그 예방

            # ── ⑥ 트레일링 시작 + 바닥 잠금 (상승장 +5%/-2.5%/TP15% | 하락장 +3%/-2%/TP10%) ──
            TRAIL_TRIGGER = trail_trigger
            TRAIL_DROP    = trail_drop
            HARD_TP       = hard_tp

            trailing_active = self.bought_coins[ticker].get("trailing_active", False)
            trail_floor_pct = self.bought_coins[ticker].get("trail_floor_pct", None)  # 바닥 잠금 %

            if not trailing_active and pnl_pct >= TRAIL_TRIGGER:
                # 트레일링 시작 & 바닥 +3% 잠금
                self.bought_coins[ticker]["trailing_active"] = True
                self.bought_coins[ticker]["trail_floor_pct"] = TRAIL_TRIGGER  # +3% 바닥 잠금
                trailing_active = True
                trail_floor_pct = TRAIL_TRIGGER
                logger.info(
                    f"🎯 [📶노말] {ticker.replace('KRW-','')} 트레일링 시작! "
                    f"+{pnl_pct:.2f}% | 바닥 +{TRAIL_TRIGGER}% 잠금"
                )

            # ── ⑦ 트레일링 중 exit 조건들 ──
            if trailing_active:
                # ⓐ 하드 TP +10% → 전량 익절
                if pnl_pct >= HARD_TP:
                    logger.info(
                        f"💰 [📶노말] {ticker.replace('KRW-','')} 하드TP +{HARD_TP}% 달성 → 전량 익절!"
                    )
                    self._queue_sell(ticker, portion=1.0,
                              reason=f"하드TP +{HARD_TP}%")
                    return

                drop_from_high = ((current - highest) / highest) * 100
                floor_price    = buy_price * (1 + trail_floor_pct / 100)

                # ⓑ 현재가가 바닥 아래로 떨어짐 → 즉시 익절 (최소 +3% 보장 발동!)
                if current < floor_price:
                    logger.info(
                        f"🛡️ [📶노말] {ticker.replace('KRW-','')} 바닥 +{trail_floor_pct}% 이탈 → 즉시 익절! "
                        f"수익 {pnl_pct:+.2f}%"
                    )
                    self._queue_sell(ticker, portion=1.0,
                              reason=f"바닥잠금 익절 +{trail_floor_pct}%")
                    return

                # ⓒ 최고점 -2% 하락 (바닥 위에서) → 트레일링 익절
                if drop_from_high <= TRAIL_DROP:
                    logger.info(
                        f"📉 [📶노말] {ticker.replace('KRW-','')} 트레일링 익절! "
                        f"최고점 {drop_from_high:.2f}% | 수익 {pnl_pct:+.2f}%"
                    )
                    self._queue_sell(ticker, portion=1.0,
                              reason=f"트레일링 익절")
                    return

            # ── ⑧ EMA 기반 Exit (EMA100 3봉+BB 이탈) ──
            try:
                df_n = safe_api_call(
                    pyupbit.get_ohlcv, ticker,
                    interval=CONFIG["candle_interval_normal"], count=120
                )
                if df_n is not None and len(df_n) >= 110:
                    close_n  = df_n["close"]
                    ema100_n = close_n.ewm(span=100, adjust=False).mean()

                    # EMA100 아래 5봉 연속 → BB 안이면 유지, BB 밖이면 매도
                    bb_mid_n  = close_n.rolling(window=20).mean()
                    bb_std_n  = close_n.rolling(window=20).std()
                    bb_low_n  = bb_mid_n - 2 * bb_std_n
                    inside_bb_n = current >= bb_low_n.iloc[-1]

                    ema100_b1 = close_n.iloc[-1] < ema100_n.iloc[-1]
                    ema100_b2 = close_n.iloc[-2] < ema100_n.iloc[-2]
                    ema100_b3 = close_n.iloc[-3] < ema100_n.iloc[-3]
                    ema100_b4 = close_n.iloc[-4] < ema100_n.iloc[-4]
                    ema100_b5 = close_n.iloc[-5] < ema100_n.iloc[-5]
                    if ema100_b1 and ema100_b2 and ema100_b3 and ema100_b4 and ema100_b5:
                        if inside_bb_n:
                            logger.info(
                                f"🛡️ [📶노말] {ticker.replace('KRW-','')} | "
                                f"EMA100 5봉 이탈이지만 BB 안 → 유지"
                            )
                        else:
                            logger.warning(
                                f"📉 [📶노말] {ticker.replace('KRW-','')} EMA100 5봉+BB 이탈 → 전량 매도! "
                                f"({pnl_pct:+.2f}%)"
                            )
                            self._queue_sell(ticker, portion=1.0,
                                      reason=f"EMA100 3봉+BB이탈", is_stoploss=pnl_pct < 0)
                            return

            except Exception:
                # 폴백: 트레일링 활성 시 -2% 컷
                if trailing_active:
                    drop = ((current - highest) / highest) * 100
                    if drop <= TRAIL_DROP:
                        self._queue_sell(ticker, portion=1.0,
                                  reason=f"트레일링 익절(폴백) ({drop:.2f}%)")
                        return

    def try_buy_if_signal(self, ticker, buy_type="normal"):
        if ticker in self.bought_coins:
            return
        if self.is_protected(ticker):
            return
        if self.is_rebuy_blocked(ticker, buy_type=buy_type):
            return
        if len(self.bought_coins) >= CONFIG["max_coins"]:
            # 🔥 홈런/active_watch/surge는 약한 포지션 즉시 정리 후 진입!
            # EDGE +22% 놓친 교훈: 슬롯 꽉 차서 홈런 못 잡음
            if buy_type in ("homerun", "active_watch", "surge"):
                worst_ticker = None
                worst_pnl = 0
                for bt, binfo in self.bought_coins.items():
                    bp = binfo.get("buy_price", 0)
                    if bp <= 0: continue
                    cp = get_current_price_safe(bt)
                    if not cp: continue
                    _pnl = (cp - bp) / bp * 100
                    if _pnl < worst_pnl:
                        worst_pnl = _pnl
                        worst_ticker = bt
                
                if worst_ticker and worst_pnl < -1.0:
                    logger.warning(
                        f"🔄 [슬롯정리] {worst_ticker.replace('KRW-','')} "
                        f"({worst_pnl:+.2f}%) 즉시 정리 → "
                        f"{ticker.replace('KRW-','')} [{buy_type}] 진입!"
                    )
                    try:
                        bal = self.upbit.get_balance(worst_ticker.replace("KRW-",""))
                        if bal and float(bal) > 0:
                            safe_api_call(pyupbit.Upbit.sell_market_order,
                                self.upbit, worst_ticker, float(bal))
                            self._record_trade("SELL", worst_ticker,
                                get_current_price_safe(worst_ticker) or 0,
                                portion=1.0,
                                reason=f"슬롯정리→{ticker.replace('KRW-','')} ({worst_pnl:+.2f}%)")
                            self.bought_coins.pop(worst_ticker, None)
                            self._save_bot_bought()
                    except Exception as e:
                        logger.error(f"슬롯정리 오류: {e}")
                        return
                else:
                    return
            else:
                return
        
        # 🛡️ 매수 품질 필터 (세력 털기 구간 진입 방지)
        # 실전 데이터: 0~10분 초단기 매도 140건 = -82.6% 손실
        # → 고점 매수 / 거래량 급감 / BTC 급락 시 차단
        q_ok, q_reason = check_buy_quality(ticker, buy_type)
        if not q_ok:
            logger.info(
                f"🛡️ [품질필터] {ticker.replace('KRW-','')} [{buy_type}] "
                f"→ 매수 차단: {q_reason}"
            )
            return

        # 🔥 공격모드: 전일대비 TOP 25 코인만 매수
        # "주간 강세장에는 전일대비 상위 25개에서만 거래" - 미누님
        # homerun은 예외 (어디서든 잡음)
        try:
            if get_time_mode() == "attack" and buy_type not in ("homerun",):
                if trader.top25_tickers and ticker not in trader.top25_tickers:
                    return  # TOP25 밖 = 감시만
        except Exception:
            pass

        # 🎯 전략별 슬롯 쿼터 체크 (서지 기회 확보)
        # 💡 출신성분(original_type) 기준으로 카운트!
        # → 매집→서지 전환돼도 여전히 매집 슬롯 차지
        # → 서지/V자 "진입 경로" 슬롯 항상 보존
        if buy_type != "surge":
            slot_limits = get_dynamic_slots()
            limit = slot_limits.get(buy_type, 999)
            # 출신성분(original_type)이 해당 타입인 코인 수
            current_count = sum(
                1 for bc in self.bought_coins.values()
                if bc.get("original_type", bc.get("buy_type")) == buy_type
            )
            if current_count >= limit:
                logger.debug(
                    f"🚫 [{buy_type} 쿼터] {ticker.replace('KRW-','')} "
                    f"{current_count}/{limit} 초과 → 매수 차단 (출신성분 기준)"
                )
                return

        # ── ETH 상태 체크 ──
        eth_status = get_eth_status()

        # 🚀 surge → 하락장(BTC 전일대비↓)이면 기본 금지
        # 🔧 예외: "독립 상승 알트" (BLUR 같은 홈런 코인)은 허용
        # ⚾ 공격 모드: 임계값 완화 - 스트라이크아웃 되더라도 홈런 잡기 우선
        if buy_type == "surge" and eth_status == "bearish":
            # ── 🌟 독립 상승 알트 예외 검사 (공격 모드) ──
            try:
                is_independent = False
                reject_reason = ""

                # 조건 1: 전일대비 +7% 이상 (완화: 15% → 7%)
                daily_change = get_cached_daily_change(ticker)
                if daily_change < 7.0:
                    reject_reason = f"전일대비 {daily_change:.1f}% < 7%"
                else:
                    # 조건 2: 1분봉 거래량 2.5배 이상 (완화: 5배 → 2.5배)
                    df_ind = safe_api_call(
                        pyupbit.get_ohlcv, ticker,
                        interval=CONFIG["candle_interval"], count=30
                    )
                    if df_ind is None or len(df_ind) < 25:
                        reject_reason = "1분봉 데이터 부족"
                    else:
                        c_ind = df_ind["close"]
                        v_ind = df_ind["volume"]
                        o_ind = df_ind["open"]

                        avg_vol_ind = v_ind.iloc[-21:-1].mean()
                        vol_ratio_ind = v_ind.iloc[-1] / avg_vol_ind if avg_vol_ind > 0 else 0
                        
                        if vol_ratio_ind < 2.5:
                            reject_reason = f"1분봉 거래량 {vol_ratio_ind:.1f}배 < 2.5배"
                        else:
                            # 조건 3: 일봉 EMA20 위 (기본 품질 필터 - 유지)
                            df_daily_ind = safe_api_call(
                                pyupbit.get_ohlcv, ticker,
                                interval="day", count=30
                            )
                            if df_daily_ind is None or len(df_daily_ind) < 22:
                                reject_reason = "일봉 데이터 부족"
                            else:
                                daily_close = df_daily_ind["close"].iloc[-1]
                                daily_ema20 = df_daily_ind["close"].ewm(span=20, adjust=False).mean().iloc[-1]
                                if daily_close <= daily_ema20:
                                    reject_reason = "일봉 EMA20 아래"
                                else:
                                    # 조건 4: 1분봉 RSI < 85 (완화: 80 → 85)
                                    delta_ind = c_ind.diff()
                                    gain_ind = delta_ind.clip(lower=0).rolling(14).mean()
                                    loss_ind = (-delta_ind.clip(upper=0)).rolling(14).mean()
                                    rs_ind = gain_ind / loss_ind
                                    rsi_ind = (100 - (100 / (1 + rs_ind))).iloc[-1]
                                    
                                    if rsi_ind >= 85:
                                        reject_reason = f"RSI {rsi_ind:.0f} ≥ 85 (과매수)"
                                    else:
                                        # 조건 5: 1분봉 EMA5 > EMA20 (상승 추세 - 유지)
                                        ema5_ind  = c_ind.ewm(span=5,  adjust=False).mean().iloc[-1]
                                        ema20_ind = c_ind.ewm(span=20, adjust=False).mean().iloc[-1]
                                        if ema5_ind <= ema20_ind:
                                            reject_reason = "EMA5 ≤ EMA20 (상승 추세 아님)"
                                        else:
                                            # 모든 조건 통과 → 독립 상승 알트로 인정!
                                            is_independent = True
                                            logger.warning(
                                                f"🌟 [독립상승알트] {ticker.replace('KRW-','')} | "
                                                f"전일대비 +{daily_change:.1f}% | "
                                                f"1분봉 거래량 {vol_ratio_ind:.1f}배 | "
                                                f"RSI {rsi_ind:.0f} | EMA5>EMA20 "
                                                f"→ 하락장 예외 허용! (공격 모드)"
                                            )
                                            send_telegram(
                                                f"🌟 <b>독립 상승 알트 감지!</b>\n"
                                                f"코인: {ticker.replace('KRW-','')}\n"
                                                f"전일대비: +{daily_change:.1f}%\n"
                                                f"거래량: {vol_ratio_ind:.1f}배\n"
                                                f"RSI: {rsi_ind:.0f}\n"
                                                f"→ 하락장 예외 매수 (공격 모드)"
                                            )

                if not is_independent:
                    logger.debug(f"🚫 [하락장surge차단] {ticker.replace('KRW-','')} {reject_reason}")
                    return
            except Exception as _e:
                logger.debug(f"독립상승알트 체크 오류 {ticker}: {_e}")
                return

        # 🚀 실시간 전일대비 체크 → surge 승격!
        if buy_type == "normal":
            change = get_cached_daily_change(ticker)
            if change >= CONFIG["normal_to_surge_pct"]:
                logger.info(f"🚀 {ticker.replace('KRW-','')} 전일대비 +{change:.1f}% → surge 승격!")
                buy_type = "surge"
                self.ticker_types[ticker] = "surge"

        # 🎯 시장 상태 기반 매수 금지는 아래 matrix에서 일괄 처리됨
        # (strategy_scale == 0 이면 자동 차단)

        # 🔥 wakeup: 이미 wakeup_thread에서 6개 조건 검증 완료
        # → 추가 신호 체크 생략하고 바로 매수로 진행
        # (세력 작전 2단계 포착한 귀한 기회라 바로 진입)
        if buy_type == "wakeup":
            signal = "buy"
            info = {"wakeup": True}
        elif buy_type == "active_watch":
            # ⚡ 1분봉 공격 감시: active_watch_thread에서 검증 완료
            # → 바로 매수 (실시간 1분봉 세력 움직임 포착)
            signal = "buy"
            info = {"active_watch": True}
        elif buy_type == "whale_hunt":
            # 🐋 whale_hunt: 이미 whale_hunt_thread에서 5개 조건 검증됨
            # → 바로 매수 (세력 1단계 매집 포착)
            signal = "buy"
            info = {"whale_hunt": True}
        elif buy_type == "homerun":
            # 🌟 homerun: 대형 홈런 코인 (BLUR 스타일)
            # → 시장 무관 즉시 매수 (하락장/폭락장 예외)
            # → surge로 처리되어 강한 매수 로직 적용
            signal = "buy"
            info = {"homerun": True}
            buy_type = "surge"  # surge 처리 로직 활용 (공격적 매수/매도)
        else:
            signal, info = get_signal(ticker) if buy_type == "surge" else get_normal_signal(ticker)

        # ── 🏦 노말 신호 없으면 세력 매집 감지 체크 ──
        if signal not in ("buy", "strong_buy") and buy_type == "normal":
            if eth_status == "bearish":
                # 하락장(BTC 전일대비↓)이어도 개별 코인 EMA100 수평/상승 시 매집 허용
                # → 세력이 개미 고시기하면서 매집하는 구간
                try:
                    df_slope = safe_api_call(pyupbit.get_ohlcv, ticker,
                                             interval=CONFIG["candle_interval"], count=115)
                    if df_slope is not None and len(df_slope) >= 110:
                        e100_s = df_slope["close"].ewm(span=100, adjust=False).mean()
                        slope  = (e100_s.iloc[-1] - e100_s.iloc[-10]) / e100_s.iloc[-10] * 100
                        if slope >= -0.1:  # EMA100 수평(-0.1%↑) or 상승 → 매집 허용
                            acc_signal, acc_info = get_accumulation_signal(ticker)
                            if acc_signal == "accumulation":
                                signal = "accumulation"
                                info   = acc_info
                                logger.info(
                                    f"🏦 [하락장매집] {ticker.replace('KRW-','')} | "
                                    f"BTC↓ 하락장이지만 EMA100기울기 {slope:+.3f}% → 매집 허용"
                                )
                        else:
                            logger.debug(
                                f"🚫 [매집금지] {ticker.replace('KRW-','')} | "
                                f"BTC↓ + EMA100 하향({slope:+.3f}%) → 진입 금지"
                            )
                except Exception:
                    pass
            else:
                acc_signal, acc_info = get_accumulation_signal(ticker)
                if acc_signal == "accumulation":
                    signal = "accumulation"
                    info   = acc_info

        if signal in ("buy", "strong_buy", "accumulation"):
            available = self.get_balance("KRW")

            # 🔥 surge 체결강도 체크 (상승장일때만 → 하락장이면 애초에 매수 안함)
            if buy_type == "surge":
                strength = get_trade_strength(ticker)
                if strength < 100.0:
                    logger.info(
                        f"⚠️ {ticker.replace('KRW-','')} 체결강도 {strength}% → 100% 미달 매수 보류"
                    )
                    return

            # 🎯 [신규] 시장 상태 5단계 기반 투자금 차등 (미누님 전면 전략)
            # 매수 타입(buy_type)이 signal보다 우선
            _scale = 0.1 if CONFIG.get("test_mode", False) else 1.0
            
            # 🌟 homerun은 시장필터 완전 우회 (BLUR 같은 대형 홈런)
            is_homerun = info.get("homerun", False)
            
            # 전략 키 결정 (accumulation signal도 포함)
            strat_key = "accumulation" if signal == "accumulation" else buy_type
            
            if is_homerun:
                # 시장 무관 전액 매수 (대형 홈런)
                market_scale = 1.0
                market_state, market_details = get_market_state()
            else:
                # 시장 상태 기반 배율 획득
                market_scale, market_state, market_details = get_strategy_scale(strat_key)
                
                # 매수 금지 체크 (배율 0) - homerun은 우회
                if market_scale <= 0:
                    logger.info(
                        f"🚫 [시장필터] {ticker.replace('KRW-','')} | "
                        f"{strat_key} | 시장: {market_state} | "
                        f"BTC {market_details.get('daily_change',0):+.1f}% "
                        f"EMA {market_details.get('ema_slope',0):+.2f}% "
                        f"RSI {market_details.get('rsi',0):.0f} → 매수 차단"
                    )
                    return
            
            # 전략별 기본 투자금 (전액 기준)
            if strat_key == "surge":
                base_invest = CONFIG["max_per_coin_krw"]  # 30만 / test 3만
            elif strat_key == "accumulation":
                base_invest = int(150_000 * _scale)
            elif strat_key == "whale_hunt":
                base_invest = int(200_000 * _scale)       # 미누님 철학: 매집 최우선
            elif strat_key == "wakeup":
                base_invest = int(180_000 * _scale)       # 세력 2단계, 매집 다음
            elif strat_key == "active_watch":
                base_invest = int(200_000 * _scale)       # ⚡ 1분봉 공격 (공격적)
            elif strat_key == "v_reversal":
                base_invest = int(150_000 * _scale)
            else:  # normal
                base_invest = int(200_000 * _scale)
            
            # 시장 상태 배율 적용
            invest_limit = int(base_invest * market_scale)
            
            # 🕐 시간대 × BTC 방향 교차 배율 (미누님 전략)
            # 공격모드+상방 = 1.2x 풀공격 / 매집모드+하방 = 0.3x 최소
            # ⭐ homerun은 시간대 무관 (BLUR 같은 홈런은 언제든 잡음)
            try:
                _tmode = get_time_mode()
                if is_homerun:
                    _time_mult = 1.0  # 홈런은 항상 전액
                else:
                    _time_mult = TIME_MARKET_MULTIPLIER.get(_tmode, {}).get(market_state, 0.5)
                invest_limit = int(invest_limit * _time_mult)
                
                # 시간대×BTC 배율로 0이 되면 매수 차단
                if invest_limit < CONFIG["min_trade_krw"] and _time_mult <= 0:
                    logger.info(
                        f"🚫 [시간대필터] {ticker.replace('KRW-','')} | "
                        f"{TIME_MODE_CONFIG[_tmode]['label']}+{market_state} | "
                        f"배율 {_time_mult} → 매수 중단"
                    )
                    return
            except Exception:
                _tmode = "normal"
                _time_mult = 1.0
            
            # 시장 상태 이모지
            eth_tag = {
                "strong_bull": "🟢🟢강상승",
                "bullish":     "🟢상승장",
                "neutral":     "⚪횡보",
                "bearish":     "🔴하락장",
                "strong_bear": "🔴🔴폭락",
            }.get(market_state, "❓")
            
            # 로그용 (시간대 모드 + 시장 상태)
            _tmode_label = TIME_MODE_CONFIG.get(_tmode, {}).get("label", "❓")
            market_str = (
                f"{eth_tag} {_tmode_label} "
                f"BTC{market_details.get('daily_change',0):+.1f}% "
                f"x{_time_mult}"
            )
            
            # 기존 eth_status 호환성 유지 (아래 레거시 코드를 위해)
            eth_status = "bullish" if market_state in ("strong_bull", "bullish", "neutral") else "bearish"

            invest = min(available * 0.95, invest_limit)
            if invest >= CONFIG["min_trade_krw"]:
                if signal == "accumulation":
                    # 🎯 매집 쿼터 재체크 (출신성분 기준)
                    acc_limit = get_dynamic_slots().get("accumulation", 999)
                    acc_count = sum(
                        1 for bc in self.bought_coins.values()
                        if bc.get("original_type", bc.get("buy_type")) == "accumulation"
                    )
                    if acc_count >= acc_limit:
                        logger.info(
                            f"🚫 [매집 쿼터] {ticker.replace('KRW-','')} "
                            f"{acc_count}/{acc_limit} 초과 → 매집 매수 차단 (출신성분 기준)"
                        )
                        return
                    
                    # None-safe 포맷
                    cci_v = info.get('cci')
                    cci_s = f"{cci_v:.1f}" if cci_v is not None else "N/A"
                    vq_v  = info.get('vol_quiet')
                    ne_v  = info.get('near_ema100')
                    
                    logger.info(
                        f"🏦 [봇3] 세력매집 감지! {ticker.replace('KRW-','')} | "
                        f"CCI: {cci_s} | "
                        f"BB수축 | 거래량: {vq_v}배 | "
                        f"EMA100이격: {ne_v}% | {invest:,.0f}원"
                    )
                    send_telegram(
                        f"🏦 <b>세력 매집 감지!</b>\n"
                        f"코인: {ticker.replace('KRW-','')}\n"
                        f"CCI: {cci_s}\n"
                        f"거래량: {vq_v}배 (낮음)\n"
                        f"→ {invest:,.0f}원 매집 진입"
                    )
                    self.buy(ticker, invest, buy_type="accumulation")
                else:
                    tag = "🔥 강한" if signal == "strong_buy" else "📶 일반"
                    type_str = "🚀surge" if buy_type == "surge" else "📶감시"
                    # 🐛 FIX: whale_hunt/wakeup은 info에 cci/vol_ratio가 없을 수 있음
                    # → None일 경우 'N/A' 출력으로 포맷 에러 방지
                    cci_val = info.get('cci')
                    cci_str = f"{cci_val:.1f}" if cci_val is not None else "N/A"
                    vol_val = info.get('vol_ratio')
                    vol_str = f"{vol_val}" if vol_val is not None else "N/A"
                    logger.info(
                        f"🎯 [봇3] {tag} 신호! [{type_str}] {ticker} | "
                        f"CCI: {cci_str} | "
                        f"거래량: {vol_str}배 | "
                        f"이격도: {info.get('ema5_gap_pct', 'N/A')}% | "
                        f"{eth_tag} {invest:,.0f}원"
                    )
                    self.buy(ticker, invest, buy_type=buy_type)
                time.sleep(1)


# ============================================================
# 🔄 메인 루프
# ============================================================

def run_bot():
    logger.info("=" * 62)
    logger.info("🚀 [봇3] EMA+BB+CCI+MACD 자동매매 봇 v7 시작!")
    logger.info("=" * 62)
    logger.info("  📶 normal: 본전보장+1% | 트레일링+3%시작/바닥+3%잠금/-2%익절 | 하드TP+10% | EMA5/20데드→전량 | EMA100이탈→전량")
    logger.info("  🚀 surge:  상승장(BTC 전일대비↑)만 매수! 트레일-2%→50% | +20% OR EMA5/20데드 → 전량 | 불타기(+5%이상)")
    logger.info("  💰 투자금: 🚀surge 상승장 30만 | 📶노말 상승장 20만/하락장 10만")
    logger.info("  🚫 surge 하락장(BTC 전일대비↓) → 매수 완전 금지!")
    logger.info(f"  🚀 전일대비 {CONFIG['normal_to_surge_pct']}% 이상 → 자동 surge 타입!")
    logger.info(f"  🛡️ 본전보장: 📶 normal만 적용 (+{CONFIG['breakeven_trigger_pct']}% → 활성)")
    logger.info(f"  🚨 실시간: 전체코인 {CONFIG['realtime_surge_mult']}배 거래량 즉시 포착 (별도 스레드)")
    logger.info("  🔍 5분 재선별 / 노말+surge 동시 탐색")
    logger.info("=" * 62)

    trader = Bot3Trader()
    krw = trader.get_balance("KRW")
    logger.info(f"💵 현재 KRW 잔고: {krw:,.0f}원")

    result = select_top_coins(trader.protected_coins)
    trader.candidate_coins   = result[0]
    trader.ticker_types      = result[1]
    trader.last_select_time  = datetime.datetime.now()
    trader.last_replace_time = datetime.datetime.now()

    import threading
    import queue

    # ── 큐 생성 (감지/실행 완전 분리) ──
    # ⚡ 매도: 우선순위 큐 (손실 큰 것/손절 우선 처리)
    # 튜플 형식: (priority, timestamp, ticker, portion, reason, is_stoploss)
    sell_queue = queue.PriorityQueue()
    # ⚡ 매수: 우선순위 큐 (서지/V자 우선, 홈런 코인 놓치지 않기)
    # 튜플 형식: (priority, timestamp, ticker, buy_type)
    # priority: 1=surge(최우선) / 2=v_reversal / 3=normal / 4=accumulation
    # 🔧 미누님 요청: 30 → 50 (감지 39종 처리하기 위해 큐 여유 확보)
    buy_queue  = queue.PriorityQueue(maxsize=50)
    # trade_lock은 trader.trade_lock (RLock)으로 통합됨
    
    # ⚡ Bot3Trader에 매도 큐 주입 (check_position이 _queue_sell 사용 가능)
    trader.sell_queue = sell_queue
    
    # 🎯 슬롯 쿼터 초기 정리 (봇 시작 시 1회)
    # 매집/노말/V자가 슬롯 독점해서 서지(BLUR) 못 잡는 문제 해결
    # -5% 이하 손실, +3% 이상 수익은 보호 (중간 구간만 청산)
    trader._rebalance_to_slot_quota()
    
    # 🎯 buy_type별 우선순위 (낮을수록 먼저 처리)
    BUY_PRIORITY = {
        "surge":        1,   # 🚀 최우선 (응급 급등)
        "homerun":      1,   # 🌟 대형 홈런 (시장무관)
        "whale_hunt":   2,   # 🐋 세력 매집찾기 (1단계)
        "active_watch": 3,   # ⚡ 1분봉 공격 감시 (NEW)
        "wakeup":       4,   # 🔥 호가깨어남 (세력 2단계)
        "v_reversal":   5,   # 📈 V자 반등
        "normal":       6,   # 📶 노말
        "accumulation": 7,   # 🏦 매집 (기존)
    }

    def _safe_put_buy(ticker, buy_type):
        """중복 ticker 방지 + 우선순위 큐 + 과부하 시 낮은 우선순위 드롭
        
        🚫 큐 적재 전 사전 필터 (큐 과부하 방지):
          - 이미 보유 중 → 스킵
          - 재매수 금지 중 → 스킵
          - 보호 중 → 스킵
          - 슬롯 꽉 참 → 스킵 (surge/homerun 제외)
        
        실전 데이터: 재매수 금지만 7,566건 → 큐 과부하 원인
        → 큐에 올리기 전에 걸러야 함!
        """
        try:
            # ── 사전 필터 (큐 적재 전 차단) ──
            if ticker in trader.bought_coins:
                return  # 이미 보유
            if trader.is_protected(ticker):
                return  # 보호 중
            if trader.is_rebuy_blocked(ticker, buy_type=buy_type):
                return  # 재매수 금지
            if buy_type not in ("surge", "homerun"):
                if len(trader.bought_coins) >= CONFIG["max_coins"]:
                    return  # 슬롯 꽉 참
            
            # 🏠 퇴근정리 모드: 신규 매수 차단 (homerun만 예외)
            # "7시 이후에는 낮에 잡았던 공격적 코인들을 정리하는 시간" - 미누님
            if get_time_mode() == "closing" and buy_type not in ("homerun",):
                return  # 퇴근정리 = 매수 안 함
            
            # 🌙 P5: 야간(20시~08:30) wakeup 차단
            # 20시 이후 분석: wakeup 7건 전패, -1,030원
            # "야간은 거래량 적어서 호가깨어남 가짜 신호 많음"
            _tm_night = get_time_mode()
            if _tm_night in ("normal", "accumulate") and buy_type == "wakeup":
                return  # 야간 wakeup 차단
            
            # 🔥 전략별 슬롯 쿼터 사전 체크 (매집 과부하 방지)
            # 공격모드 매집슬롯=0 → 매집코인 큐 적재 자체를 차단!
            if buy_type not in ("surge", "homerun"):
                _slots = get_dynamic_slots()
                _limit = _slots.get(buy_type, 999)
                _count = sum(1 for bc in trader.bought_coins.values()
                    if bc.get("original_type", bc.get("buy_type")) == buy_type)
                if _count >= _limit:
                    return  # 슬롯 쿼터 초과
            
            # 🔥 공격모드: TOP25 밖이면 큐 적재도 안 함
            if get_time_mode() == "attack" and buy_type not in ("homerun",):
                if trader.top25_tickers and ticker not in trader.top25_tickers:
                    return
            
            # 🛡️ 호가창 체크 (캐시 사용, CPU 최적화)
            if buy_type not in ("homerun", "surge"):
                try:
                    bid_ratio = get_cached_bid_ratio(ticker)
                    if bid_ratio < 0.7:
                        return  # 매수벽 부족
                except Exception:
                    pass
            
            # 중복 체크 (우선순위 큐 내부 리스트 스캔)
            existing = list(buy_queue.queue)
            if any(q[2] == ticker for q in existing):
                return  # 이미 큐에 있으면 무시
            
            priority = BUY_PRIORITY.get(buy_type, 5)
            ts       = time.time()
            item     = (priority, ts, ticker, buy_type)
            
            try:
                buy_queue.put_nowait(item)
            except queue.Full:
                # 큐 꽉 참 → 기존 낮은 우선순위 항목 드롭 시도
                # 새 아이템이 서지/V자면 기존 매집/노말 하나 밀어냄
                if priority <= 2:  # 새 아이템이 surge or v_reversal
                    with buy_queue.mutex:
                        # 큐 내부에서 우선순위 가장 낮은 것 찾기
                        items = list(buy_queue.queue)
                        if items:
                            worst_idx = max(range(len(items)), key=lambda i: items[i][0])
                            if items[worst_idx][0] > priority:
                                # 낮은 우선순위 제거 후 새 아이템 삽입
                                buy_queue.queue.remove(items[worst_idx])
                                buy_queue.queue.append(item)
                                # heapify 필요
                                import heapq
                                heapq.heapify(buy_queue.queue)
                                logger.info(
                                    f"🔄 [큐재배치] {ticker.replace('KRW-','')}({buy_type}) "
                                    f"← {items[worst_idx][2].replace('KRW-','')} 밀어냄"
                                )
        except Exception as e:
            logger.debug(f"_safe_put_buy 오류: {e}")

    # ── ① 매도 실행 전용 스레드 (병렬 3개 워커) ──
    # 포지션 스레드가 큐에 넣으면 워커들이 병렬로 처리
    # → 폭락 상황에서 여러 코인 동시 매도 가능
    def sell_exec_thread_func(worker_id=0):
        while True:
            try:
                item = sell_queue.get(timeout=0.3)
                # PriorityQueue 튜플: (priority, ts, ticker, portion, reason, is_stoploss)
                priority, ts, ticker, portion, reason, is_stoploss = item
                if ticker in trader.bought_coins:
                    logger.info(
                        f"🔥 [매도워커#{worker_id}] {ticker.replace('KRW-','')} "
                        f"처리 시작 (priority: {priority:+.2f})"
                    )
                    # 워커가 직접 sell() 호출 (trade_lock은 sell() 내부에서 RLock)
                    trader.sell(ticker, portion=portion,
                                reason=reason, is_stoploss=is_stoploss)
                sell_queue.task_done()
            except queue.Empty:
                pass
            except Exception as e:
                logger.error(f"⚠️ 매도실행 오류 [worker#{worker_id}]: {e}")

    # ── ② 매수 실행 전용 스레드 (병렬 2개 워커) ──
    # 우선순위 큐에서 꺼내서 병렬 처리
    # → 서지/V자 우선 처리, 처리 속도 2배
    def buy_exec_thread_func(worker_id=0):
        while True:
            try:
                item = buy_queue.get(timeout=0.5)
                # PriorityQueue 튜플: (priority, ts, ticker, buy_type)
                priority, ts, ticker, buy_type = item
                if (ticker not in trader.bought_coins and
                        len(trader.bought_coins) < CONFIG["max_coins"] and
                        sell_queue.empty()):   # 매도 대기 없을 때만 매수!
                    # 서지 처리 시 로그 (모니터링용)
                    if priority <= 2:
                        logger.debug(
                            f"🛒 [매수워커#{worker_id}] {ticker.replace('KRW-','')} "
                            f"({buy_type}, priority={priority})"
                        )
                    # trader.trade_lock은 buy() 내부에서 RLock으로 획득
                    trader.try_buy_if_signal(ticker, buy_type=buy_type)
                buy_queue.task_done()
            except queue.Empty:
                pass
            except Exception as e:
                logger.error(f"⚠️ 매수실행 오류 [worker#{worker_id}]: {e}")

    # ── ③ 포지션 감지 스레드 (빠름 - 매도는 큐로 떠넘김) ──
    # ⚡ 이제 check_position이 직접 매도 안 함 → 전체 사이클 단축
    # 20코인 보유 시: 20 × 0.2 + 1 = 5초 주기
    def position_thread_func():
        while True:
            try:
                for ticker in list(trader.bought_coins.keys()):
                    trader.check_position(ticker)
                    time.sleep(0.5)   # ticker당 0.5초 (P5: 0.2→0.5 CPU경감, 매도큐는 별도)
            except Exception as e:
                logger.error(f"⚠️ 포지션 스레드 오류: {e}")
            time.sleep(1)   # 사이클 1초

    # ── ④ surge 탐색 스레드 (8초마다 - 듀얼 스캔) ──
    # P5 듀얼 스캔: priority(매 사이클) + 전체(3사이클마다 = 24초)
    # FF같은 잡코인 폭발도 24초 안에 포착!
    def surge_thread_func():
        cycle = 0
        while True:
            try:
                cycle += 1
                if len(trader.bought_coins) < CONFIG["max_coins"]:
                    # 매 사이클: priority만 (CPU 절약)
                    _priority = set(trader.candidate_coins) | set(trader.wide_tickers) | set(trader.top25_tickers)
                    surge_tickers = detect_realtime_surge(
                        trader.protected_coins,
                        set(trader.bought_coins.keys()),
                        set(trader.candidate_coins),
                        priority_tickers=_priority
                    )
                    for ticker in surge_tickers:
                        if ticker not in trader.bought_coins:
                            _safe_put_buy(ticker, "surge")
                    
                    # 🆕 3사이클마다 (24초): 전체 코인 스캔 (FF 같은 잡코인 폭발 포착)
                    if cycle % 3 == 0:
                        try:
                            _all_tickers = set(get_cached_tickers())
                            # priority에 없던 코인만 추가 스캔 (중복 제거)
                            _extra = _all_tickers - _priority
                            if _extra:
                                logger.debug(f"🌐 [듀얼스캔] 전체 코인 surge 체크: {len(_extra)}개")
                                surge_extra = detect_realtime_surge(
                                    trader.protected_coins,
                                    set(trader.bought_coins.keys()),
                                    set(trader.candidate_coins),
                                    priority_tickers=_extra
                                )
                                for ticker in surge_extra:
                                    if ticker not in trader.bought_coins:
                                        logger.warning(f"🌟 [듀얼스캔 발견] {ticker.replace('KRW-','')} - 잡코인 폭발 포착!")
                                        _safe_put_buy(ticker, "surge")
                        except Exception as e:
                            logger.debug(f"듀얼스캔 오류: {e}")
            except Exception as e:
                logger.error(f"⚠️ surge 스레드 오류: {e}")
            time.sleep(8)   # P5: 5초→8초 (스캔 대상 줄어서 여유)

    # ── ⑤ 노말 탐색 스레드 (5초마다 - 큐에만 넣음) ──
    # 🔧 P4: candidate_coins(20개) + 거래대금 상위 50개 (3사이클마다 = 15초)
    # ── ⑤+⑥ 노말+매집 통합 탐색 스레드 (P5: 2개→1개) ──
    # 기존: normal(5초) + accumulation(15초) 각각 candidate + wide 순회
    # 통합: 1회 순회로 두 전략 동시 체크, wide는 3사이클마다
    def scan_thread_func():
        """노말+매집 통합 탐색
        - 매 사이클(5초): candidate_coins → normal 체크
        - 3사이클마다(15초): candidate_coins → accumulation 체크
        - 3사이클마다(15초): wide_tickers → normal 확장
        - 6사이클마다(30초): wide_tickers → accumulation 확장
        """
        cycle = 0
        while True:
            try:
                cycle += 1
                if len(trader.bought_coins) < CONFIG["max_coins"]:
                    do_acc = (cycle % 3 == 0)  # 15초마다 매집 체크
                    
                    # ── Phase 1: candidate_coins 기본 스캔 ──
                    for ticker in list(trader.candidate_coins):
                        if ticker in trader.bought_coins:
                            continue
                        
                        # Normal 체크 (매 사이클)
                        buy_type = trader.ticker_types.get(ticker, "normal")
                        if buy_type != "surge":
                            _safe_put_buy(ticker, "normal")
                        
                        # Accumulation 체크 (3사이클마다)
                        if do_acc:
                            try:
                                acc_signal, _ = get_accumulation_signal(ticker)
                                if acc_signal == "accumulation":
                                    _safe_put_buy(ticker, "normal")
                            except Exception:
                                pass
                        
                        time.sleep(0.1)
                    
                    # ── Phase 2: wide_tickers 확장 스캔 ──
                    if cycle % 3 == 0 and trader.wide_tickers:
                        candidate_set = set(trader.candidate_coins) | set(trader.bought_coins.keys())
                        wide_scan = [t for t in trader.wide_tickers if t not in candidate_set]
                        
                        do_acc_wide = (cycle % 6 == 0)  # 30초마다 매집 확장
                        
                        for ticker in wide_scan:
                            # Normal 확장
                            _safe_put_buy(ticker, "normal")
                            
                            # Accumulation 확장 (6사이클마다)
                            if do_acc_wide:
                                try:
                                    acc_signal, _ = get_accumulation_signal(ticker)
                                    if acc_signal == "accumulation":
                                        logger.info(f"🏦 [매집확장발견] {ticker.replace('KRW-','')}")
                                        _safe_put_buy(ticker, "normal")
                                except Exception:
                                    pass
                            
                            time.sleep(0.15)  # 0.1+0.3 평균
                        
                        if wide_scan:
                            logger.debug(f"📶 [통합스캔] {len(wide_scan)}개 확장 완료{' (+매집)' if do_acc_wide else ''}")
            except Exception as e:
                logger.error(f"⚠️ 통합탐색 스레드 오류: {e}")
            time.sleep(5)

    # ── ⑦ V자 반등 통합 스레드 (P5: 2개→1개 통합) ──
    # 기존 v_reversal(8초) + v_reversal_wide(30초) → 1개 스레드
    # 기본 루프 10초, 4사이클(40초)마다 wide 확장 스캔
    def v_reversal_combined_thread_func():
        """V자반등 통합: candidate + wide 한 스레드에서 처리
        - 매 사이클: candidate_coins + 보유코인 V자 탐색
        - 4사이클마다: wide_tickers 갱신 + 확장 V자 탐색
        """
        cycle = 0
        wide_tickers_local = []
        last_refresh = datetime.datetime.now() - datetime.timedelta(minutes=10)
        
        while True:
            try:
                cycle += 1
                if len(trader.bought_coins) < CONFIG["max_coins"]:
                    # ── Phase 1: candidate + 보유코인 V자 (매 사이클) ──
                    all_tickers = list(trader.candidate_coins) + list(trader.bought_coins.keys())
                    for ticker in all_tickers:
                        try:
                            v_signal, v_info = get_v_reversal_signal(ticker)
                            if v_signal == "v_reversal":
                                if ticker in trader.bought_coins:
                                    existing = trader.bought_coins[ticker]
                                    v_add_done = existing.get("v_add_done", False)
                                    if (not v_add_done and
                                            _get_current_price(ticker, existing) > existing.get("buy_price", 0)):
                                        avail = trader.get_balance("KRW")
                                        add_amt = min(avail * 0.95, int(150_000 * (0.1 if CONFIG.get("test_mode") else 1.0)))
                                        if add_amt >= CONFIG["min_trade_krw"]:
                                            logger.info(
                                                f"📈 [V자불타기] {ticker.replace('KRW-','')} | "
                                                f"EMA100이격: {v_info['gap_pct']:.1f}% | "
                                                f"양봉/음봉: {v_info['ratio']:.1f}배"
                                            )
                                            trader.buy(ticker, add_amt, buy_type="v_reversal")
                                            if ticker in trader.bought_coins:
                                                trader.bought_coins[ticker]["v_add_done"] = True
                                                trader._save_bot_bought()
                                else:
                                    logger.info(
                                        f"📈 [V자반등] {ticker.replace('KRW-','')} | "
                                        f"EMA100이격: {v_info['gap_pct']:.1f}% | "
                                        f"기울기: {v_info.get('ema100_slope',0):+.3f}% | "
                                        f"CCI: {v_info['cci_low']:.0f}→{v_info['cci_curr']:.0f} | "
                                        f"RSI: {v_info['rsi_low']:.1f}→{v_info['rsi_curr']:.1f} | "
                                        f"거래량: {v_info.get('vol_ratio',0):.1f}배 | "
                                        f"BB이탈: {v_info.get('bb_break_pct',0):.1f}% | "
                                        f"양/음비: {v_info['ratio']:.1f}배"
                                    )
                                    _safe_put_buy(ticker, "v_reversal")
                            time.sleep(0.4)
                        except Exception:
                            pass
                    
                    # ── Phase 2: wide 갱신 + 확장 V자 (4사이클마다 ≈ 40초) ──
                    if cycle % 4 == 0:
                        # 거래대금 상위 50개 갱신 (시장 상태에 따라)
                        try:
                            _mkt = get_eth_status()
                        except Exception:
                            _mkt = "bullish"
                        refresh_sec = 300 if _mkt == "bearish" else 600
                        
                        now = datetime.datetime.now()
                        if (now - last_refresh).seconds >= refresh_sec:
                            try:
                                all_t = get_cached_tickers()
                                stable = set(CONFIG.get("stable_coins", []))
                                protected = set(trader.protected_coins)
                                candidates = [t for t in all_t if t not in stable and t not in protected]
                                
                                volumes = []
                                for t in candidates[:150]:
                                    try:
                                        df_v = pyupbit.get_ohlcv(t, interval="day", count=1)
                                        if df_v is not None and len(df_v) > 0:
                                            vol_krw = df_v["volume"].iloc[-1] * df_v["close"].iloc[-1]
                                            volumes.append((t, vol_krw))
                                        time.sleep(0.05)
                                    except Exception:
                                        pass
                                volumes.sort(key=lambda x: x[1], reverse=True)
                                wide_tickers_local = [t for t, _ in volumes[:50]]
                                trader.wide_tickers = wide_tickers_local
                                last_refresh = now
                                _cycle_tag = "🔥하락장5분" if _mkt == "bearish" else "상승장10분"
                                logger.info(f"📈 [V자확장] 거래대금 상위 {len(wide_tickers_local)}개 갱신완료 ({_cycle_tag})")
                            except Exception as e:
                                logger.error(f"⚠️ V자확장 갱신 오류: {e}")
                        
                        # 확장 V자 탐색
                        candidate_set = set(trader.candidate_coins) | set(trader.bought_coins.keys())
                        scan_list = [t for t in wide_tickers_local if t not in candidate_set]
                        for ticker in scan_list:
                            try:
                                v_signal, v_info = get_v_reversal_signal(ticker)
                                if v_signal == "v_reversal":
                                    logger.info(
                                        f"📈 [V자확장발견] {ticker.replace('KRW-','')} | "
                                        f"EMA100이격: {v_info['gap_pct']:.1f}% | "
                                        f"기울기: {v_info.get('ema100_slope',0):+.3f}% | "
                                        f"CCI: {v_info['cci_low']:.0f}→{v_info['cci_curr']:.0f} | "
                                        f"RSI: {v_info['rsi_low']:.1f}→{v_info['rsi_curr']:.1f} | "
                                        f"거래량: {v_info.get('vol_ratio',0):.1f}배"
                                    )
                                    _safe_put_buy(ticker, "v_reversal")
                                time.sleep(0.3)
                            except Exception:
                                pass
            except Exception as e:
                logger.error(f"⚠️ V자반등 통합 스레드 오류: {e}")
            time.sleep(10)  # 기존 8초 → 10초 (CPU 경감)

    def _get_current_price(ticker, info):
        try:
            return get_current_price_safe(ticker) or info.get("buy_price", 0)
        except Exception:
            return info.get("buy_price", 0)

    # ── ⑦ 코인 선별 스레드 (5분마다) ──
    def select_thread_func():
        while True:
            try:
                now = datetime.datetime.now()
                full_elapsed = (now - trader.last_select_time).seconds / 60
                if full_elapsed >= CONFIG["coin_refresh_min"]:
                    result = select_top_coins(trader.protected_coins)
                    trader.candidate_coins   = result[0]
                    trader.ticker_types      = result[1]
                    trader.last_select_time  = now
                    trader.last_replace_time = now
                else:
                    replace_elapsed = (now - trader.last_replace_time).seconds / 60
                    if replace_elapsed >= CONFIG["coin_replace_min"]:
                        trader.candidate_coins = replace_top_coins(
                            trader.candidate_coins,
                            trader.protected_coins,
                            set(trader.bought_coins.keys())
                        )
                        trader.last_replace_time = now
            except Exception as e:
                logger.error(f"⚠️ 선별 스레드 오류: {e}")
            time.sleep(30)

    # ── 🔥 호가깨어남 (Wake-up) 탐색 스레드 (10초마다) ──
    # 미누님 통찰 기반: 세력 작전 2단계 감지
    # - 매집 끝나고 본격 펌핑 직전 = 가장 좋은 진입 시점
    # - candidate(20개) + 거래대금 상위 50개 동시 스캔
    # - 🔧 호가창 스냅샷도 수집해서 detect_orderbook_heating과 통합
    # 🔥 wakeup 알림 쿨다운 (같은 코인 60분 침묵) — P5: 15분→60분
    # CFG 4연패(-2.82%) 방지: "한 번 실패한 코인은 1시간 쉬자"
    _wakeup_alert_history = {}
    
    def wakeup_thread_func():
        cycle = 0
        while True:
            try:
                cycle += 1
                if len(trader.bought_coins) < CONFIG["max_coins"]:
                    bought_set = set(trader.bought_coins.keys())
                    candidate_set = set(trader.candidate_coins)
                    
                    scan_targets = list(candidate_set - bought_set)
                    # 확장 스캔 (3사이클마다 = 30초) — P5: 2→3 CPU경감
                    if cycle % 3 == 0 and trader.wide_tickers:
                        for t in trader.wide_tickers:
                            if t not in candidate_set and t not in bought_set:
                                scan_targets.append(t)
                    
                    now_ts = time.time()
                    for ticker in scan_targets:
                        try:
                            # 🔇 쿨다운 체크: 같은 코인 60분 침묵 (P5: 15분→60분)
                            last_alert = _wakeup_alert_history.get(ticker, 0)
                            if now_ts - last_alert < 3600:  # 60분
                                continue
                            
                            # 캔들 기반 wakeup 체크
                            is_wakeup, info = detect_wakeup_signal(
                                ticker, 
                                protected_coins=trader.protected_coins,
                                bought_coins=bought_set
                            )
                            
                            # 🔧 호가창 스냅샷 수집 (캐시에 저장)
                            if is_wakeup or cycle % 3 == 0:
                                get_orderbook_snapshot(ticker)
                            
                            # 호가창 깨어남도 체크 (히스토리 2개 이상이면)
                            ob_heating, ob_info = detect_orderbook_heating(ticker)
                            
                            # wakeup OR 호가깨어남 → 매수 큐 적재
                            if is_wakeup:
                                _wakeup_alert_history[ticker] = now_ts
                                logger.warning(
                                    f"🔥 [캔들깨어남] {ticker.replace('KRW-','')} | "
                                    f"거래량 {info['vol_ratio']}배 | "
                                    f"가격 {info['price_change']:+.1f}% | "
                                    f"체결강도 {info.get('taker_ratio','?')}% | "
                                    f"{info['passed']} → 매수 큐 적재!"
                                )
                                _safe_put_buy(ticker, "wakeup")
                            elif ob_heating:
                                _wakeup_alert_history[ticker] = now_ts
                                logger.warning(
                                    f"📖 [호가깨어남] {ticker.replace('KRW-','')} | "
                                    f"매수잔량 {ob_info['bid_growth']}배 | "
                                    f"가격 {ob_info['price_change']:+.2f}% | "
                                    f"스프레드 {ob_info['spread']}% | "
                                    f"{ob_info['passed']} → 매수 큐 적재!"
                                )
                                _safe_put_buy(ticker, "wakeup")
                            
                            # ⚡ 약한 호가 신호도 active_watchlist 등록 (공격모드)
                            # bid_growth 1.5배만 돼도 1분봉 감시 시작
                            elif ob_info and ob_info.get("bid_growth", 0) >= 1.5:
                                register_active_watch(
                                    ticker,
                                    f"호가매수 {ob_info['bid_growth']}배"
                                )
                        except Exception:
                            pass
                        time.sleep(0.2)
                    
                    # 오래된 쿨다운 정리 (2시간 지난 건 제거)
                    expired = [k for k, v in _wakeup_alert_history.items() 
                               if now_ts - v > 7200]
                    for k in expired:
                        del _wakeup_alert_history[k]
            except Exception as e:
                logger.error(f"⚠️ 호가깨어남 스레드 오류: {e}")
            time.sleep(10)

    # ── 🐋 세력 매집찾기 스레드 (60초마다) ──
    # 미누님 통찰: "세력은 잡코인을 며칠에 걸쳐 긴꼬리 양/음봉으로 매집"
    # 우리는 며칠 못 기다리니 → 그 패턴을 "수시로" 찾음
    # 1시간봉 기반 → 주기 60초로 여유있게 (API 부담 낮춤)
    # 🐋 매집찾기 알림 쿨다운 (같은 코인 30분 침묵)
    _whale_alert_history = {}  # {ticker: last_alert_timestamp}
    
    def whale_hunt_thread_func():
        # 첫 실행 90초 후 (초기 부하 분산)
        time.sleep(90)
        cycle = 0
        while True:
            try:
                cycle += 1
                if len(trader.bought_coins) < CONFIG["max_coins"]:
                    bought_set = set(trader.bought_coins.keys())
                    candidate_set = set(trader.candidate_coins)
                    
                    # 대상: candidate + wide 전부 (세력 매집은 잡코인도 많음)
                    scan_targets = list(candidate_set - bought_set)
                    if trader.wide_tickers:
                        for t in trader.wide_tickers:
                            if t not in candidate_set and t not in bought_set:
                                scan_targets.append(t)
                    
                    whale_found = 0
                    whale_skipped = 0
                    now_ts = time.time()
                    for ticker in scan_targets:
                        try:
                            # 🔇 쿨다운 체크: 같은 코인 30분 침묵
                            last_alert = _whale_alert_history.get(ticker, 0)
                            if now_ts - last_alert < 1800:  # 30분
                                continue
                            
                            is_whale, info = detect_whale_accumulation_pattern(
                                ticker,
                                protected_coins=trader.protected_coins,
                                bought_coins=bought_set
                            )
                            if is_whale:
                                whale_found += 1
                                _whale_alert_history[ticker] = now_ts
                                size_emoji = "🐋" if info["whale_size"] == "big" else "🐟"
                                tfs = "+".join(info["timeframes"])
                                
                                # 🎯 시장 상태별 분기 (미누님 전략)
                                mkt_state, _ = get_market_state()
                                
                                # 상승장 + big → 즉시 매수 (대형세력 매집 = 홈런 기회)
                                # 그 외 (횡보/하락장/상승+small) → watchlist 킵
                                immediate_buy = (
                                    mkt_state in ("strong_bull", "bullish") 
                                    and info["whale_size"] == "big"
                                )
                                
                                if immediate_buy:
                                    logger.warning(
                                        f"🐋 [매집봉→즉시매수] {ticker.replace('KRW-','')} | "
                                        f"{info['candle_type']} ({info['primary_tf']}) | "
                                        f"body {info['body_ratio']}배 | 거래량 {info['vol_ratio']}배 | "
                                        f"TF: {tfs} | 일거래대금 {info['daily_value']/100_000_000:.1f}억 | "
                                        f"{size_emoji} big + {mkt_state} → 매수 큐 적재!"
                                    )
                                    try:
                                        send_telegram(
                                            f"🐋 <b>매집봉 → 즉시 매수! (BIG)</b>\n"
                                            f"코인: {ticker.replace('KRW-','')}\n"
                                            f"캔들: {info['candle_type']} ({info['primary_tf']})\n"
                                            f"body: {info['body_ratio']}배 / 거래량 {info['vol_ratio']}배\n"
                                            f"TF감지: {tfs}\n"
                                            f"거래대금: {info['daily_value']/100_000_000:.1f}억\n"
                                            f"시장: {mkt_state} → 대형세력 매집 포착!"
                                        )
                                    except Exception:
                                        pass
                                    _safe_put_buy(ticker, "whale_hunt")
                                else:
                                    # 📥 watchlist에 킵 (호가창 활발 시 매수 승격)
                                    with trader.watchlist_lock:
                                        trader.whale_watchlist[ticker] = {
                                            "detected_at":  now_ts,
                                            "whale_size":   info["whale_size"],
                                            "candle_type":  info["candle_type"],
                                            "primary_tf":   info["primary_tf"],
                                            "body_ratio":   info["body_ratio"],
                                            "vol_ratio":    info["vol_ratio"],
                                            "daily_value":  info["daily_value"],
                                            "tfs":          tfs,
                                            "market_state": mkt_state,
                                        }
                                    logger.warning(
                                        f"📥 [매집봉→킵] {ticker.replace('KRW-','')} | "
                                        f"{info['candle_type']} ({info['primary_tf']}) | "
                                        f"body {info['body_ratio']}배 | 거래량 {info['vol_ratio']}배 | "
                                        f"TF: {tfs} | {size_emoji} {info['whale_size']} | "
                                        f"시장: {mkt_state} → watchlist 저장 (호가창 활발 대기)"
                                    )
                                    # big 규모만 텔레그램
                                    if info["whale_size"] == "big":
                                        try:
                                            send_telegram(
                                                f"📥 <b>매집봉 watchlist 저장</b>\n"
                                                f"코인: {ticker.replace('KRW-','')}\n"
                                                f"캔들: {info['candle_type']} ({info['primary_tf']})\n"
                                                f"시장: {mkt_state} ({size_emoji} {info['whale_size']})\n"
                                                f"→ 호가창 활발해지면 매수 승격"
                                            )
                                        except Exception:
                                            pass
                        except Exception:
                            pass
                        time.sleep(0.3)  # API 부담 덜기
                    
                    # 오래된 쿨다운 히스토리 정리 (1시간 지난 건 제거)
                    expired = [k for k, v in _whale_alert_history.items() 
                               if now_ts - v > 3600]
                    for k in expired:
                        del _whale_alert_history[k]
                    
                    if whale_found > 0:
                        logger.info(f"🐋 [매집찾기] 이번 사이클 {whale_found}개 신규 감지 "
                                    f"(쿨다운 중: {len(_whale_alert_history)}개)")
            except Exception as e:
                logger.error(f"⚠️ 매집찾기 스레드 오류: {e}")
            time.sleep(60)

    # ── 🌟 대형 홈런 헌터 (30초 주기, 시장 무관) ──
    # 미누님 요청: "BLUR 같은 대형 홈런은 시장 무관 항상 찾아서 바로 승부"
    # - 전일대비 +15% 이상
    # - 거래대금 100억+
    # - 거래량 폭발 3배+
    # - 하락장/폭락장이어도 매수 강행
    # - 매집봉/watchlist 로직 우회
    _homerun_alert_history = {}  # 같은 코인 1시간 쿨다운
    
    def homerun_hunter_thread_func():
        # 첫 실행 60초 후
        time.sleep(60)
        while True:
            try:
                if len(trader.bought_coins) < CONFIG["max_coins"]:
                    bought_set = set(trader.bought_coins.keys())
                    candidate_set = set(trader.candidate_coins)
                    
                    # 대상: candidate + wide (홈런은 아무데나 등장)
                    scan_targets = list(candidate_set - bought_set)
                    if trader.wide_tickers:
                        for t in trader.wide_tickers:
                            if t not in candidate_set and t not in bought_set:
                                scan_targets.append(t)
                    
                    now_ts = time.time()
                    for ticker in scan_targets:
                        try:
                            # 쿨다운 체크 (1시간)
                            last = _homerun_alert_history.get(ticker, 0)
                            if now_ts - last < 3600:
                                continue
                            
                            is_homerun, info = detect_homerun_coin(
                                ticker,
                                protected_coins=trader.protected_coins,
                                bought_coins=bought_set
                            )
                            if is_homerun:
                                _homerun_alert_history[ticker] = now_ts
                                
                                # 시장 상태 확인 (로그용)
                                mkt_state, _ = get_market_state()
                                _tier = info.get('tier', '?')
                                _tier_emoji = {"HUGE": "🌟🌟🌟", "MID": "🌟🌟", "SMALL": "🌟"}.get(_tier, "🌟")
                                
                                logger.warning(
                                    f"{_tier_emoji} [{_tier}홈런감지] {ticker.replace('KRW-','')} | "
                                    f"전일 +{info['daily_change']}% | "
                                    f"거래대금 {info['daily_value']/100_000_000:.0f}억 | "
                                    f"1분봉 거래량 {info['vol_ratio']}배 | "
                                    f"RSI {info['rsi']:.0f} | 시장: {mkt_state} | "
                                    f"→ 즉시 매수 강행!"
                                )
                                try:
                                    send_telegram(
                                        f"{_tier_emoji} <b>대형 홈런 코인 발견!</b>\n"
                                        f"등급: {_tier}\n"
                                        f"코인: {ticker.replace('KRW-','')}\n"
                                        f"전일대비: +{info['daily_change']}%\n"
                                        f"일 거래대금: {info['daily_value']/100_000_000:.0f}억\n"
                                        f"1분봉 거래량: {info['vol_ratio']}배\n"
                                        f"RSI: {info['rsi']:.0f}\n"
                                        f"⚡ 시장 무관 즉시 매수!"
                                    )
                                except Exception:
                                    pass
                                
                                # 🌟 homerun 타입으로 매수 큐 적재
                                # try_buy_if_signal은 homerun을 surge로 처리 (공격적)
                                _safe_put_buy(ticker, "homerun")
                                
                                # watchlist에 있으면 제거 (중복 방지)
                                with trader.watchlist_lock:
                                    trader.whale_watchlist.pop(ticker, None)
                            else:
                                # 🎯 홈런 조건 못 미쳤지만 +5% 이상이면 active_watch 등록
                                # (1분봉으로 세력 움직임 감시)
                                try:
                                    daily_change = get_cached_daily_change(ticker)
                                    if daily_change >= 5.0:
                                        register_active_watch(
                                            ticker,
                                            f"전일 +{daily_change:.1f}%"
                                        )
                                except Exception:
                                    pass
                        except Exception:
                            pass
                        time.sleep(0.2)
                    
                    # 오래된 쿨다운 정리
                    expired = [k for k, v in _homerun_alert_history.items() 
                               if now_ts - v > 7200]
                    for k in expired:
                        del _homerun_alert_history[k]
            except Exception as e:
                logger.error(f"⚠️ 홈런헌터 스레드 오류: {e}")
            time.sleep(30)

    # ──────────────────────────────────────────────────────────
    # ⚡ 1분봉 공격 감시 시스템 (미누님 요청)
    # ──────────────────────────────────────────────────────────
    # 호가 활발/급변동/대형상승 코인 → active_watchlist 등록
    # → 15초마다 1분봉 체크 → 세력 움직임 감지 시 즉시 매수
    # → 품질필터/Grace Period 모두 우회 (실시간 감지)
    
    def detect_active_watch_signal(ticker):
        """
        1분봉 홈런타자 선별 진입 (미누님 전략)
        
        필수 조건: 1분봉 EMA5 > EMA20 > EMA55 정배열
        
        트리거 (하나만 맞으면 매수):
          A. 양봉 + 거래량 (시간대별 동적)
          B. 거래량 3배+ (음봉이어도 = 세력 매집)
          C. 2봉 연속 상승
          D. 호가 매수잔량 3배+
          E. 거래대금 급증
          F. EMA55 반등 (10분봉)
        
        차단: EMA55 이격 > 7% (10분봉)
        """
        try:
            # 🔧 캐시 사용 (CPU 최적화)
            # 1분봉 EMA 정배열 체크 (30초 캐시)
            ema_1m = get_cached_ema_1m(ticker)
            if not ema_1m or not ema_1m["ok"]:
                return False, ""  # 정배열 아니면 스킵
            
            # 10분봉 EMA55 과열 체크 (60초 캐시)
            ema_10m = get_cached_ema_10m(ticker)
            df_10m_aw = None  # 호환용
            if ema_10m:
                ema55_aw = ema_10m["ema55"]
                curr_price = get_current_price_safe(ticker) or 0
                if ema55_aw > 0 and curr_price > 0:
                    ema55_gap_aw = (curr_price - ema55_aw) / ema55_aw * 100
                    if ema55_gap_aw > 7.0:
                        return False, ""  # 과열 → 스킵
            
            # 🌊 엘리엇 파동 과열 체크 (5분 캐시)
            # 미누님 통찰: 3파 고점 근처에서 매수 = 4파 조정에 물림
            wave_hot, wave_info = detect_wave_overheated(ticker, block_pct=90)
            if wave_hot:
                _fib = wave_info.get('fib', {})
                _phase = wave_info.get('wave_phase', '?')
                _w3ext = _fib.get('wave3_ext', 0)
                _support = wave_info.get('support')
                _support_str = f" | 지지 {_support:.0f}원" if _support else ""
                logger.info(
                    f"🌊 [파동과열] {ticker.replace('KRW-','')} [active] → 매수 차단: "
                    f"{_phase} | {wave_info.get('wave_count',0)}파동 | "
                    f"고점 {wave_info.get('wave3_high',0):.0f}원 | "
                    f"저점 {wave_info.get('trend_low',0):.0f}원 | "
                    f"위치 {wave_info.get('position_pct',0):.0f}% | "
                    f"3파확장 {_w3ext:.2f}x{_support_str}"
                )
                return False, ""  # 파동 과열 → 스킵
            
            # 1분봉 트리거용 (캐시된 df 사용)
            df = ema_1m.get("df")
            if df is None or len(df) < 6:
                return False, ""
            
            curr = df.iloc[-1]
            prev = df.iloc[-2]
            
            # 평균 거래량 (최근 5봉)
            avg_vol = df["volume"].iloc[-6:-1].mean()
            if avg_vol <= 0:
                return False, ""
            
            curr_vol_ratio = curr["volume"] / avg_vol
            curr_change = (curr["close"] - curr["open"]) / curr["open"] * 100
            prev_change = (prev["close"] - prev["open"]) / prev["open"] * 100
            is_bullish = curr["close"] > curr["open"]
            
            # 🕐 시간대별 트리거 민감도 조정
            _tmode = get_time_mode()
            _tcfg = TIME_MODE_CONFIG.get(_tmode, {})
            _vol_thresh = _tcfg.get("active_trigger", 2.0)  # 공격:1.5 노멀:2.0 매집:2.5
            
            # 🚨 정각 폭발 모드 (XX:00~XX:05) - 트리거 최대 완화
            if is_hour_explosion_window():
                if is_bullish and curr_vol_ratio >= 1.5 and curr_change >= 0.3:
                    return True, f"⚡정각폭발 양봉+{curr_vol_ratio:.1f}배+{curr_change:+.2f}%"
                if curr_vol_ratio >= 2.0:
                    bs = "양봉" if is_bullish else "음봉(매집)"
                    return True, f"⚡정각폭발 거래량{curr_vol_ratio:.1f}배 {bs}"
            
            # 트리거 A: 양봉 + 거래량 (시간대별 동적)
            if is_bullish and curr_vol_ratio >= _vol_thresh:
                return True, f"양봉+거래량 {curr_vol_ratio:.1f}배 ({_tmode})"
            
            # 트리거 B: 거래량 폭발 (시간대 무관 3배+)
            if curr_vol_ratio >= 3.0:
                bullish_str = "양봉" if is_bullish else "음봉(매집)"
                return True, f"거래량폭발 {curr_vol_ratio:.1f}배 {bullish_str}"
            
            # 트리거 C: 2봉 연속 상승
            if is_bullish and prev["close"] > prev["open"] and curr_vol_ratio >= 1.5:
                return True, f"2봉연속상승 {curr_change:+.2f}%/{prev_change:+.2f}%"
            
            # 트리거 D: 호가 매수잔량 3배+ (호가창 캐시 사용)
            try:
                ob_heating, ob_info = detect_orderbook_heating(ticker)
                if ob_heating and ob_info.get("bid_growth", 0) >= 3.0:
                    return True, f"호가매수벽 {ob_info['bid_growth']}배"
            except Exception:
                pass
            
            # 트리거 E: 거래대금 급증 (세력 진입 신호)
            try:
                vs, vs_ratio = detect_value_surge(ticker)
                if vs:
                    return True, f"거래대금급증 {vs_ratio:.1f}배"
            except Exception:
                pass
            
            # 트리거 F: EMA55 반등 (10분봉 캐시 기준)
            try:
                if ema_10m:
                    _ema55_f = ema_10m["ema55"]
                    _prev_cl = ema_10m.get("prev_close", 0)
                    _prev_e55 = ema_10m.get("prev_ema55", _ema55_f)
                    if _ema55_f > 0 and _prev_e55 > 0:
                        prev_gap = abs(_prev_cl - _prev_e55) / _prev_e55 * 100
                        cp = get_current_price_safe(ticker) or 0
                        if prev_gap <= 1.0 and cp > _ema55_f and is_bullish and curr_vol_ratio >= 1.5:
                            return True, f"EMA55반등 10분봉 (이격{prev_gap:.2f}%→양봉+거래량{curr_vol_ratio:.1f}배)"
            except Exception:
                pass
            
            return False, ""
        except Exception as e:
            logger.debug(f"active_watch 감지 오류 {ticker}: {e}")
            return False, ""
    
    # ⚡ active_watch 쿨다운 (같은 코인 30분 재등록 방지)
    # MINA 6회, ORDER 5회 반복 매수 → 손실 누적 방지
    _active_cooldown = {}  # {ticker: last_time}
    
    def register_active_watch(ticker, reason):
        """active_watchlist에 등록 (쿨다운 30분 + 중복/만료 관리)"""
        try:
            # 쿨다운 체크 (30분)
            now_ts = time.time()
            if ticker in _active_cooldown:
                if now_ts - _active_cooldown[ticker] < 1800:
                    return False  # 30분 쿨다운
            
            with trader.active_watch_lock:
                if ticker in trader.bought_coins:
                    return False
                if ticker in trader.active_watchlist:
                    return False
                
                trader.active_watchlist[ticker] = {
                    "registered_at": now_ts,
                    "reason":        reason,
                }
                _active_cooldown[ticker] = now_ts
                logger.info(
                    f"⚡ [active등록] {ticker.replace('KRW-','')} | {reason} "
                    f"(현재 {len(trader.active_watchlist)}개)"
                )
                return True
        except Exception as e:
            logger.debug(f"active 등록 오류 {ticker}: {e}")
            return False
    
    # ──────────────────────────────────────────────────────────
    # 🕐 시간대별 전략 모드 (미누님 4시간봉 기반 설계)
    # ──────────────────────────────────────────────────────────
    # 🕐 시간대별 전략 모드 → 전역으로 이동됨 (위에 정의)
    # HOUR_ATTACK_TIMES, get_time_mode, TIME_MODE_CONFIG,
    # TIME_MARKET_MULTIPLIER, is_pre_hour_window, is_hour_explosion_window
    
    def detect_value_surge(ticker):
        """
        💰 거래대금 급증 감지
        최근 5분 거래대금 vs 이전 25분 평균
        → 2배 이상이면 세력 진입 신호
        """
        try:
            df = safe_api_call(pyupbit.get_ohlcv, ticker, 
                               interval="minute1", count=30)
            if df is None or len(df) < 25:
                return False, 0
            
            # 거래대금 = close × volume
            value = df["close"] * df["volume"]
            recent_5 = value.iloc[-5:].sum()
            prev_25  = value.iloc[-30:-5].sum() / 25 * 5  # 5분치로 환산
            
            if prev_25 <= 0:
                return False, 0
            
            ratio = recent_5 / prev_25
            return ratio >= 2.0, ratio
        except Exception:
            return False, 0
    
    def active_watch_thread_func():
        """
        ⚡ 1분봉 공격 감시 스레드
        - 15초마다 active_watchlist의 모든 코인 1분봉 체크
        - 세력 움직임 감지 시 즉시 매수 (품질필터 우회)
        - 60분 만료
        + 정각 임박 부스터 (XX:50~)
        + TOP 거래대금 상시 등록 (5분마다)
        """
        time.sleep(60)
        last_pre_hour_register = 0
        last_top25_scan = 0
        while True:
            try:
                now_ts = time.time()
                
                # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                # 🔥 전일대비 상위 25 스캔 (5분마다, 공격모드) — P5 통합
                # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                if now_ts - last_top25_scan > 300 and get_time_mode() == "attack":
                    last_top25_scan = now_ts
                    try:
                        all_t = get_cached_tickers()
                        stable = set(CONFIG.get("stable_coins", []))
                        changes = []
                        for t in all_t:
                            if t in stable: continue
                            try:
                                dc = get_cached_daily_change(t)
                                changes.append((t, dc))
                            except Exception:
                                pass
                        
                        changes.sort(key=lambda x: x[1], reverse=True)
                        new_top25 = [t for t, _ in changes[:25]]
                        
                        # 🚀 순위 급상승 감지
                        if trader.prev_top25:
                            prev_set = set(trader.prev_top25)
                            new_entries = []
                            for rank, (tk, dc) in enumerate(changes[:25]):
                                if tk not in prev_set and tk not in trader.bought_coins:
                                    logger.warning(
                                        f"🚀 [순위급상승] {tk.replace('KRW-','')} | "
                                        f"+{dc:.1f}% → TOP{rank+1} 진입! → active 등록!"
                                    )
                                    register_active_watch(tk, f"🚀TOP{rank+1} +{dc:.1f}%")
                                    new_entries.append(tk)
                            if new_entries:
                                names = [t.replace('KRW-','') for t in new_entries[:5]]
                                logger.info(f"🔥 [TOP25] 신규진입: {', '.join(names)} (총 {len(new_entries)}개)")
                        
                        trader.prev_top25 = trader.top25_tickers
                        trader.top25_tickers = new_top25
                    except Exception as e:
                        logger.debug(f"top25 스캔 오류: {e}")
                
                # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                # 🕐 정각 임박 부스터 (XX:50 시점에 1회)
                # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                if is_pre_hour_window() and now_ts - last_pre_hour_register > 300:
                    last_pre_hour_register = now_ts
                    try:
                        from datetime import datetime
                        next_hr = (datetime.now().hour + 1) % 24
                        logger.warning(
                            f"🕐 [정각임박] {next_hr}시 공격 준비! "
                            f"거래대금 TOP 30 active 등록 시작..."
                        )
                        try:
                            send_telegram(
                                f"🕐 <b>{next_hr}시 정각 임박!</b>\n"
                                f"거래대금 TOP 30 active 등록\n"
                                f"5분 후 공격 모드 가동"
                            )
                        except Exception:
                            pass
                        
                        # 거래대금 TOP N 가져오기 (시간대별 동적)
                        _top_n = TIME_MODE_CONFIG[get_time_mode()]["pre_hour_top"]
                        if trader.wide_tickers and _top_n > 0:
                            registered = 0
                            for tk in trader.wide_tickers[:_top_n]:
                                if tk in trader.bought_coins:
                                    continue
                                if register_active_watch(tk, "🕐 정각임박 자동등록"):
                                    registered += 1
                            logger.info(f"🕐 [정각임박] {registered}개 등록 완료 (TOP {_top_n})")
                    except Exception as e:
                        logger.debug(f"정각임박 등록 오류: {e}")
                
                # 🔧 P5: 두 번째 TOP25 스캔 제거 (첫 번째 스캔으로 통합됨)
                
                with trader.active_watch_lock:
                    items = list(trader.active_watchlist.items())
                
                if not items:
                    time.sleep(15)
                    continue
                
                expired = []
                triggered = []
                
                for ticker, data in items:
                    try:
                        # 1. 만료 체크 (60분)
                        age = now_ts - data["registered_at"]
                        if age > 3600:
                            expired.append((ticker, "만료 60m"))
                            continue
                        
                        # 2. 이미 보유 중이면 제거
                        if ticker in trader.bought_coins:
                            expired.append((ticker, "이미 보유"))
                            continue
                        
                        # 3. 1분봉 트리거 체크
                        is_signal, sig_reason = detect_active_watch_signal(ticker)
                        if is_signal:
                            logger.warning(
                                f"⚡ [active매수] {ticker.replace('KRW-','')} | "
                                f"등록사유: {data['reason']} | "
                                f"트리거: {sig_reason} | "
                                f"감시 {age/60:.1f}분 → 즉시 매수!"
                            )
                            try:
                                send_telegram(
                                    f"⚡ <b>1분봉 공격 매수!</b>\n"
                                    f"코인: {ticker.replace('KRW-','')}\n"
                                    f"등록사유: {data['reason']}\n"
                                    f"트리거: {sig_reason}\n"
                                    f"감시: {age/60:.1f}분\n"
                                    f"→ 매수 큐 적재"
                                )
                            except Exception:
                                pass
                            _safe_put_buy(ticker, "active_watch")
                            triggered.append((ticker, sig_reason))
                    except Exception as e:
                        logger.debug(f"active 감시 오류 {ticker}: {e}")
                    
                    time.sleep(0.3)  # API 부담 덜기
                
                # 만료/매수 처리
                if expired or triggered:
                    with trader.active_watch_lock:
                        for ticker, _ in expired + triggered:
                            trader.active_watchlist.pop(ticker, None)
                    if triggered:
                        logger.info(
                            f"⚡ [active] 매수 {len(triggered)}개, 만료 {len(expired)}개 | "
                            f"남은: {len(trader.active_watchlist)}개"
                        )
            except Exception as e:
                logger.error(f"⚠️ active_watch 스레드 오류: {e}")
            # ⚡ 시간대별 동적 주기
            mode = get_time_mode()
            cfg = TIME_MODE_CONFIG[mode]
            sleep_sec = 5 if is_hour_explosion_window() else cfg["scan_interval"]
            time.sleep(sleep_sec)
    
    # ── 📥 watchlist 감시 스레드 (15초 주기) ──
    # 미누님 전략: 횡보장/하락장 + small 매집봉은 watchlist에 킵
    # → 호가창 활발해지면 매수로 승격 (세력 2단계 시작 = 진짜 움직임)
    # → 상승장으로 시장 전환되면 big 매집봉은 즉시 승격
    # → 12시간 경과 시 자동 만료
    def watchlist_thread_func():
        # 첫 실행 2분 후 (초기 부하 분산)
        time.sleep(120)
        while True:
            try:
                now_ts = time.time()
                
                # 스냅샷 (락 짧게)
                with trader.watchlist_lock:
                    items = list(trader.whale_watchlist.items())
                
                if not items:
                    time.sleep(15)
                    continue
                
                expired = []
                promoted = []
                
                for ticker, data in items:
                    try:
                        # 1. 만료 체크 (12시간)
                        age = now_ts - data["detected_at"]
                        if age > 43200:  # 12시간
                            expired.append((ticker, "만료 12h"))
                            continue
                        
                        # 2. 이미 보유 중이면 제거
                        if ticker in trader.bought_coins:
                            expired.append((ticker, "이미 보유"))
                            continue
                        
                        # 3. 시장 상태 전환 체크
                        # 상승장으로 바뀐 big → 즉시 승격
                        curr_mkt, _ = get_market_state()
                        if curr_mkt in ("strong_bull", "bullish") and data["whale_size"] == "big":
                            logger.warning(
                                f"🚀 [watchlist→승격] {ticker.replace('KRW-','')} | "
                                f"시장 전환 ({data['market_state']} → {curr_mkt}) | "
                                f"🐋 big → 즉시 매수!"
                            )
                            try:
                                send_telegram(
                                    f"🚀 <b>매집봉 승격 (시장전환)</b>\n"
                                    f"코인: {ticker.replace('KRW-','')}\n"
                                    f"시장: {data['market_state']} → {curr_mkt}\n"
                                    f"킵 시간: {age/60:.0f}분\n"
                                    f"→ 매수 큐 적재"
                                )
                            except Exception:
                                pass
                            _safe_put_buy(ticker, "whale_hunt")
                            promoted.append((ticker, "시장전환"))
                            continue
                        
                        # 4. 호가창 활발 감지 (세력 2단계 시작)
                        get_orderbook_snapshot(ticker)
                        ob_heating, ob_info = detect_orderbook_heating(ticker)
                        if ob_heating:
                            logger.warning(
                                f"🔥 [watchlist→승격] {ticker.replace('KRW-','')} | "
                                f"{data['candle_type']} 킵 {age/60:.0f}분 | "
                                f"호가창 활발! 매수잔량 {ob_info['bid_growth']}배 | "
                                f"가격 {ob_info['price_change']:+.2f}% → 매수 승격!"
                            )
                            try:
                                send_telegram(
                                    f"🔥 <b>매집봉 → 호가창 활발!</b>\n"
                                    f"코인: {ticker.replace('KRW-','')}\n"
                                    f"킵 시간: {age/60:.0f}분\n"
                                    f"호가: 매수잔량 {ob_info['bid_growth']}배\n"
                                    f"가격: {ob_info['price_change']:+.2f}%\n"
                                    f"→ 세력 2단계 시작, 매수 승격!"
                                )
                            except Exception:
                                pass
                            _safe_put_buy(ticker, "whale_hunt")
                            promoted.append((ticker, "호가활발"))
                            continue
                    except Exception as e:
                        logger.debug(f"watchlist 감시 오류 {ticker}: {e}")
                    
                    time.sleep(0.3)  # 코인당 0.3초
                
                # 만료/승격 일괄 제거
                if expired or promoted:
                    with trader.watchlist_lock:
                        for ticker, _ in expired + promoted:
                            trader.whale_watchlist.pop(ticker, None)
                    if promoted:
                        logger.info(
                            f"📥 [watchlist] 승격 {len(promoted)}개, 만료 {len(expired)}개 | "
                            f"남은 watchlist: {len(trader.whale_watchlist)}개"
                        )
            except Exception as e:
                logger.error(f"⚠️ watchlist 감시 스레드 오류: {e}")
            time.sleep(15)

    # ── 🛡️ 보호코인 세력 감시 스레드 (저부하 모드) ──
    # 미누님 요청: "본장에 영향없이 아주 천천히"
    # - 10분 주기 (본 로직에 영향 최소화)
    # - 코인당 2초 간격 (API 부담 제로)
    # - 첫 실행 5분 후 (봇 시작 직후 안 건드림)
    # - 매수/매도 안 함 (순수 알림만)
    # - 중복 알림 방지 (같은 신호 2시간 쿨다운)
    _protected_alert_history = {}  # {ticker: {"signal": str, "time": float}}
    
    def protected_watch_thread_func():
        # 첫 실행은 5분 후 (본 봇 안정화 후 시작)
        time.sleep(300)
        while True:
            try:
                protected_list = list(trader.protected_coins)
                if not protected_list:
                    time.sleep(600)  # 10분
                    continue
                
                for ticker in protected_list:
                    try:
                        signals_found = []
                        
                        # 1️⃣ 매집봉 감지 체크
                        is_whale, whale_info = detect_whale_accumulation_pattern(ticker)
                        if is_whale:
                            signals_found.append(("whale_hunt", 
                                f"🐋 매집봉 ({whale_info['whale_size']}) | "
                                f"{whale_info['candle_type']} {whale_info['primary_tf']} | "
                                f"body {whale_info['body_ratio']}배 거래량 {whale_info['vol_ratio']}배 | "
                                f"거래대금 {whale_info['daily_value']/100_000_000:.1f}억"
                            ))
                        
                        # 2️⃣ 캔들 깨어남 체크
                        is_wakeup, wake_info = detect_wakeup_signal(ticker)
                        if is_wakeup:
                            signals_found.append(("wakeup",
                                f"🔥 캔들깨어남 | 거래량 {wake_info['vol_ratio']}배 | "
                                f"가격 {wake_info['price_change']:+.1f}% | "
                                f"체결강도 {wake_info.get('taker_ratio','?')}%"
                            ))
                        
                        # 3️⃣ 실시간 급등 체크 (거래량 폭발) — 강한 신호만
                        try:
                            df_surge = safe_api_call(pyupbit.get_ohlcv, ticker,
                                                     interval=CONFIG["candle_interval"], 
                                                     count=CONFIG["realtime_volume_period"]+1)
                            if df_surge is not None and len(df_surge) >= 6:
                                cv = df_surge["volume"].iloc[-1]
                                av = df_surge["volume"].iloc[:-1].mean()
                                if av > 0:
                                    ratio = cv / av
                                    # 저부하 모드: 5배+ 만 (3배는 너무 약함)
                                    if ratio >= 5.0:
                                        cp = df_surge["close"].iloc[-1]
                                        pp = df_surge["close"].iloc[-2]
                                        if pp > 0:
                                            pc = (cp - pp) / pp * 100
                                            if pc >= 0.5:
                                                signals_found.append(("surge",
                                                    f"🚀 거래량폭발 | {ratio:.1f}배 | 가격 {pc:+.2f}%"
                                                ))
                        except Exception:
                            pass
                        
                        # 호가창 체크는 저부하 모드에서 제외 (API 부담 덜기)
                        
                        # 알림 전송 (중복 방지: 2시간에 1번)
                        if signals_found:
                            now_ts = time.time()
                            last = _protected_alert_history.get(ticker, {})
                            last_ts = last.get("time", 0)
                            last_sig = last.get("signal", "")
                            
                            signal_key = ",".join(s[0] for s in signals_found)
                            
                            # 같은 신호면 2시간 쿨다운 (1시간 → 2시간)
                            if signal_key == last_sig and (now_ts - last_ts) < 7200:
                                continue
                            
                            # 알림 전송
                            logger.warning(
                                f"🛡️ [보호코인 세력감지] {ticker.replace('KRW-','')} | "
                                f"{len(signals_found)}개 신호 감지!"
                            )
                            
                            # 텔레그램 알림 상세
                            try:
                                msg_lines = [
                                    f"🛡️ <b>보호코인 세력 감지!</b>",
                                    f"코인: <b>{ticker.replace('KRW-','')}</b>",
                                    f"",
                                    f"🔍 감지된 신호 ({len(signals_found)}개):",
                                ]
                                for _, desc in signals_found:
                                    msg_lines.append(f"  • {desc}")
                                msg_lines.append("")
                                msg_lines.append("💡 <i>매도 타이밍 고려해보세요!</i>")
                                
                                send_telegram("\n".join(msg_lines))
                            except Exception:
                                pass
                            
                            # 히스토리 업데이트
                            _protected_alert_history[ticker] = {
                                "signal": signal_key,
                                "time":   now_ts,
                            }
                        
                    except Exception as e:
                        logger.debug(f"보호코인 감시 오류 {ticker}: {e}")
                    time.sleep(2.0)  # 코인당 2초 간격 (저부하)
            except Exception as e:
                logger.error(f"⚠️ 보호코인 감시 스레드 오류: {e}")
            time.sleep(600)  # 10분 주기 (본장 영향 최소)

    # ── 스레드 시작 ──
    # ⚡ 매도 워커 3개 (병렬 처리로 폭락 대응)
    # ── 🤖 자동 리밸런싱 스레드 (1시간마다 쿼터 자동 정리) ──
    # 미누님 요청: 봇이 알아서 24/7 자동 운영
    # → 재시작 안 해도 1시간마다 쿼터 점검 → 초과 시 자동 정리
    # → 가망점수 기반으로 "죽어가는 코인" 우선 정리
    def auto_rebalance_thread_func():
        # 첫 실행은 30분 후 (시작 직후 정리와 겹치지 않게)
        time.sleep(1800)
        while True:
            try:
                slot_limits = get_dynamic_slots()
                # 초과 여부 사전 체크
                any_over = False
                for strategy, limit in slot_limits.items():
                    count = sum(
                        1 for bc in trader.bought_coins.values()
                        if bc.get("original_type", bc.get("buy_type")) == strategy
                    )
                    if count > limit:
                        any_over = True
                        break
                
                if any_over:
                    logger.info("🤖 [자동 리밸런싱] 쿼터 초과 감지 → 정리 실행")
                    trader._rebalance_to_slot_quota()
                else:
                    logger.debug("🤖 [자동 리밸런싱] 쿼터 OK, 정리 불필요")
            except Exception as e:
                logger.error(f"⚠️ 자동 리밸런싱 오류: {e}")
            time.sleep(3600)  # 1시간 주기

    thread_list = [
        ("💰 매도워커#1",  threading.Thread(target=sell_exec_thread_func, args=(1,), daemon=True)),
        ("💰 매도워커#2",  threading.Thread(target=sell_exec_thread_func, args=(2,), daemon=True)),
        ("💰 매도워커#3",  threading.Thread(target=sell_exec_thread_func, args=(3,), daemon=True)),
        ("🛒 매수워커#1",  threading.Thread(target=buy_exec_thread_func,  args=(1,), daemon=True)),
        ("🛒 매수워커#2",  threading.Thread(target=buy_exec_thread_func,  args=(2,), daemon=True)),
        ("🛒 매수워커#3",  threading.Thread(target=buy_exec_thread_func,  args=(3,), daemon=True)),
        ("🛡️ 포지션감지", threading.Thread(target=position_thread_func,    daemon=True)),
        ("🚨 surge탐색",  threading.Thread(target=surge_thread_func,       daemon=True)),
        ("📶🏦 통합탐색",  threading.Thread(target=scan_thread_func,        daemon=True)),  # P5: normal+accumulation 통합
        ("📈🔭 V자통합",   threading.Thread(target=v_reversal_combined_thread_func, daemon=True)),  # P5: v_reversal+wide 통합
        ("🔥 호가깨어남",  threading.Thread(target=wakeup_thread_func,       daemon=True)),
        ("🐋 매집찾기",   threading.Thread(target=whale_hunt_thread_func,  daemon=True)),
        ("🌟 홈런헌터",   threading.Thread(target=homerun_hunter_thread_func, daemon=True)),
        ("⚡ active감시", threading.Thread(target=active_watch_thread_func,  daemon=True)),
        ("📥 watchlist",  threading.Thread(target=watchlist_thread_func,   daemon=True)),
        ("🛡️ 보호코인감시", threading.Thread(target=protected_watch_thread_func, daemon=True)),
        ("🔍 코인선별",   threading.Thread(target=select_thread_func,      daemon=True)),
        ("🤖 자동리밸런싱", threading.Thread(target=auto_rebalance_thread_func, daemon=True)),
    ]
    for name, t in thread_list:
        t.start()
        logger.info(f"{name} 스레드 시작!")

    # 🔔 시장 상태 변경 감지용 (텔레그램 알림)
    _prev_market_tag = ""
    
    while True:
        try:
            for ticker in list(trader.daily_stoploss.keys()):
                if trader.daily_stoploss[ticker]["date"] != today_str():
                    del trader.daily_stoploss[ticker]

            # 보유 현황 출력 (수익률 내림차순)
            if trader.bought_coins:
                # 헤더에 시장현황 표시 (5단계)
                try:
                    _mkt_state, _mkt_det = get_market_state()
                    _mkt_emoji = {
                        "strong_bull": "🟢🟢",
                        "bullish":     "🟢",
                        "neutral":     "⚪",
                        "bearish":     "🔴",
                        "strong_bear": "🔴🔴",
                    }.get(_mkt_state, "❓")
                    _btc_str = f"BTC{_mkt_det.get('daily_change',0):+.1f}%"
                    
                    # 시간대 × BTC 교차 표시
                    _tmode = get_time_mode()
                    _tmode_label = TIME_MODE_CONFIG[_tmode]["label"]
                    _tmult = TIME_MARKET_MULTIPLIER.get(_tmode, {}).get(_mkt_state, 0.5)
                    
                    _action = {
                        (1.2,): "풀공격",
                        (1.0,): "공격",
                        (0.8,): "적극",
                        (0.7,): "선별",
                        (0.6,): "표준",
                        (0.5,): "기회",
                        (0.4,): "순환",
                        (0.3,): "방어",
                    }.get((_tmult,), f"x{_tmult}")
                    
                    _mkt_tag = (
                        f"{_tmode_label}×{_mkt_emoji}{_btc_str} = {_action} {_tmult}x"
                    )
                    
                    # 🔔 시장 상태 변경 시 텔레그램 알림
                    _curr_tag_key = f"{_tmode}_{_mkt_state}"
                    if _prev_market_tag and _prev_market_tag != _curr_tag_key:
                        send_telegram(
                            f"📊 <b>시황 변동!</b>\n"
                            f"{_mkt_tag}\n"
                            f"보유: {len(trader.bought_coins)}개"
                        )
                    _prev_market_tag = _curr_tag_key
                    
                except Exception:
                    _mkt_tag = "❓ 조회실패"
                logger.info(f"─── [봇3] 보유 현황 | {_mkt_tag} ───")
                holdings = []
                for ticker, info in list(trader.bought_coins.items()):
                    cp = get_current_price_safe(ticker)
                    if cp:
                        pnl = ((cp - info["buy_price"]) / info["buy_price"]) * 100
                        holdings.append((ticker, info, cp, pnl))
                for ticker, info, cp, pnl in sorted(holdings, key=lambda x: x[3], reverse=True):
                    emoji = "📈" if pnl >= 0 else "📉"
                    btype = "🚀" if info.get("buy_type") == "surge" else "🏦" if info.get("buy_type") == "accumulation" else "📈" if info.get("buy_type") == "v_reversal" else "📶"
                    trail = "🎯" if info.get("trailing_active") else ""
                    swing = "🔄" if info.get("swing_mode") else ""
                    logger.info(
                        f"  {emoji} {btype} {ticker.replace('KRW-',''):>8s} | "
                        f"매수: {info['buy_price']:>10,.4f} | "
                        f"현재: {cp:>10,.4f} | "
                        f"{pnl:+.2f}% {trail}{swing}"
                    )
                logger.info("─" * 50)

            # ── 시장 상태 주기적 로그 + 전환 긴급속보 (BTC 전일대비 기준) ──
            try:
                eth_now = get_eth_status()
                prev_eth = getattr(trader, "_prev_eth_status", None)

                # 전환 감지 → 긴급속보
                if prev_eth is not None and prev_eth != eth_now:
                    if eth_now == "bullish":
                        logger.warning(
                            "🚨 긴급속보: 📈 시장 전환! [하락장 → 상승장] "
                            "BTC 전일대비 상승 전환! surge 매수 해금! 🚀"
                        )
                        send_telegram(
                            "🚨 <b>긴급속보: 시장 전환!</b>\n"
                            "📈 하락장 → 상승장\n"
                            "BTC 전일대비 상승 전환!\n"
                            "surge 매수 해금! 🚀"
                        )
                    else:
                        logger.warning(
                            "🚨 긴급속보: 📉 시장 전환! [상승장 → 하락장] "
                            "BTC 전일대비 하락 전환! surge/노말 매수 금지!"
                        )
                        send_telegram(
                            "🚨 <b>긴급속보: 시장 전환!</b>\n"
                            "📉 상승장 → 하락장\n"
                            "BTC 전일대비 하락 전환!\n"
                            "surge/노말 매수 금지!"
                        )

                trader._prev_eth_status = eth_now

                # 5분마다 현재 상태 출력
                _eth_log_cnt = getattr(trader, "_eth_log_cnt", 0) + 1
                trader._eth_log_cnt = _eth_log_cnt
                if _eth_log_cnt % 10 == 0:  # 30초 * 10 = 5분마다
                    if eth_now == "bullish":
                        logger.info("📊 시장현황: 📈 상승장 (BTC 전일대비↑) | 전략: surge/노말/매집 활성")
                    else:
                        logger.info("📊 시장현황: 📉 하락장 (BTC 전일대비↓) | 전략: 매집/V자만 활성")
            except Exception:
                pass

            # 큐 상태 + 재매수 금지
            sq = sell_queue.qsize(); bq = buy_queue.qsize()
            if sq > 0:
                logger.warning(f"📊 큐: 매도대기 {sq}건 | 매수대기 {bq}건")
            elif bq >= 20:
                logger.warning(f"⚠️ 매수큐 과부하: {bq}/30건 → 낮은 우선순위 드롭 중")

            blocked_list = []
            for t, v in trader.rebuy_blocked.items():
                if datetime.datetime.now() < v:
                    blocked_list.append(f"{t.replace('KRW-','')}({v.strftime('%H:%M')}까지)")
            if blocked_list:
                logger.info(f"🚫 재매수 금지: {', '.join(blocked_list)}")

            time.sleep(CONFIG["check_interval_sec"])

        except KeyboardInterrupt:
            logger.info("\n⏹️ [봇3] 종료 요청...")
            answer = input("보유 코인을 모두 매도할까요? (y/n): ")
            if answer.lower() == "y":
                for ticker in list(trader.bought_coins.keys()):
                    trader.sell(ticker, portion=1.0, reason="수동 종료")
                    time.sleep(0.5)
            logger.info("👋 [봇3] 종료")
            break

        except Exception as e:
            logger.error(f"⚠️ [봇3] 오류: {e}")
            time.sleep(30)
            continue



# ============================================================
# ▶️ 실행
# ============================================================

if __name__ == "__main__":
    print("""
    ╔════════════════════════════════════════════════════════╗
    ║  🚀 업비트 EMA+BB+CCI+MACD 자동매매 봇 (봇 3) v7     ║
    ║  📶 normal: TP1+1.5% / TP2+3%                        ║
    ║  🚀 surge:  EMA 데드크로스까지 홀드!                   ║
    ║  🛡️ 공통:   +0.5% → 본전 보장 활성                    ║
    ╚════════════════════════════════════════════════════════╝

    ⚠️  실행 전 확인사항:
    1. CONFIG에 업비트 API 키를 입력하셨나요?
    2. bot3_v7_bought.json 준비하셨나요?
    """)

    confirm = input("봇 3을 시작하시겠습니까? (y/n): ")
    if confirm.lower() == "y":
        run_bot()
    else:
        print("봇을 시작하지 않았습니다.")
