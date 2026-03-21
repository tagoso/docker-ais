import asyncio
import json
import os
import threading
import traceback

import uvicorn
import websockets
from health_server import app
from supabase import create_client
from websockets.exceptions import ConnectionClosedError

# ===== 環境変数 =====
API_KEY = os.getenv("API_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
DEBUG = os.getenv("DEBUG", "0") == "1"  # ログ多めに見たいときは DEBUG=1 を指定

# 必須環境変数チェック（足りないときは起動時に即エラーにする）
if not API_KEY:
    raise RuntimeError("API_KEY is required (環境変数 API_KEY が設定されていません)")
if not SUPABASE_URL:
    raise RuntimeError("SUPABASE_URL is required (環境変数 SUPABASE_URL が設定されていません)")
if not SUPABASE_KEY:
    raise RuntimeError(
        "SUPABASE_SERVICE_ROLE_KEY is required "
        "(環境変数 SUPABASE_SERVICE_ROLE_KEY が設定されていません)"
    )

# ===== AIS Stream 設定 =====
URI = "wss://stream.aisstream.io/v0/stream"
BBOX = [[[-180, -90], [180, 90]]]

TARGET_MMSIS = [
    563242500,
    636020973,
    565929000,
    566716000,
    566985000,
    566798000,
    563056500,
    563029200,
    563258700,
    566969000,
    564853000,
    565155000,
    564731000,
    564724000,
    566636000,
    566274000,
    564794000,
    564415000,
    564756000,
    563121200,
    563122800,
]

# ===== Supabase クライアント =====
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


def insert_row(mmsi, lat, lon, timestamp, cog, sog, name):
    """Supabase の ais_logs に 1 レコード挿入（初回だけ）。"""
    try:
        res = (
            supabase.table("ais_logs")
            .select("id")
            .eq("mmsi", mmsi)
            .limit(1)
            .execute()
        )
        existing = res.data

        if existing and len(existing) > 0:
            if DEBUG:
                print(f"ℹ️ MMSI {mmsi} already exists in ais_logs. Skipping insert.")
            return
    except Exception as e:
        print(f"❌ Supabase select error for MMSI {mmsi}: {e}")
        traceback.print_exc()
        return

    data = {
        "mmsi": mmsi,
        "lat": lat,
        "lon": lon,
        "timestamp": timestamp,
        "cog": cog,
        "sog": sog,
        "name": name,
    }

    try:
        supabase.table("ais_logs").insert(data).execute()
        print(f"✅ Supabase insert success: {name} ({mmsi})")
    except Exception as e:
        print(f"❌ Supabase insert error for MMSI {mmsi}: {e}")
        traceback.print_exc()


def parse_ais_message(raw_msg: str):
    """
    AIS Stream の JSON メッセージから必要な情報を取り出すヘルパー。
    形は公式の aisstream.io 仕様をもとにざっくり想定：
      {
        "MessageType": "PositionReport",
        "MetaData": {
          "MMSI": 123,
          "lat": ...,
          "lon": ...,
          "received": "2025-12-10T..."
        },
        "Message": {
          "SOG": ...,
          "COG": ...,
          "VesselName": "..."
        }
      }
    """
    data = json.loads(raw_msg)

    if data.get("MessageType") != "PositionReport":
        return None  # 他のメッセージタイプは無視

    meta = data.get("MetaData", {}) or {}
    msg = data.get("Message", {}) or {}

    mmsi = meta.get("MMSI")
    lat = meta.get("lat")
    lon = meta.get("lon")
    timestamp = meta.get("received")  # 受信時刻
    cog = msg.get("COG")
    sog = msg.get("SOG")
    shipname = (
        msg.get("VesselName")
        or msg.get("ShipName")
        or meta.get("ShipName")
        or "UNKNOWN"
    )

    # 必須項目が欠けていたらスキップ
    if not all([mmsi, lat, lon, timestamp]):
        if DEBUG:
            print(f"🚫 Incomplete data, skipping: mmsi={mmsi}, lat={lat}, lon={lon}, ts={timestamp}")
        return None

    return {
        "mmsi": int(mmsi),
        "lat": float(lat),
        "lon": float(lon),
        "timestamp": timestamp,
        "cog": cog,
        "sog": sog,
        "shipname": shipname,
    }


async def main():
    """WebSocket に接続して AIS データを取り込み続けるメインループ。"""
    while True:
        try:
            print("🌐 Connecting to WebSocket...")
            async with websockets.connect(URI, ping_interval=20) as ws:
                print("✅ WebSocket connection established")

                sub = {
                    "APIKey": API_KEY,
                    "BoundingBoxes": BBOX,
                    "FilterShipMMSI": [str(mmsi) for mmsi in TARGET_MMSIS],
                    "FilterMessageTypes": ["PositionReport"],
                }
                await ws.send(json.dumps(sub))
                if DEBUG:
                    print("📨 Subscription message sent")

                async for msg in ws:
                    try:
                        parsed = parse_ais_message(msg)
                        if not parsed:
                            continue

                        mmsi = parsed["mmsi"]
                        lat = parsed["lat"]
                        lon = parsed["lon"]
                        timestamp = parsed["timestamp"]
                        cog = parsed["cog"]
                        sog = parsed["sog"]
                        shipname = parsed["shipname"]

                        # 念のためここでもターゲット MMSI をチェック
                        if TARGET_MMSIS and mmsi not in TARGET_MMSIS:
                            continue

                        insert_row(mmsi, lat, lon, timestamp, cog, sog, shipname)

                        # 通常時の1件ごとの詳細ログは DEBUG 時だけにする
                        if DEBUG:
                            print(
                                f"📡 {shipname} ({mmsi}) @ ({lat}, {lon}) | "
                                f"COG: {cog}° | SOG: {sog} knots @ {timestamp}"
                            )

                    except Exception as e:
                        print(f"⚠️ Parsing or insert error: {e}")
                        traceback.print_exc()

        except ConnectionClosedError as e:
            # 想定内の切断：少し待って再接続
            print(f"⚠️ WebSocket closed: {e}. 5秒後に再接続します…")
            await asyncio.sleep(5)

        except Exception as e:
            # 想定外のエラー：スタックトレースを出してから再接続
            print(f"❌ WebSocket error: {e}")
            traceback.print_exc()
            print("⏳ 10秒後に再接続します…")
            await asyncio.sleep(10)


def run_health_server():
    """ヘルスチェック用の HTTP サーバーを別スレッドで起動。"""
    uvicorn.run(app, host="0.0.0.0", port=8080)


if __name__ == "__main__":
    print("🚀 Launching health server + AIS streamer")
    threading.Thread(target=run_health_server, daemon=True).start()
    try:
        asyncio.run(main())
    except Exception as e:
        print("❌ asyncio.run error:", e)
        traceback.print_exc()
