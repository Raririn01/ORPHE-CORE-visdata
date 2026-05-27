"""
read_mongodb.py — อ่านข้อมูลจาก MongoDB (orphe_gait_db)

แสดงผลสรุปและตัวอย่างข้อมูลจาก 2 collections:
  1. gait_analysis  — ข้อมูลก้าวเดิน (รายก้าว)
  2. sensor_raw     — ข้อมูลเซนเซอร์ดิบ (Gyro/Quat/Acc ความถี่สูง)
"""

import sys
import pymongo
from datetime import datetime, timedelta
from pprint import pprint

# ตั้งค่าการแสดงผลภาษาไทย (UTF-8)
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')

# =============================================================================
# ตั้งค่าการเชื่อมต่อ
# =============================================================================
MONGO_URI = "mongodb://localhost:27017/"
DB_NAME = "orphe_gait_db"
GAIT_COLL = "gait_analysis"
SENSOR_COLL = "sensor_raw"


def connect_mongo():
    """เชื่อมต่อ MongoDB และคืนค่า database object"""
    try:
        client = pymongo.MongoClient(MONGO_URI, serverSelectionTimeoutMS=3000)
        client.server_info()
        print("✅ เชื่อมต่อ MongoDB สำเร็จ!\n")
        return client, client[DB_NAME]
    except Exception as e:
        print(f"❌ ไม่สามารถเชื่อมต่อ MongoDB ได้: {e}")
        sys.exit(1)


def show_overview(db):
    """แสดงภาพรวมของ collections ทั้งหมดใน database"""
    print("=" * 70)
    print(f"  📊 ภาพรวมฐานข้อมูล: {DB_NAME}")
    print("=" * 70)

    collections = db.list_collection_names()
    print(f"  Collections ทั้งหมด: {collections}\n")

    for coll_name in [GAIT_COLL, SENSOR_COLL]:
        coll = db[coll_name]
        count = coll.count_documents({})
        print(f"  📁 {coll_name}: {count:,} documents")

        if count > 0:
            # หาช่วงเวลา
            first = coll.find_one(sort=[("timestamp", pymongo.ASCENDING)])
            last = coll.find_one(sort=[("timestamp", pymongo.DESCENDING)])
            if first and last and 'timestamp' in first and 'timestamp' in last:
                print(f"     เวลาเริ่มต้น: {first['timestamp']}")
                print(f"     เวลาล่าสุด:   {last['timestamp']}")
                duration = last['timestamp'] - first['timestamp']
                print(f"     ระยะเวลา:     {duration}")

            # นับแยกตาม metadata.side
            pipeline = [
                {"$group": {"_id": "$metadata.side", "count": {"$sum": 1}}}
            ]
            sides = list(coll.aggregate(pipeline))
            for s in sides:
                print(f"     → {s['_id']}: {s['count']:,} documents")
        print()


def show_gait_data(db, limit=10):
    """แสดงตัวอย่างข้อมูลก้าวเดินล่าสุด"""
    coll = db[GAIT_COLL]
    count = coll.count_documents({})

    print("=" * 70)
    print(f"  🦶 ข้อมูลก้าวเดิน (Gait Analysis) — ล่าสุด {limit} รายการ")
    print("=" * 70)

    if count == 0:
        print("  ❌ ไม่มีข้อมูลก้าวเดินใน collection")
        return

    docs = coll.find().sort("timestamp", pymongo.DESCENDING).limit(limit)

    for i, doc in enumerate(docs, 1):
        side = doc.get('metadata', {}).get('side', 'N/A')
        addr = doc.get('metadata', {}).get('device_address', 'N/A')
        ts = doc.get('timestamp', 'N/A')
        step = doc.get('step_count', 'N/A')
        speed = doc.get('speed_m_s', 'N/A')
        stride = doc.get('stride_length_cm', 'N/A')
        impact = doc.get('landing_impact_m_s2', 'N/A')
        pronation = doc.get('pronation_deg', 'N/A')
        phase = doc.get('phase', '')
        event = doc.get('event', '')

        print(f"\n  [{i}] Step #{step} | {side} | {ts}")
        print(f"      Speed: {speed} m/s | Stride: {stride} cm")
        print(f"      Impact: {impact} m/s² | Pronation: {pronation}°")
        print(f"      Phase: {phase} | Event: {event}")
        print(f"      Address: {addr}")

        # แสดง Quaternion ถ้ามี
        if doc.get('has_quat_distance'):
            print(f"      Quat: W={doc.get('quat_w')}, X={doc.get('quat_x')}, "
                  f"Y={doc.get('quat_y')}, Z={doc.get('quat_z')}")
            print(f"      Distance: X={doc.get('x_distance')}, Y={doc.get('y_distance')}, "
                  f"Z={doc.get('z_distance')}")

    print()


