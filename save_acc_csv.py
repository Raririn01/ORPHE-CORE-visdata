import sys
import asyncio
import csv
import os
from datetime import datetime
from orphe_core import Orphe

# ตั้งค่าระบบการแสดงผลภาษาไทย (UTF-8) บน Windows console และ event loop สำหรับ Bleak
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

CSV_FILENAME = "orphe_acc_data.csv"
CSV_HEADERS = ["Timestamp", "Device_Side", "Device_Address", "Acc_X", "Acc_Y", "Acc_Z"]

# สร้างไฟล์และเขียนหัวคอลัมน์หากยังไม่มีไฟล์อยู่
if not os.path.exists(CSV_FILENAME):
    with open(CSV_FILENAME, mode='w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(CSV_HEADERS)
    print(f"สร้างไฟล์ {CSV_FILENAME} และเขียน Header เรียบร้อยแล้ว")

def log_acc_data(side, address, acc):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    try:
        with open(CSV_FILENAME, mode='a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow([timestamp, side, address, f"{acc.x:.4f}", f"{acc.y:.4f}", f"{acc.z:.4f}"])
        print(f"[{side}] บันทึก Acc: X={acc.x:.2f}, Y={acc.y:.2f}, Z={acc.z:.2f}")
    except Exception as e:
        print(f"เกิดข้อผิดพลาดในการบันทึกข้อมูล: {e}")

async def main():
    addresses = ["F4:1B:9B:AF:20:5D", "D0:A3:43:7D:01:BD"]
    orphes = [Orphe(), Orphe()]
    
    # ตั้งค่า callback ในการรับข้อมูลของแต่ละฝั่ง
    def make_callback(side, address):
        return lambda acc: log_acc_data(side, address, acc)
        
    orphes[0].set_got_converted_acc_callback(make_callback("Left", addresses[0]))
    orphes[1].set_got_converted_acc_callback(make_callback("Right", addresses[1]))

    print("กำลังเชื่อมต่อไปยัง ORPHE CORE ทั้งสองเครื่อง...")
    for i, orphe in enumerate(orphes):
        addr = addresses[i]
        try:
            success = await orphe.connect(addr)
        except Exception as e:
            print(f"ไม่สามารถเชื่อมต่อไปยัง {addr} ได้: {e}")
            success = False
            
        if not success:
            print(f"กำลังสแกนค้นหาอุปกรณ์สำรองตัวที่ {i+1} โดยอัตโนมัติ...")
            try:
                success = await orphe.connect(None)
            except Exception as e:
                print(f"เกิดข้อผิดพลาดขณะสแกนหาอุปกรณ์: {e}")
                success = False
                
        if not success:
            print(f"เชื่อมต่ออุปกรณ์ตัวที่ {i+1} ล้มเหลว!")
            return
            
        print(f"เชื่อมต่ออุปกรณ์ตัวที่ {i+1} สำเร็จ! กำลังดึงข้อมูลบริการ (Services)...")
        # Bleak 3.x จะโหลด GATT Services อัตโนมัติตอน connect แล้ว เข้าถึง .services เพื่อยืนยันว่าโหลดครบ
        _ = orphe.client.services
        await orphe.read_device_information()
        await orphe.set_led(1, 0)
        await orphe.set_led_brightness(255)
        
    # เริ่มรับสัญญาณ Notification
    print("เริ่มสตรีมข้อมูลความเร่ง (Accelerometer)...")
    for orphe in orphes:
        await orphe.start_sensor_values_notification()
        
    print("\n>>> ระบบพร้อมบันทึกข้อมูล Acc XYZ แล้ว! กรุณาเคลื่อนไหวเซนเซอร์... กด Ctrl+C เพื่อหยุดและบันทึกไฟล์ <<<\n")
    
    try:
        while True:
            await asyncio.sleep(1)
            # ตรวจสอบการเชื่อมต่อหลุด
            if not orphes[0].is_connected() or not orphes[1].is_connected():
                print("อุปกรณ์เครื่องใดเครื่องหนึ่งหลุดการเชื่อมต่อ...")
                break
    finally:
        print("\nกำลังปิดการใช้งานและตัดการเชื่อมต่อ...")
        for orphe in orphes:
            if orphe.is_connected():
                try:
                    await orphe.stop_sensor_values_notification()
                except Exception as e:
                    print(f"ไม่สามารถหยุด Notification ได้: {e}")
                await orphe.disconnect()
        print("ตัดการเชื่อมต่อเรียบร้อย บันทึกข้อมูลลง CSV เสร็จสิ้น")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nหยุดระบบ บันทึกไฟล์เสร็จสิ้น")
