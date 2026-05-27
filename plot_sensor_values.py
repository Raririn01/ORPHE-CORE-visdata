import asyncio
import threading
import matplotlib.pyplot as plt
from collections import deque
from orphe_core import Orphe
import matplotlib.animation as animation

plot_buffer_size = 512  # บัฟเฟอร์เก็บจำนวนข้อมูลที่จะแสดงผลบนกราฟ

# ข้อมูลความเร่งสำหรับเท้าซ้าย (Left)
acc_l_x = deque(maxlen=plot_buffer_size)
acc_l_y = deque(maxlen=plot_buffer_size)
acc_l_z = deque(maxlen=plot_buffer_size)

# ข้อมูลความเร่งสำหรับเท้าขวา (Right)
acc_r_x = deque(maxlen=plot_buffer_size)
acc_r_y = deque(maxlen=plot_buffer_size)
acc_r_z = deque(maxlen=plot_buffer_size)

# ตั้งค่าหน้าต่างโปรแกรมและซับพล็อต (2 ซับพล็อต: ซ้ายอยู่บน, ขวาอยู่ล่าง)
fig, (ax_l, ax_r) = plt.subplots(2, 1, sharex=True)
fig.suptitle('ORPHE CORE - Real-time Accelerometer Plot (Dual-Core)')

# ตั้งค่าซับพล็อตเท้าซ้าย
line_l_x, = ax_l.plot([], [], label='Acc X', color='red')
line_l_y, = ax_l.plot([], [], label='Acc Y', color='green')
line_l_z, = ax_l.plot([], [], label='Acc Z', color='blue')
ax_l.legend(loc='upper right')
ax_l.set_xlim(0, plot_buffer_size)
ax_l.set_ylim(-3, 3)
ax_l.set_title('Left Foot (Core 1)')
ax_l.grid(True)

# ตั้งค่าซับพล็อตเท้าขวา
line_r_x, = ax_r.plot([], [], label='Acc X', color='red')
line_r_y, = ax_r.plot([], [], label='Acc Y', color='green')
line_r_z, = ax_r.plot([], [], label='Acc Z', color='blue')
ax_r.legend(loc='upper right')
ax_r.set_xlim(0, plot_buffer_size)
ax_r.set_ylim(-3, 3)
ax_r.set_title('Right Foot (Core 2)')
ax_r.grid(True)

# ฟังก์ชันอัปเดตกราฟ (Animation Loop)
def update(frame):
    # อัปเดตกราฟเท้าซ้าย
    if len(acc_l_x) > 0:
        line_l_x.set_data(range(len(acc_l_x)), list(acc_l_x))
        line_l_y.set_data(range(len(acc_l_y)), list(acc_l_y))
        line_l_z.set_data(range(len(acc_l_z)), list(acc_l_z))
    
    # อัปเดตกราฟเท้าขวา
    if len(acc_r_x) > 0:
        line_r_x.set_data(range(len(acc_r_x)), list(acc_r_x))
        line_r_y.set_data(range(len(acc_r_y)), list(acc_r_y))
        line_r_z.set_data(range(len(acc_r_z)), list(acc_r_z))
        
    return line_l_x, line_l_y, line_l_z, line_r_x, line_r_y, line_r_z

# ตั้งค่า Asyncio Event Loop สำหรับทำงานเบื้องหลัง
loop = asyncio.new_event_loop()

def start_asyncio_loop(loop):
    asyncio.set_event_loop(loop)
    loop.run_forever()

# สร้างอ็อบเจกต์ ORPHE 2 ตัว
orphes = [Orphe(), Orphe()]

async def connect_and_start():
    def got_acc_l(acc):
        acc_l_x.append(acc.x)
        acc_l_y.append(acc.y)
        acc_l_z.append(acc.z)

    def got_acc_r(acc):
        acc_r_x.append(acc.x)
        acc_r_y.append(acc.y)
        acc_r_z.append(acc.z)

    # เชื่อมโยง Callback สำหรับแต่ละข้าง
    orphes[0].set_got_converted_acc_callback(got_acc_l)
    orphes[1].set_got_converted_acc_callback(got_acc_r)

    addresses = ["F4:1B:9B:AF:20:5D", "D0:A3:43:7D:01:BD"]
    
    # 1. เชื่อมต่อเซนเซอร์ทั้ง 2 ตัวก่อน
    for i, orphe in enumerate(orphes):
        if not await orphe.connect(addresses[i]):
            print(f"Failed to connect to device: {addresses[i]}")
            return False

    # 2. เมื่อเชื่อมต่อครบแล้ว ค่อยเริ่มรับข้อมูลความถี่สูง
    for orphe in orphes:
        await orphe.set_led(1, 0)
        await orphe.set_acc_range(16)
        await orphe.start_sensor_values_notification()
    return True

async def disconnect_and_stop():
    print("Stopping notifications...")
    for orphe in orphes:
        if orphe.is_connected():
            try:
                await orphe.stop_sensor_values_notification()
            except Exception as e:
                print(f"Error stopping notification: {e}")
                
    print("Disconnecting from all devices...")
    for orphe in orphes:
        if orphe.is_connected():
            try:
                await orphe.disconnect()
            except Exception as e:
                print(f"Error disconnecting device: {e}")
    print("Disconnected.")

if __name__ == "__main__":
    # เริ่มงานเบื้องหลังสำหรับจัดการบลูทูธ
    loop_thread = threading.Thread(target=start_asyncio_loop, args=(loop,), daemon=True)
    loop_thread.start()

    # สั่งให้เธรดเบื้องหลังเริ่มกระบวนการเชื่อมต่อ
    future = asyncio.run_coroutine_threadsafe(connect_and_start(), loop)
    
    try:
        # รอผลลัพธ์การเชื่อมต่อ (สูงสุด 20 วินาที)
        connected = future.result(timeout=20)
    except Exception as e:
        print(f"Connection error: {e}")
        connected = False

    if connected:
        # เริ่มการแสดงผลกราฟแบบเคลื่อนไหวบนเธรดหลัก (Main Thread)
        ani = animation.FuncAnimation(fig, update, interval=20, blit=True)
        plt.show() # บล็อกที่นี่เมื่อหน้าต่างเปิดอยู่

        print("Plot window closed. Cleaning up...")
        
    # ล้างสถานะเมื่อปิดหน้าต่างกราฟ
    future_shutdown = asyncio.run_coroutine_threadsafe(disconnect_and_stop(), loop)
    try:
        future_shutdown.result(timeout=5)
    except Exception as e:
        print(f"Shutdown error: {e}")

    # ปิด Event Loop
    loop.call_soon_threadsafe(loop.stop)
    loop_thread.join(timeout=3)
    print("Program exited.")
