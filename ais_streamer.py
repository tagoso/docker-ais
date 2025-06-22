import asyncio
import websockets
import os
import json
import traceback
import threading
import uvicorn
from health_server import app
from supabase import create_client

API_KEY = os.getenv("API_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

print("🔧 Starting AIS Streamer...")
print(f"🔑 API_KEY: {API_KEY}")

URI = "wss://stream.aisstream.io/v0/stream"
BBOX = [[[-180, -90], [180, 90]]]

TARGET_MMSIS = [
    563242500, 636020973, 565929000, 566716000, 566985000, 566798000, 563056500,
    563029200, 563258700, 566969000, 564853000, 565155000, 564731000, 564724000,
    566636000, 566274000, 564794000, 564415000, 564756000, 563121200, 563122800,
]

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

def insert_row(mmsi, lat, lon, timestamp, cog, sog, name):
    try:
        res = supabase.table("ais_logs").select("id").eq("mmsi", mmsi).limit(1).execute()
        existing = res.data

        if existing and len(existing) > 0:
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
        "name": name
    }

    try:
        resp = supabase.table("ais_logs").insert(data).execute()
        print(f"✅ Supabase insert success: {name} ({mmsi})")
    except Exception as e:
        print(f"❌ Supabase insert error for MMSI {mmsi}: {e}")
        traceback.print_exc()

async def main():
    while True:
        try:
            print("🌐 Connecting to WebSocket...")
            async with websockets.connect(URI, ping_interval=20) as ws:
                print("✅ WebSocket connection established")

                sub = {
                    "APIKey": API_KEY,
                    "BoundingBoxes": BBOX,
                    "FilterShipMMSI": [str(mmsi) for mmsi in TARGET_MMSIS],
                    "FilterMessageTypes": ["PositionReport"]
                }
                await ws.send(json.dumps(sub))
                print("📨 Subscription message sent")

                async for msg in ws:
                    try:
                        data = json.loads(msg)
                        msg_type = data.get("MessageType")
                        if msg_type != "PositionReport":
                            continue

                        body = data.get("Message", {}).get("PositionReport", {})
                        meta = data.get("MetaData", {})

                        mmsi = int(body.get("UserID") or body.get("MMSI"))
                        lat = body.get("Latitude")
                        lon = body.get("Longitude")
                        cog = body.get("Cog")
                        sog = body.get("Sog")
                        timestamp = meta.get("time_utc")
                        shipname = meta.get("ShipName", "").strip()

                        if timestamp:
                            timestamp = timestamp.replace(" +0000 UTC", "Z").split(".")[0] + "Z"

                        if mmsi in TARGET_MMSIS and lat is not None and lon is not None:
                            insert_row(mmsi, lat, lon, timestamp, cog, sog, shipname)
                            print(f"📡 {shipname} ({mmsi}) @ ({lat}, {lon}) | COG: {cog}° | SOG: {sog} knots @ {timestamp}")
                        else:
                            print(f"🚫 Skipped: {shipname} ({mmsi}) | lat: {lat}, lon: {lon}")

                    except Exception as e:
                        print(f"⚠️ Parsing error: {e}")
                        traceback.print_exc()

        except Exception as e:
            print(f"❌ WebSocket disconnected or error occurred: {e}")
            traceback.print_exc()
            print("⏳ Reconnecting in 0.5 seconds...")
            await asyncio.sleep(0.5)

def run_health_server():
    uvicorn.run(app, host="0.0.0.0", port=8080)

if __name__ == "__main__":
    print("🚀 Launching asyncio.run")
    threading.Thread(target=run_health_server, daemon=True).start()
    try:
        asyncio.run(main())
    except Exception as e:
        print("❌ asyncio.run error:", e)
        traceback.print_exc()