def show_sensor_data(db, limit=20):
    """แสดงตัวอย่างข้อมูลเซนเซอร์ดิบล่าสุด"""
    coll = db[SENSOR_COLL]
    count = coll.count_documents({})

    print("=" * 70)
    print(f"  📡 ข้อมูลเซนเซอร์ดิบ (Sensor Raw) — ล่าสุด {limit} รายการ")
    print("=" * 70)

    if count == 0:
        print("  ❌ ไม่มีข้อมูลเซนเซอร์ใน collection")
        return

    # แสดงสถิติแยกตาม sensor_type
    pipeline = [
        {"$group": {
            "_id": "$metadata.sensor_type",
            "count": {"$sum": 1},
            "min_ts": {"$min": "$timestamp"},
            "max_ts": {"$max": "$timestamp"}
        }}
    ]
    stats = list(coll.aggregate(pipeline))
    print("\n  สถิติแยกตามประเภทเซนเซอร์:")
    for s in stats:
        print(f"    {s['_id']}: {s['count']:,} documents "
              f"({s['min_ts']} → {s['max_ts']})")

    # แสดงตัวอย่างข้อมูลล่าสุด
    print(f"\n  ตัวอย่างข้อมูลล่าสุด {limit} รายการ:")
    docs = coll.find().sort("timestamp", pymongo.DESCENDING).limit(limit)

    for i, doc in enumerate(docs, 1):
        sensor_type = doc.get('metadata', {}).get('sensor_type', 'N/A')
        side = doc.get('metadata', {}).get('side', 'N/A')
        ts = doc.get('timestamp', 'N/A')
        x = doc.get('x', 'N/A')
        y = doc.get('y', 'N/A')
        z = doc.get('z', 'N/A')
        w = doc.get('w', '')
        pkt = doc.get('packet_number', 'N/A')

        w_str = f"W={w}, " if w != '' else ""
        print(f"    [{i:2d}] {sensor_type:4s} | {side:5s} | {ts} | "
              f"{w_str}X={x}, Y={y}, Z={z} | pkt#{pkt}")

    print()


def show_sensor_by_type(db, sensor_type, limit=5):
    """แสดงตัวอย่างข้อมูลเซนเซอร์แยกตามประเภท"""
    coll = db[SENSOR_COLL]
    query = {"metadata.sensor_type": sensor_type.upper()}
    count = coll.count_documents(query)

    print(f"\n  🔍 {sensor_type.upper()} — {count:,} documents ทั้งหมด (แสดง {limit} ล่าสุด):")

    docs = coll.find(query).sort("timestamp", pymongo.DESCENDING).limit(limit)
    for doc in docs:
        side = doc.get('metadata', {}).get('side', 'N/A')
        ts = doc.get('timestamp', 'N/A')
        print(f"    {side:5s} | {ts} | X={doc.get('x')}, Y={doc.get('y')}, Z={doc.get('z')}")


def show_raw_document(db, coll_name, limit=2):
    """แสดง raw document (ทุก field) เพื่อดูโครงสร้างข้อมูล"""
    coll = db[coll_name]
    count = coll.count_documents({})

    print("=" * 70)
    print(f"  🔎 Raw Document Structure: {coll_name} (แสดง {min(limit, count)} ตัวอย่าง)")
    print("=" * 70)

    if count == 0:
        print("  ❌ ไม่มีข้อมูล")
        return

    docs = coll.find().sort("timestamp", pymongo.DESCENDING).limit(limit)
    for i, doc in enumerate(docs, 1):
        print(f"\n  --- Document #{i} ---")
        pprint(doc, width=100, indent=4)

    print()


# =============================================================================
# Main
# =============================================================================
if __name__ == "__main__":
    client, db = connect_mongo()

    try:
        # 1. ภาพรวม
        show_overview(db)

        # 2. ข้อมูลก้าวเดิน
        show_gait_data(db, limit=10)

        # 3. ข้อมูลเซนเซอร์ดิบ
        show_sensor_data(db, limit=20)

        # 4. แสดงตัวอย่างแยกประเภทเซนเซอร์
        for sensor_type in ["GYRO", "QUAT", "ACC"]:
            show_sensor_by_type(db, sensor_type, limit=3)

        # 5. แสดง Raw document structure
        print("\n")
        show_raw_document(db, GAIT_COLL, limit=2)
        show_raw_document(db, SENSOR_COLL, limit=2)

    finally:
        client.close()
        print("\n🔌 ปิดการเชื่อมต่อ MongoDB เรียบร้อย")
